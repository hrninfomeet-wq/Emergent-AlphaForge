"""Gemini backend for the authoring AI. Lazy-imports google-genai inside call().

Primary path uses response_schema (Gemini parses straight to the Pydantic model). If
Gemini's schema converter rejects the nested/union schema, falls back to JSON-mode +
manual Pydantic validation, appending the model's JSON schema so Gemini emits the right
shape. The fallback is provider-internal — the caller's prompt and return type are unchanged.

Truncation guard: gemini-2.5 models are "thinking" models whose thinking tokens are
drawn from max_output_tokens; a modest budget can be consumed by thinking, leaving the
JSON output cut off mid-string (a raw pydantic "EOF while parsing" error). We (a) give a
generous default budget, (b) disable thinking on the flash tier where it's allowed, and
(c) detect a MAX_TOKENS finish and raise a CLEAR, actionable message instead of a cryptic
JSON-parse error.
"""
from __future__ import annotations

import json
import logging
from typing import Type, TypeVar

from pydantic import BaseModel

_log = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

# A truncated structured output is the #1 real-world failure — give plenty of room.
DEFAULT_MAX_TOKENS = 8192


def _base_config(system: str, model: str, max_tokens: int) -> dict:
    cfg = {
        "system_instruction": system,
        "response_mime_type": "application/json",
        "max_output_tokens": max_tokens,
    }
    # gemini-2.5-flash lets you spend the whole budget on the answer (thinking off);
    # 2.5-pro requires some thinking, so only disable it on flash. Best-effort: if the
    # installed SDK/model rejects the key, the primary try/except falls back cleanly.
    if "flash" in model:
        cfg["thinking_config"] = {"thinking_budget": 0}
    return cfg


def _finish_reason(resp) -> str:
    try:
        fr = resp.candidates[0].finish_reason
        return getattr(fr, "name", None) or str(fr)
    except Exception:
        return ""


def _truncated_msg(model: str, max_tokens: int) -> str:
    return (
        f"The AI ({model}) response was cut off at the {max_tokens}-token limit before it "
        "finished — the strategy description is likely too long or too complex to parse in "
        "one pass. Try a shorter, more focused description (the core entry/exit rules), or "
        "split it into fewer rules, then check feasibility again."
    )


def call(*, model: str, system: str, user: str, output_model: Type[T],
         max_tokens: int = DEFAULT_MAX_TOKENS) -> T:
    from google import genai
    from google.genai import errors as genai_errors

    client = genai.Client()  # reads GEMINI_API_KEY / GOOGLE_API_KEY
    base = _base_config(system, model, max_tokens)

    # Primary: let Gemini parse to the Pydantic model via response_schema.
    try:
        resp = client.models.generate_content(
            model=model, contents=user, config={**base, "response_schema": output_model}
        )
        parsed = getattr(resp, "parsed", None)
        if parsed is not None:
            return parsed
        if _finish_reason(resp) == "MAX_TOKENS":
            raise RuntimeError(_truncated_msg(model, max_tokens))
        text = getattr(resp, "text", None)
        if text:
            return output_model.model_validate_json(text)
        raise RuntimeError("Gemini returned no parseable output (possibly a refusal or token limit)")
    except genai_errors.APIError as e:
        raise RuntimeError(f"Gemini API error: {getattr(e, 'message', None) or str(e)}")
    except RuntimeError:
        raise
    except Exception as e:
        # schema-converter (or a malformed primary parse) rejected the model -> JSON-mode
        # fallback below. Log a breadcrumb so a real schema problem is diagnosable in prod.
        _log.info("Gemini response_schema path failed (%s); falling back to JSON mode", e)

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
    except Exception as e:
        raise RuntimeError(f"Gemini fallback request failed: {e}")
    if _finish_reason(resp) == "MAX_TOKENS":
        raise RuntimeError(_truncated_msg(model, max_tokens))
    text = getattr(resp, "text", None)
    if not text:
        raise RuntimeError("Gemini returned no parseable output (possibly a refusal or token limit)")
    try:
        return output_model.model_validate_json(text)
    except Exception as e:
        # Distinguish a genuine cut-off (unbalanced/looks-truncated JSON) from a real
        # shape mismatch, so the user gets an actionable message either way.
        stripped = text.rstrip()
        looks_truncated = not stripped.endswith("}") or stripped.count("{") > stripped.count("}")
        if looks_truncated:
            raise RuntimeError(_truncated_msg(model, max_tokens))
        raise RuntimeError(
            "The AI returned JSON that didn't match the expected strategy shape "
            f"({str(e)[:200]}). Try rephrasing the rules more explicitly."
        )
