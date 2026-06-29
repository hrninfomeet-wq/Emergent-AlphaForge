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


# ---------------------------------------------------------------------------
# FEATURE 4 — choch
# ---------------------------------------------------------------------------

def test_choch_flips_on_direction_change():
    from app.features.structures import compute_choch
    df = pd.DataFrame({
        "bos_up":   [False, True, False, False, True, False],
        "bos_down": [False, False, False, True, False, False],
    })
    out = compute_choch(df, {})
    # bar1 first up -> dir +1, no choch (was 0). bar3 down -> choch_down. bar4 up -> choch_up.
    assert out["choch_down"].tolist() == [False, False, False, True, False, False]
    assert out["choch_up"].tolist() == [False, False, False, False, True, False]


def test_choch_is_stateful_unbounded_and_backtest_only():
    from app.features.registry import feature_live_feasible
    g = FEATURE_REGISTRY["choch"]
    assert g.stateful_unbounded is True
    assert feature_live_feasible(g) is False


def test_choch_causal_under_truncation():
    params = {"swing_lookback": 5}
    df = _enrich(_ohlcv(), params)
    out_full = _materialize(df, params, ["choch"])
    i = 200
    df_t = _enrich(_ohlcv().iloc[: i + 1], params)
    out_t = _materialize(df_t, params, ["choch"])
    assert bool(out_full["choch_up"].iloc[i]) == bool(out_t["choch_up"].iloc[i])
    assert bool(out_full["choch_down"].iloc[i]) == bool(out_t["choch_down"].iloc[i])


# ---------------------------------------------------------------------------
# FEATURE 5 — fvg_zones
# ---------------------------------------------------------------------------

def _fvg_reference(df):
    fdir = df.get("fvg")
    if fdir is None:
        from app.indicators import detect_fvg
        fdir = detect_fvg(df)
    fdir = fdir.reset_index(drop=True)
    high = df["high"].reset_index(drop=True).to_numpy()
    low = df["low"].reset_index(drop=True).to_numpy()
    n = len(df)
    top = np.full(n, np.nan); bot = np.full(n, np.nan)
    state = np.array([None] * n, dtype=object)
    direction = np.array([None] * n, dtype=object)
    cur_top = cur_bot = np.nan; cur_dir = None; cur_state = "none"
    for i in range(n):
        d = fdir.iloc[i]
        if d == "UP" and i >= 2:
            cur_bot, cur_top, cur_dir, cur_state = high[i - 2], low[i], "UP", "active"
        elif d == "DOWN" and i >= 2:
            cur_bot, cur_top, cur_dir, cur_state = high[i], low[i - 2], "DOWN", "active"
        elif cur_state == "active":
            if cur_dir == "UP" and low[i] <= cur_bot:
                cur_state = "filled"
            elif cur_dir == "DOWN" and high[i] >= cur_top:
                cur_state = "filled"
        top[i], bot[i], direction[i], state[i] = cur_top, cur_bot, cur_dir, cur_state
    return pd.DataFrame({"fvg_top": top, "fvg_bottom": bot, "fvg_dir": direction,
                         "fvg_state": state})


def test_fvg_zones_matches_reference():
    params = {}
    df = _enrich(_ohlcv(seed=11), params)
    out = _materialize(df, params, ["fvg_zones"])
    ref = _fvg_reference(df)
    pd.testing.assert_series_equal(out["fvg_top"], ref["fvg_top"], check_names=False)
    pd.testing.assert_series_equal(out["fvg_bottom"], ref["fvg_bottom"], check_names=False)
    assert out["fvg_dir"].tolist() == ref["fvg_dir"].tolist()
    assert out["fvg_state"].tolist() == ref["fvg_state"].tolist()
    assert (out["fvg_state"] == "active").any()   # non-vacuity: the fixture forms >=1 gap


def test_fvg_zone_forms_and_fills():
    from app.features.structures import compute_fvg_zones
    # UP FVG at bar2: low[2]=101 > high[0]=100 -> gap bottom=100 top=101
    df = pd.DataFrame({
        "open":  [99.0, 100.5, 101.5, 101.5, 100.0],
        "high":  [100.0, 101.0, 102.0, 102.0, 101.0],
        "low":   [99.0,  100.0, 101.0, 100.5, 99.5],
        "close": [99.5,  100.8, 101.8, 101.0, 99.8],
    })
    out = compute_fvg_zones(df, {})
    assert out["fvg_dir"].iloc[2] == "UP"
    assert out["fvg_bottom"].iloc[2] == 100.0
    assert out["fvg_top"].iloc[2] == 101.0
    assert out["fvg_ce"].iloc[2] == 100.5
    assert out["fvg_state"].iloc[2] == "active"
    assert out["fvg_state"].iloc[4] == "filled"   # bar4 low 99.5 <= bottom 100


