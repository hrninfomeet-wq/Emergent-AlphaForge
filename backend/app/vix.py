"""India VIX ingestion + as-of join for the volatility-context layer.

India VIX is the implied-volatility index for NIFTY options. It is the single
most important context variable for an options buyer: high VIX = expensive,
fast-decaying premiums but explosive potential on sharp moves; low VIX = cheap
optionality. The user specifically flagged VIX>15 near expiry as the regime
where premiums can run 2x-10x on a strong move.

VIX is stored in the same `candles_1m` warehouse under instrument "INDIAVIX"
(an AUX instrument, never treated as an option underlying). This module:
  - fetches/persists VIX 1m candles via the existing Upstox machinery, and
  - provides an as-of join: for a set of trade timestamps, return the most
    recent VIX close at/before each timestamp (VIX moves slowly intraday, so an
    as-of join is the right, leakage-free mapping).

Pure-ish: the as-of join works on an in-memory list of VIX candles so it is
unit-testable without a DB.
"""
from __future__ import annotations

import bisect
from typing import Any, Dict, List, Optional

import pandas as pd

from app.instruments import AUX_INSTRUMENT_KEYS

VIX_INSTRUMENT = "INDIAVIX"


def vix_instrument_key() -> str:
    return AUX_INSTRUMENT_KEYS[VIX_INSTRUMENT]


def build_asof_index(vix_candles: List[Dict[str, Any]]) -> Dict[str, List]:
    """Build a sorted (ts, close) index for fast as-of lookups.

    Returns {"ts": [...sorted...], "close": [...aligned...]}.
    """
    rows = sorted(
        ((int(c["ts"]), float(c.get("close", c.get("vix", 0.0)))) for c in vix_candles if c.get("ts") is not None),
        key=lambda x: x[0],
    )
    return {"ts": [r[0] for r in rows], "close": [r[1] for r in rows]}


def vix_asof(index: Dict[str, List], ts_ms: Any, max_staleness_ms: Optional[int] = None) -> Optional[float]:
    """Most recent VIX close at/before ts_ms. None if nothing precedes it.

    `max_staleness_ms` optionally rejects a VIX print older than the limit
    (e.g. don't use last week's VIX for today's trade). Default None = no limit
    beyond "at or before".
    """
    ts_list = index.get("ts") or []
    if not ts_list or ts_ms is None:
        return None
    try:
        t = int(ts_ms)
    except (TypeError, ValueError):
        return None
    pos = bisect.bisect_right(ts_list, t) - 1
    if pos < 0:
        return None
    if max_staleness_ms is not None and (t - ts_list[pos]) > max_staleness_ms:
        return None
    return round(index["close"][pos], 2)


def vix_by_session_map(
    spot_df: pd.DataFrame,
    vix_candles: List[Dict[str, Any]],
    *,
    ref_time: str = "09:31",
    max_staleness_ms: Optional[int] = None,
) -> Dict[str, float]:
    """Session-date -> VIX gate value (Phase 5A.2 VIX gate route wiring).

    Per session: the VIX close as-of <= that session's REF BAR ts (the same
    ref-bar convention the sim's own strike lock uses -- first spot bar with
    ``ist_time >= ref_time``). ``max_staleness_ms`` bounds how far back the
    as-of lookup may reach, so a session with NO VIX print reaching it (e.g.
    a gap far longer than the fallback window) is simply ABSENT from the
    returned map -- callers must treat "absent" as "unverifiable", never as
    "pass" (see the VIX gate's ``sessions_excluded_vix_missing`` counter).

    Pure (no I/O): the caller loads ``vix_candles`` (INDIAVIX candles_1m rows)
    and passes them in. Ref-bar-time VIX is known at the lock moment, so this
    introduces no look-ahead."""
    if spot_df is None or spot_df.empty:
        return {}
    index = build_asof_index(vix_candles)
    result: Dict[str, float] = {}
    for session, sdf in spot_df.groupby("session_date"):
        sdf = sdf.sort_values("ts")
        ref_rows = sdf[sdf["ist_time"] >= str(ref_time)]
        if ref_rows.empty:
            continue
        ref_ts = int(ref_rows.iloc[0]["ts"])
        v = vix_asof(index, ref_ts, max_staleness_ms=max_staleness_ms)
        if v is not None:
            result[str(session)] = v
    return result


def annotate_trades_with_vix(
    spot_trades: List[Dict[str, Any]],
    vix_candles: List[Dict[str, Any]],
    *,
    max_staleness_ms: Optional[int] = None,
) -> int:
    """Attach a `vix` field to each spot trade via as-of join. Returns count tagged."""
    index = build_asof_index(vix_candles)
    tagged = 0
    for t in spot_trades:
        v = vix_asof(index, t.get("entry_ts"), max_staleness_ms=max_staleness_ms)
        if v is not None:
            t["vix"] = v
            tagged += 1
    return tagged
