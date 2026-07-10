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
