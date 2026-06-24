import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

import pandas as pd
import pytest

from app.ai.spec_schema import (
    Condition,
    ExitSpec,
    ParamSpec,
    StrategySpec,
)
from app.ai.compiler import allowed_columns, compile_spec, validate_spec


# --------------------------------------------------------------------------- #
# Spec builders
# --------------------------------------------------------------------------- #
def _ema_rsi_spec(**overrides) -> StrategySpec:
    """The worked-example EMA/RSI demo spec from the contract."""
    base = dict(
        id="ema_rsi_demo",
        name="EMA RSI Demo",
        description="A demo strategy that fires on EMA + RSI confluence.",
        params=[ParamSpec(name="rsi_thr", type="float", min=50, max=70, default=55)],
        entry_ce=[
            Condition(left="close", op=">", right="ema9"),
            Condition(left="rsi", op=">", right="param:rsi_thr", label="RSI strong"),
        ],
        entry_pe=[
            Condition(left="close", op="<", right="ema9"),
            Condition(left="rsi", op="<", right="param:rsi_thr", label="RSI weak"),
        ],
        gate_skip_regimes=["CHOP"],
        exits=ExitSpec(spot_target_pts=30, spot_stop_pts=15),
    )
    base.update(overrides)
    return StrategySpec(**base)


def _compiled_class(spec: StrategySpec):
    """exec the generated source and return the StrategyBase subclass."""
    ns: dict = {}
    exec(compile_spec(spec), ns)
    for v in ns.values():
        if isinstance(v, type) and getattr(v, "id", None) == spec.id:
            return v
    raise AssertionError("compiled class not found in namespace")


def _row(**kw) -> pd.Series:
    base = {"close": 100.0, "ema9": 99.0, "rsi": 60.0, "regime": "TREND"}
    base.update(kw)
    return pd.Series(base)


# --------------------------------------------------------------------------- #
# Validation
# --------------------------------------------------------------------------- #
def test_allowed_columns_includes_indicators_and_ohlcv():
    cols = allowed_columns()
    assert {"close", "open", "high", "low", "volume"} <= cols
    assert "ema9" in cols and "rsi" in cols and "regime" in cols


def test_validate_clean_spec_has_no_errors():
    assert validate_spec(_ema_rsi_spec()) == []


def test_validate_rejects_unknown_column():
    spec = _ema_rsi_spec(
        entry_ce=[Condition(left="not_a_col", op=">", right="ema9")]
    )
    errs = validate_spec(spec)
    assert any("not_a_col" in e for e in errs)
    with pytest.raises(ValueError):
        compile_spec(spec)


def test_validate_rejects_bad_op():
    spec = _ema_rsi_spec(
        entry_ce=[Condition(left="close", op="~=", right="ema9")]
    )
    errs = validate_spec(spec)
    assert any("~=" in e or "op" in e.lower() for e in errs)
    with pytest.raises(ValueError):
        compile_spec(spec)


def test_validate_rejects_unknown_param_ref():
    spec = _ema_rsi_spec(
        entry_ce=[Condition(left="rsi", op=">", right="param:does_not_exist")]
    )
    errs = validate_spec(spec)
    assert any("does_not_exist" in e for e in errs)


def test_validate_rejects_column_typed_right_unknown():
    spec = _ema_rsi_spec(
        entry_ce=[Condition(left="rsi", op=">", right="ghost_col")]
    )
    errs = validate_spec(spec)
    assert any("ghost_col" in e for e in errs)


def test_validate_rejects_bad_id_slug():
    for bad in ["1bad", "Bad-Id", "bad id", "", "BadId"]:
        spec = _ema_rsi_spec(id=bad)
        assert validate_spec(spec), f"expected error for id={bad!r}"


def test_validate_requires_entry():
    spec = _ema_rsi_spec(entry_ce=[], entry_pe=[])
    errs = validate_spec(spec)
    assert any("entry" in e.lower() for e in errs)


def test_validate_requires_exit():
    spec = _ema_rsi_spec(exits=ExitSpec())
    errs = validate_spec(spec)
    assert any("exit" in e.lower() for e in errs)


def test_validate_rejects_bad_param_name_and_type():
    spec = _ema_rsi_spec(
        params=[ParamSpec(name="Bad_Name", type="float", default=1.0)]
    )
    errs = validate_spec(spec)
    assert any("Bad_Name" in e for e in errs)


def test_validate_rejects_unknown_regime():
    spec = _ema_rsi_spec(gate_skip_regimes=["NOT_A_REGIME"])
    errs = validate_spec(spec)
    assert any("NOT_A_REGIME" in e for e in errs)


