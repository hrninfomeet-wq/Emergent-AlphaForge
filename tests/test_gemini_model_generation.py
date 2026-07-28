"""Spec mode moves to a current Gemini model — without silently gagging it.

Researched 2026-07-29 against Google's own docs:

  * `gemini-3.6-flash`      — GA 21 Jul 2026. Newest generally-available model.
  * `gemini-3.5-flash-lite` — GA, same date.
  * `gemini-3.1-pro-preview`— PREVIEW only (19 Feb 2026); no GA 3.x Pro exists.
  * `gemini-2.5-pro`        — still supported, but now the OLDEST thing in our stack.

So there is no GA "Gemini 3 Pro" to point at. The strongest GA option is
`gemini-3.6-flash`, which the docs describe as stronger on complex agentic tasks
than 3.5 Flash, at a lower price, with a 64k output cap.

**The trap this file exists to prevent.** `_gemini.py` disables thinking with
`if "flash" in model` — a heuristic written for `gemini-2.5-flash`, where thinking
tokens were drawn from `max_output_tokens` and truncated the JSON mid-string. A
bare model swap would therefore upgrade the model AND silently switch off the
reasoning that is the point of upgrading. Newer generations must keep their
defaults; the 2.5-era workaround stays scoped to the 2.5 era.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from app.ai import llm_client  # noqa: E402
from app.ai._gemini import DEFAULT_MAX_TOKENS, _base_config  # noqa: E402


def test_spec_mode_uses_a_current_generation_model():
    """FAST drives Spec generate + the feasibility gate — the paths that matter
    most for authoring."""
    model = llm_client._MODELS["gemini"][llm_client.FAST]
    assert model == "gemini-3.6-flash", f"FAST is {model!r}"


def test_no_preview_model_is_a_default():
    """gemini-3.1-pro-preview is the only 3.x 'Pro' and it is PREVIEW. A path
    that writes installable strategy code should not default to a preview model."""
    for tier, model in llm_client._MODELS["gemini"].items():
        assert "preview" not in model, f"{tier} defaults to a preview model: {model}"


def test_the_env_override_still_works():
    """Changing models must stay possible without a code edit."""
    assert ("gemini", llm_client.FAST) in llm_client._ENV_OVERRIDE
    assert llm_client._ENV_OVERRIDE[("gemini", llm_client.FAST)] == "GEMINI_FAST_MODEL"


# --------------------------------------------------------------------------- #
# the thinking heuristic must not gag a newer model
# --------------------------------------------------------------------------- #

def _cfg(model, _unused=None):
    return _base_config("sys", model, DEFAULT_MAX_TOKENS)


def _thinking_budget(cfg):
    return (cfg.get("thinking_config") or {}).get("thinking_budget")


def test_the_2_5_flash_workaround_is_preserved():
    """It was a measured fix for a real truncation bug — do not lose it."""
    cfg = _cfg("gemini-2.5-flash", None)
    assert _thinking_budget(cfg) == 0


def test_2_5_pro_still_keeps_its_thinking():
    cfg = _cfg("gemini-2.5-pro", None)
    assert _thinking_budget(cfg) is None


def test_a_NEWER_flash_model_is_NOT_gagged():
    """THE regression this file guards. `if "flash" in model` would disable
    thinking on gemini-3.6-flash purely because of its name — upgrading the model
    while switching off the reasoning that motivated the upgrade."""
    cfg = _cfg("gemini-3.6-flash", None)
    assert _thinking_budget(cfg) is None, (
        "gemini-3.6-flash must keep its default thinking; the budget-0 workaround "
        "was measured on the 2.5 generation only")


def test_the_default_spec_model_is_not_gagged_either():
    """End-to-end: whatever FAST points at must not arrive pre-gagged."""
    cfg = _cfg(llm_client._MODELS["gemini"][llm_client.FAST], None)
    assert _thinking_budget(cfg) is None


def test_an_output_budget_is_still_set():
    """The truncation guard's other half — a generous max_output_tokens — must
    survive regardless of which model is selected."""
    for model in ("gemini-2.5-flash", "gemini-3.6-flash", "gemini-2.5-pro"):
        cfg = _cfg(model, None)
        assert int(cfg.get("max_output_tokens") or 0) >= 8192, model
