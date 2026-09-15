"""Single-flight, pull-only cache over an expensive async read.

The live marks stream emits many times a second. The broker book underneath it
must still be read at the SAME cadence it always was (15s), no matter how many
streams or browser tabs are attached — the Flattrade key and its rate budget are
shared with the Flattrade MCP, so the streaming work is only allowed if broker
call volume stays flat or falls.

Four properties give us that:

* **Pull-only.** There is deliberately no background refresher task. A snapshot
  is fetched only when something actually asks for one, so an unattended screen
  spends zero rate budget. N readers share one fetch.
* **Single-flight.** Concurrent readers awaiting a cold/expired cache await the
  *same* fetch rather than each starting one.
* **Back off on failure.** A FAILED fetch starts a backoff window. Without this
  the failure path is a retry storm, because a failure leaves the cache expired
  and every subsequent read re-attempts. Measured live on 2026-09-15 against an
  expired Flattrade session: **286 broker calls/min** (132 PositionBook in 30s)
  against a 12/min baseline, until the page was closed.
* **Refuse rather than lie.** Last-good is served only while it is *plausibly*
  current. Past ``max_stale_s`` the cache raises :class:`StaleSnapshotError` so
  the caller can fail honestly. Serving an old book indefinitely put a squared-off
  position on a real-money screen with a Day P&L that moved, because the stale
  book kept being re-marked against live ticks — a fabricated number wearing the
  costume of a live one.

The asymmetry is deliberate: a brief stale read is better than a blank screen, and
a blank screen is better than a confident wrong one.
"""
from __future__ import annotations

import asyncio
import time
from typing import Any, Awaitable, Callable, Optional

Fetch = Callable[[], Awaitable[Any]]

#: How long a snapshot may keep being served after the source started failing.
#: Four missed 15s refreshes — long enough to ride out a transient broker blip,
#: short enough that an expired session blanks the screen within a minute rather
#: than rendering a position that no longer exists.
DEFAULT_MAX_STALE_S = 60.0


class StaleSnapshotError(RuntimeError):
    """The cached value is too old to present as current, and the source is down.

    Carries the underlying failure (e.g. "Session Expired") so the operator sees
    the actionable cause rather than a generic staleness message.
    """


class SnapshotCache:
    """Cache one expensive async read behind a refresh interval + single-flight lock."""

    def __init__(
        self,
        *,
        fetch: Fetch,
        refresh_s: float = 15.0,
        clock: Callable[[], float] = time.monotonic,
        error_backoff_s: Optional[float] = None,
        max_stale_s: Optional[float] = DEFAULT_MAX_STALE_S,
    ) -> None:
        self._fetch = fetch
        self._refresh_s = float(refresh_s)
        self._clock = clock
        # Default the backoff to the refresh interval: a persistently failing
        # source is then retried at exactly the cadence a healthy one is read, so
        # the failure path can never cost more rate budget than the happy path.
        self._error_backoff_s = float(
            error_backoff_s if error_backoff_s is not None else refresh_s)
        self._max_stale_s = max_stale_s
        self._lock = asyncio.Lock()
        self._value: Any = None
        self._fetched_at: Optional[float] = None
        self._failed_at: Optional[float] = None
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

    def _in_backoff(self) -> bool:
        if self._failed_at is None:
            return False
        return (self._clock() - self._failed_at) < self._error_backoff_s

    def _too_stale(self) -> bool:
        if self._max_stale_s is None or self._fetched_at is None:
            return False
        return (self._clock() - self._fetched_at) > self._max_stale_s

    def _serve_last_good(self) -> Any:
        """Return last-good, or refuse if there is nothing safe to serve.

        The no-snapshot case matters as much as the too-old one. Returning the
        empty ``_value`` here would hand callers a book-shaped nothing, which
        renders as a FLAT account — the inverse and more dangerous failure: the
        app showing no position while the broker holds one. There is no honest
        answer when the source has never been read, so refuse.
        """
        if self._fetched_at is None:
            raise StaleSnapshotError(
                f"no snapshot has ever been read and the source is failing: {self.last_error}")
        if self._too_stale():
            age = self.age_ms()
            raise StaleSnapshotError(
                f"snapshot is {age} ms old and the source is failing: {self.last_error}")
        return self._value

    def invalidate(self) -> None:
        """Force the next get() to refetch.

        Used after a user-initiated action (square, kill switch) where the point
        IS to spend one broker call and see the real book immediately, rather
        than show a pre-action snapshot as if it were current. Also clears the
        error backoff — an explicit request is worth one attempt.
        """
        self._fetched_at = None
        self._failed_at = None

    # -- read --------------------------------------------------------------
    async def get(self) -> Any:
        """Return the snapshot, refreshing it at most once per interval.

        Raises when a fetch fails and there is no previous snapshot, and
        :class:`StaleSnapshotError` when the previous snapshot has aged past
        ``max_stale_s`` while the source is still failing.
        """
        if self._is_fresh():
            return self._value
        # A failing source is retried on the backoff schedule, NOT on every read.
        if self._in_backoff():
            return self._serve_last_good()

        async with self._lock:
            # Re-check: a concurrent reader may have refreshed while we waited.
            if self._is_fresh():
                return self._value
            if self._in_backoff():
                return self._serve_last_good()
            try:
                value = await self._fetch()
            except Exception as exc:  # noqa: BLE001 — every source error is handled the same
                self.last_error = str(exc)[:240]
                self._failed_at = self._clock()
                if self._fetched_at is None:
                    raise
                self.stale = True
                return self._serve_last_good()
            self._value = value
            self._fetched_at = self._clock()
            self._failed_at = None
            self.last_error = None
            self.stale = False
            return value


#: The paper open-positions stream reuses this for its OPEN-rows Mongo read at a
#: ~1s TTL — same problem (a high-rate stream over a source that changes rarely),
#: same single-flight answer.
BrokerSnapshotCache = SnapshotCache
