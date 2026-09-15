"""Regression: what SnapshotCache does when the underlying read keeps FAILING.

Captured live on 2026-09-15 with an expired Flattrade session. One defect in the
failure path produced three symptoms at once:

  1. **A retry storm.** A failed fetch did not advance `_fetched_at`, so
     `_is_fresh()` stayed False and EVERY `get()` re-hit the broker. With the SSE
     stream building payloads continuously that measured **286 broker calls/min
     (132 PositionBook in 30s)** against a 12/min baseline — a 22x blowout on a
     rate budget shared with the Flattrade MCP. Closing the page dropped it to 0.
  2. **A phantom position.** The last-good book was served indefinitely and
     re-marked against live ticks, so a squared-off position rendered with a Day
     P&L that MOVED. Measured: a 77-minute-old book (broker_age_ms 4,652,351)
     marked against a 598ms-old tick.
  3. **Latency collapse.** Every emit blocked on a failing HTTP round-trip, so
     tick-to-DOM went 96ms -> 355ms and the stream fell from ~20 emits/s to 4.5.

The rule these tests encode: a cache may serve last-good briefly, but it must
BACK OFF rather than retry, and it must eventually REFUSE rather than keep
presenting an old value as current.
"""
import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from app.live_mark_cache import SnapshotCache, StaleSnapshotError


def _failing_after(n_ok, clock):
    """A fetch that succeeds n_ok times then fails forever. Counts attempts."""
    state = {"calls": 0, "ok": 0}

    async def fetch():
        state["calls"] += 1
        if state["ok"] < n_ok:
            state["ok"] += 1
            return {"n": state["ok"]}
        raise RuntimeError("Session Expired :  Invalid Session Key")

    return fetch, state


def test_a_persistently_failing_fetch_backs_off_instead_of_retrying_every_read():
    """THE rate-budget regression. On the old code this made 101 broker calls."""
    now = {"t": 0.0}
    fetch, state = _failing_after(1, now)
    cache = SnapshotCache(fetch=fetch, refresh_s=15.0, error_backoff_s=15.0,
                          clock=lambda: now["t"])

    async def run():
        await cache.get()            # seeds a good snapshot (1 call)
        now["t"] = 20.0              # snapshot expires; every get now fails
        for _ in range(100):         # a stream hammering the cache
            now["t"] += 0.05
            await cache.get()

    asyncio.run(run())
    # 1 seeding call + at most one failed attempt per backoff window (5s of
    # simulated time / 15s backoff => 1).
    assert state["calls"] <= 3, (
        f"{state['calls']} broker calls for 101 reads — the failure path is not backing off"
    )


def test_the_backoff_window_is_honoured_then_one_retry_is_allowed():
    now = {"t": 0.0}
    fetch, state = _failing_after(1, now)
    cache = SnapshotCache(fetch=fetch, refresh_s=15.0, error_backoff_s=15.0,
                          clock=lambda: now["t"])

    async def run():
        await cache.get()
        now["t"] = 20.0
        await cache.get()            # first failure -> backoff starts
        calls_after_first_fail = state["calls"]
        now["t"] = 25.0
        await cache.get()            # inside backoff -> no new attempt
        assert state["calls"] == calls_after_first_fail
        now["t"] = 40.0
        await cache.get()            # backoff elapsed -> exactly one retry
        assert state["calls"] == calls_after_first_fail + 1

    asyncio.run(run())


def test_a_snapshot_past_max_stale_is_refused_rather_than_served_as_current():
    """A real-money screen must go blank rather than render a stale book as live."""
    now = {"t": 0.0}
    fetch, _ = _failing_after(1, now)
    cache = SnapshotCache(fetch=fetch, refresh_s=15.0, error_backoff_s=1.0,
                          max_stale_s=60.0, clock=lambda: now["t"])

    async def run():
        await cache.get()
        now["t"] = 30.0
        assert (await cache.get())["n"] == 1, "inside max_stale, last-good is fine"
        now["t"] = 120.0             # two minutes since the last good read
        with pytest.raises(StaleSnapshotError):
            await cache.get()

    asyncio.run(run())


