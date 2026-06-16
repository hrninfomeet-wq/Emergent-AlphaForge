import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

import numpy as np
import pandas as pd
from app.indicators import precompute_all_indicators
from tests._adaptive_testutil import make_ohlc

NEW_COLS = ["vel_z", "accel_z", "vr", "regime_score", "squeeze_on", "squeeze_fire",
            "sqz_mom", "supertrend", "st_dir", "vwap_sigma", "vwap_u1", "vwap_u2",
            "vwap_l1", "vwap_l2", "nr7", "cpr_p", "cpr_tc", "cpr_bc", "cpr_width_pct",
            "day_type", "tod_tradeable"]


def test_precompute_adds_all_toolkit_columns():
    df = make_ohlc(list(np.cumsum(np.random.default_rng(2).standard_normal(400)) + 100))
    out = precompute_all_indicators(df)
    for c in NEW_COLS:
        assert c in out.columns, f"missing {c}"
    # existing columns still present (no regression)
    for c in ["ema9", "rsi", "vwap", "atr", "fvg", "is_swing_high"]:
        assert c in out.columns


def test_precompute_period_params_change_columns():
    df = make_ohlc(list(np.cumsum(np.random.default_rng(3).standard_normal(400)) + 100))
    a = precompute_all_indicators(df, {"vr_q": 4})
    b = precompute_all_indicators(df, {"vr_q": 12})
    assert not a["vr"].equals(b["vr"])  # vr_q actually flows through
