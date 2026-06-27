# Multi-provider AI Authoring (Part 1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the strategy-authoring AI provider-pluggable (Anthropic *or* Gemini), default to Gemini, with a provider dropdown in the wizard — Spec mode only.

**Architecture:** `llm_client` becomes a tier-based router (`FAST`/`POWERFUL`) over two lazy-importing backends (`_anthropic`, `_gemini`). Callers pass an abstract tier; the active provider (explicit arg → `AI_PROVIDER` env → single-configured) resolves it to a model. `POWERFUL` is plumbed but unused in Part 1. A `/strategies/author/providers` endpoint drives a provider `<select>` in the wizard.

**Tech Stack:** FastAPI, Pydantic, `anthropic`, `google-genai` (new), React/CRA. Host tests mock the provider seams (no SDK, no motor).

**Conventions:**
- Worktree root: `C:\Users\haroo\af-wt-strategy-library` (branch `feat/strategy-authoring`).
- Run host tests from the worktree root with the main repo's venv:
  `PY="/c/Users/haroo/OneDrive/Documents/New project/Emergent-AlphaForge/.venv/Scripts/python.exe"`
  then `"$PY" -m pytest tests/<file> -q`.
- The venv has pytest/pydantic/fastapi but **not** motor/anthropic/google-genai — keep all SDK imports lazy.

---

### Task 1: Tier router + provider resolution in `llm_client` (extract `_anthropic`)

**Files:**
- Create: `backend/app/ai/_anthropic.py`
- Modify: `backend/app/ai/llm_client.py` (full rewrite)
- Test: `tests/test_llm_client.py` (new)

- [ ] **Step 1: Write the failing test**

Create `tests/test_llm_client.py`:

```python
"""Provider router unit tests — no SDK, no network. Mocks the per-provider seams."""
import sys
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.ai import llm_client


def _clear(monkeypatch):
    for v in ("ANTHROPIC_API_KEY", "GEMINI_API_KEY", "AI_PROVIDER",
              "ANTHROPIC_FAST_MODEL", "ANTHROPIC_POWERFUL_MODEL",
              "GEMINI_FAST_MODEL", "GEMINI_POWERFUL_MODEL"):
        monkeypatch.delenv(v, raising=False)


def test_model_for_defaults_and_env_override(monkeypatch):
    _clear(monkeypatch)
    assert llm_client.model_for("anthropic", llm_client.FAST) == "claude-sonnet-4-6"
    assert llm_client.model_for("gemini", llm_client.POWERFUL) == "gemini-2.5-pro"
    monkeypatch.setenv("GEMINI_FAST_MODEL", "gemini-3.0-flash")
    assert llm_client.model_for("gemini", llm_client.FAST) == "gemini-3.0-flash"


def test_resolve_explicit_wins_when_configured(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("GEMINI_API_KEY", "g")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "a")
    monkeypatch.setenv("AI_PROVIDER", "gemini")
    assert llm_client.resolve_provider("anthropic") == "anthropic"  # explicit beats env


def test_resolve_env_then_single_then_raise(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "a")  # only anthropic
    assert llm_client.resolve_provider(None) == "anthropic"  # single configured
    monkeypatch.setenv("GEMINI_API_KEY", "g")
    monkeypatch.setenv("AI_PROVIDER", "gemini")
    assert llm_client.resolve_provider(None) == "gemini"      # env wins
    _clear(monkeypatch)
    with pytest.raises(RuntimeError):
        llm_client.resolve_provider(None)                     # none configured


def test_resolve_selected_but_unconfigured_raises(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("GEMINI_API_KEY", "g")
    with pytest.raises(RuntimeError):
        llm_client.resolve_provider("anthropic")              # named, no key


def test_providers_status_shape(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("GEMINI_API_KEY", "g")
    monkeypatch.setenv("AI_PROVIDER", "gemini")
    st = llm_client.providers_status()
    ids = {p["id"]: p for p in st["providers"]}
    assert ids["gemini"]["configured"] is True
    assert ids["anthropic"]["configured"] is False
    assert ids["gemini"]["label"] == "Google Gemini"
    assert st["active"] == "gemini"
    _clear(monkeypatch)
    assert llm_client.providers_status()["active"] is None


def test_complete_structured_routes_to_resolved_backend(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("GEMINI_API_KEY", "g")
    monkeypatch.setenv("AI_PROVIDER", "gemini")
    seen = {}
    from app.ai import _gemini
    def fake_call(*, model, system, user, output_model, max_tokens):
        seen.update(model=model, system=system); return "OK"
    monkeypatch.setattr(_gemini, "call", fake_call)
    out = llm_client.complete_structured(tier=llm_client.FAST, system="s", user="u", output_model=str)
    assert out == "OK"
    assert seen["model"] == "gemini-2.5-flash"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `"$PY" -m pytest tests/test_llm_client.py -q`
Expected: FAIL (e.g. `AttributeError: module 'app.ai.llm_client' has no attribute 'model_for'`).

- [ ] **Step 3: Create `backend/app/ai/_anthropic.py`**

```python
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
```

- [ ] **Step 4: Rewrite `backend/app/ai/llm_client.py`**

```python
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


