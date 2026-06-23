# Strategy Library — Lifecycle Management + AI-Assisted Authoring

- **Date:** 2026-06-23
- **Status:** Design approved (brainstorming); pending spec review → implementation plan
- **Surface:** `frontend/src/pages/StrategyLibrary.jsx`, `backend/app/strategies/*`, `backend/app/routers/research.py`, new `backend/app/ai/*`
- **Related:** `docs/STRATEGY_PLUGINS.md` (to be corrected — see Appendix A), `docs/STRATEGY_DEPLOYMENTS.md`

## 1. Problem

The Strategy Library page is read-only. Two gaps:

1. **No lifecycle control.** A strategy that proves unprofitable can't be removed or disabled from the UI. The registry is in-memory and discovery-only (`StrategyRegistry` in `backend/app/strategies/base.py`), with no `unregister`, no "retired" state, and no delete/retire endpoints. Built-ins are git-tracked source and some strategies have live paper deployments that pin the plugin's file SHA, so removal needs guards.
2. **No authoring on-ramp.** Creating a strategy means hand-writing a Python plugin to the `StrategyBase` contract and dropping it into `backend/app/strategies/plugins/`. There is no path from a *written idea* or a *YouTube/video transcript* to a working, validated strategy script — and no AI assistance, despite that being the natural way to capture an idea quickly and faithfully.

A separate, smaller deliverable: `docs/STRATEGY_PLUGINS.md` is moderately stale (audited 2026-06-23 — see Appendix A) and must be corrected, because an authoring tool is only as trustworthy as the contract it targets.

## 2. Goals / Non-goals

**Goals**
- Add per-strategy lifecycle control (retire/un-retire, delete) with safety guards, surfaced on a redesigned Library page.
- Add an in-app, AI-assisted authoring wizard that turns pasted rules **or** a pasted link (YouTube → fetched transcript) into a **validated, installed** strategy plugin, with an honest fidelity guarantee.
- Keep the generated artifact a **readable, editable Python plugin** that conforms to the existing `StrategyBase` contract — no new runtime concepts in the engine.
- Correct `STRATEGY_PLUGINS.md` and make its indicator/contract content **machine-generated from live code** so it cannot silently drift again.

**Non-goals (v1)**
- No multi-user / auth / cloud key management — single-user local app; the Claude API key lives in `backend/.env`.
- No live (real-money) order involvement — authoring/lifecycle is research + paper only.
- No exhaustive DSL covering every strategy on day one — the spec vocabulary is intentionally bounded, with a full-Python escape hatch for the rest. Designed for extension (§9).
- No automatic profitability judgement — the install gate checks *validity*, not edge.

## 3. Decisions (locked during brainstorming)

| # | Decision | Choice |
|---|---|---|
| D1 | Authoring architecture | **C + A**: deterministic **spec→compiler** core (safe, verifiable) with a **full-Python (Opus) escape hatch** for ideas the spec can't express. |
| D2 | AI brain | **Tiered Claude API**, key in `backend/.env`. **Sonnet 4.6** for spec-mapping; **Opus 4.8** for the full-Python escape hatch. |
| D3 | Inputs | One smart Source box: **pasted text/transcript** *or* a **pasted link** (YouTube URL → backend auto-fetches caption transcript). No file upload in v1. |
| D4 | Fidelity model | Not a blind "100% translation" promise. The mapper returns an explicit **fidelity readback** — `captured` (plain English) + `couldn't-map` + `ambiguous` (with the exact source snippets) — so nothing is encoded silently. The user closes gaps before compiling. |
| D5 | Delete/retire model | **Two-tier.** Reversible **Retire** (any strategy: hides from pickers, blocks new deployments, auto-pauses + squares-off live deployments, file stays). Hard **Delete file** for **custom plugins only** (under `plugins/`); requires retired + no active deployments; disabled for built-ins. |
| D6 | Install gate | **Block only on hard failures** (won't import · invalid schema · throws on real bars · never produces a signal). **Warn (don't block)** on a weak/negative smoke backtest, so an unproven-but-valid idea can install and then be optimized/forward-tested. |

## 4. Architecture overview

```
                         Strategy Library page (redesigned)
            ┌───────────────────────────────────────────────────────────┐
            │  + New strategy → AUTHORING WIZARD        cards + ⋯ menu    │
            └───────────────┬───────────────────────────────┬───────────┘
                            │                                │
                   authoring subsystem                lifecycle endpoints
        (ai/source ingest · grounding catalog ·   (retire / un-retire / delete /
         spec mapper · compiler · escape hatch ·   reload) + active-deployment guard
         validate-in-subprocess · install)               │
                            │                             │
                            └──────────┬──────────────────┘
                                       ▼
                       StrategyRegistry (register / unregister / reload)
                       + retired-state store (Mongo)  +  plugins/ dir
```

