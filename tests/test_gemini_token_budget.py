"""Guard against the real-world Gemini truncation bug the user hit in Strategy Library:
    "AI generation failed: The AI (gemini-2.5-pro) response was cut off at the
     8000-token limit before it finished — the strategy description is likely too
     long or too complex to parse in one pass."

Root cause: gemini-2.5-pro is a "thinking" model whose thinking tokens draw from
max_output_tokens; an 8192 budget (and, worse, py_author.py's explicit 8000) leaves
almost no room for the actual JSON output → truncation on every non-trivial rule set.

Fixes locked in by these tests:
1. `_gemini.DEFAULT_MAX_TOKENS` is generous enough for a 2.5-pro thinking + JSON call
   (≥ 32k tokens — google-genai supports up to 65k on 2.5 models).
2. `_anthropic.DEFAULT_MAX_TOKENS` is generous enough for a Sonnet/Opus JSON call
   (≥ 16k — Anthropic ships 64k on 4.x families).
3. `llm_client.complete_structured` default is at least as high as the per-backend
   default (an 8192 wrapper default silently re-introduced the truncation once — see
   the audit S1 note in llm_client.py; guard against it regressing again).
4. `py_author.author_python` no longer hard-caps at 8000 (which was the exact limit
   from the user's error message); it uses the wrapper default or a generous override.

All tests are host-safe (no google-genai / anthropic SDK required — fake modules).
"""
from __future__ import annotations

import sys, types
from pathlib import Path

import pytest
from pydantic import BaseModel

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))


class Toy(BaseModel):
    x: int


def _install_fake_genai(monkeypatch, seen: dict | None = None):
    """Fake google.genai that records the config passed to generate_content."""
    google_mod = types.ModuleType("google")
    genai_mod = types.ModuleType("google.genai")
    errors_mod = types.ModuleType("google.genai.errors")
    errors_mod.APIError = type("APIError", (Exception,), {})

    class _Models:
        def generate_content(self, *, model, contents, config):
            if seen is not None:
                seen["model"] = model
                seen["config"] = dict(config)
            class R:
                parsed = Toy(x=1)
                text = None
                candidates = []
            return R()

    genai_mod.Client = type(
        "Client", (), {"__init__": lambda self, *a, **k: setattr(self, "models", _Models())}
    )
    genai_mod.errors = errors_mod
    google_mod.genai = genai_mod
    monkeypatch.setitem(sys.modules, "google", google_mod)
    monkeypatch.setitem(sys.modules, "google.genai", genai_mod)
    monkeypatch.setitem(sys.modules, "google.genai.errors", errors_mod)


def _install_fake_anthropic(monkeypatch, seen: dict | None = None):
    """Fake anthropic SDK that records max_tokens on the messages.create call."""
    ant_mod = types.ModuleType("anthropic")

    class _Block:
        type = "tool_use"
        name = "return_structured"
        input = {"x": 2}

    class _Msg:
        stop_reason = "end_turn"
        content = [_Block()]

    class _Messages:
        def create(self, **kwargs):
            if seen is not None:
                seen.update(kwargs)
            return _Msg()

    class Anthropic:
        def __init__(self, *a, **k):
            self.messages = _Messages()

    ant_mod.Anthropic = Anthropic
    monkeypatch.setitem(sys.modules, "anthropic", ant_mod)


# --------------------------------------------------------------------------
# 1. Per-backend defaults are actually generous enough for 2.5-pro / Sonnet.
# --------------------------------------------------------------------------
def test_gemini_default_max_tokens_is_generous_enough_for_thinking_models():
    """8192 is not enough for gemini-2.5-pro (thinking tokens drawn from same budget).
    google-genai supports up to 65,536 on 2.5 models; 32,768 is the safe floor."""
    from app.ai import _gemini
    assert _gemini.DEFAULT_MAX_TOKENS >= 32768, (
        f"_gemini.DEFAULT_MAX_TOKENS is {_gemini.DEFAULT_MAX_TOKENS}; must be >= 32768 "
        "so gemini-2.5-pro's thinking tokens don't consume the JSON output budget."
    )


def test_anthropic_default_max_tokens_is_generous_enough():
    """Anthropic ships 64k on Claude 4.x; 16k is the safe minimum for structured
    strategy authoring output (rulesets + fidelity + notes can easily hit 8-12k)."""
    from app.ai import _anthropic
    assert _anthropic.DEFAULT_MAX_TOKENS >= 16384, (
        f"_anthropic.DEFAULT_MAX_TOKENS is {_anthropic.DEFAULT_MAX_TOKENS}; must be >= 16384."
    )


def test_llm_client_default_matches_backend_defaults_or_higher():
    """The wrapper default MUST be >= the per-backend default. A lower wrapper default
    silently OVERRODE the per-backend default once already — see the audit S1 comment
    in llm_client.py — and re-introduced the exact Gemini truncation bug the user hit."""
    from app.ai import _gemini, _anthropic
    from app.ai import llm_client
    import inspect

    sig = inspect.signature(llm_client.complete_structured)
    wrapper_default = sig.parameters["max_tokens"].default
    assert wrapper_default >= _gemini.DEFAULT_MAX_TOKENS, (
        f"llm_client.complete_structured max_tokens default ({wrapper_default}) is lower "
        f"than _gemini.DEFAULT_MAX_TOKENS ({_gemini.DEFAULT_MAX_TOKENS}); this silently "
        "re-introduces the truncation bug."
    )
    assert wrapper_default >= _anthropic.DEFAULT_MAX_TOKENS


