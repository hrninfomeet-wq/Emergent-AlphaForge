import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import app.features.catalog  # noqa: F401

from app.ai.capability import capability_report, WAREHOUSE_MANIFEST


def test_manifest_states_what_we_have_and_lack():
    m = WAREHOUSE_MANIFEST
    assert m["has_1m_ohlcv"] is True
    assert m["has_option_candles"] is True
    assert m["has_per_strike_greeks_history"] is False
    assert m["has_oi_history"] is False
    assert m["has_l2_depth"] is False
    assert m["has_tick_orderflow"] is False
    assert set(m["instruments"]) == {"NIFTY", "BANKNIFTY", "SENSEX"}


def test_capability_report_composes_three_sources():
    rep = capability_report()
    assert "close" in rep["columns"] and "rsi" in rep["columns"]
    assert "body_frac" in rep["columns"]            # always-on geometry is a normal column
    feats = {f["feature"]: f for f in rep["features"]}
    assert {"swing_levels", "fvg_zones", "order_block"} <= set(feats)
    assert feats["swing_levels"]["live_feasible"] is True
    assert feats["fvg_zones"]["live_feasible"] is False
    assert rep["warehouse"]["has_oi_history"] is False


def test_capability_report_columns_exclude_feature_columns():
    rep = capability_report()
    assert "fvg_top" not in rep["columns"]
    assert any("fvg_top" in f["columns"] for f in rep["features"])


def test_flagship_ict_fvg_end_to_end():
    """The motivating case: 'enter when price returns to a bullish FVG'.
    The agent (SP-4) parses this into an FVG-structure rule; SP-3's classifier
    routes it to BUILDABLE_WITH_FEATURE(fvg_zones, backtest-only), and once
    fvg_zones is declared the column becomes allowed."""
    from app.ai.capability import classify_rule, RuleTokens, FeasibilityClass
    from app.ai.compiler import allowed_columns

    v = classify_rule(RuleTokens(concepts=frozenset({"fvg"})))
    assert v.feasibility == FeasibilityClass.BUILDABLE_WITH_FEATURE
    assert v.feature == "fvg_zones" and v.live_feasible is False

    assert "fvg_top" in allowed_columns(["fvg_zones"])
    assert "fvg_top" not in allowed_columns()
