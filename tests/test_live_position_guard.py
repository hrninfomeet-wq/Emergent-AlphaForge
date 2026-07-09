"""Tests for live_position_guard — software SL/TP/trailing on LIVE positions
(the margin-free replacement for the always-rejected resting broker SL).
"""
from __future__ import annotations

import asyncio
import sys
from datetime import datetime, time as dtime, timedelta, timezone
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
        # Pin a pre-EOD clock (11:30 IST) so the 15:00 EOD square never fires in
        # tests that only exercise the per-position paths. (Manual positions are no
        # longer EOD-exempt, so an unpinned wall-clock past 15:00 IST would square
        # them and break unrelated assertions.) `_NOW` is defined later in the
        # module; late binding resolves it at call time.
        now_fn=lambda: _NOW,
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

    def test_register_carries_oco_al_id_and_token(self):
        # the resting OCO's alert id lives on the entry; the contract token lives
        # on the nested position dict (so a later task can fetch fresh quotes).
        r = LiveMonitorRegistry()
        state = build_monitor_state(250.0, stop_pct=30)
        r.register(key="ORD1", tsym=_TSYM, exch="BFO", qty=20, prd="I",
                   entry_price=250.0, state=state, oco_al_id="AL1", token="999")
        assert r.get("ORD1")["oco_al_id"] == "AL1"
        assert r.get("ORD1")["position"]["token"] == "999"

    def test_register_oco_al_id_and_token_default_none(self):
        # callers that omit the new kwargs (manual single-shot + rehydrate) get None
        r = LiveMonitorRegistry()
        _registered(r)
        entry = r.get("ORD1")
        assert entry["oco_al_id"] is None
        assert entry["position"]["token"] is None


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

    def test_stop_breach_issues_square_and_keeps_until_flat(self):
        r = LiveMonitorRegistry()
        _registered(r, entry=250.0, stop_pct=30)  # stop = 175
        client = _FakeClient([_pos(netqty=20, lp=170.0)])  # below stop
        rec = _Recorder()
        g = _guard(r, client, rec)
        exits = run(g._cycle())
        assert len(rec.squared) == 1
        tsym, reason, netqty, lp = rec.squared[0]
        assert tsym == _TSYM
        assert reason == "software_stop"
        assert netqty == 20
        assert lp == 170.0
        # Place-accept is not a fill: the entry is KEPT (squaring) — not removed —
        # so the position stays watched and protected until the broker confirms flat.
        assert len(r) == 1
        assert r.get("ORD1")["squaring"] is True
        assert len(exits) == 1
        # A `squaring` entry is not re-squared while it stays open.
        run(g._cycle())
        assert len(rec.squared) == 1
        # Broker confirms flat → entry dropped.
        client.set([_pos(netqty=0, lp=170.0)])
        run(g._cycle())
        assert len(r) == 0

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
                              square_fn=rec.square_fn, max_pending_misses=3,
                              now_fn=lambda: _NOW)  # pre-EOD: isolate the grace-window path
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
        g = LivePositionGuard(registry=r, client_factory=lambda: _aw(client),
                              square_fn=bad.square_fn, now_fn=lambda: _NOW)
        exits = run(g._cycle())  # must not raise
        # A failed square leaves squaring False, so the entry is KEPT (not orphaned)
        # and the next cycle retries — the position is never dropped un-squared.
        assert len(r) == 1
        assert r.get("ORD1")["squaring"] is False
        assert exits[0]["result"]["squared"] is False


# ---------------------------------------------------------------------------
# Basket-level overall controls (overall SL / target / trailing → square ALL)
# ---------------------------------------------------------------------------
class TestOverallBasket:
    def _mk(self, overall_cfg):
        r = LiveMonitorRegistry()
        for i, ts in enumerate(["AAA", "BBB"]):
            st = build_monitor_state(250.0, stop_pct=90)  # wide per-position stop
            r.register(key=f"P{i}", tsym=ts, exch="BFO", qty=20, prd="I",
                       entry_price=250.0, state=st)
        rec = _Recorder()
        cl = _FakeClient([])

        async def prov(_c=overall_cfg):
            return _c

        g = LivePositionGuard(registry=r, client_factory=lambda: _aw(cl),
                              square_fn=rec.square_fn, overall_provider=prov,
                              now_fn=lambda: _NOW)  # pre-EOD: isolate the basket path
        return r, cl, rec, g

    def _pos(self, ts, urmtom, lp=235):
        return {"tsym": ts, "exch": "BFO", "netqty": "20", "lp": str(lp), "urmtom": str(urmtom)}

    def test_overall_sl_squares_whole_basket(self):
        cfg = {"sl": {"enabled": True, "mode": "mtm", "value": 500},
               "target": {"enabled": False, "mode": "mtm", "value": 0},
               "trailing": {"mode": "none"}, "reentry": {"enabled": False}}
        r, cl, rec, g = self._mk(cfg)
        cl.set([self._pos("AAA", -300), self._pos("BBB", -300)])  # basket -600 <= -500
        run(g._cycle())
        assert len(rec.squared) == 2
        assert all(s[1] == "software_overall_sl" for s in rec.squared)
        # Squares issued for both legs; entries KEPT (squaring) until confirmed flat.
        assert len(r) == 2
        assert all(r.get(f"P{i}")["squaring"] is True for i in (0, 1))

    def test_overall_target_squares_basket(self):
        cfg = {"sl": {"enabled": False, "mode": "mtm", "value": 0},
               "target": {"enabled": True, "mode": "mtm", "value": 1000},
               "trailing": {"mode": "none"}, "reentry": {"enabled": False}}
        r, cl, rec, g = self._mk(cfg)
        cl.set([self._pos("AAA", 600, lp=280), self._pos("BBB", 600, lp=280)])  # +1200 >= 1000
        run(g._cycle())
        assert len(rec.squared) == 2
        assert all(s[1] == "software_overall_target" for s in rec.squared)

    def test_within_overall_no_square(self):
        cfg = {"sl": {"enabled": True, "mode": "mtm", "value": 500},
               "target": {"enabled": False, "mode": "mtm", "value": 0},
               "trailing": {"mode": "none"}, "reentry": {"enabled": False}}
        r, cl, rec, g = self._mk(cfg)
        cl.set([self._pos("AAA", -100, lp=245), self._pos("BBB", -100, lp=245)])  # -200 > -500
        run(g._cycle())
        assert rec.squared == []
        assert len(r) == 2

    def test_disabled_overall_is_noop(self):
        cfg = {"sl": {"enabled": False, "mode": "mtm", "value": 0},
               "target": {"enabled": False, "mode": "mtm", "value": 0},
               "trailing": {"mode": "none"}, "reentry": {"enabled": False}}  # nothing enabled
        r, cl, rec, g = self._mk(cfg)
        cl.set([self._pos("AAA", -5000, lp=100), self._pos("BBB", -5000, lp=100)])
        run(g._cycle())
        assert rec.squared == []  # no overall controls configured → no basket square
        assert len(r) == 2

    def test_overall_trailing_persists_then_fires(self):
        cfg = {"sl": {"enabled": False, "mode": "mtm", "value": 0},
               "target": {"enabled": False, "mode": "mtm", "value": 0},
               "trailing": {"mode": "lock", "unit": "mtm", "lock_at": 1000, "lock_floor": 600,
                            "trail_per": 0, "trail_by": 0, "base_sl": 0},
               "reentry": {"enabled": False}}
        r, cl, rec, g = self._mk(cfg)
        # basket reaches +1200 → lock floor at 600
        cl.set([self._pos("AAA", 600, lp=280), self._pos("BBB", 600, lp=280)])
        run(g._cycle())
        assert rec.squared == []
        assert g._overall_state["floor"] == 600.0
        # basket falls to +500 (< floor 600) → overall trailing exit, square all
        cl.set([self._pos("AAA", 250, lp=255), self._pos("BBB", 250, lp=255)])
        run(g._cycle())
        assert len(rec.squared) == 2
        assert all(s[1] == "software_overall_trailing" for s in rec.squared)


# ---------------------------------------------------------------------------
# Extended register: spot_exit / time_stop / source / deployment_id
# ---------------------------------------------------------------------------
_IDX_KEY = "BSE_INDEX|SENSEX"


