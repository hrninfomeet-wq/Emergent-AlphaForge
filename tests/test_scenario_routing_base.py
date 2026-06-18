import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

import pandas as pd
import pytest
from app.strategies.scenario_routing_base import ScenarioRoutedStrategyBase, ROUTING_BASE_PARAMS


def _hist(sess="2026-06-18", first_open=24000.0, n=5):
    """Small single-session history; first bar's open is the session open."""
    return pd.DataFrame({
        "session_date": [sess] * n,
        "open": [first_open] + [first_open + 10.0 * k for k in range(1, n)],
    })


def _row(**kw):
    base = {
        "ist_time": "10:00",
        "regime": "TREND",
        "session_date": "2026-06-18",
        "atr": 40.0,
        "atr_avg": 40.0,
        "nr7": False,
    }
    base.update(kw)
    return pd.Series(base)


# --- 1. __init_subclass__ validation -----------------------------------------
def test_bogus_scenario_raises_on_class_creation():
    with pytest.raises(ValueError):
        class _Bogus(ScenarioRoutedStrategyBase):
            id = "_test_bogus"
            scenarios_traded = ("BOGUS",)

            def _route(self, row, prev, params, ctx, scenario):
                return ("CE", 50, [], [])


# --- 2. scenario-not-traded gating -------------------------------------------
class _TrendOnly(ScenarioRoutedStrategyBase):
    id = "_test_trend_only"
    name = "t"
    scenarios_traded = ("TREND_CONTINUATION",)

    def _route(self, row, prev, params, ctx, scenario):
        return ("CE", 60, [], [])


def test_scenario_not_traded_gating():
    s = _TrendOnly()
    p = s.default_params()
    # WIDE open (0.9 >= wide_thr 0.60) -> VOLATILE_FADE, which _TrendOnly does NOT trade
    row = _row(orb_width_pct_partial=0.9)
    sig = s.evaluate(row, None, p, {})
    assert sig.direction == "NONE"
    assert sig.scenario == "VOLATILE_FADE"
    assert any("not traded" in b for b in sig.blockers)


# --- 3. VOLATILE_FADE end-to-end ---------------------------------------------
class _FadePE(ScenarioRoutedStrategyBase):
    id = "_test_fade_pe"
    name = "t"
    scenarios_traded = ("VOLATILE_FADE",)

    def _route(self, row, prev, params, ctx, scenario):
        return ("PE", 60, ["x"], [])


def test_volatile_fade_end_to_end():
    s = _FadePE()
    p = s.default_params()
    hist = _hist(first_open=24000.0)
    row = _row(orb_width_pct_partial=0.9)
    ctx = {"history_df": hist, "i": len(hist) - 1, "instrument": "NIFTY"}
    sig = s.evaluate(row, None, p, ctx)
    assert sig.direction == "PE"
    assert sig.scenario == "VOLATILE_FADE"
    assert sig.spot_target_level == 24000.0  # session open
    assert sig.exit_mode == "spot_exit"


# --- 4. TREND_CONTINUATION end-to-end ----------------------------------------
class _TrendCE(ScenarioRoutedStrategyBase):
    id = "_test_trend_ce"
    name = "t"
    scenarios_traded = ("TREND_CONTINUATION",)

    def _route(self, row, prev, params, ctx, scenario):
        return ("CE", 60, [], [])


def test_trend_continuation_end_to_end():
    s = _TrendCE()
    p = s.default_params()
    # NARROW open (0.2 <= narrow_thr 0.30) -> TREND_CONTINUATION
    row = _row(orb_width_pct_partial=0.2, atr=40.0)
    sig = s.evaluate(row, None, p, {})
    assert sig.direction == "CE"
    assert sig.scenario == "TREND_CONTINUATION"
    assert sig.spot_target_level is None
    assert sig.spot_target_pts >= 90  # 4.0 * 40 = 160 let-run target
    assert sig.exit_mode == "spot_exit"


# --- 5. time gate ------------------------------------------------------------
def test_time_gate_blocks_late_entry():
    s = _FadePE()
    p = s.default_params()
    # 14:30 >= default cutoff 14:00 -> blocked regardless of scenario
    row = _row(ist_time="14:30", orb_width_pct_partial=0.9)
    sig = s.evaluate(row, None, p, {})
    assert sig.direction == "NONE"
    assert any("time gate" in b for b in sig.blockers)


def test_base_params_present_in_schema():
    s = _TrendCE()
    for k in ROUTING_BASE_PARAMS:
        assert k in s.parameter_schema
