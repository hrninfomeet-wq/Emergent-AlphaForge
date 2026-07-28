"""A malformed-but-complete LLM response gets ONE bounded repair attempt.

Motivated by a real failure (2026-07-29): `gemini-3.1-pro-preview` returned a
spec with **one extra closing brace** — `trailing characters at line 43`. Nothing
was truncated; the JSON was simply malformed. The whole authoring run died, and
the error told the user to "try rephrasing the rules more explicitly", which
would not have helped at all: their description was fine.

There was no retry anywhere in the authoring pipeline — every path (`spec`,
`ruleset`, `full-python`) makes exactly one call and propagates the first error.

**The taxonomy is the design.** Not every failure should be retried:

  * **Malformed / shape-mismatched output** → REPAIRABLE. Re-ask with the parser
    error attached. Cheap, usually succeeds, and the model can see its own mistake.
  * **Truncation (MAX_TOKENS)** → NOT repairable. The same budget produces the
    same cut-off; retrying burns tokens and latency to fail identically. The
    existing message ("shorten your description") is already the right advice.
  * **API errors** (auth, quota, rate limit) → NOT repairable. Retrying a
    rate-limit makes it worse, and a bad key will never fix itself.

Bounded on purpose: ONE repair attempt, so a persistently broken model costs two
calls rather than an unbounded loop.
"""
from __future__ import annotations

import os
import sys

import pytest
from pydantic import BaseModel

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from app.ai import llm_client  # noqa: E402
from app.ai.llm_client import MAX_REPAIR_ATTEMPTS, StructuredOutputError  # noqa: E402


class Out(BaseModel):
    answer: str


def _patch_backend(monkeypatch, side_effects):
    """Make the gemini backend return/raise successive side effects."""
    calls = []

    def fake_call(*, model, system, user, output_model, max_tokens):
        calls.append({"system": system, "user": user})
        eff = side_effects[min(len(calls) - 1, len(side_effects) - 1)]
        if isinstance(eff, Exception):
            raise eff
        return eff

    from app.ai import _gemini
    monkeypatch.setattr(_gemini, "call", fake_call)
    monkeypatch.setenv("AI_PROVIDER", "gemini")
    monkeypatch.setenv("GEMINI_API_KEY", "x")
    return calls


def _run():
    return llm_client.complete_structured(
        tier=llm_client.FAST, system="sys", user="describe my strategy",
        output_model=Out, provider="gemini")


# --------------------------------------------------------------------------- #
# the happy path is untouched
# --------------------------------------------------------------------------- #

def test_a_clean_response_makes_exactly_one_call(monkeypatch):
    calls = _patch_backend(monkeypatch, [Out(answer="OK")])
    assert _run().answer == "OK"
    assert len(calls) == 1, "no extra call may be made when the first succeeds"


# --------------------------------------------------------------------------- #
# malformed output IS repaired
# --------------------------------------------------------------------------- #

def test_malformed_output_is_retried_once_and_can_succeed(monkeypatch):
    """THE case that failed for real: an extra closing brace."""
    calls = _patch_backend(monkeypatch, [
        StructuredOutputError("Invalid JSON: trailing characters at line 43"),
        Out(answer="OK"),
    ])
    assert _run().answer == "OK"
    assert len(calls) == 2


def test_the_repair_attempt_tells_the_model_what_was_wrong(monkeypatch):
    """A blind re-ask is a coin flip; showing the parser error is what makes the
    second attempt actually different from the first."""
    calls = _patch_backend(monkeypatch, [
        StructuredOutputError("Invalid JSON: trailing characters at line 43"),
        Out(answer="OK"),
    ])
    _run()
    repair = calls[1]["system"]
    assert "trailing characters" in repair, "the parser error must be shown"
    assert calls[0]["system"] in repair, "the original instructions must be kept"


def test_the_user_prompt_is_never_altered(monkeypatch):
    """The user's strategy description must reach the model verbatim on retry —
    silently editing it would change what they asked for."""
    calls = _patch_backend(monkeypatch, [
        StructuredOutputError("bad json"), Out(answer="OK")])
    _run()
    assert calls[0]["user"] == calls[1]["user"] == "describe my strategy"


def test_repair_is_BOUNDED(monkeypatch):
    """A persistently malformed model must cost a fixed number of calls."""
    calls = _patch_backend(monkeypatch, [StructuredOutputError("bad json")])
    with pytest.raises(RuntimeError):
        _run()
    assert len(calls) == 1 + MAX_REPAIR_ATTEMPTS
    assert MAX_REPAIR_ATTEMPTS == 1, "one repair attempt, by design"


def test_a_persistent_failure_still_surfaces_the_real_error(monkeypatch):
    """The user must see what actually went wrong, plus the fact that it was
    retried — never a generic 'it failed'."""
    _patch_backend(monkeypatch, [StructuredOutputError("trailing characters at line 43")])
    with pytest.raises(RuntimeError) as exc:
        _run()
    msg = str(exc.value)
    assert "trailing characters" in msg
    assert "retr" in msg.lower(), "say that a repair was attempted"


# --------------------------------------------------------------------------- #
# what must NOT be retried
# --------------------------------------------------------------------------- #

def test_truncation_is_NOT_retried(monkeypatch):
    """Same token budget => same cut-off. Retrying burns tokens and latency to
    fail identically, and the existing advice (shorten the description) is right."""
    calls = _patch_backend(monkeypatch, [
        RuntimeError("The AI (gemini-3.6-flash) response was cut off at the "
                     "32768-token limit before it finished")])
    with pytest.raises(RuntimeError) as exc:
        _run()
    assert len(calls) == 1, "a truncated response must not be retried"
    assert "cut off" in str(exc.value)


def test_api_errors_are_NOT_retried(monkeypatch):
    """Retrying a rate-limit makes it worse; a bad key never fixes itself."""
    calls = _patch_backend(monkeypatch, [RuntimeError("Gemini API error: quota exceeded")])
    with pytest.raises(RuntimeError):
        _run()
    assert len(calls) == 1


def test_a_refusal_is_NOT_retried(monkeypatch):
    calls = _patch_backend(monkeypatch, [
        RuntimeError("Gemini returned no parseable output (possibly a refusal or token limit)")])
    with pytest.raises(RuntimeError):
        _run()
    assert len(calls) == 1


# --------------------------------------------------------------------------- #
# the backends must actually raise the repairable type
# --------------------------------------------------------------------------- #

def test_the_gemini_shape_mismatch_path_raises_the_repairable_type():
    import inspect
    from app.ai import _gemini
    src = inspect.getsource(_gemini)
    assert "StructuredOutputError" in src, (
        "the shape-mismatch branch must raise the repairable type, or the retry "
        "can never trigger in production")


def test_the_misleading_rephrase_advice_is_gone():
    """'Try rephrasing the rules more explicitly' was actively wrong for a
    trailing-brace error — the description was fine."""
    import inspect
    from app.ai import _gemini
    assert "Try rephrasing the rules more explicitly" not in inspect.getsource(_gemini)
