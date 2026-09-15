"""The live guard's stop decision, on the tick instead of the broker's REST mark.

Before this, `live_position_guard` slept a flat 1.5s and then evaluated the stop
against `pos["lp"]` from the position book — a price that is itself up to 1.5s old.
Worst case a real-money stop fired ~3s after the premium actually breached it.

Now the FRESH TICK premium is the primary input and the broker `lp` is the
fallback. The broker read stays on its own 1.5s cadence, so this costs ZERO extra
broker calls on a rate budget shared with the Flattrade MCP.

These tests pin the safety properties, not just the speed:
  * with no tick source, or a stale tick, behaviour is exactly what it was
  * the fast pass never reads the broker book
  * the fast pass cannot double-square, or square outside market hours, or
    square off a book snapshot that has gone stale
"""
from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.live.live_position_guard import (  # noqa: E402
    LiveMonitorRegistry,
    LivePositionGuard,
    premium_tick_key,
)
from app.live.live_sl_monitor import build_monitor_state  # noqa: E402

_NOW = datetime(2026, 6, 25, 6, 0, tzinfo=timezone.utc)  # 11:30 IST — mid-session
_NOW_MS = int(_NOW.timestamp() * 1000)
_TSYM = "NIFTY15SEP26C23350"
_TOKEN = "47291"
_KEY = "NSE_FO|47291"


def run(coro):
    return asyncio.run(coro)


async def _aw(v):
    return v


class _FakeClient:
    def __init__(self, positions):
        self._positions = positions
        self.book_reads = 0

    def set(self, positions):
        self._positions = positions

    async def position_book(self):
        self.book_reads += 1
        return list(self._positions)


class _Recorder:
    def __init__(self):
        self.squared = []

    async def square_fn(self, client, position, *, reason):
        self.squared.append((position["tsym"], reason, position.get("lp")))
        return {"squared": True, "reason": reason}


def _pos(netqty=65, lp=250.0):
    return {"tsym": _TSYM, "exch": "NFO", "token": _TOKEN,
            "netqty": str(netqty), "lp": str(lp), "urmtom": "0"}


def _tick(price, *, age_ms=0):
    return {"instrument_key": _KEY, "last_price": price,
            "ingest_ts": _NOW_MS - age_ms, "ts": _NOW_MS - age_ms}


def _registry(entry=250.0, stop_pct=30):
    r = LiveMonitorRegistry()
    r.register(key="ORD1", tsym=_TSYM, exch="NFO", qty=65, prd="I",
               entry_price=entry, state=build_monitor_state(entry, stop_pct=stop_pct),
               token=_TOKEN)
    return r


def _guard(registry, client, rec, *, ticks=None, **kw):
    return LivePositionGuard(
        registry=registry,
        client_factory=lambda: _aw(client),
        square_fn=rec.square_fn,
        now_fn=lambda: _NOW,
        premium_tick_fn=(lambda: ticks) if ticks is not None else None,
        **kw,
    )


# ---------------------------------------------------------------------------
# The join: broker position row -> Upstox tick key
# ---------------------------------------------------------------------------
class TestPremiumTickKey:
    def test_maps_noren_exchange_to_upstox_segment(self):
        assert premium_tick_key("NFO", "47291") == "NSE_FO|47291"
        assert premium_tick_key("BFO", "830015") == "BSE_FO|830015"

    def test_unknown_exchange_or_missing_token_is_none(self):
        """No guess. A wrong key would mark a position against another contract."""
        assert premium_tick_key("XXX", "47291") is None
        assert premium_tick_key("NFO", None) is None
        assert premium_tick_key("NFO", "") is None
        assert premium_tick_key(None, "47291") is None


