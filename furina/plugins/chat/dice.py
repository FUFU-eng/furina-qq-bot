"""主动开口的骰子（参考 dsh-speak-up「主动搭话」）：本地掷，一个 token 都不花，掷中了才去写话。

命中线 = min(100%, 基础 × 时机因子 × 峰谷因子 + min(加成上限, 连着没中几次 × 没中加成))
- 时机：她刚开过口（<20 分钟）→ ×0.15，更难开口；冷了很久（>6 小时）→ ×1.5，更容易开口；中间 ×1。
- 峰谷：DeepSeek 工作日 9:00–12:00、14:00–18:00 是高峰（价格是谷时的两倍），这时少开口
  （×AI_CHAT_PEAK_FACTOR，默认 0.5；填 1 = 不区分）；其余时间和周末全天是谷时。
- 没中加成：每没掷中一次，下次命中线 +AI_CHAT_PROACTIVE_PITY（默认 10 个百分点），最多 +AI_CHAT_PROACTIVE_PITY_CAP（40）；
  掷中就清零。只记在内存里，重启归零。
"""
from __future__ import annotations

import random
import time

from . import config

_misses: dict[str, int] = {}


def is_peak(now: float | None = None) -> bool:
    lt = time.localtime(time.time() if now is None else now)
    if lt.tm_wday >= 5:
        return False  # 周六日全天按谷时计价
    h = lt.tm_hour + lt.tm_min / 60
    return 9 <= h < 12 or 14 <= h < 18


def peak_factor(now: float | None = None) -> float:
    f = float(config.PEAK_FACTOR)
    return max(0.0, min(1.0, f)) if is_peak(now) else 1.0


def timing_factor(since_spoke: float | None) -> float:
    if since_spoke is None:
        return 1.0
    if since_spoke < 20 * 60:
        return 0.15
    if since_spoke > 6 * 3600:
        return 1.5
    return 1.0


def hit_line(key: str, base: float, now: float | None = None, since_spoke: float | None = None) -> tuple[float, str]:
    """算命中线，并给一句人能看懂的来历。"""
    tf, pf = timing_factor(since_spoke), peak_factor(now)
    pity_step = max(0.0, float(config.PROACTIVE_PITY))
    bonus = min(max(0.0, float(config.PROACTIVE_PITY_CAP)), _misses.get(key, 0) * pity_step)
    p = min(1.0, max(0.0, base) * tf * pf + bonus)
    why = f"{base * 100:.0f}% × 时机{tf:g} × {'峰时' if pf < 1 else '谷时'}{pf:g} + 没中加成{bonus * 100:.0f}"
    return p, why


def roll(key: str, base: float, now: float | None = None, since_spoke: float | None = None,
         rng: random.Random | None = None) -> tuple[bool, str]:
    r = rng or random
    p, why = hit_line(key, base, now, since_spoke)
    x = r.random()
    hit = x < p
    _misses[key] = 0 if hit else _misses.get(key, 0) + 1
    return hit, f"{'掷中' if hit else '没中'}：掷出 {x * 100:.0f}（命中线 {p * 100:.0f} = {why}）"


def reset(key: str | None = None) -> None:
    if key is None:
        _misses.clear()
    else:
        _misses.pop(key, None)