The engine (`run_backtest`, `evaluate`, deployments) is unchanged. Everything new either produces a standard `StrategyBase` plugin or manages registry/lifecycle state around it.

## 5. Components

### 5.1 Strategy Library page redesign (frontend)
`frontend/src/pages/StrategyLibrary.jsx` (+ small new components).

- **Header:** title, active/retired counts, **`＋ New strategy`** (opens wizard), search box.
- **Filter chips:** All / Built-in / Custom / AI-generated / Has forward data / Retired.
- **Card (keeps today's content):** name, version, loaded/failed badge, description, forward metrics, instrument/mode/timeframe pills, params. **Adds:** origin/status badges (`built-in` / `custom` / `✦ AI-generated` / `retired`) and a **⋯ actions menu**: `Backtest · Optimize · Edit spec · View source · Retire/Un-retire · Delete file`.
  - *Edit spec* and *View source* show only for AI-generated strategies (provenance exists).
  - *Delete file* is enabled only for custom plugins; disabled with a tooltip for built-ins.
- **Retired shelf:** collapsed `Retired (n)` section; expand to Un-retire or Delete.
- New api.js methods: `createStrategy*` (wizard calls, §5.3), `retireStrategy`, `unretireStrategy`, `deleteStrategy`, `reloadStrategies`, `getStrategySource`.

### 5.2 Registry lifecycle + endpoints (backend)
`backend/app/strategies/base.py`, `backend/app/routers/research.py`.

- **Registry methods:** `unregister(id)`, `reload()` (re-run `auto_discover`; importing already-loaded modules is a no-op, so a freshly written file registers; `importlib.reload` for the overwrite-edit case), and load/merge of the **retired set**.
- **Retired state store:** Mongo collection `strategy_lifecycle` (`{strategy_id, retired: bool, retired_at}`). `list_all()` stamps `retired` and `origin` (`builtin` | `custom` | `ai`) onto each item. "custom" ≡ module under `app.strategies.plugins`; "ai" ≡ has a provenance record (§5.3).
- **Endpoints** (under `/api`):
  - `POST /strategies/{id}/retire` → mark retired; call existing deployment controls to pause + square-off this strategy's live deployments (reuse `square_off_open_paper_trades(deployment_id=…)` + deployment stop from the paper deployment-controls work).
  - `POST /strategies/{id}/un-retire` → clear retired (does **not** auto-resume deployments).
  - `DELETE /strategies/{id}` → **guarded**: 404 if unknown; 403 if built-in (not under `plugins/`); 409 if not retired or active deployments exist. On success: `unregister`, delete the `.py`, delete provenance.
  - `POST /strategies/reload` → re-discover (used by the wizard install step and manual refresh).
- **Picker exclusion:** Backtest/Optimizer/deployment-creation strategy lists exclude retired strategies (a shared `list_active()` helper).

### 5.3 Authoring subsystem (backend) — `backend/app/ai/`

**a. Source ingestion** (`source_ingest.py`)
- Input is text or a URL (detected). YouTube URL → fetch transcript via a caption library (e.g. `youtube-transcript-api`; no API key). Failure (no captions / blocked) returns a clear error so the user can paste text instead. Non-YouTube URL → fetch + extract readable text (best-effort). Output: normalized source text + metadata `{kind, url?}`.

**b. Grounding catalog** (`grounding.py`) — *the anti-staleness keystone*
- Generates the AI's vocabulary **from live code**: available indicator columns (from `indicators.precompute_all_indicators` + the regime/adaptive layers), `Signal` fields (from the `Signal` dataclass), and the param-schema shape. Emitted as JSON. Reused to (i) prompt the mapper, (ii) constrain the compiler, (iii) regenerate the doc's indicator table (Appendix A). A test asserts catalog ≡ code so it can't drift.

**c. Spec DSL** (`spec_schema.py`) — bounded, human-eyeballable
- `meta` (id, name, version, description, supported instruments/modes/timeframes).
- `params`: list of `{name, type: int|float|bool, min, max, default}`.
- `entry`: per-direction (CE/PE) boolean tree of **conditions** over catalog columns/params/constants; ops `> < >= <= == != cross_above cross_below`, combined with AND/OR. `score`/`signal_threshold`.
- `gates` (blockers): conditions that kill a signal (e.g. `regime == CHOP`); `cooldown_bars`; optional time-of-day window (`tod_tradeable`).
- `exits`: any subset of `spot_target_pts, spot_stop_pts, target_pct, stop_pct, time_stop_minutes`.
- Validated with Pydantic; every column/op is whitelisted against the catalog.

**d. Spec mapper** (`strategy_author.py`, Sonnet) → structured output
- Prompt = grounding catalog + source text + the spec schema. Returns `{spec, fidelity: {captured[], couldnt_map[], ambiguous[]}}` via a forced structured-output tool. `couldnt_map`/`ambiguous` carry the verbatim source snippets.

**e. Compiler** (`compiler.py`) — deterministic, safe
- Renders a **readable, self-contained** `StrategyBase` subclass from the spec (explicit Python conditions, `is_builtin = False`, full `parameter_schema`, `evaluate()` with warm-up guard + the entry/gate/exit logic). No arbitrary code — output is a function of the (validated) spec, so it is safe and reproducible. Golden tests pin spec→code.

**f. Escape hatch** (`strategy_author.py`, Opus) — full Python
- When the user clicks "Generate full Python," Opus writes a complete `StrategyBase` plugin grounded by the catalog + the contract. Shown to the user (code view) **before** execution. Goes through the same validation/install gate as compiled code.

**g. Validation / smoke harness** (`validate.py`) — subprocess-sandboxed
- Runs in a **child process with a timeout** (path-A code is AI-written and must never hang/wedge the backend): import → instantiate → schema check → run on recent real bars → short `run_backtest` (first supported instrument/mode, recent N sessions, costs on, walkforward off). Returns `{ran, errors[], signals_fired, trades, win_rate, pnl}`.
- **Gate (D6):** block if `not ran` / import or schema error / `signals_fired == 0`; warn if `pnl <= 0` or trades very few.

**h. Install + provenance**
- Write `.py` to `plugins/` (unique slugified id; suffix on collision), `POST /strategies/reload`-equivalent in-process register. Store provenance in Mongo `generated_strategies` (`{strategy_id, source, kind, url?, spec, model, created_at, code_sha}`) → powers *Edit spec*, *View source*, audit. A short header comment marks the file AI-generated with its id.

**i. LLM client** (`llm_client.py`)
- Thin Anthropic client; model per task (D2); key from `backend/.env` (`ANTHROPIC_API_KEY`). Graceful, explicit error if the key is missing. Tests inject a fake client (no real API calls in CI).

**Wizard UI** (frontend): 6 steps — Source → AI→Spec → Review&edit (spec editor + fidelity panel + escape-hatch button) → Compile/Generate → Validate+smoke (results + gate) → Install. Launched from the Library `＋ New strategy` button; full-screen modal/drawer.

### 5.4 Doc correction — `STRATEGY_PLUGINS.md`
Apply Appendix A's checklist. The indicator table is regenerated from the grounding catalog (§5.3b), and a test keeps doc + catalog + code in sync.

## 6. End-to-end data flow (authoring, happy path)

1. User pastes a transcript/link → `source_ingest` normalizes to text.
2. `grounding.catalog()` + text → **Sonnet** → `{spec, fidelity}`.
3. UI renders the editable spec + fidelity readback; user resolves gaps (or clicks escape hatch → **Opus** → full Python).
4. `compiler.compile(spec)` (or Opus code) → `.py` string.
5. `validate.smoke(code)` in a subprocess → results; gate decides block/warn/pass.
6. On accept → write to `plugins/`, register, store provenance → card appears in the Library.

## 7. Error handling & safety

- **Arbitrary code (path A):** shown to the user before run; executed only in a timeout-bounded subprocess; never auto-run without the install step. Path C code is template-generated and trusted.
- **Transcript fetch:** explicit, recoverable failure → fall back to paste.
- **Lifecycle guards:** delete is 403 for built-ins, 409 unless retired + deployment-free; retire squares off live deployments to avoid orphaned positions.
- **Hot-reload:** new files import cleanly; edits use `importlib.reload`; reload failures surface as a "failed" card (existing `_errors` path) without crashing discovery.
- **Determinism:** spec→code is pure; provenance stores `code_sha` so drift detection (deployment SHA pin) keeps working.

## 8. Testing strategy

- **Compiler:** golden spec→code tests; behavioral tests (compiled strategy fires expected signals on crafted bars).
- **Registry/lifecycle:** unregister/reload; retire pauses + squares off deployments; delete guards (built-in 403, not-retired 409, active-deployment 409).
- **Validation harness:** good spec passes; broken code fails; subprocess timeout enforced.
- **Spec mapper:** fake LLM client returns canned `{spec, fidelity}`; assert plumbing + compile + readback rendering.
- **Catalog/doc sync:** test asserts grounding catalog ≡ `indicators` output and doc table ≡ catalog.
- **Frontend:** page renders badges/menu; menu actions hit correct endpoints; wizard step transitions; gate blocks/warns correctly.

## 9. Extensibility (designed-for-growth)

New DSL vocabulary (e.g. session-anchored ORB via `session_precompute`, pyramiding, multi-leg), new wizard steps (optimize-on-install, compare-vs-existing), and new input sources slot in without touching the compiler core or install gate. The grounding catalog auto-absorbs new indicator columns. File-upload input and non-YouTube article extraction are obvious v2 adds.

## 10. Build phases

- **Phase 0 (small):** correct `STRATEGY_PLUGINS.md` (Appendix A) + ship the grounding catalog module + catalog/doc/code sync test.
- **Phase 1:** Library page redesign + registry lifecycle (retire/un-retire/delete/reload, guards, retired store, picker exclusion) + endpoints. Delivers ask #1 and builds the install/hot-reload plumbing the wizard needs.
- **Phase 2:** authoring subsystem (LLM client, source ingest incl. YouTube fetch, spec mapper, compiler, escape hatch, validate-in-subprocess, install/provenance) + wizard UI.

Each phase is independently shippable and testable.

## 11. Open questions / future

- Exact "recent N sessions" window for the smoke backtest (default ~10 sessions; tune in implementation).
- Whether *Edit spec* re-runs the full gate (proposed: yes — re-validate on every change).
- v2: file upload, article extraction, optimize-immediately-after-install, "compare against existing strategy" diff.

---

## Appendix A — `STRATEGY_PLUGINS.md` drift checklist (audit 2026-06-23)

Audit verdict: **moderately stale, no rewrite needed** (30 claims accurate; 4 stale, 2 incorrect, 8 missing-capability). Apply:

1. **Add a "Per-session precompute (perf)" subsection** — document `session_precompute(self, df, params) -> dict`: runs once pre-loop, return merged into `ctx`, source of ctx extras (`orb_hi`/`orb_lo`); reference `session_features.py`; worked example `opening_range_breakout.py`. *(base.py:58-65)*
2. **Fix the Template** — add `is_builtin = False` to the required attributes (StrategyBase defaults it to `True`, so a verbatim copy is mis-badged "built-in" in the UI). *(base.py:45,71-82)*
3. **Fix the built-in count** — "6 built-in strategies" → **12**; reconcile with the 4-item example list.
4. **Document the extra Signal fields** — `scenario`, `spot_target_level`, `exit_mode`. *(base.py:25-27)*
5. **Add the missing indicator columns** ("adaptive toolkit"): `vel_z, accel_z, vr, regime_score, squeeze_on, squeeze_fire, sqz_mom, supertrend, st_dir, vwap_sigma/u1/u2/l1/l2, nr7, cpr_p/cpr_tc/cpr_bc/cpr_width_pct/day_type/R1/S1/R2/S2, orb_width_pct_partial, orb_width_pct_prior, tod_tradeable`. *(indicators.py:286-313)*
6. **Fix the `regime` attribution** — added post-precompute by `classify_regime_series()`, not by `precompute_all_indicators()`. *(indicators.py; runtime.py:651)*
7. **Fix the `time_stop_minutes` row** — it is **enforced live** (reason `time_stop`, backtest parity), not "captured for audit." *(paper_auto.py:597-608)*
8. **Add an "optional base classes" section** — `AdaptiveStrategyBase` (override `_core_signal` + `extra_params`), `ScenarioRoutedStrategyBase` (set `scenarios_traded` + override `_route`); examples `gap_fade.py`, `opening_range_regime_router.py`.
9. **Add a "no hot-reload" note** to Restart Backend — discovery runs once at startup; editing a loaded plugin needs a full restart. *(server.py:62; base.py:128)* — **superseded for AI-installed strategies once Phase 1 ships in-process reload.**
10. **Document `ctx["instrument"]`** in the `evaluate()` ctx description. *(backtest.py:112)*
11. **(LOW)** note deployment POINTS fallbacks (`auto_paper_target_pts`/`auto_paper_stop_pts`), `allow_overnight` square-off exception, and prefer `ist_time`/`session_date` over the internal `dt` column.
