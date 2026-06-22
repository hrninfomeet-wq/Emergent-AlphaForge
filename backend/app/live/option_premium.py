"""Pure, host-testable helpers for resolving a current option premium.

Two public functions:

    match_contract(contracts, *, strike, side, expiry_date) -> dict | None
        Find the unique contract row matching all three keys.  Returns None
        when there is no match or more than one match (ambiguous).

    resolve_premium(*, instrument_key, tick, candle_close, now_ts,
                    max_age_sec=120) -> dict
        Choose the best available price: live tick > last candle > none.
        Never raises; skips non-finite / non-positive / garbage values.

These helpers contain zero I/O.  The FastAPI route wires them to real sources.
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _finite_positive(value: Any) -> Optional[float]:
    """Return *value* as a float iff it is finite and strictly positive.

    Accepts numbers and numeric strings.  Returns None for None, '', NaN,
    inf, -inf, 0, negatives, and anything that cannot be converted.
    """
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if math.isfinite(f) and f > 0:
        return f
    return None


def _normalize_float(value: Any) -> Optional[float]:
    """Convert value to float, returning None on any failure."""
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# match_contract
# ---------------------------------------------------------------------------

def match_contract(
    contracts: List[Dict[str, Any]],
    *,
    strike: Any,
    side: str,
    expiry_date: str,
) -> Optional[Dict[str, Any]]:
    """Return the unique contract row matching *strike*, *side*, *expiry_date*.

    Rules
    -----
    * Strike comparison is float-tolerant: both the argument and the stored
      value are cast to float before comparing.  Passing "25000" matches a
      row with strike 25000.0.
    * Side is uppercased before matching ("ce" → "CE").
    * Returns None when zero rows match (no match) or more than one row
      matches (ambiguous).  Never raises.
    """
    try:
        strike_f = float(strike)
    except (TypeError, ValueError):
        return None

    side_upper = str(side).upper()

    matches = []
    for row in contracts:
        try:
            row_strike = float(row.get("strike", ""))
        except (TypeError, ValueError):
            continue
        if (
            abs(row_strike - strike_f) < 1e-6
            and str(row.get("side", "")).upper() == side_upper
            and str(row.get("expiry_date", "")) == str(expiry_date)
        ):
            matches.append(row)

    if len(matches) == 1:
        return matches[0]
    return None  # 0 (no match) or >1 (ambiguous)


# ---------------------------------------------------------------------------
# resolve_premium
# ---------------------------------------------------------------------------

def resolve_premium(
    *,
    instrument_key: str,
    tick: Optional[Dict[str, Any]],
    candle_close: Any,
    now_ts: float,
    max_age_sec: float = 120,
) -> Dict[str, Any]:
    """Choose the best available option premium for *instrument_key*.

    Priority
    --------
    1. Fresh live tick — *tick* is not None, has a finite-positive
       ``last_price``, and the tick timestamp (``ts`` or ``received_ts``,
       seconds) is within *max_age_sec* of *now_ts*.
    2. Last candle close — *candle_close* is finite and positive.
    3. Neither available → premium None, source "none".

    Never raises on garbage input (missing keys, wrong types, NaN, etc.).

    Returns a dict::

        {
            "premium": float | None,
            "source": "live_tick" | "last_candle" | "none",
            "fresh": bool,
            "ts": float | None,          # tick ts when source == "live_tick"
        }
    """
    # --- try live tick ---
    if tick is not None:
        price = _finite_positive(tick.get("last_price"))
        if price is not None:
            # Accept either "ts" or "received_ts"; prefer "ts".
            tick_ts = _normalize_float(tick.get("ts")) or _normalize_float(tick.get("received_ts"))
            if tick_ts is not None and abs(now_ts - tick_ts) <= max_age_sec:
                return {
                    "premium": price,
                    "source": "live_tick",
                    "fresh": True,
                    "ts": tick_ts,
                }

    # --- try last candle close ---
    candle_price = _finite_positive(candle_close)
    if candle_price is not None:
        return {
            "premium": candle_price,
            "source": "last_candle",
            "fresh": False,
            "ts": None,
        }

    # --- nothing usable ---
    return {
        "premium": None,
        "source": "none",
        "fresh": False,
        "ts": None,
    }
