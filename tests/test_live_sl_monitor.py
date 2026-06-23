"""Tests for live_sl_monitor — software SL/TP/trailing exits (P1.5).

The pure ``evaluate_exit`` is the audit-critical core: stop/target/trailing/
breakeven decisions for a LONG option, with a MONOTONIC (never-decreasing) stop.
"""
from __future__ import annotations

import asyncio
import math
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.live.live_sl_monitor import (  # noqa: E402
    LiveSLMonitor,
    build_monitor_state,
    evaluate_exit,
)


def run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# build_monitor_state
# ---------------------------------------------------------------------------
class TestBuildMonitorState:
    def test_stop_and_target_pct_to_absolute(self):
        s = build_monitor_state(200.0, stop_pct=30, target_pct=50)
        assert s["entry"] == 200.0
        assert s["initial_stop"] == 140.0      # 200 * 0.70
        assert s["stop_level"] == 140.0
        assert s["target_level"] == 300.0      # 200 * 1.50
        assert s["peak"] == 200.0
        assert s["mode"] == "none"

    def test_stop_pts(self):
        s = build_monitor_state(200.0, stop_pts=40)
        assert s["stop_level"] == 160.0
        assert s["target_level"] is None

    @pytest.mark.parametrize("bad", [0, -1, None, float("nan"), float("inf"), "x", True])
    def test_bad_entry_raises(self, bad):
        with pytest.raises(ValueError):
            build_monitor_state(bad, stop_pct=30)

    def test_no_stop_target_trail_raises(self):
        with pytest.raises(ValueError):
            build_monitor_state(200.0)

    def test_bad_mode_raises(self):
        with pytest.raises(ValueError):
            build_monitor_state(200.0, stop_pct=30, trail={"mode": "rocket"})

    def test_trail_only_is_valid(self):
        s = build_monitor_state(200.0, trail={"mode": "trail", "gap": 20})
        assert s["mode"] == "trail"


# ---------------------------------------------------------------------------
# evaluate_exit — fixed stop / target / stale tick
# ---------------------------------------------------------------------------
class TestEvaluateExitBasic:
    def _state(self, **kw):
        return build_monitor_state(200.0, stop_pct=30, target_pct=50, **kw)

    def test_stop_hit(self):
        v = evaluate_exit(self._state(), 139.0)
        assert v["exit"] is True
        assert v["reason"] == "stop"

    def test_stop_hit_exact(self):
        v = evaluate_exit(self._state(), 140.0)   # ltp == stop
        assert v["exit"] is True

    def test_target_hit(self):
        v = evaluate_exit(self._state(), 300.0)
        assert v["exit"] is True
        assert v["reason"] == "target"

    def test_no_exit_in_band(self):
        v = evaluate_exit(self._state(), 250.0)
        assert v["exit"] is False
        assert v["state"]["peak"] == 250.0

    @pytest.mark.parametrize("bad", [None, float("nan"), float("inf"), 0, -5, "x"])
    def test_stale_tick_never_exits(self, bad):
        v = evaluate_exit(self._state(), bad)
        assert v["exit"] is False
        # peak unchanged on a bad tick
        assert v["state"]["peak"] == 200.0

    def test_input_state_not_mutated(self):
        s = self._state()
        evaluate_exit(s, 250.0)
        assert s["peak"] == 200.0  # original not touched


# ---------------------------------------------------------------------------
# evaluate_exit — trailing modes
# ---------------------------------------------------------------------------
class TestTrailingTrail:
    def _state(self):
        return build_monitor_state(200.0, stop_pct=30, trail={"mode": "trail", "gap": 20})

    def test_stop_trails_up_with_peak(self):
        s = self._state()
        v = evaluate_exit(s, 250.0)          # peak 250 -> stop 230
        assert v["state"]["stop_level"] == 230.0
        assert v["exit"] is False

    def test_trailing_stop_hit(self):
        s = evaluate_exit(self._state(), 250.0)["state"]   # stop now 230
        v = evaluate_exit(s, 229.0)
        assert v["exit"] is True
        assert v["reason"] == "trailing_stop"

    def test_stop_is_monotonic_never_drops(self):
        s = evaluate_exit(self._state(), 250.0)["state"]   # peak 250, stop 230
        # price falls to 240 — stop must STAY 230, not drop to 220
        v = evaluate_exit(s, 240.0)
        assert v["state"]["stop_level"] == 230.0
        assert v["exit"] is False


