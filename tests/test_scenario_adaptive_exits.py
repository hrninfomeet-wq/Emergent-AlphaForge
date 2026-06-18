import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from app.scenarios import exit_plan

def test_trend_continuation_is_let_run_target_no_level():
    p = exit_plan("TREND_CONTINUATION", {"atr": 40.0, "open": 24000.0}, params={})
    assert p["spot_target_level"] is None
    assert p["spot_target_pts"] >= 90 and p["spot_stop_pts"] > 0 and p["trail"] is True

def test_volatile_fade_targets_the_open_level():
    p = exit_plan("VOLATILE_FADE", {"atr": 40.0, "open": 24000.0}, params={})
    assert p["spot_target_level"] == 24000.0 and p["trail"] is False

def test_chop_is_small_scalp():
    p = exit_plan("CHOP", {"atr": 40.0, "open": 24000.0}, params={})
    assert p["spot_target_level"] is None and p["spot_target_pts"] < 90

def test_none_returns_no_trade_plan():
    assert exit_plan("NONE", {"atr": 40.0, "open": 24000.0}, params={}) is None

def test_unknown_scenario_returns_none():
    assert exit_plan("SOMETHING_NEW", {"atr": 40.0, "open": 24000.0}, params={}) is None

def test_params_override_drives_magnitudes():
    # The P4 optimizer tunes these magnitudes; the dispatcher MUST honor them.
    p = exit_plan("TREND_CONTINUATION", {"atr": 10.0, "open": 24000.0},
                  params={"trend_target_atr": 6.0, "trend_stop_atr": 1.0})
    assert p["spot_target_pts"] == 60.0 and p["spot_stop_pts"] == 10.0

def test_volatile_fade_missing_open_yields_none_level():
    p = exit_plan("VOLATILE_FADE", {"atr": 40.0}, params={})  # no "open" in ctx
    assert p["spot_target_level"] is None and p["trail"] is False
