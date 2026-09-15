"""The broker-snapshot cache is what keeps the tick-fresh stream from costing
any extra broker calls.

The Flattrade key and its rate budget are SHARED with the Flattrade MCP, so the
hard requirement on the streaming work is: broker call volume flat or lower. The
stream emits at ~10Hz; the broker underneath it must still be read at most once
per refresh interval, once per interval no matter how many streams/tabs are
open, and NEVER when nobody is watching.
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from app.live_mark_cache import SnapshotCache


def _cache(calls, *, refresh_s=15.0, clock=None):
    async def fetch():
        calls.append(1)
        return {"positions": [{"tsym": f"T{len(calls)}"}], "n": len(calls)}

    return SnapshotCache(fetch=fetch, refresh_s=refresh_s, clock=clock or (lambda: 0.0))


def test_first_read_fetches_once():
    calls = []
    cache = _cache(calls)
    snap = asyncio.run(cache.get())
    assert snap["n"] == 1
    assert len(calls) == 1


def test_reads_inside_the_interval_are_served_from_cache():
    """A 10Hz stream must not become a 10Hz broker poll."""
    calls = []
    now = {"t": 0.0}
    cache = _cache(calls, refresh_s=15.0, clock=lambda: now["t"])

    async def run():
        await cache.get()
        for _ in range(150):  # 15s of 10Hz emits
            now["t"] += 0.1
            await cache.get()

    asyncio.run(run())
    # t advances to 15.0 exactly, so at most one refresh is due on top of the first.
    assert len(calls) <= 2, f"expected <=2 broker fetches in 15s, got {len(calls)}"


def test_refreshes_once_the_interval_elapses():
    calls = []
    now = {"t": 0.0}
    cache = _cache(calls, refresh_s=15.0, clock=lambda: now["t"])

    async def run():
        await cache.get()
        now["t"] = 15.5
        return await cache.get()

    snap = asyncio.run(run())
    assert len(calls) == 2
    assert snap["n"] == 2


def test_concurrent_readers_share_one_fetch():
    """N open tabs must not mean N broker calls."""
    calls = []
    started = asyncio.Event()

    async def run():
        async def slow_fetch():
            calls.append(1)
            started.set()
            await asyncio.sleep(0.05)
            return {"n": len(calls)}

        cache = SnapshotCache(fetch=slow_fetch, refresh_s=15.0, clock=lambda: 0.0)
        results = await asyncio.gather(*(cache.get() for _ in range(8)))
        return results

    results = asyncio.run(run())
    assert len(calls) == 1, f"8 concurrent readers caused {len(calls)} broker fetches"
    assert all(r["n"] == 1 for r in results)


def test_a_failed_fetch_serves_the_last_good_snapshot_and_reports_the_error():
    """A transient broker error must not blank a real-money screen."""
    state = {"fail": False, "n": 0}

    async def flaky():
        if state["fail"]:
            raise RuntimeError("broker 502")
        state["n"] += 1
        return {"n": state["n"]}

    now = {"t": 0.0}
    cache = SnapshotCache(fetch=flaky, refresh_s=15.0, clock=lambda: now["t"])

    async def run():
        first = await cache.get()
        state["fail"] = True
        now["t"] = 20.0
        second = await cache.get()
        return first, second

    first, second = asyncio.run(run())
    assert first["n"] == 1
    assert second["n"] == 1          # last good, not an exception
    assert cache.last_error is not None
    assert cache.stale is True


def test_a_failed_first_fetch_raises_rather_than_inventing_a_book():
    async def boom():
        raise RuntimeError("no token")

    cache = SnapshotCache(fetch=boom, refresh_s=15.0, clock=lambda: 0.0)
    try:
        asyncio.run(cache.get())
    except RuntimeError as exc:
        assert "no token" in str(exc)
    else:
        raise AssertionError("expected the first failed fetch to raise")


def test_no_fetch_happens_without_a_reader():
    """Nobody watching == zero broker calls. The cache is pull-only: there is no
    background refresher that would spend rate budget on an unattended screen."""
    calls = []
    cache = _cache(calls)
    assert len(calls) == 0
    assert not hasattr(cache, "start"), "a pull-only cache must not expose a background task"


def test_age_ms_reports_snapshot_freshness():
    calls = []
    now = {"t": 100.0}
    cache = _cache(calls, refresh_s=15.0, clock=lambda: now["t"])

    async def run():
        await cache.get()
        now["t"] = 104.0
        return cache.age_ms()

    assert asyncio.run(run()) == 4000