class TestTrailingBreakeven:
    def _state(self):
        return build_monitor_state(
            200.0, stop_pct=30, trail={"mode": "breakeven", "trigger": 260}
        )

    def test_before_trigger_stop_unchanged(self):
        v = evaluate_exit(self._state(), 250.0)
        assert v["state"]["stop_level"] == 140.0
        assert v["state"]["activated"] is False

    def test_at_trigger_stop_moves_to_entry(self):
        v = evaluate_exit(self._state(), 265.0)
        assert v["state"]["stop_level"] == 200.0   # break-even
        assert v["state"]["activated"] is True

    def test_breakeven_stop_reason(self):
        s = evaluate_exit(self._state(), 265.0)["state"]
        v = evaluate_exit(s, 199.0)
        assert v["exit"] is True
        assert v["reason"] == "breakeven_stop"


class TestTrailingLock:
    def _state(self):
        return build_monitor_state(
            200.0, stop_pct=30,
            trail={"mode": "lock", "trigger": 260, "lock_profit": 30},
        )

    def test_locks_profit_at_trigger(self):
        v = evaluate_exit(self._state(), 265.0)
        assert v["state"]["stop_level"] == 230.0   # entry + 30
        assert v["state"]["activated"] is True

    def test_lock_does_not_drop_after_activation(self):
        s = evaluate_exit(self._state(), 265.0)["state"]   # locked at 230
        v = evaluate_exit(s, 245.0)   # below trigger, still above lock
        assert v["state"]["stop_level"] == 230.0
        assert v["exit"] is False


class TestTrailingLockTrail:
    def _state(self):
        return build_monitor_state(
            200.0, stop_pct=30,
            trail={"mode": "lock_trail", "trigger": 260, "lock_profit": 20,
                   "step": 10, "raise_by": 10},
        )

    def test_locks_then_ratchets_by_step(self):
        s = evaluate_exit(self._state(), 260.0)["state"]   # lock at 220, 0 steps
        assert s["stop_level"] == 220.0
        v = evaluate_exit(s, 285.0)   # +25 over trigger -> 2 steps -> +20
        assert v["state"]["stop_level"] == 240.0   # 200 + 20 + 2*10

    def test_ratchet_is_monotonic(self):
        s = evaluate_exit(self._state(), 285.0)["state"]   # stop 240
        v = evaluate_exit(s, 265.0)   # back near trigger -> would compute 220
        assert v["state"]["stop_level"] == 240.0   # never drops


# ---------------------------------------------------------------------------
# LiveSLMonitor — cycle wiring
# ---------------------------------------------------------------------------
class _Recorder:
    def __init__(self):
        self.squared = []
        self.removed = []

    async def square_fn(self, position, *, reason):
        self.squared.append((position, reason))
        return {"squared": True, "reason": reason}

    def remove_fn(self, pid):
        self.removed.append(pid)


def _monitor(positions, ltp_map, rec):
    return LiveSLMonitor(
        positions_factory=lambda: positions,
        ltp_lookup_factory=lambda: (lambda tsym: ltp_map.get(tsym)),
        square_fn=rec.square_fn,
        remove_fn=rec.remove_fn,
    )


class TestMonitorCycle:
    def _pos(self):
        return [{
            "id": "p1",
            "tsym": "NIFTY26JUN26C25000",
            "state": build_monitor_state(200.0, stop_pct=30, target_pct=50),
            "position": {"tsym": "NIFTY26JUN26C25000", "netqty": 65, "lp": 139.0},
        }]

    def test_stop_triggers_square_once(self):
        rec = _Recorder()
        positions = self._pos()
        mon = _monitor(positions, {"NIFTY26JUN26C25000": 139.0}, rec)
        exits = run(mon._cycle())
        assert len(rec.squared) == 1
        pos, reason = rec.squared[0]
        assert reason == "stop"
        assert pos["netqty"] == 65
        assert rec.removed == ["p1"]   # removed before squaring (no double-square)
        assert len(exits) == 1
        assert mon.status()["exits"] == 1

    def test_no_trigger_no_square(self):
        rec = _Recorder()
        positions = self._pos()
        mon = _monitor(positions, {"NIFTY26JUN26C25000": 250.0}, rec)
        run(mon._cycle())
        assert rec.squared == []
        assert rec.removed == []
        # trailing/peak state was still updated
        assert positions[0]["state"]["peak"] == 250.0

    def test_cycle_never_raises_on_lookup_error(self):
        rec = _Recorder()

        def boom_lookup():
            def _l(tsym):
                raise RuntimeError("feed down")
            return _l

        mon = LiveSLMonitor(
            positions_factory=lambda: self._pos(),
            ltp_lookup_factory=boom_lookup,
            square_fn=rec.square_fn,
            remove_fn=rec.remove_fn,
        )
        exits = run(mon._cycle())   # must not raise
        assert exits == []
        assert rec.squared == []

    def test_target_triggers_square(self):
        rec = _Recorder()
        positions = self._pos()
        mon = _monitor(positions, {"NIFTY26JUN26C25000": 305.0}, rec)
        run(mon._cycle())
        assert len(rec.squared) == 1
        assert rec.squared[0][1] == "target"
