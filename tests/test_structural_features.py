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


def test_choch_is_bounded_and_live_deployable():
    """Was `stateful_unbounded is True` / not live-feasible. The direction now
    expires after `smc_max_age_bars` without a break of structure, so the state
    cannot reach further back than the live window holds (register item #9)."""
    from app.features.registry import feature_live_feasible
    g = FEATURE_REGISTRY["choch"]
    assert g.stateful_unbounded is False
    assert feature_live_feasible(g) is True


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


def _norm_missing(seq):
    """None and NaN both mean 'no zone' — pandas 2 vs 3 differ on which one
    survives an object column, so normalize before comparing."""
    return [None if x is None or (isinstance(x, float) and x != x) else x for x in seq]


def test_fvg_zones_matches_reference():
    params = {}
    df = _enrich(_ohlcv(seed=11), params)
    out = _materialize(df, params, ["fvg_zones"])
    ref = _fvg_reference(df)
    pd.testing.assert_series_equal(out["fvg_top"], ref["fvg_top"], check_names=False)
    pd.testing.assert_series_equal(out["fvg_bottom"], ref["fvg_bottom"], check_names=False)
    assert _norm_missing(out["fvg_dir"].tolist()) == _norm_missing(ref["fvg_dir"].tolist())
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


def test_fvg_zones_is_live_deployable():
    """Was backtest-only. An unfilled, unrenewed gap now expires instead of
    being carried forever (register item #9)."""
    from app.features.registry import feature_live_feasible
    assert feature_live_feasible(FEATURE_REGISTRY["fvg_zones"]) is True


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
    assert _norm_missing(list(out["ob_dir"])) == _norm_missing(ref["ob_dir"].tolist())
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
    # Bounded carry-forward => live-deployable too (register item #9). These
    # three were backtest-only, which made half the SMC vocabulary — and the
    # half most SMC traders key on — impossible to deploy however it backtested.
    assert by["fvg_zones"]["live_feasible"] is True
    assert by["choch"]["live_feasible"] is True
    assert by["order_block"]["live_feasible"] is True


# ---------------------------------------------------------------------------
# Register item #9 — bounded carry-forward makes the SMC zones LIVE-DEPLOYABLE
#
# `choch`, `fvg_zones` and `order_block` all carried the most recent zone forward
# INDEFINITELY until an invalidation event, so the state at any bar could depend
# on data arbitrarily far back. That is what `stateful_unbounded=True` meant, and
# it made all three backtest-only: an SMC strategy built on fair-value gaps or
# order blocks would backtest beautifully and refuse to deploy.
#
# Bounding the carry-forward with an explicit max age fixes it BY CONSTRUCTION.
# Once state cannot reach further back than N bars, a rolling window longer than
# N reproduces the full-history answer on its final bar — which is the only bar
# the live evaluator reads.
# ---------------------------------------------------------------------------

from app.features.registry import feature_live_feasible  # noqa: E402
from app.features.structures import SMC_MAX_AGE_BARS  # noqa: E402

_SMC = ("choch", "fvg_zones", "order_block")


def test_the_smc_zones_are_no_longer_backtest_only():
    for name in _SMC:
        g = FEATURE_REGISTRY[name]
        assert g.stateful_unbounded is False, name
        assert feature_live_feasible(g) is True, name


def test_every_registered_feature_is_now_live_feasible():
    """The half-deployable vocabulary was the actual defect: three of six worked
    live and the three most SMC traders key on did not."""
    for name, g in FEATURE_REGISTRY.items():
        assert feature_live_feasible(g) is True, f"{name} is still backtest-only"


def test_the_declared_warmup_actually_covers_the_max_age():
    """`feature_live_feasible` trusts `min_history_bars`. If that under-declares
    the real reach of the state, the flag says live-correct and the values are
    not — a worse failure than the honest backtest-only flag it replaces."""
    for name in _SMC:
        g = FEATURE_REGISTRY[name]
        assert g.min_history_bars >= SMC_MAX_AGE_BARS, name


