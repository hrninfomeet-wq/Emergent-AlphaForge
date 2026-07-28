"""A promise the classifier makes must name a mechanism that actually exists.

`classify_rule`'s verdict messages are read by TWO audiences that both act on
them: the human in the authoring wizard, and the authoring LLM itself (the
messages are fed back as grounding). So a message naming a field that does not
exist is not cosmetic — it induces the LLM to emit a config referencing that
field, which then dies against `PremiumTriggerConfig`'s `extra="forbid"` with an
error the user cannot act on.

Two such false claims shipped undetected (found 2026-07-28):

  * `"premium_trigger_config's \\`expiry\\` field"` — `PremiumTriggerConfig` has
    **no** `expiry` field at all.
  * `"premium_trigger_config's \\`side\\` field (CE|PE|BOTH)"` — `side` is
    `Literal["ce","pe","first_to_trigger"]`; there is **no** `BOTH`.

They shipped because ~51 tests touch `classify_rule` and **none pins message
text against the schema it cites** — every existing assertion is either
verdict-only or a loose `"premium" in msg.lower()` substring check. The
classifier was verified to be internally consistent, never to be truthful.

This file closes that specific hole: any message that names a field ON a schema
must name a field that schema really has.
"""
from __future__ import annotations

import os
import re
import sys
from typing import Set

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from app.ai import capability as cap  # noqa: E402
from app.premium_trigger_config import PremiumTriggerConfig  # noqa: E402

_SRC_PATH = os.path.join(
    os.path.dirname(__file__), "..", "backend", "app", "ai", "capability.py")


def _source() -> str:
    with open(_SRC_PATH, encoding="utf-8") as fh:
        return fh.read()


def _named_config_fields(src: str) -> Set[str]:
    """Every field name a message attributes to premium_trigger_config."""
    return set(re.findall(r"premium_trigger_config's `([a-z_]+)` field", src))


def test_every_field_a_message_attributes_to_the_config_actually_exists():
    """THE regression. `expiry` was promised and never existed."""
    named = _named_config_fields(_source())
    # An empty set is a legitimate state (no message need name a field); the
    # invariant is that whatever IS named must be real.
    real = set(PremiumTriggerConfig.model_fields)
    phantom = sorted(named - real)
    assert not phantom, (
        f"capability.py promises premium_trigger_config field(s) {phantom} that do "
        f"not exist on the model. Real fields: {sorted(real)}")


def test_the_side_values_a_message_advertises_are_the_real_literal_values():
    """`(CE|PE|BOTH)` was advertised; the model allows ce|pe|first_to_trigger."""
    src = _source()
    advertised = set(re.findall(r"`side` field \(([^)]*)\)", src))
    if not advertised:
        return  # no parenthesised value list is fine — nothing to verify
    import typing
    real = {str(v).lower() for v in typing.get_args(
        PremiumTriggerConfig.model_fields["side"].annotation)}
    for group in advertised:
        for token in re.split(r"[|/,]", group):
            token = token.strip().lower()
            if not token:
                continue
            assert token in real, (
                f"message advertises side value {token!r}, but the model allows "
                f"{sorted(real)}. An LLM reading this will emit an invalid config.")


def test_expiry_selection_verdict_does_not_cite_a_nonexistent_field():
    """End-to-end through the real classifier, not just the source text."""
    v = cap.classify_rule(cap.RuleTokens(cols=set(), concepts={"weekly_expiry"}))
    msg = v.message
    assert "premium_trigger_config's `expiry`" not in msg, (
        "the expiry verdict still cites a field PremiumTriggerConfig does not have")


def test_option_kind_verdict_does_not_advertise_a_BOTH_side_value():
    import typing
    v = cap.classify_rule(cap.RuleTokens(cols=set(), concepts={"ce_leg"}))
    real = {str(x).lower() for x in typing.get_args(
        PremiumTriggerConfig.model_fields["side"].annotation)}
    # Look for an advertised value LIST after `side`, e.g. "(CE|PE|BOTH)" —
    # not any stray occurrence of the word, which would flag ordinary prose.
    for group in re.findall(r"`side`[^.]*?\(([^)]*)\)", v.message):
        for token in re.split(r"[|/,]", group):
            token = token.strip().lower()
            if token and token.isalpha():
                assert token in real, (
                    f"verdict advertises side value {token!r}; model allows {sorted(real)}")


def test_a_message_naming_the_deployment_layer_names_a_real_deployment_field():
    """Same class, different schema: messages that point at the deployment must
    point at fields DeploymentCreateReq actually has."""
    from app.schemas import DeploymentCreateReq
    src = _source()
    named = set(re.findall(r"deployment's `([a-z_]+)` field", src))
    real = set(DeploymentCreateReq.model_fields)
    phantom = sorted(named - real)
    assert not phantom, (
        f"capability.py promises deployment field(s) {phantom} that do not exist. "
        f"Real fields include: {sorted(real)[:12]}...")
