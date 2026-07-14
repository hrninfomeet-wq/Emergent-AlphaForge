# Emergent handoff session notes — 2026-07-13

> Concise pickup notes for the next AI agent / engineer. The authoritative
> narrative is CHANGELOG 0.53.0; this file is the human-readable "here's what
> just happened, here's what's left."

## What this session was scoped to do

The user came in with the [Emergent handoff prompt](superpowers/specs/2026-07-13-premium-momentum-phase4-5-full-contingency-design.md)
in one hand and a specific, reproducible Strategy Library bug in the other:

> "When I run any strategy feasibility check or AI generate button then it
> returns 'AI generation failed: The AI (gemini-2.5-pro) response was cut off
> at the 8000-token limit before it finished — the strategy description is
> likely too long or too complex to parse in one pass.'"

Plus a longer-term request: research algotest.in-style strategy building and
add new profitable-for-options-buying strategies to the app. And a course-
correction pass on Optimizer / Backtest / Live / Paper pages.

We agreed to a scoped session:

1. Land **Phase 4 groundwork** (the classifier + capability-report + LLM-prompt
   half of the Phase 4 spec — everything that unblocks the AI wizard).
2. Fix the **Gemini 8000-token cutoff**.
3. Refresh **local-setup docs** so the user can sync from GitHub and run
   without hand-holding.
4. Push once at the end, no per-commit approvals.

Explicitly deferred to a follow-up session (each is genuine multi-hour to
multi-day work on top of the design docs already committed):
- Phase 4 **engine dispatch** (make the general Optimizer/Backtest Lab actually
  run a premium-trigger deployment through the same sim as the bespoke page).
- **Phase 5** lazy-leg contingency (two-leg state machine, session overlays).
- Broader **AlgoTest-style multi-leg / Greeks-based strategy builder**.
- **Optimizer / Backtest / Live / Paper page UX review** — the user flagged
  low confidence in what those pages do and asked for a course-correction
  pass. Needs a working session with the user to know what they want changed;
  we didn't touch any of them this session.

## What actually landed (files touched)

- `backend/app/ai/capability.py` — new `PREMIUM_TRIGGER_CONCEPTS`,
  `SESSION_GATE_CONCEPTS`, `PHASE5_FUTURE_CONCEPTS` sets; three new classifier
  branches placed BEFORE the R5 structure branch (critical — the `premium`
  → ICT `premium_discount` collision was called out in the Phase 4 spec §3.3);
  `capability_summary()` returns a new `premium_trigger` block advertising
  what's shipped vs. Phase-5 future work.
- `backend/app/ai/authoring_agent.py::_ruleset_system_prompt` — LLM
  vocabulary expanded with the new concept names and an explicit
  disambiguation warning about option-premium vs. ICT premium/discount.
- `backend/app/ai/_gemini.py` — `DEFAULT_MAX_TOKENS` 8192 → 32768.
- `backend/app/ai/_anthropic.py` — `DEFAULT_MAX_TOKENS` 8192 → 16384.
- `backend/app/ai/llm_client.py` — wrapper default matched to the higher
  per-backend default; comment updated so nobody drops it again (this
  invariant has been violated once already — the S1 audit note).
- `backend/app/ai/py_author.py` — removed the explicit `max_tokens=8000`
  hard cap (the actual source of the "cut off at the 8000-token limit"
  error). Uses the wrapper default now.
- `tests/test_gemini_token_budget.py` — new, 8 host-safe tests pinning the
  token-budget invariants and the `py_author.py` regression.
- `tests/test_capability_premium_trigger.py` — new, 14 host-safe tests
  for the premium-trigger / session-gate / lazy-leg-honest-scoping
  classifier branches, INCLUDING the disambiguation guard against the ICT
  `premium_discount` collision.