class TestRegisterExtended:
    def test_register_carries_spot_exit_time_stop_source(self):
        r = LiveMonitorRegistry()
        item = r.register(
            key="N1", tsym="X", exch="NFO", qty=75, prd="I", entry_price=100.0,
            state={"stop_level": 50.0},
            spot_exit={"direction": "CE", "spot_target": 25100, "spot_stop": 24900,
                       "instrument_key": _IDX_KEY},
            time_stop_minutes=30, entry_ts="2026-06-25T05:00:00+00:00",
            source="auto_live", deployment_id="dep1",
        )
        assert item["spot_exit"]["direction"] == "CE"
        assert item["spot_exit"]["instrument_key"] == _IDX_KEY
        assert item["time_stop_minutes"] == 30
        assert item["entry_ts"] == "2026-06-25T05:00:00+00:00"
        assert item["source"] == "auto_live"
        assert item["deployment_id"] == "dep1"

    def test_register_defaults_preserve_manual(self):
        r = LiveMonitorRegistry()
        item = r.register(key="M1", tsym="X", exch="NFO", qty=75, prd="I",
                          entry_price=100.0, state={})
        assert item["source"] == "manual"
        assert item["spot_exit"] is None
        assert item["time_stop_minutes"] is None
        assert item["entry_ts"] is None
        assert item["deployment_id"] is None


# ---------------------------------------------------------------------------
# Spot-mirror + time-stop + 15:00 IST EOD square (deployed positions)
# ---------------------------------------------------------------------------
# A forced "now": 2026-06-25 06:00 UTC == 11:30 IST (well inside market hours,
# before the 15:00 IST EOD cutoff). entry_ts/spot ticks are anchored to it.
_NOW = datetime(2026, 6, 25, 6, 0, tzinfo=timezone.utc)
_NOW_MS = int(_NOW.timestamp() * 1000)
# 15:00 IST == 09:30 UTC on the same date.
_EOD_NOW = datetime(2026, 6, 25, 9, 30, tzinfo=timezone.utc)


def _spot_tick(price, *, age_sec=0):
    """A spot tick map entry: last_price + a ts age_sec seconds before _NOW."""
    return {"last_price": str(price), "ts": _NOW_MS - int(age_sec) * 1000}


class TestSpotMirrorAndTimeStop:
    def _deployed(self, *, spot_exit=None, time_stop_minutes=None,
                  entry_ts=None, stop_pct=90, entry=250.0):
        """A deployed (source='auto_live') registry holding one position with a
        WIDE premium stop (so premium evaluate_exit never fires first)."""
        r = LiveMonitorRegistry()
        state = build_monitor_state(entry, stop_pct=stop_pct)
        r.register(key="ORD1", tsym=_TSYM, exch="BFO", qty=20, prd="I",
                   entry_price=entry, state=state,
                   spot_exit=spot_exit, time_stop_minutes=time_stop_minutes,
                   entry_ts=entry_ts, source="auto_live", deployment_id="dep1")
        return r

    def _guard(self, r, client, rec, *, spot_map=None, now=_NOW):
        return LivePositionGuard(
            registry=r,
            client_factory=lambda: _aw(client),
            square_fn=rec.square_fn,
            spot_tick_fn=(lambda: spot_map) if spot_map is not None else None,
            now_fn=lambda: now,
        )

    def test_spot_mirror_target_squares_and_keeps_until_flat(self):
        spot_exit = {"direction": "CE", "instrument_key": _IDX_KEY,
                     "spot_target": 75600.0, "spot_stop": 75000.0}
        r = self._deployed(spot_exit=spot_exit)
        client = _FakeClient([_pos(netqty=20, lp=260.0)])  # premium above stop -> held
        rec = _Recorder()
        spot_map = {_IDX_KEY: _spot_tick(75650.0)}  # >= target
        g = self._guard(r, client, rec, spot_map=spot_map)
        run(g._cycle())
        assert len(rec.squared) == 1
        assert rec.squared[0][1] == "software_spot_target_hit"
        assert len(r) == 1  # kept (squaring) until the broker confirms flat
        assert r.get("ORD1")["squaring"] is True

    def test_spot_mirror_stop_first(self):
        spot_exit = {"direction": "CE", "instrument_key": _IDX_KEY,
                     "spot_target": 75600.0, "spot_stop": 75000.0}
        r = self._deployed(spot_exit=spot_exit)
        client = _FakeClient([_pos(netqty=20, lp=260.0)])
        rec = _Recorder()
        spot_map = {_IDX_KEY: _spot_tick(74900.0)}  # <= stop
        g = self._guard(r, client, rec, spot_map=spot_map)
        run(g._cycle())
        assert len(rec.squared) == 1
        assert rec.squared[0][1] == "software_spot_stop_hit"

    def test_time_stop_elapsed_squares(self):
        # entry 31 minutes before _NOW; time_stop 30 -> elapsed -> square
        entry_ts = "2026-06-25T05:29:00+00:00"  # 31 min before 06:00 UTC
        r = self._deployed(time_stop_minutes=30, entry_ts=entry_ts)
        client = _FakeClient([_pos(netqty=20, lp=260.0)])  # premium held
        rec = _Recorder()
        g = self._guard(r, client, rec)
        run(g._cycle())
        assert len(rec.squared) == 1
        assert rec.squared[0][1] == "software_time_stop"
        assert len(r) == 1  # kept (squaring) until the broker confirms flat
        assert r.get("ORD1")["squaring"] is True

    def test_time_stop_not_elapsed_held(self):
        entry_ts = "2026-06-25T05:45:00+00:00"  # 15 min before _NOW
        r = self._deployed(time_stop_minutes=30, entry_ts=entry_ts)
        client = _FakeClient([_pos(netqty=20, lp=260.0)])
        rec = _Recorder()
        g = self._guard(r, client, rec)
        run(g._cycle())
        assert rec.squared == []
        assert len(r) == 1

    def test_stale_spot_tick_no_square(self):
        spot_exit = {"direction": "CE", "instrument_key": _IDX_KEY,
                     "spot_target": 75600.0, "spot_stop": 75000.0}
        r = self._deployed(spot_exit=spot_exit)
        client = _FakeClient([_pos(netqty=20, lp=260.0)])
        rec = _Recorder()
        # tick says target hit, but it is 5 minutes old (> 120s freshness bound)
        spot_map = {_IDX_KEY: _spot_tick(75650.0, age_sec=300)}
        g = self._guard(r, client, rec, spot_map=spot_map)
        run(g._cycle())
        assert rec.squared == []
        assert len(r) == 1

    def test_absent_spot_tick_no_square(self):
        spot_exit = {"direction": "CE", "instrument_key": _IDX_KEY,
                     "spot_target": 75600.0, "spot_stop": 75000.0}
        r = self._deployed(spot_exit=spot_exit)
        client = _FakeClient([_pos(netqty=20, lp=260.0)])
        rec = _Recorder()
        spot_map = {}  # no tick for the index key
        g = self._guard(r, client, rec, spot_map=spot_map)
        run(g._cycle())
        assert rec.squared == []
        assert len(r) == 1

    def test_no_spot_tick_fn_skips_spot_mirror(self):
        spot_exit = {"direction": "CE", "instrument_key": _IDX_KEY,
                     "spot_target": 75600.0, "spot_stop": 75000.0}
        r = self._deployed(spot_exit=spot_exit)
        client = _FakeClient([_pos(netqty=20, lp=260.0)])
        rec = _Recorder()
        g = self._guard(r, client, rec, spot_map=None)  # default None
        run(g._cycle())
        assert rec.squared == []
        assert len(r) == 1

    def test_manual_entry_untouched_by_spot_time_logic(self):
        # source="manual", no spot_exit, no time_stop -> only premium guard applies
        r = LiveMonitorRegistry()
        _registered(r, entry=250.0, stop_pct=30)  # manual default register; stop=175
        client = _FakeClient([_pos(netqty=20, lp=240.0)])  # above stop -> held
        rec = _Recorder()
        spot_map = {_IDX_KEY: _spot_tick(74000.0)}  # would breach IF it were guarded
        g = LivePositionGuard(
            registry=r, client_factory=lambda: _aw(client), square_fn=rec.square_fn,
            spot_tick_fn=lambda: spot_map, now_fn=lambda: _NOW,
        )
        run(g._cycle())
        assert rec.squared == []
        assert len(r) == 1  # premium path still works, untouched by spot/time/EOD

    def test_squaring_flag_prevents_double_square(self):
        # The `squaring` flag (not remove-before-square) is the re-entrancy guard:
        # once a square is issued the entry is KEPT but marked squaring, so a later
        # cycle that still sees the position OPEN never re-issues a second square.
        spot_exit = {"direction": "CE", "instrument_key": _IDX_KEY,
                     "spot_target": 75600.0, "spot_stop": 75000.0}
        r = self._deployed(spot_exit=spot_exit)
        client = _FakeClient([_pos(netqty=20, lp=260.0)])

        class _SlowRec:
            def __init__(self):
                self.calls = 0

            async def square_fn(self, client, position, *, reason):
                self.calls += 1
                await asyncio.sleep(0)
                return {"squared": True, "reason": reason}

        slow = _SlowRec()
        spot_map = {_IDX_KEY: _spot_tick(75650.0)}
        g = LivePositionGuard(
            registry=r, client_factory=lambda: _aw(client), square_fn=slow.square_fn,
            spot_tick_fn=lambda: spot_map, now_fn=lambda: _NOW,
        )
        run(g._cycle())
        assert slow.calls == 1
        assert len(r) == 1 and r.get("ORD1")["squaring"] is True
        # a second cycle — position STILL open — must NOT re-square (squaring skip)
        run(g._cycle())
        assert slow.calls == 1
        # once the broker confirms flat, the entry is dropped
        client.set([_pos(netqty=0, lp=170.0)])
        run(g._cycle())
        assert len(r) == 0


