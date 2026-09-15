"""Bar-close notification: entries stop waiting out a poll interval.

Entry evaluation stays on the CLOSED 1m bar — that is a parity requirement, not a
performance one. What changes is only how fast the evaluator NOTICES that the bar
closed: mean ~1s of polling delay becomes ~0.
"""
import asyncio
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from app import bar_events


def _fresh():
    bar_events.reset()


def test_a_flushed_bar_wakes_the_waiter_immediately():
    _fresh()

    async def run():
        loop = asyncio.get_event_loop()
        t0 = loop.time()

        async def flush_soon():
            await asyncio.sleep(0.02)
            bar_events.signal_bar_flushed()

        asyncio.create_task(flush_soon())
        got = await bar_events.wait_for_bar(timeout=5.0)
        return got, loop.time() - t0

    got, elapsed = asyncio.run(run())
    assert got is True
    assert elapsed < 0.5, f"waited {elapsed:.3f}s for a bar that flushed at 0.02s"


def test_the_timeout_is_the_floor_so_a_silent_roller_still_cycles():
    """This is what keeps it an optimisation rather than a dependency: if the
    roller never signals, the evaluator behaves exactly as it did when polling."""
    _fresh()

    async def run():
        loop = asyncio.get_event_loop()
        t0 = loop.time()
        got = await bar_events.wait_for_bar(timeout=0.1)
        return got, loop.time() - t0

    got, elapsed = asyncio.run(run())
    assert got is False
    assert elapsed >= 0.09


def test_each_wait_needs_its_own_flush():
    """Auto-clear: one flush must not satisfy two waits, or the evaluator would
    spin re-evaluating a bar it already handled."""
    _fresh()

    async def run():
        bar_events.signal_bar_flushed()
        first = await bar_events.wait_for_bar(timeout=1.0)
        second = await bar_events.wait_for_bar(timeout=0.05)
        return first, second

    first, second = asyncio.run(run())
    assert first is True
    assert second is False


def test_a_flush_before_the_wait_is_not_lost():
    """The roller flushes on its own schedule; a signal arriving while the
    evaluator is mid-cycle must still be seen on the next wait."""
    _fresh()

    async def run():
        bar_events.signal_bar_flushed()   # arrives while the evaluator is busy
        await asyncio.sleep(0.01)
        return await bar_events.wait_for_bar(timeout=0.05)

    assert asyncio.run(run()) is True


def test_signalling_never_raises_even_with_no_loop():
    """The roller's flush path must never fail because of this."""
    _fresh()
    bar_events.signal_bar_flushed()   # no running loop — must be a no-op, not a crash
