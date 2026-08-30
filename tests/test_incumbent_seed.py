"""The optimizer must never return worse than a known-good point in its own space.

MEASURED motivation (confluence_scalper / NIFTY, 2025-11-01..2026-08-26, declared
bounds, 11-dim space): the optimizer searched 288 trials and returned -19,957 INR of
real option P&L, while the operator's saved preset — every value of which lies INSIDE
those declared bounds — scores +77,129 INR. The preset was never evaluated because no
code path calls study.enqueue_trial.

These cover the pure seeding helpers. The wiring into optimizer.py is covered by
tests/test_optimizer_incumbent_wiring.py.
"""
from __future__ import annotations

import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "backend"))

from app.incumbent_seed import (  # noqa: E402
    BAD_TYPE,
    FIXED_DIMENSION,
    NOT_IN_SPACE,
    OUT_OF_BOUNDS,
    build_seed_trials,
    clean_seed_params,
)

# Mirrors confluence_scalper.parameter_schema (backend/app/strategies/builtin).
SPACE = {
    "ema_fast": {"type": "int", "min": 5, "max": 30, "default": 9},
    "ema_slow": {"type": "int", "min": 10, "max": 60, "default": 21},
    "rsi_bull_thr": {"type": "float", "min": 50, "max": 70, "default": 52},
    "rsi_bear_thr": {"type": "float", "min": 30, "max": 50, "default": 48},
    "signal_threshold": {"type": "int", "min": 40, "max": 95, "default": 62},
    "cooldown_bars": {"type": "int", "min": 1, "max": 30, "default": 5},
    "spot_target_pts": {"type": "float", "min": 5, "max": 200, "default": 35},
    "spot_stop_pts": {"type": "float", "min": 3, "max": 100, "default": 18},
    "only_in_trend_regime": {"type": "bool", "default": True},
    "use_vwap_inhibit": {"type": "bool", "default": True},
    "vwap_inhibit_pts": {"type": "float", "min": 20, "max": 500, "default": 100},
}

# The operator's real saved preset "Confluence NIFTY · Nov-Aug-26 331%".
PRESET = {
    "ema_fast": 20, "ema_slow": 23,
    "rsi_bull_thr": 54.19293464487398, "rsi_bear_thr": 38.211349783384506,
    "signal_threshold": 59, "cooldown_bars": 19,
    "spot_target_pts": 161.35554902405147, "spot_stop_pts": 96.29802628652973,
    "only_in_trend_regime": False, "use_vwap_inhibit": False,
    "vwap_inhibit_pts": 280.1991379379354,
}


def test_the_known_good_preset_is_fully_seedable():
    """THE regression this work exists for: every one of the 11 preset values is
    inside the declared bounds, so nothing may be dropped."""
    kept, dropped = clean_seed_params(SPACE, PRESET)
    assert dropped == {}
    assert kept == PRESET
    assert len(kept) == 11


def test_out_of_range_is_dropped_not_clamped():
    """Clamping would enqueue a DIFFERENT point under the incumbent's name."""
    kept, dropped = clean_seed_params(SPACE, {**PRESET, "spot_target_pts": 285.7})
    assert dropped == {"spot_target_pts": OUT_OF_BOUNDS}
    assert "spot_target_pts" not in kept
    # the clamp-to-200 behaviour we explicitly do NOT want:
    assert kept.get("spot_target_pts") != 200
    # everything else still survives, so the seed is still useful
    assert len(kept) == 10


def test_unknown_and_fixed_dimensions_are_dropped():
    space = {**SPACE, "pinned": {"type": "int", "fixed": 7}}
    kept, dropped = clean_seed_params(
        space, {"ema_fast": 20, "fib_entry_low": 0.4, "pinned": 7})
    assert kept == {"ema_fast": 20}
    assert dropped["fib_entry_low"] == NOT_IN_SPACE      # another strategy's param
    assert dropped["pinned"] == FIXED_DIMENSION          # _suggest never asks for it


def test_bool_dimension_requires_a_real_bool():
    """suggest_categorical(name, [True, False]) — 1/'true' are not those objects."""
    kept, dropped = clean_seed_params(SPACE, {"only_in_trend_regime": 1})
    assert kept == {} and dropped["only_in_trend_regime"] == BAD_TYPE
    kept, dropped = clean_seed_params(SPACE, {"only_in_trend_regime": False})
    assert kept == {"only_in_trend_regime": False} and dropped == {}


def test_bool_is_not_accepted_as_an_int_or_float():
    """bool subclasses int, so True would silently seed 1."""
    _, d_int = clean_seed_params(SPACE, {"ema_fast": True})
    _, d_flt = clean_seed_params(SPACE, {"spot_target_pts": True})
    assert d_int["ema_fast"] == BAD_TYPE
    assert d_flt["spot_target_pts"] == BAD_TYPE


def test_bounds_are_inclusive():
    kept, dropped = clean_seed_params(
        SPACE, {"ema_fast": 5, "ema_slow": 60, "spot_stop_pts": 100.0})
    assert dropped == {}
    assert kept == {"ema_fast": 5, "ema_slow": 60, "spot_stop_pts": 100.0}


def test_non_numeric_value_is_dropped_not_crashed():
    kept, dropped = clean_seed_params(SPACE, {"ema_fast": "twenty", "ema_slow": None})
    assert kept == {}
    assert dropped == {"ema_fast": BAD_TYPE, "ema_slow": BAD_TYPE}


# --- build_seed_trials -------------------------------------------------------

def test_seeds_are_ordered_deduped_and_capped():
    cands = [
        {"source": "preset:Confluence", "params": PRESET},
        {"source": "defaults", "params": {k: v["default"] for k, v in SPACE.items()}},
        {"source": "duplicate", "params": dict(PRESET)},          # same point
        {"source": "prior_job", "params": {"ema_fast": 11}},
    ]
    seeds = build_seed_trials(SPACE, cands, max_seeds=8)
    assert [s["source"] for s in seeds] == ["preset:Confluence", "defaults", "prior_job"]
    assert seeds[0]["params"] == PRESET


def test_cap_limits_the_trial_budget_spent_on_seeds():
    cands = [{"source": f"p{i}", "params": {"ema_fast": 5 + i}} for i in range(20)]
    assert len(build_seed_trials(SPACE, cands, max_seeds=3)) == 3


def test_candidate_with_nothing_seedable_is_skipped_entirely():
    """Enqueueing {} would burn a trial on a random point labelled 'incumbent'."""
    seeds = build_seed_trials(SPACE, [{"source": "other_strategy",
                                       "params": {"fib_entry_low": 0.4, "stop_atr_mult": 3}}])
    assert seeds == []


def test_dropped_reasons_are_reported_for_the_operator():
    """The operator asked to SEE which bounds were in force; a partially
    unreachable incumbent must say so rather than look fully seeded."""
    seeds = build_seed_trials(
        SPACE, [{"source": "preset:X", "params": {**PRESET, "spot_target_pts": 285.7}}])
    assert seeds[0]["dropped"] == {"spot_target_pts": OUT_OF_BOUNDS}
    assert "spot_target_pts" not in seeds[0]["params"]


def test_empty_inputs_are_safe():
    assert build_seed_trials(SPACE, []) == []
    assert build_seed_trials(SPACE, None) == []
    assert clean_seed_params({}, PRESET)[0] == {}
    assert clean_seed_params(SPACE, {}) == ({}, {})
