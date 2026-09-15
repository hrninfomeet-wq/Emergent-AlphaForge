"""Count every Flattrade REST call, so "broker call volume" is a number.

The Flattrade API key — and therefore its rate budget — is SHARED with the
Flattrade MCP (see docs/flattrade-mcp-integration.md). Any change to how the UI
reads the broker has to prove it did not spend more of that budget, and the only
honest way to prove it is to count.

``FlattradeClient._post`` is the single choke point for every REST call the app
makes (``_post_alert`` and ``_post_ok`` both route through it), so one meter here
sees everything. Deliberately cheap: a bounded deque of timestamps plus lifetime
totals, no locks (single-process asyncio), no I/O.
"""
from __future__ import annotations

import time
from collections import Counter, deque
from typing import Any, Deque, Dict, Optional, Tuple

#: Rolling window kept for rate reporting. 5 min is long enough to smooth the
#: 15s/30s cadences on the Live page without holding a meaningful amount of memory.
WINDOW_S = 300.0
_MAX_EVENTS = 20_000


class BrokerCallMeter:
    def __init__(self, *, window_s: float = WINDOW_S, clock=time.monotonic) -> None:
        self._window_s = float(window_s)
        self._clock = clock
        self._events: Deque[Tuple[float, str]] = deque(maxlen=_MAX_EVENTS)
        self._totals: Counter = Counter()
        self._started = clock()

    def record(self, route: str) -> None:
        self._events.append((self._clock(), str(route or "?")))
        self._totals[str(route or "?")] += 1

    def _prune(self, now: float) -> None:
        cutoff = now - self._window_s
        while self._events and self._events[0][0] < cutoff:
            self._events.popleft()

    def snapshot(self) -> Dict[str, Any]:
        """Per-route counts in the rolling window, plus calls/min and lifetime totals."""
        now = self._clock()
        self._prune(now)
        window_routes: Counter = Counter(route for _ts, route in self._events)
        observed_s = min(self._window_s, max(1e-6, now - self._started))
        in_window = sum(window_routes.values())
        return {
            "window_s": round(observed_s, 1),
            "calls_in_window": in_window,
            "calls_per_min": round(in_window * 60.0 / observed_s, 2),
            "by_route_window": dict(window_routes.most_common()),
            "by_route_total": dict(self._totals.most_common()),
            "total": sum(self._totals.values()),
        }

    def reset(self) -> None:
        """Clear the window and totals — used to take a clean before/after reading."""
        self._events.clear()
        self._totals.clear()
        self._started = self._clock()


#: Process-global meter. One key, one budget, one counter.
METER = BrokerCallMeter()


def record_broker_call(route: str) -> None:
    METER.record(route)


def broker_call_snapshot() -> Dict[str, Any]:
    return METER.snapshot()


def reset_broker_calls() -> Optional[Dict[str, Any]]:
    before = METER.snapshot()
    METER.reset()
    return before
