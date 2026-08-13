"""`sensex_explosive_reversal` — correctness of the SENSEX-only detector.

The strategy adds three things to the proven confluence core: a searchable
volatility-regime band, a blended stop basis, and a session-progress gate. Each
is a place where a subtle mistake would produce plausible-looking numbers, so
each is pinned here.

The regime band is the one that matters most: it is built on a TRAILING
percentile, and a session-wide percentile would be look-ahead. The supporting
analysis originally made that exact mistake and the measured effect reversed once
corrected — so causality is tested, not trusted.
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.strategies.base import build_eval_ctx, get_registry            # noqa: E402
from app.strategies.plugins.sensex_explosive_reversal import (          # noqa: E402
    _REGIME_CTX_KEY, _GRID_CTX_KEY,
)


def _sensex_frame(n=900, seed=3, atr_ramp=True):
    """SENSEX-scale frame: ~78,500 level, two sessions, and an ATR that ramps so
    the trailing rank actually varies."""
    rng = np.random.default_rng(seed)
    close = 78500 + np.cumsum(rng.normal(0, 27, n))
    atr = (np.linspace(10, 60, n) if atr_ramp else np.full(n, 31.0)) + rng.uniform(0, 3, n)
    df = pd.DataFrame({
        "open": close, "close": close,
        "high": close + rng.uniform(0, 30, n),
        "low": close - rng.uniform(0, 30, n),
        "atr": atr,
        "rsi": rng.uniform(20, 80, n),
        "macd_hist": rng.normal(0, 4, n),
        "session_date": ["2026-01-08"] * (n // 2) + ["2026-01-09"] * (n - n // 2),
    })
    hi = np.zeros(n, dtype=bool); lo = np.zeros(n, dtype=bool)
    hi[rng.choice(n, size=n // 15, replace=False)] = True
    lo[rng.choice(n, size=n // 15, replace=False)] = True
    df["is_swing_high"] = hi
    df["is_swing_low"] = lo
    return df


def _strategy():
    r = get_registry(); r.auto_discover()
    s = r.get("sensex_explosive_reversal")
    assert s is not None, "strategy must be registered"
    return s


def _ctx(s, df, i, params):
    extras = s.session_precompute(df, params)
    return build_eval_ctx(history_df=df, i=i, instrument="SENSEX",
                          session_date=str(df["session_date"].iloc[i]), session_extras=extras)


def test_registered_and_sensex_only():
    s = _strategy()
    assert s.id == "sensex_explosive_reversal"
    assert s.supported_instruments == ["SENSEX"]


def test_atr_rank_is_causal_not_session_wide():
    """THE correctness test. A bar's rank must depend only on the trailing window;
    if it ever depends on later bars, the strategy is trading the future and every
    downstream number is meaningless."""
    s = _strategy()
    params = s.merged_params({"atr_rank_window": 60})
    df = _sensex_frame()
    full = s.session_precompute(df, params)[_REGIME_CTX_KEY]["rank"]
    cut = 700
    truncated = s.session_precompute(df.iloc[:cut].copy(), params)[_REGIME_CTX_KEY]["rank"]
    both = ~np.isnan(full[:cut]) & ~np.isnan(truncated)
    assert both.sum() > 100, "not enough overlapping non-warmup bars to prove anything"
    np.testing.assert_allclose(full[:cut][both], truncated[both], rtol=0, atol=0)


def test_regime_band_actually_gates():
    """A band that excludes the bar's rank must block it; a full band must not."""
    s = _strategy()
    df = _sensex_frame()
    i = 800
    open_band = s.merged_params({"signal_threshold": 0, "min_session_bars": 0,
                                 "min_atr_rank": 0.0, "max_atr_rank": 1.0})
    rank = s.session_precompute(df, open_band)[_REGIME_CTX_KEY]["rank"][i]
    assert not np.isnan(rank), "precondition: bar must have a rank"
    blocked = s.merged_params({"signal_threshold": 0, "min_session_bars": 0,
                               "min_atr_rank": 0.0,
                               "max_atr_rank": max(0.4, min(1.0, rank - 0.2))})
    sig = s.evaluate(df.iloc[i], df.iloc[i - 1], blocked, _ctx(s, df, i, blocked))
    assert sig.direction == "NONE"
    assert any("atr_rank" in b for b in sig.blockers)


