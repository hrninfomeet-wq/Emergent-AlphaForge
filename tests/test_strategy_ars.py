import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

import pandas as pd
import pytest
from app.strategies.plugins.adaptive_regime_scalper import AdaptiveRegimeScalper


def _row(**kw):
    base = {"atr": 10.0, "accel_z": 1.5, "ist_time": "10:00", "tod_tradeable": True,
            "regime_score": 0.5, "st_dir": 1, "vwap": 100.0, "vwap_l2": 92.0, "vwap_u2": 108.0,
            "close": 105.0, "cpr_tc": 102.0, "cpr_bc": 98.0, "day_type": "TREND"}
    base.update(kw)
    return pd.Series(base)


def test_ars_registers_and_merges_params():
    s = AdaptiveRegimeScalper()
    assert s.id == "adaptive_regime_scalper"
    assert "k_acc" in s.parameter_schema and "dead_band" in s.parameter_schema


def test_ars_trend_regime_emits_CE():
    s = AdaptiveRegimeScalper()
    p = s.default_params()
    assert s.evaluate(_row(), None, p, {}).direction == "CE"  # VR>1 trend, ST up, reclaim


def test_ars_fade_regime_emits_CE_reversion():
    s = AdaptiveRegimeScalper()
    p = s.default_params()
    # VR<1 range day, price at -2sigma, accel turning (reversion gate allows az >= -k_acc_fade)
    sig = s.evaluate(_row(regime_score=-0.5, day_type="RANGE", st_dir=-1, close=90.0, accel_z=-0.1), None, p, {})
    assert sig.direction == "CE"


def test_ars_stand_aside_in_random_walk():
    s = AdaptiveRegimeScalper()
    p = s.default_params()
    assert s.evaluate(_row(regime_score=0.05, day_type="NEUTRAL", close=100.0), None, p, {}).direction == "NONE"


def test_ars_trend_down_emits_PE():
    s = AdaptiveRegimeScalper()
    p = s.default_params()
    sig = s.evaluate(_row(st_dir=-1, close=95.0, accel_z=-1.5), None, p, {})
    assert sig.direction == "PE"
