"""Provider-pluggable structured-output client for the authoring AI.

Callers ask for an abstract TIER (FAST | POWERFUL); the active provider resolves it
to a concrete model. Provider SDKs are imported lazily inside the per-provider
backends (app.ai._anthropic / app.ai._gemini), so this module imports host-side
without anthropic or google-genai installed. Tests patch the seams.
"""
from __future__ import annotations

import os
from typing import Optional, Type, TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)

# Abstract tiers — callers use these, never a model string.
FAST = "fast"
POWERFUL = "powerful"

# Anthropic model ids (named constants for readability).
SONNET = "claude-sonnet-4-6"
OPUS = "claude-opus-4-8"

# Per-provider tier -> default model. Each id is overridable via env (see _ENV_OVERRIDE).
_MODELS = {
    "anthropic": {FAST: SONNET, POWERFUL: OPUS},
    "gemini": {FAST: "gemini-2.5-flash", POWERFUL: "gemini-2.5-pro"},
}
_ENV_OVERRIDE = {
    ("anthropic", FAST): "ANTHROPIC_FAST_MODEL",
    ("anthropic", POWERFUL): "ANTHROPIC_POWERFUL_MODEL",
    ("gemini", FAST): "GEMINI_FAST_MODEL",
    ("gemini", POWERFUL): "GEMINI_POWERFUL_MODEL",
}
_KEY_ENV = {"anthropic": "ANTHROPIC_API_KEY", "gemini": "GEMINI_API_KEY"}
_LABELS = {"anthropic": "Anthropic Claude", "gemini": "Google Gemini"}
# Preference order when no explicit arg and no AI_PROVIDER (anthropic-first = legacy behavior).
_PREFERENCE = ("anthropic", "gemini")


def provider_configured(provider: str) -> bool:
    return bool(os.environ.get(_KEY_ENV.get(provider, ""), "").strip())


def any_configured() -> bool:
    return any(provider_configured(p) for p in _MODELS)


def model_for(provider: str, tier: str) -> str:
    if provider not in _MODELS:
        raise RuntimeError(f"unknown AI provider '{provider}'")
    if tier not in _MODELS[provider]:
        raise RuntimeError(f"unknown tier '{tier}'")
    override = os.environ.get(_ENV_OVERRIDE[(provider, tier)], "").strip()
    return override or _MODELS[provider][tier]


def resolve_provider(explicit: Optional[str] = None) -> str:
    candidate = (explicit or os.environ.get("AI_PROVIDER") or "").strip().lower() or None
    if candidate:
        if candidate not in _MODELS:
            raise RuntimeError(f"unknown AI provider '{candidate}'")
        if not provider_configured(candidate):
            raise RuntimeError(f"AI provider '{candidate}' selected but its API key is not set")
        return candidate
    for p in _PREFERENCE:
        if provider_configured(p):
            return p
    raise RuntimeError("no AI provider configured — set GEMINI_API_KEY or ANTHROPIC_API_KEY in backend/.env")


def providers_status() -> dict:
    providers = [
        {"id": p, "label": _LABELS[p], "configured": provider_configured(p)}
        for p in _MODELS
    ]
    try:
        active = resolve_provider(None)
    except RuntimeError:
        active = None
    return {"providers": providers, "active": active}


def complete_structured(
    *,
    tier: str,
    system: str,
    user: str,
    output_model: Type[T],
    provider: Optional[str] = None,
    max_tokens: int = 32768,
) -> T:
    """Resolve provider + tier -> model, dispatch to that backend, return a validated
    `output_model`. Backends raise RuntimeError on any provider error (clean 502 upstream).

    max_tokens defaults to the highest per-backend default (Gemini's 32,768): a lower
    wrapper default silently OVERRODE the per-backend default and re-introduced the exact
    Gemini output truncation this budget increase was meant to solve — authored RuleSets
    / Python were cut off mid-JSON on every non-overriding caller (audit S1). If we ever
    bump _gemini.DEFAULT_MAX_TOKENS or _anthropic.DEFAULT_MAX_TOKENS higher, bump this too
    (there's a host-safe test — test_gemini_token_budget.py — that pins the invariant)."""
    prov = resolve_provider(provider)
    model = model_for(prov, tier)
    if prov == "anthropic":
        from app.ai import _anthropic
        return _anthropic.call(model=model, system=system, user=user,
                               output_model=output_model, max_tokens=max_tokens)
    from app.ai import _gemini
    return _gemini.call(model=model, system=system, user=user,
                        output_model=output_model, max_tokens=max_tokens)
