"""Tests for overall_controls — basket-level SL/target/trailing/re-entry (AlgoTest parity).

The pure ``build_overall_state`` + ``evaluate_overall`` are the audit-critical core:
basket-aggregate stop / target / trailing decisions with a MONOTONIC (never-lowering)
trailing floor, plus a pure ``consume_reentry`` budget decrementer.

Semantics mirror AlgoTest, evaluated on the BASKET aggregate MTM (₹):
- premium_pct threshold = value/100 * basket_premium.
- Trailing floor is monotonic non-decreasing (ratchets up, never hands back locked profit).
- A non-finite / None mtm is a stale tick → NO exit, state unchanged.
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.live.overall_controls import (  # noqa: E402
    build_overall_state,
    consume_reentry,
    evaluate_overall,
)


def _cfg(**over):
    """A minimal valid config; override sub-objects via kwargs."""
    base = {
        "sl": {"enabled": False, "mode": "mtm", "value": 0},
        "target": {"enabled": False, "mode": "mtm", "value": 0},
        "trailing": {
            "mode": "none",
            "unit": "mtm",
            "lock_at": 0,
            "lock_floor": 0,
            "trail_per": 0,
            "trail_by": 0,
            "base_sl": 0,
        },
        "reentry": {"enabled": False, "max": 0, "type": "asap", "reverse": False, "momentum_pct": 0},
    }
    base.update(over)
    return base


# ---------------------------------------------------------------------------
# build_overall_state
# ---------------------------------------------------------------------------
class TestBuildOverallState:
    def test_sl_mtm_mode_absolute(self):
        s = build_overall_state(_cfg(sl={"enabled": True, "mode": "mtm", "value": 5000}), basket_premium=10000)
        # SL stored as a SIGNED ₹ level (a loss → negative threshold)
        assert s["sl_level"] == -5000.0
        assert s["target_level"] is None
        assert s["floor"] is None
        assert s["activated"] is False
        assert s["peak_mtm"] == 0.0

    def test_sl_premium_pct_resolved_to_rupees(self):
        # 30% of basket_premium 10000 = 3000 loss → -3000
        s = build_overall_state(
            _cfg(sl={"enabled": True, "mode": "premium_pct", "value": 30}),
            basket_premium=10000,
        )
        assert s["sl_level"] == -3000.0

    def test_target_premium_pct_resolved_to_rupees(self):
        # 50% of 10000 = 5000 profit
        s = build_overall_state(
            _cfg(target={"enabled": True, "mode": "premium_pct", "value": 50}),
            basket_premium=10000,
        )
        assert s["target_level"] == 5000.0
        assert s["sl_level"] is None

    def test_target_mtm_mode(self):
        s = build_overall_state(_cfg(target={"enabled": True, "mode": "mtm", "value": 8000}), basket_premium=10000)
        assert s["target_level"] == 8000.0

    def test_overall_trail_seeds_base_sl_as_negative_floor_stop(self):
        s = build_overall_state(
            _cfg(trailing={
                "mode": "overall_trail", "unit": "mtm",
                "lock_at": 0, "lock_floor": 0,
                "trail_per": 1000, "trail_by": 500, "base_sl": 2000,
            }),
            basket_premium=10000,
        )
        # overall_trail: initial stop = -base_sl
        assert s["sl_level"] == -2000.0
        assert s["trailing"]["mode"] == "overall_trail"

    def test_disabled_everything_raises(self):
        with pytest.raises(ValueError):
            build_overall_state(_cfg(), basket_premium=10000)

    def test_bad_sl_mode_raises(self):
        with pytest.raises(ValueError):
            build_overall_state(
                _cfg(sl={"enabled": True, "mode": "bogus", "value": 1000}),
                basket_premium=10000,
            )

    def test_bad_trailing_mode_raises(self):
        with pytest.raises(ValueError):
            build_overall_state(
                _cfg(trailing={
                    "mode": "wat", "unit": "mtm", "lock_at": 100, "lock_floor": 50,
                    "trail_per": 0, "trail_by": 0, "base_sl": 0,
                }),
                basket_premium=10000,
            )

    def test_premium_pct_requires_positive_basket_premium(self):
        with pytest.raises(ValueError):
            build_overall_state(
                _cfg(sl={"enabled": True, "mode": "premium_pct", "value": 30}),
                basket_premium=0,
            )


# ---------------------------------------------------------------------------
# evaluate_overall — SL
# ---------------------------------------------------------------------------
class TestOverallSL:
    def test_sl_mtm_hit(self):
        s = build_overall_state(_cfg(sl={"enabled": True, "mode": "mtm", "value": 5000}), basket_premium=10000)
        v = evaluate_overall(s, -5000.0)
        assert v["exit"] is True
        assert v["reason"] == "overall_sl"

    def test_sl_mtm_just_above_no_exit(self):
        s = build_overall_state(_cfg(sl={"enabled": True, "mode": "mtm", "value": 5000}), basket_premium=10000)
        v = evaluate_overall(s, -4999.99)
        assert v["exit"] is False
        assert v["reason"] is None

    def test_sl_premium_pct_hit(self):
        s = build_overall_state(
            _cfg(sl={"enabled": True, "mode": "premium_pct", "value": 30}),
            basket_premium=10000,
        )
        # threshold = -3000
        assert evaluate_overall(s, -3000.0)["exit"] is True
        assert evaluate_overall(s, -2999.0)["exit"] is False


# ---------------------------------------------------------------------------
# evaluate_overall — target
# ---------------------------------------------------------------------------
class TestOverallTarget:
    def test_target_hit(self):
        s = build_overall_state(_cfg(target={"enabled": True, "mode": "mtm", "value": 8000}), basket_premium=10000)
        v = evaluate_overall(s, 8000.0)
        assert v["exit"] is True
        assert v["reason"] == "overall_target"

    def test_target_just_below_no_exit(self):
        s = build_overall_state(_cfg(target={"enabled": True, "mode": "mtm", "value": 8000}), basket_premium=10000)
        assert evaluate_overall(s, 7999.99)["exit"] is False


# ---------------------------------------------------------------------------
# evaluate_overall — trailing: lock
# ---------------------------------------------------------------------------
class TestLock:
    def _state(self):
        return build_overall_state(
            _cfg(trailing={
                "mode": "lock", "unit": "mtm",
                "lock_at": 5000, "lock_floor": 3000,
                "trail_per": 0, "trail_by": 0, "base_sl": 0,
            }),
            basket_premium=10000,
        )

    def test_lock_not_activated_below_lock_at(self):
        s = self._state()
        v = evaluate_overall(s, 4000.0)
        assert v["exit"] is False
        assert v["state"]["activated"] is False
        assert v["state"]["floor"] is None

    def test_lock_activates_at_lock_at(self):
        s = self._state()
        v = evaluate_overall(s, 5000.0)
        assert v["exit"] is False
        assert v["state"]["activated"] is True
        assert v["state"]["floor"] == 3000.0

    def test_lock_floor_holds_then_exits_when_below(self):
        s = self._state()
        s = evaluate_overall(s, 6000.0)["state"]   # activate, floor=3000
        assert s["activated"] is True
        # drift down but still above floor → no exit, floor unchanged
        v = evaluate_overall(s, 3500.0)
        assert v["exit"] is False
        assert v["state"]["floor"] == 3000.0
        # cross below floor → exit
        v2 = evaluate_overall(v["state"], 2999.0)
        assert v2["exit"] is True
        assert v2["reason"] == "overall_trailing"

    def test_lock_floor_is_monotonic_once_activated(self):
        s = self._state()
        s = evaluate_overall(s, 5000.0)["state"]   # floor=3000
        # mtm dips below lock_at again — floor must NOT drop / deactivate
        s2 = evaluate_overall(s, 100.0)["state"]
        assert s2["activated"] is True
        assert s2["floor"] == 3000.0


# ---------------------------------------------------------------------------
# evaluate_overall — trailing: lock_trail
# ---------------------------------------------------------------------------
class TestLockTrail:
    def _state(self):
        # lock_at=5000 → floor=2000; then +1000 per 1000 profit above lock_at
        return build_overall_state(
            _cfg(trailing={
                "mode": "lock_trail", "unit": "mtm",
                "lock_at": 5000, "lock_floor": 2000,
                "trail_per": 1000, "trail_by": 1000, "base_sl": 0,
            }),
            basket_premium=10000,
        )

    def test_floor_at_activation(self):
        s = self._state()
        v = evaluate_overall(s, 5000.0)
        assert v["state"]["activated"] is True
        assert v["state"]["floor"] == 2000.0

    def test_floor_ratchets_by_trail_by_per_trail_per(self):
        s = self._state()
        # mtm 7500 → steps = floor((7500-5000)/1000)=2 → floor = 2000 + 2*1000 = 4000
        v = evaluate_overall(s, 7500.0)
        assert v["state"]["floor"] == 4000.0

    def test_floor_monotonic_when_price_falls(self):
        s = self._state()
        s = evaluate_overall(s, 8000.0)["state"]   # steps=3 → floor=5000
        assert s["floor"] == 5000.0
        # price falls back to 6000 → naive step would be 1 (floor 3000) but MONOTONIC: stays 5000
        v = evaluate_overall(s, 6000.0)
        assert v["state"]["floor"] == 5000.0
        assert v["exit"] is False
        # falls below the locked 5000 floor → exit
        v2 = evaluate_overall(v["state"], 4999.0)
        assert v2["exit"] is True
        assert v2["reason"] == "overall_trailing"


# ---------------------------------------------------------------------------
# evaluate_overall — trailing: overall_trail
# ---------------------------------------------------------------------------
class TestOverallTrail:
    def _state(self):
        # sl = -base_sl + floor(max(0,mtm)/trail_per)*trail_by
        return build_overall_state(
            _cfg(trailing={
                "mode": "overall_trail", "unit": "mtm",
                "lock_at": 0, "lock_floor": 0,
                "trail_per": 1000, "trail_by": 500, "base_sl": 2000,
            }),
            basket_premium=10000,
        )

    def test_initial_stop_is_neg_base_sl(self):
        s = self._state()
        assert s["sl_level"] == -2000.0

    def test_sl_rises_with_profit(self):
        s = self._state()
        # mtm 3000 → floor(3000/1000)=3 steps → sl = -2000 + 3*500 = -500
        v = evaluate_overall(s, 3000.0)
        assert v["exit"] is False
        assert v["state"]["sl_level"] == -500.0

    def test_sl_can_go_positive(self):
        s = self._state()
        # mtm 5000 → 5 steps → sl = -2000 + 2500 = 500
        v = evaluate_overall(s, 5000.0)
        assert v["state"]["sl_level"] == 500.0

    def test_sl_monotonic_when_price_falls(self):
        s = self._state()
        s = evaluate_overall(s, 6000.0)["state"]  # 6 steps → sl = -2000 + 3000 = 1000
        assert s["sl_level"] == 1000.0
        v = evaluate_overall(s, 2000.0)  # naive → -1000, but monotonic: stays 1000
        assert v["state"]["sl_level"] == 1000.0
        # mtm 2000 > sl 1000 → no exit
        assert v["exit"] is False
        # mtm drops to the stop → exit (mtm <= sl)
        v2 = evaluate_overall(v["state"], 1000.0)
        assert v2["exit"] is True
        assert v2["reason"] == "overall_trailing"

    def test_initial_base_sl_exit(self):
        s = self._state()
        v = evaluate_overall(s, -2000.0)
        assert v["exit"] is True
        assert v["reason"] == "overall_trailing"


# ---------------------------------------------------------------------------
# evaluate_overall — purity / staleness
# ---------------------------------------------------------------------------
class TestPurityAndStale:
    def test_stale_none_mtm_never_exits_state_unchanged(self):
        s = build_overall_state(_cfg(sl={"enabled": True, "mode": "mtm", "value": 5000}), basket_premium=10000)
        v = evaluate_overall(s, None)
        assert v["exit"] is False
        assert v["reason"] is None
        assert v["state"] == s

    def test_stale_nan_mtm_never_exits(self):
        s = build_overall_state(_cfg(sl={"enabled": True, "mode": "mtm", "value": 5000}), basket_premium=10000)
        v = evaluate_overall(s, float("nan"))
        assert v["exit"] is False
        v2 = evaluate_overall(s, float("inf"))
        assert v2["exit"] is False

    def test_input_state_not_mutated(self):
        s = build_overall_state(
            _cfg(trailing={
                "mode": "lock_trail", "unit": "mtm",
                "lock_at": 5000, "lock_floor": 2000,
                "trail_per": 1000, "trail_by": 1000, "base_sl": 0,
            }),
            basket_premium=10000,
        )
        import copy
        before = copy.deepcopy(s)
        v = evaluate_overall(s, 8000.0)
        # input untouched
        assert s == before
        assert s["floor"] is None
        # output is a new object with the mutation
        assert v["state"] is not s
        assert v["state"]["floor"] == 5000.0

    def test_peak_mtm_updates_and_is_max(self):
        s = build_overall_state(_cfg(target={"enabled": True, "mode": "mtm", "value": 999999}), basket_premium=10000)
        s = evaluate_overall(s, 1200.0)["state"]
        assert s["peak_mtm"] == 1200.0
        s = evaluate_overall(s, 800.0)["state"]
        assert s["peak_mtm"] == 1200.0  # peak only rises


# ---------------------------------------------------------------------------
# consume_reentry
# ---------------------------------------------------------------------------
class TestReentry:
    def test_disabled_disallows(self):
        rs = {"enabled": False, "max": 3, "type": "asap", "reverse": False, "momentum_pct": 0}
        out = consume_reentry(rs)
        assert out["allow"] is False
        assert out["remaining"] == 0

    def test_budget_decrements_to_zero_then_disallows(self):
        rs = {"enabled": True, "max": 2, "type": "asap", "reverse": False, "momentum_pct": 0}
        out1 = consume_reentry(rs)
        assert out1["allow"] is True
        assert out1["remaining"] == 1

        out2 = consume_reentry(out1["state"])
        assert out2["allow"] is True
        assert out2["remaining"] == 0

        out3 = consume_reentry(out2["state"])
        assert out3["allow"] is False
        assert out3["remaining"] == 0

    def test_max_capped_at_5(self):
        rs = {"enabled": True, "max": 99, "type": "asap", "reverse": False, "momentum_pct": 0}
        out = consume_reentry(rs)
        # the budget can never exceed 5; after 1 consumption, 4 remain
        assert out["remaining"] == 4

    def test_carries_type_reverse_momentum(self):
        rs = {"enabled": True, "max": 3, "type": "momentum", "reverse": True, "momentum_pct": 12.5}
        out = consume_reentry(rs)
        assert out["state"]["type"] == "momentum"
        assert out["state"]["reverse"] is True
        assert out["state"]["momentum_pct"] == 12.5

    def test_input_state_not_mutated(self):
        rs = {"enabled": True, "max": 3, "type": "asap", "reverse": False, "momentum_pct": 0}
        import copy
        before = copy.deepcopy(rs)
        consume_reentry(rs)
        assert rs == before
