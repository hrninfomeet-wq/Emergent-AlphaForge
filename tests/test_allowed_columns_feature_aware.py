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


def test_validate_spec_unknown_feature_is_clean_error_not_raise():
    spec = StrategySpec(id="t3", name="t3", required_features=["not_a_real_feature"],
                        entry_ce=[Condition(left="close", op=">", right="rsi")])
    errors = validate_spec(spec)   # must NOT raise
    assert any("unknown feature" in e.lower() for e in errors)


def test_compile_spec_emits_required_features_roundtrip():
    from app.ai.compiler import compile_spec
    from app.ai.spec_schema import ExitSpec
    spec = StrategySpec(
        id="t4", name="t4", required_features=["fvg_zones"],
        entry_ce=[Condition(left="close", op=">", right="fvg_top")],
        exits=ExitSpec(target_pct=10, stop_pct=5),
    )
    assert validate_spec(spec) == []          # minimal-valid (declared feature + exits)
    src = compile_spec(spec)
    assert "required_features = ['fvg_zones']" in src
    ns = {}
    exec(src, ns)
    from app.strategies.base import StrategyBase
    cls = next(v for v in ns.values()
               if isinstance(v, type) and issubclass(v, StrategyBase) and v is not StrategyBase)
    assert cls.required_features == ["fvg_zones"]
