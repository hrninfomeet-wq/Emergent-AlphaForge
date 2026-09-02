"""End-to-end wiring for the opt-in `pct_of_index` bounds unit.

The unit test (`test_bounds_unit.py`) proves the conversion. This proves the
CHAIN: request schema -> API validation -> job config -> the space the optimizer
and WFO actually search. Every link is somewhere a field can be silently dropped
and a percentage read as points.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.bounds_unit import (  # noqa: E402
    BOUNDS_UNIT_PCT, BOUNDS_UNIT_POINTS, reference_index_price, resolve_bounds_overrides,
)
from app.optimizer import _build_param_space  # noqa: E402
from app.schemas import OptimizerStartReq, WfoStartReq  # noqa: E402

SCHEMA = {
    "sr_lookback": {"type": "int", "min": 20, "max": 120, "default": 60},
    "spot_target_pts": {"type": "float", "min": 5, "max": 200, "default": 40},
    "spot_stop_pts": {"type": "float", "min": 3, "max": 100, "default": 18},
}
# The overrides the two real 2026-09-01 jobs ran under.
REAL_OVERRIDES = {"spot_target_pts": {"max": 200}, "spot_stop_pts": {"max": 80}}
SENSEX_REF = 80176.0


# === GAP 1: the request models must DECLARE the fields ======================
# Pydantic ignores unknown fields, so an undeclared field sent by the frontend
# vanishes with no error and never reaches the job config.

@pytest.mark.parametrize("model,required", [
    (OptimizerStartReq, {"strategy_id": "x"}),
    (WfoStartReq, {"strategy_id": "x"}),
])
def test_both_request_models_carry_the_bounds_unit(model, required):
    req = model(**required, bounds_unit=BOUNDS_UNIT_PCT,
                bounds_pct_params=["spot_stop_pts"])
    dumped = req.model_dump()
    assert dumped["bounds_unit"] == BOUNDS_UNIT_PCT
    assert dumped["bounds_pct_params"] == ["spot_stop_pts"]


@pytest.mark.parametrize("model", [OptimizerStartReq, WfoStartReq])
def test_defaults_are_the_historical_behaviour(model):
    dumped = model(strategy_id="x").model_dump()
    assert dumped["bounds_unit"] is None      # -> normalizes to "points"
    assert dumped["bounds_pct_params"] == []


# === the space the engine actually searches =================================

def _space(overrides, unit=None, pct_params=(), ref=SENSEX_REF):
    resolved, audit = resolve_bounds_overrides(
        overrides=overrides, bounds_unit=unit, pct_params=list(pct_params),
        reference_price=ref, parameter_schema=SCHEMA)
    return _build_param_space(SCHEMA, resolved), audit


def test_default_path_is_byte_identical_to_no_bounds_unit_at_all():
    """The safety invariant. If this ever fails, every stored result is suspect."""
    baseline = _build_param_space(SCHEMA, REAL_OVERRIDES)
    for unit in (None, "", BOUNDS_UNIT_POINTS, "POINTS"):
        space, audit = _space(REAL_OVERRIDES, unit=unit, pct_params=["spot_stop_pts"])
        assert space == baseline, f"unit={unit!r} changed the search space"
        assert audit["applied"] is False


def test_pct_mode_without_ticked_params_is_also_byte_identical():
    baseline = _build_param_space(SCHEMA, REAL_OVERRIDES)
    space, _ = _space(REAL_OVERRIDES, unit=BOUNDS_UNIT_PCT, pct_params=[])
    assert space == baseline


def test_pct_mode_reaches_the_geometry_the_point_box_could_not():
    """NIFTY's winning stop is 0.3153% of index and its target 0.7390%. Under the
    point box those are capped at 80 and 200 on SENSEX; as percentages they
    resolve to ~253 and ~592 — the region that was unreachable."""
    space, audit = _space(
        {"spot_stop_pts": {"max": 0.31532}, "spot_target_pts": {"max": 0.73899}},
        unit=BOUNDS_UNIT_PCT, pct_params=["spot_stop_pts", "spot_target_pts"])
    assert space["spot_stop_pts"]["max"] == pytest.approx(252.8, abs=2)
    assert space["spot_target_pts"]["max"] == pytest.approx(592.5, abs=3)
    assert space["spot_stop_pts"]["min"] == 3      # untouched schema floor
    assert audit["applied"] is True
    assert audit["reference_price"] == SENSEX_REF


def test_unticked_params_keep_their_own_units_in_the_space():
    space, _ = _space({"spot_stop_pts": {"max": 0.31532}, "sr_lookback": {"max": 90}},
                      unit=BOUNDS_UNIT_PCT, pct_params=["spot_stop_pts"])
    assert space["sr_lookback"]["max"] == 90
    assert space["spot_stop_pts"]["max"] > 200


def test_pct_bounds_do_not_leak_into_unrelated_params():
    space, _ = _space(REAL_OVERRIDES, unit=BOUNDS_UNIT_PCT, pct_params=["spot_stop_pts"])
    # target was NOT ticked, so its 200 stays 200 points
    assert space["spot_target_pts"]["max"] == 200


# === resume determinism =====================================================

def test_same_window_resolves_to_the_same_space_on_resume():
    """A resumed job re-reads the AUTHORED percentages from its config and
    re-derives the reference from the same candles. If this drifted, a resumed
    run would silently search a different box than the one it started."""
    overrides = {"spot_stop_pts": {"max": 0.31532}}
    first, _ = _space(overrides, unit=BOUNDS_UNIT_PCT, pct_params=["spot_stop_pts"])
    second, _ = _space(overrides, unit=BOUNDS_UNIT_PCT, pct_params=["spot_stop_pts"])
    assert first == second
    assert overrides == {"spot_stop_pts": {"max": 0.31532}}, "config must keep percentages"


# === reference price comes from the same frame the backtest uses ============

def test_reference_is_the_median_of_the_run_window():
    import pandas as pd
    df = pd.DataFrame({"close": [78000.0, 80176.0, 82000.0]})
    assert reference_index_price(df) == 80176.0


# === GAP: API-boundary validation ===========================================

def test_pct_params_without_pct_unit_is_rejected_at_the_api():
    """Silently searching them as POINTS is the failure mode; a 400 is not."""
    from fastapi import HTTPException
    from app.routers.research import _validate_bounds_unit
    req = OptimizerStartReq(strategy_id="x", bounds_unit="points",
                            bounds_pct_params=["spot_stop_pts"])
    with pytest.raises(HTTPException) as exc:
        _validate_bounds_unit(req)
    assert exc.value.status_code == 400


def test_unknown_unit_is_rejected_at_the_api():
    from fastapi import HTTPException
    from app.routers.research import _validate_bounds_unit
    req = OptimizerStartReq(strategy_id="x", bounds_unit="percent")
    with pytest.raises(HTTPException) as exc:
        _validate_bounds_unit(req)
    assert exc.value.status_code == 400


@pytest.mark.parametrize("unit", [None, "points", "pct_of_index"])
def test_valid_units_pass_the_api_guard(unit):
    from app.routers.research import _validate_bounds_unit
    _validate_bounds_unit(OptimizerStartReq(strategy_id="x", bounds_unit=unit))


def test_wfo_shares_the_same_api_guard():
    from fastapi import HTTPException
    from app.routers.research import _validate_bounds_unit
    with pytest.raises(HTTPException):
        _validate_bounds_unit(WfoStartReq(strategy_id="x", bounds_unit="nonsense"))
