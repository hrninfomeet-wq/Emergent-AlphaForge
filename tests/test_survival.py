import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from app.survival import SurvivalConfig, calmar, CALMAR_DD_FLOOR_PCT


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
