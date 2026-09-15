"""Wire the pure marking layer to its real sources.

Three inputs, three very different refresh rates, and getting that right is the
whole point of this module:

* **Broker book** — the source of truth for qty / avg / realized. Read at most
  once per ``BROKER_REFRESH_S`` through :class:`SnapshotCache`, shared by
  every connected stream. This is what keeps broker call volume flat.
* **Contract index** — the identity -> Upstox instrument_key map. Positions
  change a few times a day, so the index is rebuilt ONLY when the position
  book's identity set changes. Rebuilding per emit would turn a 10Hz stream into
  a 10Hz Mongo query.
* **Tick map** — already in process memory. Free to read at any rate (measured
  0.007ms for 53 keys).
"""
from __future__ import annotations

import logging
import time
from typing import Any, Awaitable, Callable, Dict, List, Optional, Sequence

from app.live_mark_cache import SnapshotCache
from app.live_marks import Identity, build_contract_index, mark_positions, parse_position_identity

log = logging.getLogger(__name__)

#: Broker book refresh — deliberately identical to the old frontend poll cadence,
#: so the streaming path spends exactly the same rate budget it always did.
BROKER_REFRESH_S = 15.0
#: A tick older than this is not a mark. Generous enough to survive an illiquid
#: strike between prints, tight enough that a dead feed shows as "broker" not "live".
MARK_MAX_AGE_MS = 10_000


class LiveMarksService:
    """Produce the tick-marked live position payload."""

    def __init__(
        self,
        *,
        fetch_positions: Callable[[], Awaitable[List[Dict[str, Any]]]],
        load_contracts: Callable[[Sequence[Identity]], Awaitable[List[Dict[str, Any]]]],
        tick_map_factory: Callable[[], Dict[str, Dict[str, Any]]],
        refresh_s: float = BROKER_REFRESH_S,
        max_age_ms: int = MARK_MAX_AGE_MS,
        now_ms: Callable[[], int] = lambda: int(time.time() * 1000),
    ) -> None:
        self._cache = SnapshotCache(fetch=fetch_positions, refresh_s=refresh_s)
        self._load_contracts = load_contracts
        self._tick_map_factory = tick_map_factory
        self._max_age_ms = int(max_age_ms)
        self._now_ms = now_ms
        self._index: Dict[Identity, str] = {}
        self._index_for: Optional[frozenset] = None
        self.index_builds = 0
        self._last_tick_keys: List[str] = []

    def invalidate(self) -> None:
        """Drop the cached broker book so the next payload refetches it."""
        self._cache.invalidate()

    def last_tick_keys(self) -> List[str]:
        """Contracts the LIVE book held at the last payload build. Never does I/O.

        The Upstox stream auto-follow uses this to keep a real-money position's
        contract subscribed. It must NOT trigger a broker read — the auto-follow
        runs once a minute and forcing a fetch there would add broker calls to a
        path that is supposed to cost none. A cold cache returns [] and the
        contract is picked up on the next pass once a page warms the service.
        """
        return list(self._last_tick_keys)

    async def _index_for_positions(self, positions: List[Dict[str, Any]]) -> Dict[Identity, str]:
        idents = {i for i in (parse_position_identity(p) for p in positions) if i is not None}
        key = frozenset(idents)
        if key == self._index_for:
            return self._index
        rows = await self._load_contracts(sorted(idents)) if idents else []
        self._index = build_contract_index(rows)
        self._index_for = key
        self.index_builds += 1
        return self._index

    async def payload(self) -> Dict[str, Any]:
        positions = await self._cache.get()
        index = await self._index_for_positions(positions)
        now_ms = self._now_ms()
        # NEVER tick-mark a stale book. Re-marking a book we can no longer read
        # against live ticks produces a number that MOVES — which is exactly how a
        # squared-off position came to show a real-time Day P&L on 2026-09-15.
        # A frozen broker value reads as stale; a moving one reads as live. When
        # the source is failing, fall back to the broker's own last numbers and
        # let every row say mark_source="broker".
        tick_lookup = ({}.get if self._cache.stale
                       else self._tick_map_factory().get)
        marked = mark_positions(
            positions,
            tick_lookup=tick_lookup,
            now_ms=now_ms,
            max_age_ms=self._max_age_ms,
            contract_index=index,
        )
        marked.update({
            # How old the qty/avg/realized half of the screen is.
            "broker_age_ms": self._cache.age_ms(),
            "broker_stale": bool(self._cache.stale),
            "broker_error": self._cache.last_error,
            # Server clock at emit — lets the client measure tick-to-pixel and
            # correct for any client/server skew instead of assuming none.
            "emitted_at_ms": now_ms,
            "source": "stream",
        })
        self._last_tick_keys = list(marked.get("tick_keys") or [])
        return marked


async def load_contracts_for(db: Any, identities: Sequence[Identity]) -> List[Dict[str, Any]]:
    """Fetch option_contracts rows for a set of (underlying, expiry, strike, side).

    Queried by identity rather than exchange token: tokens are recycled across
    expiries, so a token query returns contracts that expired years ago.
    """
    if not identities:
        return []
    clauses = [
        {"underlying": u, "expiry_date": e, "strike": s, "side": side}
        for (u, e, s, side) in identities
    ]
    projection = {"_id": 0, "instrument_key": 1, "underlying": 1,
                  "expiry_date": 1, "strike": 1, "side": 1}
    try:
        return await db.option_contracts.find({"$or": clauses}, projection).to_list(length=1000)
    except Exception as exc:  # noqa: BLE001 — no index just means we mark from the broker
        log.warning("live marks: contract lookup failed: %s", exc)
        return []
