"""Tests for live_position_guard — software SL/TP/trailing on LIVE positions
(the margin-free replacement for the always-rejected resting broker SL).
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.live.live_position_guard import (  # noqa: E402
    LiveMonitorRegistry,
    LivePositionGuard,
)
from app.live.live_sl_monitor import build_monitor_state  # noqa: E402


def run(coro):
    return asyncio.run(coro)


_TSYM = "SENSEX26JUN76500CE"


class _FakeClient:
    """position_book returns the injected snapshot (list of position dicts)."""

    def __init__(self, positions):
        self._positions = positions

    def set(self, positions):
        self._positions = positions

    async def position_book(self):
        return list(self._positions)


class _Recorder:
    def __init__(self):
        self.squared = []

    async def square_fn(self, client, position, *, reason):
        self.squared.append((position["tsym"], reason, position.get("netqty"), position.get("lp")))
        return {"squared": True, "reason": reason}


def _guard(registry, client, rec):
    return LivePositionGuard(
        registry=registry,
        client_factory=lambda: _aw(client),
        square_fn=rec.square_fn,
    )


async def _aw(v):
    return v


def _pos(netqty=20, lp=250.0, tsym=_TSYM):
    return {"tsym": tsym, "exch": "BFO", "netqty": str(netqty), "lp": str(lp), "urmtom": "0"}


def _registered(registry, *, entry=250.0, stop_pct=30, target_pct=None, trail=None):
    state = build_monitor_state(entry, stop_pct=stop_pct, target_pct=target_pct, trail=trail)
    registry.register(key="ORD1", tsym=_TSYM, exch="BFO", qty=20, prd="I",
                      entry_price=entry, state=state)
    return registry


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------
class TestRegistry:
    def test_register_and_snapshot(self):
        r = LiveMonitorRegistry()
        _registered(r)
        assert len(r) == 1
        snap = r.snapshot()
        assert snap[0]["tsym"] == _TSYM
        assert snap[0]["position"]["netqty"] == 20

    def test_remove_and_clear(self):
        r = LiveMonitorRegistry()
        _registered(r)
        r.remove("ORD1")
        assert len(r) == 0
        _registered(r)
        r.clear()
        assert len(r) == 0

    def test_snapshot_is_live_not_copy(self):
        # the guard mutates entry["state"] in place — registry must hold the SAME dict
        r = LiveMonitorRegistry()
        _registered(r)
        r.snapshot()[0]["state"]["peak"] = 999
        assert r.get("ORD1")["state"]["peak"] == 999


# ---------------------------------------------------------------------------
# Guard cycle
# ---------------------------------------------------------------------------
class TestGuardCycle:
    def test_empty_registry_no_client_call(self):
        r = LiveMonitorRegistry()
        rec = _Recorder()
        # client_factory that would explode if called
        def boom():
            raise AssertionError("client should not be built for empty registry")
        g = LivePositionGuard(registry=r, client_factory=boom, square_fn=rec.square_fn)
        exits = run(g._cycle())
        assert exits == []
        assert rec.squared == []

    def test_no_exit_when_above_stop(self):
        r = LiveMonitorRegistry()
        _registered(r, entry=250.0, stop_pct=30)  # stop = 175
        client = _FakeClient([_pos(netqty=20, lp=240.0)])
        rec = _Recorder()
        run(_guard(r, client, rec)._cycle())
        assert rec.squared == []
        assert len(r) == 1  # still guarded
        # peak updated
        assert r.get("ORD1")["state"]["peak"] == 250.0  # entry; 240<250

    def test_stop_breach_squares_once_and_removes(self):
        r = LiveMonitorRegistry()
        _registered(r, entry=250.0, stop_pct=30)  # stop = 175
        client = _FakeClient([_pos(netqty=20, lp=170.0)])  # below stop
        rec = _Recorder()
        exits = run(_guard(r, client, rec)._cycle())
        assert len(rec.squared) == 1
        tsym, reason, netqty, lp = rec.squared[0]
        assert tsym == _TSYM
        assert reason == "software_stop"
        assert netqty == 20
        assert lp == 170.0
        assert len(r) == 0          # removed (no re-guard / double-square)
        assert len(exits) == 1

    def test_target_breach(self):
        r = LiveMonitorRegistry()
        _registered(r, entry=250.0, stop_pct=30, target_pct=20)  # target = 300
        client = _FakeClient([_pos(netqty=20, lp=305.0)])
        rec = _Recorder()
        run(_guard(r, client, rec)._cycle())
        assert rec.squared[0][1] == "software_target"

    def test_filled_then_flat_removed_no_square(self):
        # cycle 1: filled (netqty 20) → seen_filled; cycle 2: flat → removed (closed elsewhere)
        r = LiveMonitorRegistry()
        _registered(r, entry=250.0, stop_pct=30)
        client = _FakeClient([_pos(netqty=20, lp=240.0)])
        rec = _Recorder()
        g = _guard(r, client, rec)
        run(g._cycle())
        assert len(r) == 1            # still guarded (filled, above stop)
        client.set([_pos(netqty=0, lp=240.0)])  # now flat
        run(g._cycle())
        assert rec.squared == []
        assert len(r) == 0            # filled-then-flat → dropped

    def test_pending_fill_not_dropped_during_grace(self):
        # a just-armed position not yet in the book must NOT be dropped (async fill)
        r = LiveMonitorRegistry()
        _registered(r)
        client = _FakeClient([])  # not filled yet
        rec = _Recorder()
        g = _guard(r, client, rec)
        run(g._cycle())
        assert rec.squared == []
        assert len(r) == 1            # kept — still pending its fill
        assert r.get("ORD1")["misses"] == 1

    def test_never_filled_dropped_after_grace(self):
        # a rejected/never-filling entry is cleaned up after the grace window
        r = LiveMonitorRegistry()
        _registered(r)
        client = _FakeClient([])
        rec = _Recorder()
        g = LivePositionGuard(registry=r, client_factory=lambda: _aw(client),
                              square_fn=rec.square_fn, max_pending_misses=3)
        for _ in range(3):
            run(g._cycle())
        assert rec.squared == []
        assert len(r) == 0            # dropped after 3 misses

    def test_stale_lp_never_squares(self):
        r = LiveMonitorRegistry()
        _registered(r, entry=250.0, stop_pct=30)
        for bad in [None, "", "nan", "0", "-5"]:
            r.clear(); _registered(r, entry=250.0, stop_pct=30)
            client = _FakeClient([{"tsym": _TSYM, "exch": "BFO", "netqty": "20", "lp": bad}])
            rec = _Recorder()
            run(_guard(r, client, rec)._cycle())
            assert rec.squared == [], f"stale lp {bad!r} must not square"
            assert len(r) == 1

    def test_trailing_state_persists_across_cycles(self):
        r = LiveMonitorRegistry()
        _registered(r, entry=250.0, stop_pct=30, trail={"mode": "trail", "gap": 20})
        rec = _Recorder()
        client = _FakeClient([_pos(netqty=20, lp=300.0)])
        g = _guard(r, client, rec)
        run(g._cycle())   # peak 300 -> trail stop = 280
        assert r.get("ORD1")["state"]["stop_level"] == 280.0
        # price falls to 285 (above trailed stop) -> no exit, stop stays 280
        client.set([_pos(netqty=20, lp=285.0)])
        run(g._cycle())
        assert r.get("ORD1")["state"]["stop_level"] == 280.0
        assert rec.squared == []
        # falls to 279 -> trailing stop breached
        client.set([_pos(netqty=20, lp=279.0)])
        run(g._cycle())
        assert rec.squared[0][1] == "software_trailing_stop"

    def test_cycle_never_raises_on_book_error(self):
        r = LiveMonitorRegistry()
        _registered(r)

        class _BoomClient:
            async def position_book(self):
                raise RuntimeError("broker down")

        rec = _Recorder()
        g = LivePositionGuard(registry=r, client_factory=lambda: _aw(_BoomClient()),
                              square_fn=rec.square_fn)
        exits = run(g._cycle())  # must not raise
        assert exits == []
        assert rec.squared == []
        assert len(r) == 1  # left intact for the next cycle

    def test_no_client_skips(self):
        r = LiveMonitorRegistry()
        _registered(r)
        rec = _Recorder()
        g = LivePositionGuard(registry=r, client_factory=lambda: _aw(None),
                              square_fn=rec.square_fn)
        run(g._cycle())
        assert rec.squared == []
        assert len(r) == 1

    def test_square_failure_does_not_kill_loop(self):
        r = LiveMonitorRegistry()
        _registered(r, entry=250.0, stop_pct=30)
        client = _FakeClient([_pos(netqty=20, lp=170.0)])

        class _BadRec:
            squared = []
            async def square_fn(self, client, position, *, reason):
                raise RuntimeError("square exploded")

        bad = _BadRec()
        g = LivePositionGuard(registry=r, client_factory=lambda: _aw(client), square_fn=bad.square_fn)
        exits = run(g._cycle())  # must not raise
        assert len(r) == 0          # still removed (no re-square attempt next cycle)
        assert exits[0]["result"]["squared"] is False