# ---------------------------------------------------------------------------
# Tick-primary stop decision
# ---------------------------------------------------------------------------
class TestTickPrimary:
    def test_fresh_tick_below_the_stop_fires_even_though_broker_lp_has_not_caught_up(self):
        """The whole point: the broker's 1.5s-old mark says 250, the tick says the
        premium already fell through the 175 stop."""
        r, c, rec = _registry(), _FakeClient([_pos(lp=250.0)]), _Recorder()
        g = _guard(r, c, rec, ticks={_KEY: _tick(170.0)})
        run(g._cycle())
        assert len(rec.squared) == 1, "a breached tick premium did not square"
        assert rec.squared[0][1] == "software_stop"

    def test_a_stale_tick_falls_back_to_the_broker_mark(self):
        """A tick older than the freshness bound is treated as absent — identical
        behaviour to before this feature existed."""
        r, c, rec = _registry(), _FakeClient([_pos(lp=250.0)]), _Recorder()
        g = _guard(r, c, rec, ticks={_KEY: _tick(170.0, age_ms=30_000)})
        run(g._cycle())
        assert rec.squared == [], "squared on a stale tick"

    def test_no_tick_source_is_the_old_behaviour_exactly(self):
        r, c, rec = _registry(), _FakeClient([_pos(lp=170.0)]), _Recorder()
        g = _guard(r, c, rec)  # premium_tick_fn=None
        run(g._cycle())
        assert len(rec.squared) == 1
        assert rec.squared[0][2] == 170.0  # decided on the broker lp

    def test_a_tick_for_a_different_contract_is_ignored(self):
        r, c, rec = _registry(), _FakeClient([_pos(lp=250.0)]), _Recorder()
        g = _guard(r, c, rec, ticks={"NSE_FO|99999": _tick(1.0)})
        run(g._cycle())
        assert rec.squared == []

    def test_a_nonsense_tick_price_falls_back_rather_than_squaring(self):
        for bad in (0, -5, float("nan"), float("inf"), "abc", None):
            r, c, rec = _registry(), _FakeClient([_pos(lp=250.0)]), _Recorder()
            g = _guard(r, c, rec, ticks={_KEY: _tick(bad)})
            run(g._cycle())
            assert rec.squared == [], f"squared on a {bad!r} tick price"

    def test_a_tick_source_that_raises_never_breaks_the_cycle(self):
        def boom():
            raise RuntimeError("tick map exploded")

        r, c, rec = _registry(), _FakeClient([_pos(lp=170.0)]), _Recorder()
        g = LivePositionGuard(registry=r, client_factory=lambda: _aw(c),
                              square_fn=rec.square_fn, now_fn=lambda: _NOW,
                              premium_tick_fn=boom)
        run(g._cycle())
        assert len(rec.squared) == 1, "a broken tick source must degrade to broker lp"

    def test_the_premium_source_is_recorded_for_audit(self):
        r, c, rec = _registry(), _FakeClient([_pos(lp=250.0)]), _Recorder()
        g = _guard(r, c, rec, ticks={_KEY: _tick(240.0)})
        run(g._cycle())
        entry = r.get("ORD1")
        assert entry["position"]["premium_source"] == "tick"
        assert entry["position"]["lp"] == 240.0
        assert entry["position"]["broker_lp"] == 250.0


# ---------------------------------------------------------------------------
# The fast pass — speed without broker cost
# ---------------------------------------------------------------------------
class TestFastPass:
    def test_it_never_reads_the_broker_book(self):
        """This is the constraint that makes the whole thing permissible: the
        shared Flattrade rate budget must not grow."""
        r, c, rec = _registry(), _FakeClient([_pos(lp=250.0)]), _Recorder()
        ticks = {_KEY: _tick(250.0)}
        g = _guard(r, c, rec, ticks=ticks)
        run(g._cycle())                     # one slow cycle to seed the book cache
        reads_after_slow = c.book_reads
        ticks[_KEY] = _tick(170.0)          # premium craters between broker reads
        run(g._fast_premium_pass(_NOW))
        assert c.book_reads == reads_after_slow, "the fast pass hit the broker"
        assert len(rec.squared) == 1, "the fast pass did not act on the breach"

    def test_it_does_nothing_before_a_book_snapshot_exists(self):
        """On boot there is no cached book — netqty is unknown, so squaring would
        be sizing off nothing."""
        r, c, rec = _registry(), _FakeClient([_pos(lp=250.0)]), _Recorder()
        g = _guard(r, c, rec, ticks={_KEY: _tick(1.0)})
        run(g._fast_premium_pass(_NOW))
        assert rec.squared == []
        assert c.book_reads == 0

    def test_it_stands_down_when_the_cached_book_has_gone_stale(self):
        """If the slow cycle stopped refreshing (broker down), the cached netqty is
        no longer trustworthy — hold and let the slow path recover."""
        r, c, rec = _registry(), _FakeClient([_pos(lp=250.0)]), _Recorder()
        ticks = {_KEY: _tick(250.0)}
        g = _guard(r, c, rec, ticks=ticks)
        run(g._cycle())
        g._book_at = _NOW.timestamp() - 60.0   # snapshot is a minute old
        ticks[_KEY] = _tick(1.0)
        run(g._fast_premium_pass(_NOW))
        assert rec.squared == [], "squared off a stale book snapshot"

    def test_it_never_double_squares_an_entry_already_squaring(self):
        r, c, rec = _registry(), _FakeClient([_pos(lp=250.0)]), _Recorder()
        ticks = {_KEY: _tick(250.0)}
        g = _guard(r, c, rec, ticks=ticks)
        run(g._cycle())
        r.get("ORD1")["squaring"] = True
        ticks[_KEY] = _tick(1.0)
        run(g._fast_premium_pass(_NOW))
        assert rec.squared == [], "re-issued a square for an entry already squaring"

    def test_it_respects_square_stopped_and_dry_run_flags(self):
        for flag in ("square_stopped", "dry_run_exit_logged"):
            r, c, rec = _registry(), _FakeClient([_pos(lp=250.0)]), _Recorder()
            ticks = {_KEY: _tick(250.0)}
            g = _guard(r, c, rec, ticks=ticks)
            run(g._cycle())
            r.get("ORD1")[flag] = True
            ticks[_KEY] = _tick(1.0)
            run(g._fast_premium_pass(_NOW))
            assert rec.squared == [], f"fast pass ignored {flag}"

    def test_it_does_not_touch_flat_detection_counters(self):
        """Finalizing a position is irreversible and belongs to the slow path,
        which has a fresh authenticated book."""
        r, c, rec = _registry(), _FakeClient([_pos(lp=250.0)]), _Recorder()
        ticks = {_KEY: _tick(250.0)}
        g = _guard(r, c, rec, ticks=ticks)
        run(g._cycle())
        entry = r.get("ORD1")
        before = (entry.get("misses"), entry.get("flat_reads"), entry.get("seen_filled"))
        ticks[_KEY] = _tick(249.0)
        run(g._fast_premium_pass(_NOW))
        assert (entry.get("misses"), entry.get("flat_reads"), entry.get("seen_filled")) == before