def test_a_rolling_window_reproduces_full_history_on_the_final_bar():
    """THE property that makes these live-correct, tested the only way that
    counts: compute on full history, compute on the trailing window the live
    evaluator would actually hold, and compare the bar live would read.

    This is the check the `live_window_anchors_session_indicators` trap exists
    for — a session-VWAP anchor error of 2.12 ATR silently inverted nine shipped
    strategies precisely because nobody compared the two paths.
    """
    df = _enrich(_ohlcv(n=600), {})
    params = {}
    full = _materialize(df, params, list(_SMC))
    window_len = 200                      # the live evaluator's default lookback
    windowed = _materialize(df.iloc[-window_len:], params, list(_SMC))

    cols = [c for name in _SMC for c in FEATURE_REGISTRY[name].columns]
    for col in cols:
        a = full[col].iloc[-1]
        b = windowed[col].iloc[-1]
        if pd.isna(a) and pd.isna(b):
            continue
        assert a == b, f"{col}: full-history={a!r} but live window={b!r}"


def test_the_agreement_holds_across_many_window_positions():
    """One matching bar could be luck. Slide the window and check every ending."""
    df = _enrich(_ohlcv(n=700, seed=11), {})
    full = _materialize(df, {}, list(_SMC))
    cols = [c for name in _SMC for c in FEATURE_REGISTRY[name].columns]
    for end in range(300, 700, 47):
        w = _materialize(df.iloc[max(0, end - 200):end], {}, list(_SMC))
        for col in cols:
            a, b = full[col].iloc[end - 1], w[col].iloc[-1]
            if pd.isna(a) and pd.isna(b):
                continue
            assert a == b, f"{col} @bar {end}: full={a!r} window={b!r}"


def test_a_zone_expires_once_it_is_older_than_the_max_age():
    """The bound is not decorative — an unrenewed zone must actually go away,
    or the state still reaches back forever and the live flag is a lie."""
    from app.features.structures import compute_fvg_zones
    n = SMC_MAX_AGE_BARS + 60
    # One UP gap at bar 2, then a long flat drift that neither fills nor renews it.
    high = np.full(n, 101.0); low = np.full(n, 100.5); close = np.full(n, 100.8)
    high[0], low[0], close[0] = 100.2, 99.8, 100.0
    high[1], low[1], close[1] = 100.6, 100.1, 100.5
    high[2], low[2], close[2] = 101.4, 100.9, 101.2      # low[2] > high[0] -> UP FVG
    df = pd.DataFrame({"high": high, "low": low, "close": close,
                       "open": close, "volume": np.ones(n)})
    out = compute_fvg_zones(df, {})
    assert out["fvg_state"].iloc[3] == "active"
    assert out["fvg_state"].iloc[-1] != "active", "the gap never expired"
    assert pd.isna(out["fvg_top"].iloc[-1])


# The rolling-window equivalence test above passes on realistic data whether or
# not the bound exists, because events recur often enough inside 200 bars that
# bounded and unbounded agree anyway. It proves the features are live-CORRECT but
# it does not exercise the BOUND. A mutation sweep made that concrete: removing
# the expiry from choch and order_block, and removing the age reset from both
# fvg and order_block, left it green. These construct the long quiet stretches
# where the bound is the only thing that differs.

def _flat(n, low=105.0, high=106.0, close=105.5):
    return (np.full(n, high), np.full(n, low), np.full(n, close))


def test_choch_direction_expires_so_it_cannot_reach_past_the_window():
    from app.features.structures import compute_choch
    n = SMC_MAX_AGE_BARS + 40
    bu = np.zeros(n, dtype=bool); bd = np.zeros(n, dtype=bool)
    bu[0] = True                      # trend up established at the very start
    bd[n - 1] = True                  # ... and reversed far beyond the max age
    out = compute_choch(pd.DataFrame({"bos_up": bu, "bos_down": bd}), {})
    assert out["choch_down"].iloc[-1] is np.False_ or not out["choch_down"].iloc[-1], (
        "an expired direction must not emit a change-of-character — that would be "
        "state reaching back further than the live window holds")


