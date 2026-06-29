import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import app.features.catalog  # noqa: F401

from app.ai.capability import classify_rule, RuleTokens, FeasibilityClass as FC


def T(**kw):
    base = dict(cols=frozenset(), concepts=frozenset(), barspan=1, window=0,
                session_anchored=False, ohlcv_derivable=False)
    base.update(kw)
    base["cols"] = frozenset(base["cols"])
    base["concepts"] = frozenset(base["concepts"])
    return RuleTokens(**base)


def test_r1_buildable_now_from_existing_columns():
    v = classify_rule(T(cols={"close", "rsi"}, barspan=1))
    assert v.feasibility == FC.BUILDABLE_NOW


def test_r1_does_not_fire_when_barspan_exceeds_two():
    v = classify_rule(T(cols={"close"}, barspan=5))
    assert v.feasibility != FC.BUILDABLE_NOW


def test_r2_oi_needs_new_data():
    v = classify_rule(T(concepts={"oi"}))
    assert v.feasibility == FC.NEEDS_NEW_DATA
    assert "oi" in v.message.lower()


def test_r3_relative_strength_is_engine_plumbing_feature():
    v = classify_rule(T(concepts={"relative_strength"}))
    assert v.feasibility == FC.BUILDABLE_WITH_FEATURE


def test_r4_order_flow_is_infeasible():
    v = classify_rule(T(concepts={"order_flow"}))
    assert v.feasibility == FC.INFEASIBLE


def test_r5_fvg_maps_to_seed_feature_with_backtest_only_caveat():
    v = classify_rule(T(concepts={"fvg"}))
    assert v.feasibility == FC.BUILDABLE_WITH_FEATURE
    assert v.feature == "fvg_zones"
    assert v.live_feasible is False
    assert "backtest" in v.message.lower()


def test_r5_premium_discount_maps_to_live_feasible_feature():
    v = classify_rule(T(concepts={"premium_discount"}))
    assert v.feasibility == FC.BUILDABLE_WITH_FEATURE
    assert v.feature == "premium_discount"
    assert v.live_feasible is True


def test_r5_already_declared_feature_column_is_buildable_now():
    v = classify_rule(T(cols={"fvg_top"}, barspan=1), required_features=["fvg_zones"])
    assert v.feasibility == FC.BUILDABLE_NOW


def test_r6_ohlcv_derivable_short_window_is_live_safe_feature():
    v = classify_rule(T(ohlcv_derivable=True, window=20))
    assert v.feasibility == FC.BUILDABLE_WITH_FEATURE
    assert v.live_feasible is True


def test_r7_ohlcv_derivable_long_window_is_live_gated():
    v = classify_rule(T(ohlcv_derivable=True, window=300))
    assert v.feasibility == FC.BUILDABLE_WITH_FEATURE
    assert v.live_feasible is False


def test_r7_session_anchored_is_live_gated():
    v = classify_rule(T(ohlcv_derivable=True, window=10, session_anchored=True))
    assert v.feasibility == FC.BUILDABLE_WITH_FEATURE
    assert v.live_feasible is False


def test_r8_history_beyond_two_bars_is_full_python_feature():
    v = classify_rule(T(cols={"close"}, barspan=8))
    assert v.feasibility == FC.BUILDABLE_WITH_FEATURE
    assert "full-python" in v.message.lower() or "history" in v.message.lower()


def test_r9_unrecognised_is_infeasible():
    v = classify_rule(T(concepts={"astrology"}))
    assert v.feasibility == FC.INFEASIBLE