def is_configured() -> bool:
    """Back-compat alias — True if ANY provider is configured."""
    return any_configured()


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
    max_tokens: int = 4000,
) -> T:
    """Resolve provider + tier -> model, dispatch to that backend, return a validated
    `output_model`. Backends raise RuntimeError on any provider error (clean 502 upstream)."""
    prov = resolve_provider(provider)
    model = model_for(prov, tier)
    if prov == "anthropic":
        from app.ai import _anthropic
        return _anthropic.call(model=model, system=system, user=user,
                               output_model=output_model, max_tokens=max_tokens)
    from app.ai import _gemini
    return _gemini.call(model=model, system=system, user=user,
                        output_model=output_model, max_tokens=max_tokens)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `"$PY" -m pytest tests/test_llm_client.py -q`
Expected: PASS (6 tests).

- [ ] **Step 6: Commit**

```bash
git add backend/app/ai/_anthropic.py backend/app/ai/llm_client.py tests/test_llm_client.py
git commit -m "feat(ai): tier-based provider router + anthropic backend extraction"
```

---

### Task 2: Gemini backend (`_gemini`) with fallback

**Files:**
- Create: `backend/app/ai/_gemini.py`
- Test: `tests/test_gemini_backend.py` (new)

- [ ] **Step 1: Write the failing test**

Create `tests/test_gemini_backend.py`:

