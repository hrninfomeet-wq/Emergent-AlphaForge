# AI-agent takeover prompt (as of v0.58.0 + unreleased Stage-1 integrity, 2026-08-01)

_Copy-paste the block below as the first message to a new AI agent taking over this repo.
It is self-contained; keep it in sync when the app state changes materially._

---

You are taking over active development of **AlphaForge Trading Lab** — a local-first
research + forward-test + live-execution app for Indian index options (NIFTY / BANKNIFTY /
SENSEX). Repo: `Emergent-AlphaForge` (GitHub: `hrninfomeet-wq/Emergent-AlphaForge`).
React (CRA + craco) frontend :3000, FastAPI backend :8001 (**all routes under `/api`**),
MongoDB (motor), Docker Compose. **Upstox** = market data; **Flattrade** (Noren/PiConnect)
= live broker. This app can place REAL-MONEY orders — treat every change to
`backend/app/live/`, the deployment/guard/recovery seams, or the broker-token path as
safety-critical.

## Orient yourself in this order (before writing any code)

1. `docs/HANDOFF.md` §2 — START HERE. §2.0 is a 60-second orientation table; §2.1 lists the
   two traps that will bite you immediately.
2. `docs/BACKTEST_INTEGRITY_AUDIT.md` — **read before trusting any backtest or optimizer
   number, and before touching `optimizer.py`, `option_backtest.py`, `runtime.py` or
   `premium_trigger_dispatch.py`.** Its four verified HIGH findings are fixed in the
   current working tree; all 8 confirmed MED findings are also closed locally. One
   disputed LOW remains separate.
3. `docs/LIVE_VALIDATION_PLAN_2026-08.md` — **read before ANY market session.**
   Twelve changes landed on the real-money path 2026-07-29 → 08-11 and EIGHT have
   never run in a market session; that plan exercises exactly those.
4. `docs/STAGE1_INTEGRITY_SESSION_HANDOFF_2026-08-01.md` — the latest session's commit
   map, verification ledger, runtime snapshot, file routing and exact resume sequence.
4. `docs/AGENT_TODO.md` — the live board. Its ★ START HERE block names the three
   highest-value next actions.
5. `docs/DEVELOPER_GUIDE.md` — run/build/test, live-safety model (§E, read twice),
   warehouse model, India rules, **Gotchas (§H — read fully; every item was paid for)**.
6. `docs/ARCHITECTURE.md` (module map, Mongo collections, L0–L3 gate chain) and
   `docs/STRATEGY_DEPLOYMENTS.md` (deployment/guard model) as needed.
7. `CHANGELOG.md` top entries. **0.56.0 is mandatory before touching any live seam** — it
   removed the ARM ceremony and lists four silent regressions a naive removal would have
   shipped. **0.58.0** is mandatory before touching backtest reporting.
8. `docs/flattrade-mcp-integration.md` **before touching the broker token path or using the
   Flattrade MCP tools** — the broker account is shared with a separate MCP server.

## Where the app stands (v0.58.0 + unreleased Stage-1 integrity, 2026-08-01)

All subsystems are built and integrated on `main` (sole branch): data warehouse, Backtest
Lab, Optimizer (honest OOS + survival gate), Strategy Library with AI authoring, paper
trading, gated live execution with a layered safety stack (confirm-flat guard, kill
switches, per-token recovery), the premium-momentum family including Phase 5B multi-leg
execution, and Flattrade-MCP session sharing.

- **Git:** local `main` and `origin/main` are synchronized at the published Stage-1
  checkpoint, containing the HIGH fixes, promotion-freedom policy, next-stage roadmap and
  completed Stage-1 integrity work. One branch, zero stashes.
  Two archive tags preserve retired work (`archive/live-deploy-wip-2026-06-25`,
  `archive/journal-wip-2026-06-21`). Always run `git log origin/main..main --oneline`
  before describing "current state".
- **Host test baseline: 4,354 passed, 4 xfailed, 0 failed** (4,358 collected) —
  `.venv\Scripts\python.exe -m pytest tests -q` from the repo root. Motor/route tests run
  **inside the backend container** instead (`docker cp tests/. alphaforge_backend:/app/tests`
  then `docker exec -w /app alphaforge_backend python -m pytest tests/<file> -q`).
  Confirm this baseline on your machine before changing anything.
