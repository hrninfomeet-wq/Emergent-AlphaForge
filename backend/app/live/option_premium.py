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


# Any epoch value larger than this is milliseconds, not seconds. 1e11 seconds is
# ~year 5138, so no real seconds timestamp reaches it, while every millisecond
# timestamp after 1973 exceeds it — a safe, unambiguous ms-vs-s discriminator.
_MS_EPOCH_THRESHOLD = 1e11


def _to_epoch_seconds(ts: Optional[float]) -> Optional[float]:
    """Normalize an epoch timestamp to SECONDS.

    Upstox ticks (``ltt`` / ``currentTs``) and warehouse candles carry epoch
    MILLISECONDS. Freshness here is compared against a SECONDS ``now_ts`` (callers
    pass ``time.time()`` / ``datetime.timestamp()``), so an unnormalized ms tick
    reads ~1.75e12 seconds in the future and NEVER falls inside ``max_age_sec`` —
    the bug that silently refused every deploy-to-live entry. Divide ms → s."""
    if ts is None:
        return None
    return ts / 1000.0 if ts > _MS_EPOCH_THRESHOLD else ts


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
    candle_ts: Any = None,
    max_candle_age_sec: Optional[float] = None,
) -> Dict[str, Any]:
    """Choose the best available option premium for *instrument_key*.

    Priority
    --------
    1. Fresh live tick — *tick* is not None, has a finite-positive
       ``last_price``, and the tick timestamp (``ts`` or ``received_ts``) is
       within *max_age_sec* of *now_ts*. Tick timestamps are epoch MILLISECONDS
       (Upstox ``ltt``/``currentTs``) and are normalized to seconds to match the
       seconds *now_ts* (see ``_to_epoch_seconds``).
    2. Last candle close — *candle_close* is finite and positive. When
       *candle_ts* is given, the result carries ``candle_age_sec``; when
       *max_candle_age_sec* is also given, a candle older than that is REJECTED
       (falls through to "none") rather than shown as if current.
    3. Neither available → premium None, source "none".

    ``now_ts`` is epoch SECONDS. Never raises on garbage input.

    Returns a dict::

        {
            "premium": float | None,
            "source": "live_tick" | "last_candle" | "none",
            "fresh": bool,
            "ts": float | None,          # tick ts (seconds) when source == "live_tick"
            "candle_age_sec": float | None,   # present when source == "last_candle" and candle_ts given
        }
    """
    # --- try live tick ---
    if tick is not None:
        price = _finite_positive(tick.get("last_price"))
        if price is not None:
            # Accept either "ts" or "received_ts"; prefer "ts". Normalize ms → s.
            tick_ts = _to_epoch_seconds(
                _normalize_float(tick.get("ts")) or _normalize_float(tick.get("received_ts")))
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
        cts = _to_epoch_seconds(_normalize_float(candle_ts))
        candle_age = (now_ts - cts) if cts is not None else None
        too_old = (max_candle_age_sec is not None and candle_age is not None
                   and candle_age > max_candle_age_sec)
        if not too_old:
            return {
                "premium": candle_price,
                "source": "last_candle",
                "fresh": False,
                "ts": None,
                "candle_age_sec": candle_age,
            }

    # --- nothing usable ---
    return {
        "premium": None,
        "source": "none",
        "fresh": False,
        "ts": None,
    }