# ---------------------------------------------------------------------------
# Contract: runtime.py wires spot_tick_fn into LivePositionGuard production
# ---------------------------------------------------------------------------

class TestGuardWiringContract:
    """Assert that the production LivePositionGuard construction in runtime.py
    passes spot_tick_fn (the upstox stream tick map) and an explicit eod_square_ist.
    This is a contract-corpus string assertion — the standard for wiring that
    can't be unit-imported (server + runtime use motor which is absent on host)."""

    def test_guard_wired_with_spot_tick_fn(self):
        from tests.contract_corpus import backend_api_text
        src = backend_api_text()
        assert "spot_tick_fn=" in src, (
            "LivePositionGuard in runtime.py must pass spot_tick_fn= so spot-mirror "
            "exits fire on live positions."
        )

    def test_guard_wired_with_latest_tick_map(self):
        from tests.contract_corpus import backend_api_text
        src = backend_api_text()
        # Pin that the lambda actually calls upstox_stream_manager.latest_tick_map()
        assert "latest_tick_map()" in src, (
            "spot_tick_fn lambda must call upstox_stream_manager.latest_tick_map()"
        )

    def test_guard_wired_with_eod_square_ist(self):
        from tests.contract_corpus import backend_api_text
        src = backend_api_text()
        assert "eod_square_ist=" in src, (
            "LivePositionGuard in runtime.py must pass eod_square_ist= explicitly "
            "for documentation."
        )


class TestEodSquare:
    def _mk(self, *, source, now=_EOD_NOW):
        r = LiveMonitorRegistry()
        state = build_monitor_state(250.0, stop_pct=90)  # wide -> premium never fires
        r.register(key="ORD1", tsym=_TSYM, exch="BFO", qty=20, prd="I",
                   entry_price=250.0, state=state, source=source,
                   deployment_id=("dep1" if source != "manual" else None))
        rec = _Recorder()
        client = _FakeClient([_pos(netqty=20, lp=255.0)])  # held by premium guard
        g = LivePositionGuard(
            registry=r, client_factory=lambda: _aw(client), square_fn=rec.square_fn,
            now_fn=lambda: now,
        )
        return r, rec, g

    def test_eod_squares_deployed_at_1500_ist(self):
        r, rec, g = self._mk(source="auto_live", now=_EOD_NOW)
        run(g._cycle())
        assert len(rec.squared) == 1
        assert rec.squared[0][1] == "eod_square"
        # EOD issues the square; the entry is KEPT (squaring) until confirmed flat.
        assert len(r) == 1
        assert r.get("ORD1")["squaring"] is True

    def test_eod_squares_manual(self):
        # The 10-min auto-square timer was removed; the 15:00 IST EOD square is now
        # the "never left open" backstop for a manual test position too.
        r, rec, g = self._mk(source="manual", now=_EOD_NOW)
        run(g._cycle())
        assert len(rec.squared) == 1
        assert rec.squared[0][1] == "eod_square"
        assert len(r) == 1
        assert r.get("ORD1")["squaring"] is True

    def test_no_eod_square_before_1500_ist(self):
        r, rec, g = self._mk(source="auto_live", now=_NOW)  # 11:30 IST
        run(g._cycle())
        assert rec.squared == []
        assert len(r) == 1


class TestRehydrateFromBroker:
    """rehydrate_from_broker re-attaches the guard to open broker positions after a
    restart (the in-memory registry is empty on boot)."""

    def test_rehydrates_open_position_with_default_stop_and_source(self):
        reg = LiveMonitorRegistry()
        client = _FakeClient([_pos(netqty=20, lp=250.0, tsym=_TSYM)])
        n = run(_guard(reg, client, _Recorder()).rehydrate_from_broker())
        assert n == 1
        item = reg.get(_TSYM)
        assert item is not None
        assert item["source"] == "rehydrated"
        assert item["qty"] == 20
        # deep-default 50% stop on a 250 entry → stop_level 125.
        assert item["state"]["stop_level"] == 125.0

    def test_skips_flat_positions(self):
        reg = LiveMonitorRegistry()
        client = _FakeClient([_pos(netqty=0, lp=250.0, tsym=_TSYM)])
        n = run(_guard(reg, client, _Recorder()).rehydrate_from_broker())
        assert n == 0 and len(reg) == 0

    def test_does_not_clobber_an_already_registered_tsym(self):
        reg = LiveMonitorRegistry()
        reg.register(key="N1", tsym=_TSYM, exch="BFO", qty=20, prd="I", entry_price=300.0,
                     state=build_monitor_state(300.0, stop_pct=30), source="auto_live")
        client = _FakeClient([_pos(netqty=20, lp=250.0, tsym=_TSYM)])
        n = run(_guard(reg, client, _Recorder()).rehydrate_from_broker())
        assert n == 0
        assert len(reg) == 1  # no duplicate entry added for the same tsym
        item = reg.get("N1")  # original arm (keyed by norenordno) survives untouched
        assert item["source"] == "auto_live" and item["entry_price"] == 300.0

    def test_not_connected_returns_zero(self):
        reg = LiveMonitorRegistry()
        g = LivePositionGuard(registry=reg, client_factory=lambda: _aw(None),
                              square_fn=_Recorder().square_fn)
        assert run(g.rehydrate_from_broker()) == 0 and len(reg) == 0

    def test_position_book_error_returns_zero(self):
        reg = LiveMonitorRegistry()

        class _Boom:
            async def position_book(self):
                raise RuntimeError("broker down")

        g = LivePositionGuard(registry=reg, client_factory=lambda: _aw(_Boom()),
                              square_fn=_Recorder().square_fn)
        assert run(g.rehydrate_from_broker()) == 0 and len(reg) == 0

    def test_skips_position_without_a_price(self):
        reg = LiveMonitorRegistry()
        client = _FakeClient([{"tsym": _TSYM, "exch": "BFO", "netqty": "20"}])  # no lp/avg
        n = run(_guard(reg, client, _Recorder()).rehydrate_from_broker())
        assert n == 0 and len(reg) == 0


