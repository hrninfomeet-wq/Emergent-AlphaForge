"""Layer 2: stop/target detection latency.

The monitor used to sleep a flat 1.5s between cycles, so a stop was detected on
average 750ms (p95 ~1425ms) after the tick that breached it — pure dead time on a
real-money exit. It now wakes on a tick for a contract it actually holds, floored
so a measured 40 ticks/s cannot turn a 13ms cycle into a busy loop.

The 1.5s cycle remains as a FLOOR, not a ceiling: with no stream, or no ticks, the
monitor behaves exactly as it did.
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from app.live_exit_monitor import LiveExitMonitor


class _Stream:
    def __init__(self):
        self.queue = asyncio.Queue(maxsize=256)
        self.unsubscribed = 0

    def subscribe(self, **_kw):
        return self.queue

    def unsubscribe(self, _q):
        self.unsubscribed += 1


def _monitor(stream, *, summaries=None, **kw):
    cycles = {"n": 0}

    async def mark(db, *, latest_tick_lookup):
        cycles["n"] += 1
        return list(summaries or [])

    mon = LiveExitMonitor(
        db_factory=lambda: object(),
        tick_lookup_factory=lambda: (lambda k: None),
        mark_fn=mark,
        subscribe=stream.subscribe,
        unsubscribe=stream.unsubscribe,
        in_market_hours=lambda: True,
        **kw,
    )
    return mon, cycles


HELD = [{"id": "t1", "instrument_key": "NSE_FO|47291", "closed": False}]


def test_a_tick_on_a_held_contract_wakes_the_monitor_far_inside_the_poll_interval():
    stream = _Stream()
    mon, cycles = _monitor(stream, summaries=HELD, poll_seconds=5.0, tick_floor_seconds=0.0)

    async def run():
        task = asyncio.create_task(mon._run())
        await asyncio.sleep(0.05)          # first (idle-path) cycle establishes the watch set
        first = cycles["n"]
        stream.queue.put_nowait({"instrument_key": "NSE_FO|47291"})
        await asyncio.sleep(0.05)
        second = cycles["n"]
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        return first, second

    first, second = asyncio.run(run())
    assert first >= 1, "the monitor must run an initial cycle to learn what it holds"
    assert second > first, "a tick on a held contract did not wake the monitor"


def test_ticks_on_contracts_we_do_not_hold_are_ignored():
    """~53 instruments are subscribed; only the few we hold should cost a cycle."""
    stream = _Stream()
    mon, cycles = _monitor(stream, summaries=HELD, poll_seconds=5.0, tick_floor_seconds=0.0)

    async def run():
        task = asyncio.create_task(mon._run())
        await asyncio.sleep(0.05)
        first = cycles["n"]
        for i in range(50):
            stream.queue.put_nowait({"instrument_key": f"NSE_INDEX|Other{i}"})
        await asyncio.sleep(0.1)
        second = cycles["n"]
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        return first, second

    first, second = asyncio.run(run())
    assert second == first, f"unheld ticks caused {second - first} wasted cycles"


def test_the_floor_bounds_the_cycle_rate_under_a_tick_storm():
    """A measured 40-50 ticks/s must not become 40-50 cycles/s."""
    stream = _Stream()
    mon, cycles = _monitor(stream, summaries=HELD, poll_seconds=5.0, tick_floor_seconds=0.08)

    async def run():
        task = asyncio.create_task(mon._run())
        await asyncio.sleep(0.02)
        start = cycles["n"]
        deadline = asyncio.get_event_loop().time() + 0.4
        while asyncio.get_event_loop().time() < deadline:
            stream.queue.put_nowait({"instrument_key": "NSE_FO|47291"})
            await asyncio.sleep(0.005)     # 200 ticks/s
        end = cycles["n"]
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        return end - start

    ran = asyncio.run(run())
    # 0.4s at an 80ms floor => at most ~6 cycles (plus scheduling slack).
    assert ran <= 8, f"floor did not bound the cycle rate: {ran} cycles in 0.4s"
    assert ran >= 2, f"floor throttled too hard: only {ran} cycles in 0.4s"


def test_without_a_stream_it_is_the_old_fixed_interval_loop():
    """No subscribe wired => byte-for-byte the previous behaviour."""
    cycles = {"n": 0}

    async def mark(db, *, latest_tick_lookup):
        cycles["n"] += 1
        return []

    mon = LiveExitMonitor(
        db_factory=lambda: object(),
        tick_lookup_factory=lambda: (lambda k: None),
        mark_fn=mark,
        poll_seconds=0.05,
        in_market_hours=lambda: True,
    )

    async def run():
        task = asyncio.create_task(mon._run())
        await asyncio.sleep(0.22)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(run())
    assert cycles["n"] >= 3, f"fixed-interval loop ran {cycles['n']} cycles"


def test_the_poll_interval_still_fires_when_nothing_ticks():
    """A dead feed must not stop stop-loss checking — the interval is a floor."""
    stream = _Stream()
    mon, cycles = _monitor(stream, summaries=HELD, poll_seconds=0.05, tick_floor_seconds=0.0)

    async def run():
        task = asyncio.create_task(mon._run())
        await asyncio.sleep(0.22)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(run())
    assert cycles["n"] >= 3, f"idle loop ran only {cycles['n']} cycles"


def test_it_unsubscribes_when_stopped():
    stream = _Stream()
    mon, _ = _monitor(stream, summaries=HELD, poll_seconds=0.05)

    async def run():
        await mon.start()
        await asyncio.sleep(0.05)
        await mon.stop()

    asyncio.run(run())
    assert stream.unsubscribed == 1, "the tick queue leaked on stop"


def test_outside_market_hours_no_cycle_runs():
    stream = _Stream()
    cycles = {"n": 0}

    async def mark(db, *, latest_tick_lookup):
        cycles["n"] += 1
        return []

    mon = LiveExitMonitor(
        db_factory=lambda: object(),
        tick_lookup_factory=lambda: (lambda k: None),
        mark_fn=mark,
        subscribe=stream.subscribe,
        unsubscribe=stream.unsubscribe,
        poll_seconds=0.02,
        tick_floor_seconds=0.0,
        in_market_hours=lambda: False,
    )

    async def run():
        task = asyncio.create_task(mon._run())
        for _ in range(20):
            stream.queue.put_nowait({"instrument_key": "NSE_FO|47291"})
        await asyncio.sleep(0.15)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(run())
    assert cycles["n"] == 0, "the monitor must stand down outside market hours"


def test_stats_separate_tick_wakes_from_idle_wakes():
    """Operational visibility: is the fast path actually firing?"""
    stream = _Stream()
    mon, _ = _monitor(stream, summaries=HELD, poll_seconds=5.0, tick_floor_seconds=0.0)

    async def run():
        task = asyncio.create_task(mon._run())
        await asyncio.sleep(0.05)
        stream.queue.put_nowait({"instrument_key": "NSE_FO|47291"})
        await asyncio.sleep(0.05)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(run())
    st = mon.status()
    assert st["tick_wakes"] >= 1
    assert st["watching_keys"] == 1
