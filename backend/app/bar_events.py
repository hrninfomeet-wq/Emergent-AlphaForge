"""Bar-close notification between the candle roller and the deployment evaluator.

WHY
---
Entries are evaluated on the CLOSED 1-minute bar, and that must not change:
tick-driven entries would repaint signals and break parity with the backtest that
chose the strategy. But the evaluator was *polling* for the new bar every 2s, so
a signal that was fully determined the instant the bar closed waited a further
mean ~1s (max ~2s) before anyone looked at it. That is dead time, not policy.

The roller already knows the exact moment it flushes a bar. This lets it say so.

WHAT THIS DOES NOT CHANGE
-------------------------
The evaluator still gates on `candles_1m` actually holding a newer timestamp, and
still evaluates the same closed bar with the same inputs. This only removes the
wait between "the bar exists" and "someone noticed". A missed or dropped signal
costs nothing, because the poll interval remains as a timeout — so a roller that
never signals (or is not running at all) behaves exactly as before.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Optional

log = logging.getLogger(__name__)

_bar_flushed: Optional[asyncio.Event] = None


def _event() -> asyncio.Event:
    """Lazily created so import order never binds this to the wrong event loop."""
    global _bar_flushed
    if _bar_flushed is None:
        _bar_flushed = asyncio.Event()
    return _bar_flushed


def signal_bar_flushed() -> None:
    """Announce that a 1-minute bar just landed. Never raises.

    Called from the candle roller's flush path, which must never fail because a
    listener is missing or the loop is shutting down.
    """
    try:
        _event().set()
    except Exception as exc:  # noqa: BLE001
        log.debug("bar-flush signal dropped: %s", exc)


async def wait_for_bar(timeout: float) -> bool:
    """Block until a bar flushes or ``timeout`` elapses. True ⇒ a bar flushed.

    Auto-clears, so each wait corresponds to at least one new flush. The timeout
    is the safety floor: it is what makes this an optimisation rather than a
    dependency.
    """
    event = _event()
    try:
        await asyncio.wait_for(event.wait(), timeout=timeout)
    except asyncio.TimeoutError:
        return False
    finally:
        event.clear()
    return True


def reset() -> None:
    """Drop the event (tests only — each test gets its own loop)."""
    global _bar_flushed
    _bar_flushed = None
