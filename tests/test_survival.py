import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from app.survival import SurvivalConfig, calmar, CALMAR_DD_FLOOR_PCT, _finite


def test_survival_config_from_dict_defaults_and_overrides():
    assert SurvivalConfig.from_dict(None).enabled is False
    cfg = SurvivalConfig.from_dict({"enabled": True, "max_drawdown_pct": 30,
                                    "objective": "net_inr", "min_oos_folds": "majority"})
    assert cfg.enabled is True
    assert cfg.max_drawdown_pct == 30.0
    assert cfg.objective == "net_inr"
    assert cfg.min_oos_folds == "majority"
    assert SurvivalConfig.from_dict({"objective": "bogus"}).objective == "calmar"


def test_calmar_floors_denominator_at_meaningful_dd():
    assert calmar(150.0, -30.0) == 5.0
    assert calmar(150.0, -0.5) == 150.0 / CALMAR_DD_FLOOR_PCT
    assert calmar(150.0, 0.0) == 150.0 / CALMAR_DD_FLOOR_PCT
    assert calmar(-40.0, -20.0) < 0


def test_finite_drops_nonfinite_and_nonnumeric():
    assert _finite([1.0, None, float("nan"), float("inf"), float("-inf"), "abc", 2]) == [1.0, 2.0]
    assert _finite([]) == []


from app.survival import monte_carlo_risk_of_ruin


def test_ror_zero_when_only_gains():
    r = monte_carlo_risk_of_ruin([100.0] * 200, capital=200_000, ruin_floor=0,
                                 n_paths=2000, seed=1)
    assert r["ror_pct"] == 0.0
    assert r["ror_ci_high"] >= 0.0
    assert r["n_days"] == 200


def test_ror_high_when_capital_tiny_vs_swings():
    r = monte_carlo_risk_of_ruin([-50.0, 60.0] * 100, capital=40, ruin_floor=0,
                                 n_paths=4000, seed=1)
    assert r["ror_pct"] > 50.0


def test_ror_is_reproducible_with_seed():
    a = monte_carlo_risk_of_ruin([-10, 12, -8, 15] * 50, 1000, 0, n_paths=3000, seed=7)
    b = monte_carlo_risk_of_ruin([-10, 12, -8, 15] * 50, 1000, 0, n_paths=3000, seed=7)
    assert a["ror_pct"] == b["ror_pct"]


def test_ror_empty_series_is_insufficient_and_max_risk():
    r = monte_carlo_risk_of_ruin([], capital=200_000, ruin_floor=0)
    assert r["n_days"] == 0
    assert r["ror_pct"] == 100.0


def test_ror_drops_nonfinite_days():
    r = monte_carlo_risk_of_ruin([float("nan"), 10.0, float("inf"), -5.0], 1000, 0,
                                 n_paths=500, seed=1)
    assert r["n_days"] == 2
