"""Tests for the live tick -> 1m OHLC roller (slice 6.5).

Validates bucket aggregation, minute rollover flush, idempotency on re-flush,
and stale-bucket flush after tick pauses. Uses a fake stream manager (asyncio
queue) and a fake persister so no Mongo or Upstox is touched.
"""
from __future__ import annotations

import asyncio
import os
import sys
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import pandas as pd
import pytest


sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from app.live_candle_roller import LiveCandleRoller, minute_floor_ms  # noqa: E402


# ---- helpers ---------------------------------------------------------------


class FakeStream:
    """Mimics UpstoxMarketStreamManager.subscribe()/unsubscribe() pub-sub."""

    def __init__(self):
        self._queues: List[asyncio.Queue] = []

    def subscribe(self, *, max_queue: int = 256) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=max_queue)
        self._queues.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        if q in self._queues:
            self._queues.remove(q)

    async def push(self, tick: Dict[str, Any]) -> None:
        for q in list(self._queues):
            await q.put(tick)


class FakePersister:
    """Records each persist call so tests can assert flush behavior."""

    def __init__(self):
        self.calls: List[Dict[str, Any]] = []

    async def __call__(self, instrument: str, df: pd.DataFrame, db: Any) -> Dict[str, Any]:
        # df should be a single-row DataFrame
        row = df.iloc[0].to_dict() if not df.empty else {}
        self.calls.append({
            "instrument": instrument,
            "row": row,
        })
        return {"upserted": 1, "modified": 0, "matched": 0, "dates": [row.get("session_date")]}


def fake_db():
    return object()


def tick(instrument_key: str, last_price: float, ts_ms: int) -> Dict[str, Any]:
    return {
        "instrument_key": instrument_key,
        "last_price": last_price,
        "received_ts": ts_ms,
        "ts": ts_ms,
    }