- **Real money has never traded.** `live_trades` is empty. All four pre-real-money code
  blockers (C2 transmit fence, C4 breach demotion, H1 compare-and-swap, C3 account-global
  caps) are **fixed**, but none has been exercised against a live broker: the current IP is
  not registered with Flattrade. The remaining gate is **operational, not code**.
- **No strategy has a demonstrated edge.** Three independent campaigns have now failed a
  holdout — premium-momentum (`docs/PREMIUM_MOMENTUM_EDGE_VERDICT_2026-07.md`), the pooled
  regime study (`docs/POOLED_REGIME_VERDICT_2026-07.md`), and `algotest_option_buy_nifty`
  (+60.83% / Sharpe 4.49 in train Jan–Apr → **−1.65% / Sharpe −0.27** on the untouched
  May–Jul holdout). **Edge hunting is parked by explicit user decision.**
- **The active program is the capability phase** (`docs/CAPABILITY_PHASE_PLAN_2026-07.md`,
  board item 9): make backtesting, paper and live fully usable without constraints, and
  make a strategy described in plain words deployable end to end. Phase 0 and Phase 1 are
  complete.

**The live authorization model (unchanged since v0.56.0):** deploying a strategy in LIVE
mode *is* the authorization — there is no per-deployment ARM and no `LIVE_GUARD_ARMED`.
Authorization = `deployment.mode == "live"` AND broker connected AND before the 15:00 IST
entry cutoff (`backend/app/live/mode.py::is_deployment_live_allowed`).
`POST /deployments/{id}/live/enable` is the ONLY writer of live mode; it carries the seven
preflight checks and REQUIRES the risk caps (it is also their only writer). The software
exit guard ALWAYS transmits. `LIVE_AUTOPLACE_ARMED` remains the single master switch for
automated entries.

## Non-negotiable standing rules (user decisions — do not relitigate)

- **Never place, square, modify or authorize a real order yourself** — through the app OR
  the Flattrade MCP's write tools — and never flip a deployment to live mode. Going live is
  exclusively the user's manual act.
- **Never call the Flattrade MCP's `login` / `logout` tools.** One API key ⇒ one redirect
  URI (AlphaForge owns it) ⇒ the MCP cannot OAuth on its own, and Flattrade is
  last-login-wins, so a second login would silently kill AlphaForge's live session. The
  user's AlphaForge login is the ONLY login. Read tools are fine and genuinely useful as an
  independent broker-truth witness. Stale MCP session ⇒
  `backend/scripts/resync_mcp_session.py --clean`. Never refresh Flattrade OAuth while
  `LIVE_AUTOPLACE_ARMED` is on.
- **Never create a second Flattrade API key.** API V2 = one key per account; a second
  requires the paid registered-algo tier (₹5,000+GST per exchange). The user has declined.
- **Commit freely at green milestones; push ONLY with per-changeset user approval.**
- **Never commit** `.env`, tokens, credentials, or MCP client configs.
- **Do not add any new live-arming gate** — ride the existing mode/env/cap chain
  (`DEVELOPER_GUIDE.md` §E). Extra "safety" gates have been explicitly removed TWICE on user
  request. Propose, don't impose.
- **Never restore an authorization field to `risk.live`.** It is a pure CONFIG sub-doc
  (caps + catastrophe band). `mode` authorizes; anything else is a second source of truth.
- **Honesty over convenience**: report failing tests verbatim; a review/verification step
  that dies incomplete is UNVERIFIED, not passed; prefer refuse-with-explanation over silent
  degradation; never let a `| tail` pipe mask a pytest exit code before committing.
- **Rebuild the backend container after backend edits** (code is baked into the image).
  Browser-verify frontend changes with **Ctrl+Shift+R** (the CRA bundle goes stale;
  client-side navigation does not reload JS).
- Verify India-market facts (lot sizes, expiry cadence, holidays) against `instruments.py` /
  `nse_calendar.py` / `dte.py` — **never from memory**; they rotate.

## Load-bearing technical invariants (each one closed a real bug)

**Reporting / research**

