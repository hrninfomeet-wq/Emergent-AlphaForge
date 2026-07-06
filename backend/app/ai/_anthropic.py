"""Anthropic backend for the authoring AI. Lazy-imports anthropic inside call()."""
from __future__ import annotations
from typing import Type, TypeVar
from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)

# Match the Gemini backend: a truncated structured output is the #1 failure.
DEFAULT_MAX_TOKENS = 8192


def call(*, model: str, system: str, user: str, output_model: Type[T],
         max_tokens: int = DEFAULT_MAX_TOKENS) -> T:
    import anthropic  # lazy
    client = anthropic.Anthropic()
    try:
        resp = client.messages.parse(
            model=model, max_tokens=max_tokens, system=system,
            messages=[{"role": "user", "content": user}],
            output_format=output_model,
        )
    except anthropic.APIError as e:
        raise RuntimeError(f"Anthropic API error: {getattr(e, 'message', None) or str(e)}")
    if getattr(resp, "stop_reason", None) == "max_tokens":
        raise RuntimeError(
            f"The AI ({model}) response was cut off at the {max_tokens}-token limit before "
            "it finished — the strategy description is likely too long or complex to parse "
            "in one pass. Try a shorter, more focused description, then check feasibility again."
        )
    if resp.parsed_output is None:
        raise RuntimeError("AI returned no parseable output (possibly a refusal)")
    return resp.parsed_output
