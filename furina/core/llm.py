"""统一的大模型调用（OpenAI 兼容接口，默认 DeepSeek ``deepseek-flash``）。

文字 / 看图 / 图片描述 / 意图识别 / 提醒解析 共用这里：
- 共享 httpx 连接池，最多 3 个并发请求；
- 429/5xx/超时 先快速重试一次，``finish_reason=length`` 时放大预算重试一次；
- 单模型部署只做短冷却，不会因为一次 400 把所有 AI 功能停掉；
- ``.env`` 改动后自动清空冷却状态；
- 每次成功调用记一笔 ``model:<名字>`` 用量，供面板显示“今日模型调用”；
- 每次调用还按「哪个功能调的」记下 token（缓存命中 / 未命中 / 输出）和折算金额（``usage_report()``），
  日志里一行一条「[用量] …」，用来看钱花在哪。价格按 DeepSeek V4.1-Flash 官方价（元/百万 token，空闲时段）：
  命中 0.02 / 未命中 1 / 输出 4，工作日 9–12、14–18 点高峰翻倍；可用 AI_CHAT_PRICE_HIT/MISS/OUT 改。
"""
from __future__ import annotations

import asyncio
import random
import sys
import time
from datetime import date

import httpx
from nonebot import logger

from . import db, env, free_model

DEFAULT_MODEL = "deepseek-flash"

_semaphore = asyncio.Semaphore(3)
_vision_semaphore = asyncio.Semaphore(2)
_free_semaphore = asyncio.Semaphore(3)
_client: httpx.AsyncClient | None = None
_state: dict = {"cooldown": {}, "dead": set(), "version": -1}


class LLMError(RuntimeError):
    pass


class _ModelUnavailable(LLMError):
    def __init__(self, msg: str, *, quota: bool = False, retryable: bool = False, short: bool = False):
        super().__init__(msg)
        self.quota, self.retryable, self.short = quota, retryable, short


# ---------------- 配置（全部热读取） ----------------

def api_key() -> str:
    return env.get("AI_CHAT_LLM_API_KEY")


def endpoint() -> str:
    base = env.get("AI_CHAT_LLM_BASE_URL").rstrip("/")
    if not base:
        return ""
    if base.endswith("/chat/completions"):
        return base
    return base + ("/chat/completions" if base.endswith("/v1") else "/v1/chat/completions")


def models() -> list[str]:
    pool = [m for m in env.get("AI_CHAT_LLM_MODELS").replace("，", ",").split(",") if m.strip()]
    return [m.strip() for m in pool] or [env.get("AI_CHAT_LLM_MODEL", DEFAULT_MODEL)]


def temperature() -> float:
    return env.get_float("AI_CHAT_TEMPERATURE", 0.6)


def max_tokens_default() -> int:
    return env.get_int("AI_CHAT_MAX_TOKENS", 512)


def timeout_default() -> float:
    return env.get_float("AI_CHAT_LLM_TIMEOUT", 40)


# ---------------- 工具 ----------------

async def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=10.0),
                                    limits=httpx.Limits(max_connections=10, max_keepalive_connections=5))
    return _client


async def aclose() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
    _client = None


def _classify(status: int, body: str) -> tuple[bool, bool]:
    """(鉴权/额度问题, 瞬时可重试)。400 只有明确的额度字样才算额度问题。"""
    body = (body or "").lower()
    quota_words = ("quota", "额度", "免费", "余额", "欠费", "insufficient", "balance", "arrear",
                   "billing", "payment", "激活", "activated", "access denied")
    quota = status in (401, 402, 403) or (status == 400 and any(w in body for w in quota_words))
    retryable = status in (408, 429, 500, 502, 503, 504)
    return quota, retryable


def has_image(messages: list) -> bool:
    for m in messages:
        c = m.get("content") if isinstance(m, dict) else None
        if isinstance(c, list) and any(isinstance(i, dict) and i.get("type") == "image_url" for i in c):
            return True
    return False


def _reset_on_config_change() -> None:
    v = env.version()
    if v != _state["version"]:
        if _state["version"] != -1:
            logger.info(f"[LLM] 检测到配置热更新 (version={v})，已重置模型冷却状态")
        _state["version"] = v
        _state["cooldown"].clear()
        _state["dead"].clear()


