# tests/test_spot_option_correlation.py
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.deployment_quality import compute_spot_option_correlation, evaluate_source_quality

def _ranked(pairs):
    return [{"spot_objective": s, "option_pnl_value": o} for s, o in pairs]

def test_correlation_none_when_too_few():
    assert compute_spot_option_correlation([]) is None
    assert compute_spot_option_correlation(_ranked([(1.0, 2.0)])) is None

def test_correlation_none_on_zero_variance():
    assert compute_spot_option_correlation(_ranked([(1.0, 5.0), (1.0, 9.0)])) is None  # spot constant
    assert compute_spot_option_correlation(_ranked([(1.0, 5.0), (2.0, 5.0)])) is None  # option constant

def test_correlation_perfect_positive():
    r = _ranked([(1.0, 10.0), (2.0, 20.0), (3.0, 30.0)])
    assert compute_spot_option_correlation(r) == 1.0

def test_correlation_negative():
    r = _ranked([(1.0, 30.0), (2.0, 20.0), (3.0, 10.0)])
    assert compute_spot_option_correlation(r) == -1.0

def test_objective_misalignment_warning_fires_below_threshold():
    doc = {"metrics": {"sharpe": 1.2, "trade_count": 60, "win_rate": 0.55,
                       "profit_factor": 1.5, "max_dd_pts": 30, "total_pnl_pts": 400}}
    q_low = evaluate_source_quality(doc, evidence={"spot_option_correlation": 0.1})
    assert any(w["id"] == "objective_misalignment" for w in q_low["warnings"])
    q_high = evaluate_source_quality(doc, evidence={"spot_option_correlation": 0.8})
    assert not any(w["id"] == "objective_misalignment" for w in q_high["warnings"])
    q_none = evaluate_source_quality(doc, evidence={})
    assert not any(w["id"] == "objective_misalignment" for w in q_none["warnings"])