# ---------------------------------------------------------------------------
# Close-loop on_close hook (slice: live_trades realized-P&L close-loop)
# ---------------------------------------------------------------------------
class TestOnCloseHook:
    def _guard_with_close(self, reg, client, rec, calls, *, now_fn=None, raise_in_close=False):
        async def on_close(entry, exit_price, reason, result):
            calls.append({"tsym": entry["tsym"], "exit_price": exit_price,
                          "reason": reason, "result": result, "source": entry.get("source")})
            if raise_in_close:
                raise RuntimeError("boom")  # must NOT kill the cycle
        return LivePositionGuard(
            registry=reg, client_factory=lambda: _aw(client),
            square_fn=rec.square_fn, on_close=on_close, now_fn=now_fn)

    def test_on_close_fires_on_confirmed_flat_with_exit_mark_and_result(self):
        reg = LiveMonitorRegistry()
        _registered(reg, entry=250.0, stop_pct=30)            # stop at 175
        client = _FakeClient([_pos(netqty=20, lp=170.0)])     # below stop → exit
        rec, calls = _Recorder(), []
        guard = self._guard_with_close(reg, client, rec, calls, now_fn=lambda: _NOW)
        # Cycle 1: breach → square ISSUED; on_close does NOT fire yet (still open).
        run(guard._cycle())
        assert calls == []
        assert len(reg) == 1 and reg.get("ORD1")["squaring"] is True
        # Cycle 2: broker confirms flat → on_close journals the close ONCE.
        client.set([_pos(netqty=0, lp=170.0)])
        run(guard._cycle())
        assert len(calls) == 1
        assert calls[0]["exit_price"] == 170.0                # the last broker mark (cycle 1)
        assert calls[0]["reason"] == "stop"
        assert calls[0]["result"] == {"squared": True, "via": "confirmed_flat"}
        assert len(reg) == 0                                  # dropped on confirmed-flat

    def test_on_close_exception_does_not_kill_the_cycle(self):
        reg = LiveMonitorRegistry()
        _registered(reg, entry=250.0, stop_pct=30)
        client = _FakeClient([_pos(netqty=20, lp=170.0)])
        rec, calls = _Recorder(), []
        guard = self._guard_with_close(reg, client, rec, calls,
                                       now_fn=lambda: _NOW, raise_in_close=True)
        run(guard._cycle())                                    # issue (still open)
        assert rec.squared and rec.squared[0][0] == _TSYM      # square happened
        client.set([_pos(netqty=0, lp=170.0)])
        run(guard._cycle())                                    # confirm-flat → on_close raises
        assert len(calls) == 1                                 # hook fired
        assert len(reg) == 0                                   # still dropped despite the raise

    def test_on_close_fires_for_eod_square_of_deployed_position(self):
        reg = LiveMonitorRegistry()
        state = build_monitor_state(250.0, stop_pct=30)
        reg.register(key="ORDX", tsym=_TSYM, exch="BFO", qty=20, prd="I",
                     entry_price=250.0, state=state, source="auto_live", deployment_id="dep-1")
        client = _FakeClient([_pos(netqty=20, lp=250.0)])      # no premium breach
        rec, calls = _Recorder(), []
        # 15:30 IST = 10:00 UTC → past the 15:00 EOD cutoff
        now_fn = lambda: datetime(2026, 6, 26, 10, 0, tzinfo=timezone.utc)
        guard = self._guard_with_close(reg, client, rec, calls, now_fn=now_fn)
        # Cycle 1: EOD square ISSUED; on_close deferred until confirmed flat.
        run(guard._cycle())
        assert calls == []
        assert reg.get("ORDX")["squaring"] is True
        # Cycle 2: broker confirms flat → on_close fires with the EOD reason.
        client.set([_pos(netqty=0, lp=170.0)])
        run(guard._cycle())
        assert len(calls) == 1
        assert calls[0]["reason"] == "eod_square"
        assert calls[0]["source"] == "auto_live"


# ---------------------------------------------------------------------------
# OCO cancel-after-real-fill (B4 #3): cancel the resting OCO ONLY after a
# CONFIRMED REAL square fill. A dry-run square (LIVE_GUARD_ARMED=0, the default
# while validating) must NOT cancel the OCO — doing so would strip the broker
# net WITHOUT squaring → position fully unprotected. cancel only when the
# square is real (squared and not dry_run). Cancel AFTER the square, never before.
# ---------------------------------------------------------------------------
class _OcoClient(_FakeClient):
    """A position-book fake that ALSO records cancel_oco calls."""

    def __init__(self, positions):
        super().__init__(positions)
        self.cancel_oco_calls = []

    async def cancel_oco(self, al_id):
        self.cancel_oco_calls.append(al_id)
        return {"ok": True, "al_id": str(al_id)}


class _ScriptedSquare:
    """square_fn returning a scripted result; records the ORDER of square vs cancel."""

    def __init__(self, result, *, client=None):
        self._result = result
        self._client = client          # to inspect cancel-state AT square time
        self.squared = []
        self.cancel_count_at_square = None

    async def square_fn(self, client, position, *, reason):
        self.squared.append((position["tsym"], reason))
        # Snapshot how many cancel_oco calls happened BEFORE the square ran —
        # proves the cancel is issued AFTER (never before) the square.
        tgt = self._client if self._client is not None else client
        self.cancel_count_at_square = len(getattr(tgt, "cancel_oco_calls", []))
        return dict(self._result)


def _registered_with_oco(registry, *, oco_al_id="OCO1", entry=250.0, stop_pct=30):
    state = build_monitor_state(entry, stop_pct=stop_pct)
    registry.register(key="ORD1", tsym=_TSYM, exch="BFO", qty=20, prd="I",
                      entry_price=entry, state=state, oco_al_id=oco_al_id)
    return registry