```python
"""_gemini.call against a FAKE google.genai (no real SDK / network)."""
import sys, types
from pathlib import Path
import pytest
from pydantic import BaseModel

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))


class Toy(BaseModel):
    x: int


def _install_fake_genai(monkeypatch, *, parsed=None, text=None, raise_api=False, raise_convert=False):
    google_mod = types.ModuleType("google")
    genai_mod = types.ModuleType("google.genai")
    errors_mod = types.ModuleType("google.genai.errors")

    class APIError(Exception):
        def __init__(self, message="boom"):
            super().__init__(message); self.message = message
    errors_mod.APIError = APIError

    class _Resp:
        def __init__(self, parsed, text): self.parsed = parsed; self.text = text

    class _Models:
        def generate_content(self, *, model, contents, config):
            if raise_convert and "response_schema" in config:
                raise ValueError("schema conversion failed")  # client-side converter error
            if raise_api:
                raise APIError("rate limit exceeded")
            # fallback call (no response_schema) returns text only
            if "response_schema" not in config:
                return _Resp(None, text)
            return _Resp(parsed, text)

    class Client:
        def __init__(self, *a, **k): self.models = _Models()

    genai_mod.Client = Client
    genai_mod.errors = errors_mod
    google_mod.genai = genai_mod
    monkeypatch.setitem(sys.modules, "google", google_mod)
    monkeypatch.setitem(sys.modules, "google.genai", genai_mod)
    monkeypatch.setitem(sys.modules, "google.genai.errors", errors_mod)


def test_gemini_success_returns_parsed(monkeypatch):
    _install_fake_genai(monkeypatch, parsed=Toy(x=7))
    from app.ai import _gemini
    out = _gemini.call(model="gemini-2.5-flash", system="s", user="u", output_model=Toy)
    assert out.x == 7


def test_gemini_api_error_becomes_runtimeerror(monkeypatch):
    _install_fake_genai(monkeypatch, raise_api=True)
    from app.ai import _gemini
    with pytest.raises(RuntimeError) as ei:
        _gemini.call(model="m", system="s", user="u", output_model=Toy)
    assert "Gemini API error" in str(ei.value)


def test_gemini_schema_convert_failure_falls_back_to_json(monkeypatch):
    # response_schema path raises a converter error; fallback uses .text + model_validate_json
    _install_fake_genai(monkeypatch, raise_convert=True, text='{"x": 5}')
    from app.ai import _gemini
    out = _gemini.call(model="m", system="s", user="u", output_model=Toy)
    assert out.x == 5
```

- [ ] **Step 2: Run test to verify it fails**

Run: `"$PY" -m pytest tests/test_gemini_backend.py -q`
Expected: FAIL (`ModuleNotFoundError: No module named 'app.ai._gemini'`).

