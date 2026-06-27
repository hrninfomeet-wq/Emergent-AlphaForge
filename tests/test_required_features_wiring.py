"""SP-1: required_features declaration + no-op wiring (byte-identical back-compat)."""
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

import pandas as pd
from app.strategies.base import StrategyBase, Signal


def test_required_features_defaults_empty_and_in_meta():
    class _S(StrategyBase):
        id = "rf_default"
    s = _S()
    assert s.required_features == []
    assert s.meta()["required_features"] == []


def test_required_features_declared_appears_in_meta():
    class _S(StrategyBase):
        id = "rf_decl"
        required_features = ["fvg_zones", "swing_levels"]
    assert _S().meta()["required_features"] == ["fvg_zones", "swing_levels"]