def _record_failure(model: str, multi: bool, e: _ModelUnavailable | LLMError) -> None:
    now = time.time()
    if getattr(e, "quota", False):
        if multi:
            _state["dead"].add(model)
            logger.warning(f"[LLM] 模型 {model} 额度/鉴权不可用（{e}），本次运行跳过")
        else:
            _state["cooldown"][model] = now + 60
            logger.warning(f"[LLM] 模型 {model} 鉴权/额度异常（{e}），60s 后再试")
    elif getattr(e, "short", False):
        _state["cooldown"][model] = now + 15
        logger.warning(f"[LLM] 模型 {model} 瞬时故障（{e}），短冷却 15s")
    elif getattr(e, "retryable", False):
        wait = 120 if multi else 20
        _state["cooldown"][model] = now + wait
        logger.warning(f"[LLM] 模型 {model} 限流/服务端错误（{e}），冷却 {wait}s")
    elif multi:
        _state["cooldown"][model] = now + 120
        logger.warning(f"[LLM] 模型 {model} 不可用（{e}），冷却 120s")
    else:
        logger.warning(f"[LLM] 模型 {model} 本次请求失败（{e}），单次问题不冷却")


def thinking_payload(effort: str | None) -> dict:
    """DeepSeek 思考模式参数（OpenAI 格式）：off = 关；low / high / max = 开并指定强度。"""
    if effort in ("low", "high", "max"):
        return {"thinking": {"type": "enabled"}, "reasoning_effort": effort}
    return {"thinking": {"type": "disabled"}}


