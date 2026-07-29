"""A spec must be able to carry the FULL shipped premium surface, not a subset.

User-reported (2026-07-29): a BUILD verdict, then generation reported
`couldnt_map` for the lazy-leg contingency AND for the session targets /
square-off time. The AI was right — the spec genuinely could not carry them.

Measured cause: `StrategySpec.premium_trigger` validated against
`PremiumTriggerConfig` (14 fields), while the shipped plugin supports 20 params.
Twelve shipped capabilities were therefore inexpressible in an authored strategy:

    entry_cutoff, exit_time, leg_mode, lazy_enabled, lazy_momentum_pct,
    lazy_moneyness, lazy_stop_pct, lazy_target_pct,
    session_max_loss_rupees, session_max_profit_rupees, vix_min, vix_max

Same promise-vs-reality class as the rest of Phase 1, pointing the other way:
`classify_rule` says lazy-leg is BUILDABLE_NOW (true — the plugin does it), but
the spec had nowhere to put it.

**`PremiumTriggerConfig` is deliberately NOT widened.** It is what the
byte-identical parity test protects and what the dispatcher hands to the sim;
adding fields there risks the shipped strategy's numbers. The SPEC validates
against the union of that config and the shipped plugin's own declared params —
derived, so it cannot drift as the plugin gains capability.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from app.ai.compiler import compile_spec, validate_spec  # noqa: E402
from app.ai.spec_schema import StrategySpec  # noqa: E402
from app.premium_trigger_config import PremiumTriggerConfig  # noqa: E402
from app.premium_trigger_dispatch import (  # noqa: E402
    is_premium_trigger_strategy,
    premium_trigger_allowed_keys,
)
from app.strategies.base import get_registry  # noqa: E402

_CORE = {"reference_time": "09:31", "moneyness": "itm1",
         "side": "first_to_trigger", "momentum_pct": 15.0, "stop_pct": 20.0}

_FIVE_B = {"leg_mode": "both", "lazy_enabled": True, "lazy_momentum_pct": 20.0,
           "lazy_stop_pct": 25.0, "lazy_moneyness": "itm1",
           "entry_cutoff": "13:00", "exit_time": "14:30",
           "session_max_loss_rupees": 5000.0, "session_max_profit_rupees": 8000.0}


def _spec(**kw):
    base = dict(id="full_surface_thing", name="Full Surface")
    base.update(kw)
    return StrategySpec(**base)


def _shipped():
    reg = get_registry()
    reg.auto_discover()
    return set(reg.get("premium_momentum").parameter_schema or {})


# --------------------------------------------------------------------------- #
# the allowed surface is DERIVED, and covers everything shipped
# --------------------------------------------------------------------------- #

def test_every_shipped_plugin_param_is_expressible():
    """THE regression: 12 shipped capabilities were inexpressible."""
    allowed = premium_trigger_allowed_keys()
    missing = sorted(_shipped() - allowed)
    assert not missing, f"shipped but still inexpressible in a spec: {missing}"


def test_the_core_config_fields_are_still_allowed():
    allowed = premium_trigger_allowed_keys()
    assert set(PremiumTriggerConfig.model_fields) <= allowed


def test_the_surface_is_derived_not_hardcoded():
    """It must widen automatically as the plugin gains params — a literal list is
    how this drifted out of date in the first place."""
    import inspect
    from app import premium_trigger_dispatch as d
    src = inspect.getsource(d.premium_trigger_allowed_keys)
    assert "parameter_schema" in src and "model_fields" in src


# --------------------------------------------------------------------------- #
# the user's actual strategy now validates
# --------------------------------------------------------------------------- #

def test_a_lazy_leg_strategy_validates():
    """This is precisely what came back as couldnt_map."""
    assert validate_spec(_spec(premium_trigger={**_CORE, **_FIVE_B})) == []


def test_session_targets_and_squareoff_validate():
    assert validate_spec(_spec(premium_trigger={
        **_CORE, "exit_time": "14:30",
        "session_max_loss_rupees": 5000.0, "session_max_profit_rupees": 8000.0})) == []


def test_vix_bounds_validate():
    assert validate_spec(_spec(premium_trigger={**_CORE, "vix_min": 12.0, "vix_max": 28.0})) == []


# --------------------------------------------------------------------------- #
# widening must not weaken validation
# --------------------------------------------------------------------------- #

def test_a_genuinely_unknown_field_is_still_refused():
    """Widening the surface must not turn the block into a free-form dict —
    `expiry` is the canonical invented field."""
    errors = validate_spec(_spec(premium_trigger={**_CORE, "expiry": "weekly"}))
    assert errors and any("premium_trigger" in e for e in errors)


def test_the_cross_field_rules_still_apply():
    """momentum_pct XOR momentum_pts is enforced by PremiumTriggerConfig and must
    survive the split — the 5B fields go around that model, the core must not."""
    errors = validate_spec(_spec(premium_trigger={**_CORE, "momentum_pts": 10.0}))
    assert errors and any("premium_trigger" in e for e in errors)


def test_no_entry_trigger_is_still_refused():
    errors = validate_spec(_spec(premium_trigger={
        "reference_time": "09:31", "moneyness": "itm1", **_FIVE_B}))
    assert errors and any("premium_trigger" in e for e in errors)


# --------------------------------------------------------------------------- #
# end to end: the generated plugin carries them and is still premium-native
# --------------------------------------------------------------------------- #

def _compile_and_load(spec):
    ns: dict = {}
    exec(compile(compile_spec(spec), "<gen>", "exec"), ns)  # noqa: S102
    cls = next(v for v in ns.values()
               if isinstance(v, type) and getattr(v, "id", None) == spec.id)
    return cls()


def test_generated_plugin_declares_the_5B_params_too():
    inst = _compile_and_load(_spec(premium_trigger={**_CORE, **_FIVE_B}))
    schema = inst.parameter_schema
    for key, val in _FIVE_B.items():
        assert key in schema, f"{key} missing from the generated plugin"
        assert schema[key]["default"] == val


def test_generated_plugin_is_still_premium_native():
    """The predicate every runtime path routes on must still recognise it."""
    inst = _compile_and_load(_spec(premium_trigger={**_CORE, **_FIVE_B}))
    assert is_premium_trigger_strategy(inst) is True


def test_the_prompt_teaches_the_wider_surface():
    """If the generator is not told these fields exist, it will keep reporting
    them as couldnt_map — which is the bug the user actually hit."""
    from app.ai.grounding import build_grounding_catalog
    from app.ai.strategy_author import _system_prompt
    prompt = _system_prompt(build_grounding_catalog())
    for name in ("lazy_enabled", "leg_mode", "exit_time", "session_max_loss_rupees"):
        assert name in prompt, f"the prompt never mentions {name}"