def test_fvg_down_zone_forms_and_fills():
    from app.features.structures import compute_fvg_zones
    # DOWN FVG at bar2: high[2]=99.0 < low[0]=100.0 -> bottom=high[2]=99, top=low[0]=100
    df = pd.DataFrame({
        "open":  [101.0, 100.0, 99.0,  99.2,  100.5],
        "high":  [101.0, 100.5, 99.0,  99.4,  100.5],
        "low":   [100.0, 99.0,  98.5,  99.0,  98.5],
        "close": [100.5, 99.5,  98.8,  99.3,  100.2],
    })
    out = compute_fvg_zones(df, {})
    assert out["fvg_dir"].iloc[2] == "DOWN"
    assert out["fvg_bottom"].iloc[2] == 99.0
    assert out["fvg_top"].iloc[2] == 100.0
    assert out["fvg_state"].iloc[2] == "active"
    assert out["fvg_state"].iloc[4] == "filled"   # DOWN filled when high>=top: bar4 high 100.5 >= 100


def test_fvg_zones_backtest_only():
    from app.features.registry import feature_live_feasible
    assert feature_live_feasible(FEATURE_REGISTRY["fvg_zones"]) is False


def test_fvg_zones_causal_under_truncation():
    params = {}
    df = _enrich(_ohlcv(seed=11), params)
    out_full = _materialize(df, params, ["fvg_zones"])
    i = 180
    df_t = _enrich(_ohlcv(seed=11).iloc[: i + 1], params)
    out_t = _materialize(df_t, params, ["fvg_zones"])
    a, b = out_full["fvg_top"].iloc[i], out_t["fvg_top"].iloc[i]
    assert (pd.isna(a) and pd.isna(b)) or a == pytest.approx(b)
    assert out_full["fvg_state"].iloc[i] == out_t["fvg_state"].iloc[i]


# ---------------------------------------------------------------------------
# FEATURE 6 — order_block
# ---------------------------------------------------------------------------

def _ob_reference(df, lookback=10):
    o = df["open"].reset_index(drop=True).to_numpy()
    h = df["high"].reset_index(drop=True).to_numpy()
    l = df["low"].reset_index(drop=True).to_numpy()
    c = df["close"].reset_index(drop=True).to_numpy()
    disp = df["displacement"].reset_index(drop=True).to_numpy(dtype=bool)
    n = len(df)
    lb = min(int(lookback), 20)
    top = np.full(n, np.nan); bot = np.full(n, np.nan)
    direction = np.array([None] * n, dtype=object); active = np.zeros(n, dtype=bool)
    cur_top = cur_bot = np.nan; cur_dir = None; cur_active = False
    for i in range(n):
        if disp[i] and c[i] > o[i]:
            for j in range(i - 1, max(-1, i - 1 - lb), -1):
                if c[j] < o[j]:
                    cur_top, cur_bot, cur_dir, cur_active = h[j], l[j], "bull", True
                    break
        elif disp[i] and c[i] < o[i]:
            for j in range(i - 1, max(-1, i - 1 - lb), -1):
                if c[j] > o[j]:
                    cur_top, cur_bot, cur_dir, cur_active = h[j], l[j], "bear", True
                    break
        elif cur_active:
            if cur_dir == "bull" and l[i] <= cur_bot:
                cur_active = False
            elif cur_dir == "bear" and h[i] >= cur_top:
                cur_active = False
        top[i], bot[i], direction[i], active[i] = cur_top, cur_bot, cur_dir, cur_active
    return pd.DataFrame({"ob_top": top, "ob_bottom": bot, "ob_dir": direction,
                         "ob_active": active})


def test_order_block_forms_and_mitigates():
    from app.features.structures import compute_order_block
    # bar0 down candle (the OB); bar1 bullish displacement; bar4 mitigates (low<=ob_bottom)
    df = pd.DataFrame({
        "open":  [100.0, 99.0,  106.0, 106.0, 106.0],
        "close": [99.0,  106.0, 106.5, 106.5, 97.0],
        "high":  [100.5, 106.5, 107.0, 107.0, 107.0],
        "low":   [98.5,  99.0,  105.5, 105.5, 97.0],
        "displacement": [False, True, False, False, False],
    })
    out = compute_order_block(df, {"ob_lookback": 10})
    assert out["ob_dir"].iloc[1] == "bull"
    assert out["ob_top"].iloc[1] == 100.5
    assert out["ob_bottom"].iloc[1] == 98.5
    assert bool(out["ob_active"].iloc[1]) is True
    assert bool(out["ob_active"].iloc[4]) is False   # low 97 <= ob_bottom 98.5


