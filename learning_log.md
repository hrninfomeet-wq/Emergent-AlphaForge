# Learning Log

Orchestrator session log: one core lesson, confirmed approaches, and dead ends per execution,
so the next session starts smarter. Newest entry first.

---

## 2026-07-21 — Codex release-audit triage (Claude Fable 5 session)

**Task:** Assess the ChatGPT-5.6 Codex session's "release audit failed" verdict (5 critical
blockers, 8 high-risk findings) against the real codebase; decide implement vs. neglect,
with focus on paper-trading-in-live-market and real-money readiness.

### Core lesson

**Verify an external agent's audit against the actual code before acting on it.** The Codex
audit cited file paths that don't exist in this repo (`backend/app/api/`,
`backend/app/services/`, `backend/app/live/auto_live.py`), yet most of its capital-safety
findings verified TRUE at the real locations. Neither blind trust nor blind dismissal
survives contact with the code — every claim needed a file:line confirmation, and one
critical claim (the "broken" activation dialog) looks likely to be a test artifact
(stale frontend bundle — a known, documented gotcha of this project) rather than a code bug.

### Confirmed findings (verified inline, file:line)

| ID | Claim | Verdict | Where |
|----|-------|---------|-------|
| C1 | No auth / network isolation | CONFIRMED (predates Codex) | `docker-compose.yml:7,35` publishes Mongo+backend on all interfaces, credential-less Mongo, no API auth |
| C2 | Stop doesn't fence in-flight orders | CONFIRMED (predates Codex) | `executor.py:459` single `allow_fn()` check; `auto_live.py:409` closes over stale doc + frozen `now` |
| C3 | Limits per-deployment, not account-global/atomic | CONFIRMED (predates Codex) | `live_deploy_governor.py:105` queries by `deployment_id` only; `engine.py:264` `guardrail_tick` has test-only callers |
| C4 | Loss-breaker resumable without re-consent | CONFIRMED (predates Codex) | `routers/deployments.py:852` resume→ACTIVE with no pause-reason / mode check |
| C5 | Live activation dialog broken | UNPROVEN | Code reads correct (loaded-flags always set, button disabled matches submit guard). Likely stale-bundle repro or Radix double-modal stacking (`DeployToLivePanel.jsx:241` + `:427` both open simultaneously). Needs browser retest after hard refresh |
| H1 | Enable/stop race, no compare-and-swap | CONFIRMED | `routers/deployments.py:1166` plain `$set` by id |
| H2 | NaN accepted, disables loss breaker | CONFIRMED | `routers/deployments.py:249` no finiteness validator; governor `loss_cap > 0` is False for NaN |
| H3 | Safety config fails open to 20 lots | CONFIRMED | `live_deploy_context.py:264-269` |
| H6 | OCO failure tolerated | CONFIRMED but DELIBERATE | `live_deploy_context.py:167-217` documented design: never unwind a filled+guarded entry |
| H7 | Consent not server-verifiable | TRUE but moot | Single-operator local app; meaningless until an auth layer exists (C1) |
| H4/H5 | Premium-momentum deploy rejection; preset validation parity | NOT YET VERIFIED | Check during lazy-leg / strategy-builder work |

### Key facts that reframe the Codex verdict

- **The user's deployment-freedom request is ALREADY implemented** in the uncommitted diff:
  `routers/deployments.py:1095-1129` makes forward-validation advisory with explicit
  `accept_unvalidated_live` consent override. Broker/capital gates stay hard. Paper deploys
  are gated only by warning acknowledgment (`:404-431`) — never blocked.
- **Most confirmed blockers predate the Codex session** — they are v0.56.0 gaps in files the
  Codex diff never touched (executor.py, live_deploy_governor.py, docker-compose.yml).
  Reverting the Codex diff would fix nothing and lose the consent flow + option-data
  provenance work.
- Nothing found blocks PAPER trading in live market hours: the paper path transmits no
  broker orders; C2/C3/C4-class risks are live-only.

### Confirmed approaches

- Targeted inline verification (Grep for the mechanism → Read ±40 lines) confirmed/refuted
  11 of 13 findings in ~15 tool calls without burning subagent budget.
- Reading the transcript tail-first (verdict section) + grep for section headers beat
  reading 16k lines linearly.
