"""Single-flight, pull-only cache over a broker read.

The live marks stream emits at ~10Hz. The broker book underneath it must still
be read at the SAME cadence it always was (15s), no matter how many streams or
browser tabs are attached — the Flattrade key and its rate budget are shared with
the Flattrade MCP, so the streaming work is only allowed if broker call volume
stays flat or falls.

Three properties give us that:

* **Pull-only.** There is deliberately no background refresher task. A snapshot
  is fetched only when something actually asks for one, so an unattended screen
  (or a closed browser) spends zero rate budget. This is a real difference from
  the old path: each open tab used to drive its own 15s poll, so two tabs meant
  two broker calls per interval. Now N readers share one.
* **Single-flight.** Concurrent readers awaiting a cold/expired cache await the
  *same* fetch rather than each starting one.
* **Last-good on failure.** A transient broker error keeps serving the previous
  snapshot with ``stale``/``last_error`` set, so a real-money screen degrades to
  "as of HH:MM:SS, stale" instead of blanking. Only a failure with nothing
  cached propagates.
"""
from __future__ import annotations

import asyncio
import time
from typing import Any, Awaitable, Callable, Optional

Fetch = Callable[[], Awaitable[Any]]


class SnapshotCache:
    """Cache one expensive async read behind a refresh interval + single-flight lock."""

    def __init__(
        self,
        *,
        fetch: Fetch,
        refresh_s: float = 15.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._fetch = fetch
        self._refresh_s = float(refresh_s)
        self._clock = clock
        self._lock = asyncio.Lock()
        self._value: Any = None
        self._fetched_at: Optional[float] = None
        self.last_error: Optional[str] = None
        self.stale: bool = False

    # -- introspection -----------------------------------------------------
    def age_ms(self) -> Optional[int]:
        """Age of the cached snapshot in ms, or None when nothing is cached."""
        if self._fetched_at is None:
            return None
        return int(max(0.0, self._clock() - self._fetched_at) * 1000)

    def _is_fresh(self) -> bool:
        if self._fetched_at is None:
            return False
        return (self._clock() - self._fetched_at) < self._refresh_s

    def invalidate(self) -> None:
        """Force the next get() to refetch.

        Used after a user-initiated action (square, kill switch) where the point
        IS to spend one broker call and see the real book immediately, rather
        than show a pre-action snapshot as if it were current.
        """
        self._fetched_at = None

    # -- read --------------------------------------------------------------
    async def get(self) -> Any:
        """Return the snapshot, refreshing it at most once per interval.

        Raises only when a fetch fails and there is no previous snapshot to fall
        back to — callers can then surface "not connected" honestly.
        """
        if self._is_fresh():
            return self._value

        async with self._lock:
            # Re-check: a concurrent reader may have refreshed while we waited.
            if self._is_fresh():
                return self._value
            try:
                value = await self._fetch()
            except Exception as exc:  # noqa: BLE001 — every broker error is handled the same
                self.last_error = str(exc)[:240]
                if self._fetched_at is None:
                    raise
                self.stale = True
                return self._value
            self._value = value
            self._fetched_at = self._clock()
            self.last_error = None
            self.stale = False
            return value


#: The paper open-positions stream reuses this for its OPEN-rows Mongo read at a
#: ~1s TTL — same problem (a 10Hz stream over a source that changes rarely), same
#: single-flight answer.
BrokerSnapshotCache = SnapshotCache
