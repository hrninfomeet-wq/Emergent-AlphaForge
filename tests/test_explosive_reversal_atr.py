"""`explosive_reversal_atr` must differ from its parent in EXACTLY two ways.

It exists because the parent's absolute-point exits cannot express a workable
stop on SENSEX (capped at 3.20 ATR where NIFTY's winner uses 6.49). The fix is
units, not logic — so the confluence scoring is a deliberate copy, and a copy is
free to drift. These tests pin it:

  - the scoring must agree with the parent's, bar for bar, once the round-level
    grid is configured to match (round_step_pct=0.41 reproduces NIFTY's 100/50);
  - the exits must be ATR-denominated, i.e. actually scale with volatility;
  - the parent must remain untouched, since its NIFTY results depend on it.
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.context_signals import (                                   # noqa: E402
    nice_round_step, round_level_proximity, derive_round_grid, round_level_proximity_grid,
)
from app.strategies.base import build_eval_ctx, get_registry        # noqa: E402


def _frame(n=600, level=24400.0, seed=5):
    rng = np.random.default_rng(seed)
    close = level + np.cumsum(rng.normal(0, level * 0.0004, n))
    df = pd.DataFrame({
        "open": close, "close": close,
        "high": close + rng.uniform(0, level * 0.0004, n),
        "low": close - rng.uniform(0, level * 0.0004, n),
        "atr": rng.uniform(level * 0.0002, level * 0.0006, n),
        "rsi": rng.uniform(20, 80, n),
        "macd_hist": rng.normal(0, 4, n),
        "session_date": "2026-01-08",
    })
    hi = np.zeros(n, dtype=bool); lo = np.zeros(n, dtype=bool)
    hi[rng.choice(n, size=n // 15, replace=False)] = True
    lo[rng.choice(n, size=n // 15, replace=False)] = True
    df["is_swing_high"] = hi
    df["is_swing_low"] = lo
    return df


def _both():
    r = get_registry(); r.auto_discover()
    parent, child = r.get("explosive_reversal"), r.get("explosive_reversal_atr")
    assert parent is not None and child is not None, "both strategies must be registered"
    return parent, child


def test_round_grid_default_reproduces_niftys_existing_levels():
    """0.41% of a ~24,400 index is 100, and the minor grid is half of it — exactly
    the hardcoded NIFTY pair. That is what makes the derived grid a generalization
    of the table rather than a different signal."""
    major, minor = derive_round_grid(24400.0, 0.41)
    assert (major, minor) == (100, 50)
    got = round_level_proximity_grid(24400.0, major, minor, atr=10.0)
    ref = round_level_proximity(24400.0, "NIFTY", atr=10.0)
    for key in ("nearest_level", "distance_pts", "distance_atr", "is_near"):
        assert got[key] == ref[key], key


def test_round_grid_scales_to_sensex_and_keeps_two_distinct_tiers():
    """The hardcoded table gives SENSEX 500/500 — both tiers identical, so the
    two-grid comparison collapses to one. A derived grid keeps them distinct at
    every sane granularity the optimizer can pick."""
    for pct in (0.2, 0.41, 0.64, 1.0):
        major, minor = derive_round_grid(78500.0, pct)
        assert minor < major, (pct, major, minor)
    # the whole searchable range stays finer than the hardcoded 500 at its low end
    assert derive_round_grid(78500.0, 0.2)[0] < 500


def test_round_grid_is_stable_as_price_drifts():
    """Derived per bar, 0.41% of prices around 24,400 straddles 100 and the grid
    would flip 100 <-> 50 mid-run, scoring the same level differently on different
    days. The grid must come from ONE reference price for the whole run."""
    ref_grid = derive_round_grid(24400.0, 0.41)
    drifting = {derive_round_grid(p, 0.41) for p in (24100.0, 24400.0, 24900.0)}
    assert len(drifting) > 1, "precondition: per-bar derivation really is unstable"
    assert ref_grid == (100, 50)


def test_nice_round_step_never_returns_unhuman_levels():
    for target, expected in [(100, 100), (110, 100), (322, 250), (502, 500),
                             (0.2, 1), (99, 50), (12000, 10000)]:
        assert nice_round_step(target) == expected, target


def test_scoring_matches_the_parent_bar_for_bar():
    """The copied confluence must stay identical to the parent's. Compared on a
    NIFTY-scale frame with the grid configured to match, so the ONLY thing that
    could differ is drift in the copy."""
    from app.strategies.plugins.explosive_reversal_atr import _GRID_CTX_KEY

    parent, child = _both()
    df = _frame()
    # The parent's NIFTY grid is the hardcoded 100/50. Pick the percent that lands
    # the derived grid on the SAME pair for THIS frame's median, so the only thing
    # that can differ is drift in the copied scoring — not the grid.
    median = float(df["close"].median())
    p_params = parent.merged_params({"signal_threshold": 30})
    c_params = child.merged_params({"signal_threshold": 30,
                                    "round_step_pct": 100.0 * 100.0 / median})
    p_extras = parent.session_precompute(df, p_params)
    c_extras = child.session_precompute(df, c_params)
    assert c_extras[_GRID_CTX_KEY] == (100, 50), c_extras[_GRID_CTX_KEY]
    compared = 0
    for i in range(int(p_params["sr_lookback"]) + 4, len(df)):
        row = df.iloc[i]
        p_ctx = build_eval_ctx(history_df=df, i=i, instrument="NIFTY",
                               session_date="2026-01-08", session_extras=p_extras)
        c_ctx = build_eval_ctx(history_df=df, i=i, instrument="NIFTY",
                               session_date="2026-01-08", session_extras=c_extras)
        p_sig = parent.evaluate(row, df.iloc[i - 1], p_params, p_ctx)
        c_dir, c_score, c_reasons = child.confluence(row, c_params, c_ctx)
        if p_sig.direction == "NONE":
            continue          # parent gates on threshold; compare only where it fired
        assert (c_dir, c_score, c_reasons) == (p_sig.direction, p_sig.score, p_sig.reasons), f"bar {i}"
        compared += 1
    assert compared > 20, f"only {compared} bars compared — the test proved nothing"


def test_exits_scale_with_atr_not_with_a_constant():
    """The whole point: the same parameters must mean the same THING on a 24,400
    index and a 78,500 one. Under the parent they mean 3.2x less."""
    _, child = _both()
    params = child.merged_params({"signal_threshold": 0, "spot_stop_atr": 6.5,
                                  "spot_target_atr": 10.0})
    for level in (24400.0, 78500.0):
        df = _frame(level=level)
        extras = child.session_precompute(df, params)
        i = int(params["sr_lookback"]) + 5
        row = df.iloc[i]
        ctx = build_eval_ctx(history_df=df, i=i, instrument="X",
                             session_date="2026-01-08", session_extras=extras)
        sig = child.evaluate(row, df.iloc[i - 1], params, ctx)
        if sig.direction == "NONE":
            continue
        atr = float(row["atr"])
        assert sig.spot_stop_pts == pytest.approx(atr * 6.5)
        assert sig.spot_target_pts == pytest.approx(atr * 10.0)
        # and in ATR terms it is the SAME trade on both instruments
        assert sig.spot_stop_pts / atr == pytest.approx(6.5)


def test_a_bar_without_atr_is_refused_rather_than_silently_fixed():
    """Falling back to a fixed point value would reintroduce the exact bug this
    strategy exists to remove — a number that means something different per index."""
    _, child = _both()
    df = _frame()
    df.loc[:, "atr"] = 0.0
    params = child.merged_params({"signal_threshold": 0})
    extras = child.session_precompute(df, params)
    i = int(params["sr_lookback"]) + 5
    ctx = build_eval_ctx(history_df=df, i=i, instrument="NIFTY",
                         session_date="2026-01-08", session_extras=extras)
    sig = child.evaluate(df.iloc[i], df.iloc[i - 1], params, ctx)
    assert sig.direction == "NONE"
    assert sig.blockers == ["no ATR to size exits"]


def test_parent_strategy_is_untouched():
    """NIFTY's results depend on explosive_reversal exactly as it is."""
    src = (ROOT / "backend/app/strategies/plugins/explosive_reversal.py").read_text(encoding="utf-8")
    assert '"spot_target_pts": {"type": "float", "min": 5, "max": 200, "default": 40}' in src
    assert '"spot_stop_pts": {"type": "float", "min": 3, "max": 100, "default": 18}' in src
    assert "round_level_proximity(close, instrument, atr_val)" in src
    assert "round_level_proximity_scaled" not in src
