"""Live tick -> 1-minute OHLC roller.

Subscribes to the Upstox WS broadcast and aggregates ticks into per-minute
OHLC bars for the supported index instruments (NIFTY, BANKNIFTY, SENSEX).
Bars are flushed into the same `candles_1m` collection that the historical
ingest writes to, so the deployment evaluator transparently gets today's
intraday bars without changing its query logic.

Why this exists (2026-05-29 finding):
  - The WS stream already persists ticks to the `ticks` collection.
  - The evaluator reads bars from `candles_1m`.
  - Upstox's "historical candles" endpoint returns empty for the same trading
    day, so the bars-for-today gap was never closed.
  - Without this roller, the evaluator can only operate on yesterday's data.

Behavior:
  - One in-memory bucket per (instrument, minute_ts).
  - When a new tick crosses the minute boundary for that instrument, the
    previous minute's bucket is flushed to `candles_1m` (upsert), then
    discarded; the new tick seeds the next bucket.
  - Late-arriving ticks for an already-flushed minute will trigger a
    re-flush; the upsert guarantees idempotency.
  - Volume is set to 0 for index ticks because index feeds have no traded volume.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

import pandas as pd

from app.instruments import INSTRUMENT_KEYS

log = logging.getLogger(__name__)

IST_OFFSET = timedelta(hours=5, minutes=30)

# Reverse map: WS instrument_key -> internal underlying name
_WS_KEY_TO_INSTRUMENT: Dict[str, str] = {v: k for k, v in INSTRUMENT_KEYS.items()}


def minute_floor_ms(ts_ms: int) -> int:
    """Return the millisecond timestamp of the start of the minute containing ts_ms."""
    return int(ts_ms) // 60_000 * 60_000


def _ist_datetime_str(ts_ms: int) -> str:
    dt_utc = datetime.fromtimestamp(int(ts_ms) / 1000, tz=timezone.utc)
    ist = dt_utc + IST_OFFSET
    return ist.strftime("%Y-%m-%d %H:%M:%S")


def _ist_session_date(ts_ms: int) -> str:
    dt_utc = datetime.fromtimestamp(int(ts_ms) / 1000, tz=timezone.utc)
    ist = dt_utc + IST_OFFSET
    return ist.strftime("%Y-%m-%d")


def _ist_time_str(ts_ms: int) -> str:
    dt_utc = datetime.fromtimestamp(int(ts_ms) / 1000, tz=timezone.utc)
    ist = dt_utc + IST_OFFSET
    return ist.strftime("%H:%M")


class LiveCandleRoller:
    """Aggregates streaming ticks into 1m OHLC bars and persists them to candles_1m.

    The class is deliberately small. Caller responsibilities:
      - Provide a stream-manager-compatible subscribe() returning an asyncio.Queue
      - Provide a db_factory returning the Mongo db handle
      - Call start() once at app startup; stop() at shutdown.
    """

    def __init__(
        self,
        *,
        stream_manager: Any,
        db_factory: Any,
        persister: Any,
    ):
        self._stream_manager = stream_manager
        self._db_factory = db_factory
        self._persister = persister  # async fn: (instrument, df, db) -> dict
        self._task: Optional[asyncio.Task] = None
        self._stop_event: Optional[asyncio.Event] = None
        self._queue: Optional[asyncio.Queue] = None
        # Per-instrument current minute bucket
        self._buckets: Dict[str, Dict[str, Any]] = {}
        # Stats for /api/live-candles/status
        self._stats: Dict[str, Any] = {
            "running": False,
            "started_at": None,
            "ticks_seen": 0,
            "ticks_used": 0,
            "ticks_dropped": 0,
            "bars_flushed": 0,
            "last_flush_at": None,
            "last_error": None,
            "active_buckets": 0,
        }

    def status(self) -> Dict[str, Any]:
        return {
            **self._stats,
            "active_buckets": len(self._buckets),
            "current_buckets": [
                {
                    "instrument": inst,
                    "ts": b["ts"],
                    "ist_time": _ist_time_str(b["ts"]),
                    "open": b["open"],
                    "high": b["high"],
                    "low": b["low"],
                    "close": b["close"],
                    "tick_count": b["tick_count"],
                }
                for inst, b in self._buckets.items()
            ],
        }


    async def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._stop_event = asyncio.Event()
        self._stats["running"] = True
        self._stats["started_at"] = datetime.now(timezone.utc).isoformat()
        self._stats["last_error"] = None
        # Subscribe BEFORE the task runs so any tick pushed right after start()
        # is delivered. Subscribing inside the task body would race the producer.
        self._queue: "asyncio.Queue" = self._stream_manager.subscribe(max_queue=512)
        self._task = asyncio.create_task(self._run(), name="live-candle-roller")
        log.info("Live candle roller started")

    async def stop(self) -> None:
        if self._stop_event:
            self._stop_event.set()
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        # Flush any buckets that survived
        try:
            await self._flush_all_buckets("shutdown")
        except Exception as exc:
            log.warning("flush on shutdown failed: %s", exc)
        self._stats["running"] = False
        log.info("Live candle roller stopped")

    async def _run(self) -> None:
        """Main loop: pull ticks from the stream subscriber, route into minute buckets."""
        assert self._stop_event is not None
        queue = self._queue
        try:
            while not self._stop_event.is_set():
                try:
                    tick = await asyncio.wait_for(queue.get(), timeout=5.0)
                except asyncio.TimeoutError:
                    # No tick for 5s; not an error. Use the lull to flush
                    # any bucket whose minute has rolled over (in case ticks paused).
                    await self._maybe_flush_stale_buckets()
                    continue
                except asyncio.CancelledError:
                    raise
                try:
                    await self._handle_tick(tick)
                except Exception as exc:
                    self._stats["last_error"] = str(exc)[:240]
                    log.exception("live candle roller failed on tick: %s", exc)
        except asyncio.CancelledError:
            return
        finally:
            try:
                self._stream_manager.unsubscribe(queue)
            except Exception:
                pass

    async def _handle_tick(self, tick: Dict[str, Any]) -> None:
        self._stats["ticks_seen"] = int(self._stats.get("ticks_seen") or 0) + 1
        instrument_key = str(tick.get("instrument_key") or "")
        instrument = _WS_KEY_TO_INSTRUMENT.get(instrument_key)
        if not instrument:
            self._stats["ticks_dropped"] = int(self._stats.get("ticks_dropped") or 0) + 1
            return  # not one of our tracked indices

        last_price = tick.get("last_price")
        if last_price is None:
            self._stats["ticks_dropped"] = int(self._stats.get("ticks_dropped") or 0) + 1
            return

        try:
            price = float(last_price)
        except (TypeError, ValueError):
            self._stats["ticks_dropped"] = int(self._stats.get("ticks_dropped") or 0) + 1
            return

        ts_raw = tick.get("received_ts") or tick.get("ts")
        if ts_raw is None:
            self._stats["ticks_dropped"] = int(self._stats.get("ticks_dropped") or 0) + 1
            return
        ts_ms = int(ts_raw)
        bucket_ts = minute_floor_ms(ts_ms)

        existing = self._buckets.get(instrument)
        if existing and existing["ts"] != bucket_ts:
            # Minute rolled over - flush the previous bucket, start a new one
            await self._flush_bucket(instrument, existing, reason="rollover")
            existing = None

        if existing is None:
            self._buckets[instrument] = {
                "ts": bucket_ts,
                "open": price,
                "high": price,
                "low": price,
                "close": price,
                "tick_count": 1,
                "first_tick_ts": ts_ms,
                "last_tick_ts": ts_ms,
            }
        else:
            existing["high"] = max(existing["high"], price)
            existing["low"] = min(existing["low"], price)
            existing["close"] = price
            existing["tick_count"] += 1
            existing["last_tick_ts"] = ts_ms
        self._stats["ticks_used"] = int(self._stats.get("ticks_used") or 0) + 1

    async def _maybe_flush_stale_buckets(self) -> None:
        """If a bucket's minute is more than 1 minute old, flush it.

        Without this, if ticks for an instrument pause (e.g., low liquidity index
        or feed gap), the partial bar would never reach the warehouse. Running on
        every 5s timeout keeps stored bars fresh within ~minute+5s.
        """
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        for instrument, bucket in list(self._buckets.items()):
            if now_ms - bucket["ts"] >= 90_000:  # 1 minute + 30s grace
                await self._flush_bucket(instrument, bucket, reason="stale")

    async def _flush_bucket(self, instrument: str, bucket: Dict[str, Any], *, reason: str) -> None:
        """Persist one bucket as a single-row DataFrame upsert."""
        try:
            df = pd.DataFrame([{
                "ts": int(bucket["ts"]),
                "open": float(bucket["open"]),
                "high": float(bucket["high"]),
                "low": float(bucket["low"]),
                "close": float(bucket["close"]),
                "volume": 0,  # index ticks have no traded volume
                "datetime": _ist_datetime_str(bucket["ts"]),
                "ist_time": _ist_time_str(bucket["ts"]),
                "session_date": _ist_session_date(bucket["ts"]),
                "instrument": instrument,
            }])
            db = self._db_factory()
            await self._persister(instrument, df, db)
            self._stats["bars_flushed"] = int(self._stats.get("bars_flushed") or 0) + 1
            self._stats["last_flush_at"] = datetime.now(timezone.utc).isoformat()
            log.debug(
                "flushed live bar %s ts=%s ohlc=%s/%s/%s/%s ticks=%d reason=%s",
                instrument, bucket["ts"], bucket["open"], bucket["high"],
                bucket["low"], bucket["close"], bucket["tick_count"], reason,
            )
            # Drop the bucket only on successful flush; rollover always replaces it.
            if self._buckets.get(instrument) is bucket:
                del self._buckets[instrument]
        except Exception as exc:
            self._stats["last_error"] = f"flush failed: {str(exc)[:200]}"
            log.exception("flush_bucket failed for %s: %s", instrument, exc)

    async def _flush_all_buckets(self, reason: str) -> None:
        for instrument, bucket in list(self._buckets.items()):
            await self._flush_bucket(instrument, bucket, reason=reason)
