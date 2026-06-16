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


from app.indicators import bollinger, keltner, squeeze


def test_squeeze_on_during_compression_then_fires():
    flat = [100 + 0.05 * np.sin(i) for i in range(60)]   # tight range -> squeeze on
    burst = list(np.linspace(100, 130, 30))              # expansion -> fires
    df = make_ohlc(flat + burst, high_pad=0.2, low_pad=0.2)
    on, fire, mom = squeeze(df, bb_len=20, bb_mult=2.0, kc_len=20, kc_atr_mult=1.5, mom_len=20)
    assert on.iloc[40]            # compressed during the flat stretch
    assert fire.iloc[60:75].any() # fires at/after the expansion onset
    assert mom.iloc[75] > 0       # up-expansion -> positive momentum


def test_squeeze_fire_is_single_bar_edge():
    df = make_ohlc([100 + 0.05 * np.sin(i) for i in range(40)] + list(range(100, 140)))
    on, fire, _ = squeeze(df)
    # fire only where prior bar was on and this bar is off
    expected = on.shift(1, fill_value=False) & (~on)
    assert (fire == expected).all()


from app.indicators import supertrend


def test_supertrend_dir_flips_with_trend():
    df = make_ohlc(list(range(100, 160)) + list(range(160, 100, -1)))
    st, d = supertrend(df, period=10, mult=3.0)
    assert d.iloc[40] == 1     # uptrend -> long
    assert d.iloc[-1] == -1    # downtrend -> short


def test_supertrend_is_causal():
    closes = list(np.cumsum(np.random.default_rng(1).standard_normal(150)) + 100)
    df = make_ohlc(closes)
    st_full, d_full = supertrend(df, period=10, mult=3.0)
    st_cut, d_cut = supertrend(df.iloc[:120], period=10, mult=3.0)
    assert int(d_full.iloc[119]) == int(d_cut.iloc[119])


from app.indicators import vwap_sigma_bands, nr7
from tests._adaptive_testutil import make_sessions
from app.indicators import session_vwap


def test_vwap_sigma_bands_widen_with_dispersion():
    df = make_sessions([[100, 101, 99, 103, 97, 105, 95]])  # one volatile session
    df["vwap"] = session_vwap(df)  # price-based (no volume) fallback
    sigma, u1, u2, l1, l2 = vwap_sigma_bands(df)
    assert (u2 >= u1).all() and (l2 <= l1).all()
    assert sigma.iloc[-1] > 0


def test_nr7_flags_session_after_narrow_day():
    wide = list(range(100, 130))                      # big range
    narrow = [110 + 0.1 * (i % 2) for i in range(30)] # tiny range (NR)
    after = list(range(110, 140))
    df = make_sessions([wide, wide, wide, wide, wide, wide, narrow, after])
    flag = nr7(df)
    last_date = df["session_date"].iloc[-1]
    assert flag[df["session_date"] == last_date].iloc[0]  # day after the NR is flagged


def test_nr7_does_not_use_today_full_range():
    # today's flag must equal the prior session's NR status, independent of how
    # today's later bars extend the range -> causal.
    df = make_sessions([[100]*30, [100]*30, [100]*30, [100]*30, [100]*30,
                        [100]*30, [100 + 0.1*(i%2) for i in range(30)], list(range(100, 200))])
    full = nr7(df)
    cut = nr7(df.iloc[: len(df) - 50])  # truncate today's tail
    last_date = df["session_date"].iloc[-1]
    a = full[df["session_date"] == last_date].iloc[0]
    b = cut[cut.index][df["session_date"].iloc[: len(cut)] == last_date]
    assert bool(a) == bool(b.iloc[0])