def test_choch_age_resets_on_a_renewing_break():
    from app.features.structures import compute_choch
    n = SMC_MAX_AGE_BARS + 40
    bu = np.zeros(n, dtype=bool); bd = np.zeros(n, dtype=bool)
    bu[0] = True
    bu[n - 20] = True                 # renews the up-direction inside the bound
    bd[n - 1] = True                  # now only 19 bars later -> still live
    out = compute_choch(pd.DataFrame({"bos_up": bu, "bos_down": bd}), {})
    assert bool(out["choch_down"].iloc[-1]), "a renewed direction must stay alive"


def _ob_frame(n, *, renew_at=None):
    """A bull order block at bar 3, a displacement at bar 5, then quiet."""
    high, low, close = _flat(n)
    open_ = close.copy()
    disp = np.zeros(n, dtype=bool)
    high[3], low[3], open_[3], close[3] = 101.0, 100.0, 100.9, 100.1   # bearish
    disp[5] = True; open_[5], close[5] = 100.2, 102.0                  # bull displacement
    if renew_at is not None:
        high[renew_at - 2], low[renew_at - 2] = 104.0, 103.0
        open_[renew_at - 2], close[renew_at - 2] = 103.9, 103.1        # bearish
        disp[renew_at] = True; open_[renew_at], close[renew_at] = 103.2, 105.0
    return pd.DataFrame({"high": high, "low": low, "close": close,
                         "open": open_, "displacement": disp})


def test_order_block_expires_when_nothing_renews_or_mitigates_it():
    from app.features.structures import compute_order_block
    n = SMC_MAX_AGE_BARS + 60
    out = compute_order_block(_ob_frame(n), {})
    assert bool(out["ob_active"].iloc[6]), "the block should form on the displacement"
    assert not bool(out["ob_active"].iloc[-1]), "an unmitigated block is not immortal"
    assert pd.isna(out["ob_top"].iloc[-1]), "an expired block must not leave a price band"


def test_order_block_age_resets_when_a_new_block_forms():
    from app.features.structures import compute_order_block
    n = SMC_MAX_AGE_BARS + 60
    out = compute_order_block(_ob_frame(n, renew_at=SMC_MAX_AGE_BARS + 20), {})
    assert bool(out["ob_active"].iloc[-1]), (
        "a block re-formed inside the bound must still be live at the end")


def test_fvg_age_resets_when_a_new_gap_forms():
    from app.features.structures import compute_fvg_zones
    n = SMC_MAX_AGE_BARS + 60
    renew = SMC_MAX_AGE_BARS + 20
    high = np.full(n, 101.0); low = np.full(n, 100.5); close = np.full(n, 100.8)
    high[0], low[0], close[0] = 100.2, 99.8, 100.0
    high[2], low[2], close[2] = 101.4, 100.9, 101.2          # UP gap vs high[0]
    high[renew - 2], low[renew - 2] = 100.6, 100.2
    high[renew], low[renew], close[renew] = 101.9, 101.0, 101.5   # renewing UP gap
    # Everything after the renewal must stay ABOVE the new gap's floor (100.6),
    # or the zone reads as FILLED and the test would prove nothing about ageing.
    high[renew + 1:], low[renew + 1:], close[renew + 1:] = 102.5, 101.2, 102.0
    df = pd.DataFrame({"high": high, "low": low, "close": close,
                       "open": close, "volume": np.ones(n)})
    out = compute_fvg_zones(df, {})
    assert out["fvg_state"].iloc[-1] == "active", "a renewed gap must not expire early"
