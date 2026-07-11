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
