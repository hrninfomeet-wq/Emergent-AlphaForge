"""Thin Anthropic client for the authoring AI. The anthropic import is LAZY so the
module imports host-side without the package; the actual call is a patchable seam."""
from __future__ import annotations

import os
from typing import Type, TypeVar

from pydantic import BaseModel

SONNET = "claude-sonnet-4-6"
OPUS = "claude-opus-4-8"
T = TypeVar("T", bound=BaseModel)


def is_configured() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


def complete_structured(
    *,
    model: str,
    system: str,
    user: str,
    output_model: Type[T],
    max_tokens: int = 4000,
) -> T:
    """Call Claude with structured output; return a validated `output_model` instance.

    Lazy-imports anthropic. Raises RuntimeError if no API key is configured."""
    if not is_configured():
        raise RuntimeError("ANTHROPIC_API_KEY is not configured")
    import anthropic  # lazy

    client = anthropic.Anthropic()
    try:
        resp = client.messages.parse(
            model=model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
            output_format=output_model,
        )
    except anthropic.APIError as e:
        # Surface billing / rate-limit / request errors as a clean RuntimeError so
        # callers return a readable message (e.g. "credit balance too low") instead
        # of a 500. `.message` exists on APIStatusError subclasses; fall back to str.
        raise RuntimeError(f"Anthropic API error: {getattr(e, 'message', None) or str(e)}")
    if resp.parsed_output is None:
        raise RuntimeError("AI returned no parseable output (possibly a refusal)")
    return resp.parsed_output
