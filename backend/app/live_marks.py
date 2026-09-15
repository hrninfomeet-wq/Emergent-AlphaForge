"""Re-mark the broker position book against the live Upstox tick.

WHY THIS EXISTS
---------------
The Live page's LTP / MTM come from the Flattrade REST position book, polled at
15s to bound the shared broker rate budget. Measured tick-to-pixel on that path
was p50 ~7.8s / p95 ~14.5s — unusable to trade against. Meanwhile the Upstox WS
is already streaming ticks for the very same contracts into
``upstox_stream_manager._latest_ticks``, for free.

This module is the pure bridge: broker book (source of truth for quantity,
average price and realized P&L) x live tick (source of truth for LTP), with
**zero** additional broker calls.

THE ANCHORING RULE
------------------
We never re-derive Noren's MTM formula. Noren computes ``urmtom`` against a base
price whose choice varies (carry-forward positions mark against the upload price,
intraday against the day average), and guessing wrong would put a wrong number on
a real-money screen. Instead we apply the *delta*::

    urmtom_live = urmtom_broker + (lp_tick - lp_broker) * netqty * prcftr * mult

The derivative of MTM with respect to LTP is ``netqty * prcftr * mult`` whatever
base price the broker chose, so this is exact, and it collapses to the broker's
own number when the tick equals the broker ``lp``. Realized P&L (``rpnl``) only
moves on a fill and is passed through untouched.

Every row reports ``mark_source`` ("tick" | "broker") and ``mark_age_ms`` so the
UI can never present a broker-stale number as if it were live.
"""
from __future__ import annotations

import math
import re
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

TickLookup = Callable[[str], Optional[Dict[str, Any]]]

#: Identity of a tradeable contract: (underlying, expiry ISO, strike, side).
Identity = Tuple[str, str, float, str]

_MONTHS = {
    "JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
    "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12,
}

# "NIFTY 15SEP26 23350 CE" — the Flattrade display name.
_DNAME_RE = re.compile(
    r"^\s*(?P<sym>[A-Z0-9&\-]+)\s+(?P<d>\d{1,2})(?P<mon>[A-Z]{3})(?P<y>\d{2,4})\s+"
    r"(?P<strike>\d+(?:\.\d+)?)\s+(?P<side>CE|PE)\s*$"
)
# "NIFTY15SEP26C23350" — the Flattrade trading symbol.
_TSYM_RE = re.compile(
    r"^(?P<sym>[A-Z0-9&\-]+?)(?P<d>\d{1,2})(?P<mon>[A-Z]{3})(?P<y>\d{2,4})"
    r"(?P<cp>[CP])(?P<strike>\d+(?:\.\d+)?)$"
)


def _to_float(value: Any) -> Optional[float]:
    """Float or None. Rejects NaN/inf — a non-finite price must never reach a screen."""
    if value is None or value == "":
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def _f(value: Any, default: float = 0.0) -> float:
    out = _to_float(value)
    return default if out is None else out


def _expiry_iso(day: str, mon: str, year: str) -> Optional[str]:
    month = _MONTHS.get(mon.upper())
    if month is None:
        return None
    y = int(year)
    if y < 100:
        y += 2000
    return f"{y:04d}-{month:02d}-{int(day):02d}"


def parse_position_identity(pos: Dict[str, Any]) -> Optional[Identity]:
    """Parse a broker position row into (underlying, expiry ISO, strike, side).

    Prefers ``dname`` (space-delimited, unambiguous) and falls back to ``tsym``.
    Returns None for anything it cannot parse cleanly — a guess here would mark a
    position against a different contract's premium.
    """
    dname = str(pos.get("dname") or "").strip().upper()
    match = _DNAME_RE.match(dname)
    if match:
        expiry = _expiry_iso(match.group("d"), match.group("mon"), match.group("y"))
        strike = _to_float(match.group("strike"))
        if expiry and strike is not None:
            return (match.group("sym"), expiry, strike, match.group("side"))

    tsym = str(pos.get("tsym") or "").strip().upper()
    match = _TSYM_RE.match(tsym)
    if match:
        expiry = _expiry_iso(match.group("d"), match.group("mon"), match.group("y"))
        strike = _to_float(match.group("strike"))
        if expiry and strike is not None:
            side = "CE" if match.group("cp") == "C" else "PE"
            return (match.group("sym"), expiry, strike, side)
    return None