async def _call_once(model: str, messages: list, *, timeout: float, max_tokens: int,
                     temp: float | None, effort: str | None = None, free: bool = False) -> str:
    url, key = (free_model.endpoint(), free_model.api_key()) if free else (endpoint(), api_key())
    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature() if temp is None else temp,
        "max_tokens": max_tokens,
        "stream": False,
    }
    payload.update(free_model.thinking_payload(effort) if free else thinking_payload(effort))
    try:
        client = await _get_client()
        resp = await client.post(url, json=payload, timeout=timeout,
                                 headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    except httpx.TimeoutException:
        raise _ModelUnavailable("请求超时", retryable=True, short=True)
    except httpx.HTTPError as e:
        raise _ModelUnavailable(f"网络错误: {e!r}", retryable=True, short=True)
    if resp.status_code != 200:
        quota, retryable = _classify(resp.status_code, resp.text)
        if resp.status_code == 400:
            roles = [m.get("role", "?") for m in messages if isinstance(m, dict)]
            logger.warning(f"[LLM] {model} 返回 400，消息数={len(messages)} 角色={roles} 响应={resp.text[:300]}")
        raise _ModelUnavailable(f"HTTP {resp.status_code}: {resp.text[:200]}", quota=quota, retryable=retryable)
    try:
        data = resp.json()
        choice = data["choices"][0]
        content = (choice["message"].get("content") or "").strip()
    except Exception as e:
        raise LLMError(f"响应解析失败: {e}")
    _record_tokens(model, data.get("usage") or {}, effort=effort, free=free)
    if not content:
        raise LLMError(f"模型返回空内容（finish_reason={choice.get('finish_reason', '')}）")
    return content


# ---------------- 用量与花费（看钱花在哪） ----------------
_SKIP_MODULES = ("furina.core.llm", "furina.plugins.chat.llm", "asyncio", "concurrent", "threading", "contextlib")


def caller_tag() -> str:
    """哪个功能调的模型：调用栈里第一个不是 llm 本身的 furina 函数，如「chat.planner:plan」。"""
    try:
        f = sys._getframe(2)
    except ValueError:
        return "?"
    while f is not None:
        mod = f.f_globals.get("__name__", "")
        if mod.startswith("furina") and not mod.startswith(_SKIP_MODULES):
            short = mod.replace("furina.plugins.", "").replace("furina.services.", "svc.").replace("furina.", "")
            return f"{short}:{f.f_code.co_name}"
        f = f.f_back
    return "?"


def is_peak(ts: float | None = None) -> bool:
    lt = time.localtime(time.time() if ts is None else ts)
    if lt.tm_wday >= 5:
        return False
    h = lt.tm_hour + lt.tm_min / 60
    return 9 <= h < 12 or 14 <= h < 18


def prices(ts: float | None = None) -> tuple[float, float, float]:
    """(命中, 未命中, 输出) 元/百万 token，已按峰谷折算。"""
    k = 2.0 if is_peak(ts) else 1.0
    return (env.get_float("AI_CHAT_PRICE_HIT", 0.02) * k, env.get_float("AI_CHAT_PRICE_MISS", 1.0) * k,
            env.get_float("AI_CHAT_PRICE_OUT", 4.0) * k)


THINK_MARK = "#思考"


def _record_tokens(model: str, usage: dict, effort: str | None = None, tag: str | None = None,
                   free: bool = False) -> None:
    try:
        prompt = int(usage.get("prompt_tokens") or 0)
        hit = int(usage.get("prompt_cache_hit_tokens") or 0)
        miss = int(usage.get("prompt_cache_miss_tokens") or max(0, prompt - hit))
        out = int(usage.get("completion_tokens") or 0)
        think = int(usage.get("reasoning_tokens") or (usage.get("completion_tokens_details") or {}).get("reasoning_tokens") or 0)
        if not (prompt or out):
            return
        ph, pm, po = prices()
        cost = 0.0 if free else (hit * ph + miss * pm + out * po) / 1e6
        tag = tag or caller_tag()
        if effort in ("low", "high", "max"):
            tag = f"{tag}{THINK_MARK}{effort}"  # 面板「模型花费」按这个把思考的花费单列出来
        day = date.today().isoformat()
        fields = [("calls", 1), ("hit", hit), ("miss", miss), ("out", out), ("cost_u", int(round(cost * 1e6)))]
        if think:
            fields.append(("think", think))
        for field, n in fields:
            db.usage_add(day, f"llmtok:{field}", tag, "", n)
        if free:
            db.usage_add(day, "llmfree:calls", tag, "", 1)
        logger.info(f"[用量] {tag} 输入{prompt}（命中{hit}）输出{out}" + (f"（思考{think}）" if think else "")
                    + (f" 免费（{model}）" if free else f" ≈{cost * 100:.3f}分"))
    except Exception as e:  # noqa: BLE001  记账失败不影响调用
        logger.debug(f"[用量] 记账失败: {e}")


def record_usage(usage: dict, tag: str, effort: str | None = None, model: str = "") -> None:
    """外部内核（DSH）的一次模型请求也记进同一本账。"""
    _record_tokens(model or DEFAULT_MODEL, usage or {}, effort=effort, tag=tag)


def usage_report(day: str | None = None) -> list[dict]:
    """某天各功能的用量，按花费从高到低：[{tag, calls, hit, miss, out, cost}]（cost 单位：元）。"""
    d = day or date.today().isoformat()
    rows: dict[str, dict] = {}
    for _day, kind, tag, _u, n in db.usage_rows(d):
        if _day != d or not kind.startswith("llmtok:"):
            continue
        rows.setdefault(tag, {"tag": tag, "calls": 0, "hit": 0, "miss": 0, "out": 0, "cost_u": 0})[kind[7:]] = int(n)
    out = []
    for r in rows.values():
        r["cost"] = r.pop("cost_u", 0) / 1e6
        out.append(r)
    return sorted(out, key=lambda r: -r["cost"])


def _count_usage(model: str) -> None:
    try:
        db.usage_add(date.today().isoformat(), f"model:{model}", "", "", 1)
    except Exception:
        pass


# ---------------- 对外接口 ----------------

async def chat(messages: list[dict], timeout: float | None = None, max_tokens: int | None = None,
               temperature: float | None = None, effort: str | None = None) -> str:
    """OpenAI 风格 messages → 回复文本；全部失败抛 LLMError。

    effort：思考强度 off / low / high / max（默认不思考）。开思考时自动放大输出预算和超时，
    免得只想不说被截断成空回复；思考的 token 按输出价计费，面板「模型花费」里单列。"""
    _reset_on_config_change()
    if not api_key():
        raise LLMError("未配置 API Key（AI_CHAT_LLM_API_KEY）")
    if not endpoint():
        raise LLMError("未配置模型接口地址（AI_CHAT_LLM_BASE_URL）")
    pool = models()
    multi = len(pool) > 1
    timeout = timeout or timeout_default()
    budget = max_tokens or max_tokens_default()
    if effort in ("low", "high", "max"):
        budget += {"low": 2048, "high": 6144, "max": 16384}[effort]
        timeout = max(timeout, {"low": 60, "high": 120, "max": 240}[effort])
    if has_image(messages):
        logger.info(f"[LLM] 本条请求带图，使用模型池: {pool}")
    elif free_model.enabled():
        content = await _try_free(messages, timeout=timeout, max_tokens=budget, temp=temperature, effort=effort)
        if content:
            return content
    errors: list[str] = []
    async with _semaphore:
        for model in pool:
            if model in _state["dead"] or time.time() < _state["cooldown"].get(model, 0):
                continue
            for attempt in range(2):
                try:
                    content = await _call_once(model, messages, timeout=timeout, max_tokens=budget, temp=temperature,
                                               effort=effort)
                    logger.info(f"[AI聊天] 使用模型: {model}")
                    _count_usage(model)
                    return content
                except _ModelUnavailable as e:
                    if attempt == 0 and e.retryable:
                        wait = 1.2 + random.random()
                        logger.warning(f"[LLM] 模型 {model} 限流/过载（{e}），{wait:.1f}s 后重试")
                        await asyncio.sleep(wait)
                        continue
                    errors.append(f"{model}: {e}")
                    _record_failure(model, multi, e)
                    break
                except LLMError as e:
                    if attempt == 0 and "finish_reason=length" in str(e):
                        budget = max(budget * 2, max_tokens_default() * 3, 768)
                        logger.warning(f"[LLM] 模型 {model} 输出被截断为空，放大预算到 {budget} 重试")
                        continue
                    errors.append(f"{model}: {e}")
                    _record_failure(model, multi, e)
                    break
    raise LLMError("模型池全部不可用: " + " | ".join(errors[-5:]) if errors else "模型池全部处于冷却中")


async def _try_free(messages: list, *, timeout: float, max_tokens: int, temp: float | None,
                    effort: str | None) -> str:
    """免费线路：按顺序试一遍免费模型，成功返回文本；全失败返回空串（调用方接着走付费），不抛异常。"""
    tag = caller_tag()
    pool = free_model.available_models(tag)
    if not pool:
        return ""
    if not free_model.api_key():
        free_model.ensure_service()
        return ""
    main = set(free_model.models())
    need_json = free_model.wants_json(messages)
    async with _free_semaphore:
        for model in pool:
            if effort in ("low", "high", "max"):
                limit = max(free_model.timeout(), timeout)
            elif model in main:
                limit = min(timeout, free_model.timeout())
            else:  # 后台任务的免费模型：慢一点没关系
                limit = max(timeout, free_model.timeout())
            try:
                content = await _call_once(model, messages, timeout=limit, max_tokens=max_tokens, temp=temp,
                                           effort=effort, free=True)
                if need_json and not free_model.json_ok(content):
                    raise LLMError("回的不是 JSON")
                free_model.mark_ok(model)
                _count_usage(model)
                logger.info(f"[AI聊天] 使用免费模型: {model}")
                return content
            except _ModelUnavailable as e:
                text = str(e)
                if "网络错误" in text and ("ConnectError" in text or "Connect" in text):
                    hint = free_model.ensure_service()
                    secs = free_model.mark_failed(model, text, limited=False)
                    logger.warning(f"[LLM] 免费模型服务连不上（{hint}），先用付费模型，{secs / 60:.0f} 分钟后再试")
                    return ""
                limited = e.quota or "429" in text or "limit" in text.lower()
                secs = free_model.mark_failed(model, text, limited=limited)
                logger.warning(f"[LLM] 免费模型 {model} 不可用（{text[:120]}），改用付费模型，冷却 {secs / 60:.0f} 分钟")
            except LLMError as e:
                secs = free_model.mark_failed(model, str(e), limited=False)
                logger.warning(f"[LLM] 免费模型 {model} 本次失败（{e}），改用付费模型，冷却 {secs / 60:.0f} 分钟")
    return ""


async def describe_image(data_url: str, max_len: int = 40, prompt: str = "") -> str:
    """一句话图片描述（存进上下文，方便以后提到这张图）。失败返回空串。prompt 可换成别的看图要求（如给表情包写备注）。"""
    if not data_url or not api_key() or not endpoint():
        return ""
    messages = [{
        "role": "user",
        "content": [
            {"type": "image_url", "image_url": {"url": data_url}},
            {"type": "text", "text": prompt or "用一句话简短描述这张图片的内容和文字（如果有），30 字以内，直接输出描述本身，不要任何前缀或标点符号"},
        ],
    }]
    async with _vision_semaphore:
        try:
            text = await chat(messages, timeout=30, max_tokens=256)
        except LLMError as e:
            logger.warning(f"[LLM] 图片描述失败: {e}")
            return ""
    return (text or "").strip()[:max_len]
