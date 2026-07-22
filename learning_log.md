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

### Open items carried forward

1. Safety-fix sprint (pending user decision on scope): H2+H3 (trivial), C1 loopback
   binding (trivial), C4 resume re-consent, C2 transmit fence, C5 browser verification,
   H1 CAS, C3 account-global wiring.
2. User's items 2-8: lazy-leg Phase 5, strategy-builder audit, live-page redesign, new
   strategy plugins, profit-leverage ideas, end-to-end audit, handover docs.
3. Uncommitted Codex diff (~2.7k lines, 50 files) needs a commit decision + full suite run.
4. H4/H5 verification.
