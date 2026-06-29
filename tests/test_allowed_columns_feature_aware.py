import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import app.features.catalog  # noqa: F401  -> registers seed features

from app.ai.compiler import allowed_columns, validate_spec
from app.ai.spec_schema import StrategySpec, Condition


def test_base_columns_exclude_feature_columns():
    base = allowed_columns()
    assert "close" in base and "rsi" in base
    assert "fvg_top" not in base
    assert "last_swing_high_level" not in base


def test_declared_feature_columns_are_allowed():
    cols = allowed_columns(["fvg_zones"])
    assert "fvg_top" in cols and "fvg_bottom" in cols
    assert "close" in cols


def test_declared_feature_pulls_dependency_columns():
    cols = allowed_columns(["order_block"])
    assert "ob_top" in cols
    assert "displacement" in cols
    assert "last_swing_high_level" in cols


def test_validate_spec_rejects_feature_col_when_undeclared():
    spec = StrategySpec(
        id="t1", name="t1",
        entry_ce=[Condition(left="close", op=">", right="fvg_top")],
    )
    errors = validate_spec(spec)
    assert any("fvg_top" in e for e in errors)


def test_validate_spec_accepts_feature_col_when_declared():
    spec = StrategySpec(
        id="t2", name="t2", required_features=["fvg_zones"],
        entry_ce=[Condition(left="close", op=">", right="fvg_top")],
    )
    errors = validate_spec(spec)
    assert not any("fvg_top" in e for e in errors)