def test_order_block_matches_reference():
    from app.features.structures import compute_order_block
    rng = np.random.default_rng(7)
    n = 60
    o = 100 + np.cumsum(rng.normal(0, 0.5, n))
    c = o + rng.normal(0, 1.5, n)            # real non-zero bodies => up AND down candles
    h = np.maximum(o, c) + np.abs(rng.normal(0, 0.3, n))
    low = np.minimum(o, c) - np.abs(rng.normal(0, 0.3, n))
    disp = rng.random(n) < 0.15              # ~15% displacement bars
    df = pd.DataFrame({"open": o, "high": h, "low": low, "close": c, "displacement": disp})
    out = compute_order_block(df, {"ob_lookback": 10})
    ref = _ob_reference(df, lookback=10)
    pd.testing.assert_series_equal(
        pd.Series(out["ob_top"]).reset_index(drop=True), ref["ob_top"], check_names=False)
    assert list(out["ob_dir"]) == ref["ob_dir"].tolist()
    assert list(out["ob_active"].astype(bool)) == ref["ob_active"].tolist()
    assert out["ob_active"].astype(bool).any()   # non-vacuity: real OBs actually formed


def test_order_block_bear_and_multibar_lookback():
    from app.features.structures import compute_order_block
    # bar1 = up candle (c>o) -> the bear OB; bar2 neutral (c==o); bar3 = bearish displacement.
    # The opposing up-candle (bar1) is 2 bars before the displacement -> multi-bar lookback scan.
    df = pd.DataFrame({
        "open":  [100.0, 100.0, 103.0, 103.0, 100.0, 100.0],
        "close": [100.0, 103.0, 103.0, 97.0,  100.0, 104.0],
        "high":  [100.5, 103.5, 103.2, 103.5, 100.5, 104.5],
        "low":   [99.5,  99.5,  102.8, 97.0,  99.5,  99.5],
        "displacement": [False, False, False, True, False, False],
    })
    out = compute_order_block(df, {"ob_lookback": 10})
    assert out["ob_dir"].iloc[3] == "bear"
    assert out["ob_top"].iloc[3] == 103.5     # bar1 high
    assert out["ob_bottom"].iloc[3] == 99.5   # bar1 low
    assert bool(out["ob_active"].iloc[3]) is True
    assert bool(out["ob_active"].iloc[5]) is False   # bear mitigated: bar5 high 104.5 >= ob_top 103.5


def test_order_block_requires_displacement_chain():
    groups = [g.name for g in resolve_features(["order_block"])]
    assert groups.index("swing_levels") < groups.index("displacement") < groups.index("order_block")


def test_order_block_lookback_hard_capped():
    params = {"ob_lookback": 999}
    df = _enrich(_ohlcv(seed=21), params)
    df = _materialize(df, params, ["displacement"])
    from app.features.structures import compute_order_block
    out = compute_order_block(df, params)
    assert "ob_top" in out


# ---------------------------------------------------------------------------
# Integration — all six seed features materialize together + catalog advertises them
# ---------------------------------------------------------------------------

def test_all_features_materialize_together():
    params = {"swing_lookback": 5}
    df = _enrich(_ohlcv(seed=31), params)
    # declaring these four pulls in swing_levels + displacement via the DAG -> all six
    required = ["premium_discount", "order_block", "fvg_zones", "choch"]
    out = _materialize(df, params, required)
    for col in ["last_swing_high_level", "premium_discount_pct", "range_state",
                "displacement", "bos_up", "choch_up", "fvg_top", "fvg_state",
                "ob_top", "ob_active"]:
        assert col in out.columns, col
    assert len(out) == len(df)
    # non-vacuity: the DAG actually produced real values (swings always form here)
    assert out["last_swing_high_level"].notna().any()


def test_catalog_advertises_all_seed_features():
    from app.features.catalog import feature_catalog_entries
    entries = feature_catalog_entries()
    names = {e["feature"] for e in entries}
    assert {"swing_levels", "premium_discount", "displacement", "choch",
            "fvg_zones", "order_block"} <= names
    by = {e["feature"]: e for e in entries}
    # vectorized + bounded => live-deployable
    assert by["swing_levels"]["live_feasible"] is True
    assert by["premium_discount"]["live_feasible"] is True
    assert by["displacement"]["live_feasible"] is True
    # stateful-unbounded => backtest-only in v1
    assert by["fvg_zones"]["live_feasible"] is False
    assert by["choch"]["live_feasible"] is False
    assert by["order_block"]["live_feasible"] is False
