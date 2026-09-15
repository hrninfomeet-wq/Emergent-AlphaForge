"""Shared Server-Sent-Events coalescer for tick-driven payloads.

Extracted from the market-header SSE (``/market/header/stream``), which proved
the transport works end-to-end in this stack, so the live-marks and paper-marks
streams do not each re-implement the tricky parts:

* **Paint on connect.** A snapshot is emitted before any tick arrives, so a page
  never shows a blank card while waiting for the market to move.
* **Coalesce.** Ticks arrive at a measured p50 40/s across ~53 instruments. Each
  wake drains the whole queue and builds ONE payload, rate-limited to
  ``min_interval_s`` — so a 40/s feed drives ~10 renders/s, not 40.
* **Drop, never block.** The manager's fan-out queue is bounded and drops oldest
  on overflow; a slow client can never stall the WS receive loop.
* **Heartbeat.** An idle market (or a proxy) would otherwise close the
  connection. A heartbeat costs no payload build — no broker or DB work while
  nothing is moving.
* **Always unsubscribe.** A leaked queue stays in the manager set forever and
  the producer keeps filling it.

Why SSE and not a WebSocket: this is strictly server -> client, EventSource
reconnects on its own, it rides the existing HTTP/CORS setup with no new
handshake path, and the app already has a working precedent. A WebSocket would
buy bidirectionality we do not need and a second auth/reconnect surface to get
wrong.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, AsyncIterator, Awaitable, Callable, Optional

log = logging.getLogger(__name__)

#: At most ~20 payloads/s per client. The window is the DOMINANT term in
#: tick-to-pixel (measured: 188ms of a 195ms p50 was this wait), so halving it
#: halves perceived latency. Measured React commit cost is ~7ms per update, so
#: 20/s is ~14% of one core's frame budget — affordable. Going further has
#: diminishing returns: the eye cannot resolve 20/s, and the remaining latency is
#: then transport, not policy.
DEFAULT_MIN_INTERVAL_S = 0.05
#: Idle keep-alive. Must stay under common proxy idle timeouts (~60s).
DEFAULT_HEARTBEAT_S = 15.0
#: Ticks drained per wake before we build a payload anyway.
_MAX_DRAIN = 256


def _ingest_ts(tick: Any) -> Optional[int]:
    """Local receive clock of a tick, if it carries one."""
    try:
        raw = (tick or {}).get("ingest_ts")
        return int(raw) if raw is not None else None
    except (AttributeError, TypeError, ValueError):
        return None


def sse_frame(event: str, payload: Any) -> str:
    return f"event: {event}\ndata: {json.dumps(payload, default=str)}\n\n"


async def tick_event_stream(
    *,
    subscribe: Callable[..., "asyncio.Queue"],
    unsubscribe: Callable[["asyncio.Queue"], None],
    build_payload: Callable[[], Awaitable[Any]],
    is_disconnected: Optional[Callable[[], Awaitable[bool]]] = None,
    min_interval_s: float = DEFAULT_MIN_INTERVAL_S,
    heartbeat_s: float = DEFAULT_HEARTBEAT_S,
    event: str = "snapshot",
    max_queue: int = 256,
) -> AsyncIterator[str]:
    """Yield SSE frames: one on connect, then one per coalesced tick batch."""
    queue = subscribe(max_queue=max_queue)
    loop = asyncio.get_event_loop()
    try:
        try:
            yield sse_frame(event, await build_payload())
        except Exception as exc:  # noqa: BLE001
            log.warning("tick stream: initial payload failed: %s", exc)
            yield sse_frame("stream_error", {"error": str(exc)[:240]})
        last_emit = loop.time()

        while True:
            if is_disconnected is not None and await is_disconnected():
                break

            try:
                tick = await asyncio.wait_for(queue.get(), timeout=heartbeat_s)
            except asyncio.TimeoutError:
                # A REAL event, not an SSE comment. A `: comment` keeps the TCP
                # connection warm but EventSource discards it without firing any
                # listener, so the client cannot tell a live-but-quiet stream from
                # a silently dead one and has no basis for a watchdog. Costs no
                # payload build.
                yield sse_frame("heartbeat", {"ts_ms": int(time.time() * 1000)})
                continue

            # Drain everything queued in the same instant — one payload per batch.
            # The newest ingest_ts in the batch is the clock this emit is racing:
            # stamping it on the payload is what makes tick-to-pixel measurable
            # end to end (client paint time minus this), in production, with real
            # ticks — not just inferable from poll intervals.
            # The tick that WOKE us is the oldest in the batch, so it is the one
            # that waits longest — attribute the batch to it. Using the newest
            # would quietly understate latency by the whole coalescing window.
            wake_ingest_ts = _ingest_ts(tick)
            newest_ingest_ts = wake_ingest_ts
            drained = 0
            while drained < _MAX_DRAIN:
                try:
                    other = queue.get_nowait()
                    drained += 1
                    got = _ingest_ts(other)
                    if got is not None and (newest_ingest_ts is None or got > newest_ingest_ts):
                        newest_ingest_ts = got
                except asyncio.QueueEmpty:
                    break

            wait = (last_emit + min_interval_s) - loop.time()
            if wait > 0:
                await asyncio.sleep(wait)

            try:
                payload = await build_payload()
            except Exception as exc:  # noqa: BLE001 — a blip must not kill the stream
                log.warning("tick stream: payload build failed: %s", exc)
                yield sse_frame("stream_error", {"error": str(exc)[:240]})
                last_emit = loop.time()
                continue

            if isinstance(payload, dict) and wake_ingest_ts is not None:
                payload["wake_ingest_ts_ms"] = wake_ingest_ts
                payload["newest_ingest_ts_ms"] = newest_ingest_ts
                payload["wake_tick_count"] = drained + 1
            yield sse_frame(event, payload)
            last_emit = loop.time()
    finally:
        unsubscribe(queue)


SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "X-Accel-Buffering": "no",  # disable nginx buffering for instant push
    "Connection": "keep-alive",
}
