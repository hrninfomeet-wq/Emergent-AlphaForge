"""Date- and segment-aware NSE/BSE trading-session bounds.

Until 2026-08-02 every segment shared one shape: 09:15-15:30 IST, 375 one-minute
candles. Effective **2026-08-03** SEBI's Closing Auction Session (CAS) framework
split that in two (NSE, BSE and MSEI alike):

  * **Cash / index** — continuous trading now ends at **15:15**. A closing
    auction runs 15:15-15:35 and prints the official close. Because every
    NIFTY 50 / SENSEX constituent is F&O-eligible, *no constituent trades during
    the auction*, so the published index simply stops moving: index candles from
    15:15 are **frozen** (zero-range, identical OHLC) until the auction close
    prints as a single large jump bar. Observed 2026-08-03 on NIFTY: 14 flat bars
    at 24573.35, then 24774.30 — a +200.95 point (+0.82%) one-bar gap.

  * **Equity derivatives** (index + stock F&O) — trade on to **15:40**, ten
    minutes past the old close. No auction applies to the derivatives segment.

So the two series we store no longer share a day length: spot is 375 bars,
options are 385. That is the reason this module exists.

Everything keys off `CAS_EFFECTIVE_DATE`. Dates before it MUST resolve to the
pre-CAS shape — roughly two years of stored history and every backtest built on
it have to keep computing exactly as they did. `tests/test_session_spec.py`
guards that boundary.

Usage:
    from app.session_spec import session_spec, Segment

    spec = session_spec("2026-08-03", "options")
    spec.close_min          # 940 == 15:40
    spec.expected_candles   # 385
    spec.in_cas(15 * 60 + 20)   # False — options have no auction
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import time as dtime
from typing import Any, Optional

import pandas as pd

from app.nse_calendar import MUHURAT_SESSIONS, is_trading_day

# Segment identifiers. "spot" covers the cash/index series we store as the
# signal source; "options" covers anything trading in the F&O segment.
SPOT = "spot"
OPTIONS = "options"

# The day SEBI's Closing Auction Session framework took effect across NSE/BSE/MSEI.
CAS_EFFECTIVE_ISO = "2026-08-03"

SESSION_OPEN_MIN = 9 * 60 + 15        # 09:15
LEGACY_CLOSE_MIN = 15 * 60 + 30       # 15:30, exclusive — the pre-CAS close
OPTIONS_CLOSE_MIN_CAS = 15 * 60 + 40  # 15:40, exclusive — F&O from 2026-08-03
CAS_START_MIN = 15 * 60 + 15          # 15:15 — continuous cash trading ends
CAS_END_MIN = LEGACY_CLOSE_MIN        # 15:30, exclusive

LEGACY_SESSION_CANDLES = 375          # 09:15..15:29 inclusive
OPTIONS_SESSION_CANDLES_CAS = 385     # 09:15..15:39 inclusive


@dataclass(frozen=True)
class SessionSpec:
    """Resolved session bounds for one (date, segment) pair.

    All minute fields are IST minutes-since-midnight. `close_min` and
    `cas_end_min` are **exclusive** upper bounds, matching the half-open
    convention the warehouse filters already use.
    """

    open_min: int
    close_min: int
    cas_start_min: Optional[int]
    cas_end_min: Optional[int]
    expected_candles: int

    @property
    def has_cas(self) -> bool:
        """True when a closing auction applies to this (date, segment)."""
        return self.cas_start_min is not None and self.cas_end_min is not None

    def contains(self, minute_of_day: int) -> bool:
        """True when an IST minute-of-day falls inside the tradeable session."""
        return self.open_min <= minute_of_day < self.close_min

    def in_cas(self, minute_of_day: int) -> bool:
        """True when an IST minute-of-day falls inside the closing auction.

        Always False for segments without an auction (derivatives) and for every
        date before the CAS framework took effect.
        """
        if self.cas_start_min is None or self.cas_end_min is None:
            return False
        return self.cas_start_min <= minute_of_day < self.cas_end_min


def cas_in_effect(iso_date: str) -> bool:
    """True when `iso_date` falls on or after the CAS framework start date.

    ISO-8601 dates compare correctly as strings, so no parsing is needed; an
    unparseable/short value simply sorts before the effective date and reads as
    pre-CAS, which is the safe default (it preserves legacy behaviour).
    """
    return str(iso_date) >= CAS_EFFECTIVE_ISO


def session_spec(iso_date: str, segment: str = SPOT) -> SessionSpec:
    """Return the session bounds for a trading date and segment.

    `segment` is "spot" (cash/index) or "options" (F&O); any other value is
    treated as "spot", which is the conservative choice because it keeps the
    narrower 15:30 bound rather than silently widening a caller's window.

    Non-trading days return the regular bounds with `expected_candles == 0`,
    mirroring `nse_calendar.expected_candle_count`. Muhurat evening sessions keep
    their curated short count; their unusual bounds are deliberately not modeled
    (the same simplification `nse_calendar.market_status` already makes).
    """
    iso = str(iso_date)
    is_options = segment == OPTIONS
    cas = cas_in_effect(iso)

    if is_options and cas:
        close_min = OPTIONS_CLOSE_MIN_CAS
        full_day_candles = OPTIONS_SESSION_CANDLES_CAS
    else:
        close_min = LEGACY_CLOSE_MIN
        full_day_candles = LEGACY_SESSION_CANDLES

    # The auction lives in the cash/index segment only — derivatives trade
    # continuously straight through it.
    if cas and not is_options:
        cas_start: Optional[int] = CAS_START_MIN
        cas_end: Optional[int] = CAS_END_MIN
    else:
        cas_start = None
        cas_end = None

    if iso in MUHURAT_SESSIONS:
        expected = int(MUHURAT_SESSIONS[iso])
    elif not is_trading_day(iso):
        expected = 0
    else:
        expected = full_day_candles

    return SessionSpec(
        open_min=SESSION_OPEN_MIN,
        close_min=close_min,
        cas_start_min=cas_start,
        cas_end_min=cas_end,
        expected_candles=expected,
    )


def _to_time(minute_of_day: int) -> dtime:
    return dtime(minute_of_day // 60, minute_of_day % 60)


def segment_close_time(iso_date: str, segment: str = SPOT) -> dtime:
    """Exclusive wall-clock session close for a (date, segment).

    Live guards and stop monitors watch **option** positions, so they must pass
    `OPTIONS` — otherwise they stand down at 15:30 while the position they are
    protecting is still tradeable until 15:40.
    """
    return _to_time(session_spec(iso_date, segment).close_min)


def cas_start_time(iso_date: str) -> Optional[dtime]:
    """Wall-clock start of the cash closing auction, or None if it does not apply.

    Past this time the index is frozen, so any signal derived from spot is stale
    by construction and no new entry should be opened on it.
    """
    spec = session_spec(iso_date, SPOT)
    return _to_time(spec.cas_start_min) if spec.cas_start_min is not None else None


def session_rows_mask(df: Any, segment: str = SPOT, *, ts_col: str = "ts") -> pd.Series:
    """Per-row bool: True where a bar belongs to a real trading session.

    Storage is written straight from the vendor feed with no session filter, so
    stale-feed artifacts land in it — bars stamped 23:47, or 00:09, or 15:30 on a
    pre-CAS day. They are invisible on the chart (`warehouse_ohlc` filters) but
    reach anything reading raw rows, which is how a backtest ends up treating
    23:47 as a market minute.

    Two rules, both date-aware:

      * the date must be a trading day, and
      * the minute must fall inside that (date, segment)'s session.

    **Short sessions are exempt from the second rule.** Muhurat bounds are not
    modeled (`nse_calendar.market_status` makes the same simplification) — the
    exchange has scheduled it in the afternoon and in the evening in different
    years — so every bar on such a day is kept and the day-level expected count
    remains the only check. Dropping them on an assumed window would silently
    destroy a whole session.
    """
    if df is None or len(df) == 0 or ts_col not in getattr(df, "columns", []):
        return pd.Series([], dtype=bool)

    ist = pd.to_datetime(df[ts_col], unit="ms", utc=True).dt.tz_convert("Asia/Kolkata")
    minute = ist.dt.hour * 60 + ist.dt.minute
    iso = ist.dt.strftime("%Y-%m-%d")

    # A handful of distinct dates even for a multi-year frame — resolve once each.
    # A non-trading day gets an empty window (open > close), so nothing matches.
    open_min: dict = {}
    close_min: dict = {}
    short_session: dict = {}
    for day in iso.unique():
        trading = is_trading_day(day)
        spec = session_spec(day, segment)
        open_min[day] = spec.open_min if trading else 1
        close_min[day] = spec.close_min if trading else 0
        short_session[day] = trading and day in MUHURAT_SESSIONS

    within = (minute >= iso.map(open_min)) & (minute < iso.map(close_min))
    return (within | iso.map(short_session)).fillna(False).astype(bool)


def expected_candle_count(iso_date: str, segment: str = SPOT) -> int:
    """Segment-aware expected 1-minute candle count for a date.

    The segment-blind `nse_calendar.expected_candle_count` remains the spot
    answer and is unchanged; option-side callers should use this so a full
    post-CAS contract-day reads as 385 rather than a short 375.
    """
    return session_spec(iso_date, segment).expected_candles
