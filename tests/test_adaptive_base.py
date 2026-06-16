import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

import pandas as pd
import pytest
from app.strategies.adaptive_base import AdaptiveStrategyBase, BASE_PARAMS


class _Mom(AdaptiveStrategyBase):
    id = "_test_mom"
    name = "t"
    extra_params = {"foo": {"type": "int", "min": 1, "max": 9, "default": 3}}

    def _core_signal(self, row, prev, params, ctx):
        return ("CE", 70, ["x"], [], "momentum")


def _row(**kw):
    base = {"atr": 10.0, "accel_z": 1.5, "ist_time": "10:00", "tod_tradeable": True}
    base.update(kw)
    return pd.Series(base)


def test_merges_base_and_extra_params():
    s = _Mom()
    assert "k_acc" in s.parameter_schema and "foo" in s.parameter_schema


def test_momentum_speed_gate_blocks_weak_accel():
    s = _Mom()
    p = s.default_params()
    ok = s.evaluate(_row(accel_z=1.5), None, p, {})
    assert ok.direction == "CE" and ok.spot_target_pts == pytest.approx(p["t_atr"] * 10.0, rel=1e-6)
    blocked = s.evaluate(_row(accel_z=0.1), None, p, {})
    assert blocked.direction == "NONE" and "speed gate" in blocked.blockers


def test_time_gate_blocks_after_cutoff_and_dead_bucket():
    s = _Mom()
    p = s.default_params()
    assert s.evaluate(_row(ist_time="14:30"), None, p, {}).direction == "NONE"
    assert s.evaluate(_row(tod_tradeable=False), None, p, {}).direction == "NONE"


def test_reversion_speed_gate_allows_turning_accel():
    class _Rev(_Mom):
        id = "_test_rev"
        def _core_signal(self, row, prev, params, ctx):
            return ("CE", 70, [], [], "reversion")
    s = _Rev()
    p = s.default_params()
    # reversion CE allowed when accel not strongly negative (>= -k_acc_fade)
    assert s.evaluate(_row(accel_z=-0.1), None, p, {}).direction == "CE"
    assert s.evaluate(_row(accel_z=-2.0), None, p, {}).direction == "NONE"


def test_pe_speed_gate_momentum_and_reversion():
    # PE branch of the mode-aware speed gate (subclass the base DIRECTLY).
    class _MomPE(AdaptiveStrategyBase):
        id = "_test_mom_pe"
        name = "t"

        def _core_signal(self, row, prev, params, ctx):
            return ("PE", 70, [], [], "momentum")

    class _RevPE(AdaptiveStrategyBase):
        id = "_test_rev_pe"
        name = "t"

        def _core_signal(self, row, prev, params, ctx):
            return ("PE", 70, [], [], "reversion")

    mom, rev = _MomPE(), _RevPE()
    pm, pr = mom.default_params(), rev.default_params()
    # momentum PE: needs accel_z <= -k_acc (accelerating down, with the trade)
    assert mom.evaluate(_row(accel_z=-1.5), None, pm, {}).direction == "PE"
    assert mom.evaluate(_row(accel_z=-0.1), None, pm, {}).direction == "NONE"
    # reversion PE: allowed unless strongly counter (accel_z <= k_acc_fade)
    assert rev.evaluate(_row(accel_z=0.1), None, pr, {}).direction == "PE"
    assert rev.evaluate(_row(accel_z=2.0), None, pr, {}).direction == "NONE"
