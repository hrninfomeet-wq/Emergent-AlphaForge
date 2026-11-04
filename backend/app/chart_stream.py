"""Read-only chart candle updates over the existing Upstox connection.

Tick bars are provisional. REST remains the authority for missed trades and
volume. Every queued tick updates extrema before SSE payloads are coalesced.
"""
from __future__ import annotations

import asyncio
import json
import math
import time
from app.tick_stream import sse_frame

_CHANNEL_RETRY_INITIAL_S = 1.0
_CHANNEL_RETRY_MAX_S = 30.0


def parse_chart_channels(raw):
    """Validate the entire subscription before any feed leases are acquired."""
    from app.chart_data import TIMEFRAME_SECONDS
    try:
        channels = json.loads(raw)
    except (ValueError, TypeError) as exc:
        raise ValueError("channels must be a JSON array") from exc
    if not isinstance(channels, list) or not channels:
        raise ValueError("channels must be a nonempty JSON array")
    distinct = {}
    for channel in channels:
        if not isinstance(channel, dict) or set(channel) != {"instrument_key", "timeframe"}:
            raise ValueError("Each channel requires instrument_key and timeframe")
        key, timeframe = channel["instrument_key"], channel["timeframe"]
        if not isinstance(key, str) or not key.strip() or len(key) > 200:
            raise ValueError("instrument_key must contain 1 to 200 characters")
        if not isinstance(timeframe, str) or timeframe not in TIMEFRAME_SECONDS:
            raise ValueError("Unsupported chart timeframe")
        distinct[(key, timeframe)] = {"instrument_key": key, "timeframe": timeframe}
        if len(distinct) > 6:
            raise ValueError("At most six distinct chart channels are supported")
    return list(distinct.values())


async def chart_multi_event_stream(*, manager, channels, history_loader, is_disconnected,
                                   history_peek=None, max_queue=64, initial_errors=()):
    """Merge channel generators without dropping candle extrema or resync frames.

    Backpressure is confined to these chart consumers. The manager's independent
    bounded tick queues never block the market feed; their existing overflow
    handling emits a resync and reseeds historical extrema before more candles.
    Only this parent reads the ASGI disconnect receiver.
    """
    queue = asyncio.Queue(maxsize=max(1, max_queue))
    stopped = asyncio.Event()
    remaining = len(channels)

    async def disconnected():
        return stopped.is_set()

    async def pump(instrument, timeframe):
        nonlocal remaining
        retry_delay = _CHANNEL_RETRY_INITIAL_S
        restarting = False
        try:
            while not stopped.is_set():
                source = chart_event_stream(manager=manager, instrument=instrument, timeframe=timeframe,
                                            history_loader=history_loader, is_disconnected=disconnected,
                                            history_peek=history_peek)
                failed = False
                try:
                    async for event in source:
                        if event.startswith("event: stream_error\n"):
                            failed = True
                        else:
                            retry_delay = _CHANNEL_RETRY_INITIAL_S
                            if restarting:
                                await queue.put(sse_frame("resync", {
                                    "instrument_key": instrument["instrument_key"], "timeframe": timeframe,
                                    "reason": "channel_recovered"}))
                                restarting = False
                        await queue.put(event)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    failed = True
                    await queue.put(sse_frame("stream_error", {
                        "instrument_key": instrument["instrument_key"], "timeframe": timeframe,
                        "status": "disconnected", "connected": False,
                        "warnings": ["Live chart updates are unavailable. Historical candles remain available."],
                        "emitted_at_ms": int(time.time() * 1000)}))
                finally:
                    # Finish releasing the old lease before creating a replacement.
                    await source.aclose()
                if not failed:
                    break
                # A healthy sibling keeps the HTTP connection open, so retry the
                # failed channel here rather than relying on EventSource reconnect.
                await asyncio.sleep(retry_delay)
                retry_delay = min(_CHANNEL_RETRY_MAX_S, retry_delay * 2)
                restarting = True
        finally:
            remaining -= 1

    for payload in initial_errors:
        yield sse_frame("stream_error", {**payload, "status": "disconnected", "connected": False,
                                         "emitted_at_ms": int(time.time() * 1000)})
    tasks = [asyncio.create_task(pump(instrument, timeframe)) for instrument, timeframe in channels]
    try:
        while remaining or not queue.empty():
            if await is_disconnected():
                break
            try:
                event = await asyncio.wait_for(queue.get(), timeout=.25)
            except asyncio.TimeoutError:
                continue
            yield event
    finally:
        stopped.set()
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


