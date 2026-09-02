"""Opt-in `pct_of_index` search bounds.

Point-denominated bounds do not transfer across instruments: SENSEX runs at
~3.28x NIFTY's index level at IDENTICAL relative volatility, so a box of
`spot_stop_pts <= 80` that comfortably holds NIFTY's +Rs 514,052 optimum (stop
77.16 = 0.317% of index, a true interior peak) is ~3.3x tighter on SENSEX, where
the same geometry needs ~250 points. The SENSEX search could not reach a
profitable configuration at all and returned -Rs 932,976.

This module lets an operator express those bounds as a PERCENT OF INDEX so they
mean the same thing on every instrument. It is strictly OPT-IN: the default
resolves to today's behaviour byte-for-byte.

The single most dangerous failure mode is a percentage being read as points —
"0.32" meaning a 0.32-POINT stop instead of 250. Every path that cannot produce
a trustworthy reference price must therefore RAISE, never pass values through.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.bounds_unit import (  # noqa: E402
    BOUNDS_UNIT_PCT,
    BOUNDS_UNIT_POINTS,
    VALID_BOUNDS_UNITS,
    BoundsUnitError,
    normalize_bounds_unit,
    reference_index_price,
    resolve_bounds_overrides,
)

SCHEMA = {
    "sr_lookback": {"type": "int", "min": 20, "max": 120, "default": 60},
    "cooldown_bars": {"type": "int", "min": 1, "max": 30, "default": 6},
    "spot_target_pts": {"type": "float", "min": 5, "max": 200, "default": 40},
    "spot_stop_pts": {"type": "float", "min": 3, "max": 100, "default": 18},
    "trail_pts": {"type": "int", "min": 5, "max": 300, "default": 30},
}
NIFTY_REF, SENSEX_REF = 24468.0, 80176.0


def _resolve(**kw):
    kw.setdefault("parameter_schema", SCHEMA)
    return resolve_bounds_overrides(**kw)


# === THE SAFETY INVARIANT: default must change nothing =======================

def test_points_unit_returns_overrides_unchanged():
    ov = {"spot_target_pts": {"max": 200}, "spot_stop_pts": {"max": 80}}
    out, audit = _resolve(overrides=ov, bounds_unit=BOUNDS_UNIT_POINTS,
                          pct_params=["spot_stop_pts"], reference_price=SENSEX_REF)
    assert out == ov
    assert audit["applied"] is False


def test_absent_unit_defaults_to_points():
    ov = {"spot_stop_pts": {"min": 3, "max": 80}}
    out, audit = _resolve(overrides=ov, bounds_unit=None,
                          pct_params=["spot_stop_pts"], reference_price=SENSEX_REF)
    assert out == ov
    assert audit["applied"] is False


def test_pct_unit_with_no_selected_params_changes_nothing():
    ov = {"spot_stop_pts": {"max": 80}}
    out, audit = _resolve(overrides=ov, bounds_unit=BOUNDS_UNIT_PCT,
                          pct_params=[], reference_price=SENSEX_REF)
    assert out == ov
    assert audit["applied"] is False


def test_empty_overrides_are_inert_under_pct():
    out, audit = _resolve(overrides={}, bounds_unit=BOUNDS_UNIT_PCT,
                          pct_params=["spot_stop_pts"], reference_price=SENSEX_REF)
    assert out == {}
    assert audit["applied"] is False


def test_resolution_never_mutates_the_caller_dict():
    ov = {"spot_stop_pts": {"max": 0.317}}
    snapshot = {"spot_stop_pts": {"max": 0.317}}
    _resolve(overrides=ov, bounds_unit=BOUNDS_UNIT_PCT,
             pct_params=["spot_stop_pts"], reference_price=SENSEX_REF)
    assert ov == snapshot, "the persisted config must keep the AUTHORED percentages"


# === conversion maths ========================================================

def test_percent_converts_to_points_against_the_reference():
    out, audit = _resolve(overrides={"spot_stop_pts": {"min": 0.05, "max": 0.317}},
                          bounds_unit=BOUNDS_UNIT_PCT, pct_params=["spot_stop_pts"],
                          reference_price=SENSEX_REF)
    assert out["spot_stop_pts"]["max"] == pytest.approx(254.2, abs=0.5)
    assert out["spot_stop_pts"]["min"] == pytest.approx(40.1, abs=0.5)
    assert audit["applied"] is True
    assert audit["reference_price"] == SENSEX_REF


def test_the_geometry_that_was_unreachable_becomes_reachable():
    """NIFTY's winner: stop 77.16 pts = 0.3153% of 24,468; target 180.82 =
    0.7390%. Expressed as percentages those same numbers land at ~253/~592 on
    SENSEX — outside the 80/200 point box the SENSEX job was given."""
    stop_pct = 100 * 77.16 / NIFTY_REF
    tgt_pct = 100 * 180.82 / NIFTY_REF
    out, _ = _resolve(
        overrides={"spot_stop_pts": {"max": stop_pct}, "spot_target_pts": {"max": tgt_pct}},
        bounds_unit=BOUNDS_UNIT_PCT,
        pct_params=["spot_stop_pts", "spot_target_pts"], reference_price=SENSEX_REF)
    assert out["spot_stop_pts"]["max"] == pytest.approx(252.8, abs=2)
    assert out["spot_target_pts"]["max"] == pytest.approx(592.5, abs=3)


def test_same_percent_on_its_own_instrument_is_a_round_trip():
    pct = 100 * 77.16 / NIFTY_REF
    out, _ = _resolve(overrides={"spot_stop_pts": {"max": pct}},
                      bounds_unit=BOUNDS_UNIT_PCT, pct_params=["spot_stop_pts"],
                      reference_price=NIFTY_REF)
    assert out["spot_stop_pts"]["max"] == pytest.approx(77.16, abs=0.05)


def test_fixed_is_converted_too():
    out, _ = _resolve(overrides={"spot_stop_pts": {"fixed": 0.317}},
                      bounds_unit=BOUNDS_UNIT_PCT, pct_params=["spot_stop_pts"],
                      reference_price=SENSEX_REF)
    assert out["spot_stop_pts"]["fixed"] == pytest.approx(254.2, abs=0.5)


def test_int_typed_params_stay_integers():
    """optuna's int sampler cannot take float bounds."""
    out, _ = _resolve(overrides={"trail_pts": {"min": 0.05, "max": 0.30}},
                      bounds_unit=BOUNDS_UNIT_PCT, pct_params=["trail_pts"],
                      reference_price=SENSEX_REF)
    assert isinstance(out["trail_pts"]["min"], int)
    assert isinstance(out["trail_pts"]["max"], int)
    assert out["trail_pts"]["max"] == 241