class TestOcoCancelAfterRealFill:
    def test_place_accept_keeps_oco_until_confirmed_flat(self):
        """A place-accepted square ({"squared":True}) is PENDING, not done: the OCO
        stays resting and the entry stays registered until the BROKER BOOK confirms
        the position flat. Only THEN is the OCO cancelled and the entry dropped.

        This is the Layer 1 safety invariant — on a fast crash a marketable exit can
        rest unfilled, so cancelling the OCO / dropping the entry on place-acceptance
        would leave the position OPEN and UNPROTECTED yet reported closed."""
        reg = LiveMonitorRegistry()
        _registered_with_oco(reg, oco_al_id="OCO1")
        client = _OcoClient([_pos(netqty=20, lp=170.0)])     # breach; still OPEN in book
        sq = _ScriptedSquare({"squared": True}, client=client)
        guard = LivePositionGuard(registry=reg, client_factory=lambda: _aw(client),
                                  square_fn=sq.square_fn, now_fn=lambda: _NOW)
        # Cycle 1: breach → square ISSUED, but the position still shows open.
        run(guard._cycle())
        assert sq.squared and sq.squared[0][0] == _TSYM       # square issued
        assert client.cancel_oco_calls == []                  # OCO STILL resting
        assert len(reg) == 1                                  # entry STILL registered
        assert reg.get("ORD1")["squaring"] is True
        # Cycle 2: the broker now reports the position flat → confirmed-flat →
        # cancel the OCO and drop the entry.
        client.set([_pos(netqty=0, lp=170.0)])
        run(guard._cycle())
        assert client.cancel_oco_calls == ["OCO1"]            # OCO cancelled on confirmed-flat
        assert len(reg) == 0                                  # entry dropped

    def test_real_fill_cancels_oco_on_confirmed_flat(self):
        """The OCO is cancelled once the broker confirms the position flat (not on
        the place-accept cycle)."""
        reg = LiveMonitorRegistry()
        _registered_with_oco(reg, oco_al_id="OCO1")
        client = _OcoClient([_pos(netqty=20, lp=170.0)])     # below stop → breach
        sq = _ScriptedSquare({"squared": True}, client=client)
        guard = LivePositionGuard(registry=reg, client_factory=lambda: _aw(client),
                                  square_fn=sq.square_fn, now_fn=lambda: _NOW)
        run(guard._cycle())                                   # issue (still open)
        assert sq.squared and sq.squared[0][0] == _TSYM       # square ran
        assert client.cancel_oco_calls == []                  # not yet — still open
        client.set([_pos(netqty=0, lp=170.0)])                                        # broker confirms flat
        run(guard._cycle())
        assert client.cancel_oco_calls == ["OCO1"]            # OCO cancelled

    def test_closed_elsewhere_cancels_orphan_oco_without_journaling(self):
        """A seen-filled position that goes flat WITHOUT the guard squaring it
        (closed manually / the OCO fired) is dropped and its OCO cancelled (orphan
        cleanup — a resting alert against a flat account could open a fresh naked
        short) but the close is NOT journaled (no guard square was pending)."""
        reg = LiveMonitorRegistry()
        _registered_with_oco(reg, oco_al_id="OCO1", stop_pct=30)  # stop 175
        client = _OcoClient([_pos(netqty=20, lp=250.0)])          # held (above stop)
        rec = _Recorder()
        calls = []

        async def on_close(entry, exit_price, reason, result):
            calls.append(reason)

        guard = LivePositionGuard(registry=reg, client_factory=lambda: _aw(client),
                                  square_fn=rec.square_fn, on_close=on_close,
                                  now_fn=lambda: _NOW)
        run(guard._cycle())                                       # seen_filled, no square
        assert rec.squared == [] and reg.get("ORD1")["squaring"] is False
        client.set([_pos(netqty=0, lp=170.0)])                                            # closed elsewhere → flat
        run(guard._cycle())
        assert client.cancel_oco_calls == ["OCO1"]               # orphan OCO cancelled
        assert calls == []                                       # NOT journaled (not squaring)
        assert len(reg) == 0                                     # dropped

    def test_dry_run_square_does_not_cancel_oco(self):
        """square_fn returns a dry-run ({"squared":False,"dry_run":True}) →
        cancel_oco is NOT called (cancelling would strip the broker net while the
        position is still open → unprotected)."""
        reg = LiveMonitorRegistry()
        _registered_with_oco(reg, oco_al_id="OCO1")
        client = _OcoClient([_pos(netqty=20, lp=170.0)])
        sq = _ScriptedSquare({"squared": False, "dry_run": True}, client=client)
        guard = LivePositionGuard(registry=reg, client_factory=lambda: _aw(client),
                                  square_fn=sq.square_fn, now_fn=lambda: _NOW)
        run(guard._cycle())
        assert sq.squared                                     # square (dry-run) ran
        assert client.cancel_oco_calls == []                  # OCO NOT cancelled
        # A dry-run transmits nothing → entry kept, OCO resting, squaring stays False.
        assert len(reg) == 1
        assert reg.get("ORD1")["squaring"] is False

    def test_real_dry_run_field_present_but_false_cancels_on_flat(self):
        """A real square carrying an explicit dry_run=False cancels the OCO — once
        the broker confirms flat (not on the place-accept cycle)."""
        reg = LiveMonitorRegistry()
        _registered_with_oco(reg, oco_al_id="OCO1")
        client = _OcoClient([_pos(netqty=20, lp=170.0)])
        sq = _ScriptedSquare({"squared": True, "dry_run": False}, client=client)
        guard = LivePositionGuard(registry=reg, client_factory=lambda: _aw(client),
                                  square_fn=sq.square_fn, now_fn=lambda: _NOW)
        run(guard._cycle())
        assert client.cancel_oco_calls == []                  # still open → not yet
        client.set([_pos(netqty=0, lp=170.0)])
        run(guard._cycle())
        assert client.cancel_oco_calls == ["OCO1"]

    def test_failed_square_does_not_cancel_oco(self):
        """A failed (not dry-run) square ({"squared":False}) → cancel_oco NOT
        called (the position is NOT closed; keep its OCO protection)."""
        reg = LiveMonitorRegistry()
        _registered_with_oco(reg, oco_al_id="OCO1")
        client = _OcoClient([_pos(netqty=20, lp=170.0)])
        sq = _ScriptedSquare({"squared": False, "failures": ["rejected twice"]},
                             client=client)
        guard = LivePositionGuard(registry=reg, client_factory=lambda: _aw(client),
                                  square_fn=sq.square_fn, now_fn=lambda: _NOW)
        run(guard._cycle())
        assert client.cancel_oco_calls == []

    def test_no_oco_al_id_never_calls_cancel_oco(self):
        """An entry with NO oco_al_id → cancel_oco is never called even on a real
        fill (existing position_book-only fakes without oco_al_id stay green)."""
        reg = LiveMonitorRegistry()
        _registered(reg, entry=250.0, stop_pct=30)            # no oco_al_id
        client = _OcoClient([_pos(netqty=20, lp=170.0)])
        sq = _ScriptedSquare({"squared": True}, client=client)
        guard = LivePositionGuard(registry=reg, client_factory=lambda: _aw(client),
                                  square_fn=sq.square_fn, now_fn=lambda: _NOW)
        run(guard._cycle())
        assert sq.squared                                     # square ran
        assert client.cancel_oco_calls == []                  # nothing to cancel

    def test_client_without_cancel_oco_real_fill_does_not_raise(self):
        """A client lacking cancel_oco (the legacy _FakeClient) + a real fill on an
        oco_al_id entry → the cycle must NOT raise (hasattr guard)."""
        reg = LiveMonitorRegistry()
        _registered_with_oco(reg, oco_al_id="OCO1")
        client = _FakeClient([_pos(netqty=20, lp=170.0)])     # no cancel_oco attr
        sq = _ScriptedSquare({"squared": True})
        guard = LivePositionGuard(registry=reg, client_factory=lambda: _aw(client),
                                  square_fn=sq.square_fn, now_fn=lambda: _NOW)
        exits = run(guard._cycle())                           # must not raise
        assert sq.squared and len(exits) == 1

    def test_cancel_oco_raising_does_not_break_cycle(self):
        """A cancel_oco that RAISES (on confirmed-flat) is logged but never breaks
        the guard cycle, and the entry is still dropped."""
        class _RaisingOco(_OcoClient):
            async def cancel_oco(self, al_id):
                self.cancel_oco_calls.append(al_id)
                raise RuntimeError("cancel rejected")
        reg = LiveMonitorRegistry()
        _registered_with_oco(reg, oco_al_id="OCO1")
        client = _RaisingOco([_pos(netqty=20, lp=170.0)])
        sq = _ScriptedSquare({"squared": True}, client=client)
        guard = LivePositionGuard(registry=reg, client_factory=lambda: _aw(client),
                                  square_fn=sq.square_fn, now_fn=lambda: _NOW)
        exits = run(guard._cycle())                           # issue (still open)
        assert len(exits) == 1                                # square issue recorded
        assert client.cancel_oco_calls == []                  # not on place-accept
        client.set([_pos(netqty=0, lp=170.0)])
        run(guard._cycle())                                   # confirm-flat → cancel raises
        assert client.cancel_oco_calls == ["OCO1"]            # attempted
        assert len(reg) == 0                                  # still dropped despite the raise


