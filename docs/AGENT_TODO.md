# AGENT TODO — live plan, status, and takeover state

> **Purpose:** the single continuously-updated source of truth for the current work
> program, so ANY agent (Claude, Codex, Gemini, human) can take over with zero loss of
> context. **Update this file after every completed work unit** — flip statuses, add
> notes, never delete history (strike through instead).
>
> Companion files: [`learning_log.md`](../learning_log.md) (lessons per session, verified
> audit-finding evidence table) · [`docs/HANDOFF.md`](HANDOFF.md) (architecture/state
> entry point) · `CHANGELOG.md`.

**Last updated:** 2026-07-21 (Claude Fable 5 session — Codex audit triage + quick wins)

---

## 0. Standing decisions (user-confirmed 2026-07-21 — do NOT relitigate)

1. **Deployment freedom is policy.** Any saved/optimized preset may be deployed to paper
   or live after express consent + warning acknowledgment. Evidence gates (forward
   validation, quality warnings) are ADVISORY ONLY — never hard blocks. This is already
   implemented: consent override at `backend/app/routers/deployments.py` (~line 1095,
   `accept_unvalidated_live`); paper create gated only by `acknowledged_warnings`.
2. **The Codex diff is the baseline.** The ~2.7k-line uncommitted ChatGPT/Codex session
   work (consent-override live gate, forward-validation advisory, option-data
   provenance/integrity, docs, tests) is committed as-is, fixes land on top. No revert.
3. **Safety-fix scope now = quick wins only** (H2 NaN-reject, H3 fail-closed, C1 loopback
   binding). The remaining confirmed blockers (C2, C4, H1, C3) are REQUIRED BEFORE THE
   FIRST REAL-MONEY SESSION but deferred until then — see §2.
4. **Priorities after fixes (all four, in this order):** item 2 lazy-leg contingency →
   item 3 strategy-builder audit → item 4 live-page redesign → item 5 new strategy
   plugins. Items 6 (ideas), 7 (deep audit), 8 (docs) are cross-cutting/closing work.
5. **Work inline, single-threaded.** The monthly AI spend limit was hit 2026-07-21;
   multi-agent fan-outs FAIL until it resets/raises. Keep sessions lean.
6. **Push policy:** commit locally; push only with per-changeset user approval
   (long-standing project rule).
7. **Broker safety (permanent):** never call the Flattrade MCP login/logout; never
   place/modify/cancel broker orders from an agent; AlphaForge's own OAuth is the only
   login. Do not refresh Flattrade OAuth while `LIVE_AUTOPLACE_ARMED` is on until §2
   fixes land.

---

## 1. Master status board

| # | Work item | Status | Notes |
|---|-----------|--------|-------|
| A | Commit Codex baseline (suite-gated) | ✅ DONE | `d301272` (3,524 passed) + docs `4b441fd` |
| B1 | H2: reject non-finite monetary values | ✅ DONE | `f9a2482` governor `invalid_daily_loss_cap` guard; route checks pre-existed in the Codex diff (deployments.py:1029) |
| B2 | H3: safety-config fail-closed (no 20-lot default) | ✅ DONE | `f9a2482` — unreadable/invalid config → live disabled for the cadence |
| B3 | C1-lite: loopback port bindings in docker-compose | ✅ DONE | `f9a2482` — ⚠️ run `docker compose up -d` to apply |
| B5 | C4: daily-loss breach demotes mode→paper | ✅ DONE | `f9a2482` (bonus — turned out to be a 1-line fix, not half a day; resume can no longer re-authorize live) |
| B4 | C5: live activation dialog — Continue didn't open confirm step | ✅ FIXED + browser-verified | REAL cause = HTML5 step validation: daily-loss input `min={1} step={100}` → default 4000 is stepMismatch-invalid → native form validation silently blocks submit → handleFormSubmit never runs (button looks enabled b/c the JS guard ignores step). Fix `step="any"` on loss + both catastrophe %-fields; ALSO collapsed the two sibling Radix dialogs into one stepped dialog (robustness). Verified E2E in Chrome (caps→Continue→typed-ENABLE renders; ENABLE gated; Back preserves values). Commit `3f3b457`. NOT the two-dialog theory the Codex audit guessed |
| C | Deferred pre-real-money fixes (C2, H1, C3) | ⏸ DEFERRED | MUST land before first real-money session — §2 |
| 2 | Lazy-leg contingency (Phase 5 design → ship) | ✅ DONE | Was already shipped in backtest+live; built the only gap = **paper-mode lazy arming** (`ab453fa`) + H4 nullable-param deploy fix (`3639009`). Suite 3,549/0. See §3 item 2 |
| 3 | Strategy builder + AI authoring audit/completion | ✅ DONE | H5 preset/backtest validation parity (`10f8ce7`) + AI-install file rollback (`6e8861d`); wizard audited = already robust. Suite 3,557/0. See §3 item 3 |
| 4 | Live-trading page redesign | ⬜ NEXT | Incl. H8 (confirmation completeness), H6 UI surfacing |
| 5 | New strategy plugins (candidates, honestly validated) | ⬜ QUEUED | |
| 6 | Profit-leverage ideas write-up | ⬜ QUEUED | |
| 7 | End-to-end deep audit | ⏸ BLOCKED | Needs multi-agent budget (spend-limit reset) or several lean sessions |
| 8 | Handover documentation refresh | 🔄 ROLLING | This file + learning_log.md updated continuously; final pass at end |

