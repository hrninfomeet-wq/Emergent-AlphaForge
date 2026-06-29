import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import numpy as np
import pandas as pd
import pytest

from app.indicators import candle_geometry


def _frame(rows):
    # rows: list of (open, high, low, close)
    return pd.DataFrame(rows, columns=["open", "high", "low", "close"])


def test_body_and_wick_fractions_basic():
    # one bar: o=10 h=14 l=9 c=12 -> range=5, body=2, upper=14-12=2, lower=10-9=1
    df = _frame([(10, 14, 9, 12)])
    g = candle_geometry(df)
    assert g["body_frac"].iloc[0] == pytest.approx(2 / 5)
    assert g["upper_wick_frac"].iloc[0] == pytest.approx(2 / 5)
    assert g["lower_wick_frac"].iloc[0] == pytest.approx(1 / 5)


def test_zero_range_bar_is_safe():
    df = _frame([(10, 10, 10, 10)])
    g = candle_geometry(df)
    assert g["body_frac"].iloc[0] == 0.0
    assert g["upper_wick_frac"].iloc[0] == 0.0
    assert g["lower_wick_frac"].iloc[0] == 0.0


def test_inside_bar_flag():
    # bar1 wide (h=20,l=5); bar2 inside (h<20,l>5); bar3 not inside (h>prev h)
    df = _frame([(10, 20, 5, 12), (11, 18, 7, 14), (12, 25, 8, 20)])
    g = candle_geometry(df)
    assert bool(g["inside_bar"].iloc[0]) is False     # first bar: no prev
    assert bool(g["inside_bar"].iloc[1]) is True
    assert bool(g["inside_bar"].iloc[2]) is False


def test_close_z_is_nan_during_warmup_then_finite():
    close = pd.Series(np.linspace(100, 110, 80))
    df = pd.DataFrame({"open": close, "high": close + 1, "low": close - 1, "close": close})
    g = candle_geometry(df, z_window=60)
    assert g["close_z"].iloc[:59].isna().all()        # warmup
    assert np.isfinite(g["close_z"].iloc[70])         # finite once full window


def test_geometry_emitted_identically_by_both_paths():
    from app.indicators import precompute_all_indicators
    from app.indicator_groups import run_all_groups
    from tests._adaptive_testutil import make_sessions
    rng = np.random.default_rng(7)
    n = 75  # 75 bars per session to fit typical 09:15-10:30 window
    close_vals = 100 + np.cumsum(rng.normal(0, 1, n * 2))
    # Build 2 sessions so session-dependent indicators have a prior day
    s1 = close_vals[:n].tolist()
    s2 = close_vals[n:].tolist()
    df = make_sessions([s1, s2], start_date="2026-01-05")
    params = {}
    mono = precompute_all_indicators(df.copy(), params)
    cached = run_all_groups(df.copy(), params)
    for col in ["body_frac", "upper_wick_frac", "lower_wick_frac", "inside_bar", "close_z"]:
        assert col in mono.columns and col in cached.columns
        pd.testing.assert_series_equal(
            mono[col], cached[col], check_names=False, check_dtype=True)