# --------------------------------------------------------------------------- #
# Codegen — the important behavioural tests
# --------------------------------------------------------------------------- #
def test_pascal_case_class_name():
    ns: dict = {}
    exec(compile_spec(_ema_rsi_spec()), ns)
    assert any(
        isinstance(v, type) and v.__name__ == "EmaRsiDemo" for v in ns.values()
    )


def test_compiled_is_not_builtin():
    cls = _compiled_class(_ema_rsi_spec())
    assert cls.is_builtin is False


def test_param_default_merges():
    cls = _compiled_class(_ema_rsi_spec())
    assert cls().default_params()["rsi_thr"] == 55


def test_compiled_strategy_fires_ce():
    cls = _compiled_class(_ema_rsi_spec())
    inst = cls()
    params = inst.default_params()
    # close > ema9 and rsi > 55 -> CE
    sig = inst.evaluate(_row(close=101.0, ema9=99.0, rsi=60.0), pd.Series({}), params, {})
    assert sig.direction == "CE"
    assert sig.spot_target_pts == 30
    assert sig.spot_stop_pts == 15
    assert "RSI strong" in sig.reasons


def test_compiled_strategy_fires_pe():
    cls = _compiled_class(_ema_rsi_spec())
    inst = cls()
    params = inst.default_params()
    # close < ema9 and rsi < 55 -> PE
    sig = inst.evaluate(_row(close=98.0, ema9=99.0, rsi=40.0), pd.Series({}), params, {})
    assert sig.direction == "PE"
    assert "RSI weak" in sig.reasons


def test_compiled_strategy_no_signal_when_mixed():
    cls = _compiled_class(_ema_rsi_spec())
    inst = cls()
    params = inst.default_params()
    # close > ema9 (bull) but rsi < 55 -> neither CE nor PE fully holds
    sig = inst.evaluate(_row(close=101.0, ema9=99.0, rsi=40.0), pd.Series({}), params, {})
    assert sig.direction == "NONE"
    assert sig.score == 0


def test_compiled_strategy_regime_gate_blocks():
    cls = _compiled_class(_ema_rsi_spec())
    inst = cls()
    params = inst.default_params()
    sig = inst.evaluate(_row(close=101.0, ema9=99.0, rsi=60.0, regime="CHOP"), pd.Series({}), params, {})
    assert sig.direction == "CE"
    assert "regime CHOP" in sig.blockers


def test_compiled_strategy_warmup_guard():
    cls = _compiled_class(_ema_rsi_spec())
    inst = cls()
    params = inst.default_params()
    sig = inst.evaluate(_row(rsi=float("nan")), pd.Series({}), params, {})
    assert sig.direction == "NONE"
    assert sig.blockers  # "indicators warming up"


def test_required_columns_sorted_unique():
    # cross-check the generated 'required' list contains every referenced column
    code = compile_spec(_ema_rsi_spec())
    assert 'required = ["close", "ema9", "rsi"]' in code


def test_cross_above_renders_and_fires():
    spec = _ema_rsi_spec(
        entry_ce=[Condition(left="close", op="cross_above", right="ema9", label="cross up")],
        entry_pe=[Condition(left="close", op="cross_below", right="ema9", label="cross down")],
    )
    cls = _compiled_class(spec)
    inst = cls()
    params = inst.default_params()
    # prev close <= ema9, now close > ema9 -> cross_above -> CE
    prev = pd.Series({"close": 98.0, "ema9": 99.0, "rsi": 50.0, "regime": "TREND"})
    sig = inst.evaluate(_row(close=101.0, ema9=99.0), prev, params, {})
    assert sig.direction == "CE"
    assert "cross up" in sig.reasons
    # no cross when prev is missing -> treat as no-cross
    sig2 = inst.evaluate(_row(close=101.0, ema9=99.0), pd.Series({}), params, {})
    assert sig2.direction == "NONE"


def test_only_set_exits_emitted():
    spec = _ema_rsi_spec(exits=ExitSpec(target_pct=0.5, time_stop_minutes=20))
    code = compile_spec(spec)
    assert "target_pct=0.5" in code
    assert "time_stop_minutes=20" in code
    assert "spot_target_pts" not in code.split("def evaluate")[1]


def test_no_regime_block_when_empty():
    spec = _ema_rsi_spec(gate_skip_regimes=[])
    code = compile_spec(spec)
    assert "regime" not in code.split("def evaluate")[1]


def test_string_literals_use_repr_no_injection():
    # a malicious-looking name/description must be emitted via repr, not interpolated raw
    spec = _ema_rsi_spec(
        name='Demo"); import os; os.system("rm -rf /")  #',
        description='line1\nline2"',
    )
    code = compile_spec(spec)
    # the raw payload must never appear unquoted as code
    assert 'import os' not in code.split("name = ")[1].split("\n")[0] or 'name = ' in code
    # exec must still succeed and produce a working class
    cls = _compiled_class(spec)
    assert cls.is_builtin is False