- **A backtest result has TWO envelopes.** ORDINARY strategies put the truth in
  `result.metrics` / `result.trades`; **PREMIUM-NATIVE** ones (whose `evaluate()` is a
  deliberate inert stub) leave those as a **zero-filled stub** and put the entire real
  result in `result.option_backtest.*`. Reading the wrong one yields a plausible wrong
  answer — it caused eight separate user-visible defects. Route on
  `option_backtest.dispatch == "premium_trigger_config"` (backend `is_premium_trigger_strategy`,
  frontend `isPremiumNative()` / `resultKpis()` in `lib/backtestMetrics.js`).
- **`bool(float("nan")) is True`.** Never truth-test a pandas cell. This single fact keyed
  every legacy option candle as the string `"nan"` and invalidated **every paired-option
  backtest saved before 2026-07-30**. Use `isinstance(x, str) and x.strip()`.
- **A certification tool must reproduce the lookup it certifies.** The coverage preflight
  queried Mongo directly and reported 100% coverage on a run that paired 4%.
- **Objectives monotonic in something you don't want maximised will maximise it.**
  `net_pnl_inr` is monotonic in `lots` AND in trade count; the optimizer invented a 0–100
  range for `lots` and drove it to 100. Sizing/risk knobs are pinned via
  `NON_ALPHA_PARAM_NAMES`; parameter bounds must come from the strategy, never be invented.
- **Threshold booleans hide signal.** A 10-point walk-forward cut hid 9.44- and 9.65-point
  decays on two strategies that then failed out of sample. Report the signed magnitude.
- **Option-leg joins:** `index_trade_id` must always be a position in the **full** spot-trade
  list. `simulate_paired_option_trades` numbers whatever list it receives, so any caller that
  filters first (DTE filter) MUST remap afterwards. Join by id or `signal_entry_ts`, **never
  by array position**.

**Live execution**

- `live/executor.py` is the sole real-order ENTRY chokepoint; **broker-confirmed-flat** is
  the sole position finalizer (never place-accept).
- A broker **read failure is UNKNOWN, never flat** — an empty position book must never be
  read as "position closed".
- Recovery resolves premium-momentum leg symbols exclusively via the broker order book's
  `norenordno→tsym` join. Persisted `trading_symbol` is **Upstox-space** and must never be
  matched against the **Noren-keyed** position book; unresolvable ⇒ skip, never mark exited.
- All IST `HH:MM` comparisons go through `premium_momentum.normalize_hhmm` — raw string
  compares are **fail-open** for unpadded times (a cutoff that never fires).
- Any new option-stream subscription rebuild site must union in `premium_pin_keys()`.
- Paper exits ride `live_exit_monitor.py` and never touch `premium_locks`; the 5B exit
  machinery (lazy arming, `exit_time`, per-leg finalize) is **live-guard-only**.
- **Emergency stops must write `status="PAUSED"`.** `evaluate_all` only iterates
  `{"status": "ACTIVE"}`, so PAUSED is the authoritative halt; flattening alone lets the next
  confirmed signal re-enter.
- **Entry strict / exit permissive.** A too-strict exit gate STRANDS a position.
- **NSE/BSE reuse exchange tokens across expiry cycles**: 2-part canonical instrument keys
  can map to two different contracts. Any lookup by 2-part key must stay time-windowed or
  expiry-constrained.
- **A Mongo selector over a field nothing writes any more returns empty SILENTLY.** When a
  field's writer is removed, grep every selector, projection and index that reads it.
- Test fakes for broker interfaces must model the **real two-symbol-space world** (Upstox vs
  Noren strings deliberately different) and must APPLY the query they are given rather than
  hardcoding one selector's semantics.

## Tips & tricks learned building this (save yourself the rediscovery)

- **A source-contract test asserting a STRING appears in a file cannot tell a USE from a
  BINDING.** Three `NameError`s shipped to the user that way. `tests/test_no_undefined_names.py`
  now runs pyflakes across `backend/app` with a small verified baseline — it has since caught
  two more before any test ran. Keep it green.
- **Test frontend logic by EXECUTING it** (`node --input-type=module`), not by grepping JSX.
- **Fix every path, not the one you're looking at.** A premium walk-forward fix landed on
  `/backtest/run` while the UI calls `/backtest/start`; the user kept the defect I had
  reported fixed. Cure: one shared function, plus a test asserting both callers delegate.
- **Verify each call site of a bulk edit.** A `sed -i .../g` hit two call sites; only one was
  checked; the other referenced a local that did not exist in that scope.
