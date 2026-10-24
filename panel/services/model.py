"""AI 模型接口设置（写 .env，机器人热加载）与连接测试。"""
from __future__ import annotations

import time

from . import envfile

MODEL = "deepseek-flash"  # 文字聊天、看图、搜图比对共用，已验证可用


def get() -> dict:
    v = envfile.values()
    return {"base_url": v.get("AI_CHAT_LLM_BASE_URL", ""), "api_key": v.get("AI_CHAT_LLM_API_KEY", ""),
            "model": v.get("AI_CHAT_LLM_MODEL") or MODEL}


def save(base_url: str, api_key: str) -> None:
    base_url, api_key = base_url.strip(), api_key.strip()
    if not base_url or not api_key:
        raise ValueError("Base URL 和 API Key 都需要填写")
    envfile.set_values({"AI_CHAT_LLM_BASE_URL": base_url, "AI_CHAT_LLM_API_KEY": api_key,
                        "AI_CHAT_LLM_MODEL": MODEL, "AI_CHAT_LLM_MODELS": MODEL})


def test(base_url: str, api_key: str) -> dict:
    url, key = base_url.strip().rstrip("/"), api_key.strip()
    if not url or not key:
        return {"ok": False, "text": "请先填写 Base URL 和 API Key"}
    if not url.endswith("/chat/completions"):
        url += "/chat/completions" if url.endswith("/v1") else "/v1/chat/completions"
    import httpx

    t0 = time.time()
    try:
        with httpx.Client(timeout=15.0) as client:
            resp = client.post(url, headers={"Authorization": f"Bearer {key}"}, json={
                "model": MODEL, "messages": [{"role": "user", "content": "1"}],
                "max_tokens": 16, "thinking": {"type": "disabled"}, "stream": False})
        ms = int((time.time() - t0) * 1000)
        if resp.status_code == 200:
            return {"ok": True, "text": f"连接成功，耗时 {ms} ms"}
        return {"ok": False, "text": f"连接失败（HTTP {resp.status_code}）：{resp.text[:120].replace(chr(10), ' ')}"}
    except Exception as exc:
        return {"ok": False, "text": f"请求异常：{str(exc)[:120]}"}
