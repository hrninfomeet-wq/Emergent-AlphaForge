"""Pure helpers governing the optimizer Analyzing stage: a wall-clock budget and
a self-calibrating ETA. No I/O — the caller supplies monotonic elapsed + counters."""
from __future__ import annotations
from typing import Optional


def over_budget(*, elapsed: float, budget_sec: int) -> bool:
    """True once elapsed >= budget. budget_sec <= 0 means unlimited (always False)."""
    return budget_sec > 0 and elapsed >= float(budget_sec)


def ewma(prev: Optional[float], sample: float, alpha: float = 0.3) -> float:
    """Exponential moving average of per-item wall-times. First sample seeds it."""
    s = float(sample)
    return s if prev is None else (alpha * s + (1.0 - alpha) * float(prev))


def eta_seconds(*, done: int, total: int, per_item_sec: Optional[float]) -> Optional[float]:
    """Remaining seconds = (total-done)*per_item. None until we have a per-item estimate."""
    if per_item_sec is None:
        return None
    return max(0, int(total) - int(done)) * float(per_item_sec)
