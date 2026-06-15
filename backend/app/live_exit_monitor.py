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

log = logging.getLogger(__name__)

POLL_SECONDS = 1.5
_IST = timedelta(hours=5, minutes=30)


def _in_market_hours(now_utc: Optional[datetime] = None) -> bool:
    ist = (now_utc or datetime.now(timezone.utc)) + _IST
    if ist.weekday() >= 5:
        return False
    return dtime(9, 15) <= ist.time() < dtime(15, 30)


class LiveExitMonitor:
    def __init__(
        self,
        *,
        db_factory: Callable[[], Any],
        tick_lookup_factory: Callable[[], Callable[[str], Optional[Dict[str, Any]]]],
        mark_fn: Callable[..., Awaitable[List[Dict[str, Any]]]],
        poll_seconds: float = POLL_SECONDS,
    ):
        self._db_factory = db_factory
        self._tick_lookup_factory = tick_lookup_factory
        self._mark_fn = mark_fn
        self._poll_seconds = float(poll_seconds)
        self._task: Optional[asyncio.Task] = None
        self._stats: Dict[str, Any] = {
            "running": False, "started_at": None, "cycles": 0,
            "open_trades_checked": 0, "auto_closes": 0,
            "last_run_at": None, "last_error": None,
        }

    def status(self) -> Dict[str, Any]:
        return dict(self._stats)

    async def _cycle(self) -> List[Dict[str, Any]]:
        """One exit-marking pass. Never raises — records errors in stats."""
        try:
            db = self._db_factory()
            tick_lookup = self._tick_lookup_factory()
            summaries = await self._mark_fn(db, latest_tick_lookup=tick_lookup)
            closed = sum(1 for s in (summaries or []) if s.get("closed"))
            self._stats["cycles"] += 1
            self._stats["open_trades_checked"] += len(summaries or [])
            self._stats["auto_closes"] += closed
            self._stats["last_run_at"] = datetime.now(timezone.utc).isoformat()
            self._stats["last_error"] = None
            return summaries or []
        except Exception as exc:
            self._stats["last_error"] = str(exc)[:240]
            log.exception("live exit monitor cycle failed: %s", exc)
            return []

    async def _run(self) -> None:
        self._stats["running"] = True
        self._stats["started_at"] = datetime.now(timezone.utc).isoformat()
        try:
            while True:
                await asyncio.sleep(self._poll_seconds)
                if not _in_market_hours():
                    continue
                summaries = await self._cycle()
                closed = [s for s in summaries if s.get("closed")]
                if closed:
                    log.info("exit monitor auto-closed %d trade(s): %s", len(closed),
                             ", ".join(f"{s.get('id','')[:8]}/{s.get('exit_reason')}" for s in closed[:5]))
        except asyncio.CancelledError:
            return
        finally:
            self._stats["running"] = False

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
