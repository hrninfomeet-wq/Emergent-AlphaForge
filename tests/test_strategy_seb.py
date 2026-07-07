import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

import pandas as pd
import pytest
from app.strategies.plugins.squeeze_expansion_breakout import SqueezeExpansionBreakout


def _row(**kw):
    base = {"atr": 10.0, "accel_z": 1.5, "ist_time": "10:00", "tod_tradeable": True,
            "squeeze_on": False, "squeeze_fire": True, "sqz_mom": 2.0,
            "vwap": 100.0, "close": 105.0, "nr7": False}
    base.update(kw)
    return pd.Series(base)


def test_seb_registers_and_merges_params():
    s = SqueezeExpansionBreakout()
    assert s.id == "squeeze_expansion_breakout"
    assert "k_acc" in s.parameter_schema and "min_coil_bars" in s.parameter_schema
    assert s.supported_instruments == ["NIFTY", "SENSEX"]


def test_seb_fire_up_emits_CE_with_atr_exits():
    s = SqueezeExpansionBreakout()
    p = s.default_params()
    sig = s.evaluate(_row(), _row(sqz_mom=1.0), p, {})
    assert sig.direction == "CE"
    assert sig.spot_target_pts == pytest.approx(p["t_atr"] * 10.0)
    assert sig.spot_stop_pts == pytest.approx(p["s_atr"] * 10.0)


def test_seb_fire_down_emits_PE():
    s = SqueezeExpansionBreakout()
    p = s.default_params()
    sig = s.evaluate(_row(sqz_mom=-2.0, close=95.0, accel_z=-1.5), _row(sqz_mom=-1.0), p, {})
    assert sig.direction == "PE"


def test_seb_no_fire_is_none():
    s = SqueezeExpansionBreakout()
    p = s.default_params()
    sig = s.evaluate(_row(squeeze_fire=False, squeeze_on=True), None, p, {})
    assert sig.direction == "NONE"


def test_seb_weak_accel_blocked_by_speed_gate():
    s = SqueezeExpansionBreakout()
    p = s.default_params()
    sig = s.evaluate(_row(accel_z=0.1), None, p, {})  # below k_acc -> base blocks
    assert sig.direction == "NONE" and "speed gate" in sig.blockers
