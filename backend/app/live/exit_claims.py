"""Process-level per-tsym EXIT mutex.

Three automated paths can place an exit order for the same trading symbol:
the software position guard, the 10-minute auto-square timer, and the manual
square route — and in production ALL THREE funnel through
``auto_square.square_position`` (the guard's injected ``square_fn`` is
``runtime._live_guard_square_fn`` → ``square_position``). If two of them fire on
the same tsym in the same asyncio loop, they can each place a SELL and the second
turns a flat/long position into a naked short (a margin-reject / unbounded-risk
event).

This registry serializes exits per tsym: a path claims the tsym before placing an
exit; a second concurrent path finds it claimed and SKIPS (its caller keeps
retrying / re-reads to confirm flat). In-process only — one backend process, all
exit paths share one asyncio loop — so an in-memory, ``asyncio.Lock``-guarded
registry is sufficient (no cross-process CAS needed). A TTL releases a claim whose
holder died without releasing, so a crash can never wedge a scrip permanently.
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from contextlib import asynccontextmanager
from typing import Callable, Dict, Optional, Tuple

log = logging.getLogger(__name__)

# The TTL only backstops a HUNG/DEAD holder (a crash takes the whole single-process
# backend down, clearing this in-memory registry). It MUST comfortably exceed the
# worst-case exit — a degraded broker gives each round-trip a 20s timeout
# (flattrade_client), and a single square does ~11 round-trips (fresh reads +
# cancel-confirm passes + banded place retries) ≈ 220s at the timeout ceiling — so
# a still-active exit path can NEVER have its claim silently reclaimed and
# double-sold — the whole point of the mutex. A hung holder blocking exits for this
# long is the acceptable side (the position keeps its existing protection meanwhile).
# The kill switch holds claims across its whole flatten + GTT sweep + re-sweep,
# which can run far longer than one square — it passes its own ttl_seconds
# (see live_broker._KILL_CLAIM_TTL_SECONDS).
_DEFAULT_TTL_SECONDS = 360.0


class ExitClaimRegistry:
    """Per-tsym exclusive-exit registry. STRICT: any unexpired claim blocks a new
    one (claims carry a unique token, so an expired holder's late release can never
    delete a successor's claim)."""

    def __init__(
        self,
        ttl_seconds: float = _DEFAULT_TTL_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._claims: Dict[str, Tuple[str, float]] = {}  # tsym -> (token, expires_at)
        self._lock = asyncio.Lock()
        self._ttl = float(ttl_seconds)
        self._clock = clock

    async def claim(
        self, tsym: str, token: str, ttl_seconds: Optional[float] = None
    ) -> bool:
        """Acquire the exit claim for *tsym*. True on success; False if another
        (unexpired) token already holds it. ``ttl_seconds`` overrides the registry
        default for THIS claim — the kill switch holds claims across its entire
        multi-pass flatten + re-sweep and must size the TTL to that, not to a
        single square (an expired kill claim would let the guard place a COMPETING
        exit mid-kill → double-sell)."""
        key = str(tsym)
        async with self._lock:
            now = self._clock()
            cur = self._claims.get(key)
            if cur is not None and cur[1] > now:
                return False
            self._claims[key] = (token, now + float(ttl_seconds or self._ttl))
            return True

    async def release(self, tsym: str, token: str) -> None:
        """Release *tsym* only if *token* still owns it (a token that timed out and
        was superseded must not delete the successor's claim)."""
        key = str(tsym)
        async with self._lock:
            cur = self._claims.get(key)
            if cur is not None and cur[0] == token:
                del self._claims[key]

    def reset(self) -> None:
        """Clear all claims (tests only)."""
        self._claims.clear()


# Module singleton — the one live backend process shares it across all exit paths.
_registry = ExitClaimRegistry()


def registry() -> ExitClaimRegistry:
    return _registry


def reset_exit_claims() -> None:
    """Reset the shared registry (tests only)."""
    _registry.reset()


@asynccontextmanager
async def claim_exit(tsym: str, label: str = ""):
    """``async with claim_exit(tsym, label) as got:`` — *got* is True when the
    caller now holds the exclusive exit claim for *tsym* (place the exit) and False
    when another path holds it (skip / retry later). Always releases on exit if it
    was acquired. An empty tsym yields True (nothing to serialize on)."""
    key = str(tsym or "")
    if not key:
        yield True
        return
    token = uuid.uuid4().hex
    got = await _registry.claim(key, token)
    if not got:
        log.warning("exit claim BUSY for %s (label=%s) — another exit path holds it", key, label)
    try:
        yield got
    finally:
        if got:
            await _registry.release(key, token)