def test_the_refusal_names_the_underlying_cause():
    """'Session Expired' has to reach the operator, not a generic staleness error."""
    now = {"t": 0.0}
    fetch, _ = _failing_after(1, now)
    cache = SnapshotCache(fetch=fetch, refresh_s=15.0, error_backoff_s=1.0,
                          max_stale_s=10.0, clock=lambda: now["t"])

    async def run():
        await cache.get()
        now["t"] = 100.0
        try:
            await cache.get()
        except StaleSnapshotError as exc:
            return str(exc)
        raise AssertionError("expected a StaleSnapshotError")

    msg = asyncio.run(run())
    assert "Session Expired" in msg, msg


def test_a_success_clears_the_backoff_and_the_stale_flag():
    now = {"t": 0.0}
    state = {"calls": 0, "fail": False}

    async def fetch():
        state["calls"] += 1
        if state["fail"]:
            raise RuntimeError("broker 502")
        return {"n": state["calls"]}

    cache = SnapshotCache(fetch=fetch, refresh_s=15.0, error_backoff_s=15.0,
                          clock=lambda: now["t"])

    async def run():
        await cache.get()
        state["fail"] = True
        now["t"] = 20.0
        await cache.get()
        assert cache.stale is True and cache.last_error is not None
        state["fail"] = False
        now["t"] = 40.0              # backoff elapsed
        out = await cache.get()
        assert cache.stale is False and cache.last_error is None
        return out

    assert asyncio.run(run())["n"] == 3


def test_a_healthy_cache_is_completely_unaffected():
    """The fix must not change the happy path: still one fetch per interval."""
    now = {"t": 0.0}
    calls = []

    async def fetch():
        calls.append(1)
        return {"n": len(calls)}

    cache = SnapshotCache(fetch=fetch, refresh_s=15.0, clock=lambda: now["t"])

    async def run():
        for _ in range(200):
            now["t"] += 0.05         # 10s of 20Hz reads
            await cache.get()

    asyncio.run(run())
    assert len(calls) == 1, f"{len(calls)} fetches for 10s of reads at a 15s interval"


def test_a_cold_cache_still_raises_the_real_error():
    async def boom():
        raise RuntimeError("no token")

    cache = SnapshotCache(fetch=boom, refresh_s=15.0, clock=lambda: 0.0)
    with pytest.raises(RuntimeError, match="no token"):
        asyncio.run(cache.get())


def test_a_cold_cache_in_backoff_refuses_rather_than_returning_an_empty_book():
    """The INVERSE failure, and the more dangerous one: a book-shaped nothing
    renders as a FLAT account, so the app would show no position while the broker
    holds one. There is no honest answer before the first successful read."""
    now = {"t": 0.0}
    calls = []

    async def always_fails():
        calls.append(1)
        raise RuntimeError("Session Expired :  Invalid Session Key")

    cache = SnapshotCache(fetch=always_fails, refresh_s=15.0, error_backoff_s=15.0,
                          clock=lambda: now["t"])

    async def run():
        with pytest.raises(RuntimeError, match="Session Expired"):
            await cache.get()          # cold + first failure -> the real error
        now["t"] = 1.0
        # Now inside the backoff window with nothing cached. Must still refuse —
        # and must NOT have re-hit the source.
        with pytest.raises(StaleSnapshotError, match="no snapshot"):
            await cache.get()
        return len(calls)

    assert asyncio.run(run()) == 1, "backoff did not suppress the retry"


def test_the_refusal_is_never_a_falsy_book():
    """Belt and braces: no code path may return something a caller could iterate
    as an empty position book."""
    now = {"t": 0.0}

    async def always_fails():
        raise RuntimeError("broker down")

    cache = SnapshotCache(fetch=always_fails, refresh_s=15.0, error_backoff_s=15.0,
                          clock=lambda: now["t"])

    async def run():
        for t in (0.0, 1.0, 5.0, 20.0, 100.0):
            now["t"] = t
            try:
                got = await cache.get()
            except (RuntimeError, StaleSnapshotError):
                continue
            raise AssertionError(f"returned {got!r} at t={t} instead of refusing")

    asyncio.run(run())
