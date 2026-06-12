"""THE execution policy: one place where exit semantics live.

Before this module, exit LEVELS and exit DECISIONS were implemented in four
places — the spot backtest loop, the option premium-level walk, the live
per-minute marker, and the live spot-mirror check. `exit_engine.intrabar_exit`
already unified the bar-level decision for the two sim paths; this module
finishes the job so the LIVE paths share the very same rules, enforced by
parity tests (tests/test_execution_policy.py). If backtest and live ever
disagree about when a trade exits, every forward result is unfalsifiable —
this module is what makes "live mirrors the backtest" a tested invariant
instead of a hope.

The contract (identical in sim and live):
  - Premium levels (long option): target = entry + pts or entry x (1 + pct/100),
    ABOVE entry; stop = entry - pts or entry x (1 - pct/100), BELOW entry.
    POINTS take precedence over PERCENT when both are given. Stops never go
    below `stop_floor` (0.0 in the sim, Rs 0.05 live — an exchange tick).
  - Spot-mirror levels: CE target ABOVE entry spot / stop BELOW; PE mirrored.
    Formulas are byte-for-byte the spot engine's (backtest.py).
  - Decisions are PESSIMISTIC, STOP-FIRST: if both levels are touched in the
    same bar — or by the same tick in degenerate configurations — the stop
    fills first. (The live deciders used to check target first; fixed here.)
  - A live tick is a degenerate bar (high == low == price): every tick
    decision delegates to `intrabar_exit(price, price, ...)` so sim and live
    can never drift apart again.
"""
from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

from app.exit_engine import intrabar_exit


def _positive(value: Any) -> Optional[float]:
    """Positive float or None ('' / 0 / garbage are 'unset')."""
    try:
        if value in (None, ""):
            return None
        f = float(value)
        return f if f > 0 else None
    except (TypeError, ValueError):
        return None


def resolve_premium_levels(
    entry_price: float,
    *,
    target_pts: Any = None,
    stop_pts: Any = None,
    target_pct: Any = None,
    stop_pct: Any = None,
    stop_floor: float = 0.0,
    ndigits: Optional[int] = None,
) -> Tuple[Optional[float], Optional[float]]:
    """(stop, target) absolute premium levels for a LONG option position.

    Points take precedence over percent per leg. Unset/invalid/non-positive
    inputs leave that leg None. `stop_floor` clamps the stop (0.0 keeps the
    sim's legacy behavior; live uses 0.05). `ndigits` optionally rounds —
    live stores 2dp, the sim keeps full precision.
    """
    entry = float(entry_price)

    t_pts, s_pts = _positive(target_pts), _positive(stop_pts)
    t_pct, s_pct = _positive(target_pct), _positive(stop_pct)

    target: Optional[float] = None
    if t_pts is not None:
        target = entry + t_pts
    elif t_pct is not None:
        target = entry * (1.0 + t_pct / 100.0)

    stop: Optional[float] = None
    if s_pts is not None:
        stop = entry - s_pts
    elif s_pct is not None:
        stop = entry * (1.0 - s_pct / 100.0)
    if stop is not None:
        stop = max(float(stop_floor), stop)

    if ndigits is not None:
        target = round(target, ndigits) if target is not None else None
        stop = round(stop, ndigits) if stop is not None else None
    return stop, target


def tick_exit_reason(
    price: Any,
    *,
    stop: Any,
    target: Any,
    is_long: bool = True,
    stop_reason: str = "stop_hit",
    target_reason: str = "target_hit",
) -> Optional[str]:
    """Exit decision for a single live price — a degenerate bar.

    Delegates to the SAME `intrabar_exit` the backtest uses, with
    high == low == price, so the live marker can never disagree with the sim.
    Stop-first when both levels are satisfied (the old live deciders checked
    the target first and would book the lucky fill in degenerate configs).
    """
    try:
        p = float(price)
    except (TypeError, ValueError):
        return None
    stop_level = None if stop in (None, "") else float(stop)
    target_level = None if target in (None, "") else float(target)
    if stop_level is None and target_level is None:
        return None
    _level, reason = intrabar_exit(
        high=p, low=p, stop=stop_level, target=target_level, is_long=is_long,
    )
    if reason == "STOP":
        return stop_reason
    if reason == "TARGET":
        return target_reason
    return None


def spot_mirror_levels(
    direction: str,
    entry_spot: float,
    *,
    target_pts: Any = None,
    stop_pts: Any = None,
    ndigits: int = 2,
) -> Dict[str, Optional[float]]:
    """Absolute spot-mirror levels — byte-for-byte the spot engine's formulas
    (backtest.py: CE stop = entry - stp / target = entry + tgt; PE mirrored)."""
    sign = 1.0 if str(direction or "").upper() == "CE" else -1.0
    entry = float(entry_spot)
    t_pts, s_pts = _positive(target_pts), _positive(stop_pts)
    return {
        "spot_target": round(entry + sign * t_pts, ndigits) if t_pts else None,
        "spot_stop": round(entry - sign * s_pts, ndigits) if s_pts else None,
    }


def spot_mirror_exit_reason(
    direction: str,
    spot_price: Any,
    *,
    spot_target: Any,
    spot_stop: Any,
) -> Optional[str]:
    """Has the underlying tick hit a spot-mirror level?

    CE behaves like the sim's LONG spot position, PE like SHORT — the same
    is_long mapping `backtest.py` passes to `intrabar_exit`. Stop-first.
    """
    return tick_exit_reason(
        spot_price,
        stop=spot_stop,
        target=spot_target,
        is_long=(str(direction or "").upper() == "CE"),
        stop_reason="spot_stop_hit",
        target_reason="spot_target_hit",
    )
