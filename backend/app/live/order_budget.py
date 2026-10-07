"""Key-wide ORDER-API rate budget with a reserve for exits and cancels.

Flattrade enforces an ORDER API limit of 10 requests/second AND 40/minute per API
key (docs/Resources/flattrade-pi-api/endpoints/57-api-rate-limits.md). Every
order mutation spends it — entries, exits, cancels, modifies, GTT/OCO alerts —
and the Flattrade MCP shares the same key.

``safety.RateThrottle`` only meters deployed ENTRIES per second (a token bucket
that can burst ~2x across a second boundary) and has no per-minute window, so
entries alone could spend the minute and the broker would then refuse the exit
that protects a position. This budget:

  * COUNTS every order-API call this process makes, key-wide, in sliding 1 s and
    60 s windows. ``FlattradeClient._post`` records into it — the single choke
    point every REST call passes through, whoever the caller is.
  * GATES ENTRIES only: an entry is refused when taking it would leave less than
    the exit reserve plus a headroom for order traffic this process cannot see
    (the shared MCP). Exits and cancels are never refused here — throttling an
    exit traps a losing position — they simply count.

With the defaults an entry may use at most 5 of any rolling second and 24 of any
rolling minute, so >= 16/min and >= 5/s are always left for exits and cancels.
"""
from __future__ import annotations

import time
from collections import deque
from typing import Callable, Deque, Dict, Tuple

#: Flattrade ORDER API limits, per API key.
ORDER_API_PER_SEC = 10
ORDER_API_PER_MIN = 40

#: Routes the ORDER API limit applies to: every order / alert mutation.
ORDER_API_ROUTES = frozenset({
    "PlaceOrder", "ModifyOrder", "CancelOrder", "ExitSNOOrder",
    "PlaceGTTOrder", "ModifyGTTOrder", "CancelGTTOrder",
    "PlaceOCOOrder", "ModifyOCOOrder", "CancelOCOOrder",
})

#: Capacity entries may never take — kept for exits / cancels / re-prices.
EXIT_RESERVE_PER_SEC = 4
EXIT_RESERVE_PER_MIN = 12
#: Margin for order traffic on the same key that this process cannot count (MCP).
UNSEEN_HEADROOM_PER_SEC = 1
UNSEEN_HEADROOM_PER_MIN = 4


class OrderRateBudget:
    """Sliding-window count of one API key's order-API calls. Single-process
    asyncio: no locks; ``entry_allowed`` and the ``_post`` record run without an
    await between them only by accident, so the reserve also absorbs the odd
    over-admission between a gate check and its transmit."""

    def __init__(
        self,
        *,
        per_sec: int = ORDER_API_PER_SEC,
        per_min: int = ORDER_API_PER_MIN,
        exit_reserve_per_sec: int = EXIT_RESERVE_PER_SEC,
        exit_reserve_per_min: int = EXIT_RESERVE_PER_MIN,
        headroom_per_sec: int = UNSEEN_HEADROOM_PER_SEC,
        headroom_per_min: int = UNSEEN_HEADROOM_PER_MIN,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._per_sec = int(per_sec)
        self._per_min = int(per_min)
        self._entry_cap_sec = self._per_sec - int(exit_reserve_per_sec) - int(headroom_per_sec)
        self._entry_cap_min = self._per_min - int(exit_reserve_per_min) - int(headroom_per_min)
        if self._entry_cap_sec < 1 or self._entry_cap_min < 1:
            raise ValueError("exit reserve + headroom leave no room for entries")
        self._clock = clock
        self._events: Deque[float] = deque()

    def _prune(self, now: float) -> None:
        while self._events and self._events[0] <= now - 60.0:
            self._events.popleft()

    def record(self, route: str) -> None:
        """Count one call if ``route`` is an order-API route. Never refuses."""
        if str(route or "") not in ORDER_API_ROUTES:
            return
        now = self._clock()
        self._prune(now)
        self._events.append(now)

    def _counts(self, now: float) -> Tuple[int, int]:
        self._prune(now)
        last_sec = sum(1 for t in self._events if t > now - 1.0)
        return last_sec, len(self._events)

    def entry_allowed(self) -> Tuple[bool, str]:
        """(True, "") if one more ENTRY fits without touching the exit reserve."""
        last_sec, last_min = self._counts(self._clock())
        if last_sec + 1 > self._entry_cap_sec:
            return False, f"order_budget_per_sec:{last_sec}/{self._per_sec}"
        if last_min + 1 > self._entry_cap_min:
            return False, f"order_budget_per_min:{last_min}/{self._per_min}"
        return True, ""

    def limits(self) -> Dict[str, int]:
        return {"per_sec": self._per_sec, "per_min": self._per_min,
                "entry_cap_per_sec": self._entry_cap_sec,
                "entry_cap_per_min": self._entry_cap_min}

    def snapshot(self) -> Dict[str, int]:
        last_sec, last_min = self._counts(self._clock())
        return {"last_1s": last_sec, "last_60s": last_min, **self.limits()}


_BUDGETS: Dict[str, OrderRateBudget] = {}


def budget_for(key: str) -> OrderRateBudget:
    """The process-wide budget for one API key (the account uid identifies it —
    the daily session token changes, the key's rate limit does not)."""
    k = str(key or "")
    b = _BUDGETS.get(k)
    if b is None:
        b = _BUDGETS[k] = OrderRateBudget()
    return b