# ---------------------------------------------------------------------------
# Confirmed-flat requires a REAL broker read — an empty/garbage position_book (a
# broker Not_Ok hiccup returns [], NOT a real flat) or an unparseable netqty must
# NEVER finalize a guard square (cancel OCO / journal CLOSED / drop). Mirrors the
# reboot_reconcile "empty-book false-close hole" guard and kill_switch._parse_netqty
# ("NEVER coerce an unparseable netqty to flat").
# ---------------------------------------------------------------------------
class TestConfirmedFlatRequiresRealBook:
    def _garbage_row(self, netqty):
        return {"tsym": _TSYM, "exch": "BFO", "netqty": netqty, "lp": "1.0", "urmtom": "0"}

    def test_transient_empty_book_does_not_finalize_open_squaring_position(self):
        """A single empty position_book (broker Not_Ok → []) must NOT be read as
        flat: the OCO stays, no false CLOSE is journaled, the entry stays watched."""
        reg = LiveMonitorRegistry()
        _registered_with_oco(reg, oco_al_id="OCO1")
        client = _OcoClient([_pos(netqty=20, lp=170.0)])      # breach → squares, still open
        calls = []

        async def on_close(entry, exit_price, reason, result):
            calls.append(reason)

        sq = _ScriptedSquare({"squared": True}, client=client)
        guard = LivePositionGuard(registry=reg, client_factory=lambda: _aw(client),
                                  square_fn=sq.square_fn, on_close=on_close,
                                  now_fn=lambda: _NOW)
        run(guard._cycle())                                   # issue → squaring, open
        assert reg.get("ORD1")["squaring"] is True
        client.set([])                                        # broker hiccup — NOT a real flat
        run(guard._cycle())
        assert client.cancel_oco_calls == []                 # OCO must stay (position may be open!)
        assert calls == []                                   # no false CLOSED journaled
        assert len(reg) == 1                                 # entry kept + still watched
        # A REAL flat (present netqty==0 row in a non-empty book) DOES finalize.
        client.set([_pos(netqty=0, lp=170.0)])
        run(guard._cycle())
        assert client.cancel_oco_calls == ["OCO1"]
        assert calls == ["stop"]
        assert len(reg) == 0

    def test_unparseable_netqty_does_not_finalize(self):
        """A present row whose netqty can't be parsed ('nan'/'abc') is UNKNOWN, not
        flat — never finalize on it."""
        reg = LiveMonitorRegistry()
        _registered_with_oco(reg, oco_al_id="OCO1")
        client = _OcoClient([_pos(netqty=20, lp=170.0)])
        sq = _ScriptedSquare({"squared": True}, client=client)
        guard = LivePositionGuard(registry=reg, client_factory=lambda: _aw(client),
                                  square_fn=sq.square_fn, now_fn=lambda: _NOW)
        run(guard._cycle())                                   # issue → squaring
        client.set([self._garbage_row("nan")])
        run(guard._cycle())
        assert client.cancel_oco_calls == []                 # unparseable ≠ flat → hold
        assert len(reg) == 1
        assert reg.get("ORD1")["squaring"] is True

    def test_absent_from_nonempty_book_is_a_confirmed_flat(self):
        """A tsym ABSENT from a NON-empty book (a complete book that no longer lists
        it) is a genuine flat and DOES finalize — only an empty/garbage book is held."""
        reg = LiveMonitorRegistry()
        _registered_with_oco(reg, oco_al_id="OCO1")
        client = _OcoClient([_pos(netqty=20, lp=170.0)])
        sq = _ScriptedSquare({"squared": True}, client=client)
        guard = LivePositionGuard(registry=reg, client_factory=lambda: _aw(client),
                                  square_fn=sq.square_fn, now_fn=lambda: _NOW)
        run(guard._cycle())                                   # issue → squaring
        # Book is non-empty (some OTHER scrip) but our tsym is gone → confirmed flat.
        client.set([{"tsym": "SOMEOTHER", "exch": "BFO", "netqty": "50",
                     "lp": "10.0", "urmtom": "0"}])
        run(guard._cycle())
        assert client.cancel_oco_calls == ["OCO1"]
        assert len(reg) == 0

    def test_partial_fill_holds_squaring_no_re_issue_until_flat(self):
        """A guard exit that only PARTIALLY fills (broker netqty shrinks 20→10 but
        never 0) keeps the entry `squaring`: it is NOT re-issued (the squaring skip)
        and NOT finalized (still open), across cycles, until the broker confirms
        netqty→0. Pins the Layer-1 behavior — the resting exit + OCO protect the
        remainder; Layer 2 adds an interval-gated re-price for the unfilled part."""
        reg = LiveMonitorRegistry()
        _registered_with_oco(reg, oco_al_id="OCO1")
        client = _OcoClient([_pos(netqty=20, lp=170.0)])      # breach → square
        sq = _ScriptedSquare({"squared": True}, client=client)
        guard = LivePositionGuard(registry=reg, client_factory=lambda: _aw(client),
                                  square_fn=sq.square_fn, now_fn=lambda: _NOW)
        run(guard._cycle())                                   # issue → squaring
        assert len(sq.squared) == 1
        # Partial fill: netqty 20 → 10. Still open → held, NOT re-squared, NOT finalized.
        client.set([_pos(netqty=10, lp=170.0)])
        run(guard._cycle())
        assert len(sq.squared) == 1                           # no second square issued
        assert client.cancel_oco_calls == []                  # not finalized (still open)
        assert len(reg) == 1 and reg.get("ORD1")["squaring"] is True
        # Remaining fills: netqty → 0 → confirmed flat → finalize.
        client.set([_pos(netqty=0, lp=170.0)])
        run(guard._cycle())
        assert client.cancel_oco_calls == ["OCO1"]
        assert len(reg) == 0


# ---------------------------------------------------------------------------
# Guard cycle — token capture from the broker book row (Task C3)
# ---------------------------------------------------------------------------
# The depth-aware square (auto_square C3) refreshes the exit ref price from a
# fresh GetQuotes when position["token"] is set. The token lives on the broker
# book row, so the guard's per-cycle position refresh must copy it onto
# entry["position"] so the square has it.
# ---------------------------------------------------------------------------

class TestGuardTokenCapture:
    def test_cycle_captures_token_from_book_row(self):
        r = LiveMonitorRegistry()
        _registered(r, entry=250.0, stop_pct=30)  # stop = 175
        # Live, non-breaching position (lp 240 > stop 175 → no square) whose book
        # row carries a contract token.
        row = _pos(netqty=20, lp=240.0)
        row["token"] = "555"
        client = _FakeClient([row])
        rec = _Recorder()
        run(_guard(r, client, rec)._cycle())
        # Not squared (above stop), still guarded, and the token was captured.
        assert rec.squared == []
        assert len(r) == 1
        assert r.get("ORD1")["position"]["token"] == "555"

    def test_cycle_token_none_when_book_row_has_no_token(self):
        r = LiveMonitorRegistry()
        _registered(r, entry=250.0, stop_pct=30)
        client = _FakeClient([_pos(netqty=20, lp=240.0)])  # no token on the row
        rec = _Recorder()
        run(_guard(r, client, rec)._cycle())
        assert r.get("ORD1")["position"]["token"] is None


# ---------------------------------------------------------------------------
# Layer 2 — over-sell-safe widening re-price of a resting-unfilled guard exit.
# The first square (band 1%) goes through square_fn (unchanged). A squaring entry
# that stays open past the interval is ESCALATED at widening bands (2%, 4%) via a
# SEPARATE injected reprice_fn — over-sell-safe (cancels the tracked prior order,
# re-reads its fillshares, sizes to the confirmed remainder). Band advances only on
# a genuine new placement; empty/UNKNOWN reads never re-price; a per-cycle budget
# bounds a synchronized basket; the loop terminates loudly (never silently spins).
# ---------------------------------------------------------------------------
class _SquareRec:
    """square_fn that returns a norenordno (so the first square's exit id is tracked)."""

    def __init__(self, result=None):
        self.calls = []
        self._result = result or {"squared": True, "via": "exit_order", "norenordno": "EXIT0"}

    async def square_fn(self, client, position, *, reason):
        self.calls.append((position["tsym"], reason))
        return dict(self._result)


class _RepriceRec:
    """reprice_fn recorder. Returns scripted results in order (last repeats); default =
    a successful exit_order placement with a fresh norenordno each call."""

    def __init__(self, results=None):
        self.calls = []
        self._results = list(results) if results is not None else None
        self._n = 0

    async def reprice_fn(self, client, position, *, band_pct, prev_ordno, prev_qty, reason):
        self.calls.append({"band_pct": band_pct, "prev_ordno": prev_ordno,
                           "prev_qty": prev_qty, "reason": reason, "tsym": position["tsym"]})
        if self._results is not None:
            r = self._results[min(self._n, len(self._results) - 1)]
            self._n += 1
            return dict(r)
        self._n += 1
        return {"squared": True, "via": "exit_order", "norenordno": f"RP{self._n}", "qty": prev_qty}


class _Clock:
    """Mutable injected clock; advance() between cycles to drive the interval gate."""

    def __init__(self, t=_NOW):
        self.t = t

    def now(self):
        return self.t

    def advance(self, secs):
        self.t = self.t + timedelta(seconds=float(secs))


class _RepriceByTsym:
    """reprice_fn recorder that returns `unpriced` for a given set of tsyms and a
    successful exit_order for the rest (K-budget fairness test)."""

    def __init__(self, unpriced_tsyms):
        self.calls = []
        self._unpriced = set(unpriced_tsyms)

    async def reprice_fn(self, client, position, *, band_pct, prev_ordno, prev_qty, reason):
        self.calls.append({"tsym": position["tsym"], "band_pct": band_pct})
        if position["tsym"] in self._unpriced:
            return {"squared": False, "reason": "unpriced"}
        return {"squared": True, "via": "exit_order",
                "norenordno": f"RP_{position['tsym']}", "qty": prev_qty}


class _CancelRecClient(_OcoClient):
    """_OcoClient that ALSO records cancel_order calls (finalize orphan-cancel test)."""

    def __init__(self, positions):
        super().__init__(positions)
        self.cancel_order_calls = []

    async def cancel_order(self, ordno):
        self.cancel_order_calls.append(ordno)
        return type("R", (), {"ok": True})()


