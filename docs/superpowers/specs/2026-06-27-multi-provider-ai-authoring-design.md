# Multi-provider AI strategy authoring — Part 1: Anthropic + Gemini (Spec mode)

**Date:** 2026-06-27
**Status:** Approved design → ready for plan
**Branch:** built on `feat/strategy-authoring` (worktree `af-wt-strategy-library`), on top of the Phase 2B AI-mapper commits.

## 1. Context & motivation

The strategy-authoring AI layer (Phase 2B) maps a free-text description or a YouTube
transcript into a constrained `StrategySpec` + a fidelity readback. It is built, host-tested,
and pushed on `feat/strategy-authoring` but **unmerged, pending live validation** — which was
blocked because the user's Anthropic API key is unfunded (and, after an org deletion, invalid).

The user holds a **paid-tier Google Gemini key**. This change makes the AI provider
**pluggable** (Anthropic *or* Gemini), defaults to Gemini, and live-validates the whole
authoring path on the working key — which also finally unblocks the Phase 2B merge.

## 2. Scope

**In scope (Part 1):**
- Provider abstraction in `app/ai/llm_client.py`: a tier-based router over per-provider backends.
- Gemini backend (`google-genai`) alongside the existing Anthropic backend.
- Provider **dropdown + env default** in the authoring wizard (the chosen selection UX).
- **Spec mode only** on both providers (the existing text/transcript → `StrategySpec` mapper).
- The `powerful` tier is **plumbed but dormant** — the tier→model map includes it so Part 2
  drops in cleanly, but nothing in Part 1 invokes it.

**Deferred to Part 2 (separate spec):**
- The **full-Python escape hatch** (the `powerful` tier writes an arbitrary `StrategyBase`
  module) and its AST + subprocess validation sandbox.
- The **mode toggle** (`Spec (fast)` / `Full Python (powerful)`) in the wizard.

**Recorded Part 2 decisions (so they are not re-litigated):**
- Tier escalation = **explicit mode toggle** (user-set, not fidelity-driven auto-escalation).
- Escape-hatch safety = AST static check (import/call/dunder allowlist) → subprocess smoke
  test (fresh `python -I`, hard timeout, Linux-container rlimits) → install gate; server always
  re-validates; install disabled until a Validate pass; Spec mode stays the default.
- Residual risk acknowledged: an installed plugin runs in-process at deploy time; the sandbox
  guards install, not ongoing execution. Acceptable for a single-user local tool with code review.

## 3. Architecture

### 3.1 Tier abstraction

Callers ask for an **abstract tier**, not a model string. The active provider resolves it.

- `FAST` — the spec mapper (structured extraction into the DSL). The only tier used in Part 1.
- `POWERFUL` — the future full-Python author. Plumbed, dormant in Part 1.

Per-provider tier → model map, each model id **overridable via env** (model names churn):

| Tier | Anthropic (default / env override) | Gemini (default / env override) |
|------|------------------------------------|---------------------------------|
| `FAST` | `claude-sonnet-4-6` / `ANTHROPIC_FAST_MODEL` | `gemini-2.5-flash` / `GEMINI_FAST_MODEL` |
| `POWERFUL` | `claude-opus-4-8` / `ANTHROPIC_POWERFUL_MODEL` | `gemini-2.5-pro` / `GEMINI_POWERFUL_MODEL` |

### 3.2 Provider router + backends

`app/ai/llm_client.py` becomes a thin dispatcher (keeps lazy imports — nothing heavy at module top):

```
complete_structured(*, tier, system, user, output_model, provider=None, max_tokens=4000) -> BaseModel
resolve_provider(explicit: str | None) -> str          # raises if unresolvable
providers_status() -> dict                              # drives the dropdown
any_configured() -> bool
model_for(provider, tier) -> str
```

New backend modules, each lazy-importing its SDK inside the call (host-safe seam):
- `app/ai/_anthropic.py` — the existing `client.messages.parse(... output_format=...)` body.
  Returns `resp.parsed_output`; wraps `anthropic.APIError` → `RuntimeError`.
- `app/ai/_gemini.py` — `from google import genai`; one structured call (see 3.4);
  wraps `google.genai.errors.APIError` → `RuntimeError`.

`complete_structured` resolves provider → resolves `model_for(provider, tier)` → calls that
backend's `call(model=, system=, user=, output_model=, max_tokens=)`. The two backends share
the contract: take a Pydantic `output_model`, return a validated instance, raise `RuntimeError`
on any provider error (so callers surface a clean 502).

### 3.3 Provider resolution order (`resolve_provider`)