# ---------------------------------------------------------------------------
# The loop
# ---------------------------------------------------------------------------
class _Stream:
    def __init__(self):
        self.queue: asyncio.Queue = asyncio.Queue(maxsize=256)
        self.unsubscribed = 0

    def subscribe(self, **_kw):
        return self.queue

    def unsubscribe(self, _q):
        self.unsubscribed += 1


class TestLoop:
    def test_a_tick_storm_does_not_increase_broker_reads(self):
        """200 ticks/s must not become 200 position_book calls/s."""
        stream = _Stream()
        r, c, rec = _registry(), _FakeClient([_pos(lp=250.0)]), _Recorder()
        g = _guard(r, c, rec, ticks={_KEY: _tick(250.0)},
                   poll_seconds=0.3, tick_floor_seconds=0.02,
                   subscribe=stream.subscribe, unsubscribe=stream.unsubscribe,
                   in_market_hours=lambda: True)

        async def drive():
            task = asyncio.create_task(g._run())
            deadline = asyncio.get_event_loop().time() + 0.6
            while asyncio.get_event_loop().time() < deadline:
                stream.queue.put_nowait({"instrument_key": _KEY})
                await asyncio.sleep(0.003)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        run(drive())
        # 0.6s at a 0.3s broker cadence => ~2-3 reads. Anything near the tick rate
        # (200/s) would be a rate-budget blowout.
        assert c.book_reads <= 5, f"{c.book_reads} broker reads under a tick storm"

    def test_without_a_stream_it_is_the_old_fixed_interval_loop(self):
        r, c, rec = _registry(), _FakeClient([_pos(lp=250.0)]), _Recorder()
        g = _guard(r, c, rec, poll_seconds=0.05, in_market_hours=lambda: True)

        async def drive():
            task = asyncio.create_task(g._run())
            await asyncio.sleep(0.22)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        run(drive())
        assert c.book_reads >= 3, f"fixed-interval loop made {c.book_reads} reads"

    def test_outside_market_hours_nothing_runs(self):
        stream = _Stream()
        r, c, rec = _registry(), _FakeClient([_pos(lp=1.0)]), _Recorder()
        g = _guard(r, c, rec, ticks={_KEY: _tick(1.0)},
                   poll_seconds=0.02, tick_floor_seconds=0.0,
                   subscribe=stream.subscribe, unsubscribe=stream.unsubscribe,
                   in_market_hours=lambda: False)

        async def drive():
            task = asyncio.create_task(g._run())
            for _ in range(20):
                stream.queue.put_nowait({"instrument_key": _KEY})
            await asyncio.sleep(0.15)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        run(drive())
        assert rec.squared == [], "squared outside market hours"
        assert c.book_reads == 0

    def test_the_tick_queue_is_released_on_stop(self):
        stream = _Stream()
        r, c, rec = _registry(), _FakeClient([_pos(lp=250.0)]), _Recorder()
        g = _guard(r, c, rec, ticks={_KEY: _tick(250.0)}, poll_seconds=0.05,
                   subscribe=stream.subscribe, unsubscribe=stream.unsubscribe,
                   in_market_hours=lambda: True)

        async def drive():
            await g.start()
            await asyncio.sleep(0.06)
            await g.stop()

        run(drive())
        assert stream.unsubscribed == 1, "the guard leaked its tick queue"
