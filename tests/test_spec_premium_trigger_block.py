"""Step 4: a StrategySpec can finally EMIT a premium-trigger config.

This is the piece that closes the loop Phase 1 opened. `classify_rule` returns
BUILDABLE_NOW for premium-trigger concepts; Steps 1-3 made every runtime path
route on capability instead of one hardcoded id; but a spec still had no field
to carry the config, so an AI-authored strategy could never become premium-native.

The contract Step 3 fixed: **a strategy is premium-native because of its OWN
declared defaults**, never because a block was attached at deploy time (that
rule is what stops a block from silently bypassing an ordinary strategy's
`evaluate()`). So the compiler must emit the config into the generated plugin's
`parameter_schema` — that, and only that, is what makes
`is_premium_trigger_strategy` true for it.

Two refusals are pinned here, both instances of "fail closed on an unbuildable
config" rather than installing something that silently no-ops:

* a spec carrying BOTH a premium-trigger config AND per-bar entry conditions is
  self-contradictory — Track B bypasses `evaluate()`, so those conditions could
  never fire, and the user would watch a strategy ignore rules it displays;
* an invalid/unknown-field config is rejected at compile time, not at the first
  evaluated bar.
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from app.ai.compiler import compile_spec, validate_spec  # noqa: E402
from app.ai.spec_schema import Condition, ExitSpec, StrategySpec  # noqa: E402
from app.premium_trigger_dispatch import is_premium_trigger_strategy  # noqa: E402

_CFG = {"reference_time": "09:31", "moneyness": "itm1", "side": "first_to_trigger",
        "momentum_pct": 15.0, "stop_pct": 20.0, "target_pct": 50.0, "lots": 2}


def _spec(**kw):
    base = dict(id="my_premium_thing", name="My Premium Thing")
    base.update(kw)
    return StrategySpec(**base)


# --------------------------------------------------------------------------- #
# the field exists and validates
# --------------------------------------------------------------------------- #

def test_spec_has_the_field():
    assert "premium_trigger" in StrategySpec.model_fields


def test_a_valid_config_passes_validation():
    assert validate_spec(_spec(premium_trigger=dict(_CFG))) == []


def test_an_unknown_config_field_is_a_clean_validation_error():
    """extra='forbid' surfaces as a spec error — this is exactly what an LLM
    emits when it invents a field name (it invented `expiry` once already)."""
    errors = validate_spec(_spec(premium_trigger={**_CFG, "expiry": "weekly"}))
    assert errors and any("premium_trigger" in e for e in errors)


def test_a_config_with_no_entry_trigger_is_refused():
    errors = validate_spec(_spec(premium_trigger={"reference_time": "09:31",
                                                  "moneyness": "itm1"}))
    assert errors and any("premium_trigger" in e for e in errors)


def test_a_spec_with_BOTH_a_config_and_entry_conditions_is_refused():
    """Self-contradictory: Track B bypasses evaluate(), so these conditions could
    never fire. Installing it would show the user rules the engine ignores."""
    errors = validate_spec(_spec(
        premium_trigger=dict(_CFG),
        entry_ce=[Condition(left="close", op=">", right="ema21")],
    ))
    assert errors
    assert any("entry" in e.lower() and "premium_trigger" in e for e in errors), errors


def test_an_ordinary_spec_is_completely_unaffected():
    """No config ⇒ nothing about the existing compile path changes."""
    spec = _spec(entry_ce=[Condition(left="close", op=">", right="ema21")],
                 exits=ExitSpec(spot_stop_pts=15.0))
    assert validate_spec(spec) == []
    src = compile_spec(spec)
    assert "premium_trigger" not in src


# --------------------------------------------------------------------------- #
# codegen: the generated plugin must actually BE premium-native
# --------------------------------------------------------------------------- #

def _compile_and_load(spec):
    src = compile_spec(spec)
    ns: dict = {}
    exec(compile(src, "<generated>", "exec"), ns)  # noqa: S102 - compiler output under test
    cls = next(v for k, v in ns.items()
               if isinstance(v, type) and getattr(v, "id", None) == spec.id)
    return src, cls()


def test_generated_plugin_declares_the_config_as_schema_DEFAULTS():
    src, inst = _compile_and_load(_spec(premium_trigger=dict(_CFG)))
    schema = inst.parameter_schema
    for key, val in _CFG.items():
        assert key in schema, f"{key} missing from the generated parameter_schema"
        assert schema[key]["default"] == val, f"{key} default mismatch"


def test_THE_END_TO_END_PROOF_generated_plugin_is_premium_native():
    """The whole point of Phase 1: an AI-authored strategy is now recognised by
    the same predicate every runtime path routes on."""
    _src, inst = _compile_and_load(_spec(premium_trigger=dict(_CFG)))
    assert is_premium_trigger_strategy(inst) is True


def test_generated_premium_plugin_has_an_INERT_evaluate():
    """Track B never calls it. It must return NONE rather than pretend to
    decide — a plausible-looking evaluate would be a lie about what runs."""
    _src, inst = _compile_and_load(_spec(premium_trigger=dict(_CFG)))
    import pandas as pd
    sig = inst.evaluate(pd.Series({"close": 100.0}), pd.Series({"close": 99.0}),
                        inst.default_params(), {})
    assert sig.direction == "NONE"


def test_generated_premium_plugin_says_WHY_its_evaluate_is_inert():
    """A future reader must not 'fix' the stub back into a real evaluate."""
    src, _inst = _compile_and_load(_spec(premium_trigger=dict(_CFG)))
    low = src.lower()
    assert "track b" in low or "premium session engine" in low


def test_an_ordinary_generated_plugin_is_NOT_premium_native():
    _src, inst = _compile_and_load(
        _spec(entry_ce=[Condition(left="close", op=">", right="ema21")],
              exits=ExitSpec(spot_stop_pts=15.0)))
    assert is_premium_trigger_strategy(inst) is False