def finite(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        value = float(value)
        return value if math.isfinite(value) else None
    except (TypeError, ValueError):
        return None


def tick_age_ms(tick, now_ms):
    """Receiving an old last-traded price now does not make that price fresh."""
    stamps = [finite(tick.get(field)) for field in ("ingest_ts", "ts")]
    if any(stamp is None or stamp <= 0 or stamp > now_ms + 5000 for stamp in stamps):
        return None
    return int(max(0, *(now_ms - stamp for stamp in stamps)))


class ChartCandleAccumulator:
    def __init__(self, instrument_key, timeframe, bars=(), *, bucket=None, as_of_ms=0):
        if bucket is None:
            from app.chart_data import chart_bucket_time
            bucket = chart_bucket_time
        self.key = instrument_key
        self.timeframe = timeframe
        self.bucket = bucket
        self.bars = {int(bar["time"]): dict(bar) for bar in bars[-2:]}
        self.history_as_of_ms = finite(as_of_ms) or 0
        self.last_trade_ms = self.history_as_of_ms

    def update(self, tick, now_ms):
        if tick.get("instrument_key") != self.key:
            return None
        price, stamp = finite(tick.get("last_price")), finite(tick.get("ts"))
        age = tick_age_ms(tick, now_ms)
        if price is None or price <= 0 or stamp is None or age is None or age > 10_000:
            return None
        if stamp <= self.history_as_of_ms or stamp < self.last_trade_ms:
            return None  # A delayed trade cannot move the current close backwards.
        at = self.bucket(int(stamp // 1000), self.timeframe)
        if self.bars and at < max(self.bars):
            return None
        self.last_trade_ms = stamp
        bar = self.bars.get(at)
        if bar is None:
            bar = {"time": at, "open": price, "high": price, "low": price, "close": price}
            self.bars[at] = bar
        bar.update(high=max(bar["high"], price), low=min(bar["low"], price), close=price,
                   volume=None, provisional=True)
        for old in sorted(self.bars)[:-3]:
            self.bars.pop(old, None)
        return dict(bar)


def stream_state(manager, key, timeframe, now_ms):
    status = manager.status()
    tick = manager.latest_tick_map().get(key) or {}
    age = tick_age_ms(tick, now_ms)
    connected = bool(status.get("connected"))
    state = "disconnected" if not connected else "waiting" if not tick else "live" if age is not None and age <= 10_000 else "stale"
    safe_tick = {field: tick.get(field) for field in ("last_price", "close_price", "ts", "ingest_ts", "received_ts")}
    return {"instrument_key": key, "timeframe": timeframe, "tick": safe_tick,
            "connected": connected, "last_tick_age_ms": age, "source": "upstox_ws_v3",
            "status": state, "emitted_at_ms": now_ms,
            "warnings": [] if connected else ["Live feed is disconnected; historical candles remain available."]}


async def chart_event_stream(*, manager, instrument, timeframe, history_loader,
                             is_disconnected, now_ms=lambda: int(time.time() * 1000),
                             heartbeat_s=5.0, coalesce_s=0.1, history_peek=None):
    key = instrument["instrument_key"]
    queue = manager.subscribe(max_queue=1024, instrument_keys=[key])
    lease = None
    try:
        lease = await manager.acquire_chart_instrument(key)
        history = await history_loader(key, timeframe=timeframe, limit=600)
        candle = ChartCandleAccumulator(key, timeframe, history.get("bars") or [], as_of_ms=history.get("as_of_ms"))
        initial = stream_state(manager, key, timeframe, now_ms())
        initial["warnings"] += history.get("warnings") or []
        initial["history_as_of_ms"] = candle.history_as_of_ms
        yield sse_frame("snapshot", initial)
        previous_connection = manager.status().get("connected")
        previous_session = (manager.status().get("session_id"), manager.status().get("reconnect_count"))
        dropped = 0
        while not await is_disconnected():
            batch = []
            try:
                batch.append(await asyncio.wait_for(queue.get(), timeout=heartbeat_s))
                # The queue continues collecting all price extrema during this
                # short paint window; no last-tick-only drain is allowed.
                await asyncio.sleep(coalesce_s)
                while not queue.empty():
                    batch.append(queue.get_nowait())
            except asyncio.TimeoutError:
                pass
            current = manager.status()
            session = (current.get("session_id"), current.get("reconnect_count"))
            reconnected = bool(current.get("connected")) and (not previous_connection or session != previous_session)
            previous_connection = bool(current.get("connected"))
            previous_session = session
            lost = getattr(queue, "dropped_count", 0)
            if lost != dropped or reconnected:
                dropped = lost
                yield sse_frame("resync", {"instrument_key": key, "timeframe": timeframe,
                                          "reason": "feed_reconnected" if reconnected else "tick_queue_overflow"})
                # Re-seed server extrema too: browser-only reconciliation could
                # otherwise be overwritten by an incomplete server candle.
                history = await history_loader(key, timeframe=timeframe, limit=600)
                candle = ChartCandleAccumulator(key, timeframe, history.get("bars") or [], as_of_ms=history.get("as_of_ms"))
            # The browser periodically refreshes the same shared history cache.
            # Adopt that authoritative correction before publishing more ticks;
            # otherwise a partial server candle could erase corrected extrema.
            cached = history_peek(key, timeframe=timeframe, limit=600,
                                  after_as_of_ms=candle.history_as_of_ms) if history_peek else None
            if cached and (finite(cached.get("as_of_ms")) or 0) > candle.history_as_of_ms:
                candle = ChartCandleAccumulator(key, timeframe, cached.get("bars") or [], as_of_ms=cached.get("as_of_ms"))
            changed = {}
            for tick in batch:
                bar = candle.update(tick, now_ms())
                if bar is not None:
                    changed[bar["time"]] = bar
            payload = stream_state(manager, key, timeframe, now_ms())
            payload["history_as_of_ms"] = candle.history_as_of_ms
            if changed:
                payload["bars"] = [changed[at] for at in sorted(changed)]
                payload["bar"] = payload["bars"][-1]
                payload["warnings"].append("Forming candle is provisional; REST reconciles OHLC and volume.")
            yield sse_frame("snapshot" if changed else "heartbeat", payload)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        # Never expose upstream URLs, authorization bodies or credential errors.
        message = str(exc) if isinstance(exc, ValueError) else "Live chart updates are unavailable. Historical candles remain available."
        yield sse_frame("stream_error", {"instrument_key": key, "timeframe": timeframe,
                                        "status": "disconnected", "connected": False,
                                        "warnings": [message], "emitted_at_ms": now_ms()})
    finally:
        manager.unsubscribe(queue)
        if lease is not None:
            await asyncio.shield(manager.release_chart_instrument(lease))