def test_unselected_params_keep_their_native_units():
    out, _ = _resolve(
        overrides={"spot_stop_pts": {"max": 0.317}, "sr_lookback": {"max": 90}},
        bounds_unit=BOUNDS_UNIT_PCT, pct_params=["spot_stop_pts"],
        reference_price=SENSEX_REF)
    assert out["sr_lookback"] == {"max": 90}
    assert out["spot_stop_pts"]["max"] > 200


# === failing closed ==========================================================

@pytest.mark.parametrize("bad_ref", [None, 0, -1, float("nan"), float("inf")])
def test_unusable_reference_price_raises_rather_than_passing_percent_through(bad_ref):
    """If this returned the input untouched, "0.317" would become a 0.317-POINT
    stop — a sub-tick stop, the exact catastrophe this feature exists to end."""
    with pytest.raises(BoundsUnitError):
        _resolve(overrides={"spot_stop_pts": {"max": 0.317}},
                 bounds_unit=BOUNDS_UNIT_PCT, pct_params=["spot_stop_pts"],
                 reference_price=bad_ref)


def test_no_reference_needed_when_nothing_is_converted():
    """A missing reference must not break the default path."""
    out, _ = _resolve(overrides={"spot_stop_pts": {"max": 80}},
                      bounds_unit=BOUNDS_UNIT_POINTS, pct_params=[],
                      reference_price=None)
    assert out["spot_stop_pts"]["max"] == 80


@pytest.mark.parametrize("bad", ["percent", "pct", "POINTS_", "index_pct", 5])
def test_unknown_unit_raises(bad):
    with pytest.raises(BoundsUnitError):
        normalize_bounds_unit(bad)