def build_contract_index(contracts: Iterable[Dict[str, Any]]) -> Dict[Identity, str]:
    """Index option contracts by identity -> Upstox instrument_key.

    Keyed by identity rather than exchange token ON PURPOSE: exchange tokens are
    recycled across expiries (token 47291 is a Dec-2024 22600 PE, an Aug-2025
    25350 PE *and* a Sep-2026 23350 CE), so a token join silently resolves to an
    expired contract that will never tick.
    """
    index: Dict[Identity, str] = {}
    for row in contracts or []:
        key = str(row.get("instrument_key") or "")
        underlying = str(row.get("underlying") or "").strip().upper()
        expiry = str(row.get("expiry_date") or "").strip()[:10]
        strike = _to_float(row.get("strike"))
        side = str(row.get("side") or "").strip().upper()
        if not (key and underlying and expiry and strike is not None and side in ("CE", "PE")):
            continue
        ident: Identity = (underlying, expiry, strike, side)
        # A 2-part instrument_key is the live contract; 3-part keys are the
        # expired-contract archive. Never let an archive row shadow a live one.
        if ident in index and key.count("|") > index[ident].count("|"):
            continue
        index[ident] = key
    return index


def resolve_tick_key(pos: Dict[str, Any], contract_index: Dict[Identity, str]) -> Optional[str]:
    """Upstox instrument_key for a broker position row, or None when unknown."""
    ident = parse_position_identity(pos)
    if ident is None:
        return None
    return contract_index.get(ident)


def _tick_age_ms(tick: Dict[str, Any], now_ms: int) -> Optional[int]:
    """Age of a tick in ms.

    Prefers ``ingest_ts`` — OUR local receive clock. ``received_ts`` and ``ts``
    are broker-side clocks (measured p50 254ms / p95 1.7s apart from each other),
    so using them for a freshness gate mixes broker skew into our staleness test.
    """
    for field in ("ingest_ts", "received_ts", "ts"):
        raw = tick.get(field)
        if raw is None:
            continue
        try:
            return int(now_ms) - int(raw)
        except (TypeError, ValueError):
            continue
    return None


def mark_positions(
    positions: List[Dict[str, Any]],
    *,
    tick_lookup: TickLookup,
    now_ms: int,
    max_age_ms: int,
    contract_index: Dict[Identity, str],
) -> Dict[str, Any]:
    """Return the position book re-marked to the live tick.

    Every input row is returned (a row we cannot identify still renders at its
    broker values), annotated with ``mark_source`` and ``mark_age_ms``.
    ``tick_keys`` lists the contracts an OPEN position depends on, so the stream
    auto-follow can keep them subscribed.
    """
    out: List[Dict[str, Any]] = []
    tick_keys: List[str] = []
    total_unrealized = 0.0
    total_realized = 0.0
    marked_count = 0

    for pos in positions or []:
        row = dict(pos)
        qty = _f(pos.get("netqty"))
        lp_broker = _to_float(pos.get("lp"))
        urmtom_broker = _f(pos.get("urmtom"))
        rpnl = _f(pos.get("rpnl"))
        slope = qty * _f(pos.get("prcftr"), 1.0) * _f(pos.get("mult"), 1.0)

        key = resolve_tick_key(pos, contract_index)
        if key and qty != 0 and key not in tick_keys:
            tick_keys.append(key)

        lp_out = lp_broker
        urmtom_out = urmtom_broker
        source = "broker"
        age_ms: Optional[int] = None

        tick = tick_lookup(key) if key else None
        if tick:
            lp_tick = _to_float(tick.get("last_price"))
            age_ms = _tick_age_ms(tick, now_ms)
            fresh = (
                lp_tick is not None
                and lp_tick > 0
                and (age_ms is None or age_ms <= max_age_ms)
            )
            if fresh and lp_broker is not None:
                lp_out = lp_tick
                urmtom_out = urmtom_broker + (lp_tick - lp_broker) * slope
                source = "tick"
                marked_count += 1

        row["lp"] = lp_out
        row["urmtom"] = round(urmtom_out, 2)
        row["rpnl"] = rpnl
        row["tick_key"] = key
        row["mark_source"] = source
        row["mark_age_ms"] = age_ms if source == "tick" else None
        out.append(row)

        total_unrealized += row["urmtom"]
        total_realized += rpnl

    return {
        "positions": out,
        "day_pnl": round(total_unrealized + total_realized, 2),
        "unrealized": round(total_unrealized, 2),
        "realized": round(total_realized, 2),
        "tick_keys": tick_keys,
        "marked": marked_count,
        "count": len(out),
    }