- **Spot-check breadth before declaring a refutation.** A `best_so_far` claim was refuted
  after checking 2 of **5** writers; the other three were unguarded and the claim was right.
- **Symptom ≠ location.** A "trading logic" bug has twice turned out to be a display/join bug
  or an infrastructure gap (the tick→candle roller not started ⇒ "0 trades all day"). Verify
  plumbing and row/id alignment before diving into strategy math.
- **Reproduce against the real DB before theorizing.** `pymongo` is in the repo venv;
  querying `backtest_runs` / `options_1m` directly settles most "is the data wrong?"
  questions in one script. Write throwaway scripts to the scratchpad, not the repo.
- **Prove a regression test fails pre-fix.** Stash-free method: `git show HEAD:<file> > <file>`,
  run pytest, then restore. Do **not** use `git stash` casually here.
- **The optimizer saves multiple sibling run docs per job** (different option configs, seconds
  apart, same display name). When a user reports something odd in a saved run, find *all*
  matching docs — the one they opened may not be the one you inspect.
- **Whole-suite-in-container always fails path-contract tests** — judge container runs by
  motor/route results only; run the full suite on the host.
- **`.venv` is pandas 3.0.3**: `date_range` yields µs resolution, so `asi8 // 1_000_000`
  silently gives epoch **seconds** — pin the unit first with `as_unit("ms")`.
- **Tailwind `min-h-0` loses the cascade on flex children** — use inline `style={{minHeight: 0}}`.
- **sklearn is load-bearing** via optuna's lazy import even though nothing imports it
  directly — don't "clean it up".
- **Optimizer analysis now honors pause/cancel/budget** during option re-rank and WFO final
  analysis, retaining completed finite evidence. Heavy option re-ranks still want
  `opt_workers=1`; do not restart the backend merely to apply a requested stop.
- **Multi-agent verifier runs can die on spend limits.** A panel with no completed reviewer
  is UNVERIFIED, not passed. Recover partial work from
  `.../subagents/workflows/<run>/journal.jsonl` — the result key is `result`, not `value`.
- **Windows/Docker:** host probes must dial `127.0.0.1`; `localhost` resolves to `::1` first
  and stalls ~2s against IPv4-only Docker.

## Current next steps (priority order, unless the user redirects)

1. **Market-hours Gate A** from `docs/phase5b-market-validation-runbook.md` in strict
   **PAPER + READ-ONLY** posture. Do not enable live. This is the highest-value remaining
   validation and cannot be completed while the market is closed.
2. **Stage 2 Dashboard decision surface** from `docs/NEXT_STAGE_ROADMAP_2026-07.md`,
   after reviewing the completed Stage-1 evidence. Do not add live order controls to it.
3. **Real-LLM authoring acceptance** after the capability work is accepted: author →
   install → backtest → optimize → paper. Do not spend an untouched holdout or enable live
   merely to prove plumbing.
4. After a clean paper day and registered static IP: the **1-lot live validation day** (`docs/live-readback-checklist.md`
   + runbook §6) — the user performs all arming. Blocked on a Flattrade-registered static IP;
   the approved host design is `docs/durable-static-ip-deployment.md`.
5. Continue the **capability phase** (`docs/CAPABILITY_PHASE_PLAN_2026-07.md`): the remaining
   high-value additions to make backtest/paper/live usable without constraints.
6. Known deferred items: the four planned live-safety fixes in
   `docs/superpowers/plans/2026-07-25-live-safety-four-fixes.md`; the 41 UNVERIFIED open
   items in `docs/live-cockpit-audit-2026-07-25.md`; per-leg chips on the Live strip;
   `opt_workers>1` for premium_momentum.
7. **Do not start new edge research.** It is parked. If the user reopens it, follow the
   pre-registration discipline in `docs/PREMIUM_MOMENTUM_EDGE_VERDICT_2026-07.md`
   (three-way chronological split, costs mandatory, untouched holdout, kill criteria written
   BEFORE running) and use more data rather than more tuning — the warehouse reaches back to
   2024-11-25.

Start by reading the docs in the order above, run the host test suite to confirm the
**4,354 passed / 4 xfailed** baseline on your machine, check `git log origin/main..main`,
and give the user a short readback of the current state plus your plan for their first
request before changing anything.

---
