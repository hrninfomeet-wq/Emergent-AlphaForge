import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from app.early_stop import is_significant_improvement, should_early_stop

def test_first_trial_anchor_neg_inf_is_improvement():
    assert is_significant_improvement(0.5, float("-inf"), 0.001) is True

def test_relative_threshold_just_below_is_not_improvement():
    assert is_significant_improvement(100.05, 100.0, 0.001) is False

def test_relative_threshold_just_above_is_improvement():
    assert is_significant_improvement(100.2, 100.0, 0.001) is True

def test_negative_anchor_uses_abs_magnitude():
    assert is_significant_improvement(-99.5, -100.0, 0.001) is True
    assert is_significant_improvement(-99.95, -100.0, 0.001) is False

def test_nan_new_value_is_not_improvement():
    assert is_significant_improvement(float("nan"), 1.0, 0.001) is False

def test_no_stop_before_warmup():
    assert should_early_stop(completed=50, last_improve_trial=0, warmup=200, patience=20) is False

def test_no_stop_within_patience():
    assert should_early_stop(completed=210, last_improve_trial=200, warmup=200, patience=20) is False

def test_stop_at_patience_boundary():
    assert should_early_stop(completed=220, last_improve_trial=200, warmup=200, patience=20) is True

def test_patience_below_one_never_stops():
    assert should_early_stop(completed=1000, last_improve_trial=0, warmup=10, patience=0) is False