Legend: ⬜ not started · 🔄 in progress · ⏸ deferred/blocked · ✅ done

---

## 2. Safety fixes — verified findings and exact implementation plans

Full verification evidence (file:line, verdict per finding): `learning_log.md` §2026-07-21.
Paper trading in live market hours is NOT blocked by any of these — the paper path
transmits no broker orders. These matter for real money.

### Quick wins (doing now)

**B1 — H2 non-finite values (~30 min).**
- `backend/app/routers/deployments.py` `_LiveEnableBody` (~line 246): add a pydantic
  `field_validator` on `daily_loss_cap`, `catastrophe_stop_pct`, `catastrophe_target_pct`
  rejecting non-finite floats (`math.isfinite`). Python's json parser ACCEPTS `NaN`.
- Defense in depth: `backend/app/live_deploy_governor.py` `_float_or_none` → return
  `None` for non-finite input, AND treat a live deployment whose configured
  `daily_loss_cap` is non-finite as `live_caps_missing` (refuse, pause) rather than
  silently uncapped (`NaN > 0` is False — that's the bug).
- Tests: NaN/Infinity daily_loss_cap → 422 on `/live/enable`; governor with NaN cap
  refuses entry.

**B2 — H3 fail-closed safety config (~30 min).**
- `backend/app/live_deploy_context.py` (~line 263): on `get_config()` failure, do NOT
  default `account_max = 20`; log + `return None` (live disabled this cadence — same
  fail-soft-to-paper path as a broken connection). Test: config store raising →
  `build_live_deploy_context` returns None.

**B3 — C1-lite loopback binding (~15 min).**
- `docker-compose.yml`: `"127.0.0.1:27017:27017"`, `"127.0.0.1:8001:8001"`,
  `"127.0.0.1:3000:3000"`. Container-to-container networking unaffected (backend reaches
  `mongo:27017` internally). Cuts LAN exposure of credential-less Mongo + unauthenticated
  API. `docker compose up -d` to apply. Full API auth + Mongo credentials: only needed at
  VPS migration (do together with H7 then).

**B4 — C5 dialog verification (browser, ~15 min).**
- Rebuild frontend if needed, HARD refresh (Ctrl+Shift+R — stale-bundle gotcha), open
  Live page → Deploy panel → fill form → Continue. If the typed-ENABLE dialog opens:
  Codex's C5 was a stale-bundle artifact; record and close. Either way, consider making
  the two dialogs sequential (`setFormOpen(false)` before `setConfirmOpen(true)` in
  `handleFormSubmit`, reopen form on confirm-cancel) — sibling modals both-open is
  fragile in Radix.

### Deferred — REQUIRED before first real-money session (est. 2–4 days total)

**C2 — transmit fence (~1 day).** `backend/app/live/executor.py` Gate 1 (~line 459)
checks `allow_fn()` once; `backend/app/auto_live.py` (~line 409) builds `allow_fn` over a
STALE deployment doc + frozen `now`. Fix: immediately before the actual `place_order`
transmit, re-fetch the deployment doc from Mongo and re-evaluate
`is_deployment_live_allowed` with a fresh `now`; abort as `blocked:stale_authorization`
if it no longer allows. Also re-check after every await that can take >~1s (margin call,
throttle wait). Test: flip deployment to stopped between margin gate and transmit (mock
broker) → order NOT sent.

**C4 — breaker re-consent (~half day).** `resume` endpoint
(`routers/deployments.py` ~852) must check WHY the deployment paused: if
`risk.live.last_block_reason == "daily_loss_cap"` (or any breaker pause) AND
`mode == "live"`, resume must either (a) demote to `mode: "paper"` + require a fresh
`/live/enable`, or (b) require an explicit `acknowledge_loss_breaker: true` body flag.
Option (a) is cleaner and matches stop-all semantics. Test: breach → PAUSED → plain
resume → deployment is ACTIVE but mode==paper.

**H1 — compare-and-swap transitions (~half day).** All mode/status transitions
(`/live/enable`, `/live/disable`, `stop`, `pause`, `resume`, `stop-all`) currently do
plain `$set` by id. Fix: conditional `update_one({"id": id, "mode": expected_mode,
"status": expected_status}, ...)` and 409 on zero matched count. Test: concurrent
stop-during-enable → one of the two gets 409, final state is stopped.

**C3 — account-global caps (~1–2 days, pragmatic version).** Governor
(`live_deploy_governor.py` ~105) counts only its own deployment's trades. Fix v1:
add an account-scope pass (query live_trades WITHOUT deployment filter) enforcing the
account-level `max_open_positions` + `daily_loss_limit` from the safety config store;
wire `engine.guardrail_tick` (currently test-only, `live/engine.py:264`) into the
evaluator cadence. Fix v2 (atomicity): reserve-then-place via a Mongo
`findOneAndUpdate` reservation doc so two concurrent entries can't both pass
`max_concurrent`. Single-deployment usage makes v1 the priority.

### Explicitly neglected (user-ratified)

- H7 server-verifiable consent — moot until an auth layer exists (VPS phase).
- H8 confirmation completeness — fold into item 4 redesign.
- H6 OCO-failure tolerance — deliberate design; add loud "no broker backstop" UI badge
  in item 4; do NOT unwind filled entries on OCO reject.
- Codex promotion-gate regime (60 sessions/120 trades/bootstrap) — advisory panel only.
- npm build-chain CVEs (prod image = nginx, no node_modules) — revisit at VPS phase.
- H4 (Premium-Momentum deploy rejected: nullable defaults vs "must be numeric") and
  H5 (preset validation parity) — UNVERIFIED claims; verify + fix inside items 2 and 3
  respectively.

---

## 3. Feature items — plans and junior-agent prompts

### Item 2 — Lazy-leg contingency (opposite-side activation on primary-leg SL)

**GAP ANALYSIS DONE 2026-07-21 (verified against code, not memory/docs).** The design
doc (`docs/superpowers/specs/2026-07-13-premium-momentum-phase4-5-full-contingency-design.md`)
is STALE — its "nothing implemented" header predates the 2026-07-17 Phase 5B build. The
lazy-leg contingency IS shipped on two of three rails:

| Rail | Lazy-leg status | Evidence |
|------|-----------------|----------|
| Backtest | ✅ FULL | `premium_momentum_backtest.py`: `leg_mode="both"`, `lazy_enabled`, fresh opposite-side strike lock at the stop-out bar, all `lazy_*` params, moneyness-band preload (C1 fix), 1 reversal/primary/session; adversarially reviewed |
| Live | ✅ SHIPPED | `runtime.py::_live_guard_on_close` (~L288-319) arms `lazy_armed_<side>` on a STOP-class PRIMARY confirmed-flat close (never target/EOD/basket); `premium_momentum_live.py` does the fresh strike pickup + lazy monitor; `premium_lock_store.set_lazy_armed` is the idempotent one-shot |
| **Paper** | ❌ **NOT SHIPPED** | Lazy arming rides the LIVE-guard close hook (`_live_guard_on_close`), which matches a broker `norenordno`. Paper trades have no broker order and no live guard, so a stopped PRIMARY in paper never arms a lazy leg. Paper CAN run both PRIMARY legs (`deployment_evaluator.py` L738/L786 `leg_mode=="both"`) — only the lazy contingency is absent. This matches the known limitation in memory ("guard-side 5B exits are LIVE-only in paper") |

**So the ONLY real remaining work for item 2 = paper-mode lazy arming** — a paper-side
trigger that, when a PRIMARY paper leg closes STOP-class, arms + enters the opposite lazy
leg with a fresh snapshot, mirroring `_live_guard_on_close`. Value: lets the user
forward-test the lazy contingency by paper trading BEFORE risking real money (directly
serves the user's stated goal). **Caveat to state when deciding:** the premium-momentum
edge hunt CLOSED / FAILED (validation +₹103.5k → −₹153.8k holdout; `forward_metrics.py:530`
comment, `docs/PREMIUM_MOMENTUM_EDGE_VERDICT_2026-07.md`) — building paper-lazy is pure
capability, not an edge bet (user already ratified capability-over-edge for 5B).

**RESOLVED 2026-07-21 — user chose (A): build paper-mode lazy arming. DONE.**

Implementation (`ab453fa`): the pickup/entry/latch/exit are all mode-agnostic
(`deployment_evaluator` + `evaluate_premium_momentum_bar` run for paper); the ONLY
live-only piece was arming (it rode `_live_guard_on_close`). Fix:
- `premium_momentum_live.lazy_arm_side()` — PURE shared arming-gate predicate; both
  rails call it so they can't drift. Each rail classifies its own stop reasons
  (`LIVE_STOP_CLASS_REASONS` = stop/breakeven_stop/trailing_stop/spot_stop_hit;
  `PAPER_STOP_CLASS_REASONS` = stop_hit) and passes `is_stop_class` in.
- `runtime._live_guard_on_close` refactored to call it (behavior-identical; live suite green).
- `paper_auto.build_auto_trade` stamps `pm_leg`; `_maybe_arm_paper_lazy_leg` hook in
  `mark_open_deployment_trades` arms the opposite lazy leg on a PRIMARY paper stop-out.
- Tests: `tests/test_premium_momentum_paper_lazy.py` (16). Best-effort — never breaks the
  exit marker; no-op for non-pm / first_to_trigger / non-stop closes.

**H4 DONE (`3639009`):** `runtime._load_deployment_source` now accepts `None` for a param
whose schema default is `None` (nullable), so premium_momentum (and any nullable-param
strategy) deploys directly; required params + non-None values still fully validated.
3 tests in `test_strategy_deployments.py`.

### Item 3 — Strategy builder + AI authoring audit/completion ✅ DONE 2026-07-21

**Audit result:** the authoring stack is in good shape; two concrete gaps fixed.

1. **H5 — preset/backtest validation parity (`10f8ce7`).** `_load_deployment_source`
   fully validated a `strategy` source (registry existence, 1m/timeframe + instrument
   compat, unknown/invalid params) but returned `preset`/`backtest_run` sources straight
   from the DB unvalidated → a preset referencing a deleted strategy / bad timeframe /
   unknown params became a dead ACTIVE deployment. Fixed by extracting a shared
   `_validate_strategy_deployment_config` chokepoint (carries the H4 nullable tolerance)
   and running it for EVERY source type. 6 tests.
2. **AI-install file rollback (`6e8861d`).** `author_install` (spec→code) left a broken
   `.py` on disk when the generated strategy failed to load → broke every future
   `reg.reload()` + next boot. `author_python_install` removed the orphan but destroyed a
   working strategy on a failed overwrite. Fixed with a shared `_write_plugin_with_rollback`
   (restore previous / remove orphan / reload clean / 500). 2 tests.
3. **Frontend wizard (`components/strategy/AuthoringWizard.jsx`) audited — robust:**
   persistent error panels (not vanishing toasts), provider-status gating
   (`aiReady`/`configuredProviders`; AI buttons disabled + "set GEMINI_API_KEY…" hint when
   unconfigured), capability-explainer panel, installedId next-step panel, spec+python
   modes. No risky changes needed; the earlier authoring-UX work holds up.
4. H4 (premium-momentum nullable-param direct deploy) — already fixed `3639009` (item-2 session).

Residual (non-blocking, deferred): live-Gemini end-to-end wizard validation remains a
user manual step (needs a funded key + real market). The H5 unknown-param check is a HARD
reject for parity — if a legit old preset with schema-drifted params ever needs to deploy,
consider softening unknown-params to a quality WARNING rather than a 400.

### Item 4 — Live-trading page redesign

User verdict: current page "not-so-helpful for a trader". Goals: modern UI, easy
deployment control, market context at a glance, Flattrade MCP read-tools as data
sources, optional price-based analysis aids. Constraints: read-only broker calls only;
keep rate budget sparse while deployments armed; include H8 (show complete frozen
config in the enable confirmation) and an H6 "no broker backstop" badge.

Junior-agent prompt:
> Redesign `frontend/src/pages/LiveSignals.jsx` (and `components/live/*`) into a
> trader-first cockpit: (1) deployment cards with mode/status/caps/last-block-reason and
> one-click enable/disable/stop with the consent flow; (2) positions + OCO/backstop
> status with a loud "software-guard-only" badge when oco_al_id is null; (3) market
> context strip (spot, VIX, expiry countdown) from existing backend endpoints; (4) an
> account panel (funds/margin via existing Flattrade read endpoints); (5) the enable
> confirmation must display the complete frozen config that will trade (params,
> timeframe, source SHA, sizing, friction, exits — H8). Do not add new broker-mutating
> endpoints. Chrome-verify with hard refresh.

### Item 5 — New strategy plugins

Honest framing (standing project verdict): no current strategy has proven edge;
optimizer optimizes spot unless option-net mode is used; survival gates exist. Candidate
directions from prior research: regime-routing (EV-positive but sample-starved), ORB
variants, VWAP mean-reversion with option-net objective, IV-crush/theta-aware entries.

Junior-agent prompt:
> Build 2-3 new strategy plugins on the standard StrategyBase rails (registry,
> capability_report, optimizer-compatible params, paper/live deployable). For each:
> backtest 2025-11→latest warehouse, optimizer run with option-net objective, WFO, and
> an untouched holdout check. Report results HONESTLY (edge or no edge) in a verdict
> doc. Do not promise profitability; the deliverable is deployable candidates +
> truthful evidence.

### Items 6/7/8 — cross-cutting

- **6 (ideas):** brainstorm doc — leverage angles: regime router as meta-strategy,
  paper-cohort A/B harness, MCP-fed morning briefing, signal-quality dashboards,
  option-flow features from Full feed (five-level depth + Greeks now captured).
- **7 (deep audit):** wait for spend-limit reset (multi-agent) or run as several lean
  single-file passes; seed list = learning_log.md findings table + Codex remediation
  order §"Required remediation order" in the transcript.
- **8 (docs):** rolling — this file + learning_log.md; final pass = HANDOFF/CHANGELOG/
  takeover-prompt refresh once items land.

---

## 4. Session log

- **2026-07-21 (Claude Opus 4.8, item 3):** item 3 DONE — H5 preset/backtest validation
  parity (`10f8ce7`, 6 tests) + AI-install plugin-file rollback (`6e8861d`, 2 tests);
  authoring wizard audited and found robust. Suite 3,557/0. Local main `6e8861d`.
  NEXT: item 4 (live-page redesign).
- **2026-07-21 (Claude Opus 4.8, cont.):** C5 dialog fixed + browser-verified (real
  cause = HTML5 step validation, `3f3b457`); item 2 lazy-leg gap-analysed then the
  paper-mode arming gap BUILT (`ab453fa`, 16 tests) + H4 nullable-param deploy fix
  (`3639009`, 3 tests). Full suite 3,549/0. Local main `3639009`, ~13 ahead of
  origin, UNPUSHED. Checkpoint per user ("one more item then checkpoint"). NEXT: item 3
  (strategy-builder + AI authoring audit; fold in H5 preset-validation parity).
- **2026-07-21 (Claude Fable 5):** Codex audit triaged; 11/13 findings verified inline
  (evidence in learning_log.md). User interview locked decisions §0. LANDED: Codex
  baseline `d301272`, orchestrator docs `4b441fd`, safety quick wins `f9a2482`
  (H3 fail-closed, H2 governor guard, C4 breach demotion, C1-lite loopback ports)
  — suite 3,530 passed / 4 xfailed. Local main is 3 commits ahead of the last doc
  state (`7ced6e6`) and 10 ahead of origin/main — UNPUSHED (per-changeset push
  approval rule). KEY discoveries: the Codex diff itself already fixed H2 at the
  route level and stop-demotion; its audit apparently reproduced C4/C5 against the
  RUNNING CONTAINERS (old build), not the patched tree — so C5 needs a browser
  retest after rebuild + hard refresh before treating it as a code bug. Deferred:
  C2 (transmit fence), H1 (CAS transitions), C3 (account-global caps) — before
  first real money. Next up: B4 (C5 browser check, needs Docker rebuild) then
  item 2 (lazy-leg).
