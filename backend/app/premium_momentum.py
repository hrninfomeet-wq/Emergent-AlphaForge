# backend/app/premium_momentum.py
"""Pure, host-testable helpers for the premium-momentum contingency strategy.

No DB / tick I/O — callers pass already-loaded contracts and option candles.
These are the SHARED rule functions: the backtest sim and (later) the live
deployment loop both call them, so entry/exit/strike semantics cannot drift.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from app.options_universe import select_contract_for_signal


def lock_reference_strike(*, contracts: List[Dict[str, Any]], underlying: str,
                          spot_at_ref: float, side: str,
                          moneyness: str = "itm1") -> Optional[Dict[str, Any]]:
    """Resolve and LOCK the option contract at the reference bar's spot.

    Wraps the shared selector so backtest and live pick the identical strike.
    Returns {"instrument_key","strike","side","moneyness"} or None if the strike
    is absent from `contracts` (coverage gap for that moneyness)."""
    sel = select_contract_for_signal(
        contracts=contracts, underlying=underlying,
        spot_price=float(spot_at_ref), direction=str(side).upper(),
        moneyness=str(moneyness),
    )
    if not sel:
        return None
    return {
        "instrument_key": sel["instrument_key"],
        "strike": int(sel["strike"]),
        "side": str(sel["side"]).upper(),
        "moneyness": str(moneyness),
    }


def premium_series_for_key(option_candles: pd.DataFrame,
                           instrument_key: str) -> Tuple[np.ndarray, np.ndarray]:
    """(ts[], premium[]) for one instrument_key, ascending by ts. premium = close.
    Empty arrays when the key is absent."""
    if option_candles is None or option_candles.empty:
        return np.array([], dtype="int64"), np.array([], dtype="float64")
    sub = option_candles[option_candles["instrument_key"] == instrument_key]
    if sub.empty:
        return np.array([], dtype="int64"), np.array([], dtype="float64")
    sub = sub.sort_values("ts")
    return sub["ts"].to_numpy(dtype="int64"), sub["close"].to_numpy(dtype="float64")


def momentum_triggered(*, premium_now: float, ref_premium: float,
                       pct: Optional[float] = None, pts: Optional[float] = None) -> bool:
    """True once premium_now has risen to/above the momentum trigger from ref.
    Exactly one of pct (% of ref) or pts (absolute premium points) is used."""
    if pct is not None and pts is not None:
        raise ValueError("momentum_triggered: pass exactly one of pct or pts, not both")
    if ref_premium is None or ref_premium <= 0:
        return False
    if pct is not None:
        return float(premium_now) >= float(ref_premium) * (1.0 + float(pct) / 100.0)
    if pts is not None:
        return float(premium_now) >= float(ref_premium) + float(pts)
    return False


def walk_premium_momentum(*, ts, premium, ref_premium: float,
                          entry_pct: Optional[float] = None,
                          entry_pts: Optional[float] = None,
                          target_pct: Optional[float] = None,
                          target_pts: Optional[float] = None,
                          stop_pct: Optional[float] = None,
                          stop_pts: Optional[float] = None,
                          trail=None) -> Dict[str, Any]:
    """Walk a single locked strike's premium series (ascending ts):
    1. find the FIRST bar whose premium crosses the momentum trigger -> ENTRY;
    2. from the next bar, exit on premium stop / target (continuous), else at EOD.
    Look-ahead safe: never reads a future bar for the current decision. `trail`
    is a callable(entry_premium, running_high, base_stop)->stop for Phase 2;
    None => continuous base stop only. Returns a trade dict (entered=False if the
    momentum trigger never fired)."""
    ts = list(ts); premium = [float(p) for p in premium]
    n = len(premium)
    # --- entry: first cross ---
    entry_i = None
    for i in range(n):
        if momentum_triggered(premium_now=premium[i], ref_premium=ref_premium,
                              pct=entry_pct, pts=entry_pts):
            entry_i = i
            break
    if entry_i is None:
        return {"entered": False}
    entry_premium = premium[entry_i]
    base_stop = _stop_or_target_level(entry_premium, stop_pct, stop_pts, is_stop=True)
    target = _stop_or_target_level(entry_premium, target_pct, target_pts, is_stop=False)
    running_high = entry_premium
    # --- exit: from the bar AFTER entry (fill at entry bar's premium) ---
    for j in range(entry_i + 1, n):
        p = premium[j]
        running_high = max(running_high, p)
        stop = base_stop
        if trail is not None and base_stop is not None:
            stop = trail(entry_premium=entry_premium, running_high=running_high,
                         base_stop=base_stop)
        # stop-first (pessimistic), mirroring the spot engine's intrabar_exit
        if stop is not None and p <= stop:
            return _exit(ts, entry_i, entry_premium, j, stop, "STOP")
        if target is not None and p >= target:
            return _exit(ts, entry_i, entry_premium, j, target, "TARGET")
    # EOD
    return _exit(ts, entry_i, entry_premium, n - 1, premium[n - 1], "EOD")


def _stop_or_target_level(entry: float, pct: Optional[float], pts: Optional[float], *, is_stop: bool):
    # Fail loud on ambiguous config, symmetric with momentum_triggered — never
    # silently prefer one unit over the other.
    if pct is not None and pts is not None:
        raise ValueError("stop/target: pass exactly one of pct or pts, not both")
    if pct is not None:
        return entry * (1.0 - pct / 100.0) if is_stop else entry * (1.0 + pct / 100.0)
    if pts is not None:
        return entry - pts if is_stop else entry + pts
    return None


def _exit(ts, entry_i, entry_premium, exit_i, exit_premium, reason) -> Dict[str, Any]:
    return {
        "entered": True,
        "entry_ts": ts[entry_i], "entry_premium": round(float(entry_premium), 4),
        "exit_ts": ts[exit_i], "exit_premium": round(float(exit_premium), 4),
        "exit_reason": reason,
        "premium_pnl": round(float(exit_premium) - float(entry_premium), 4),
        "bars_held": int(exit_i - entry_i),
    }


def stepped_trail_stop(*, entry_premium: float, running_high: float,
                       base_stop: float, x: float, y: float) -> float:
    """AlgoTest discrete ratchet: for every X favorable move (premium above entry),
    raise the stop by Y. stop = base_stop + floor(favorable / X) * Y. NOT a
    continuous high-water-minus-offset trail. Never below base_stop."""
    if x is None or x <= 0 or y is None or y <= 0:
        return base_stop
    favorable = float(running_high) - float(entry_premium)
    if favorable < x:
        return base_stop
    steps = int(favorable // float(x))
    return float(base_stop) + steps * float(y)