- [ ] **Step 3: Create `backend/app/ai/_gemini.py`**

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `"$PY" -m pytest tests/test_gemini_backend.py -q`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add backend/app/ai/_gemini.py tests/test_gemini_backend.py
git commit -m "feat(ai): gemini backend (google-genai) with JSON-mode fallback"
```

---

### Task 3: `strategy_author` forwards the provider

**Files:**
- Modify: `backend/app/ai/strategy_author.py:101-120` (`map_source_to_spec`)
- Test: `tests/test_strategy_author.py` (modify)

- [ ] **Step 1: Update the canned-seam test + add a provider-forwarding test**

In `tests/test_strategy_author.py`, replace `test_complete_structured_requires_api_key` and
`test_is_configured_true_with_key` with provider-aware versions, and add a forwarding test.
Add at top: `import pytest` (already importing pytest inside the old test — make it module-level).

```python
def test_complete_structured_raises_when_no_provider(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("AI_PROVIDER", raising=False)
    assert llm_client.any_configured() is False
    import pytest
    with pytest.raises(RuntimeError):
        llm_client.complete_structured(tier=llm_client.FAST, system="s", user="u", output_model=MappedSpec)


def test_any_configured_true_with_either_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("GEMINI_API_KEY", "g")
    assert llm_client.any_configured() is True


def test_map_source_forwards_provider(monkeypatch):
    captured = {}
    def fake(*, tier, system, user, output_model, provider=None, max_tokens=4000):
        captured.update(tier=tier, provider=provider); return _CANNED
    monkeypatch.setattr(llm_client, "complete_structured", fake)
    map_source_to_spec("buy calls above ema9", provider="gemini")
    assert captured["provider"] == "gemini"
    assert captured["tier"] == llm_client.FAST
```

- [ ] **Step 2: Run to verify it fails**

Run: `"$PY" -m pytest tests/test_strategy_author.py -q`
Expected: FAIL (`map_source_to_spec()` takes 1 positional arg / `complete_structured` got unexpected kwarg `tier`).

- [ ] **Step 3: Update `map_source_to_spec`**

In `backend/app/ai/strategy_author.py`, change the signature and the call:

```python
def map_source_to_spec(source_text: str, provider: str | None = None) -> Dict[str, Any]:
    """Fast tier maps the text to {spec, fidelity}. Returns plain dicts. The grounding
    catalog + validate keep the AI honest. `provider` overrides AI_PROVIDER when set."""
    from app.ai.grounding import build_grounding_catalog
    from app.ai.compiler import validate_spec
    from app.ai import llm_client

    catalog = build_grounding_catalog()
    mapped: MappedSpec = llm_client.complete_structured(
        tier=llm_client.FAST,
        system=_system_prompt(catalog),
        user=source_text,
        output_model=MappedSpec,
        provider=provider,
    )
    errors = validate_spec(mapped.spec)
    return {
        "spec": mapped.spec.model_dump(),
        "fidelity": mapped.fidelity.model_dump(),
        "errors": errors,
    }
```

- [ ] **Step 4: Run to verify it passes**

Run: `"$PY" -m pytest tests/test_strategy_author.py -q`
Expected: PASS (all tests in the file, including the existing `test_from_source_*` that patch
`is_configured` — `is_configured` still exists as an alias, so they keep passing).

- [ ] **Step 5: Commit**

```bash
git add backend/app/ai/strategy_author.py tests/test_strategy_author.py
git commit -m "feat(ai): map_source_to_spec forwards provider, uses FAST tier"
```

---

### Task 4: Schema + router (`provider` field, `any_configured` gate, `/providers`, 400 on unconfigured)

**Files:**
- Modify: `backend/app/schemas.py:418-419` (`StrategyFromSourceReq`)
- Modify: `backend/app/routers/strategies_admin.py:248-271` (`/from-source`) + add `/providers`
- Test: `tests/test_strategy_authoring_routes.py` (modify) and `tests/test_strategy_author.py` (modify the two `from_source` patches)

- [ ] **Step 1: Write the failing tests**

In `tests/test_strategy_authoring_routes.py` add:

```python
def test_providers_endpoint_returns_status(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    import app.routers.strategies_admin as sa
    from app.ai import llm_client
    monkeypatch.setattr(llm_client, "providers_status",
                        lambda: {"providers": [{"id": "gemini", "label": "Google Gemini", "configured": True}],
                                 "active": "gemini"})
    app = FastAPI(); app.include_router(sa.api)
    r = TestClient(app).get("/strategies/author/providers")
    assert r.status_code == 200
    assert r.json()["active"] == "gemini"


def test_from_source_400_when_named_provider_unconfigured(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from unittest.mock import patch
    import app.routers.strategies_admin as sa
    app = FastAPI(); app.include_router(sa.api)
    with patch("app.ai.llm_client.any_configured", return_value=True), \
         patch("app.ai.llm_client.resolve_provider", side_effect=RuntimeError("anthropic selected but its API key is not set")):
        r = TestClient(app).post("/strategies/author/from-source",
                                 json={"source": "buy calls", "provider": "anthropic"})
    assert r.status_code == 400, r.text
    assert "api key is not set" in r.json()["detail"].lower()
```

Update the existing `test_from_source_*` (in BOTH `tests/test_strategy_author.py` and
`tests/test_strategy_authoring_routes.py`) that patch `app.ai.llm_client.is_configured` →
patch `app.ai.llm_client.any_configured` instead (the router now gates on `any_configured`).
Example replacement (apply to each occurrence):

```python
    with patch("app.ai.llm_client.any_configured", return_value=True), \
         patch("app.ai.strategy_author.map_source_to_spec", return_value=canned):
```
and the 503 test:
```python
    with patch("app.ai.llm_client.any_configured", return_value=False):
```

- [ ] **Step 2: Run to verify it fails**

Run: `"$PY" -m pytest tests/test_strategy_authoring_routes.py tests/test_strategy_author.py -q`
Expected: FAIL (no `/providers` route → 404; the 400 test → currently 502/200).

- [ ] **Step 3: Update the schema**

In `backend/app/schemas.py`, ensure `Optional` is imported (top of file: `from typing import ... Optional`),
then:

```python
class StrategyFromSourceReq(BaseModel):
    source: str                       # pasted text/transcript OR a YouTube URL
    provider: Optional[str] = None    # "anthropic" | "gemini"; None -> AI_PROVIDER / default
```

- [ ] **Step 4: Update the router**

In `backend/app/routers/strategies_admin.py`, add the providers route (near the other author
routes) and update `/from-source`:

```python
@api.get("/strategies/author/providers")
async def author_providers():
    """Configured AI providers + the active default. Host-safe (env only)."""
    from app.ai import llm_client
    return llm_client.providers_status()
```

Replace the body of `author_from_source` with:

```python
@api.post("/strategies/author/from-source")
async def author_from_source(req: StrategyFromSourceReq):
    """Ingest pasted text or a YouTube link, then map to a constrained StrategySpec +
    fidelity via the FAST tier of the selected provider (or the configured default)."""
    from app.ai import llm_client
    from app.ai.source_ingest import ingest_source
    from app.ai.strategy_author import map_source_to_spec
    if not llm_client.any_configured():
        raise HTTPException(503, "AI authoring is not configured — set GEMINI_API_KEY or ANTHROPIC_API_KEY in backend/.env")
    if not (req.source or "").strip():
        raise HTTPException(400, "source is empty")
    if req.provider:
        try:
            llm_client.resolve_provider(req.provider)   # 400 if named provider lacks a key
        except RuntimeError as e:
            raise HTTPException(400, str(e))
    try:
        ing = ingest_source(req.source)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except RuntimeError as e:
        raise HTTPException(502, f"Transcript fetch failed: {e}")
    try:
        out = map_source_to_spec(ing["text"], provider=req.provider)
    except RuntimeError as e:
        raise HTTPException(502, f"AI mapping failed: {e}")
    out["source_kind"] = ing["kind"]
    return out
```

- [ ] **Step 5: Run to verify it passes**

Run: `"$PY" -m pytest tests/test_strategy_authoring_routes.py tests/test_strategy_author.py -q`
Expected: PASS (all).

- [ ] **Step 6: Commit**

```bash
git add backend/app/schemas.py backend/app/routers/strategies_admin.py \
        tests/test_strategy_authoring_routes.py tests/test_strategy_author.py
git commit -m "feat(ai): /providers endpoint + provider field on /from-source (any_configured gate, 400 on unconfigured)"
```

---

### Task 5: Add `google-genai` to requirements

**Files:**
- Modify: `backend/requirements.txt`

- [ ] **Step 1: Append the dependency**

Add after the `anthropic` / `youtube-transcript-api` lines:

```
google-genai>=1.0,<2.0
```

- [ ] **Step 2: Commit**

```bash
git add backend/requirements.txt
git commit -m "build(ai): add google-genai dependency"
```

(The container installs it at the §Task 8 rebuild; host tests never import it.)

---

### Task 6: Frontend — provider dropdown in the wizard

**Files:**
- Modify: `frontend/src/lib/api.js:32-37`
- Modify: `frontend/src/components/strategy/AuthoringWizard.jsx`

- [ ] **Step 1: Add API methods**

In `frontend/src/lib/api.js`, alongside the other author methods:

```javascript
  getAuthorProviders: () => apiClient.get("/strategies/author/providers").then((r) => r.data),
  authorFromSource: (source, provider) =>
    apiClient.post("/strategies/author/from-source", { source, provider }, { timeout: LONG_TIMEOUT_MS }).then((r) => r.data),
```

(Replace the existing one-arg `authorFromSource`.)

- [ ] **Step 2: Wire provider state into the wizard**

In `frontend/src/components/strategy/AuthoringWizard.jsx`:

Add state near the other AI-section state:
```javascript
  const [providers, setProviders] = useState([]);          // [{id,label,configured}]
  const [provider, setProvider] = useState("");            // selected id
```

Extend the open-effect to also fetch providers (in the same `useEffect(..., [open])`):
```javascript
        const prov = await api.getAuthorProviders();
        if (!cancelled) {
          setProviders(prov.providers || []);
          setProvider(prov.active || (prov.providers || []).find((p) => p.configured)?.id || "");
        }
```
(Wrap in its own try/catch so a providers failure doesn't blank the catalog; on failure leave
`providers` empty.)

Derive a configured list and use it to gate Generate:
```javascript
  const configuredProviders = providers.filter((p) => p.configured);
  const aiReady = configuredProviders.length > 0;
```

In the ✨ box, above the textarea, render the selector (only when ≥1 configured):
```jsx
          {aiReady ? (
            <div className="flex items-center gap-2">
              <label className={labelCls + " mb-0"}>Provider</label>
              <select
                value={provider}
                onChange={(e) => setProvider(e.target.value)}
                className={inputCls + " w-44"}
                data-testid="author-ai-provider"
              >
                {configuredProviders.map((p) => (
                  <option key={p.id} value={p.id}>{p.label}</option>
                ))}
              </select>
            </div>
          ) : (
            <div className="text-[11px] text-amber-300">
              No AI provider configured — set GEMINI_API_KEY or ANTHROPIC_API_KEY in backend/.env.
            </div>
          )}
```

Pass the provider through and disable Generate when not ready:
```javascript
      const res = await api.authorFromSource(aiSource, provider || undefined);
```
```jsx
              disabled={aiBusy || !aiSource.trim() || !aiReady}
```

- [ ] **Step 3: Build the frontend to verify it compiles**

Run (from worktree): `cd frontend && npx --no-install craco build`
Expected: "Compiled successfully" (warnings OK). If `node_modules` is a junction needed for
Docker, recreate per project notes after building.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/lib/api.js frontend/src/components/strategy/AuthoringWizard.jsx
git commit -m "feat(strategy-library): provider dropdown in authoring wizard"
```

---

### Task 7: Full host-suite regression

- [ ] **Step 1: Run the whole suite**

Run (from worktree): `"$PY" -m pytest tests -q`
Expected: all pass (prior count + the new `test_llm_client.py` (6) and `test_gemini_backend.py` (3)).

- [ ] **Step 2: If green, commit any incidental fixups**

```bash
git add -A && git commit -m "test(ai): multi-provider suite green" || echo "nothing to commit"
```

---

### Task 8: Live validation on Gemini (the unlock) + finalize the Gemini path

**Pre-req:** both `backend/.env` files already have `GEMINI_API_KEY` + `AI_PROVIDER=gemini` (verified, HTTP 200).

- [ ] **Step 1: Rebuild the running Docker backend (worktree) with the new deps/code**

Per project notes, worktree backend rebuilds need `--no-cache`; remove the `node_modules`
junction before any frontend Docker build (`cmd /c rmdir frontend\node_modules`).
```bash
cd /c/Users/haroo/af-wt-strategy-library
docker compose build --no-cache backend && docker compose up -d backend
```

- [ ] **Step 2: Real Gemini round-trip of the actual MappedSpec schema (decides primary vs fallback)**

```bash
curl -s -X POST http://localhost:8001/api/strategies/author/from-source \
  -H "Content-Type: application/json" \
  -d '{"source":"Buy a call when price closes above the 9 EMA and RSI is above 55. Target 30 spot points, stop 15. Skip choppy markets.","provider":"gemini"}'
```
Expected: HTTP 200 JSON with `spec.id`, `entry_ce`, `fidelity.captured`. If you see
`Gemini API error` or a validation failure, inspect `docker compose logs backend` — the
fallback should already have engaged; if the schema path is the problem, confirm the fallback
returned a valid spec. Record which path succeeded in the spec's §3.4.

- [ ] **Step 3: YouTube → spec via Gemini**

```bash
curl -s -X POST http://localhost:8001/api/strategies/author/from-source \
  -H "Content-Type: application/json" \
  -d '{"source":"https://www.youtube.com/watch?v=<a short trading-strategy video>","provider":"gemini"}'
```
Expected: HTTP 200, `source_kind":"youtube"`, a populated spec. (A transcript-disabled video
returns 502 "Transcript fetch failed" — try another link; that path is already covered by host tests.)

- [ ] **Step 4: Chrome — dropdown + end-to-end**

Open `http://localhost:3000` → Strategy Library → `＋ New strategy`. Confirm:
- the ✨ box shows a **Provider** dropdown listing **Google Gemini** (default), Anthropic only if its key is set;
- paste the Step-2 text, click **Generate with AI** → the form fills, fidelity readback shows;
- **Preview code** compiles; no console errors.

- [ ] **Step 5: If the Gemini schema path needed adjustment, update `_gemini.py` + re-run Task 2 + Task 7**

Make the minimal change indicated by Step 2, re-run `"$PY" -m pytest tests/test_gemini_backend.py tests -q`, rebuild, re-verify Step 2. Commit:
```bash
git add backend/app/ai/_gemini.py && git commit -m "fix(ai): finalize gemini structured-output path per live round-trip"
```

---

### Task 9: Rebase onto main + open the merge PR

- [ ] **Step 1: Rebase the branch onto current origin/main**

```bash
cd /c/Users/haroo/af-wt-strategy-library
git fetch origin
git rebase origin/main
```
Expected: AI-authoring files are disjoint from the live-execution/OCO work on main → minimal/no
conflicts. Resolve any that arise (favor keeping both sides' intent), then continue the rebase.

- [ ] **Step 2: Re-run the host suite after rebase**

Run: `"$PY" -m pytest tests -q`  → all pass.

- [ ] **Step 3: Push + open the PR (whole AI-authoring stack: Phase 2B + Part 1)**

```bash
git push -u origin feat/strategy-authoring --force-with-lease
gh pr create --base main --head feat/strategy-authoring \
  --title "AI strategy authoring: multi-provider (Anthropic + Gemini), Spec mode" \
  --body "Phase 2B (text/YouTube -> StrategySpec + fidelity) + Part 1 (pluggable provider, Gemini default, provider dropdown). Live-validated on Gemini. Part 2 (full-Python escape hatch + mode toggle) is a separate spec."
```

- [ ] **Step 4: Hand off to the user to review/merge the PR.**

---

## Self-Review

**Spec coverage:**
- §3.1 tiers + env overrides → Task 1 (`model_for`, constants) ✓
- §3.2 router + backends → Task 1 (`_anthropic`, dispatcher) + Task 2 (`_gemini`) ✓
- §3.3 resolution order + `providers_status` → Task 1 ✓
- §3.4 Gemini call + fallback → Task 2 + Task 8 Step 2/5 (live finalize) ✓
- §3.5 host-safety (lazy, seams) → backends lazy-import; tests mock; Task 7 runs on host venv ✓
- §4 API (`provider` field, `any_configured` gate, `/providers`, 400) → Task 4 ✓
- §5 frontend dropdown → Task 6 ✓
- §6 config/env + `google-genai` → `.env` done (pre-verified); requirements Task 5 ✓
- §7 tests + live validation → Tasks 1-4 (host), Task 8 (live) ✓
- §9 rollout (rebase + PR) → Task 9 ✓

**Placeholder scan:** Step 3 of Task 8 has a `<a short trading-strategy video>` URL placeholder —
intentional (the human picks a real link at run time); all code blocks are complete.

**Type consistency:** `complete_structured(tier=, provider=)`, `model_for(provider, tier)`,
`resolve_provider`, `any_configured`, `providers_status` used identically across Tasks 1/3/4/6 and
all tests. `_gemini.call` / `_anthropic.call` share the same kwargs. `provider` field on
`StrategyFromSourceReq` matches the router + `map_source_to_spec(provider=)` + api.js arg.
