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


def premium_ohlc_for_key(option_candles: pd.DataFrame,
                         instrument_key: str) -> Dict[str, np.ndarray]:
    """{ts, open, high, low, close} arrays for one instrument_key, ascending by ts.
    close = premium (the momentum/target basis); low/open drive GAP-HONEST stop
    fills. Missing o/h/l columns fall back to close (so close-only fixtures still
    behave). Empty arrays when the key is absent."""
    empty = np.array([], dtype="float64")
    blank = {"ts": np.array([], dtype="int64"), "open": empty, "high": empty, "low": empty, "close": empty}
    if option_candles is None or option_candles.empty:
        return blank
    sub = option_candles[option_candles["instrument_key"] == instrument_key]
    if sub.empty:
        return blank
    sub = sub.sort_values("ts")
    close = sub["close"].to_numpy(dtype="float64")
    col = lambda name: sub[name].to_numpy(dtype="float64") if name in sub.columns else close
    return {"ts": sub["ts"].to_numpy(dtype="int64"), "open": col("open"),
            "high": col("high"), "low": col("low"), "close": close}


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
                          trail=None, low=None, open_=None) -> Dict[str, Any]:
    """Walk a single locked strike's premium series (ascending ts):
    1. find the FIRST bar whose premium (close) crosses the momentum trigger -> ENTRY;
    2. from the next bar, exit on premium stop / target, else at EOD.

    STOP fills are GAP-HONEST when the per-bar ``low`` and ``open_`` arrays are given
    (the real backtest path passes them): the stop is TOUCHED intra-bar when the bar
    LOW <= stop, and FILLED at ``min(stop, bar_open)`` — so a premium that gaps down
    THROUGH the stop books the real (worse) fill, not the stop level. This is the
    dominant tail risk for an option buyer, so it must not be flattered. When low/open
    are omitted (simple unit fixtures / the legacy path), the stop falls back to a
    close-touch fill at the stop level. Target stays conservative (close >= target,
    fill at target). Look-ahead safe: never reads a future bar for the current
    decision. ``trail`` is the Phase-2 stepped ratchet. Returns entered=False if the
    momentum trigger never fired."""
    ts = list(ts); premium = [float(p) for p in premium]
    lo = [float(v) for v in low] if low is not None else None
    op = [float(v) for v in open_] if open_ is not None else None
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
        c = premium[j]
        running_high = max(running_high, c)
        stop = base_stop
        if trail is not None and base_stop is not None:
            stop = trail(entry_premium=entry_premium, running_high=running_high,
                         base_stop=base_stop)
        # stop-first (pessimistic), mirroring the spot engine's intrabar_exit.
        if stop is not None:
            # touch: intra-bar LOW when available (gap-honest), else the close.
            touched = (lo[j] <= stop) if lo is not None else (c <= stop)
            if touched:
                # fill: gap-honest min(stop, open) — a bar that OPENED below the stop
                # gapped through it and fills worse; else fills at the stop.
                fill = min(stop, op[j]) if op is not None else stop
                return _exit(ts, entry_i, entry_premium, j, fill, "STOP")
        if target is not None and c >= target:
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
