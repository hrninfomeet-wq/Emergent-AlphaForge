import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

import numpy as np
import pandas as pd
import pytest
from app.indicators import velocity_accel, variance_ratio
from tests._adaptive_testutil import make_ohlc


def test_velocity_sign_tracks_direction():
    # noisy uptrend: velocity varies so std>0, z-score is finite and positive
    rng = np.random.default_rng(42)
    closes = 100 + np.cumsum(1.0 + 0.3 * rng.standard_normal(80))  # drift >> noise
    df = make_ohlc(list(closes))
    vel_z, accel_z = velocity_accel(df["close"], vel_n=2, z_window=20)
    assert vel_z.iloc[-1] is not None and not pd.isna(vel_z.iloc[-1])
    assert vel_z.iloc[-1] > 0  # rising price -> positive velocity z


def test_velocity_accel_is_causal():
    closes = list(np.cumsum(np.sin(np.arange(120) / 5.0)) + 100)
    df = make_ohlc(closes)
    full, _ = velocity_accel(df["close"], vel_n=2, z_window=30)
    cut, _ = velocity_accel(df["close"].iloc[:80], vel_n=2, z_window=30)
    assert full.iloc[79] == pytest.approx(cut.iloc[79], rel=1e-9, nan_ok=True)


def test_variance_ratio_trend_gt_1_revert_lt_1():
    trend = make_ohlc(list(range(100, 250)))                      # pure trend
    vr_t, score_t = variance_ratio(trend["close"], q=4, lookback=60)
    assert vr_t.iloc[-1] > 1.0 and score_t.iloc[-1] > 0
    osc = make_ohlc([100 + (3 if i % 2 else -3) for i in range(150)])  # zig-zag revert
    vr_r, score_r = variance_ratio(osc["close"], q=4, lookback=60)
    assert vr_r.iloc[-1] < 1.0 and score_r.iloc[-1] < 0


def test_variance_ratio_is_causal():
    closes = list(np.cumsum(np.random.default_rng(0).standard_normal(200)) + 100)
    df = make_ohlc(closes)
    full, _ = variance_ratio(df["close"], q=4, lookback=60)
    cut, _ = variance_ratio(df["close"].iloc[:150], q=4, lookback=60)
    assert full.iloc[149] == pytest.approx(cut.iloc[149], rel=1e-9, nan_ok=True)