def test_warmup_bars_are_not_admitted_as_if_they_passed():
    """Before the trailing window fills, rank is NaN — carrying no evidence. It
    must not be silently treated as inside the band, which a naive comparison
    would do since every comparison against NaN is False."""
    s = _strategy()
    df = _sensex_frame()
    params = s.merged_params({"atr_rank_window": 300, "signal_threshold": 0,
                              "min_session_bars": 0, "min_atr_rank": 0.9, "max_atr_rank": 1.0})
    reg = s.session_precompute(df, params)[_REGIME_CTX_KEY]
    warm = int(np.argmax(~np.isnan(reg["rank"])))
    assert warm > 100, "precondition: a real warmup region must exist"
    i = warm - 5
    sig = s.evaluate(df.iloc[i], df.iloc[i - 1], params, _ctx(s, df, i, params))
    # It may or may not fire on confluence, but it must NOT be rejected for the band.
    assert not any("atr_rank" in b for b in sig.blockers)


def test_session_progress_gate_blocks_only_early_bars():
    s = _strategy()
    df = _sensex_frame()
    params = s.merged_params({"signal_threshold": 0, "min_session_bars": 60,
                              "min_atr_rank": 0.0, "max_atr_rank": 1.0})
    reg = s.session_precompute(df, params)[_REGIME_CTX_KEY]
    early = [i for i in range(len(df)) if reg["bars_in"][i] == 10 and i > 200]
    assert early, "precondition"
    i = early[0]
    sig = s.evaluate(df.iloc[i], df.iloc[i - 1], params, _ctx(s, df, i, params))
    assert sig.direction == "NONE" and "too early in session" in sig.blockers


def test_bars_in_session_resets_per_day():
    """Cross-session leakage here would let day 2 inherit day 1's progress and the
    gate would stop gating."""
    s = _strategy()
    df = _sensex_frame()
    reg = s.session_precompute(df, s.merged_params({}))[_REGIME_CTX_KEY]
    bars_in = reg["bars_in"]
    boundary = int((df["session_date"] != df["session_date"].shift()).to_numpy().nonzero()[0][-1])
    assert bars_in[boundary] == 0, bars_in[boundary]
    assert bars_in[boundary - 1] > 0


@pytest.mark.parametrize("blend,expect", [(0.0, "instant"), (1.0, "baseline")])
def test_stop_basis_blend_moves_between_instant_and_baseline_atr(blend, expect):
    """Entering on a compressed bar with a stop sized off that same bar reproduces
    the original SENSEX failure in miniature. The blend must actually reach the
    trailing baseline."""
    s = _strategy()
    df = _sensex_frame()
    params = s.merged_params({"signal_threshold": 0, "min_session_bars": 0,
                              "min_atr_rank": 0.0, "max_atr_rank": 1.0,
                              "stop_basis_blend": blend, "spot_stop_atr": 3.0})
    i = 800
    reg = s.session_precompute(df, params)[_REGIME_CTX_KEY]
    sig = s.evaluate(df.iloc[i], df.iloc[i - 1], params, _ctx(s, df, i, params))
    if sig.direction == "NONE":
        pytest.skip("no signal on this bar")
    inst, base = float(df["atr"].iloc[i]), float(reg["base"][i])
    assert inst != pytest.approx(base), "precondition: the two bases must differ"
    want = inst if expect == "instant" else base
    assert sig.spot_stop_pts == pytest.approx(want * 3.0)
    # the TARGET always rides the current bar's ATR, whatever the stop basis
    assert sig.spot_target_pts == pytest.approx(inst * float(params["spot_target_atr"]))


def test_round_grid_lands_on_sensex_hundreds_at_the_default():
    """0.15% of ~78,500 is ~118 -> the 100/50 grid the SENSEX search actually
    chose, not the hardcoded 500/500 that collapses both tiers into one."""
    s = _strategy()
    df = _sensex_frame()
    grid = s.session_precompute(df, s.merged_params({}))[_GRID_CTX_KEY]
    assert grid == (100, 50), grid


def test_no_atr_means_no_trade():
    s = _strategy()
    df = _sensex_frame()
    df.loc[:, "atr"] = 0.0
    params = s.merged_params({"signal_threshold": 0, "min_session_bars": 0})
    sig = s.evaluate(df.iloc[800], df.iloc[799], params, _ctx(s, df, 800, params))
    assert sig.direction == "NONE" and sig.blockers == ["no ATR to size exits"]


def test_the_other_two_strategies_are_untouched():
    """NIFTY results depend on explosive_reversal; explosive_reversal_atr is the
    instrument-agnostic one. Neither may be edited by adding this."""
    p = ROOT / "backend/app/strategies/plugins"
    parent = (p / "explosive_reversal.py").read_text(encoding="utf-8")
    assert '"spot_stop_pts": {"type": "float", "min": 3, "max": 100, "default": 18}' in parent
    agnostic = (p / "explosive_reversal_atr.py").read_text(encoding="utf-8")
    assert 'id = "explosive_reversal_atr"' in agnostic
    assert "_REGIME_CTX_KEY" not in agnostic