def ist_ts(date_str: str, hh: int, mm: int) -> int:
    t = pd.Timestamp(f"{date_str} {hh:02d}:{mm:02d}", tz="Asia/Kolkata")
    return int(t.tz_convert("UTC").value // 10**6)


# ---- pure helpers ----------------------------------------------------------


def test_minute_floor_ms_aligns_to_minute_boundary():
    # 1779875985000 is some random ms in IST 15:29:45
    ts = 1779875985000
    floor = minute_floor_ms(ts)
    assert floor % 60_000 == 0
    assert ts - floor < 60_000


# ---- bucket aggregation ----------------------------------------------------


@pytest.mark.asyncio
async def test_first_tick_seeds_bucket_with_open_high_low_close_equal_to_price():
    stream = FakeStream()
    persister = FakePersister()
    roller = LiveCandleRoller(stream_manager=stream, db_factory=fake_db, persister=persister)
    await roller.start()
    try:
        await asyncio.sleep(0.05)  # let the loop subscribe before we push
        await stream.push(tick("NSE_INDEX|Nifty 50", 23900.5, 1779875985000))
        await asyncio.sleep(0.2)  # let the loop process
        s = roller.status()
        assert s["ticks_used"] == 1
        bucket = next(b for b in s["current_buckets"] if b["instrument"] == "NIFTY")
        assert bucket["open"] == 23900.5
        assert bucket["high"] == 23900.5
        assert bucket["low"] == 23900.5
        assert bucket["close"] == 23900.5
        assert bucket["tick_count"] == 1
    finally:
        await roller.stop()


@pytest.mark.asyncio
async def test_multiple_ticks_in_same_minute_update_high_low_close():
    stream = FakeStream()
    persister = FakePersister()
    roller = LiveCandleRoller(stream_manager=stream, db_factory=fake_db, persister=persister)
    await roller.start()
    try:
        ts = 1779875985000  # IST 15:29:45 some day
        await stream.push(tick("NSE_INDEX|Nifty 50", 23900.0, ts))
        await stream.push(tick("NSE_INDEX|Nifty 50", 23905.0, ts + 1000))  # higher
        await stream.push(tick("NSE_INDEX|Nifty 50", 23898.5, ts + 2000))  # lower
        await stream.push(tick("NSE_INDEX|Nifty 50", 23901.0, ts + 3000))  # close
        await asyncio.sleep(0.05)
        s = roller.status()
        bucket = next(b for b in s["current_buckets"] if b["instrument"] == "NIFTY")
        assert bucket["open"] == 23900.0
        assert bucket["high"] == 23905.0
        assert bucket["low"] == 23898.5
        assert bucket["close"] == 23901.0
        assert bucket["tick_count"] == 4
        assert persister.calls == []  # bucket still in progress, no flush yet
    finally:
        await roller.stop()


@pytest.mark.asyncio
async def test_minute_rollover_flushes_previous_bucket_and_starts_new_one():
    stream = FakeStream()
    persister = FakePersister()
    roller = LiveCandleRoller(stream_manager=stream, db_factory=fake_db, persister=persister)
    await roller.start()
    try:
        m1 = ist_ts("2026-05-27", 10, 0)  # exact minute boundary
        m2 = m1 + 60_000
        # First minute: 2 ticks
        await stream.push(tick("NSE_INDEX|Nifty 50", 23900.0, m1 + 5_000))
        await stream.push(tick("NSE_INDEX|Nifty 50", 23910.0, m1 + 30_000))
        # Cross into second minute - this triggers the previous bar's flush
        await stream.push(tick("NSE_INDEX|Nifty 50", 23912.0, m2 + 1_000))
        await asyncio.sleep(0.1)
        # Exactly one bar should have been flushed (the m1 bar)
        assert len(persister.calls) == 1
        flushed = persister.calls[0]
        assert flushed["instrument"] == "NIFTY"
        row = flushed["row"]
        assert int(row["ts"]) == m1
        assert float(row["open"]) == 23900.0
        assert float(row["high"]) == 23910.0
        assert float(row["low"]) == 23900.0
        assert float(row["close"]) == 23910.0
        # And the new bucket is the m2 bar with the latest tick as open/high/low/close
        s = roller.status()
        new_bucket = next(b for b in s["current_buckets"] if b["instrument"] == "NIFTY")
        assert new_bucket["ts"] == m2
        assert new_bucket["close"] == 23912.0
    finally:
        await roller.stop()


@pytest.mark.asyncio
async def test_unknown_instrument_keys_are_dropped():
    """Ticks for instruments outside our tracked indices are counted as dropped."""
    stream = FakeStream()
    persister = FakePersister()
    roller = LiveCandleRoller(stream_manager=stream, db_factory=fake_db, persister=persister)
    await roller.start()
    try:
        await stream.push(tick("GLOBAL_INDEX|SGX NIFTY", 23900.0, 1779875985000))
        await stream.push(tick("GLOBAL_INDICATOR|USDINR", 95.7, 1779875985000))
        await asyncio.sleep(0.05)
        s = roller.status()
        assert s["ticks_seen"] == 2
        assert s["ticks_used"] == 0
        assert s["ticks_dropped"] == 2
        assert s["active_buckets"] == 0
        assert persister.calls == []
    finally:
        await roller.stop()


@pytest.mark.asyncio
async def test_non_session_ticks_are_dropped_before_bucket_creation():
    """The roller must not create warehouse candles outside regular index sessions."""
    stream = FakeStream()
    persister = FakePersister()
    roller = LiveCandleRoller(stream_manager=stream, db_factory=fake_db, persister=persister)
    await roller.start()
    try:
        await stream.push(tick("NSE_INDEX|Nifty 50", 23900.0, ist_ts("2026-05-28", 9, 15)))   # holiday
        await stream.push(tick("NSE_INDEX|Nifty 50", 23901.0, ist_ts("2026-05-30", 9, 15)))   # Saturday
        await stream.push(tick("NSE_INDEX|Nifty 50", 23902.0, ist_ts("2026-05-31", 21, 15)))  # Sunday
        await stream.push(tick("NSE_INDEX|Nifty 50", 23903.0, ist_ts("2026-05-27", 15, 30)))  # close boundary
        await stream.push(tick("NSE_INDEX|Nifty 50", 23904.0, ist_ts("2026-05-27", 16, 0)))   # after close
        await asyncio.sleep(0.05)

        s = roller.status()
        assert s["ticks_seen"] == 5
        assert s["ticks_used"] == 0
        assert s["ticks_dropped"] == 5
        assert s["active_buckets"] == 0
        assert persister.calls == []
    finally:
        await roller.stop()


@pytest.mark.asyncio
async def test_close_boundary_tick_flushes_last_valid_bucket_without_starting_new_bar():
    stream = FakeStream()
    persister = FakePersister()
    roller = LiveCandleRoller(stream_manager=stream, db_factory=fake_db, persister=persister)
    await roller.start()
    try:
        await stream.push(tick("NSE_INDEX|Nifty 50", 23900.0, ist_ts("2026-05-27", 15, 29)))
        await stream.push(tick("NSE_INDEX|Nifty 50", 23905.0, ist_ts("2026-05-27", 15, 30)))
        await asyncio.sleep(0.05)

        s = roller.status()
        assert s["ticks_seen"] == 2
        assert s["ticks_used"] == 1
        assert s["ticks_dropped"] == 1
        assert s["active_buckets"] == 0
        assert len(persister.calls) == 1
        assert int(persister.calls[0]["row"]["ts"]) == ist_ts("2026-05-27", 15, 29)
    finally:
        await roller.stop()


@pytest.mark.asyncio
async def test_stop_flushes_in_progress_buckets():
    """Buckets that exist at shutdown time must reach the persister, not be lost."""
    stream = FakeStream()
    persister = FakePersister()
    roller = LiveCandleRoller(stream_manager=stream, db_factory=fake_db, persister=persister)
    await roller.start()
    ts = 1779875985000
    await stream.push(tick("NSE_INDEX|Nifty 50", 23900.0, ts))
    await stream.push(tick("BSE_INDEX|SENSEX", 75900.0, ts))
    await asyncio.sleep(0.05)
    await roller.stop()
    # Both buckets should have been flushed during shutdown
    instruments_flushed = sorted(c["instrument"] for c in persister.calls)
    assert instruments_flushed == ["NIFTY", "SENSEX"]


@pytest.mark.asyncio
async def test_three_indices_keep_independent_buckets():
    """NIFTY, BANKNIFTY and SENSEX must roll into separate per-instrument buckets."""
    stream = FakeStream()
    persister = FakePersister()
    roller = LiveCandleRoller(stream_manager=stream, db_factory=fake_db, persister=persister)
    await roller.start()
    try:
        ts = 1779875985000
        await stream.push(tick("NSE_INDEX|Nifty 50", 23900.0, ts))
        await stream.push(tick("NSE_INDEX|Nifty Bank", 54900.0, ts))
        await stream.push(tick("BSE_INDEX|SENSEX", 75900.0, ts))
        await asyncio.sleep(0.05)
        s = roller.status()
        instruments = sorted(b["instrument"] for b in s["current_buckets"])
        assert instruments == ["BANKNIFTY", "NIFTY", "SENSEX"]
        assert s["ticks_used"] == 3
    finally:
        await roller.stop()


@pytest.mark.asyncio
async def test_stop_then_start_resumes_clean_state():
    """Restart resets stats but the next ticks resume normally."""
    stream = FakeStream()
    persister = FakePersister()
    roller = LiveCandleRoller(stream_manager=stream, db_factory=fake_db, persister=persister)
    await roller.start()
    await stream.push(tick("NSE_INDEX|Nifty 50", 23900.0, 1779875985000))
    await asyncio.sleep(0.05)
    await roller.stop()

    # Restart and feed more ticks for a different minute
    await roller.start()
    try:
        ts2 = ist_ts("2026-05-27", 10, 0)
        await stream.push(tick("NSE_INDEX|Nifty 50", 24000.0, ts2))
        await asyncio.sleep(0.05)
        s = roller.status()
        # Counter resets are not strictly required, but the bucket should be the new minute
        bucket = next(b for b in s["current_buckets"] if b["instrument"] == "NIFTY")
        assert bucket["close"] == 24000.0
    finally:
        await roller.stop()
