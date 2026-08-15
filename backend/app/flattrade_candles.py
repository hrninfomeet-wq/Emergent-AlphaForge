"""Flattrade TPSeries as a warehouse-shaped candle source — the fallback broker.

Upstox is the primary historical source; this is what recovery falls back to when
Upstox is down, rate-limited, or its daily token has expired. TPSeries accepts
explicit ``st``/``et`` bounds, so unlike Upstox's intraday endpoint (no date
arguments, today wholesale) a gap can be requested precisely.

## Source agreement, measured

Comparing 60 common minutes of NIFTY on 2026-08-14, TPSeries vs Upstox historical:

    high    differs on 0/60 bars    max delta 0.00
    low     differs on 0/60 bars    max delta 0.00
    close   differs on 0/60 bars    max delta 0.00
    open    differs on 2/60 bars    max delta 1.85

High/low/close are identical; the two vendors disagree only on the OPEN of a
couple of bars, by at most 1.85 points on a ~24,350 index (0.008%). The clearest
case is the session's first bar: Upstox reports the 09:15 open as the last
pre-open price, TPSeries as the first price traded inside the minute. That is a
vendor convention difference, not an error in either — but it means a day
assembled from BOTH sources can carry a mixed convention, which is why Upstox
stays primary and this is only ever a fallback.

## Session filtering is mandatory

TPSeries returned one bar OUTSIDE 09:15-15:29 for the window requested (a 09:14
pre-open marker with O=H=L=C). `candle_gap` ignores unexpected timestamps when
assessing, but `persist_candles_df` would happily WRITE them, polluting
`candles_1m` with rows no session model accounts for. Rows are therefore clipped
to the day's session grid here.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional, Tuple

import pandas as pd

from app.candle_gap import expected_minute_ts
from app.session_spec import SPOT

log = logging.getLogger(__name__)

#: Noren index tokens, resolved live via SearchScrip on 2026-08-15 and verified by
#: fetching real candles through TPSeries. All three carry ``instname: UNDIND``.
#: Hard-coded rather than resolved per call because index tokens are stable and a
#: SearchScrip round trip per recovery would double the rate cost for no benefit;
#: if one ever changes, TPSeries returns empty and recovery reports "unresolved"
#: rather than writing anything wrong.
NOREN_INDEX_TOKENS: Dict[str, Tuple[str, str]] = {
    "NIFTY": ("NSE", "26000"),        # tsym "Nifty 50"
    "BANKNIFTY": ("NSE", "26009"),    # tsym "Nifty Bank"
    "SENSEX": ("BSE", "1"),           # tsym "SENSEX"
}


def make_tpseries_fetcher(
    client_factory: Any,
    *,
    segment: str = SPOT,
) -> Any:
    """Build a ``fallback_fetch(instrument, start_ms, end_ms) -> DataFrame``.

    Returns an async callable matching `candle_recovery`'s fallback seam. Yields an
    EMPTY frame — never raises — for an instrument with no known Noren token or
    when no broker client is available, so an unmapped instrument degrades to
    "fallback produced nothing" rather than taking the recovery pass down.
    """
    async def fetch(instrument: str, start_ms: int, end_ms: int) -> pd.DataFrame:
        inst = str(instrument).upper()
        mapped: Optional[Tuple[str, str]] = NOREN_INDEX_TOKENS.get(inst)
        if mapped is None:
            log.info("tpseries fallback: no Noren token for %s — skipping", inst)
            return pd.DataFrame()

        client = await client_factory()
        if client is None:
            return pd.DataFrame()

        exch, token = mapped
        rows = await client.time_price_series(
            exch=exch, token=token, start_ms=int(start_ms), end_ms=int(end_ms))
        if not rows:
            return pd.DataFrame()

        # Clip to the session grid of every day the window touches. TPSeries
        # returns pre-open marker bars outside 09:15-15:29 which would otherwise
        # be persisted as real candles.
        days = {pd.Timestamp(r["ts"], unit="ms", tz="Asia/Kolkata").strftime("%Y-%m-%d")
                for r in rows}
        allowed = set()
        for day in days:
            allowed.update(expected_minute_ts(day, segment=segment))
        kept = [r for r in rows if r["ts"] in allowed]
        if not kept:
            return pd.DataFrame()

        return pd.DataFrame([{
            "instrument": inst,
            "ts": int(r["ts"]),
            "datetime": pd.Timestamp(r["ts"], unit="ms", tz="Asia/Kolkata").isoformat(),
            "open": float(r["open"]),
            "high": float(r["high"]),
            "low": float(r["low"]),
            "close": float(r["close"]),
            "volume": float(r.get("volume") or 0),
        } for r in kept])

    return fetch
