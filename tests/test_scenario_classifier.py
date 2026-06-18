import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from app.scenario_classifier import classify_scenario, SCENARIOS

def test_wide_open_is_volatile_fade():
    assert classify_scenario(regime="MIXED", orb_width_pct=0.9, day_type="RANGE",
                             nr7=False, atr_ratio=1.2) == "VOLATILE_FADE"

def test_narrow_open_is_trend_continuation():
    assert classify_scenario(regime="TREND", orb_width_pct=0.2, day_type="TREND",
                             nr7=True, atr_ratio=1.0) == "TREND_CONTINUATION"

def test_mid_chop_regime_is_chop():
    assert classify_scenario(regime="CHOP", orb_width_pct=0.45, day_type="NEUTRAL",
                             nr7=False, atr_ratio=1.0) == "CHOP"

def test_none_when_orb_width_missing():
    assert classify_scenario(regime="TREND", orb_width_pct=None, day_type="TREND",
                             nr7=False, atr_ratio=1.0) == "NONE"

def test_column_swap_guard_keys_off_orb_not_cpr():
    # Quiet OPEN today (narrow orb) but WIDE prior pivot must classify on the OPEN -> TREND_CONTINUATION,
    # NOT VOLATILE_FADE. classify_scenario takes orb_width_pct only; there is NO cpr_width_pct param,
    # so a swap is structurally impossible — this test pins that contract.
    import inspect
    params = inspect.signature(classify_scenario).parameters
    assert "orb_width_pct" in params and "cpr_width_pct" not in params

def test_thresholds_overridable():
    assert classify_scenario(regime="MIXED", orb_width_pct=0.5, day_type="RANGE", nr7=False,
                             atr_ratio=1.0, narrow_thr=0.55, wide_thr=0.45) == "VOLATILE_FADE"

def test_thresholds_are_inclusive_at_boundaries():
    # Pins the >=/<= contract: exactly-at-threshold widths classify, not fall through.
    # A regression to strict >/< would break these.
    assert classify_scenario(regime="MIXED", orb_width_pct=0.60, day_type="RANGE",
                             nr7=False, atr_ratio=1.0) == "VOLATILE_FADE"   # w == wide_thr
    assert classify_scenario(regime="MIXED", orb_width_pct=0.30, day_type="RANGE",
                             nr7=False, atr_ratio=1.0) == "TREND_CONTINUATION"  # w == narrow_thr

def test_mid_width_non_chop_non_trend_regime_is_none():
    # Rule-6 fall-through: width between thresholds AND regime not in the chop family -> NONE.
    assert classify_scenario(regime="TREND", orb_width_pct=0.45, day_type="TREND",
                             nr7=False, atr_ratio=1.0) == "NONE"

def test_scenarios_tuple_is_the_full_contract():
    assert SCENARIOS == ("TREND_CONTINUATION", "VOLATILE_FADE", "CHOP", "NONE")
