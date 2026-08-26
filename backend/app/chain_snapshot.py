"""Pure normalizer for an Upstox option-chain response -> a stored snapshot.

`chain_snapshots` is the only dataset in this warehouse that **cannot be
backfilled**. Spot candles gap-fill, option candles gap-fill, expired contracts
backfill — but option-chain structure is a point-in-time observation, and a
minute not recorded is a minute that never existed. The collection has carried
an index and no writer since it was created, so every session to date is gone.
That asymmetry sets the design rules here:

* **Record raw, derive nothing.** PCR, max pain, OI walls and IV rank can all be
  recomputed from a faithful snapshot at any point in the future. A derived
  value stored today freezes today's definition into the historical record and
  cannot be un-frozen. The API's own ``pcr`` is kept ON THE STRIKE it belongs
  to, for cross-checking a later local computation — never summarised to
  document level. The first real capture made the reason concrete: the vendor's
  ``pcr`` is per-strike (PE OI / CE OI at that strike) and the lowest strike
  returned **789.09**, which promoted to a document field would have read as a
  chain-wide put/call ratio and been wrong by three orders of magnitude.

* **Missing is ``None``; zero is ``0.0``.** An absent open interest and an open
  interest of zero are different facts about the world. Collapsing them is
  precisely the defect that made the old ``vix_boost_threshold`` knob worthless:
  a rule scoring zero on missing data is indistinguishable from one scoring zero
  on real data. :func:`_num` therefore refuses to invent a default.

* **Capture every field offered.** In particular ``bid_price`` / ``ask_price``:
  the optimizer's ``research_eligibility`` guard blocks promotion today with
  ``no_point_in_time_execution_surface``, and this stream is the only thing that
  can ever close it. The repo's cost model currently assumes a flat 1% of
  premium per side — an assumption that was 54% of gross P&L in the operator's
  2026-08-23 SENSEX run, and the single least-validated number in the system.

Pure: no motor, no I/O, no network, no FastAPI. The caller does the fetching and
the writing (see ``app.chain_recorder``), which keeps this module trivially
testable and host-importable.
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Sequence, Tuple

#: Stamped on every document so a later reader can tell which feed produced it.
#: A second source (a broker chain, a different vendor) must use its own value
#: rather than pretending to be this one — provenance is not cosmetic here.
CHAIN_SOURCE = "upstox_rest_v2"

#: `MarketData` on the Upstox response. Order is the stored key order.
MARKET_FIELDS: Tuple[str, ...] = (
    "ltp", "close_price", "volume", "oi", "prev_oi",
    "bid_price", "bid_qty", "ask_price", "ask_qty",
)

#: `AnalyticsData` on the Upstox response.
GREEK_FIELDS: Tuple[str, ...] = ("iv", "delta", "gamma", "theta", "vega", "pop")


def _num(value: Any) -> Optional[float]:
    """Coerce to a finite float, or ``None``.

    Deliberately never returns 0.0 as a fallback. ``0`` reaching this function
    as a real value is preserved as ``0.0``; anything unusable — absent, blank,
    non-numeric, NaN, +/-inf, a bool, a container — becomes ``None``.

    ``bool`` is excluded explicitly because ``isinstance(True, int)`` is True in
    Python, so a stray boolean would otherwise be recorded as a price of 1.0.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (dict, list, tuple, set)):
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def _leg(raw: Any) -> Optional[Dict[str, Any]]:
    """One side (CE or PE) of a strike, or ``None`` when the side is absent.

    ``None`` for the whole leg means "no such contract in this response", which
    is a different fact from "quoted at zero" and is kept distinct for the same
    reason :func:`_num` keeps missing and zero distinct.

    Reads the documented nesting (``market_data`` / ``option_greeks``) and falls
    back to a flat lookup on the leg itself. The fallback is defensive rather
    than speculative: this parser runs unattended against a live vendor API, and
    an unannounced flattening would otherwise record an entire chain of `None`s
    that nobody could reconstruct afterwards.
    """
    if not isinstance(raw, dict):
        return None

    market = raw.get("market_data")
    greeks = raw.get("option_greeks")
    market = market if isinstance(market, dict) else {}
    greeks = greeks if isinstance(greeks, dict) else {}

    def pick(source: Dict[str, Any], name: str) -> Optional[float]:
        return _num(source[name]) if name in source else _num(raw.get(name))

    leg: Dict[str, Any] = {}
    key = raw.get("instrument_key")
    leg["instrument_key"] = str(key) if key not in (None, "") else None
    for name in MARKET_FIELDS:
        leg[name] = pick(market, name)
    for name in GREEK_FIELDS:
        leg[name] = pick(greeks, name)
    return leg