- `tests/test_authored_pipeline.py` — updated the S1 invariant test from
  `== 8192` to `>= per-backend default` (the old pin was pinning the OLD
  value; the invariant is "wrapper default not below per-backend
  default", not "wrapper default equals a specific number").
- `backend/.env.example` — full template with AI keys, live-trading flags,
  Upstox, and Fernet, grouped by purpose.
- `docs/LOCAL_SETUP.md` — rewritten. TL;DR sync-from-GitHub flow, AI wizard
  setup section (with the Phase 4 sanity check), and troubleshooting entries
  covering the historic token-cutoff error.
- `docs/HANDOFF.md` — one paragraph pointing at the 0.53.0 changes.
- `CHANGELOG.md` — new 0.53.0 entry (the authoritative narrative).

## Non-negotiables preserved

Verified with tests + code inspection, in the spirit of the "no new arming
gate / offline-first / executor is the sole entry chokepoint" invariants:

- **Zero changes to `app/live/*`**. No new arming gate, no new entry path,
  no side-effect on `live_deploy_governor.py` / `deployment_kill_switch.py`
  / `auto_square.py`. The classifier just POINTS AT them in its messages.
- **Zero changes to `app/premium_momentum*.py` / `app/deployment_evaluator.py`**
  → byte-identical backward compatibility for the shipped single-leg
  `premium_momentum` deployment (the spec's "regression safety" invariant).
  This is a *lift*, not a rewrite — Phase 4's runtime dispatch hasn't
  landed yet, so nothing is dispatched differently at runtime.
- **Offline-first defaults intact**. `LIVE_AUTOPLACE_ARMED=0` /
  `LIVE_GUARD_ARMED=0` in the new `.env.example`; the sandbox `.env` used
  for this session's live Gemini API test also stays offline-first.
- **`.env` never committed**. `.gitignore` covers `backend/.env` and
  `frontend/.env`; the sandbox test files are inside `/app/` at those
  gitignored paths.

## Verification (how we know it works)

1. **Host-safe tests: 3,304 pass, 4 xfail, 0 fail** (excluding the 1
   pre-existing container-only warehouse test that needs a seeded Mongo —
   verified failing on the baseline commit BEFORE any changes, so
   independent of this session).
2. **Live Gemini API sanity call** using the user-supplied key confirmed
   the 32,768-token budget actually lands in `generate_content(config=...)`
   and gemini-2.5-pro returns a parsed JSON without truncation.
3. **End-to-end blueprint test** (see CHANGELOG 0.53.0): pasting the exact
   AlgoTest "Configurable Contingency Breakout (NF CE PE EXP2 Base)"
   blueprint into `map_source_to_ruleset` now returns `ADVISE` (buildable
   with the lazy-leg-contingency backtest-only caveat), not `REJECT`.

## What to pick up next (in priority order)

### 1. Phase 4 engine dispatch — the second Phase 4 bug (§1.2 of the spec)

Running the shipped `premium_momentum` plugin through the general Optimizer
page STILL produces "Option re-rank produced no paired results" — the
plugin's `evaluate()` is deliberately a stub and the real logic lives only
in `deployment_evaluator.py`'s dedicated `strategy_id == "premium_momentum"`
branch. The Phase 4 spec's item §3.2 is explicit:

> "Wire it into the general path. `backtest.run_backtest` / the Optimizer /
> Backtest Lab pages need a way to run a premium-trigger config through the
> SAME sim, cost model, and honest tuner that
> `premium_momentum_backtest.py`/`_tuner.py` already provide — likely by
> having the generic backtest dispatcher recognize the config block and
> delegate to the existing option-native sim, rather than trying to force
> premium logic through the spot-first two-stage engine."

The capability classifier now speaks the vocabulary; the runtime does not.
Required work:

- Introduce a `premium_trigger` block on the deployment / preset schema
  (mirror the shipped `plugin params` shape: `reference_time`, `moneyness`,
  `side`, `momentum_pct/pts`, `stop_pct`, `target_pct`, `late_lock_cutoff`,
  `trail_x`, `trail_y`, `lots`).
- Teach `backtest.run_backtest` / the Optimizer to detect the block and
  delegate to `premium_momentum_backtest.py`'s existing sim.
- Teach `deployment_evaluator.py` to dispatch on `deployment.premium_trigger`
  presence, not on `strategy_id == "premium_momentum"`.
- Parity test: existing `premium_momentum` deployments must produce
  IDENTICAL trades before/after the dispatch-on-config change (§3.5 of the
  spec's "regression safety" clause).
- Honest-tuning discipline preserved: costs mandatory, chronological
  train/OOS, overfit flag (DEVELOPER_GUIDE §G).

### 2. Phase 5 — lazy-leg contingency + session overlays

Full spec in
[`superpowers/specs/2026-07-13-premium-momentum-phase4-5-full-contingency-design.md`](superpowers/specs/2026-07-13-premium-momentum-phase4-5-full-contingency-design.md).
Hard-gated on Phase 4 engine dispatch landing first, AND on a backtest
showing the contingency is measurably better than the single-leg version
(not merely different).

### 3. AlgoTest-style broader multi-leg / Greeks-based builder

User's own request. Blocked on Phase 5 landing (a genuine multi-leg state
machine is the prerequisite for expressing any multi-leg strategy
declaratively). When picked up, log in to algotest.in with the user for a
concrete feature-parity list — otherwise we're guessing.

### 4. Optimizer / Backtest / Live / Paper page UX review

User: "I have no clue [if these pages need course correction] ... I just
want to be able to create profitable strategies, backtest them and deploy
them for live market trading and/or paper trading."

Best done as a working session with the user driving. Don't do
speculative refactors here — the whole point of the exercise is to align
the pages to the user's actual workflow, not to redesign them in a
vacuum.

## Local-setup / sync-from-GitHub validation

For the user's local run (Docker Desktop + `docker compose up`):

1. `git pull`
2. Copy `backend/.env.example` → `backend/.env`, fill in the sections you
   need (Fernet key, Upstox credentials, `GEMINI_API_KEY` — the Anthropic
   key is optional).
3. `docker compose up -d --build`
4. Sanity: `curl http://localhost:8001/api/strategies/author/providers` should show
   `gemini` (or `anthropic`) with `configured: true`.
5. Strategy Library → AI Author → paste the AlgoTest blueprint → Check
   Feasibility → should return **ADVISE**, not REJECT.

If step 5 still shows REJECT, follow the "AI wizard says..." troubleshooting
section in the refreshed `docs/LOCAL_SETUP.md`.
