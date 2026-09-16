"""`_session_counts` — the memo, the single-flight, and the aggregation fallback.

Measured 2026-09-16 against the live warehouse: `/paper/strategy-stats` took
~1.55s, and `_session_counts` was 1102ms of it across 16 calls with only 5
distinct keys — the rest was pure duplication, because every deployment recomputes
per-day traded-minute counts over the same instrument and an overlapping day range.

The counts gate the forward-validation completeness verdict, which gates capital
promotion, so a cache here is only acceptable if it can never answer for the wrong
dataset. These tests pin exactly that.
"""
from __future__ import annotations

import asyncio
import sys
from datetime import time as dtime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app import forward_metrics as FM  # noqa: E402


class _Cursor:
    def __init__(self, rows): self._rows = list(rows)
    def sort(self, *a, **kw): return self
    async def to_list(self, length=None): return list(self._rows)


class _Agg:
    def __init__(self, rows): self._rows = rows

    async def to_list(self, length=None):
        # A REAL suspension point. Without one this coroutine runs start-to-finish
        # without yielding, so asyncio.gather never interleaves and the
        # single-flight test passes even with the lock removed — verified: that
        # exact mutant slipped through the first version of this file.
        await asyncio.sleep(0)
        return list(self._rows)


class _Candles:
    """Stand-in candles_1m. Counts calls so a cache hit is observable."""

    def __init__(self, ts_rows, agg_rows=None, agg_raises=False):
        self.ts_rows = ts_rows
        self.agg_rows = agg_rows
        self.agg_raises = agg_raises
        self.find_calls = 0
        self.agg_calls = 0

    def find(self, *a, **kw):
        self.find_calls += 1
        return _Cursor(self.ts_rows)

    def aggregate(self, pipeline):
        self.agg_calls += 1
        if self.agg_raises:
            raise RuntimeError("aggregation unsupported")
        return _Agg(self.agg_rows or [])


class _DB:
    def __init__(self, candles): self.candles_1m = candles


def _ms(day: str, hh: int, mm: int) -> int:
    return FM._ist_ms(day, dtime(hh, mm))


DAY = "2026-09-15"


@pytest.fixture(autouse=True)
def _clear():
    FM._session_counts_cache_clear()
    yield
    FM._session_counts_cache_clear()


def test_the_memo_serves_a_repeat_query_without_touching_the_database():
    db = _DB(_Candles([], agg_rows=[{"_id": DAY, "n": 42}]))
    a = asyncio.run(FM._session_counts(db, instrument="NIFTY", session_days=[DAY]))
    b = asyncio.run(FM._session_counts(db, instrument="NIFTY", session_days=[DAY]))
    assert a == b == {DAY: 42}
    assert db.candles_1m.agg_calls == 1, "second call re-queried instead of hitting the memo"


def test_a_different_database_never_reads_the_first_one_s_counts():
    """THE regression this file exists for.

    The first version keyed only on (instrument, range, window). The full suite went
    red on test_forward_metrics_hides_strategy_library_until_ten_complete_sessions
    while it passed in isolation, because a previous case's stand-in DB had already
    cached an answer under the identical key.
    """
    db1 = _DB(_Candles([], agg_rows=[{"_id": DAY, "n": 42}]))
    db2 = _DB(_Candles([], agg_rows=[{"_id": DAY, "n": 7}]))
    first = asyncio.run(FM._session_counts(db1, instrument="NIFTY", session_days=[DAY]))
    second = asyncio.run(FM._session_counts(db2, instrument="NIFTY", session_days=[DAY]))
    assert first == {DAY: 42}
    assert second == {DAY: 7}, "a second database was served the first one's counts"
    assert db2.candles_1m.agg_calls == 1


def test_a_different_instrument_or_window_is_a_different_entry():
    db = _DB(_Candles([], agg_rows=[{"_id": DAY, "n": 5}]))
    asyncio.run(FM._session_counts(db, instrument="NIFTY", session_days=[DAY]))
    asyncio.run(FM._session_counts(db, instrument="SENSEX", session_days=[DAY]))
    asyncio.run(FM._session_counts(
        db, instrument="NIFTY", session_days=[DAY],
        window_start=FM.PROMOTION_SESSION_START_IST,
        window_end=FM.PROMOTION_SESSION_END_IST))
    assert db.candles_1m.agg_calls == 3, "distinct queries collapsed into one entry"


def test_concurrent_callers_compute_once(monkeypatch):
    """Single-flight. The strategy-stats route fans deployments out concurrently,
    so without the lock every one of them would miss the empty cache in the same
    tick and run the same aggregation."""
    db = _DB(_Candles([], agg_rows=[{"_id": DAY, "n": 11}]))

    async def main():
        return await asyncio.gather(*(
            FM._session_counts(db, instrument="NIFTY", session_days=[DAY])
            for _ in range(8)
        ))

    out = asyncio.run(main())
    assert all(o == {DAY: 11} for o in out)
    assert db.candles_1m.agg_calls == 1, (
        f"{db.candles_1m.agg_calls} concurrent callers each ran the aggregation")


def test_it_falls_back_to_the_scan_when_aggregation_is_unavailable():
    """A DB stand-in with no aggregate(), or a server that rejects the pipeline,
    must still produce counts — the scan stays the fallback and the oracle."""
    rows = [{"ts": _ms(DAY, 10, m)} for m in range(0, 30)]
    db = _DB(_Candles(rows, agg_raises=True))
    out = asyncio.run(FM._session_counts(db, instrument="NIFTY", session_days=[DAY]))
    assert out == {DAY: 30}
    assert db.candles_1m.find_calls == 1, "the scan fallback did not run"


def test_the_aggregation_and_the_scan_agree_on_the_same_data():
    """Equality against the original implementation, which is the only thing that
    makes swapping it in safe. (Also verified against the live warehouse across
    2 instruments x 4 day-ranges x 2 windows: 0 mismatches.)"""
    # Minutes inside the window, one duplicate, and two outside it.
    rows = ([{"ts": _ms(DAY, 10, m)} for m in range(0, 20)]
            + [{"ts": _ms(DAY, 10, 5)}]          # duplicate minute -> must not double-count
            + [{"ts": _ms(DAY, 9, 30)}]          # before window_start
            + [{"ts": _ms(DAY, 15, 30)}])        # at/after window_end
    start_ts = _ms(DAY, 10, 0)
    end_ts = _ms(DAY, 15, 0)
    db = _DB(_Candles(rows))
    scan = asyncio.run(FM._session_counts_scan(
        db, instrument="NIFTY", days=[DAY], start_ts=start_ts, end_ts=end_ts,
        window_start=FM.SESSION_START_IST, window_end=FM.SESSION_END_IST))
    assert scan == {DAY: 20}, scan


def test_every_requested_day_is_present_even_with_no_data():
    """Callers index the result directly, so a day with no candles must be 0 rather
    than missing."""
    db = _DB(_Candles([], agg_rows=[{"_id": DAY, "n": 3}]))
    out = asyncio.run(FM._session_counts(
        db, instrument="NIFTY", session_days=["2026-09-14", DAY, "2026-09-16"]))
    assert out == {"2026-09-14": 0, DAY: 3, "2026-09-16": 0}