def _iso_date(value: Any) -> Optional[str]:
    """First 10 characters of a date-ish value, when they look like an ISO date.

    The chain API types ``expiry`` as a datetime, while every other collection in
    this warehouse (``option_contracts``, ``options_1m``) keys ``expiry_date`` as
    a 10-character ISO string. Storing the richer form would silently break every
    join against them — the identity join that §11.1 established as the only safe
    one.
    """
    if value in (None, ""):
        return None
    text = str(value)[:10]
    if len(text) != 10 or text[4] != "-" or text[7] != "-":
        return None
    return text


def _rows(payload: Any) -> List[Dict[str, Any]]:
    """The strike rows of a response, or ``[]`` for anything unusable.

    Never raises. This runs on a timer against a network API, so a malformed or
    error-shaped body must degrade to "nothing captured this tick" — which the
    writer logs and skips — rather than take down the loop.
    """
    if not isinstance(payload, dict):
        return []
    data = payload.get("data")
    if not isinstance(data, list):
        return []
    return [row for row in data if isinstance(row, dict)]


def normalize_chain_response(
    payload: Any,
    *,
    instrument: str,
    captured_ts_ms: int,
    session_date: str,
) -> Dict[str, Any]:
    """Build the document stored in ``chain_snapshots``.

    ``captured_ts_ms`` is the capture time in epoch milliseconds (UTC), matching
    the ``ts`` convention used by ``candles_1m`` / ``options_1m`` so the three
    collections join on the same clock. ``session_date`` is the IST trading date.

    A strike whose ``strike_price`` is unusable is dropped rather than stored
    under a null key: a chain row with no strike cannot be joined to anything,
    and keeping it would put a permanent hole in a dataset that can never be
    re-derived.
    """
    rows = _rows(payload)

    strikes: List[Dict[str, Any]] = []
    spot: Optional[float] = None
    expiry: Optional[str] = None
    underlying_key: Optional[str] = None

    for row in rows:
        strike = _num(row.get("strike_price"))
        if strike is None:
            continue
        if spot is None:
            spot = _num(row.get("underlying_spot_price"))
        if expiry is None:
            expiry = _iso_date(row.get("expiry"))
        if underlying_key is None:
            key = row.get("underlying_key")
            underlying_key = str(key) if key not in (None, "") else None
        strikes.append({
            "strike": strike,
            # PER-STRIKE, not chain-level. The vendor puts `pcr` on every strike
            # row (PE OI / CE OI at THAT strike). The first real capture returned
            # 789.09 for the lowest strike — a deep-ITM put against a near-dead
            # call — which stored at document level would have read as a
            # chain-wide put/call ratio and been wrong by three orders of
            # magnitude. Kept where it belongs, and never summarised here.
            "pcr": _num(row.get("pcr")),
            "ce": _leg(row.get("call_options")),
            "pe": _leg(row.get("put_options")),
        })

    strikes.sort(key=lambda s: s["strike"])

    return {
        "instrument": str(instrument).upper(),
        "underlying_key": underlying_key,
        "expiry_date": expiry,
        "ts": int(captured_ts_ms),
        "session_date": str(session_date),
        "spot": spot,
        "source": CHAIN_SOURCE,
        "strike_count": len(strikes),
        "strikes": strikes,
    }