def _l2_guard(reg, client, sq, rp, clock, **kw):
    return LivePositionGuard(
        registry=reg, client_factory=lambda: _aw(client),
        square_fn=sq.square_fn, reprice_fn=rp.reprice_fn, now_fn=clock.now,
        reprice_interval_seconds=4.0, reprice_band_schedule=(1.0, 2.0, 4.0),
        reprice_max_per_cycle=2, **kw)


class TestLayer2Reprice:
    def test_reprice_escalates_at_widening_bands_after_interval(self):
        reg = LiveMonitorRegistry()
        _registered(reg, entry=250.0, stop_pct=30)             # stop 175
        client = _FakeClient([_pos(netqty=20, lp=170.0)])      # breach; stays OPEN
        sq, rp, clock = _SquareRec(), _RepriceRec(), _Clock(_NOW)
        g = _l2_guard(reg, client, sq, rp, clock)
        # Cycle 1: FIRST square at band 1% via square_fn; tracking seeded; no re-price.
        run(g._cycle())
        assert len(sq.calls) == 1 and rp.calls == []
        e = reg.get("ORD1")
        assert e["squaring"] is True and e["square_band_idx"] == 1
        assert e["square_ordno"] == "EXIT0" and e["square_qty"] == 20
        # Cycle 2: interval NOT elapsed → no re-price.
        clock.advance(1.5)
        run(g._cycle())
        assert rp.calls == []
        # Cycle 3: interval elapsed → re-price at band 2%, prev exit id/qty passed.
        clock.advance(3.0)                                     # 4.5s > 4.0
        run(g._cycle())
        assert len(rp.calls) == 1
        assert rp.calls[0]["band_pct"] == 2.0
        assert rp.calls[0]["prev_ordno"] == "EXIT0" and rp.calls[0]["prev_qty"] == 20
        e = reg.get("ORD1")
        assert e["square_band_idx"] == 2 and e["square_ordno"] == "RP1"
        # Cycle 4: interval again → band 4% (terminal) → exhausted.
        clock.advance(4.5)
        run(g._cycle())
        assert len(rp.calls) == 2 and rp.calls[1]["band_pct"] == 4.0
        e = reg.get("ORD1")
        assert e["square_band_idx"] == 3 and e["reprice_exhausted"] is True
        # Cycle 5+: schedule exhausted → no further re-price (resting exit + OCO remain).
        clock.advance(4.5)
        run(g._cycle())
        assert len(rp.calls) == 2
        assert g.status()["stuck"] == 1 and g.status()["reprices"] == 2

    def test_no_reprice_on_unknown_empty_book(self):
        """A squaring entry on an UNKNOWN (empty []) book is NOT re-priced (broker
        hiccup ≠ real read); state is frozen (no false finalize either); a later good
        read resumes the escalation."""
        reg = LiveMonitorRegistry()
        _registered(reg, entry=250.0, stop_pct=30)
        client = _FakeClient([_pos(netqty=20, lp=170.0)])
        sq, rp, clock = _SquareRec(), _RepriceRec(), _Clock(_NOW)
        g = _l2_guard(reg, client, sq, rp, clock)
        run(g._cycle())                                        # first square
        clock.advance(5.0)
        client.set([])                                         # broker hiccup → UNKNOWN
        run(g._cycle())
        assert rp.calls == []                                  # NOT re-priced on []
        assert reg.get("ORD1")["square_band_idx"] == 1         # state frozen
        assert len(reg) == 1                                   # not finalized on a bad read
        client.set([_pos(netqty=20, lp=170.0)])                # good read again
        run(g._cycle())
        assert len(rp.calls) == 1 and rp.calls[0]["band_pct"] == 2.0

    def test_hard_reject_stops_entry_no_spam(self):
        """A re-price that hard-REJECTS (failures) STOPS the entry — over many cycles
        it is attempted exactly ONCE (rate-limit safe), band NOT advanced, surfaced."""
        reg = LiveMonitorRegistry()
        _registered(reg, entry=250.0, stop_pct=30)
        client = _FakeClient([_pos(netqty=20, lp=170.0)])
        rp = _RepriceRec([{"squared": False, "failures": ["RMS reject"]}])
        sq, clock = _SquareRec(), _Clock(_NOW)
        g = _l2_guard(reg, client, sq, rp, clock)
        run(g._cycle())
        for _ in range(10):
            clock.advance(5.0)
            run(g._cycle())
        assert len(rp.calls) == 1                              # attempted once, then STOPPED
        assert reg.get("ORD1")["reprice_stopped"] is True
        assert reg.get("ORD1")["square_band_idx"] == 1         # band NOT advanced
        assert g.status()["stuck"] == 1

    def test_cancel_unconfirmed_retries_same_band(self):
        """cancel_unconfirmed (prior exit not provably dead → placed nothing) keeps the
        SAME band, stamps ts (rate gate), and retries only after the interval."""
        reg = LiveMonitorRegistry()
        _registered(reg, entry=250.0, stop_pct=30)
        client = _FakeClient([_pos(netqty=20, lp=170.0)])
        rp = _RepriceRec([{"squared": False, "reason": "cancel_unconfirmed"},
                          {"squared": True, "via": "exit_order", "norenordno": "RP2", "qty": 20}])
        sq, clock = _SquareRec(), _Clock(_NOW)
        g = _l2_guard(reg, client, sq, rp, clock)
        run(g._cycle())
        clock.advance(5.0); run(g._cycle())                   # attempt 1 → cancel_unconfirmed
        assert len(rp.calls) == 1 and rp.calls[0]["band_pct"] == 2.0
        assert reg.get("ORD1")["square_band_idx"] == 1         # band NOT advanced
        clock.advance(2.0); run(g._cycle())                   # < interval → no retry
        assert len(rp.calls) == 1
        clock.advance(3.0); run(g._cycle())                   # ≥ interval → retry SAME band
        assert len(rp.calls) == 2 and rp.calls[1]["band_pct"] == 2.0
        assert reg.get("ORD1")["square_band_idx"] == 2         # advanced on the success

    def test_unpriced_reprice_stamps_ts_but_not_counted(self):
        """unpriced (primitive placed nothing — bad lp/quote) is NOT counted as a
        re-price and does NOT advance the band, but DOES stamp square_last_ts (so it
        rotates fairly through the K-budget instead of monopolizing it) and surfaces a
        signal."""
        reg = LiveMonitorRegistry()
        _registered(reg, entry=250.0, stop_pct=30)
        client = _FakeClient([_pos(netqty=20, lp=170.0)])
        rp = _RepriceRec([{"squared": False, "reason": "unpriced"}])
        sq, clock = _SquareRec(), _Clock(_NOW)
        g = _l2_guard(reg, client, sq, rp, clock)
        run(g._cycle())
        t0 = reg.get("ORD1")["square_last_ts"]
        clock.advance(5.0); run(g._cycle())                   # unpriced
        assert len(rp.calls) == 1
        assert reg.get("ORD1")["square_last_ts"] != t0         # ts STAMPED (fair rotation)
        assert reg.get("ORD1")["square_band_idx"] == 1         # band unchanged
        assert g.status()["reprices"] == 0                     # not counted as a placement
        assert "unpriced" in (g.status()["last_error"] or "")  # surfaced, not silent

    def test_already_flat_reprice_no_band_advance_then_finalizes(self):
        """already_flat at re-price (a fill in the cancel window) → band NOT advanced;
        the next confirmed-flat cycle finalizes."""
        reg = LiveMonitorRegistry()
        _registered(reg, entry=250.0, stop_pct=30)
        client = _FakeClient([_pos(netqty=20, lp=170.0)])
        rp = _RepriceRec([{"squared": True, "via": "already_flat", "remaining": 0}])
        sq, clock = _SquareRec(), _Clock(_NOW)
        g = _l2_guard(reg, client, sq, rp, clock)
        run(g._cycle())
        clock.advance(5.0); run(g._cycle())                   # already_flat
        assert len(rp.calls) == 1
        assert reg.get("ORD1")["square_band_idx"] == 1         # NOT advanced
        client.set([_pos(netqty=0, lp=170.0)])                # broker confirms flat
        run(g._cycle())
        assert len(reg) == 0                                   # finalized

    def test_k_budget_bounds_synchronized_basket(self):
        """A synchronized basket of N squaring legs re-prices at most K per cycle
        (oldest-square_last_ts first → round-robin drain), not all N at once."""
        reg = LiveMonitorRegistry()
        tsyms = [f"T{i}" for i in range(6)]
        for i, ts in enumerate(tsyms):
            reg.register(key=f"K{i}", tsym=ts, exch="BFO", qty=20, prd="I",
                         entry_price=250.0, state=build_monitor_state(250.0, stop_pct=30))
        book = [{"tsym": ts, "exch": "BFO", "netqty": "20", "lp": "170.0", "urmtom": "0"}
                for ts in tsyms]
        client = _FakeClient(book)
        sq, rp, clock = _SquareRec(), _RepriceRec(), _Clock(_NOW)
        g = _l2_guard(reg, client, sq, rp, clock)
        run(g._cycle())                                        # 6 first squares
        assert len(sq.calls) == 6
        clock.advance(5.0); run(g._cycle())                   # K=2 → exactly 2 re-prices
        assert len(rp.calls) == 2
        clock.advance(5.0); run(g._cycle())                   # the 2 oldest untouched legs
        assert len(rp.calls) == 4

    def test_finalize_cancels_orphaned_guard_exit(self):
        """On confirmed-flat, _finalize_flat cancels BOTH the OCO and the tracked
        resting guard exit (square_ordno) — else the SELL is orphaned → naked short if
        the OCO filled first."""
        reg = LiveMonitorRegistry()
        _registered_with_oco(reg, oco_al_id="OCO1", stop_pct=30)
        client = _CancelRecClient([_pos(netqty=20, lp=170.0)])
        sq = _SquareRec({"squared": True, "via": "exit_order", "norenordno": "EXIT0"})
        rp, clock = _RepriceRec(), _Clock(_NOW)
        g = _l2_guard(reg, client, sq, rp, clock)
        run(g._cycle())                                        # first square → square_ordno EXIT0
        assert reg.get("ORD1")["square_ordno"] == "EXIT0"
        client.set([_pos(netqty=0, lp=170.0)])                # OCO filled → flat
        run(g._cycle())
        assert client.cancel_oco_calls == ["OCO1"]
        assert "EXIT0" in client.cancel_order_calls           # orphaned guard exit cancelled
        assert len(reg) == 0

    def test_square_reason_never_mutated_by_reprice(self):
        """square_reason is written once by the first square and never overwritten by a
        re-price; the journal reason on confirmed-flat is the ORIGINAL reason."""
        reg = LiveMonitorRegistry()
        _registered(reg, entry=250.0, stop_pct=30)            # breach → reason "stop"
        client = _FakeClient([_pos(netqty=20, lp=170.0)])
        calls = []

        async def on_close(entry, exit_price, reason, result):
            calls.append(reason)

        sq, rp, clock = _SquareRec(), _RepriceRec(), _Clock(_NOW)
        g = _l2_guard(reg, client, sq, rp, clock, on_close=on_close)
        run(g._cycle())
        clock.advance(5.0); run(g._cycle())                   # reprice 2%
        clock.advance(5.0); run(g._cycle())                   # reprice 4%
        assert reg.get("ORD1")["square_reason"] == "stop"     # never overwritten
        assert rp.calls[0]["reason"] == "stop_reprice"        # local suffix only
        client.set([_pos(netqty=0, lp=170.0)]); run(g._cycle())
        assert calls == ["stop"]                              # journal reason == original

    def test_eod_skips_squaring_while_reprice_escalates(self):
        """Past 15:00 IST, EOD does NOT re-square a `squaring` entry (rate-limit); the
        re-price loop is the sole escalator."""
        reg = LiveMonitorRegistry()
        _registered(reg, entry=250.0, stop_pct=30)
        client = _FakeClient([_pos(netqty=20, lp=170.0)])
        sq, rp, clock = _SquareRec(), _RepriceRec(), _Clock(_EOD_NOW)  # 15:00 IST
        g = _l2_guard(reg, client, sq, rp, clock)
        run(g._cycle())                                        # premium first square; EOD skips
        assert len(sq.calls) == 1                              # not an EOD double-square
        clock.advance(5.0); run(g._cycle())                   # re-price escalates
        assert len(sq.calls) == 1                              # EOD still skips the squaring entry
        assert len(rp.calls) == 1 and rp.calls[0]["band_pct"] == 2.0

    def test_integration_real_primitive_and_executor_full_crash(self):
        """End-to-end with the REAL square_position + reprice_exit_leg + MockNoren (not
        recorders): breach → real first square → real widening re-price (cancels the
        prior exit, places the new) → broker fills → confirmed-flat finalizes + journals
        ONCE. Proves the guard↔primitive contract matches by construction."""
        from app.live.mock_noren import MockNoren
        from app.live.auto_square import reprice_exit_leg, square_position
        reg = LiveMonitorRegistry()
        _registered_with_oco(reg, oco_al_id="OCO1", stop_pct=30)   # stop 175
        client = MockNoren()
        client.set_position_book([{"tsym": _TSYM, "exch": "BFO", "netqty": "20",
                                   "lp": "170", "token": "999"}])
        client.set_quotes({"lp": "170", "bp1": "168", "sp1": "172", "lc": "150", "uc": "190"})
        calls = []

        async def on_close(entry, exit_price, reason, result):
            calls.append(reason)

        async def sqfn(cl, position, *, reason):
            return await square_position(cl, position, reason=reason)

        clock = _Clock(_NOW)
        g = LivePositionGuard(
            registry=reg, client_factory=lambda: _aw(client), square_fn=sqfn,
            reprice_fn=reprice_exit_leg, on_close=on_close, now_fn=clock.now,
            reprice_interval_seconds=4.0, reprice_band_schedule=(1.0, 2.0, 4.0),
            reprice_max_per_cycle=2)
        # Cycle 1: breach → REAL first square (a marketable SELL LMT is placed).
        run(g._cycle())
        e = reg.get("ORD1")
        assert e["squaring"] is True and e["square_ordno"] is not None
        first = e["square_ordno"]
        assert client._orders[first]["status"] == "OPEN" and client._orders[first]["trantype"] == "S"
        # Cycle 2: interval elapsed → REAL re-price at 2% — cancels the prior exit,
        # places a fresh one for the full (unfilled) remaining.
        clock.advance(5.0); run(g._cycle())
        e = reg.get("ORD1")
        assert e["square_band_idx"] == 2 and e["square_ordno"] != first
        assert client._orders[first]["status"] == "CANCELED"       # prior exit cancelled
        assert g.status()["reprices"] == 1
        # The broker fills the escalated exit → position flat → finalize + journal once.
        client.set_position_book([{"tsym": _TSYM, "exch": "BFO", "netqty": "0", "lp": "170"}])
        run(g._cycle())
        assert len(reg) == 0 and calls == ["stop"]
        assert client._orders[e["square_ordno"]]["status"] == "CANCELED"  # resting exit cleaned up

    def test_unpriced_legs_do_not_starve_priceable_legs(self):
        """BUG-2 regression: persistently-`unpriced` squaring legs must NOT monopolize
        the per-cycle K-budget. If `unpriced` never stamped square_last_ts, the two
        unpriced legs would keep the oldest ts forever and be re-selected every cycle,
        starving the priceable leg at the Layer-1 1% band on a blown-through market."""
        reg = LiveMonitorRegistry()
        for k in ["U0", "U1", "P0"]:
            reg.register(key=k, tsym=k, exch="BFO", qty=20, prd="I",
                         entry_price=250.0, state=build_monitor_state(250.0, stop_pct=30))
        book = [{"tsym": t, "exch": "BFO", "netqty": "20", "lp": "170.0", "urmtom": "0"}
                for t in ["U0", "U1", "P0"]]
        client = _FakeClient(book)
        rp = _RepriceByTsym({"U0", "U1"})            # these two are always unpriced
        sq, clock = _SquareRec(), _Clock(_NOW)
        g = _l2_guard(reg, client, sq, rp, clock)    # K=2
        run(g._cycle())                              # 3 first squares
        priced = False
        for _ in range(4):
            clock.advance(5.0)
            run(g._cycle())
            if any(c["tsym"] == "P0" for c in rp.calls):
                priced = True
                break
        assert priced, "priceable leg P0 was starved by unpriced legs monopolizing the K-budget"
