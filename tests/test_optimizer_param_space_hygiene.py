"""The optimizer must not invent bounds, and must not tune position size.

User-reported (2026-07-29). They optimized the AI-authored
`algotest_option_buy_nifty` and got `lots: 100` — with the option form set to 5 —
plus an "impossible" account value. Their real job document
(`optimization_jobs/cf53e98f-…`) shows exactly why:

    param_space: "lots": {"type":"int","default":2}      <- no min, no max
    best_params: lots: 100                                <- the [0,100] ceiling
    best_value:  5,929,384.5    (Rs 59.3 lakh)

Two independent defects produced that:

1. **Fabricated bounds.** `_suggest` defaulted an unbounded int to `[0, 100]`
   and an unbounded float to `[0.0, 1.0]`. Neither default has anything to do
   with the parameter's meaning: a percent knob written `20.0` instead of `20`
   silently gets a 0-1% search range. Worse, `_grid_combinations` read the SAME
   schema with `info["min"]`, so Grid Search raised `KeyError: 'min'` and failed
   the whole job with a one-character error message.

2. **Position size treated as alpha.** Every rupee objective is monotonic in
   `lots`, so searching it is not tuning, it is leverage-seeking — the optimizer
   pins it to whatever ceiling it is handed. The shipped `premium_momentum`
   plugin never declares `lots` at all and hand-writes `"fixed"` on its risk
   knobs; the AI compiler reproduced neither protection.

The fix reuses the mechanism the codebase already has — `"fixed"` — so a param
that cannot honestly be searched is PINNED to its declared default rather than
dropped. That keeps it flowing into `merged_params` (the strategy still runs with
it) while removing it as a search dimension, and it fixes the grid KeyError for
free because `_grid_combinations` already handles `"fixed"`.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from app.optimizer import (  # noqa: E402
    NON_ALPHA_PARAM_NAMES,
    _build_param_space,
    _grid_combinations,
)

#: The user's actual authored plugin schema, verbatim from
#: backend/app/strategies/plugins/algotest_option_buy_nifty.py.
AUTHORED = {
    "reference_time": {"type": "str", "default": "09:31"},
    "moneyness": {"type": "str", "default": "itm1"},
    "side": {"type": "str", "default": "first_to_trigger"},
    "leg_mode": {"type": "str", "default": "both"},
    "momentum_pct": {"type": "int", "default": 15},
    "stop_pct": {"type": "int", "default": 20},
    "target_pct": {"type": "int", "default": 50},
    "trail_x_pct": {"type": "int", "default": 5},
    "trail_y_pct": {"type": "int", "default": 5},
    "lots": {"type": "int", "default": 2},
    "lazy_enabled": {"type": "bool", "default": True},
}


def _searchable(space):
    """Names the optimizer will actually vary (a 'fixed' entry is pinned)."""
    return {k for k, v in space.items() if "fixed" not in v}


# --------------------------------------------------------------------------- #
# defect 2 — position size is not a search dimension
# --------------------------------------------------------------------------- #

def test_lots_is_never_searched():
    """THE reported bug: lots=100 was the range ceiling, not a discovery."""
    space = _build_param_space(AUTHORED, None)
    assert "lots" not in _searchable(space), (
        "position size is monotonic in every rupee objective — searching it "
        "makes the optimizer a leverage maximizer, not a strategy tuner")


def test_lots_still_reaches_the_strategy_with_its_declared_default():
    """Pinned, NOT dropped: the sim must still receive the configured size."""
    space = _build_param_space(AUTHORED, None)
    assert space["lots"]["fixed"] == 2


def test_the_denylist_names_sizing_and_risk_knobs_not_alpha_knobs():
    assert "lots" in NON_ALPHA_PARAM_NAMES
    for alpha in ("momentum_pct", "stop_pct", "target_pct"):
        assert alpha not in NON_ALPHA_PARAM_NAMES, (
            f"{alpha} is a real alpha knob and must stay tunable")


def test_an_explicit_user_override_can_still_force_a_sizing_sweep():
    """Escape hatch: if the user deliberately supplies bounds for lots, that is
    their call — but it must be an explicit act, never the default."""
    space = _build_param_space(AUTHORED, {"lots": {"min": 1, "max": 5}})
    assert "lots" in _searchable(space)


# --------------------------------------------------------------------------- #
# defect 1 — bounds are never invented
# --------------------------------------------------------------------------- #

def test_an_unbounded_numeric_param_is_pinned_not_invented():
    """`trail_y_pct` has no min/max. It must not silently become [0,100]."""
    space = _build_param_space(AUTHORED, None)
    for name in ("momentum_pct", "stop_pct", "trail_x_pct", "trail_y_pct"):
        assert "fixed" in space[name], (
            f"{name} has no declared bounds; searching it over an invented "
            f"range produced junk like trail_y_pct=78")


def test_a_bounded_param_is_still_searched():
    """The fix must not disarm legitimate optimization."""
    schema = {"momentum_pct": {"type": "int", "min": 5, "max": 50, "default": 15}}
    space = _build_param_space(schema, None)
    assert "momentum_pct" in _searchable(space)
    assert space["momentum_pct"]["min"] == 5 and space["momentum_pct"]["max"] == 50


def test_a_float_without_bounds_is_not_searched_over_0_to_1():
    """The 100x scale trap: `stop_pct: 20.0` must not become a 0-1% search."""
    schema = {"stop_pct": {"type": "float", "default": 20.0}}
    space = _build_param_space(schema, None)
    assert "fixed" in space["stop_pct"]


def test_bools_are_still_searchable_without_bounds():
    """min/max are meaningless for a bool — it has an exhaustive domain."""
    space = _build_param_space(AUTHORED, None)
    assert "lazy_enabled" in _searchable(space)


def test_partial_bounds_are_not_enough():
    """min-only or max-only still leaves the other end to be invented."""
    for half in ({"min": 5}, {"max": 40}):
        schema = {"p": dict({"type": "int", "default": 10}, **half)}
        assert "fixed" in _build_param_space(schema, None)["p"]


# --------------------------------------------------------------------------- #
# the grid/bayesian disagreement that crashed the job outright
# --------------------------------------------------------------------------- #

def test_grid_search_no_longer_raises_KeyError_on_the_authored_schema():
    """`_grid_combinations` read info["min"] while `_suggest` read .get("min",0).
    Grid Search therefore died with `KeyError: 'min'` and the user saw an error
    message that was literally one character long."""
    space = _build_param_space(AUTHORED, None)
    combos = _grid_combinations(space, max_trials=50)
    assert combos, "grid produced no combinations"


def test_grid_holds_lots_constant_at_its_default():
    space = _build_param_space(AUTHORED, None)
    for combo in _grid_combinations(space, max_trials=50):
        assert combo.get("lots") == 2


def test_string_params_are_still_ignored_entirely():
    """Pre-existing behaviour: str params were never optimizable."""
    space = _build_param_space(AUTHORED, None)
    for name in ("reference_time", "moneyness", "side", "leg_mode"):
        assert name not in space


# --------------------------------------------------------------------------- #
# indicator periods are provably inert for a premium-native strategy
# --------------------------------------------------------------------------- #

def test_indicator_periods_are_not_injected_for_a_premium_native_strategy():
    """The user's real job searched 22 dimensions, TEN of them indicator periods
    (ema_fast, rsi_length, macd_*, atr_length, adx_length, chop_length,
    swing_lookback) — for a strategy whose `evaluate()` is an inert stub and
    which reads no indicator at all. Nearly half the trial budget was spent on
    dimensions that provably cannot move the objective."""
    from app.optimizer import resolve_indicator_period_search

    assert resolve_indicator_period_search(True, premium_native=True) is False
    assert resolve_indicator_period_search(True, premium_native=False) is True


def test_the_user_opting_out_is_still_respected_for_ordinary_strategies():
    from app.optimizer import resolve_indicator_period_search

    assert resolve_indicator_period_search(False, premium_native=False) is False


# --------------------------------------------------------------------------- #
# a searched dimension must be one the engine actually consumes
# --------------------------------------------------------------------------- #

def test_a_param_the_engine_never_reads_is_not_searched():
    """The user's 2026-07-29 job searched `lazy_enabled`, `lazy_momentum_pct` and
    `lazy_stop_pct` while the dispatcher discarded all three — so its
    `lazy_enabled: false` verdict was noise fitted to an unchanging result.
    Tuning a knob that cannot move the objective is never valid, however cheap."""
    from app.optimizer import restrict_space_to_engine_params

    space = {"momentum_pct": {"type": "int", "min": 5, "max": 50, "default": 15},
             "phantom_knob": {"type": "int", "min": 1, "max": 9, "default": 3}}
    out = restrict_space_to_engine_params(space, premium_native=True)
    assert "momentum_pct" in out and "fixed" not in out["momentum_pct"]
    assert "fixed" in out["phantom_knob"], "an inert dimension must be pinned"


def test_the_shipped_lazy_knobs_ARE_consumed_now_and_stay_searchable():
    """After the dispatch widening they reach the engine, so they are legitimate
    search dimensions again — the restriction must track reality, not a denylist."""
    from app.optimizer import restrict_space_to_engine_params

    space = {k: {"type": "int", "min": 5, "max": 40, "default": 10}
             for k in ("lazy_momentum_pct", "lazy_stop_pct")}
    out = restrict_space_to_engine_params(space, premium_native=True)
    for k in space:
        assert "fixed" not in out[k], f"{k} is engine-consumed and must stay tunable"


def test_ordinary_strategies_are_untouched():
    """Their params go through evaluate(), not the premium engine."""
    from app.optimizer import restrict_space_to_engine_params

    space = {"ema_fast": {"type": "int", "min": 3, "max": 20, "default": 9}}
    out = restrict_space_to_engine_params(space, premium_native=False)
    assert out == space


# --------------------------------------------------------------------------- #
# Trade-FREQUENCY knobs are exposure, not alpha
#
# `OPTION_BUYING_MICROSTRUCTURE_2026-08.md` §2 measured round-trip friction at
# 32-90% of the median favourable move at 3-5 minute horizons and concluded
# "more trades is the wrong direction in this app regardless of signal quality".
# A knob that multiplies trade count therefore moves a rupee objective mostly by
# changing EXPOSURE, which is the same failure mode as searching `lots`.
#
# Policy (operator's decision, 2026-08-23): the schema range stays WIDE so the
# knob is fully controllable by hand in the UI, while the optimizer leaves it
# pinned unless the run explicitly supplies both bounds.
# --------------------------------------------------------------------------- #

from app.strategies.plugins.expiry_regime_trend_continuation import (  # noqa: E402
    ExpiryRegimeTrendContinuation,
)

_B_SCHEMA = ExpiryRegimeTrendContinuation.parameter_schema


def test_trade_frequency_knobs_are_pinned_by_default():
    space = _build_param_space(_B_SCHEMA, None)
    searched = _searchable(space)
    assert "max_trades_per_session" not in searched
    assert "signal_cooldown_bars" not in searched


def test_pinned_frequency_knobs_still_reach_the_strategy_at_their_default():
    """Pinned, NOT dropped — same contract as `lots`."""
    space = _build_param_space(_B_SCHEMA, None)
    assert space["max_trades_per_session"]["fixed"] == 1
    assert space["signal_cooldown_bars"]["fixed"] == 15


def test_the_real_alpha_knobs_stay_tunable():
    """The pin must be surgical: stop/target/hold geometry is the hypothesis."""
    searched = _searchable(_build_param_space(_B_SCHEMA, None))
    for alpha in ("stop_bps", "stop_atr_mult", "target_mult", "range_mult",
                  "hold_max_minutes", "entry_cutoff_minutes_after_open"):
        assert alpha in searched, f"{alpha} is a real alpha knob and must stay tunable"


def test_the_schema_range_stays_wide_for_manual_control():
    """Pinning is an OPTIMIZER default, not a restriction on the operator. The
    UI renders these bounds, so narrowing them here would remove hand control."""
    assert _B_SCHEMA["max_trades_per_session"]["max"] >= 10
    assert _B_SCHEMA["signal_cooldown_bars"]["max"] >= 120


def test_an_explicit_override_can_still_sweep_trade_frequency():
    space = _build_param_space(_B_SCHEMA, {
        "max_trades_per_session": {"min": 1, "max": 4},
        "signal_cooldown_bars": {"min": 5, "max": 60},
    })
    searched = _searchable(space)
    assert "max_trades_per_session" in searched
    assert "signal_cooldown_bars" in searched


def test_signal_threshold_is_actually_pinned_not_just_documented_as_pinned():
    """B's score is the constant 65, so every value <= 65 behaves identically and
    every value above it suppresses EVERY signal. That makes the knob an on/off
    switch, not a tuning dimension.

    Left searchable, the optimizer learns the off switch and attributes the
    result to it: the operator's 426-trial NIFTY job on 2026-08-23 assigned
    **80.2%** of parameter importance to `signal_threshold`, burying the
    dimensions that actually shape the trade. The schema comment already claimed
    it was pinned; nothing enforced that.
    """
    space = _build_param_space(_B_SCHEMA, None)
    assert "signal_threshold" not in _searchable(space)
    assert space["signal_threshold"]["fixed"] <= 65, (
        "a pinned value above the fixed score would suppress every signal")


# --------------------------------------------------------------------------- #
# Finding #32 — the gap between the stated principle and the enforced one
#
# `NON_ALPHA_PARAM_NAMES` argues generally ("more trades is the wrong direction
# in this app regardless of signal quality") but matches on the NAME, and the
# frequency spellings it carries are declared by ONE plugin. Eleven others spell
# the same concept `cooldown_bars` and are still swept.
#
# The operator's decision (2026-08-26) was to RECORD that gap, not close it:
# adding the name would silently change the default search space of eleven
# already-optimized strategies, which is an evidence-bearing decision.
#
# These tests exist so the gap cannot drift shut — or drift WIDER — in silence.
# They deliberately pin code against documentation: change one without the other
# and this file goes red, which is the whole point.
# --------------------------------------------------------------------------- #

import importlib  # noqa: E402
import pkgutil  # noqa: E402
from pathlib import Path  # noqa: E402

_REGISTER = Path(__file__).resolve().parents[1] / "docs" / "BACKTEST_INTEGRITY_AUDIT.md"


def _plugins_declaring(param_name):
    """Every plugin module whose strategy declares `param_name` in its schema.

    Iterates EVERY attribute of each module on purpose. Taking the first object
    that has a `parameter_schema` picks up the imported `StrategyBase` and
    silently returns an empty set — the exact bug that made the first version of
    the documented repro command print `[]`.
    """
    import app.strategies.plugins as plugins
    found = set()
    for mod_info in pkgutil.iter_modules(plugins.__path__):
        mod = importlib.import_module(f"app.strategies.plugins.{mod_info.name}")
        for attr in vars(mod).values():
            schema = getattr(attr, "parameter_schema", None)
            if isinstance(schema, dict) and param_name in schema:
                found.add(mod_info.name)
    return found


def test_the_helper_itself_finds_something():
    """Guard the guard: an empty set would make every test below vacuously true."""
    assert _plugins_declaring("cooldown_bars"), (
        "the plugin scan found nothing — the scan is broken, not the codebase")


def test_the_cooldown_bars_gap_is_recorded_not_forgotten():
    """If the gap is open in code, the register must say so — and vice versa."""
    register = _REGISTER.read_text(encoding="utf-8")
    gap_open_in_code = "cooldown_bars" not in NON_ALPHA_PARAM_NAMES

    if gap_open_in_code:
        assert "| 32 |" in register, (
            "`cooldown_bars` is still swept by the optimizer, but finding #32 is "
            "missing from docs/BACKTEST_INTEGRITY_AUDIT.md. An undocumented gap "
            "is an oversight; a documented one is a decision.")
        assert "deliberately deferred" in register
    else:
        # NOT `or "Closed" in register`: the register already contains "Closed
        # 2026-07-31" and "Closed 2026-08-01" headings for earlier findings, so
        # that spelling matches unconditionally and the check never fires. A
        # mutation run on 2026-08-26 confirmed it — closing the gap in code left
        # this test GREEN with the register still calling #32 deferred, which is
        # precisely the silent tidy-up the test exists to prevent.
        assert "deliberately deferred" not in register, (
            "`cooldown_bars` is now pinned, so finding #32 is no longer deferred "
            "— move it out of the open section of "
            "docs/BACKTEST_INTEGRITY_AUDIT.md and record which eleven "
            "strategies' default search spaces this changed.")


def test_the_register_count_matches_reality():
    """The register commits to `11`. If a new plugin adds the knob, the recorded
    finding is stale and understates the blast radius of closing it."""
    actual = _plugins_declaring("cooldown_bars")
    assert len(actual) == 11, (
        f"{len(actual)} plugins now declare `cooldown_bars` ({sorted(actual)}), "
        "but finding #32 in docs/BACKTEST_INTEGRITY_AUDIT.md says eleven. "
        "Update the register — the blast radius of closing the gap has changed.")


def test_the_narrow_spellings_really_are_narrow():
    """The premise of #32: the pinned names cover exactly one plugin. If another
    plugin adopts them, the gap has partially closed by accident."""
    for name in ("max_trades_per_session", "signal_cooldown_bars"):
        assert name in NON_ALPHA_PARAM_NAMES
        assert _plugins_declaring(name) == {"expiry_regime_trend_continuation"}, (
            f"`{name}` is no longer declared by exactly one plugin, so finding "
            "#32's premise has changed and the register needs re-reading.")
