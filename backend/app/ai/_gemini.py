"""Gemini backend for the authoring AI. Lazy-imports google-genai inside call().

Primary path uses response_schema (Gemini parses straight to the Pydantic model). If
Gemini's schema converter rejects the nested/union schema, falls back to JSON-mode +
manual Pydantic validation, appending the model's JSON schema so Gemini emits the right
shape. The fallback is provider-internal — the caller's prompt and return type are unchanged.
"""
from __future__ import annotations

import json
from typing import Type, TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


def call(*, model: str, system: str, user: str, output_model: Type[T], max_tokens: int = 4000) -> T:
    from google import genai
    from google.genai import errors as genai_errors

    client = genai.Client()  # reads GEMINI_API_KEY / GOOGLE_API_KEY
    base = {
        "system_instruction": system,
        "response_mime_type": "application/json",
        "max_output_tokens": max_tokens,
    }

    # Primary: let Gemini parse to the Pydantic model via response_schema.
    try:
        resp = client.models.generate_content(
            model=model, contents=user, config={**base, "response_schema": output_model}
        )
        parsed = getattr(resp, "parsed", None)
        if parsed is not None:
            return parsed
        text = getattr(resp, "text", None)
        if text:
            return output_model.model_validate_json(text)
        raise RuntimeError("Gemini returned no parseable output (possibly a refusal or token limit)")
    except genai_errors.APIError as e:
        raise RuntimeError(f"Gemini API error: {getattr(e, 'message', None) or str(e)}")
    except RuntimeError:
        raise
    except Exception:
        pass  # schema-converter rejected the model -> JSON-mode fallback below

    # Fallback: JSON mode, schema described in the prompt, validate with Pydantic.
    schema_hint = (
        "\n\nReturn ONLY a JSON object matching this JSON Schema (no markdown, no commentary):\n"
        + json.dumps(output_model.model_json_schema())
    )
    try:
        resp = client.models.generate_content(
            model=model, contents=user, config={**base, "system_instruction": system + schema_hint}
        )
    except genai_errors.APIError as e:
        raise RuntimeError(f"Gemini API error: {getattr(e, 'message', None) or str(e)}")
    text = getattr(resp, "text", None)
    if not text:
        raise RuntimeError("Gemini returned no parseable output (possibly a refusal or token limit)")
    try:
        return output_model.model_validate_json(text)
    except Exception as e:
        raise RuntimeError(f"Gemini output failed validation: {e}")
