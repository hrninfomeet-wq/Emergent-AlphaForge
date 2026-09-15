"""Fast tick-level exit monitor for OPEN paper trades.

Mirrors LiveCandleRoller's lifecycle (start/stop/status). Every ~1.5s during NSE
market hours it calls the existing, proven mark_open_deployment_trades — which is
idempotent, status-conditional, and staleness-guarded — so stop/target/spot-mirror/
time-stop exits fire at near-tick latency against the LIVE premium instead of once
per minute. It owns NO subscription state (the option-stream auto-follow guarantees
every held contract stays markable) and never places broker orders.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, time as dtime, timedelta, timezone
from typing import Any, Awaitable, Callable, Dict, List, Optional

from app.session_spec import OPTIONS, segment_close_time

log = logging.getLogger(__name__)

POLL_SECONDS = 1.5
#: Minimum gap between tick-driven cycles. Ticks arrive at a measured p50 40/s,
#: p95 50/s; a cycle costs a measured p50 3ms (idle) to 13ms (20 open trades),
#: p95 25ms. Waking on every tick would put the monitor at ~50% event-loop duty
#: for no detection benefit a human or a stop can use. At 200ms the worst case is
#: 5 cycles/s (~6% duty at 20 open trades) while mean detection latency falls from
#: 750ms to 100ms.
TICK_FLOOR_SECONDS = 0.2
_IST = timedelta(hours=5, minutes=30)


def _in_market_hours(now_utc: Optional[datetime] = None) -> bool:
    """True while the positions this monitor protects are still tradeable.

    Bounded by the OPTIONS close: from 2026-08-03 the derivatives segment trades
    until 15:40, so a 15:30 bound would stand the monitor down while an open
    position could still move for another ten minutes.
    """
    ist = (now_utc or datetime.now(timezone.utc)) + _IST
    if ist.weekday() >= 5:
        return False
    return dtime(9, 15) <= ist.time() < segment_close_time(ist.strftime("%Y-%m-%d"), OPTIONS)


class LiveExitMonitor:
    def __init__(
        self,
        *,
        db_factory: Callable[[], Any],
        tick_lookup_factory: Callable[[], Callable[[str], Optional[Dict[str, Any]]]],
        mark_fn: Callable[..., Awaitable[List[Dict[str, Any]]]],
        overall_fn: Optional[Callable[..., Awaitable[Dict[str, Any]]]] = None,
        poll_seconds: float = POLL_SECONDS,
        tick_floor_seconds: float = TICK_FLOOR_SECONDS,
        subscribe: Optional[Callable[..., "asyncio.Queue"]] = None,
        unsubscribe: Optional[Callable[["asyncio.Queue"], None]] = None,
        in_market_hours: Callable[[], bool] = _in_market_hours,
    ):
        self._db_factory = db_factory
        self._tick_lookup_factory = tick_lookup_factory
        self._mark_fn = mark_fn
        # Optional basket-level pass run after per-leg marking (paper overall
        # controls: SL / target / trailing on the whole open basket).
        self._overall_fn = overall_fn
        self._poll_seconds = float(poll_seconds)
        self._tick_floor_seconds = float(tick_floor_seconds)
        # Tick fan-out. When absent the loop is exactly the old fixed-interval
        # sleep — every caller that does not wire a stream is unaffected.
        self._subscribe = subscribe
        self._unsubscribe = unsubscribe
        self._in_market_hours = in_market_hours
        # Contracts an OPEN trade holds. Only a tick on one of these is worth a
        # cycle; the other ~50 subscribed instruments must not cost anything.
        self._watch_keys: set[str] = set()
        self._queue: Optional["asyncio.Queue"] = None
        self._task: Optional[asyncio.Task] = None
        self._stats: Dict[str, Any] = {
            "running": False, "started_at": None, "cycles": 0,
            "open_trades_checked": 0, "auto_closes": 0,
            "overall_exits": 0, "overall_last": None,
            "last_run_at": None, "last_error": None,
            "tick_wakes": 0, "idle_wakes": 0, "watching_keys": 0,
        }

    def status(self) -> Dict[str, Any]:
        return dict(self._stats)

    async def _cycle(self) -> List[Dict[str, Any]]:
        """One exit-marking pass. Never raises — records errors in stats."""
        try:
            db = self._db_factory()
            tick_lookup = self._tick_lookup_factory()
            summaries = await self._mark_fn(db, latest_tick_lookup=tick_lookup)
            if self._overall_fn is not None:
                overall = await self._overall_fn(db, latest_tick_lookup=tick_lookup)
                self._stats["overall_last"] = {k: overall.get(k) for k in ("exit", "reason", "mtm")}
                if overall.get("exit"):
                    self._stats["overall_exits"] += 1
                    log.info("overall controls squared the paper basket: %s", overall.get("reason"))
            closed = sum(1 for s in (summaries or []) if s.get("closed"))
            self._stats["cycles"] += 1
            self._stats["open_trades_checked"] += len(summaries or [])
            self._stats["auto_closes"] += closed
            self._stats["last_run_at"] = datetime.now(timezone.utc).isoformat()
            self._stats["last_error"] = None
            # Refresh the watch set from what this cycle actually saw — no extra
            # DB round-trip. A trade that just closed drops out; a trade that just
            # opened is picked up on the next cycle (entries are bar-gated, so a
            # sub-second lag to start watching one cannot miss a stop).
            self._watch_keys = {
                str(s.get("instrument_key")) for s in (summaries or [])
                if s.get("instrument_key")
            }
            self._stats["watching_keys"] = len(self._watch_keys)
            return summaries or []
        except Exception as exc:
            self._stats["last_error"] = str(exc)[:240]
            log.exception("live exit monitor cycle failed: %s", exc)
            return []

    async def _wait_for_work(self) -> bool:
        """Block until a cycle is due. Returns True if a held contract ticked.

        Without a stream this is the original ``sleep(poll_seconds)``. With one,
        the poll interval becomes a TIMEOUT rather than a fixed delay: a tick on a
        watched contract cuts the wait short, a tick on anything else is drained
        and ignored, and a silent feed still cycles every ``poll_seconds`` so a
        dead stream can never stop stop-loss checking.
        """
        if self._queue is None:
            await asyncio.sleep(self._poll_seconds)
            return False

        loop = asyncio.get_event_loop()
        deadline = loop.time() + self._poll_seconds
        while True:
            timeout = deadline - loop.time()
            if timeout <= 0:
                return False
            try:
                tick = await asyncio.wait_for(self._queue.get(), timeout=timeout)
            except asyncio.TimeoutError:
                return False
            hit = str((tick or {}).get("instrument_key") or "") in self._watch_keys
            # Drain whatever else arrived in the same instant; one cycle covers
            # the whole batch.
            while True:
                try:
                    other = self._queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
                hit = hit or str((other or {}).get("instrument_key") or "") in self._watch_keys
            if hit:
                return True

    async def _run(self) -> None:
        self._stats["running"] = True
        self._stats["started_at"] = datetime.now(timezone.utc).isoformat()
        self._queue = self._subscribe(max_queue=512) if self._subscribe else None
        last_cycle = 0.0
        woke_on_tick = False
        loop = asyncio.get_event_loop()
        try:
            # Cycle FIRST, then wait. The old loop slept 1.5s before its first
            # check, so a restart left open positions unwatched for that window —
            # and the tick-wake path cannot work at all until a cycle has told it
            # which contracts are held.
            while True:
                if self._in_market_hours():
                    self._stats["tick_wakes" if woke_on_tick else "idle_wakes"] += 1
                    last_cycle = loop.time()
                    summaries = await self._cycle()
                    closed = [s for s in summaries if s.get("closed")]
                    if closed:
                        log.info("exit monitor auto-closed %d trade(s): %s", len(closed),
                                 ", ".join(f"{s.get('id','')[:8]}/{s.get('exit_reason')}" for s in closed[:5]))
                woke_on_tick = await self._wait_for_work()
                if woke_on_tick:
                    # Floor the tick-driven path so a 40/s feed cannot busy-loop
                    # a 13ms cycle. The idle path is already floored by poll_seconds.
                    wait = (last_cycle + self._tick_floor_seconds) - loop.time()
                    if wait > 0:
                        await asyncio.sleep(wait)
        except asyncio.CancelledError:
            raise  # propagate so task.cancelled() is True (finally still runs)
        finally:
            self._stats["running"] = False
            if self._queue is not None and self._unsubscribe is not None:
                self._unsubscribe(self._queue)
            self._queue = None

    async def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._task = asyncio.create_task(self._run(), name="live-exit-monitor")

    async def stop(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None