# --------------------------------------------------------------------------
# 2. The default actually reaches the SDK call.
# --------------------------------------------------------------------------
def test_gemini_default_budget_reaches_sdk_call(monkeypatch):
    """Regression: confirm the DEFAULT_MAX_TOKENS is what actually lands in the config."""
    seen: dict = {}
    _install_fake_genai(monkeypatch, seen=seen)
    from app.ai import _gemini
    _gemini.call(model="gemini-2.5-pro", system="s", user="u", output_model=Toy)
    assert seen["config"]["max_output_tokens"] == _gemini.DEFAULT_MAX_TOKENS


def test_gemini_pro_does_not_disable_thinking(monkeypatch):
    """2.5-pro requires thinking; disabling it via thinking_budget=0 is an error.
    Only flash tier gets thinking disabled."""
    seen: dict = {}
    _install_fake_genai(monkeypatch, seen=seen)
    from app.ai import _gemini
    _gemini.call(model="gemini-2.5-pro", system="s", user="u", output_model=Toy)
    # pro path: thinking_config is either absent OR set to a NON-ZERO budget.
    tc = seen["config"].get("thinking_config")
    if tc is not None:
        assert tc.get("thinking_budget", 1) != 0, (
            "gemini-2.5-pro should not have thinking disabled — that would break the "
            "model (thinking is not optional on pro)."
        )


# --------------------------------------------------------------------------
# 3. py_author no longer hard-caps at 8000 (the exact number from user's error).
# --------------------------------------------------------------------------
def test_py_author_does_not_hard_cap_below_default(monkeypatch):
    """py_author.py had `max_tokens=8000` — this was the source of the user's error
    'The AI (gemini-2.5-pro) response was cut off at the 8000-token limit'. It must
    now use at least the wrapper default (which is >= 32k)."""
    import inspect
    from app.ai import llm_client, py_author

    real_default = inspect.signature(llm_client.complete_structured).parameters["max_tokens"].default

    seen_max: dict = {}
    def fake_complete_structured(*, tier, system, user, output_model, provider=None,
                                  max_tokens=real_default):
        # Record whatever py_author passed; if it passed nothing explicitly, we see
        # the real wrapper's default (i.e., the correct high value) — which is the
        # whole point of the fix.
        seen_max["max_tokens"] = max_tokens
        # Fake structured return in the shape py_author expects.
        return py_author.AuthoredPython(code="pass", fidelity=__import__(
            "app.ai.strategy_author", fromlist=["Fidelity"]
        ).Fidelity(), notes="", suggested_id="s")

    monkeypatch.setattr(llm_client, "complete_structured", fake_complete_structured)
    py_author.author_python("dummy source text", provider="gemini")

    # Must be at least as high as _gemini.DEFAULT_MAX_TOKENS (i.e., 32768+).
    from app.ai import _gemini
    assert seen_max["max_tokens"] >= _gemini.DEFAULT_MAX_TOKENS, (
        f"py_author.author_python passed max_tokens={seen_max['max_tokens']}, which is "
        f"below _gemini.DEFAULT_MAX_TOKENS ({_gemini.DEFAULT_MAX_TOKENS}). This reintroduces "
        "the 8000-token cutoff bug — the user's exact reported error."
    )
    # And specifically NOT the historic 8000 that was hard-coded.
    assert seen_max["max_tokens"] != 8000


# --------------------------------------------------------------------------
# 4. Flash tier still gets fast, cheap treatment (thinking off).
# --------------------------------------------------------------------------
def test_gemini_flash_still_disables_thinking(monkeypatch):
    seen: dict = {}
    _install_fake_genai(monkeypatch, seen=seen)
    from app.ai import _gemini
    _gemini.call(model="gemini-2.5-flash", system="s", user="u", output_model=Toy)
    assert seen["config"].get("thinking_config") == {"thinking_budget": 0}


# --------------------------------------------------------------------------
# 5. The actionable truncation message still fires when a real MAX_TOKENS happens
#    (i.e., even with the higher budget, we degrade gracefully).
# --------------------------------------------------------------------------
def test_gemini_truncation_message_still_actionable_on_max_tokens(monkeypatch):
    """Even with the higher default budget, a truly gigantic input can still hit the
    limit — the error must remain user-actionable, not a cryptic JSON parse error."""
    google_mod = types.ModuleType("google")
    genai_mod = types.ModuleType("google.genai")
    errors_mod = types.ModuleType("google.genai.errors")
    errors_mod.APIError = type("APIError", (Exception,), {})

    class _Cand:
        finish_reason = "MAX_TOKENS"

    class _Resp:
        parsed = None
        text = None
        candidates = [_Cand()]

    class _Models:
        def generate_content(self, **k):
            return _Resp()

    genai_mod.Client = type(
        "Client", (), {"__init__": lambda self, *a, **k: setattr(self, "models", _Models())}
    )
    genai_mod.errors = errors_mod
    google_mod.genai = genai_mod
    monkeypatch.setitem(sys.modules, "google", google_mod)
    monkeypatch.setitem(sys.modules, "google.genai", genai_mod)
    monkeypatch.setitem(sys.modules, "google.genai.errors", errors_mod)

    from app.ai import _gemini
    with pytest.raises(RuntimeError) as ei:
        _gemini.call(model="gemini-2.5-pro", system="s", user="u", output_model=Toy)
    msg = str(ei.value)
    assert "cut off" in msg
    # The error should mention the ACTUAL token limit that was hit, not a stale one.
    assert str(_gemini.DEFAULT_MAX_TOKENS) in msg
