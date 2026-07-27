import ast
import inspect
import sys
import textwrap
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from app.scenario_classifier import classify_scenario, SCENARIOS

# Inputs the router passes that NO rule reads yet. Declared here (and in the
# module docstring) so reserved surface is a deliberate, visible choice rather
# than drift — see test_non_reserved_parameters_are_read_by_the_body.
RESERVED_UNUSED = {"day_type", "nr7"}

def test_wide_open_is_volatile_fade():
    assert classify_scenario(regime="MIXED", orb_width_pct=0.9, day_type="RANGE",
                             nr7=False) == "VOLATILE_FADE"

def test_narrow_open_is_trend_continuation():
    assert classify_scenario(regime="TREND", orb_width_pct=0.2, day_type="TREND",
                             nr7=True) == "TREND_CONTINUATION"

def test_mid_chop_regime_is_chop():
    assert classify_scenario(regime="CHOP", orb_width_pct=0.45, day_type="NEUTRAL",
                             nr7=False) == "CHOP"

def test_none_when_orb_width_missing():
    assert classify_scenario(regime="TREND", orb_width_pct=None, day_type="TREND",
                             nr7=False) == "NONE"

def test_column_swap_guard_keys_off_orb_not_cpr():
    # Quiet OPEN today (narrow orb) but WIDE prior pivot must classify on the OPEN -> TREND_CONTINUATION,
    # NOT VOLATILE_FADE. classify_scenario takes orb_width_pct only; there is NO cpr_width_pct param,
    # so a swap is structurally impossible — this test pins that contract.
    params = inspect.signature(classify_scenario).parameters
    assert "orb_width_pct" in params and "cpr_width_pct" not in params

def test_signature_is_exactly_the_documented_input_set():
    """Pins the classifier's whole input surface, so adding or dropping an input
    is a deliberate act. `atr_ratio` and `vix_bucket` were REMOVED: both were
    accepted and never read by any rule, and `atr_ratio` additionally cost a
    per-bar `_atr_ratio()` helper call in the router to supply a value nothing
    consumed. `vix_bucket` had no caller at all and no near-term prospect of one
    (there is no per-bar VIX column — see explosive_reversal's module docstring)."""
    assert set(inspect.signature(classify_scenario).parameters) == {
        "regime", "orb_width_pct", "day_type", "nr7", "narrow_thr", "wide_thr",
    }

def test_non_reserved_parameters_are_read_by_the_body():
    """Every declared input except the explicitly RESERVED ones must actually be
    referenced by a rule. This is the check that would have flagged `atr_ratio` /
    `vix_bucket` as inert: a new parameter must either be used, or be added to
    RESERVED_UNUSED on purpose — it can no longer sit in the signature unnoticed
    while the docstring implies the classifier combines it."""
    fndef = ast.parse(textwrap.dedent(inspect.getsource(classify_scenario))).body[0]
    read_names = {n.id for stmt in fndef.body for n in ast.walk(stmt)
                  if isinstance(n, ast.Name)}
    declared = set(inspect.signature(classify_scenario).parameters)
    unread = (declared - RESERVED_UNUSED) - read_names
    assert not unread, (
        f"parameter(s) declared but never read by any rule: {sorted(unread)} — "
        "either use them or list them in RESERVED_UNUSED (and say so in the docstring)"
    )

def test_thresholds_overridable():
    assert classify_scenario(regime="MIXED", orb_width_pct=0.5, day_type="RANGE", nr7=False,
                             narrow_thr=0.55, wide_thr=0.45) == "VOLATILE_FADE"

def test_thresholds_are_inclusive_at_boundaries():
    # Pins the >=/<= contract: exactly-at-threshold widths classify, not fall through.
    # A regression to strict >/< would break these.
    assert classify_scenario(regime="MIXED", orb_width_pct=0.60, day_type="RANGE",
                             nr7=False) == "VOLATILE_FADE"   # w == wide_thr
    assert classify_scenario(regime="MIXED", orb_width_pct=0.30, day_type="RANGE",
                             nr7=False) == "TREND_CONTINUATION"  # w == narrow_thr

def test_mid_width_non_chop_non_trend_regime_is_none():
    # Rule-6 fall-through: width between thresholds AND regime not in the chop family -> NONE.
    assert classify_scenario(regime="TREND", orb_width_pct=0.45, day_type="TREND",
                             nr7=False) == "NONE"

def test_scenarios_tuple_is_the_full_contract():
    assert SCENARIOS == ("TREND_CONTINUATION", "VOLATILE_FADE", "CHOP", "NONE")