- Checking `git status` provenance per finding ("is the vulnerable code in the uncommitted
  diff or in HEAD?") instantly settled the keep-vs-revert debate.

### Dead ends to avoid

- **23-agent workflow fan-out died on the monthly spend limit** (17/23 agents failed;
  ~1.3M subagent tokens spent for 6 chunk digests). Until the limit resets/raises: work
  inline, single-threaded, lean. Don't retry workflows this month.
- Codex's cited file paths are unreliable — always re-locate by mechanism (Grep), not path.
- Don't judge C5-class UI bugs from code reading alone; this project's frontend serves
  stale bundles after rebuilds (hard-refresh first, then test).

### Addendum (same session, after fixes landed)

- **The Codex audit tested the RUNNING CONTAINERS, not its own patched working
  tree.** Its diff had already fixed H2 at the route level
  (`deployments.py:1029` finite+positive `daily_loss_cap` mandatory) and made
  stop demote live→paper — yet the audit reported both as broken. Corollary: C5
  (the "broken" activation dialog) was very likely reproduced against the old
  bundle; retest in a browser after rebuild + hard refresh before touching the
  component.
- **C4 was real but tiny:** the breach path paused without demoting `mode` —
  while the enable route's docstring already CLAIMED "pauses and demotes". A
  1-line `$set` addition + tests closed it (estimated half a day, took minutes).
  Lesson: when code contradicts its own documented contract, the fix is usually
  the missing line, not a redesign.
- **Dead end:** adding a pydantic `field_validator` for finiteness broke the
  existing test contract (tests construct the body model directly with NaN and
  expect the ROUTE to raise 400). Route-level checks already existed; the right
  defense-in-depth layer was the governor (protects against DB-crafted docs).
  Reverted the validator, kept the governor guard.
- **PowerShell gotcha:** a here-string commit message containing double quotes
  got re-tokenized and split into bogus pathspecs. Write commit messages to a
  temp file and use `git commit -F <file>`.
- Landed: `d301272` (Codex baseline), `4b441fd` (orchestrator docs), `f9a2482`
  (H3 fail-closed + H2 governor guard + C4 breach demotion + C1-lite loopback).
  Suite 3,530/0. Unpushed.

### C5 — TWO wrong diagnoses before the browser gave the real one

The activation dialog's "Continue does nothing" bug took THREE hypotheses. Only the
last, forced by direct browser event-inspection, was correct. A cautionary tale in
not trusting plausible theories (mine or another agent's) without instrumentation.

- **Hypothesis 1 (mine, wrong):** "stale bundle — the code reads correct." Killed by a
  fresh rebuild that still reproduced.
- **Hypothesis 2 (Codex's + mine, wrong):** "two sibling Radix `<Dialog>`s; the confirm
  layer eats the submit's pointerup." Plausible, and I even refactored to a single
  stepped dialog to fix it — but after that refactor the confirm step STILL didn't open.
  If I'd stopped at "it compiles + looks right," I'd have shipped a non-fix.
- **Hypothesis 3 (correct, found by instrumenting the DOM event):** attached a capture
  `submit` listener + `click` listener to the form/button → **`click-fired` but
  `submit-fired` NEVER fired.** A type=submit click that doesn't submit ⇒ native HTML5
  form validation is blocking it. `form.checkValidity()` = false; the daily-loss input
  (`min={1} step={100}`, value 4000) reported `stepMismatch:true` — "nearest valid values
  3901 and 4001." Valid values are 1+100n, so 4000 (and every round rupee amount) is
  natively invalid. Submit never fires → `handleFormSubmit` never runs. The button looks
  enabled because `canProceedToConfirm` (JS) checks `>0`, not step validity.
- **Fix:** `step="any"` on the loss field + both catastrophe %-fields (same latent trap:
  `min={0.1} step={0.5}` → "50" invalid). Kept the single-dialog refactor as a genuine
  robustness win. Verified E2E in Chrome. Commit `3f3b457`.
- **Lessons:**
  1. When a `type="submit"` button "does nothing," check `form.checkValidity()` and each
     input's `.validity.*` FIRST — native validation silently swallows the submit with no
     console error. A capture-phase `submit`-vs-`click` listener pair localizes it in one probe.
  2. `<input type="number" step={X}>` with a `min` that isn't a multiple of `X` makes most
     human-entered values invalid. Use `step="any"` unless you truly want a discrete grid.
  3. A JS "can I proceed" guard that gates a button's `disabled` does NOT replace native
     form validity — the two can disagree, and native wins at submit time.
  4. Don't stop at "compiles + looks right." My dialog refactor was correct code that
     fixed the WRONG bug; only re-running the real user action proved it insufficient.
- Contract tests grepping the JSX (`accept_unvalidated_live`, `api.deploymentMetrics(dep.id)`,
  `armAdvisories`, the consent label) still pass (74) — consent strings untouched.
- **Docker/OneDrive footnote:** three "FIX-ABSENT" scares were ALSO measurement errors —
  I grepped the *minified* `main.*.js` for the original identifier `closeConfirmBackToForm`,
  which CRA renames in production. The `.js.map` (original names preserved) is the correct
  check. And `docker compose build` from the repo root read a stale build-context for this
  OneDrive path while a direct `docker build` from `frontend/` read fresh — build directly
  from `frontend/` and verify the served bundle hash + `.map`, not a minified grep.

### Item 2 (lazy-leg) — the premise was wrong; verify against code, not docs/memory

- The user (and the committed design doc dated 2026-07-13) said lazy-leg was "not yet
  shipped." **It was shipped in backtest + live** (the doc predates the 2026-07-17 build).
  Only PAPER arming was missing. Lesson (again): a design doc's "nothing implemented"
  header is a point-in-time claim — grep the actual code before scoping "finish it" work.
- **Architecture insight that shrank the task 5×:** the lazy pickup/entry/latch/exits are
  all mode-agnostic (`evaluate_premium_momentum_bar` + `deployment_evaluator` run for paper
  too); leg-resolution gates on `<prefix>_triggered` which the evaluator latches for paper.
  The ONLY live-only piece was ARMING (`set_lazy_armed`), called from the live guard-close
  hook `_live_guard_on_close` (matches a broker `norenordno` paper never has). So "build
  paper lazy contingency" reduced to "call set_lazy_armed when a paper primary stops out."
- **Reason strings differ per rail** — a real trap. Live guard emits
  stop/breakeven_stop/trailing_stop/spot_stop_hit; the paper marker's premium stop is
  `stop_hit` (execution_policy.tick_exit_reason default). A naive shared STOP-class set
  would have silently never armed in paper. Fix: shared PURE gate predicate
  (`lazy_arm_side`), per-rail reason classification passed in as `is_stop_class`.
- Refactoring the working LIVE hook to call the shared predicate was de-risked by the full
  suite (existing premium-momentum + live arming tests) — green after = safe. Single source
  of truth beats duplicated gate logic that can drift.
- **H4 fix pattern:** a general numeric validator that rejects `None` breaks any strategy
  with nullable params. The principled signal for "nullable" is `schema default is None`.
  Gate on that, not on a param-name allowlist.
- **PowerShell here-string commit messages with embedded double-quotes get re-tokenized
  into bogus pathspecs — AGAIN.** Always `git commit -F <tempfile>`. (Third time this
  session; it is now muscle-memory: never inline a quoted commit body in PowerShell.)

### Item 3 (strategy builder + AI authoring) — audit findings

- **H5 was a validation-parity gap, not a wizard bug.** The wizard itself is solid; the
  hole was downstream: `_load_deployment_source` validated a `strategy` source thoroughly
  but returned `preset`/`backtest_run` docs from the DB unvalidated. Lesson: when one code
  path validates and a sibling doesn't, extract ONE shared chokepoint rather than copy the
  checks — I made `_validate_strategy_deployment_config` the single validator for all three
  source types (it also absorbed the H4 nullable tolerance for free).
- **Two install paths, two different post-write-failure behaviors** — a classic drift bug.
  `author_python_install` cleaned up the orphaned .py on load failure; `author_install`
  (spec) did NOT. And neither restored the previous file on a failed overwrite (silent loss
  of a working strategy). Unified into `_write_plugin_with_rollback`: snapshot → write →
  reload → on failure restore-or-remove → reload → 500. General lesson: any "write file then
  reload/validate" needs an explicit rollback, or one bad write poisons every future reload.
- **Auditing ≠ rewriting.** The frontend AuthoringWizard turned out well-built (persistent
  error panels, `aiReady` provider gating, capability panel). The right audit outcome was
  "verified robust, no change" for the frontend + two precise backend fixes — not churning
  a working 973-line component. Resisted scope creep.
- `git commit -F <tempfile>` used for every commit body this session — zero PowerShell
  here-string mangling incidents once I stopped inlining quoted messages.

### Item 4 (live cockpit) Phase 1 — build lessons

- **Brainstorm-before-build paid off.** The FIRST mockup (tabbed cockpit) was rejected by
  the user ("rethink the layout") — tabs hid positions/market behind clicks. Iterating the
  mockup 3× (tabbed → always-on-core+drawer → +broker module +account tabs +compact regime)
  BEFORE writing code saved a large wasted implementation. The design skill's hard gate
  (no code until approved) is worth the ceremony for a page redesign.
- **Fast host build beats Docker for the edit loop.** The frontend uses **craco** (not raw
  react-scripts) — `node_modules/.bin/craco build` on the host compiles in ~30s and resolves
  the `@/` alias (raw react-scripts does NOT — it fails on `@/App`). Use craco for the
  compile loop, Docker rebuild only for the final Chrome verify. Confirm the served bundle
  hash matches the host build hash to prove freshness (dodges the OneDrive stale-context trap).
- **CRA/CI treats unused vars + bad imports as errors.** Removed unused state/imports
  proactively; verified lucide icon names against the installed version (`node -e "'X' in
  require('lucide-react')"`) before relying on them — cheaper than a failed build.
- **Retiring a component means repointing its source-contract tests.** LiveDashboard.jsx was
  pinned by 3 grep-the-JSX tests (degraded banner, kill switch, trade stats). Deleting it
  without repointing = 3 red tests. Moved the assertions to the new homes (AlertRail,
  liveHelpers, LiveCockpit, AccountTabs) and confirmed the asserted safety features actually
  moved (caught that I'd dropped the `live-hero-asof` STALE stamp — restored it).
- **`git add <deleted-and-git-rm'd path>` aborts the whole add** ("did not match any files"),
  silently leaving new files unstaged. Don't re-add a path already staged for deletion; amend
  if the first commit came out partial.
- Phase 1 on branch `feat/live-cockpit` (`3511874`), suite 3,564/0, Chrome-verified.

### Item 4 Phase 2 (market-analysis engine) — the orchestration lesson

**Core lesson: delegate the specified, keep the integrated — and NEVER accept a
subagent's self-report as verification.** Both juniors reported clean passes; both
had a real defect that only my own adversarial probe found. The review step is not
ceremony, it is where the bugs are caught.

**Confirmed approaches (repeat these):**
- **Tiering by risk worked.** Pure, fully-specified TDD functions and pure
  presentational components → junior agents (Sonnet). Anything touching the broker
  client, live routes, shared provider state, or real-money paths → me. Zero rework
  was needed on the delegated slices beyond the two review fixes.
- **Recon-first delegation.** Dispatching a READ-ONLY Explore agent to produce an
  exact reference sheet (signatures, collection names, row shapes, cache patterns)
  BEFORE writing the endpoint corrected three wrong assumptions in my own plan:
  there is no daily candle collection (resample or aggregate), the indicator columns
  are `ema9/21/50` (no `ema_20`), and the "option chain" is assembled client-side
  from two calls — but full-mode ticks DO carry `open_interest`. Writing the endpoint
  against guesses would have cost far more than the recon.
- **Parallel juniors + my own work.** Recon and the UI components ran in background
  while I built the broker holdings path — three work streams, no file conflicts
  (they were scoped to disjoint files, and I told each agent exactly which files it owned).
- **Give juniors the failure semantics, not just the happy path.** The prompts that
  specified "never raise / return this sentinel / declare provenance" produced code
  that degrades honestly; the one gap (below) was where I under-specified.

**Defects the review caught (both would have shipped):**
1. `put_call_ratio` raised `ValueError` on a non-numeric OI — inside an endpoint that
   renders a live risk surface, one malformed chain row would have 500'd it. Fixed
   with a shared `_f()` coercion + 4 regression tests.
2. Live output showed `label: "CHOPPY"` with `kind: "trending"` — a self-contradiction
   on screen, plus an unrounded `ADX=27.904506321486576` in the human "why" string.
   Only visible by calling the real endpoint against real data, not from tests.

**Dead ends / traps to avoid:**
- **Testing a rebuilt frontend against a stale backend.** I rebuilt the backend, THEN
  fixed the ADX rounding, and the browser still showed the old string. Rebuild the
  container for the tier you just changed, and re-verify the value you actually fixed.
- **Case-sensitive text probes against CSS-uppercased UI.** My verification probe
  reported `Intraday: false` / `Confidence: false` purely because Tailwind's
  `uppercase` makes `innerText` return `INTRADAY`. Dump the surrounding text before
  concluding a panel failed to render.
- Don't recompute what the page already polls: net greeks come from the existing
  `/live-broker/greeks` slice merged client-side, avoiding a duplicate broker read
  and keeping the panel alive while disconnected.

Landed `e0fb250` (primitives) + `df6ebe3` (holdings) + `afbd24b` (engine+wiring);
suite 3,610/0; endpoint + cockpit verified against live market data.

### Live Cockpit page audit (2026-07-25) — measure the bug, don't theorise it

**Core lesson: reproduce a UI bug with MEASUREMENTS in the real browser before
touching code.** The user reported "the Configure drawer can't be scrolled or
modified". Reading the code suggested the classic missing `min-h-0`. The actual
DOM said something more specific: the drawer body had `scrollHeight === clientHeight`
(so no scrollbar could ever appear) while each section reported
`clientHeight 231 / scrollHeight 653` — the sections were flex children with the
default `flex-shrink:1` AND `overflow-hidden`, so they SQUASHED and silently
clipped 422px of their own content instead of the body scrolling. The user
"couldn't modify" because the editor chips were inside the clipped region. Fix =
`shrink-0` on the sections + `flex-1` + inline `minHeight:0` on the body.
Verified after: sections at full height, `scrollHeight 1215 > client 507`, editor
opens (+1 input).

**Confirmed approaches:**
- A 20-line browser probe that prints `clientHeight/scrollHeight/computed flex`
  per node localises a layout bug in one shot. Ship the probe, not a hypothesis.
- Re-run the SAME probe after the fix as the acceptance test — before/after
  numbers are the proof.
- `git commit` immediately after each verified batch. Two spend-limit interrupts
  hit mid-session and cost nothing because the work was already committed.
- Recovering a dead workflow's output from `journal.jsonl` salvaged 20
  critical/high findings from agents that completed before the limit — a killed
  workflow is not necessarily lost work.

**Dead ends / traps:**
- **A workflow that dies on the spend limit reports `survived: []` and a nonzero
  `refuted_count` — that is NOT a clean bill of health.** The "refutations" were
  verify agents that errored. Always check the failures list before believing a
  green summary.
- **React silently drops the `inert` JSX attribute** (verified: the closed drawer
  still had 16 tabbable controls with `inert=""` in JSX). Set `el.inert = true`
  on the node in an effect instead.
- Focusing an element in the same commit that un-inerts it silently fails —
  defer with `requestAnimationFrame`.
- **Case-sensitive text probes lie** against Tailwind `uppercase` (innerText
  returns "INTRADAY"); and a self-reported "N passed" from a subagent is not
  verification — my own adversarial probe found the crash its 27 tests missed.

**Regression worth remembering:** replacing LiveDashboard with LiveCockpit
dropped `ExecutionStateStrip` entirely — the page lost its "will a signal
transmit a REAL order right now" verdict, the exit-gap warning and Stand-down.
When retiring a container component, enumerate what it RENDERED, not just what it
computed.

### Open items carried forward

1. Safety-fix sprint (pending user decision on scope): H2+H3 (trivial), C1 loopback
   binding (trivial), C4 resume re-consent, C2 transmit fence, C5 browser verification,
   H1 CAS, C3 account-global wiring.
2. User's items 2-8: lazy-leg Phase 5, strategy-builder audit, live-page redesign, new
   strategy plugins, profit-leverage ideas, end-to-end audit, handover docs.
3. Uncommitted Codex diff (~2.7k lines, 50 files) needs a commit decision + full suite run.
4. H4/H5 verification.

---

## Session 2026-07-27 (cont.) — C3 account-global caps: the last real-money blocker

**Core lesson: when you delegate a verdict to an existing shared function, you inherit
its fail-safe — and a fail-safe that was proportionate at its original call site can be
wildly disproportionate at a new one.**

`evaluate_guardrails` returns `broker_stop_loss` for a genuine loss breach AND for
non-finite inputs ("don't trade on an unknown P&L"). Reusing it was right — account
semantics should live in one place. But I also forwarded its verdict to
`engine.guardrail_tick`, which *persists* the latch and halts the engine until a human
resets it. So a single NaN in one `live_trades` row — and `json.loads` accepts `NaN` —
would have escalated a DATA DEFECT into an account-wide, manual-reset halt. The fix is a
two-way split the shared function can't make for me, because only the caller knows what
its side effect costs:

- numbers **unknown** (non-finite) → refuse THIS entry (`account_exposure_invalid`), no latch
- numbers **known and breaching** → refuse AND halt the desk (the real stop)

Generalised: reuse the *judgement*, but decide the *escalation* at the call site. Ask of
every delegated verdict, "what does this function's worst-case answer make me DO, and is
that proportionate to what could have caused it?"

**Confirmed approaches:**
- **A gate is not done when it works; it's done when it can't become dead code.** The
  gate was correct and fully green while still being unreachable in production — the
  config never left `build_live_deploy_context`. Three behavioural wiring tests through
  the real orchestrator plus one source-contract test (`inspect.getsource` asserting the
  evaluator's `live_kwargs` allowlist forwards `account_safety_config`) close that gap.
  The allowlist is the trap: adding a key to the context dict does nothing unless the
  copy loop also names it, and nothing fails when you forget.
- **Order the gates by breadth, not by convenience.** The account check runs BEFORE the
  per-deployment governor: a deployment can be well inside its own caps while the account
  is already at its limit because of siblings.
- **Verify the semantics you're relying on, don't infer them from a passing test.** I read
  `evaluate_guardrails` (`>=` on open count, `mtm <= -abs(limit)`, priority order) and
  `guardrail_tick` (`trip()` + `_halt`) before trusting either. The staleness question
  ("is my per-cadence config snapshot safe?") only resolved by finding that the executor
  re-reads the latch fresh via `can_trade()` at the chokepoint — a snapshot that can only
  under-block, behind a fresh authoritative check, is fine. That reasoning is now a
  docstring, so the next reader doesn't re-litigate it.

**Dead ends / traps:**
- Source-contract tests must name the *enclosing* function: the `live_kwargs` allowlist is
  in `evaluate_active_deployments`, not `evaluate_deployment_on_close`. Two wrong guesses
  before grepping the `def` line numbers.
- `tests/test_live_deploy_governor.py` is `asyncio.run`-style, not `pytest.mark.asyncio` —
  appending in the wrong idiom breaks *collection* for the whole file, so every other test
  in it disappears rather than failing loudly.
- `_float()` does NOT sanitise non-finite values: `_float(float("nan")) == nan`. Any sum
  built from journal rows can be NaN.

**Status:** suite 3639 passed / 0 failed. C2, C4, H1, C3 are all closed — the pre-real-money
blocker list is CODE-complete. What now stands between the app and real money is
VALIDATION, not implementation: none of these four has been exercised against a live
broker, because the current IP is not registered with Flattrade.

---

## Session 2026-07-27 (cont.) — Item 6: what a capability inventory is actually for

**Core lesson: before searching harder inside a family, check whether the tool can express
anything outside it. Three expensive campaigns had a shared cause that no parameter sweep
could ever reach.**

`direction: "CE"|"PE"|"NONE"` (no side), `pnl_pts = exit - entry`, `side="B"` always. Every
one of ~600 premium-momentum configs, and the three families before it, was a variant
inside the single family the engine can express: PAY premium. The verdict's own line —
"gross points on the holdout are −798 **before a single rupee of friction**" — reads as a
scorecard but is really a *measurement*, taken from one side only.

**Confirmed approaches:**
- **State the counter-argument before the idea.** The naive inference from "long premium
  loses" is "so short it", and that inference is wrong: friction is two-sided, short gamma
  inverts the distribution into the many-small-wins/rare-ruin shape that specifically
  flatters a 20-month backtest, margin replaces premium outlay, and the risk machinery
  assumes max-loss = premium paid. Writing that *first* is what made the next step
  (defined-risk spreads only) forced rather than optional — and it is what made the
  warehouse measurement decisive instead of merely interesting.
- **Measure the load-bearing fact; never accept a manifest for it.** The whole
  recommendation hinged on strike depth. `capability.py` documents "ATM ±1 band" — and the
  same inventory had already caught that file being wrong about two other fields. Querying
  Mongo directly gave the decisive number (exactly ONE expiry per day, 100% of days;
  median 6/8/9 strikes) *and* proved `has_vix_history: False` false against 104,685 stored
  VIX candles. A stale manifest is not a cheap error here: it drives what the AI authoring
  wizard permits, so it was silently refusing rules against data that exists.
- **Let the measurement overturn the ranking.** The short side was the most interesting
  idea and it finished LAST — deferred to a procurement question — because the only
  defensible form of the experiment is the one the data cannot support. The cheapest
  option (pool three indices: 2.97× sample, zero engine changes) finished first. Ranking
  before measuring would have inverted this.
- **Delegate inventory, keep synthesis.** Three Sonnet agents did file-and-line and
  database inventory well and flagged their own uncertainty honestly (one explicitly said
  "recommend checking a live sample before relying on either claim" — and it was right).
  The judgement calls — what the counter-argument is, what the ranking should be, what to
  refuse — stayed with me. Two of their concrete claims I re-verified myself before
  building on them; both held.

**Dead ends / traps:**
- **Editing a doc by replacing a section HEADER leaves the old body stranded.** Replacing
  "## 4 …header + pending note" inserted new §4-§7 *above* the old §4 body, producing two
  §5s and two §6s. The first cleanup fixed only half of it. When restructuring a document,
  cut to the next *sibling* boundary, and re-`grep "^## "` afterwards — the header list is
  the cheap proof the structure is right.
- Assert on the boundary before deleting text (`assert "…" in s[:i]`) — a wrong index
  silently destroys the new work instead of the old.

**Standing conclusion recorded in the deliverable:** if pooled regime routing fails its
pre-registered criterion, that is a real result and the honest reading is that this app's
value is as a research and risk-control instrument rather than a source of directional
alpha. Loss avoidance is already banked: the survival gate has refused three families,
one of them sourced from a vendor PDF claiming +₹2.79L that assumed ZERO slippage.

---

## Session 2026-07-27 (cont.) — pre-flight checks before a research campaign

**Core lesson: before running a campaign on a new instrument, verify the constants that
silently scale every number it produces. A wrong lot size does not fail — it reports.**

Heading into the pooled-index campaign, my stored memory and an inventory agent disagreed
on lot sizes (BANKNIFTY 30 vs 35). Checking resolved more than the disagreement: there were
**two independent lot-size sources in the codebase**, and they disagreed with each other.
`option_backtest.py:750` reads the selected contract's `lot_size` (data-driven, correct);
`premium_momentum_backtest.py:342` and `premium_trigger_dispatch.py:194` read the hardcoded
`UNDERLYING_META`. NIFTY (65) and SENSEX (20) agree in both, so the bug was invisible for
two of three instruments — it only showed on the one nobody had ever backtested
(**BANKNIFTY: 0 backtest runs ever**, vs NIFTY 225).

**Confirmed approaches:**
- **Fix the architecture, don't assert the number.** I could not confirm the true current
  NSE lot: the broker MCP is unauthenticated on this IP and its login must never be called.
  So instead of hardcoding a different constant, I removed the hardcoding — both paths now
  resolve from contract data and agree *by construction*, and they track reality as the
  exchange revises lots. Being unable to verify a value is an argument for deleting the
  hardcode, not for guessing a better one.
- **Don't paper over the genuinely hard case.** BANKNIFTY really did carry 35 (Jul–Dec 2025)
  and 30 after, so a run spanning that boundary cannot be sized by any single number. The
  resolver takes the most recent expiry's lot *and returns a warning*, surfaced in the
  backtest summary. A silent "pick one" would have made every rupee figure in such a run
  quietly approximate.
- **Ask what a disagreement between two sources means, not just which is right.** The
  valuable finding was not "30 vs 35" — it was that two sources existed at all.

**A defect I introduced, caught by the clock:** my C2 transmit-fence test asserted the
*authorised* case while the fence deliberately uses a **fresh wall clock** (re-checking the
time is half its purpose — a deployment can cross its 15:00 IST late-entry cutoff during
broker round-trips). So the test passed when written and failed every afternoon. It
surfaced only because this run happened to be at 15:30 IST. The production behaviour was
correct and stayed unchanged; the clock is now injectable and only the test pins it.
**Any test whose subject reads `datetime.now()` is time-dependent until proven otherwise —
and a suite that runs green all morning is not evidence against it.**

**Dead ends / traps:**
- `live_trades` is EMPTY — zero real fills ever. So direction **C** (realized fill vs
  model friction) from the profit-leverage analysis is **not measurable**: paper trades
  carry `entry_slippage_pts`/`entry_spread_pts`, which are the friction model's own
  outputs, so measuring them against the model is circular. C is blocked behind a
  real-money session, itself blocked on a registered IP. Recorded rather than faked.
- Patching a test body by exact-match string replace failed twice on indentation. Read the
  real lines first; do not reconstruct them from memory.

---

## Session 2026-07-27 (cont.) — a deterministic test is not a tested property

**Core lesson: fixing a flaky test removes the symptom; it does not add the coverage the
flakiness was pointing at. Ask what the flaky assertion was *failing to assert*.**

The entry above recorded pinning the C2 fence's clock so the test stopped failing every
afternoon. Re-examining it showed the pin closed the symptom and left a hole: the suite
asserted only the *authorised* case (clock pinned PRE-cutoff), and **nothing anywhere
asserted that the fence refuses once the 15:00 IST cutoff passes** — which is the entire
reason the fence re-reads the clock instead of reusing `now_utc`.

Proved by mutation rather than by argument: reverting `auto_live.py:493` to the frozen
`now_utc` is caught by exactly **one** test — the new one. The other 54 in the file stay
green. So the C2 safety property could have regressed silently, opening a real position
minutes before the EOD square, with a fully green suite.

- **Mutation-test the safety-critical line, not just the failing one.** "The suite is
  green" says nothing until you have broken the code on purpose and watched it go red.
- **A pinned clock cuts both ways.** Pin it on BOTH sides of the boundary and assert each
  branch; pinning only the convenient side is how the untested half hides.

**Sweep for the same class — no other instance found.** `test_live_mode.py`'s cutoff tests
all pin fixed datetimes; the wall-clock read at `live_broker.py:1661` feeds a deployment
scan no test exercises (tests target the pure `compute_arm_state`); the three *duplicated*
`_in_market_hours` gates (`live_exit_monitor.py:23`, `live/live_position_guard.py:97`,
`live/live_sl_monitor.py:55`) are reachable only from `run()` poll loops that **no test
drives at all**; `resolve_token_expiry`'s fixture JWT `exp` is already two months past but
benign (returned verbatim, never compared against now). The **date-rollover** class is
empirically excluded: fixtures hardcode `2026-06-25` and still pass on `2026-07-27`, so no
hardcoded-fixture-vs-floating-clock coupling survives anywhere the suite touches.

Verified **3,649 passed / 0 failed at 15:52 IST** — i.e. with the after-cutoff AND the
outside-market-hours (>15:30) branches both live. The weekend path takes the same
`_in_market_hours` False branch. NB the docs baseline of "3,639" is stale: HEAD was 3,648
before this test.

**Dead end, recorded so it is not retried:** I tried to prove "green at any hour" by running
the suite under a faked process clock. Swapping `datetime.date` stack-overflows through
zoneinfo/pandas C paths; swapping `datetime` alone still crashes. The safe narrow variant —
rebinding only `app.*` module globals — would silently miss the function-level
`from datetime import datetime` that `mode.py` itself uses, so a green run would have been
false assurance. Abandoned rather than reported as proof. There is **no `freezegun` /
`time_machine` in the venv**; if an hour-sweep is ever genuinely needed, install one rather
than hand-rolling it.

---

## Session 2026-07-31 — promotion freedom is a capability boundary, not an evidence gate

**Core lesson: represent candidate availability explicitly. Job status, qualification,
objective score and parameter-dict truthiness are all lossy proxies for whether a concrete
configuration can execute.** A legitimate strategy can have zero tunable parameters, a
finite optimizer candidate can exist while its job is still running, and a disqualified
research result can still be technically executable. The promotion decision must inspect
the exact params+metrics tuple and keep research evidence separate from operator authority.

**Confirmed approaches:**
- One recursive finite-value check now guards optimizer candidates and deployment execution
  values, while the shared `Strategy.validate_signal` boundary guards every backtest,
  authoring smoke run and paper/live evaluation. This catches nested NaN/infinity instead
  of checking only the optimizer objective or a few monetary fields.
- Runtime competency is re-established at state transitions, not assumed from creation:
  strategy loaded, source SHA unchanged, instrument/timeframe supported, schema/ranges
  valid, and signal output finite. Resume and live-enable both repeat this check; live
  consent, capital, broker, account and transmit gates remain separate and unchanged.
- AI-authored Python is treated as untrusted executable logic: random/current-time APIs are
  blocked and the same canonical smoke input must produce the same outputs twice.
- Backend/frontend schema parity matters at the promotion seam. A shared indicator catalog
  prevents optimizer-only keys becoming deployment vetoes; nullable UI defaults are kept
  null instead of silently becoming zero; old deep links use exact fetch; acknowledgment
  errors return the wizard to the actionable step.
- For local browser verification, use the configured `localhost` origin. The production
  bundle bakes that backend origin; opening `127.0.0.1` creates a different browser origin
  and can mimic empty strategy data without exposing a product defect.

**Dead ends / traps:**
- `if params` is not a validity check: `{}` is the correct config for a zero-parameter
  strategy. Likewise, a terminal job status is not proof that no promotable snapshot exists.
- Objective-only finiteness is insufficient; params, nested metrics and emitted signals can
  independently contain non-finite values.
- Frontend default coercion, list-only deep-link resolution and a 400 acknowledgment dead end
  can nullify a correct backend policy.
- Repository-layout/source-contract tests cannot run inside the stripped backend image;
  run them on the host and deselect only those explicit cases from the in-container Motor
  route pass rather than describing the container subset as the whole suite.

---

## Session 2026-07-31 (cont.) — capability breadth is not the next bottleneck

**Core lesson: AlphaForge already has enough strategy, backtest, optimizer, paper and live
execution machinery to learn from the market. The next unit of value is a reproducible
prospective-evidence loop and a truthful decision surface, not another strategy family.**

**Confirmed approaches:**
- Separate three programs that answer different questions: product capability, edge
  research and broker/execution validation. A pass in one never substitutes for a pass in
  another.
- Freeze hypothesis, source hash, parameters, data manifest, costs, split boundaries,
  trial budget and kill rules before selection. Treat validation and one-use holdout as
  named states, not a generic `OOS` label.
- Make Dashboard a decision and workflow surface. Fix premium-result routing and bound the
  summary payload before adding new visuals; otherwise a polished page can display the
  wrong result more quickly.
- A live index chart does not require persistent candle storage: bootstrap the current day
  from Upstox intraday candles, aggregate subsequent ticks into a bounded in-memory ring,
  and stream only the selected instrument. Label the view transient and expose stale/feed
  state.
- Use binary gates for every roadmap slice: deterministic replay, immutable evidence hash,
  one-use holdout audit, fixed-config forward cohort, and broker reconciliation.

**Dead ends / traps:**
- The Dashboard's static phase/roadmap text is stale and cannot be used as project state.
- `result.metrics` is not authoritative for premium-native backtests; use the dispatch-aware
  option result envelope.
- A latest-tick map is not chart history. Without an intraday bootstrap or an ephemeral bar
  buffer, a live chart starts empty and cannot recover after reconnect.
- Adding stocks is not a universe toggle: instrument assumptions cross data, strategy,
  optimizer, backtest, expiry/strike metadata, live execution and frontend selectors.
- More optimizer objectives, charts or strategy plugins cannot turn in-sample selection
  into evidence. Historical option coverage also cannot manufacture point-in-time spread,
  depth or IV data that was never recorded.

---

## Session 2026-08-01 — a normalized metric is a cross-path contract

**Core lesson: changing an optimizer metric is incomplete until serial evaluation, every
parallel worker, persisted/resume evidence and the UI all carry the same definition.** The
unitless drawdown fix passed its new direct test but the full suite caught WFO worker parity:
the serial evaluator emitted `pnl_abs_sum` while both fork workers dropped it. That omission
would have ranked risk objectives differently by execution mode even though each path looked
internally plausible.

**Confirmed approaches:**
- Classify audit rows against current source before editing. MED #14/#23 were not open
  defects; they were stale descriptions already covered by the HIGH #18/#28 regressions.
  Documentation closure was the correct fix.
- Keep surviving defects in isolated commits with their own red/green proof. It made the
  full-suite parity regression attributable to #29 without entangling controls, robustness
  or early-stop behavior.
- Use an inclusion projection for bounded API summaries. Excluding known root arrays missed
  premium-native nested arrays; selecting only scalar fields reduced the live response from
  62,924 to 1,857 bytes and preserved the authoritative option envelope.
- Reuse the production ingestion helper in preflight. Check is read-only; Ingest calls the
  same audit → fill → re-audit function as sync/async backtests, so a certification panel
  cannot silently diverge from the workflow it certifies.
- Review delegated changes from a clean repo-root command. One delegated dashboard test
  passed only when another test had already modified `sys.path`; isolated execution exposed
  the hidden dependency before commit.

**Dead ends / traps:**
- Do not stop at a new objective's unit test. Run serial/parallel exact-dict parity and the
  whole suite; execution-mode-dependent rankings are more dangerous than an obvious crash.
- Do not guess test filenames. Two guessed WFO files did not exist and caused zero tests to
  run; enumerate with `rg --files` and then invoke only returned paths.
- Run repo-root commands from the repo root. A compile command launched from `frontend/`
  could not resolve `.venv`; this was command-context failure, not source evidence.
- Browser origin is part of the test. Opening `127.0.0.1:3000` while the bundle targets
  `localhost:8001` creates a CORS failure and fake empty Dashboard. Re-run on the configured
  canonical origin before diagnosing product behavior.
- Backend-container source-contract tests that read `/frontend` or `/backend/server.py` are
  repository-layout checks, not container behavior. Keep them green on the host and deselect
  only those exact cases from the container behavioral gate.