1. **explicit** request arg, if that provider's key is configured → use it.
2. explicit arg given but its key missing → **raise** `RuntimeError` ("<provider> selected but
   its API key is not set") — no silent fallback to a different provider/key.
3. no explicit arg → `AI_PROVIDER` env, if configured → use it (same missing-key raise).
4. no explicit, no/empty `AI_PROVIDER` → the single configured provider in preference order
   `[anthropic, gemini]` (preserves prior behavior when only `ANTHROPIC_API_KEY` is set).
5. none configured → **raise** `RuntimeError` ("no AI provider configured").

`providers_status()` returns, env-only (no DB, no SDK import):
```json
{ "providers": [ {"id":"gemini","label":"Google Gemini","configured":true},
                 {"id":"anthropic","label":"Anthropic Claude","configured":false} ],
  "active": "gemini" }     // resolve_provider(None) result, or null if none configured
```

### 3.4 Gemini structured output (`_gemini.call`)

Primary path:
```python
from google import genai
client = genai.Client()                       # reads GEMINI_API_KEY / GOOGLE_API_KEY
resp = client.models.generate_content(
    model=model, contents=user,
    config={"system_instruction": system,
            "response_mime_type": "application/json",
            "response_schema": output_model,  # the Pydantic MappedSpec
            "max_output_tokens": max_tokens})
return resp.parsed                            # validated output_model instance
```

**Risk + fallback (de-risked first, see §7):** Gemini's `response_schema` is stricter than
Anthropic's about nested models and union fields (our `Condition.right` is number-or-string).
If a live round-trip of the real `MappedSpec` schema fails schema conversion, `_gemini.call`
falls back to **no `response_schema`** — `response_mime_type="application/json"` only — and
appends `output_model.model_json_schema()` (the exact `{spec, fidelity}` envelope) to the
system text so the model knows the wrapper to emit, then validates with
`output_model.model_validate_json(resp.text)`. This removes any dependency on Gemini's schema
converter while keeping the same validated return type. The fallback is provider-internal: the
caller's prompt and return type are unchanged. The chosen path is decided once, live,
during the first build step; the fallback ships baked in so failures are self-healing.

### 3.5 Host-safety

No new heavy import at module top. `_gemini.py` / `_anthropic.py` import their SDKs inside
`call()`. `is_configured`/`providers_status`/`resolve_provider`/`model_for` read only env.
Tests mock the seams (`llm_client.complete_structured`, `_gemini.call`, `_anthropic.call`) and
never import the SDKs. `requirements.txt` adds `google-genai>=1.0,<2.0`.

## 4. API changes

- `StrategyFromSourceReq` gains optional `provider: str | None`.
- `POST /strategies/author/from-source`:
  - gate changes from "set `ANTHROPIC_API_KEY`" → `llm_client.any_configured()` else **503**
    "AI authoring is not configured — set GEMINI_API_KEY or ANTHROPIC_API_KEY in backend/.env".
  - forwards `req.provider` → `map_source_to_spec(text, provider=...)`.
  - if `resolve_provider` raises (selected provider unconfigured) → **400** with the message.
- `GET /strategies/author/providers` (new) → `llm_client.providers_status()`. Host-safe, env-only.
- `app/ai/strategy_author.py`: `map_source_to_spec(source_text, provider=None)` →
  `complete_structured(tier=FAST, provider=provider, ...)`.

## 5. Frontend

- `lib/api.js`: add `getAuthorProviders()`; `authorFromSource(source, provider)` includes
  `provider` in the body (keeps the existing `LONG_TIMEOUT_MS`).
- `AuthoringWizard.jsx`: on open, fetch `/providers`. In the ✨ box render a small **Provider**
  `<select>` listing only `configured` providers, default = `active`. If none configured →
  disable Generate with a hint to set a key in `backend/.env`. Selected provider rides along
  in `authorFromSource`. No mode toggle yet (Part 2).

## 6. Config / env

Both `.env` files (`Emergent-AlphaForge/backend/.env` and `af-wt-strategy-library/backend/.env`):
```
GEMINI_API_KEY=<paid-tier key>
AI_PROVIDER=gemini
# optional model overrides: GEMINI_FAST_MODEL, GEMINI_POWERFUL_MODEL,
#                           ANTHROPIC_FAST_MODEL, ANTHROPIC_POWERFUL_MODEL
```
`requirements.txt`: `+ google-genai>=1.0,<2.0`. (`anthropic` stays.)

## 7. Testing & validation

**Host-safe unit tests (mock seams; no SDK, no motor):**
- `resolve_provider`: explicit > env > single-configured; raise on selected-but-unconfigured;
  raise on none; preference order when both set with no `AI_PROVIDER`.
- `providers_status`: shape, `configured` flags from env, `active` value (incl. null when none).
- `model_for`: each (provider, tier) pair + env override wins.
- `complete_structured`: routes to the right backend (patch `_anthropic.call` / `_gemini.call`),
  passes the resolved model for the tier, propagates `RuntimeError`.
- `_gemini.call`: against a monkeypatched fake `google.genai` (success → `.parsed`; APIError →
  RuntimeError; fallback path validates from `.text`).
- Router: `GET /providers` returns status (patch `llm_client`); `/from-source` forwards
  `provider` and maps `resolve_provider` raise → 400, none-configured → 503.
- `map_source_to_spec`: forwards `provider`, still runs `validate_spec`.

**Live validation (Gemini key — the unlock):**
1. `_gemini.call` real round-trip of the actual `MappedSpec` schema → decide primary vs fallback.
2. `POST /from-source` with pasted text → spec + fidelity, via Gemini.
3. `POST /from-source` with a YouTube URL → transcript → spec, via Gemini.
4. Chrome: provider dropdown renders, lists Gemini, Generate fills the form; switch provider value.
5. Full host suite green.

## 8. Risks & mitigations

- **Gemini nested/union schema** → §3.4 baked-in fallback; decided live in build step 1.
- **Model-name churn** → env overrides for every tier/provider model id.
- **Merge debt** (branch 49 behind main) → rebase `feat/strategy-authoring` onto current main
  at rollout; AI-authoring files are disjoint from the live-execution/OCO work on main, so
  conflicts should be minimal. Validate, then merge 2B + Part 1 together via PR.
- **Silent wrong-key fallback** → `resolve_provider` raises rather than falling back when a named
  provider is unconfigured.

## 9. Rollout

1. Build Part 1 on `feat/strategy-authoring` atop the Phase 2B commits.
2. Live-validate on Gemini (§7). Decide Gemini structured-output path.
3. Rebase onto current `origin/main`; resolve any (expected-minimal) conflicts.
4. Open a PR merging the full AI-authoring stack (2B + Part 1) to main. User reviews/merges.
5. Part 2 (escape hatch + mode toggle) starts from its own spec.
