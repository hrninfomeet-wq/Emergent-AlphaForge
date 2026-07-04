import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from app.early_stop import is_significant_improvement, should_early_stop, effective_warmup_patience


def test_default_ceiling_warmup_scales_below_ui_budget_so_autostop_can_fire():
    # The bug: schema warmup=patience=200, UI budget 150 -> auto-stop never fired.
    eff_w, eff_p = effective_warmup_patience(n_trials=150, warmup=200, patience=200)
    assert eff_w < 150 and eff_p >= 1
    # and it CAN now fire on a real plateau within the budget:
    assert should_early_stop(completed=eff_w + eff_p, last_improve_trial=eff_w,
                             warmup=eff_w, patience=eff_p) is True


def test_effective_warmup_never_swallows_the_whole_budget():
    for n in (10, 40, 150, 200, 500):
        eff_w, eff_p = effective_warmup_patience(n_trials=n, warmup=200, patience=200)
        assert eff_w < n, f"warmup {eff_w} must be < budget {n}"


def test_large_deliberate_budget_keeps_full_ceiling():
    # A user who runs 1000 trials still gets up to the 200 ceiling.
    eff_w, eff_p = effective_warmup_patience(n_trials=1000, warmup=200, patience=200)
    assert eff_w == 200 and eff_p == 200

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


def test_optimizer_start_req_early_stop_defaults():
    from app.schemas import OptimizerStartReq
    r = OptimizerStartReq(strategy_id="x")
    assert r.early_stop is True
    assert r.early_stop_warmup == 200 and r.early_stop_patience == 200
    assert r.early_stop_min_delta == 0.001


def test_optimizer_start_req_analyze_budget_default():
    from app.schemas import OptimizerStartReq
    r = OptimizerStartReq(strategy_id="x")
    assert r.analyze_budget_sec == 1800
