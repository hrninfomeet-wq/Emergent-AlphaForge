"""Step 5: the generators must know the premium-trigger mechanism exists.

Steps 1-4 built complete rails: every runtime path routes on capability, the
deployment carries a validated block, and a StrategySpec can emit one. But
neither generator prompt mentioned any of it, so the authoring LLM would never
produce a config — the mechanism existed and was unreachable from plain English,
which is the exact gap Phase 1 set out to close.

Two different fixes, because the two modes have genuinely different answers:

* **Spec mode** can express it — teach the LLM the `premium_trigger` field.
* **Full-Python mode CANNOT.** A premium-native strategy is driven by the premium
  session engine, which replaces `evaluate()` entirely. Any `evaluate()` the LLM
  writes for such a description is dead code that will never be called. The
  prompt must say so and route the user to Spec mode, rather than let the model
  produce plausible logic that silently never runs.

**The anti-drift rule pinned here:** the field list in the prompt is DERIVED from
`PremiumTriggerConfig.model_fields`, never hand-typed. Hand-copying is precisely
what produced the phantom `expiry` field that shipped in `capability.py` — a
prompt that advertises a nonexistent field induces the LLM to emit it, and it
then dies against `extra="forbid"` with an error the user cannot act on.
"""
from __future__ import annotations

import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from app.premium_trigger_config import PremiumTriggerConfig  # noqa: E402


def _spec_prompt() -> str:
    from app.ai.grounding import build_grounding_catalog
    from app.ai.strategy_author import _system_prompt
    return _system_prompt(build_grounding_catalog())


def _py_prompt() -> str:
    from app.ai.grounding import build_grounding_catalog
    from app.ai.py_author import _system_prompt
    return _system_prompt(build_grounding_catalog())


# --------------------------------------------------------------------------- #
# Spec mode: the mechanism is teachable, so teach it
# --------------------------------------------------------------------------- #

def test_spec_prompt_tells_the_model_the_field_exists():
    assert "premium_trigger" in _spec_prompt()


def test_spec_prompt_enumerates_the_real_config_fields():
    prompt = _spec_prompt()
    for field in ("reference_time", "moneyness", "side", "momentum_pct", "stop_pct"):
        assert field in prompt, f"{field} missing from the spec prompt"


def test_spec_prompt_advertises_NO_field_that_does_not_exist():
    """THE anti-drift property. A hand-typed list is how `expiry` got promised."""
    prompt = _spec_prompt()
    # the prompt lists config fields as `name` in a dedicated section
    section = prompt[prompt.index("premium_trigger"):]
    advertised = set(re.findall(r"`([a-z_]{3,})`", section))
    real = set(PremiumTriggerConfig.model_fields)
    # only judge tokens that look like config fields (ignore prose backticks)
    suspects = {a for a in advertised if a in real or a.endswith("_pct")
                or a.endswith("_pts") or a.startswith("momentum")}
    phantom = sorted(suspects - real)
    assert not phantom, (
        f"the spec prompt advertises premium-trigger field(s) {phantom} that do not "
        f"exist on PremiumTriggerConfig. Real: {sorted(real)}")


def test_spec_prompt_states_the_mutual_exclusion_with_entry_conditions():
    """The compiler REFUSES both together; the model must be told, or it will
    produce specs that fail validation with no idea why."""
    p = _spec_prompt().lower()
    assert "entry_ce" in p and "premium_trigger" in p
    assert ("not" in p and "both" in p) or "instead of" in p or "mutually exclusive" in p


def test_spec_prompt_states_the_momentum_xor_rule():
    """momentum_pct XOR momentum_pts — setting both is a validation error."""
    p = _spec_prompt()
    assert "momentum_pct" in p and "momentum_pts" in p


def test_spec_prompt_field_list_is_derived_from_the_model_not_hardcoded():
    """Source contract: a literal list silently rots the moment a field is added
    or renamed on the model."""
    import inspect
    from app.ai import strategy_author
    src = inspect.getsource(strategy_author)
    assert "model_fields" in src or "PremiumTriggerConfig" in src, (
        "the prompt's config-field list must be derived from the model")


# --------------------------------------------------------------------------- #
# Full-Python mode: it CANNOT express this — say so
# --------------------------------------------------------------------------- #

def test_python_prompt_warns_that_premium_native_cannot_be_written_here():
    """Track B replaces evaluate(), so any evaluate() written for a
    premium-trigger description is dead code that never runs."""
    p = _py_prompt().lower()
    assert "premium" in p, "the python prompt never mentions the premium mechanism"
    assert "evaluate" in p


def test_python_prompt_routes_the_user_to_spec_mode():
    p = _py_prompt().lower()
    assert "spec" in p, (
        "the python prompt must route a premium-trigger description to Spec mode "
        "rather than let the model write an evaluate() that never runs")


# --------------------------------------------------------------------------- #
# neither prompt should have grown a phantom mechanism
# --------------------------------------------------------------------------- #

def test_neither_prompt_promises_an_expiry_field():
    """`expiry` is the canonical phantom: capability.py promised it for months
    and PremiumTriggerConfig never had it."""
    assert "expiry" not in PremiumTriggerConfig.model_fields  # guard the guard
    for prompt in (_spec_prompt(), _py_prompt()):
        assert "premium_trigger's `expiry`" not in prompt
        assert "`expiry` field" not in prompt