def test_known_units_normalize():
    assert normalize_bounds_unit(None) == BOUNDS_UNIT_POINTS
    assert normalize_bounds_unit("") == BOUNDS_UNIT_POINTS
    assert normalize_bounds_unit("POINTS") == BOUNDS_UNIT_POINTS
    assert normalize_bounds_unit(" pct_of_index ") == BOUNDS_UNIT_PCT
    assert set(VALID_BOUNDS_UNITS) == {BOUNDS_UNIT_POINTS, BOUNDS_UNIT_PCT}


@pytest.mark.parametrize("bad_value", ["abc", None, float("nan")])
def test_non_numeric_bound_raises_rather_than_corrupting(bad_value):
    with pytest.raises(BoundsUnitError):
        _resolve(overrides={"spot_stop_pts": {"max": bad_value}},
                 bounds_unit=BOUNDS_UNIT_PCT, pct_params=["spot_stop_pts"],
                 reference_price=SENSEX_REF)


def test_negative_percent_raises():
    with pytest.raises(BoundsUnitError):
        _resolve(overrides={"spot_stop_pts": {"max": -0.3}},
                 bounds_unit=BOUNDS_UNIT_PCT, pct_params=["spot_stop_pts"],
                 reference_price=SENSEX_REF)


# === params that cannot bite are ignored, not fatal ==========================

def test_param_absent_from_schema_is_ignored_and_recorded():
    """Consistent with the existing foreign-override audit: it cannot affect the
    search, so it must not brick a cloned config whose strategy has moved on."""
    out, audit = _resolve(overrides={"ghost_pts": {"max": 0.3}},
                          bounds_unit=BOUNDS_UNIT_PCT, pct_params=["ghost_pts"],
                          reference_price=SENSEX_REF)
    assert out == {"ghost_pts": {"max": 0.3}}
    assert "ghost_pts" in audit["ignored"]
    assert audit["applied"] is False


def test_selected_param_with_no_override_is_not_invented():
    out, audit = _resolve(overrides={}, bounds_unit=BOUNDS_UNIT_PCT,
                          pct_params=["spot_stop_pts"], reference_price=SENSEX_REF)
    assert out == {}
    assert "spot_stop_pts" not in out


# === audit trail =============================================================

def test_audit_records_every_conversion_for_reproducibility():
    out, audit = _resolve(
        overrides={"spot_stop_pts": {"min": 0.05, "max": 0.317}},
        bounds_unit=BOUNDS_UNIT_PCT, pct_params=["spot_stop_pts"],
        reference_price=SENSEX_REF)
    assert audit["unit"] == BOUNDS_UNIT_PCT
    assert audit["reference_price"] == SENSEX_REF
    conv = audit["converted"]["spot_stop_pts"]
    assert conv["max"]["pct"] == 0.317
    assert conv["max"]["pts"] == pytest.approx(254.2, abs=0.5)


# === reference price from the candle frame ===================================

def test_reference_price_is_the_window_median_close():
    df = pd.DataFrame({"close": [100.0, 200.0, 300.0, 400.0, 500.0]})
    assert reference_index_price(df) == 300.0


def test_reference_price_ignores_nans():
    df = pd.DataFrame({"close": [100.0, float("nan"), 300.0]})
    assert reference_index_price(df) == 200.0


@pytest.mark.parametrize("df", [
    None,
    pd.DataFrame(),
    pd.DataFrame({"close": []}),
    pd.DataFrame({"open": [1, 2, 3]}),
    pd.DataFrame({"close": [float("nan"), float("nan")]}),
    pd.DataFrame({"close": [0.0, 0.0]}),
])
def test_reference_price_returns_none_when_it_cannot_be_trusted(df):
    """None is safe because resolve_bounds_overrides RAISES on it — the two
    together are what make a percentage impossible to misread as points."""
    assert reference_index_price(df) is None


def test_reference_price_is_deterministic_for_resume():
    """A resumed job re-derives the reference from the same window; it must land
    on the same number or the resumed search runs a different space."""
    df = pd.DataFrame({"close": [24000.0, 24500.0, 25000.0, 24700.0]})
    assert reference_index_price(df) == reference_index_price(df.copy())
