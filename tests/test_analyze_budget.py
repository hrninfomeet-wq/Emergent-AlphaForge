import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from app.analyze_budget import over_budget, eta_seconds, ewma

def test_over_budget_false_when_unlimited():
    assert over_budget(elapsed=10_000.0, budget_sec=0) is False

def test_over_budget_false_under():
    assert over_budget(elapsed=100.0, budget_sec=1800) is False

def test_over_budget_true_at_or_over():
    assert over_budget(elapsed=1800.0, budget_sec=1800) is True
    assert over_budget(elapsed=2000.0, budget_sec=1800) is True

def test_ewma_first_sample_is_value():
    assert ewma(prev=None, sample=4.0, alpha=0.3) == 4.0

def test_ewma_blends():
    assert abs(ewma(prev=2.0, sample=4.0, alpha=0.5) - 3.0) < 1e-9

def test_eta_seconds_remaining_times_per_item():
    assert eta_seconds(done=10, total=150, per_item_sec=4.0) == (150 - 10) * 4.0

def test_eta_zero_when_done():
    assert eta_seconds(done=150, total=150, per_item_sec=4.0) == 0.0

def test_eta_none_when_no_estimate():
    assert eta_seconds(done=0, total=150, per_item_sec=None) is None
