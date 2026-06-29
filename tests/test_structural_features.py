import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import numpy as np
import pandas as pd
import pytest

from app.features.registry import FEATURE_REGISTRY, resolve_features, materialize_features
import app.features.catalog  # noqa: F401  -> importing registers seed features


def _ohlcv(n=400, seed=3):
    # Build a realistic multi-session frame enrichment accepts. Adapt to the real
    # make_sessions signature in tests/_adaptive_testutil.py.
    from tests._adaptive_testutil import make_sessions
    rng = np.random.default_rng(seed)
    half = n // 2
    closes = 100 + np.cumsum(rng.normal(0, 1.0, n))
    return make_sessions([closes[:half].tolist(), closes[half:].tolist()],
                         start_date="2026-01-05")


def _enrich(df, params):
    from app.indicator_groups import run_all_groups
    return run_all_groups(df.copy(), params)


def _materialize(df, params, required):
    return materialize_features(df.reset_index(drop=True), params, required, {})


# ---------------------------------------------------------------------------
# FEATURE 1 — swing_levels
# ---------------------------------------------------------------------------

def test_swing_levels_registered():
    assert "swing_levels" in FEATURE_REGISTRY
    g = FEATURE_REGISTRY["swing_levels"]
    assert set(g.columns) == {
        "last_swing_high_level", "last_swing_low_level",
        "swing_high_swept", "swing_low_swept"}
    assert g.cost_class == "vectorized"
    assert g.stateful_unbounded is False and g.session_anchored is False


def test_swing_levels_values_and_causality():
    params = {"swing_lookback": 5}
    df = _enrich(_ohlcv(), params)
    out = _materialize(df, params, ["swing_levels"])
    is_sh = df["is_swing_high"].reset_index(drop=True)
    expected = df["high"].reset_index(drop=True).where(is_sh).ffill().shift(1)
    pd.testing.assert_series_equal(
        out["last_swing_high_level"], expected, check_names=False)
    assert not (out["swing_high_swept"] & out["last_swing_high_level"].isna()).any()


def test_swing_levels_is_causal_under_truncation():
    params = {"swing_lookback": 5}
    full = _enrich(_ohlcv(), params)
    i = 250
    out_full = _materialize(full, params, ["swing_levels"])
    trunc = _enrich(_ohlcv().iloc[: i + 1], params)
    out_trunc = _materialize(trunc, params, ["swing_levels"])
    for col in ["last_swing_high_level", "last_swing_low_level"]:
        a = out_full[col].iloc[i]
        b = out_trunc[col].iloc[i]
        assert (pd.isna(a) and pd.isna(b)) or a == pytest.approx(b)


# ---------------------------------------------------------------------------
# FEATURE 2 — premium_discount
# ---------------------------------------------------------------------------

def test_premium_discount_values():
    params = {"swing_lookback": 5}
    df = _enrich(_ohlcv(), params)
    out = _materialize(df, params, ["premium_discount"])
    hi = out["last_swing_high_level"]
    lo = out["last_swing_low_level"]
    rng = (hi - lo)
    valid = rng > 0
    exp = 100 * (df["close"].reset_index(drop=True) - lo) / rng.where(valid, np.nan)
    pd.testing.assert_series_equal(
        out["premium_discount_pct"], exp, check_names=False)
    prem = out["range_state"] == "premium"
    assert (out.loc[prem, "premium_discount_pct"] > 55).all()


def test_premium_discount_requires_swing_levels_auto_resolved():
    groups = [g.name for g in resolve_features(["premium_discount"])]
    assert groups.index("swing_levels") < groups.index("premium_discount")


# ---------------------------------------------------------------------------
# FEATURE 3 — displacement + BOS
# ---------------------------------------------------------------------------

def test_displacement_and_bos():
    params = {"swing_lookback": 5, "disp_atr_mult": 1.5, "disp_body_frac_min": 0.5}
    df = _enrich(_ohlcv(), params)
    out = _materialize(df, params, ["displacement"])
    atr = df["atr"].reset_index(drop=True)
    o = df["open"].reset_index(drop=True)
    c = df["close"].reset_index(drop=True)
    h = df["high"].reset_index(drop=True)
    l = df["low"].reset_index(drop=True)
    body = (c - o).abs()
    rng = (h - l)
    exp_disp = (body >= 1.5 * atr) & ((body / rng.where(rng > 0, np.nan)) >= 0.5)
    exp_disp = exp_disp.fillna(False)
    pd.testing.assert_series_equal(
        out["displacement"].astype(bool), exp_disp.astype(bool), check_names=False)
    assert (out.loc[out["bos_up"], "close"]
            > out.loc[out["bos_up"], "last_swing_high_level"]).all()


def test_displacement_param_keys():
    g = FEATURE_REGISTRY["displacement"]
    assert set(g.param_keys) == {"disp_atr_mult", "disp_body_frac_min"}


def test_displacement_true_branch_directly():
    from app.features.structures import compute_displacement
    # 3 bars; bar 1 has a big body (5) vs atr 1.0 and body_frac 5/6 -> displacement True
    df = pd.DataFrame({
        "open":  [100.0, 100.0, 100.0],
        "close": [100.5, 105.0, 100.5],   # bar1 body=5
        "high":  [101.0, 106.0, 101.0],   # bar1 range=6 -> body_frac=5/6>=0.5
        "low":   [100.0, 100.0, 100.0],
        "atr":   [1.0, 1.0, 1.0],
        "last_swing_high_level": [104.0, 104.0, 104.0],
        "last_swing_low_level":  [99.0, 99.0, 99.0],
    })
    out = compute_displacement(df, {"disp_atr_mult": 1.5, "disp_body_frac_min": 0.5})
    assert bool(out["displacement"].iloc[1]) is True      # 5 >= 1.5*1 and 5/6 >= 0.5
    assert bool(out["displacement"].iloc[0]) is False     # 0.5 < 1.5
    assert bool(out["bos_up"].iloc[1]) is True            # close 105 > last_swing_high 104
    assert bool(out["bos_down"].iloc[1]) is False
