# AGENT TODO — live plan, status, and takeover state

> **Purpose:** the single continuously-updated source of truth for the current work
> program, so ANY agent (Claude, Codex, Gemini, human) can take over with zero loss of
> context. **Update this file after every completed work unit** — flip statuses, add
> notes, never delete history (strike through instead).
>
> Companion files: [`learning_log.md`](../learning_log.md) (lessons per session, verified
> audit-finding evidence table) · [`docs/HANDOFF.md`](HANDOFF.md) (architecture/state
> entry point) · [`STAGE1_INTEGRITY_SESSION_HANDOFF_2026-08-01.md`](STAGE1_INTEGRITY_SESSION_HANDOFF_2026-08-01.md)
> (latest completed-session checkpoint) · `CHANGELOG.md`.

**Last updated:** 2026-08-01 (Codex — Stage 1 integrity complete; takeover package refreshed)

---

## ★ START HERE — the state of play on 2026-08-01

**Repo:** `main` and `origin/main` are synchronized at the published Stage-1 checkpoint; one branch, zero stashes.
**Suite:** 4,354 passed · 4 xfailed · 0 failed (4,358 collected).
**Version:** v0.58.0 + unreleased Stage-1 integrity work.

### Where the project actually is

The **capability phase** (board item 9) is the active program: make backtesting, paper
and live fully usable, and make a plain-English strategy deployable end to end. Phase 0
(unlock built-but-unreachable features) and Phase 1 (config-block generalization) are
COMPLETE. **Edge hunting is parked by explicit user decision** — do not start a new
strategy search.

Real money is blocked on **operations, not code**: all four pre-real-money blockers
(C2/C4/H1/C3) are fixed, but nothing has ever been validated against a live broker
because the current IP is not registered with Flattrade, and `live_trades` is empty.

### The three highest-value things to do next

The four verified HIGH and all eight confirmed MED optimizer findings are **DONE in the
current working tree** with regression proof and the full suite green. MED #14/#23 were
stale audit rows already subsumed by HIGH #18/#28; disputed LOW #31 remains separate.

1. **Market-hours validation** (board row V) — still pending, still the main event, and
   the only thing that converts "the code is correct" into evidence. Posture stays
   **PAPER + READ-ONLY**.
2. **Stage 2 Dashboard decision surface** in
   [`NEXT_STAGE_ROADMAP_2026-07.md`](NEXT_STAGE_ROADMAP_2026-07.md): runtime data trust,
   experiment/deployment attention and continuation actions. Keep the Live Broker cockpit
   as the only order-control surface.
3. **Run an AI-authored strategy end to end with a real LLM call** after the capability
   work is accepted: author → install → backtest → optimize → paper. Do not spend an
   untouched holdout or enable live merely to prove plumbing.

### Non-negotiables a new agent must not rediscover the hard way

* **Never** call `mcp__flattrade__login` / `logout`, and never place/modify/cancel an
  order through the MCP. It shares AlphaForge's single API key; last-login-wins would
  invalidate the app's own token. Read tools are fine. Recover a stale MCP session with
  `backend/scripts/resync_mcp_session.py --clean`.
* **Push only with per-changeset user approval.** Commit freely; ask before pushing.
* **A saved backtest from before 2026-07-30 is not evidence.** Re-run it.
* **Read `result.option_backtest.*` for premium-native runs** — `result.metrics` is a
  deliberate zero stub and reading it produces a plausible wrong answer.
* No strategy has a demonstrated edge. Three campaigns have failed a holdout. Report
  results honestly; never promise profitability.

## 0. Standing decisions (user-confirmed 2026-07-21 — do NOT relitigate)

1. **Deployment freedom is policy.** Any saved/optimized preset may be deployed to paper
   or live after express consent + warning acknowledgment. Evidence gates (forward
   validation, quality warnings) are ADVISORY ONLY — never hard blocks. This is already
   implemented: consent override at `backend/app/routers/deployments.py` (~line 1095,
   `accept_unvalidated_live`); paper create gated only by `acknowledged_warnings`.
2. **The Codex diff is the baseline.** The ~2.7k-line uncommitted ChatGPT/Codex session
   work (consent-override live gate, forward-validation advisory, option-data
   provenance/integrity, docs, tests) is committed as-is, fixes land on top. No revert.
