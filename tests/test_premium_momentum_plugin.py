# tests/test_premium_momentum_plugin.py
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.strategies.plugins.premium_momentum import PremiumMomentum


def test_plugin_identity_and_schema():
    s = PremiumMomentum()
    assert s.id == "premium_momentum"
    assert s.supported_instruments == ["NIFTY"]          # spec: v1 NIFTY-only
    schema = s.parameter_schema
    for key, default in [("reference_time", "09:31"), ("moneyness", "itm1"),
                         ("side", "first_to_trigger"), ("momentum_pct", 15.0),
                         ("stop_pct", 20.0)]:
        assert key in schema and schema[key]["default"] == default
    assert "target_pct" in schema        # optional, default None
    # trail knobs are DELIBERATELY absent: live trail is single-sourced from
    # deployment.risk.exit_controls (mode stepped_xy) — no drift.
    assert "trail_x" not in schema and "trail_y" not in schema


def test_evaluate_is_inert_none():
    # The evaluator's Track B branch does the real work; the plugin's evaluate
    # is inert so the GENERIC path can never fire a spot signal for it.
    from app.strategies.base import Signal
    s = PremiumMomentum()
    sig = s.evaluate({"close": 1.0}, {"close": 1.0}, s.default_params(), {})
    assert isinstance(sig, Signal) and sig.direction == "NONE"


# ---------------------------------------------------------------------------
# Phase 5B Task A2 — schema extension (live/paper multi-leg capability build)
# ---------------------------------------------------------------------------

def test_schema_has_all_phase5b_fields_at_their_documented_defaults():
    schema = PremiumMomentum().parameter_schema
    expected_defaults = {
        "leg_mode": "first_to_trigger",
        "lazy_enabled": False,
        "lazy_momentum_pct": None,
        "lazy_stop_pct": None,
        "lazy_target_pct": None,
        "lazy_moneyness": "itm1",
        "entry_cutoff": None,
        "exit_time": None,
        "session_max_loss_rupees": None,
        "session_max_profit_rupees": None,
        "vix_min": None,
        "vix_max": None,
    }
    for key, default in expected_defaults.items():
        assert key in schema, f"missing Phase 5B schema key: {key}"
        assert schema[key]["default"] == default, (
            f"{key} default {schema[key]['default']!r} != expected {default!r}")


def test_schema_phase5b_types_and_bounds():
    schema = PremiumMomentum().parameter_schema
    assert schema["leg_mode"]["type"] == "str"
    assert schema["entry_cutoff"]["type"] == "str"
    assert schema["exit_time"]["type"] == "str"
    assert schema["lazy_moneyness"]["type"] == "str"

    assert schema["lazy_enabled"]["type"] == "bool"
    assert schema["lazy_momentum_pct"]["type"] == "float"
    assert schema["lazy_momentum_pct"]["min"] == 5.0 and schema["lazy_momentum_pct"]["max"] == 50.0
    assert schema["lazy_stop_pct"]["type"] == "float"
    assert schema["lazy_stop_pct"]["min"] == 10.0 and schema["lazy_stop_pct"]["max"] == 40.0
    assert schema["lazy_target_pct"]["type"] == "float"
    assert schema["session_max_loss_rupees"]["type"] == "float"
    assert schema["session_max_profit_rupees"]["type"] == "float"
    assert schema["vix_min"]["type"] == "float"
    assert schema["vix_max"]["type"] == "float"


def test_schema_deliberate_optimizer_space_fixes():
    # These are deliberate optimizer-space decisions (Phase 5B plan A2): the
    # general Optimizer must never search multi-leg/lazy/day-stop/VIX-gate
    # dimensions -- str-typed fields are excluded from _build_param_space
    # unconditionally; float/bool fields that must stay OFF the search space
    # carry an explicit "fixed" key.
    schema = PremiumMomentum().parameter_schema
    assert schema["lazy_enabled"]["fixed"] is False
    assert schema["lazy_target_pct"]["fixed"] is None
    assert schema["session_max_loss_rupees"]["fixed"] is None
    assert schema["session_max_profit_rupees"]["fixed"] is None
    assert schema["vix_min"]["fixed"] is None
    assert schema["vix_max"]["fixed"] is None
    # lazy_momentum_pct/lazy_stop_pct are deliberately NOT fixed (tunable dims
    # for the dedicated tuner; harmless dead weight in the general Optimizer
    # since lazy_enabled itself is always fixed False there).
    assert "fixed" not in schema["lazy_momentum_pct"]
    assert "fixed" not in schema["lazy_stop_pct"]
    assert "fixed" not in schema["leg_mode"]   # str -- excluded structurally, no "fixed" needed


def test_merged_params_passthrough_for_pre_5b_stored_params_zero_migration():
    """A deployment saved BEFORE Phase 5B only ever persisted the original 8
    keys. merged_params must produce every new Phase-5B key at its schema
    default with no migration step (base.py's allow-list mechanics: unknown
    override keys are dropped, missing keys fall back to default_params())."""
    s = PremiumMomentum()
    pre_5b_stored_params = {
        "reference_time": "09:31",
        "moneyness": "itm1",
        "side": "first_to_trigger",
        "momentum_pct": 15.0,
        "momentum_pts": None,
        "stop_pct": 20.0,
        "target_pct": None,
        "late_lock_cutoff": "10:15",
    }
    merged = s.merged_params(pre_5b_stored_params)
    # original keys pass through unchanged
    for k, v in pre_5b_stored_params.items():
        assert merged[k] == v
    # every new Phase 5B key appears at its schema default
    defaults = s.default_params()
    for key in ("leg_mode", "lazy_enabled", "lazy_momentum_pct", "lazy_stop_pct",
                "lazy_target_pct", "lazy_moneyness", "entry_cutoff", "exit_time",
                "session_max_loss_rupees", "session_max_profit_rupees",
                "vix_min", "vix_max"):
        assert key in merged
        assert merged[key] == defaults[key]
    assert merged["leg_mode"] == "first_to_trigger"
    assert merged["lazy_enabled"] is False
