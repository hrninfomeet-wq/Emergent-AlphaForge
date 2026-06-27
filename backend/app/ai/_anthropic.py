"""Anthropic backend for the authoring AI. Lazy-imports anthropic inside call()."""
from __future__ import annotations
from typing import Type, TypeVar
from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


def call(*, model: str, system: str, user: str, output_model: Type[T], max_tokens: int = 4000) -> T:
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
    if resp.parsed_output is None:
        raise RuntimeError("AI returned no parseable output (possibly a refusal)")
    return resp.parsed_output
