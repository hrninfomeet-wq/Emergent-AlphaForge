"""`sensex_vwap_mean_reversion` — correctness of the SENSEX-only VWAP detector.

This plugin ships as CAPABILITY, not as a validated edge (see its docstring for
the four-quarter measurement that killed the premise). That makes these tests
MORE load-bearing, not less: an unvalidated strategy that is also mis-engineered
would produce plausible-looking optimizer output for two independent reasons, and
nobody could tell them apart.

Pinned here, in rough order of how quietly each would fail:

* Exits scale with ATR. A hardcoded-point mutant must FAIL this suite — scaling
  one bar by a constant is a tautology that a hardcoded strategy also passes, so
  the mutant is constructed explicitly rather than assumed impossible.
* The ATR-rank band is TRAILING. A session-wide percentile is look-ahead and is
  exactly the mistake the supporting analysis for the sibling plugin made.
* A missing `regime` column BLOCKS BY NAME. The parent silently turns every
  signal into a blocker on any path that has not enriched `regime`, which is
  indistinguishable from the filter working.
* The flow gate is inert-by-name when its columns are absent.
* Mode switches are bool, because `optimizer._build_param_space` drops str.
* The parent file is untouched.
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.strategies.base import build_eval_ctx, get_registry              # noqa: E402
from app.strategies.plugins.sensex_vwap_mean_reversion import (           # noqa: E402
    _REGIME_CTX_KEY, MIN_STOP_ATR, TRENDING_REGIMES,
)

PLUGINS = ROOT / "backend" / "app" / "strategies" / "plugins"


def _frame(n=600, level=78500.0, scale=1.0, seed=5, stretch_at=None):
    """SENSEX-scale frame with a session VWAP and a controllable displacement.

    `scale` multiplies EVERY price quantity, so a scale-transfer test can assert
    that exits move proportionally. `stretch_at` forces a large displacement at
    one index so a signal is guaranteed to fire there.
    """
    rng = np.random.default_rng(seed)
    close = (level + np.cumsum(rng.normal(0, 27, n))) * scale
    atr = (np.full(n, 30.0) + rng.uniform(0, 3, n)) * scale
    vwap = close.copy()
    if stretch_at is not None:
        # Push price far above VWAP at that bar: +6 ATR.
        close[stretch_at] = vwap[stretch_at] + 6.0 * atr[stretch_at]
    df = pd.DataFrame({
        "open": close, "close": close,
        "high": close + rng.uniform(0, 20, n) * scale,
        "low": close - rng.uniform(0, 20, n) * scale,
        "vwap": vwap,
        "vwap_sigma": atr * 0.8,
        "atr": atr,
        "rsi": np.full(n, 80.0),          # overbought, so a fade PE passes RSI
        "regime": ["MIXED"] * n,
        "session_date": ["2026-01-08"] * (n // 2) + ["2026-01-09"] * (n - n // 2),
    })
    return df


def _strategy():
    r = get_registry(); r.auto_discover()
    s = r.get("sensex_vwap_mean_reversion")
    assert s is not None, "strategy must be registered"
    return s


def _ctx(s, df, i, params):
    extras = s.session_precompute(df, params)
    return build_eval_ctx(history_df=df, i=i, instrument="SENSEX",
                          session_date=str(df["session_date"].iloc[i]),
                          session_extras=extras)


def _eval(s, df, i, params):
    return s.evaluate(df.iloc[i], df.iloc[i - 1] if i else df.iloc[i], params, _ctx(s, df, i, params))


# --------------------------------------------------------------------------
# registration / scope
# --------------------------------------------------------------------------

def test_registered_and_sensex_only():
    s = _strategy()
    assert s.supported_instruments == ["SENSEX"]
    assert s.id == "sensex_vwap_mean_reversion"


def test_live_lookback_reaches_session_open():
    """335 bars are needed to reach 09:15 from the 14:50 cutoff. Below that the
    live session VWAP diverges from the backtest for the rest of the day."""
    assert _strategy().live_lookback_bars >= 335


def test_mode_switches_are_bool_so_the_optimizer_can_actually_sweep_them():
    """`_build_param_space` drops every param whose type is not int/float/bool.
    A str mode knob would be silently pinned — a dead knob wearing a filter's
    clothes, the same class as the removed `vix_boost_threshold`."""
    schema = _strategy().parameter_schema
    for name in ("fade_mode", "use_sigma_basis", "use_rsi_filter",
                 "block_trending", "flow_gate"):
        assert schema[name]["type"] == "bool", f"{name} must be bool to be searchable"
    for name, defn in schema.items():
        if defn["type"] in ("int", "float"):
            assert "min" in defn and "max" in defn, (
                f"{name} declares no bounds; the optimizer would pin it to its default")


def test_declared_data_columns_are_high_coverage_only():
    """`ce_oi_delta_z` / `pe_oi_delta_z` cover 38% of the SENSEX window. A rule
    reading them is inert on the rest, which looks exactly like a filter."""
    declared = set(_strategy().required_data)
    assert declared == {"ce_volume_z", "pe_volume_z"}
    assert not (declared & {"ce_oi_delta_z", "pe_oi_delta_z"})


# --------------------------------------------------------------------------
# exits: the units failure this plugin exists to fix
# --------------------------------------------------------------------------

def test_exits_are_atr_multiples_not_points():
    s = _strategy()
    p = s.default_params()
    df = _frame(stretch_at=400)
    sig = _eval(s, df, 400, p)
    assert sig.direction == "PE"
    atr = float(df["atr"].iloc[400])
    assert sig.spot_target_pts == pytest.approx(atr * p["spot_target_atr"], rel=1e-6)


def test_exits_scale_with_the_instrument_and_a_points_mutant_does_not():
    """THE decisive test for this plugin's reason to exist.

    Scaling one frame by a constant is a tautology on its own: a strategy that
    returns a hardcoded 40 points passes any test that only checks "a number came
    back". So the mutant is built explicitly and must FAIL the same assertion the
    real strategy passes.
    """
    s = _strategy()
    p = s.default_params()

    small = _frame(scale=1.0, stretch_at=400)
    large = _frame(scale=3.28, stretch_at=400)   # SENSEX-vs-NIFTY point scale

    sig_small = _eval(s, small, 400, p)
    sig_large = _eval(s, large, 400, p)
    assert sig_small.direction == sig_large.direction == "PE"

    ratio = sig_large.spot_stop_pts / sig_small.spot_stop_pts
    assert ratio == pytest.approx(3.28, rel=1e-3), (
        "stop must scale with the instrument's point scale")

    # The mutant: same signal, exits in absolute points like the parent.
    class _PointsMutant(type(s)):
        def evaluate(self, row, prev, params, ctx):
            sig = super().evaluate(row, prev, params, ctx)
            if sig.direction in ("CE", "PE"):
                sig.spot_stop_pts = 12.0        # parent default
                sig.spot_target_pts = 18.0
            return sig

    m = _PointsMutant()
    m_small = m.evaluate(small.iloc[400], small.iloc[399], p, _ctx(m, small, 400, p))
    m_large = m.evaluate(large.iloc[400], large.iloc[399], p, _ctx(m, large, 400, p))
    m_ratio = m_large.spot_stop_pts / m_small.spot_stop_pts
    assert m_ratio == pytest.approx(1.0), "mutant sanity: it does not scale"
    assert m_ratio != pytest.approx(3.28, rel=1e-3), (
        "the scale-transfer assertion must be able to FAIL — otherwise it proves nothing")


def test_stop_floor_keeps_the_stop_out_of_one_bar_noise():
    """87% of the parent family's SENSEX trades died at a sub-ATR stop. The floor
    lives on the schema so the optimizer cannot search below it."""
    assert _strategy().parameter_schema["spot_stop_atr"]["min"] == MIN_STOP_ATR
    assert MIN_STOP_ATR > 0.5


@pytest.mark.parametrize("blend,expect", [(0.0, "instant"), (1.0, "baseline")])
def test_stop_basis_blend_moves_between_instant_and_baseline_atr(blend, expect):
    s = _strategy()
    p = {**s.default_params(), "stop_basis_blend": blend, "atr_rank_window": 60}
    df = _frame(n=400)
    # Make the CURRENT bar's ATR far from the trailing baseline, THEN size the
    # displacement against that new ATR — bumping it afterwards would shrink the
    # stretch below the entry threshold and fire nothing.
    df.loc[350, "atr"] = float(df["atr"].iloc[349]) * 5.0
    df.loc[350, "close"] = df["vwap"].iloc[350] + 6.0 * df["atr"].iloc[350]
    sig = _eval(s, df, 350, p)
    assert sig.direction == "PE", "the blend test needs a live signal to inspect"
    instant = float(df["atr"].iloc[350])
    implied = sig.spot_stop_pts / p["spot_stop_atr"]
    if expect == "instant":
        assert implied == pytest.approx(instant, rel=1e-6)
    else:
        assert implied < instant * 0.9, "blend=1 must lean on the trailing baseline"


# --------------------------------------------------------------------------
# causality
# --------------------------------------------------------------------------

def test_atr_rank_is_causal_not_session_wide():
    """A trailing rank cannot be computed before its window fills. A session-wide
    percentile would be defined everywhere — that difference IS the look-ahead."""
    s = _strategy()
    p = {**s.default_params(), "atr_rank_window": 240}
    df = _frame(n=600)
    block = s.session_precompute(df, p)[_REGIME_CTX_KEY]
    rank = block["rank"]
    assert np.isnan(rank[:239]).all(), "ranks must be undefined before the window fills"
    assert np.isfinite(rank[239:]).any()


def test_atr_rank_of_a_prefix_matches_the_full_frame():
    """Evaluating bar i must not depend on bars after i."""
    s = _strategy()
    p = {**s.default_params(), "atr_rank_window": 60}
    df = _frame(n=600)
    full = s.session_precompute(df, p)[_REGIME_CTX_KEY]["rank"]
    prefix = s.session_precompute(df.iloc[:300].copy(), p)[_REGIME_CTX_KEY]["rank"]
    np.testing.assert_allclose(full[:300], prefix, equal_nan=True)


def test_bars_in_session_resets_per_day():
    s = _strategy()
    p = s.default_params()
    df = _frame(n=600)
    bars_in = s.session_precompute(df, p)[_REGIME_CTX_KEY]["bars_in"]
    boundary = (df["session_date"] != df["session_date"].shift()).to_numpy().nonzero()[0]
    for b in boundary:
        assert bars_in[b] == 0, "session progress must restart each day"


# --------------------------------------------------------------------------
# gates
# --------------------------------------------------------------------------

def test_direction_flips_between_fade_and_continuation():
    s = _strategy()
    df = _frame(stretch_at=400)
    fade = _eval(s, df, 400, {**s.default_params(), "fade_mode": True})
    cont = _eval(s, df, 400, {**s.default_params(), "fade_mode": False,
                              "use_rsi_filter": False})
    assert fade.direction == "PE", "stretched above VWAP, faded -> buy puts"
    assert cont.direction == "CE", "stretched above VWAP, joined -> buy calls"


def test_missing_regime_blocks_by_name_not_silently():
    """The parent emits an unnamed blocker for a column it never required, so a
    frame without `regime` looks identical to a working filter."""
    s = _strategy()
    df = _frame(stretch_at=400).drop(columns=["regime"])
    sig = _eval(s, df, 400, {**s.default_params(), "block_trending": True})
    assert any("regime unavailable" in b for b in sig.blockers)


def test_regime_gate_blocks_trending_and_admits_others():
    s = _strategy()
    p = {**s.default_params(), "block_trending": True}
    for regime in TRENDING_REGIMES:
        df = _frame(stretch_at=400); df["regime"] = regime
        assert any("trending" in b for b in _eval(s, df, 400, p).blockers)
    df = _frame(stretch_at=400); df["regime"] = "CHOP"
    assert not any("trending" in b for b in _eval(s, df, 400, p).blockers)


def test_regime_gate_off_by_default_does_not_read_regime():
    s = _strategy()
    df = _frame(stretch_at=400).drop(columns=["regime"])
    sig = _eval(s, df, 400, s.default_params())
    assert not any("regime" in b for b in sig.blockers)


def test_flow_gate_is_inert_by_name_when_columns_are_absent():
    s = _strategy()
    df = _frame(stretch_at=400)          # no ce_volume_z / pe_volume_z
    sig = _eval(s, df, 400, {**s.default_params(), "flow_gate": True})
    assert any("option flow unavailable" in b for b in sig.blockers)


def test_flow_gate_requires_agreement_with_trade_direction():
    s = _strategy()
    p = {**s.default_params(), "flow_gate": True, "flow_imbalance_min": 2.0}
    df = _frame(stretch_at=400)
    # Fade of a pop -> PE. Agreement means PUT aggression, i.e. ce_z - pe_z <= -2.
    df["ce_volume_z"] = 0.0; df["pe_volume_z"] = 3.0
    assert not any("flow" in b for b in _eval(s, df, 400, p).blockers)
    df["ce_volume_z"] = 3.0; df["pe_volume_z"] = 0.0
    assert any("does not confirm" in b for b in _eval(s, df, 400, p).blockers)


def test_atr_rank_band_actually_gates():
    s = _strategy()
    df = _frame(n=600, stretch_at=400)
    wide = _eval(s, df, 400, {**s.default_params(), "atr_rank_window": 60,
                              "min_atr_rank": 0.0, "max_atr_rank": 1.0})
    narrow = _eval(s, df, 400, {**s.default_params(), "atr_rank_window": 60,
                                "min_atr_rank": 0.99, "max_atr_rank": 1.0})
    assert not any("ATR rank" in b for b in wide.blockers)
    assert any("ATR rank" in b for b in narrow.blockers)


def test_session_progress_gate_blocks_only_early_bars():
    s = _strategy()
    p = {**s.default_params(), "min_session_bars": 30}
    df = _frame(n=600)
    df.loc[5, "close"] = df["vwap"].iloc[5] + 6 * df["atr"].iloc[5]
    df.loc[200, "close"] = df["vwap"].iloc[200] + 6 * df["atr"].iloc[200]
    assert any("of session" in b for b in _eval(s, df, 5, p).blockers)
    assert not any("of session" in b for b in _eval(s, df, 200, p).blockers)


def test_sigma_basis_changes_the_displacement_measure():
    s = _strategy()
    df = _frame(n=600)
    # sigma = 0.8 * atr, so a 5-ATR push is 6.25 sigma: crosses a 6.0 threshold
    # on the sigma basis but not on the ATR basis.
    i = 400
    df.loc[i, "close"] = df["vwap"].iloc[i] + 5.0 * df["atr"].iloc[i]
    p = {**s.default_params(), "stretch_mult": 6.0, "use_rsi_filter": False}
    assert _eval(s, df, i, {**p, "use_sigma_basis": False}).direction == "NONE"
    assert _eval(s, df, i, {**p, "use_sigma_basis": True}).direction == "PE"


# --------------------------------------------------------------------------
# warmup / degenerate input
# --------------------------------------------------------------------------

def test_warmup_bars_are_not_admitted_as_if_they_passed():
    s = _strategy()
    df = _frame(stretch_at=400)
    df.loc[400, "atr"] = np.nan
    assert _eval(s, df, 400, s.default_params()).direction == "NONE"


def test_zero_atr_means_no_trade():
    s = _strategy()
    df = _frame(stretch_at=400)
    df.loc[400, "atr"] = 0.0
    sig = _eval(s, df, 400, s.default_params())
    assert sig.direction == "NONE"
    assert any("ATR" in b for b in sig.blockers)


def test_sigma_basis_without_sigma_column_does_not_trade():
    s = _strategy()
    df = _frame(stretch_at=400).drop(columns=["vwap_sigma"])
    sig = _eval(s, df, 400, {**s.default_params(), "use_sigma_basis": True})
    assert sig.direction == "NONE"


def test_emitted_signals_are_valid():
    """Signal validation is shared by backtest, paper and live; a NaN target
    would poison saved metrics rather than raise."""
    from app.strategies.base import validate_signal
    s = _strategy()
    df = _frame(stretch_at=400)
    for fade in (True, False):
        sig = _eval(s, df, 400, {**s.default_params(), "fade_mode": fade,
                                 "use_rsi_filter": False})
        validate_signal(sig)


# --------------------------------------------------------------------------
# the parent must not move
# --------------------------------------------------------------------------

def test_the_parent_strategy_is_untouched():
    """NIFTY results depend on `vwap_mean_reversion`. This plugin is additive."""
    parent = (PLUGINS / "vwap_mean_reversion.py").read_text(encoding="utf-8")
    assert 'id = "vwap_mean_reversion"' in parent
    assert '"spot_target_pts": {"type": "float", "min": 5, "max": 80, "default": 18}' in parent
    assert '"spot_stop_pts": {"type": "float", "min": 3, "max": 60, "default": 12}' in parent
    assert "sensex" not in parent.lower()
