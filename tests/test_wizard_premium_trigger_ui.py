"""Step 6: the wizard must carry a premium-trigger config, and show it.

Source contracts over the JSX (this repo's established pattern for wiring
assertions — see tests/test_authored_pipeline_frontend.py).

**The bug this closes is a silent data loss, not a missing pretty panel.**
`loadFromSpec` populated form state from the AI's spec but never read
`spec.premium_trigger`, and `buildSpec` reconstructed the spec from that form
state on Install. So a premium-trigger strategy would:

  1. come back from the AI with a config and EMPTY entry conditions,
  2. render as a blank rule row (`loadFromSpec` substitutes `[emptyCond()]` when
     the entry list is empty), making it look like nothing was generated, and
  3. lose the config entirely at Install, because `buildSpec` never re-emitted it.

The user would author a premium strategy, see an empty rules panel, install it,
and receive an ordinary strategy that cannot trade — with no error at any point.
"""
from __future__ import annotations

import os

_WIZ = os.path.join(os.path.dirname(__file__), "..", "frontend", "src",
                    "components", "strategy", "AuthoringWizard.jsx")


def _src() -> str:
    with open(_WIZ, encoding="utf-8") as fh:
        return fh.read()


def test_the_generated_config_is_captured_from_the_spec():
    """loadFromSpec must read it, or the config is lost the moment it arrives."""
    src = _src()
    assert "premium_trigger" in src, "the wizard never mentions premium_trigger"
    i = src.index("function loadFromSpec")
    body = src[i:i + 3000]
    assert "premium_trigger" in body, (
        "loadFromSpec must capture spec.premium_trigger into state")


def test_the_config_SURVIVES_install():
    """THE silent-loss bug: buildSpec rebuilds the spec from form state, so a
    config it does not re-emit is discarded between generation and install."""
    src = _src()
    i = src.index("const spec = {")
    body = src[i:i + 1500]
    assert "premium_trigger" in body, (
        "buildSpec must re-emit premium_trigger or Install silently drops it")


def test_the_config_is_rendered_so_the_user_can_see_what_was_generated():
    src = _src()
    assert "premium-trigger-panel" in src, (
        "a premium-trigger strategy must show its config; otherwise the user sees "
        "an empty rules panel and assumes generation failed")


def test_the_panel_renders_EVERY_field_dynamically():
    """A panel that just says 'premium strategy' is not review — the user must be
    able to check the numbers the AI chose.

    Asserted as DYNAMIC rendering rather than a hardcoded field list, on purpose:
    a hardcoded panel silently stops showing any field added to
    PremiumTriggerConfig later, so the user would review an incomplete config and
    believe they had seen all of it. Same anti-drift rule the prompt follows.
    """
    src = _src()
    i = src.index("premium-trigger-panel")
    body = src[i:i + 2500]
    assert "Object.entries(premiumTrigger)" in body, (
        "the panel must enumerate the config dynamically so it cannot omit a field")
    # and it must not silently hide values it does not recognise
    assert "JSON.stringify" in body, "non-scalar values must still be shown"


def test_entry_condition_rows_are_hidden_for_a_premium_strategy():
    """The compiler REFUSES a spec carrying both. Showing editable entry rows
    would invite the user to build something that cannot install."""
    src = _src()
    assert "isPremiumTrigger" in src or "premiumTrigger &&" in src, (
        "the entry-condition editor must be conditional on premium-trigger mode")


def test_an_ordinary_strategy_is_unaffected():
    """No config ⇒ the existing rule editor renders exactly as before."""
    src = _src()
    # the entry editor must still exist and still be reachable
    assert "entryCe" in src and "setEntryCe" in src