3. **The pre-real-money code blockers are closed.** H2/H3/C1/C2/C4/H1/C3 have landed;
   do not treat green code as broker validation. The remaining prerequisite is operational:
   registered static IP, market-hours PAPER/read-only Gate A, then a user-authorized live
   readback if the user decides — see §2 and the Stage 1 session handoff.
4. **Current priorities:** market-hours PAPER/read-only Gate A → Stage 2 Dashboard decision
   surface when Gate A cannot run → real-LLM author/install/backtest/optimize/paper acceptance.
   Edge research and live activation remain explicitly decision-gated.
5. **Checkpoint work into small green commits.** Use scoped parallel review only when the
   user requests orchestration and the work units are independent; review every delegated
   result against source and isolated tests before accepting it.
6. **Push policy:** commit locally; push only with per-changeset user approval
   (long-standing project rule).
7. **Broker safety (permanent):** never call the Flattrade MCP login/logout; never
   place/modify/cancel broker orders from an agent; AlphaForge's own OAuth is the only
   login. Never refresh Flattrade OAuth while `LIVE_AUTOPLACE_ARMED` is on.

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
| C2 | Transmit fence (stale authorization) | ✅ DONE `62e9457` | Executor Gate 7: async `recheck_fn` invoked AFTER the armed check and BEFORE the throttle, so NO await remains before transmit; fails CLOSED. auto_live supplies it — re-reads the deployment + current clock, and requires `status == ACTIVE` **and** `is_deployment_live_allowed` (pause writes only status, so the status check is load-bearing). Blocks as `stale_authorization:<why>`. 10 tests; suite 3,623/0. NOT yet market-validated. |
| H1 | Compare-and-swap transitions | ✅ DONE `f3aa9cf` | `/live/enable` write is now conditional on `{id, status: ACTIVE, updated_at: <read value>}` (optimistic-concurrency token) → 409 `deployment_changed_during_enable` if a Stop/pause/archive landed during the preflight. `_set_deployment_status` switched from read-modify-`replace_one` (whole-doc clobber) to a targeted `$set`, and stays UNCONDITIONAL by design: safety transitions must always land, so a concurrent Stop beats a late Enable. 5 tests; suite 3,628/0. NOT market-validated. |
| C3 | Account-global caps | ✅ DONE 2026-07-27 (`d8ef559`) | **The last pre-real-money blocker is closed.** New `check_account_caps()` in `live_deploy_governor.py` queries `live_trades` with NO deployment filter → account-wide open count + today's realized+unrealized, then delegates the verdict to the EXISTING pure `evaluate_guardrails(mtm, open_count, config)` so account semantics live in exactly one place. Refuses as `account_<action>`; on `broker_stop_loss` it calls `engine.guardrail_tick(...)` — which trips the latch (`_config_store.trip()`) + halts the engine, finally giving `guardrail_tick` its FIRST production caller. Honours the `blocked_until_reset` latch via `is_entry_blocked`. Wired ahead of the per-deployment governor in `auto_live` (account is the broader constraint); config threaded `build_live_deploy_context` → evaluator allowlist → `auto_live_trade_for_signal(account_safety_config=...)`, with a source-contract test so the gate can never silently become dead code. **Adversarial find, fixed:** delegating blindly meant one NaN in a journal row (json.loads accepts NaN) hit evaluate_guardrails' non-finite fail-safe → `broker_stop_loss` → latch tripped + engine halted until a human reset — a DATA DEFECT escalating into an account-wide halt. Now split: non-finite MTM → refuse entry as `account_exposure_invalid`, pause=False, no engine call. 10 tests (6 unit + 3 orchestrator wiring + 1 contract). v2 (atomic reserve-then-place) still deferred. |
| C | (was: deferred pre-real-money fixes — C2/H1/C3 ALL DONE) | ✅ COMPLETE | MUST land before first real-money session — §2 |
| 2 | Lazy-leg contingency (Phase 5 design → ship) | ✅ DONE | Was already shipped in backtest+live; built the only gap = **paper-mode lazy arming** (`ab453fa`) + H4 nullable-param deploy fix (`3639009`). Suite 3,549/0. See §3 item 2 |
| 3 | Strategy builder + AI authoring audit/completion | ✅ DONE | H5 preset/backtest validation parity (`10f8ce7`) + AI-install file rollback (`6e8861d`); wizard audited = already robust. Suite 3,557/0. See §3 item 3 |
| 4b | ↳ Page audit + fixes (2026-07-25) | ✅ AUDIT COMPLETE | All 5 dimensions done (4 by agents, data-null-safety inline after both workflow runs died on the usage limit). Register: `docs/live-cockpit-audit-2026-07-25.md` (66 findings, 23 fixed). Landed `3007f7d`,`58c158d`,`91fa367`,`bbafd54`: drawer clipping (the reported bug), ExecutionStateStrip regression, duplicate ticker, clipped dialogs app-wide, drawer a11y/inert, honest unavailable states, Day-Stop wiring, guard fail-safe, 2 money-figure semantic inversions. **4 deferred safety items planned**: `docs/superpowers/plans/2026-07-25-live-safety-four-fixes.md` |
| 4 | Live-trading page redesign | ✅ DONE (both phases) | **Phase 2 landed 2026-07-22**: read-only `GET /market/analysis` engine (`market_analysis.py` pure primitives + `market_analysis_build.py` assembly, ~8s single-flight cache) + `GET /live-broker/holdings`; MarketPulse (structure/regime meter/confidence/multi-TF trend/S-R range bar), MarketAnalysis (PCR·max-pain·IV-rank·straddle·net Δ,Θ + chain) and the Holdings tab all wired. Honest degradation everywhere (PCR suppressed without OI; IV rank declares `vix_proxy`). Commits `e0fb250`,`df6ebe3`,`afbd24b`. Suite 3,610/0; verified against live data. Phase-1 detail below |
| 4a | ↳ Phase 1 (shell) | ✅ DONE | Design+plan approved+committed (`c524ddf`,`e94d9cc`). **Phase 1 SHELL BUILT + Chrome-verified** on branch `feat/live-cockpit` (`3511874`): always-on cockpit (command bar + market-status pill + Upstox/Flattrade connection module + MarketHeader ticker), always-on core (risk KPIs, positions, kill, guard, quick-trade, deployment summary), config drawer (deployments/backstop/overall), tabbed account panel (Funds/Holdings/Orders/Trades). LiveDashboard retired→liveHelpers.js; 3 tests repointed; 7 new contract tests; suite 3,564/0. Historical note: the original row ended with **Phase 2 PENDING**; Phase 2 was later completed and is recorded in row 4 above. |
| V | **Market-hours validation** | ⏳ NEXT MARKET SESSION | Plan (deleted after the session; recover with `git show 23ccfed:docs/market-session-plan-2026-07-27.md`). Phase-5B paper validation is STILL PENDING and remains the main event. Posture: **PAPER + READ-ONLY, do NOT enable any live deployment**. Historical note: this row previously said C3 was open; C3 is now closed (row C3) and the remaining gate is runtime validation. Tier A = cockpit/analysis panels on live data (PCR + max-pain should stop being suppressed once the stream runs in FULL mode). Tier D remains deferred to the 1-lot live day: Item 1 + the C2 fence both need a real transmit. |
| 5 | New strategy plugins / edge hunting | ⏸ **PARKED BY USER 2026-07-27** | Direction B ran and was **KILLED AT VALIDATION** (`docs/POOLED_REGIME_VERDICT_2026-07.md`, `d6ef472`): 0/36 NIFTY configs had positive GROSS on train; every survivor was SENSEX-only; holdout NEVER touched and stays clean. **User decision: stop going deeper on edge findings.** Strategy hunting (incl. internet research for an index-option-BUYING edge) is deferred to a LATER phase, explicitly after the capability work below. The one open research question — whether to spend the clean holdout on a SENSEX-only retest — stays OPEN and my recommendation stands: DON'T (a survivor is ~₹380/month on 1 lot; friction is a % of premium so lots scale reward and cost together). |
| **9** | **CAPABILITY PHASE (new user priority 2026-07-27)** | ➡ **ACTIVE** | User's stated goal, verbatim intent: (a) make **backtesting, paper trading and live trading fully usable WITHOUT CONSTRAINTS** and fit to hand to a user; (b) build the **strategy builder** so a strategy defined in PLAIN WORDS becomes a plugin that backtests, optimizes, and deploys to paper and/or live. Edge hunting comes AFTER. Focus = high-value additions. Plan being assembled from two audits (authoring pipeline end-to-end; backtest/paper/live friction). |
| 9.0 | **Phase 0 — unlock built-but-unreachable capability** | ✅ **COMPLETE** 2026-07-27 (`e1bfd4c`, `6d89370`) | Four backend features were fully built + tested with **ZERO frontend callers**. **0.1 safety-latch reset** — `blocked_until_reset` halts ALL live entries and never self-clears; the reset endpoint had no caller, so the only exit was a raw API call. **Newly urgent because C3 gave `guardrail_tick` its FIRST production caller that same day** — the latch became trippable and I opened that reachability. Backend now records `latched_at`+`latched_reason` in the SAME write as the flag (they can never disagree); `reset()` clears provenance so a stale cause can't mislead the next operator; `put_config` still refuses all three keys so a halt can't be relabelled. Banner is two-step (clearing re-authorises real money). **0.2 recovery banner** — `/live-broker/recovery-status` existed to drive a UI strip per its own docstring; severity now follows exposure (unrecovered + open positions = danger). **0.3 deploy from a backtest run** — backend always accepted `source_type="backtest_run"` with full H5 validation parity; the wizard never offered it, forcing a save-a-preset detour on EVERY deploy. Now a third source + Deploy button + guarded `?backtest=` deep link. **0.4 pipeline chips** — `/strategies/{id}/pipeline` was built to power exactly these; distinguishes `live_ever_count` from `live_armed_count`. All contract tests assert components are **MOUNTED, not merely imported** (how `ExecutionStateStrip` was silently dropped). Suite **3682/0**, frontend build clean. |
| 9.1 | Phase 1 — config-block generalization (strategy builder) | ✅ **COMPLETE** 2026-07-28 (`1abc3a9`) | All 7 steps. `classify_rule` promised BUILDABLE_NOW for premium-trigger concepts and NOTHING could build them; now end-to-end. **1** dispatch routes on CONFIG PRESENCE + fixed a silent 6-field loss (`stop_pts`/`target_pts`/`trail_x`/`trail_y` were dropped by `merged_params`' allow-list while the run reported numbers as if applied) + split absent-vs-invalid. **1b** optimizer ×5 + coverage preflight route on `is_premium_trigger_strategy` (literal count in optimizer 6→0; predicate matches exactly the 1 strategy the string did, measured across all 12). **1c** classifier stopped promising fields that don't exist (`expiry` never existed; `side` had no `BOTH`) — ~51 tests touched `classify_rule` and none pinned message text to the schema it cites. **2** deployment carries a validated `premium_trigger` block, refused at CREATION. **3** Track B routes on capability, configured by the deployment — a block can NEVER flip a strategy's capability, which is what stops it silently bypassing an ordinary `evaluate()`; principle recorded: **entry strict / exit permissive** (a too-strict exit gate STRANDS a position). **4** `StrategySpec` emits a config; end-to-end test proves the generated plugin IS premium-native. **5** both generators taught; field list DERIVED from the model. **6** wizard carries + displays it (fixed silent data-loss: the config was discarded at Install). **7** paper honours `exit_time`; sizing replays the config's lots. **Suite 3902/0; the `premium_momentum` parity test stayed green and untouched throughout (invariant #1).** NOT yet validated against a live broker, and no AI-authored premium strategy generated end-to-end with a real LLM call — both are validation, not implementation. Log deleted after completion; recover with `git show 23ccfed:docs/PHASE1_CONFIG_BLOCK_LOG.md`. |
| 9.2 | Four verified HIGH optimizer defects + promotion-freedom policy | ✅ **DONE 2026-07-31, published 2026-08-01** | #11 one finite-result path across Grid/sequential/parallel/resume, including running snapshots and zero-param strategies; finite guardrail/survival failures remain saveable and deployable with explicit warnings, while recursively non-finite params/metrics/signals are refused. WFO calculates finite unqualified windows OOS; failed/running backtest status is advisory after executable-config validation. Deployment competency is rechecked on resume/live-enable, optimizer indicator params share a runtime catalog, AI-authored Python must reproduce a canonical smoke pass, and nullable/direct/deep-linked wizard sources preserve their configs. #18 truthful evaluated/finalist/not-evaluated counts; #22 owner-only fork-pool teardown; #28 exact tell-time params+metrics. Focused promotion/deployment/evaluator set 264/264; full host **4,326 passed / 4 xfailed / 0 failed**; container route/Motor set **212 passed / 4 source-layout tests deselected / 0 failed**; compileall, frontend build, rebuilt-service health and hard-refreshed browser smoke green. |
| 9.3 | Next-stage product/evidence assessment | ✅ **DONE 2026-07-31** | Source + runtime + three delegated audits converged: the bottleneck is reproducible prospective evidence and a truthful decision surface, not strategy supply. Staged plan: market-hours PAPER/read-only validation → 8 MED optimizer + Dashboard truth/performance fixes → Dashboard v2 + bounded transient live index chart → experiment ledger → real-LLM E2E. Edge research, arbitrary-stock expansion and live activation remain decision-gated. See [`NEXT_STAGE_ROADMAP_2026-07.md`](NEXT_STAGE_ROADMAP_2026-07.md). |
| 9.4 | **Stage 1 — truth + optimizer integrity** | ✅ **DONE AND PUBLISHED 2026-08-01** | All 8 MED rows closed (#14/#23 already subsumed; #17/#20/#25/#26/#29/#30 implemented independently). Full serial/parallel risk-objective parity; truthful early-stop/robustness/control evidence. Dashboard summary bounded and dispatch-aware; stale phase text removed; async errors/config/static-IP guidance actionable; spot Check → shared Ingest → auto-recheck added without gating deterministic runs. Host **4,354 passed / 4 xfailed / 0 failed**; container **142 passed / 9 source-layout deselected / 0 failed**; compileall/build/services/API/runtime/browser green. Dashboard runtime **1,857 bytes / 348 ms** vs prior 62,924 bytes / 3,087 ms. |
| P1 | Lot-size single source of truth | ✅ DONE 2026-07-27 (`da4e85b`) | **Pre-flight for the pooled campaign.** Two independent lot sources disagreed: `option_backtest.py:750` reads the CONTRACT's lot (correct, data-driven) while `premium_momentum_backtest.py:342` + `premium_trigger_dispatch.py:194` read hardcoded `UNDERLYING_META`. NIFTY(65)/SENSEX(20) agree so it was invisible; **BANKNIFTY contracts say 30, the map said 35 → 16.7% error in every quantity/₹ figure** on those (backtest-only, not live placement). New `instruments.resolve_lot_size()` resolves from contract data + returns warnings; surfaces `lot_size_changed_in_window` (BANKNIFTY really was 35 Jul-Dec-2025, 30 after) instead of silently picking. Did NOT assert a number — broker MCP unauthenticated on this IP and its login must never be called — removed the hardcode so both paths agree by construction. Closes the long-standing BANKNIFTY lot OPEN ITEM. **Context: BANKNIFTY has had 0 backtest runs EVER (NIFTY 225, SENSEX 31)** — the untested path is where the bug lived. |
| P2 | C2 fence test was wall-clock dependent | ✅ DONE 2026-07-27 (`da4e85b`) | My own C2 test asserted the authorised case while the fence deliberately uses a FRESH clock (re-checking the time IS half the fence's purpose — a deployment can cross the 15:00 IST cutoff during broker round-trips). Passed when written, failed every afternoon; surfaced only because this run was at 15:30 IST. Production behaviour UNCHANGED and correct; clock now injectable, only the test pins it. |
| P3 | C2 fence: the post-cutoff branch was never tested | ✅ DONE 2026-07-27 | **P2 made the test deterministic but never tested the property the fresh clock exists FOR.** The suite had NO assertion that the fence *refuses* after 15:00 IST — the only `clock_fn` use pinned it PRE-cutoff and asserted the authorised case. Proved by mutation: reverting `auto_live.py:493` to the frozen `now_utc` is caught by exactly ONE test (the new one) while the other 54 in the file stay green — so production could have regressed to the frozen clock, opening a real position minutes before the EOD square, with a green suite. Added `test_transmit_fence_refuses_when_entry_cutoff_passes_mid_flight` (both clocks pinned → deterministic at any hour). **Suite 3,649/0 verified at 15:52 IST**, i.e. after-cutoff AND outside-market-hours branches live; docs' "3,639" baseline is stale (HEAD was 3,648). Swept the rest of the suite for the same class — none found; details + the faked-clock dead end in `learning_log.md`. Residual (not a bomb, but untested): `_in_market_hours` is triplicated across `live_exit_monitor.py:23` / `live/live_position_guard.py:97` / `live/live_sl_monitor.py:55` and is only reachable from `run()` loops NO test drives. |
| C-blocked | Friction measurement (analysis direction C) | ⛔ NOT MEASURABLE | `live_trades` is EMPTY — zero real fills ever. Paper trades carry `entry_slippage_pts`/`entry_spread_pts` which ARE the friction model's own outputs, so measuring them against the model is circular. Blocked behind a real-money session → blocked on a registered IP. Do not fake it. |
| 6 | Profit-leverage ideas write-up | ✅ DONE 2026-07-27 (`bb06e9f`) | Deliverable **`docs/PROFIT_LEVERAGE_ANALYSIS_2026-07.md`**. Structural finding: the app is LONG-PREMIUM ONLY by construction (`base.py:21` no side; `option_backtest.py:749` long P&L; `auto_live.py:483` `side="B"` always), so all three failed campaigns searched ONE family — the one that PAYS the variance premium. **Decisive measurement (Mongo, not the manifest): every day for every index stores exactly ONE expiry (100% of 408/392/410 days); median strikes/day 6/8/9 spanning ~±1-1.5% of spot.** → calendars untestable, verticals barely; the only DEFENSIBLE short experiment (defined-risk spreads) is the one the data can't support, and the one it can (naked) the executor blocks by design. Direction A = ~20 files + novel offline margin model (`GetOrderMargin` is live-only, unreplayable) + multi-leg (doesn't exist — premium_momentum's "both" is two independent trades) + a data campaign. **Reduced to a scoped procurement question; no experiment authorised.** **RANKING: F (wire existing signal) → B (pool 3 indices: 1,210 option index-days vs 408 = 2.97×, ZERO engine changes, the one signal already judged +EV-but-sample-starved), C (realized-fill vs model) in parallel.** D reframed — front-expiry-only storage is fatal for calendars but is exactly what 0DTE trades. Kill criteria pre-registered for all. Side findings: VIX exists for 280 sessions (67.6% of history) so `capability.py:27 has_vix_history: False` is PROVABLY WRONG (AI wizard refusing rules against real data); BANKNIFTY option gap 2024-11-28→19 must be excluded from any pooled study; long P&L convention reimplemented in FOUR places with no chokepoint; OI written per candle and read by NOTHING; six ICT/SMC structural features have ZERO consumers; `explosive_reversal`'s `vix_boost_threshold` is a DEAD optimizer knob → chip `task_ff707a16`. |
| 7 | End-to-end deep audit | ⏸ BLOCKED | Needs multi-agent budget (spend-limit reset) or several lean sessions |
| 8 | Handover documentation refresh | ✅ CURRENT 2026-08-01 | Consolidated Stage 1 checkpoint added at `docs/STAGE1_INTEGRITY_SESSION_HANDOFF_2026-08-01.md`; HANDOFF, takeover prompt, live board, changelog and learning log agree. Continue updating after each future work unit. |

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

### Deferred — REQUIRED before first real-money session (C2 + H1 + C3) — ✅ ALL LANDED 2026-07-27

> These were THE gate to real money. **All four are now fixed** (C4 `f9a2482`, C2 `62e9457`, H1 `f3aa9cf`,
> C3 2026-07-27). What remains before a first real-money session is no longer CODE but
> VALIDATION: none of these fixes has been exercised against a live broker, because the
> current IP is not registered with Flattrade. See row V.

**C2 — transmit fence (~1 day).** `backend/app/live/executor.py` Gate 1 (~line 459)
checks `allow_fn()` once; `backend/app/auto_live.py` (~line 409) builds `allow_fn` over a
STALE deployment doc + frozen `now`. Fix: immediately before the actual `place_order`
transmit, re-fetch the deployment doc from Mongo and re-evaluate
`is_deployment_live_allowed` with a fresh `now`; abort as `blocked:stale_authorization`
if it no longer allows. Also re-check after every await that can take >~1s (margin call,
throttle wait). Test: flip deployment to stopped between margin gate and transmit (mock
broker) → order NOT sent.

**C4 — breaker re-consent — ✅ ALREADY DONE (`f9a2482`, see board row B5).** Kept for history: `resume` endpoint
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

- **2026-08-01 (Codex) — Stage 1 published.** User explicitly authorized the push.
  Pruned stale remote-tracking refs, confirmed the only local/remote branch is `main`,
  preserved archive tags, and pushed the complete HIGH/MED/Stage-1/takeover chain with a
  normal non-force update. Local and remote main matched after publication. No broker or
  deployment state changed.
- **2026-08-01 (Codex) — Stage 1 takeover package refreshed.** Added the authoritative
  session checkpoint with the eight-finding closure map, commit/file/test routing, verified
  runtime and real-money state, residual risks, ordered roadmap and exact resume commands.
  Reconciled the live board/HANDOFF/takeover prompt and removed stale claims that optimizer
  analysis ignored pause/cancel or that completed pre-real-money blockers were still
  deferred. Documentation contracts 33/33; no source/runtime/broker mutation. Publication
  occurred later under the user's explicit approval, as recorded above.
- **2026-08-01 (Codex + three scoped reviewers) — Stage 1 integrity complete.**
  Classified all eight MED optimizer rows before editing: #14/#23 were already fully
  subsumed by HIGH #18/#28; the other six received isolated fixes and red regressions.
  Full-suite review then exposed and closed the adjacent #29 serial/fork-worker denominator
  mismatch. Dashboard projection/KPIs/operator copy, async error parity, Fernet/static-IP
  guidance and the spot-data preflight are complete. The spot preflight is advisory:
  Check never fetches, Ingest delegates to the existing audit/fill/re-audit helper, and Run
  remains enabled. Host **4,354/4 xfailed/0**, container **142/9 deselected/0**, compileall,
  frontend/Docker build, health, runtime summary/preflight and canonical-localhost browser
  checks pass. One cosmetic `favicon.ico` 404 remains outside Stage 1. No broker write,
  live-mode change or push occurred.
- **2026-07-31 (Codex) — four verified HIGH optimizer defects + operator promotion
  competency closed.** Added pre-fix-red regression coverage for #11/#18/#22/#28 and the
  former qualification vetoes. Finite guardrail/survival failures now retain exact
  params and deterministic metrics for acknowledged paper/live choice; running snapshots
  and zero-param strategies are valid, while recursively non-finite params/metrics/signals
  remain refused. WFO still calculates finite unqualified OOS
  windows, incomplete backtests warn rather than veto, survival truncation is truthful,
  pool teardown is owner-only, and parallel winners bind their own params+metrics.
  Deployment competency is rechecked before resume/live-enable; shared indicator metadata,
  deterministic AI smoke validation, nullable form parity, older-backtest exact fetch, and
  acknowledgment recovery close the deploy → paper → live handoff. Focused 264/264, full
  host **4,326 passed / 4 xfailed / 0 failed**, container route/Motor set **212 passed /
  4 source-layout tests deselected / 0 failed**, compileall, optimized frontend build,
  rebuilt services, health checks and browser smoke green. Committed locally; not pushed.
- **2026-07-29 → 07-31 (Claude Opus 5) — backtest & optimizer integrity audit.**
  Triggered by a user report: an optimized premium strategy showed `lots: 100` for a form
  that said 5, blank KPI cards, an empty Trades pane and a +197% headline. Root cause was a
  **defect class** — one surface reading another's data envelope — found in eight places,
  plus one critical bug (`contract_key` NaN collapse) that had invalidated **every saved
  paired-option backtest in the database**. ~20 fixes across `option_backtest.py`,
  `optimizer.py`, `premium_trigger_dispatch.py`, `walkforward.py`, `routers/research.py`
  and the results/journal frontend; suite 3,972 → 4,271. A train/holdout study
  (train Jan–Apr, holdout May–Jul) proved `algotest_option_buy_nifty` has **no edge**:
  +60.83% / Sharpe 4.49 in train → **−1.65% / Sharpe −0.27** out of sample. A full
  claim-verification pass (23 claims, 0 disputed) plus the never-audited
  `result-persistence-display` dimension (9 findings, all fixed) closed the cycle.
  Register: [`BACKTEST_INTEGRITY_AUDIT.md`](BACKTEST_INTEGRITY_AUDIT.md) — including
  **13 verified-but-unfixed findings** and 10 generalisable lessons. Documentation audited
  and consolidated the same session (40 → 29 doc files; four running logs replaced by the
  one register). All work merged and **pushed** to `origin/main`.

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
