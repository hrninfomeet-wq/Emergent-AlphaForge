# Handoff — START HERE

_Entry point for the next engineer or AI agent. This is the shortest useful orientation; the repository and `tests/` are the source of truth, not any prior chat._

**Read order:** this file → [`TAKEOVER_CHECKLIST.md`](TAKEOVER_CHECKLIST.md) (what to DO, in order — safety rules, setup, lessons) → [`BACKTEST_INTEGRITY_AUDIT.md`](BACKTEST_INTEGRITY_AUDIT.md) (before trusting any result) → [`STAGE1_INTEGRITY_SESSION_HANDOFF_2026-08-01.md`](STAGE1_INTEGRITY_SESSION_HANDOFF_2026-08-01.md) (a dated session record — historical, not current) → [`DEVELOPER_GUIDE.md`](DEVELOPER_GUIDE.md) (the consolidated deep onboarding — run/build/test, live-trading safety model, warehouse model, India rules, research→deploy, gotchas) → [`ARCHITECTURE.md`](ARCHITECTURE.md) (technical reference). Use the ["Where to go deep"](#5-where-to-go-deep) table below to jump straight to a topic.

---

## 1. Orientation

**AlphaForge Trading Lab** is a **local-first research + forward-test app for Indian index options** (NIFTY / BANKNIFTY / SENSEX). The loop: warehouse 1-minute spot + option candles → select a compatible Strategy Library entry directly or backtest/optimize and save a preset → deploy for signal generation, paper trading, and (after explicit user consent plus hard operational/capital gates) live Flattrade execution.

Stack: **React** (CRA + craco) frontend, **FastAPI** (Python) backend, **MongoDB** (motor), all in **Docker Compose**. Frontend `:3000`, backend `:8001` (**every route under `/api`**), mongo `:27017`. **Upstox** = market data feed; **Flattrade** (Noren / PiConnect OMS) = live broker execution.

### 1.1 Where things live

**Pages** (`frontend/src/App.js` -> `pages/`). The four that matter for research:

| Route | Page | What it is |
|---|---|---|
| `/backtest` | Backtest Lab | Run a backtest; the **Backtest run journal** lists saved runs, each with a Trades pane and the Config / Result / Trades.csv / Save-as-preset / Deploy buttons |
| `/optimizer` | Optimizer | Optimization setup form + its own saved-job history (separate from the Backtest journal) |
| `/presets` | Saved Presets | Named strategy params + option execution policy; the deployable artifact |
| `/warehouse` | Data Warehouse | 1-minute spot + option candle coverage, gap fill, hygiene |

Others: `/` dashboard, `/strategies` library, `/journal` signals, `/paper` paper trading,
`/live` + `/live-trading` execution, `/checklist` pre-trade, `/premium-momentum`.

**Data** (MongoDB `alphaforge`, container `alphaforge_mongo`, named volume `mongo_data` -
NOT inside the project/OneDrive folder). The collections you will actually reach for:

| Collection | Holds |
|---|---|
| `backtest_runs` | every saved backtest result - `metrics`/`trades` (spot) **and** `option_backtest.*` (the real result for a premium-native run); this is what the Backtest journal lists |
| `optimization_jobs` | optimizer jobs: `config`, `param_space` (the bounds actually searched), `incumbent_seeds`, `best_params`, `best_metrics`, `trial_log`, `top_n_alternatives` |
| `presets` | saved presets (`config.params` + `config.execution`) |
| `candles_1m` / `options_1m` / `option_contracts` | the warehouse: spot candles, option candles (~8M rows), contract universe |
| `strategy_deployments` | deployments (paper/live) |

Read them directly with:
`docker exec alphaforge_mongo mongosh alphaforge --quiet --eval '<js>'`
(in Git Bash a JS regex literal starting with `/` gets path-mangled - use `new RegExp("...")`).

**Build / run.** `start-app.bat` is the supported launcher and rebuilds **both** halves:
`start-app.bat --rebuild --no-browser` (flags: `--check-only`, `--rebuild`, `--no-browser`).
It is a `cmd` batch file - call it by ABSOLUTE path, because
`NoDefaultCurrentDirectoryInExePath=1` is set on the operator's box and a bare `call foo.bat`
reads as "not recognized". The raw `docker compose` equivalents are in section 3.

## 2. Current state

> **As of 2026-08-20 · v0.58.0 + unreleased live-integrity work.** Verification baseline:
> **4,973 passed, 4 xfailed, 5 failed** — the five are `test_bootstrap_contract.py` and are a
> pre-existing Windows-launcher working-directory issue (`'start-app.bat' is not recognized`),
> unrelated to any strategy or live-path code. Both images rebuilt; `/api/health` returned
> `{"db":"ok"}`, the frontend returned HTTP 200, and 17 strategies registered with zero load
> failures.
>
> ⚠ **A live-only defect class was closed on 2026-08-20 — read
> [§2.0e](#20e-what-changed-2026-08-19--08-20-live-window-integrity-and-a-fail-open-safety-gate)
> before deploying ANY strategy.** Nine shipped strategies, including the permanent built-in
> `confluence_scalper`, were reading a session anchor through too small a live window and so
> diverged from their own backtests after ~12:34 every day. No backtest could show it.
>
> ⚠ **Before the next market session read
> [`LIVE_VALIDATION_PLAN_2026-08.md`](LIVE_VALIDATION_PLAN_2026-08.md).** Changes have landed
> on the real-money path continuously since 2026-07-29 and **most have never run in a market
> session.** That plan exercises them in an order where each step's failure is cheap.
>
> ⚠ **The 2026-08-14 live session found two defects that a green suite did not.** Read
> [§2.0c](#20c-what-changed-2026-08-12--08-15-the-live-session-and-what-it-exposed) before
> touching the live path — one of them cost a real trade, and one of them was a regression
> introduced two commits earlier by a change whose own tests passed.

### 2.0 The 60-second orientation

| Question | Answer |
|---|---|
| Is it running real money? | **Twice.** The 2026-08-04 NIFTY 24550 PE trade exposed fill/journal defects; the 2026-08-14 NIFTY 24300 PE trade exposed live/paper `exit_controls` divergence. Those defects are fixed, but most live-integrity work remains market-session unverified. |
| What stops it? | Not code. All four pre-real-money blockers (C2/C4/H1/C3) are **fixed**; what's missing is a **Flattrade-registered static IP** and a market-hours validation session. |
| Does any strategy have a proven edge? | **No.** Three independent campaigns have failed a holdout. See [`BACKTEST_INTEGRITY_AUDIT.md`](BACKTEST_INTEGRITY_AUDIT.md) §6 and [`PREMIUM_MOMENTUM_EDGE_VERDICT_2026-07.md`](PREMIUM_MOMENTUM_EDGE_VERDICT_2026-07.md). Do not re-litigate without new data. |
| Can I trust a saved backtest? | **Only if it was run on/after 2026-07-30.** Every paired-option backtest saved before then is wrong — see the ⚠ below. |
| What is the active work program? | The **capability phase**: make backtest/paper/live fully usable, and make a plain-English strategy deployable. Edge hunting is explicitly parked. [`AGENT_TODO.md`](AGENT_TODO.md) is the live board. |
| Where do I look first when a number looks wrong? | [`BACKTEST_INTEGRITY_AUDIT.md`](BACKTEST_INTEGRITY_AUDIT.md) — it names the defect class, reproductions, and the closed HIGH/MED register. Disputed LOW #31 remains separate. |

### 2.0b What changed on the live path (2026-07-29 → 2026-08-11)

The 2026-07 audits found the app was **strong at deciding to enter and weak at knowing
what it holds**. That layer is now largely closed. The "verified" column is the honest
state, not the intent — most of this has only ever run in tests.

| Change | What it fixes | Runtime-verified? |
|---|---|---|
| `ce82ba6` `7d85ff8` | Guard adopts ONLY positions AlphaForge can prove it opened (was: every row in the book). Refuses shorts. Ownership from the intent store's `intent.tsym`, not today's order book | ❌ needs an open unowned position |
| `358fcc3` | Restart-recovered positions stay inside the account basket stop (a regression introduced by `ce82ba6` itself) | ❌ |
| `be04cca` | A lost ACK from `place_order` is INDETERMINATE — claim retained, engine halted — not a clean not-placed | ❌ needs a network failure |
| `05b6822` | A never-filled entry stops consuming a `max_concurrent` slot forever | ❌ |
| `1a9a8ed` `6718aba` | **The day-stop can see open risk** (guard marks every cycle) and evaluates on a TIMER, not only when a signal arrives | ❌ needs an open position |
| `0330156` `f19bb75` `6c78198` | P&L measured from the true fill; statutory charges journalled; broker-space `noren_tsym`/`exch` reach the journal | ❌ needs a real fill |
| `339500b` | Boot reconciles orphaned warehouse runs + stranded paper trades | ✅ **cleaned 9 runs + 19 trades** |
| `0a9db7d` | A promoted deployment stops reading as dead on every performance surface | ❌ |
| `e8dfba3` | A session missed while the PC was off is no longer invisible to the planners | ◐ found 0 gaps (consistent) |
| `f6181a5` | The auto-update log cannot report green for a day it skipped | ❌ |
| `d1ce64d` | **Pre-open readiness at 08:45** — says whether the day can trade before the bell | ✅ real brokers, `ready:true` |
| `97ac3c7` `58ef491` `4a8c96b` | Paper: the cost toggle no longer alters a statutory fact; the deployment's own cost schedule is honoured; `allow_overnight` stops exempting positions from **stop-losses** | ❌ |

**Two of these were bugs I introduced and an adversarial audit caught** — `358fcc3`
(basket exclusion) and `58ef491` (cost-schedule substitution). Both passed the full suite.
Audit your own commits with the same machinery you use on others'.

### 2.0c What changed 2026-08-12 → 08-15 (the live session, and what it exposed)

The app traded live on **2026-08-14** (NIFTY 24300 PE, 1 lot). It was the second real trade
ever. Correlating it against the SAME deployment's paper trade exposed a class of defect the
test suite could not see, because both sides of each test shared the implementation's
assumption.

| Commit | What it fixes | How it was found |
|---|---|---|
| `20c9750` | **Live silently DISCARDED `risk.exit_controls`.** `resolve_live_exit_plan` passed the NESTED config verbatim to `build_monitor_state`, which expects a FLAT trail schema; no `mode` key meant `mode="none"` and every trail field null — **with no error**, because "none" is a legal mode. Paper and the sim called `exit_controls.effective_premium_stop` correctly; live was the one caller that never did. Fixed by DELEGATING to that same canonical decider. | Same deployment, same day: PAPER ratcheted its stop to entry+8 and booked **+₹4,882.69**; LIVE dropped the trail and ran from +₹520-worth to −₹1,651-worth. |
| `23d422b` | **A regression I introduced in `3222640` broke EVERY Flattrade call.** Percent-encoding the whole `jData` JSON made the server answer `HTTP 400 "jData is not valid json object"`. Reverted; `&` is now escaped as `&` instead. | One real API call. The suite was green because the implementation encoded with `urlencode()` and its test decoded with `parse_qs()` — **both sides shared the same wrong assumption about the server.** |
| `46e934e` `8d61019` | Candle-gap detection, any-day recovery, a real Flattrade TPSeries fallback, and a fail-closed data-integrity gate. | The backend booted 09:49 on 08-14 and NIFTY lost 09:15–09:49 as a clean leading hole. At the first live bar the evaluator's 200-bar window held **199 previous-session bars and one of today's** — the strategy traded a session whose open it had never seen. |
| `846da50` | The enable dialog now shows the exits that will actually be in force, before arming. | That deployment went live on `stop = 50% deep default, target = None, trail = discarded` — three facts, none displayed anywhere. |
| `c5d380b` | The deploy wizard sent `risk: null` where the schema wants a dict, so the OPTIONAL Exit/Risk panel was effectively mandatory. | Operator hit `risk: Input should be a valid dictionary`. |
| `b3f7df3` `cfe1c25` `b6ceb76` `152f52e` | Trailing anchors to the real fill, not the reference; "% of premium" means premium PAID; no OCO order spent when the account provably cannot margin it; the broker's decorated OCO tag is parsed, not demanded verbatim. | Verification of a 31-finding audit ledger (`audit-verification-2026-08-14.json`). |

**The lesson that generalises — and it has now bitten twice:**
> A contract cannot be validated against a mock that shares the implementation's assumption.
> The `jData` encoding and the `exit_controls` schema were both green in CI and both wrong in
> production. When the thing under test is an INTERFACE (a wire format, a schema another module
> consumes), test it against the real other side at least once.

**Also verified this session:** 2026-08-13 had been sitting at 368/375 bars on all three
instruments and nothing would ever have repaired it. Recovery now closes prior-day gaps too;
all six instrument-days are 375/375.

### 2.0d What changed in the 2026-08-15 takeover pass

The takeover did not assume that the green baseline proved runtime behavior. It drove the
scheduler, browser-facing action helpers and approval state machine, then rebuilt both images.

| Change | Why it matters | Verification |
|---|---|---|
| Scheduled 15:00 paper square-off now passes `honour_allow_overnight` to `square_off_open_paper_trades`, not to `latest_tick_map`. | The old parenthesis error raised `TypeError` inside the scheduler and silently skipped the EOD paper exit. A source-text assertion had seen the argument name but could not prove which function received it. | A one-cycle scheduler regression failed on the old call and passes now; overnight and paper square-off suites pass. |
| Deployment evaluation wakes from the latest bar of **NIFTY, BANKNIFTY or SENSEX**, not NIFTY alone. | A stalled NIFTY feed could previously prevent a fresh SENSEX/BANKNIFTY bar from waking the evaluator. Per-deployment `last_evaluated` still deduplicates work. | Behavioral regression advances only SENSEX while NIFTY is unchanged and observes a second evaluation. |
| Manual stand-down failures are loud, and a resolved `{indeterminate:true}` placement response is treated as UNKNOWN rather than rejected. | A lost broker ACK can arrive as HTTP 200. Re-queueing its one-shot token and showing “failed” could let the operator duplicate an order that is already live. | Backend marks the approval terminal `indeterminate`, clears its token and returns `retryable:false`; Node-driven frontend tests keep Place blocked and suppress stand-down. An adversarial re-review approved both closures. |
| Flattrade connection state has one shared decider for `expired` and `regenerate_after_6am`. | The chip, OAuth banner and recovery banner could otherwise contradict the backend and each other about a stale token. | Node behavior test plus optimized frontend build; all three cockpit consumers use the shared result. |

**Verification boundary:** full host suite **4,896 passed / 4 xfailed / 0 failed** in
41.89 seconds; focused safety set 121 passed; Python compileall, host frontend build, Docker
backend/frontend builds and service health passed. No broker order, login/logout, deployment-live
change or push occurred. This was a holiday/weekend pass, so none of the changes above is a
substitute for the next market-session validation plan.

### 2.0e What changed 2026-08-19 → 08-20 (live-window integrity and a fail-open safety gate)

Two defect classes, both invisible to the test suite that was green over them.

| Commit | What it fixes | How it was found |
|---|---|---|
| `fc424a1` `1cc6ce2` | **Session anchors were computed over the live WINDOW, not the session.** `precompute_all_indicators` groups `vwap` by `session_date` over only the rows it is handed (`indicators.py:470-473`). The backtest hands it the whole frame; the evaluator hands it the last N bars. At the 200-bar default the window stops reaching 09:15 after **12:34**, and by 14:49 the VWAP anchor error measured **+17.02 pts = 2.12 ATR** — larger than a default 1-ATR entry band, so momentum/fade signals silently INVERT for ~40% of the session. Nine strategies affected (`confluence_scalper`, both `vwap_*`, `squeeze_expansion_breakout`, `adaptive_regime_scalper`, both opening-range routers, `gap_fade`, `atr_sigma_router`); all now declare `live_lookback_bars = 400`. | An adversarial deployment audit of a newly added plugin. The bug was never specific to that plugin. |
| `fa2b65d` | **`detect_drift` failed OPEN.** It returned `False` (the ALLOW answer) whenever either hash was missing. A deployment with no `strategy_source_sha` read as verified forever with no protection. Six such rows existed in the database, one pinned to `FAKE_PINNED_FROM_TEST`. Now `False` only when both hashes are present and equal. | Auditing dev-era residue found while answering "do I need fresh deployments?" |

**The lesson that generalises, and it is the same shape both times:**
> Ask which answer is the ALLOW answer, then check that every "cannot verify" path returns the
> DENY one. Both defects were *documented as conservative* and were the opposite. And both were
> invisible to the suite because the fixtures omitted a field production always sets — the
> live-route fixtures built deployments with no pin, which real creation never does. Fixing the
> fixture to match reality repaired 14 unrelated pre-existing failures.

**Also landed:** `atr_sigma_router`, a scale-free entry-family SEARCH SPACE with **no
demonstrated edge** (four optimizer runs, all failed their holdout — best in-sample
+₹655,931 → **−₹518,145** out-of-sample); and
[`OPTION_BUYING_MICROSTRUCTURE_2026-08.md`](OPTION_BUYING_MICROSTRUCTURE_2026-08.md), a
measured register showing the ATM option buyer's MFE/MAE is **0.90–0.95, i.e. a negative
payoff before costs**. Read it before proposing another option-buying campaign.

**Known open (deliberate):** `deployment_evaluator.py` guards its own drift call site with
`if pinned_sha:`, so it still skips the check for an unpinned deployment. Defense-in-depth
only — creation always pins and the resume gate is the sole path to ACTIVE — left untouched
because a concurrent session held uncommitted work in that file.

### 2.0f What changed 2026-08-28 -> 08-30 (Backtest Lab action buttons, optimizer incumbent seeding)

Rollback point: commit `cd6521e`, tag `checkpoint/pre-optimizer-perf-2026-08-30`
(`git reset --hard checkpoint/pre-optimizer-perf-2026-08-30`). 73 focused tests green;
optimizer/entry-window failures identical to clean HEAD.

**Backtest Lab action buttons - FIXED.** `Trades.csv` exported `result.trades` verbatim while
the pane rendered `displayTrades()` joined to `option_backtest.trades`. Option runs lost all 14
option columns *including the rupee P&L*, and a premium-native run (spot `trades` empty by
construction) downloaded the literal string `"(empty)"` despite showing trades and a large Rs
P&L on screen. It now exports the pane's own rows; the CSV's `opt_pnl_value` column sums to
`option_backtest.portfolio.net_pnl_value` on **every** option run in the corpus (105/105).
`Save as preset` read sizing from `run.config.option_backtest` (the request echo) instead of
`run.option_backtest` (the resolved envelope the optimizer rewrites), so a run that traded
**100 lots produced a 5-lot preset**; it now reads the resolved block, matching what
Deploy-from-run already did. `Config` / `Result` / `Deploy` were checked and are correct -
Result is a lossless dump, Config carries `params_applied`, and Deploy only deep-links to the
wizard (`navigate('/live?backtest=...')`), it never auto-deploys.

**Optimizer "regression" on Confluence Scalper - diagnosed; it was NOT a code regression.**
Ruled out with evidence: the engine reproduces the saved run (257/257 trades, within 1.3%);
the optimizer's promoted result replays standalone **to the cent** (no evaluation drift); the
sampler is seeded (`seed=42`) so repeat runs are deterministic, not variance; and the Backtest
Lab frontend fix cannot reach the backend at all (0 backend files changed, the files do not
exist inside `alphaforge_backend`, and there is no JS runtime there). What actually happened:

- `optimize_indicator_periods` was switched **on** (it defaults to `false`), injecting 8
  dimensions `confluence_scalper` does not declare. Turning it off moved the *same* config from
  **-75,636 to +159,781** INR.
- A leftover cross-strategy `param_overrides` had silently widened `spot_target_pts` past the
  strategy-declared max of 200. It is now surfaced in the UI (setup form *and* finished job),
  deliberately **not** auto-removed - it was beneficial, and the operator wants the choice.
- **No code path called `study.enqueue_trial`**, so a known-good point inside the search space
  was never evaluated: a clean 11-dim run returned **-19,957** while the operator's saved preset,
  every value of which is inside those bounds, scores **+77,129**. FIXED - seeding presets /
  prior job bests / strategy defaults took the same config to **+148,602** (independently
  replayed, all 8 metrics identical). A deliberately under-budgeted 40-trial run returned
  *exactly* the preset's +77,129.19, which is the "never worse than the incumbent" floor working.

**STILL OPEN (deliberately deferred, not forgotten):** `net_pnl_inr` is
`total_pnl_pts x a constant lot_size`, so it **ranks trials identically to `total_pnl_pts`** and
models no premium - the search optimises a SPOT proxy and only the top-K finalists are re-scored
on real option money. Measured: the trial it ranked best had MORE spot points (2548) but
**-75,636** of option P&L, while a lower-spot config (2269 pts) made **+77,129**. Making it
option-native is blocked on a measured ceiling - **4.38M option rows across 4,294 keys** for
NIFTY over the 10-month window against `_option_rerank`'s **4M-row cap** - so it needs a
chunked/cached loader, not a bigger query. See the proposal list in
[`AGENT_TODO.md`](AGENT_TODO.md).

### 2.0g What changed 2026-08-30 (optimizer reporting units, deploy gate)

Three commits after the checkpoint: `2b47ed6`, `ff5e5a0`, `39e5f4f`. Tag
`checkpoint/validated-3-5-6-2026-08-30` marks the first of them.

**Optimizer now surfaces what it already knew.** The option re-rank loads candles under a
hard 4,000,000-row cap; past it trades silently do not pair, so EVERY candidate's rupee
P&L is understated — and the only signal was a `log.warning` inside the container. The cap
is reachable with a realistic window (NIFTY over 2025-11-01..2026-08-26 holds 4.38M option
rows across 4,294 keys). It now lands on the job as `rerank_coverage` and renders as a
warning stating the DIRECTION of the error. Run duration (`Took 2m 9s for 210 trials ·
0.61s/trial`) and `lot_size` are also shown; all three were persisted and rendered nowhere.

**Two reported numbers carried the wrong quantity (`ff5e5a0`).** Both were found by
reconciling stored jobs against recomputed truth, not by reading the UI:

- `best_so_far.value` was labelled "spot obj". It is not: the field holds the Stage-1 spot
  objective while the trial loop runs and is REPLACED at promotion with the option rupee
  P&L (or calmar). Wrong on 12 of 12 completed jobs checked — 684,602 shown as the spot
  objective where the real one (`total_pnl_pts x lot_size`) was 333,689, and one job ~4x
  out the other way. The three promotion sites now carry `spot_objective` through and
  `best_so_far_doc` persists it; older jobs render nothing rather than a wrong number.
- `best_value_metric` was derived from `evaluation_mode` alone, so a winner promoted by a
  CALMAR survival objective stored a RATIO under the label `option_pnl_value` — jobs
  `fbf72695` (11.3084 vs a real Rs 696,158.70) and `427a5cb5` (4.904 vs Rs 1,535.39). The
  sortable history column was ranking ratios against rupees. The label is now chosen in the
  same expression as the value, and the renderer refuses to assert a unit the figure
  contradicts (legacy calmar jobs therefore render bare, not as "Rs 11").

**Deploy no longer blocks on the optimizer's search bounds (`39e5f4f`).** See §2.1(4).

### 2.1 ⚠ Four things that will bite you immediately

1. **Every paired-option backtest saved before 2026-07-30 is wrong.** Option candles were
   grouped by a `contract_key` present on only ~2.3% of stored rows; the absent ones became
   pandas `NaN`, and **`bool(float("nan")) is True`**, so every legacy candle was keyed as the
   literal string `"nan"`. One Confluence config paired **10 of 253** signals before the fix and
   **253 of 253** after. Re-run anything you intend to rely on. Premium-native runs were never
   affected. Fixed in `dcaf722`.
2. **A backtest result has TWO envelopes and reading the wrong one gives a plausible wrong
   answer.** For an ORDINARY strategy the truth is in `result.metrics` / `result.trades`; for a
   **PREMIUM-NATIVE** one those are a deliberate **zero-filled stub** (its `evaluate()` is inert)
   and the entire real result lives in `result.option_backtest.*`. Route on
   `option_backtest.dispatch == "premium_trigger_config"` (backend: `is_premium_trigger_strategy`;
   frontend: `isPremiumNative()` / `resultKpis()` in `lib/backtestMetrics.js`). This single mistake
   produced eight separate user-visible defects.

3. **Option legs are SPARSE - never join them by array position.** 16 of 105 stored option
   runs have far fewer legs than spot trades (one has **835 signals / 317 legs**), because a
   signal with no option data never produces a leg. `index_trade_id` is the index of the spot
   trade a leg belongs to; a positional fallback stamped ANOTHER trade's strike and rupee P&L
   onto rows that should be blank - **1,100 rows corpus-wide** - and broke reconciliation (one
   run summed to -104,324.65 against a true -63,181.45). Every leg in every stored run carries
   `index_trade_id`, so there is no legacy case needing a fallback. Frontend:
   `joinOptionLegs()` in `lib/backtestMetrics.js`; guarded by
   `tests/test_backtest_lab_action_buttons.py`.

4. **`parameter_schema` min/max is the OPTIMIZER'S SEARCH RANGE, not a feasibility limit —
   do not gate on it.** The same schema is what `_build_param_space` searches, and
   `param_overrides` exist to WIDEN it, so the app routinely backtests, ranks and PROMOTES
   values outside it (a promoted confluence winner sat at `spot_target_pts` 285.7 against a
   declared max of 200). `_validate_strategy_deployment_config` used to raise HTTP 400 on
   it, which made **4 of 12 saved presets undeployable** and had already forced
   `atr_sigma_router` to keep a deliberate no-op 40-59 band just to avoid bricking saved
   artifacts. Out-of-range is now an acknowledgeable warning; only genuine infeasibility
   blocks (wrong type, non-finite, and non-positive where the schema declares a positive
   minimum). The same principle is stated in `deployment_quality.py`: *"Surface them as
   warnings - never block ... the app aids the user, never restricts."* Guarded by
   `tests/test_deploy_param_range_is_advisory.py`.

   The general shape of traps 2-4: **a stored field means different things in different
   states, and something read it under a fixed label.** Before trusting any displayed
   number, check what actually writes that field.

### 2.2 What landed most recently (2026-07-28 → 08-01, v0.57.5 + v0.58.0 + Stage 1)

A full reporting-integrity audit of the backtest → optimizer → results → journal chain.
Twenty-plus fixes, ~300 new tests, all merged and pushed. Highlights: the `contract_key`
collapse above; premium KPIs/Trades/Journal/trust-scorecard read the right envelope; the
optimizer stopped inventing a 0–100 range for `lots` and maximising it (the reported
`lots: 100` from a form that said 5); spread cost disclosed (was understated 3.7×); real
premium walk-forward with a **signed decay** instead of a boolean that hid 9.4pt decays;
one entry window across trial/re-rank/saved-run/preset; saved runs **155.3 MB → 3.5 MB**;
coverage preflight now certifies through the real lookup it had been bypassing.

**The four verified HIGH optimizer findings are closed in the current working tree**:
finite candidates retain exact params+metrics and non-finite trials cannot promote
(#11); truncated survival coverage is reported truthfully (#18); fork-pool teardown is
owner-only (#22); and parallel winners keep their own params+metrics including pinned
dimensions (#28). By operator decision, guardrail/survival/backtest-result status is
advisory: a technically executable config remains available for acknowledged paper and
separately authorized live deployment. This now includes finite snapshots from running
optimizer jobs and zero-parameter strategies. The execution boundary recursively rejects
non-finite params/metrics/signals, validates optimizer-added indicator keys from a shared
catalog, repeats deterministic smoke evaluation for AI-authored Python, and revalidates a
deployment before resume and live enablement. The wizard preserves nullable defaults,
recovers warning acknowledgment without a dead end, and exact-fetches older deep links.
**Stage 1 integrity is complete and published to `origin/main`.** The six surviving MED
defects now honor controls during re-rank/WFO analysis, report actual early-stop evidence,
exclude robustness no-ops, score negative objectives symmetrically, and normalize drawdown
objectives across points/rupees with serial/parallel parity. MED #14 and #23 were stale
rows already subsumed by HIGH #18/#28; their existing red regressions remain. Dashboard
summary payloads are inclusion-projected (runtime: **1,857 bytes / 348 ms**, down from
62,924 bytes / 3,087 ms), Latest Backtest uses the dispatch-aware KPI selector, operator
copy/config warnings are actionable, and Backtest Lab has read-only Check → shared Ingest
→ automatic recheck for spot data. The full verification record is in
[`BACKTEST_INTEGRITY_AUDIT.md`](BACKTEST_INTEGRITY_AUDIT.md) §5. Only disputed LOW #31
remains separate; it was not silently promoted into this milestone.

The session-specific commit map, runtime snapshot, verification ledger, file routing and
exact next-agent startup sequence are consolidated in
[`STAGE1_INTEGRITY_SESSION_HANDOFF_2026-08-01.md`](STAGE1_INTEGRITY_SESSION_HANDOFF_2026-08-01.md).

### 2.3 Release history (newest first — archival, read only what you need)

**Previous (2026-07-21, v0.56.2)**: **deployment selection is free, real-money
authority is explicit, and capital remains gated.** The `/live` wizard accepts a
saved preset or any loaded, non-retired Strategy Library entry that supports the
current 1-minute evaluator. A direct library choice exposes its instrument and full
parameter schema and becomes an immutable deployment snapshot with version, params,
and source SHA pinned. Ordinary creation remains signal-only or paper; every
compatible deployment then appears on Live Trading.

`POST /deployments/{id}/live/enable` no longer treats failed/incomplete forward
evidence as an absolute veto. The UI displays the exact failed checks and requires
a separate **unvalidated real-money** checkbox plus typed `ENABLE`; the backend
requires strict `accept_unvalidated_live=true` and audits the evidence snapshot,
failed checks, user, and timestamp under `risk.live.evidence_consent`. Passing the
pre-registered policy still earns the forward-validated label and is the recommended
path. The consent bypasses evidence only: broker/engine/drift/retirement checks,
positive daily loss cap, account lot/open-position ceilings, exchange/order safety,
margin, idempotency, fresh premium, and OCO/guard protection remain hard. The exact
one-lot/one-per-day/one-open/₹4,000 initial contract is now a recommendation rather
than a code veto; selected positive caps may vary within account ceilings.

Verification baseline for v0.56.2: **3,524 passed, 4 xfailed, 0 failed** and the
optimized frontend production build completed without compile/ESLint warnings
(2026-07-21).

**Previous (2026-07-20, v0.56.1)**: **research evidence can no longer masquerade as
live permission.** The user fixed the capital contract at ₹2,00,000, a 25% monthly
drawdown ceiling, and a one-sided 95% annual impairment-risk upper bound below 30%.
`forward_validation.py` pre-registers the minimum cohort: fixed hash, account capital
enforced, one lot, no overnight violation, ≥60 complete sessions + ≥120 closed trades,
≥95% fresh Full-feed execution surfaces, lower block-bootstrap daily-mean CI > 0,
≥4/6 positive 10-session blocks, ≤25% monthly and whole-record drawdown, and the
252-session five-day-block impairment bound. In that release `/live/enable` failed
closed unless the whole record passed and enforced an exact initial live cap; v0.56.2
retains the evidence verdict while replacing that absolute veto with explicit audited
operator consent and account-level capital ceilings.

For new deployments, "fixed hash" means `forward_config_hash`, covering strategy
source/parameters plus option policy, pre-trade profile, sizing, friction and paper
exit/risk controls. Promotion recomputes it and requires an exact match. The capital
gate also requires the fixed account-wide ₹2,00,000 contract exactly; a per-deployment,
cumulative, or differently sized budget does not qualify.

Historical option results are explicitly **research only**. The warehouse audit found
8,714 reusable two-part tokens spanning identities and 2,551,919 candles on 2,423
collision tokens; legacy rows lack first-ingest/run provenance, master snapshots, and
explicit bar-end decision time. New ingestion stamps token+expiry `contract_key` and
retrieval provenance; pairing refuses ambiguous token aliases. Upstox streaming now
defaults to Full and retains five-level depth, OI/IV/Greeks and timestamps. Paper trades
persist entry/exit surfaces and a top-of-book-after-charges `execution_realized_pnl`.
Read [`option-data-provenance.md`](option-data-provenance.md) before treating any option
backtest as evidence and [`forward-validation-policy.md`](forward-validation-policy.md)
before creating a cohort.

Live close semantics were also repaired: Stop/Stop-all/kill no longer remove the guard,
cancel OCO, or journal CLOSED on broker **place acceptance**. They report submitted /
deferred / failed symbols and retain protection until the normal consecutive
authenticated broker-flat reads finalize. See `live-readback-checklist.md`.

The existing ten-lot confluence paper deployment is **legacy/non-comparable** despite
large realized P&L: it had no enforced account capital until 2026-07-20 and still had
overnight-open positions. The account-wide fixed ₹2,00,000 gate is now enabled for new
paper entries, but a fresh one-lot deployment/hash is required; old trades are not
retroactively promoted.

The approved durable-host design is documented in
[`durable-static-ip-deployment.md`](durable-static-ip-deployment.md): reserved
IPv4 VPS, application ports kept private behind SSH/WireGuard, Full-feed paper
capture first, and no public exposure of the current unauthenticated app. The
existing Windows Compose file is not a production manifest and must not be
copied unchanged to a public host.

Verification baseline for v0.56.1: **3,516 passed, 4 xfailed, 0 failed** and the
optimized frontend production build completed successfully (2026-07-20).

**Previous (2026-07-19, v0.56.0)**: **deploying a strategy in live mode IS the
authorization — the per-deployment ARM ceremony and the `LIVE_GUARD_ARMED` env gate
are GONE** (explicit user decision). A live deployment trades on its own strategy
logic (entry / stop / target / trailing) with the resting broker OCO as the PC-down
backstop; the software exit guard now **always transmits** (both the square and the
Layer-2 re-price), which closes the old "dangerous gate split" where real entries
opened while automated exits only logged. `LIVE_AUTOPLACE_ARMED` survives as the
single set-once master switch for automated entries.

This was **not** a deletion: `mode == "live"` did not previously exist (`ALLOWED_MODES`
was `{signal_only, paper}`), so the arm record *was* the live marker. Authorization is
now `mode == "live"` **and** broker connected **and** before a new explicit **15:00 IST
entry cutoff** (`is_deployment_live_allowed`). `POST /deployments/{id}/live/enable` is
the only writer of live mode — it runs the same seven preflight checks the arm route
did and now **requires** the risk caps, because it is also their only writer. The caps
governor **fails closed** for a live deployment with no caps rather than fast-pathing
to allow-all. Emergency stops (`/live/stop`, stop-all, kill switch, daily-loss breaker)
now write `status="PAUSED"` — the evaluator only iterates ACTIVE, so that is the real
halt. **Read CHANGELOG 0.56.0 before touching any live seam**; it lists the four silent
regressions a naive removal would have shipped. Suite 3489 passed, 0 failed.
**Not market-validated** — see `phase5b-market-validation-runbook.md`, which now leads
with this model change.

**Previous (2026-07-19, v0.55.2)**: **the official Flattrade Trading MCP server now
shares AlphaForge's single Flattrade API key** — installed and live-validated. Flattrade's
API V2 policy allows ONE key per account (a second needs the paid registered-algo tier,
₹5,000+GST per exchange, only relevant above 10 orders/sec — **never push AlphaForge onto
that tier**), and that key holds one redirect URI which AlphaForge owns, so the MCP binary
structurally *cannot* complete its own OAuth; Flattrade is also last-login-wins, so a
second login would silently kill AlphaForge's live session. Resolution: AlphaForge stays
the **sole OAuth owner** and its auth callback mirrors the fresh jKey into the MCP's
`~/.flattrade/session.json` (`live/mcp_session_sync.py`, opt-in via
`FLATTRADE_MCP_SESSION_DIR`, never fails the login). One daily AlphaForge login now serves
both. The MCP is a **separate closed-source product**, not part of AlphaForge's execution
path — and **positions it opens are invisible to AlphaForge's guard/OCO/kill-switch** (an
explicit, user-accepted trade-off). Read
[`flattrade-mcp-integration.md`](flattrade-mcp-integration.md) **before using or touching
it** — especially the never-call-`login`/`logout` rule. See CHANGELOG 0.55.2.

**Previous (2026-07-18, v0.55.1)**: **fix — option legs rendered on the wrong
Trades-pane rows whenever a DTE filter was active.** `_run_paired_option_backtest`
rebuilt `spot_trades` to the filtered subset before the sim, so each leg's
`index_trade_id` was a *filtered-list* position while the saved run doc's `trades[]` and
the Backtest Lab's Trades pane join by *full-list* index — 168 of 171 legs in the
reported run rendered against the wrong spot trade (a CE row showing another trade's
25850 PE leg). **Contract selection was never wrong**: `select_contract_for_signal` is
side- and ATM-exact, and a warehouse audit (63,868 contracts) found zero
symbol↔strike/side/expiry mismatches and zero orphan candle keys. Fixed by remapping to
full-list positions after the sim; 20 already-saved journal docs repaired in Mongo
(reversible via `option_backtest.index_remap_backup`). See CHANGELOG 0.55.1.

**Previous (2026-07-17, v0.55.0)**: **Phase 5B — live/paper multi-leg
premium-momentum execution is BUILT** (both-legs mode, one-shot lazy
reversal off STOP-class guard exits, per-deployment exit_time squares
clamped below the 15:00 EOD, realized-only session day-stop, VIX gate) as a
**pure capability by explicit user decision** — the family's failed edge
gate (0.54.2) travels with every multi-leg deployment as an informational
`premium_edge_verdict` arm advisory (never a gate; there is still NO
strategy-specific arming gate of any kind). first_to_trigger/single-leg
deployments are byte-identical to Track B (source-pinned). The independent
review of the recovery path caught and closed a HIGH defect before any
live exposure: recovery matched Upstox trading_symbols against the
Noren-keyed broker position book (different symbol spaces → every open leg
reads "gone" on restart → false finalize with money open); the fix joins
through the broker order book's norenordno→tsym and treats an unresolvable
order as skip-never-exit. Full suite 3478 passed, 0 failed. NOT yet
validated in a real market-hours session. See CHANGELOG 0.55.0 and the
plan's parity-divergence table before touching any of these seams.
**NEXT STEP (planned 2026-07-20)**: first market-hours validation, paper
mode — follow `docs/phase5b-market-validation-runbook.md` exactly; it also
scopes what paper CANNOT prove (the guard-side 5B exits: lazy arming,
exit_time, recovery join — those need the later 1-lot live day).

**Previous (2026-07-15, v0.54.2)**: **the premium-momentum edge hunt is CLOSED
with a failed gate.** Phase 5A.2 added the session day-stop + India VIX gate
overlays (backtest-only) and a byte-identical sweep-perf fix, then the
pre-registered ~600-config campaign ran on a three-way chronological split:
the validation-best config (+₹103.5k on the friendly 2025-Q4 slice) lost
−₹153.8k on the untouched 2026 holdout at 1%/side — worse than the untuned
baseline. Robust three-period NO. **Phase 5B (live multi-leg execution) is
NOT to be built on current evidence** — the revival kill-criterion is
pre-registered in `PREMIUM_MOMENTUM_EDGE_VERDICT_2026-07.md` (read it before
proposing any premium-momentum live work). The hunting tools remain in the
app (16 tunable keys through the honest tuner). Also in 0.54.1: the Backtest
Lab now surfaces premium-native results in the main view (they were hidden
in the collapsed Advanced section), the option preflight reports honest
per-session coverage for this strategy, and the option form's lots/costs
reach the dispatch.

**Previous (2026-07-14, v0.54.0)**: **Phase 5A** — the full AlgoTest "EXP2"
contingency shape (both-legs mode, one-shot lazy reversal leg with a fresh
strike + snapshot at the stop-out bar, entry cutoff, hard exit time,
%-of-entry stepped trail) is built and adversarially reviewed in the
**backtest engine only**, reachable via the `/premium-momentum` page +
backtest/tune routes. Backtest-only by mechanism, not promise: the plugin
schema and `PremiumTriggerConfig` were deliberately NOT extended, so
deployments structurally cannot carry the new params — live/general-Optimizer
support is Phase 5B. **The 5B gate was run and FAILED at EXP2 defaults**
(2026-H1 NIFTY, costs on): the lazy legs are gross-positive but net-negative
after friction, and the full EXP2 config is worse than plain both-legs
(−₹69.5k vs −₹60.6k on 2 lots); notable structural finding — both-legs mode
massively outperforms first-to-trigger (−₹60.6k vs −₹140.2k), though all
configs remain net-negative. Do NOT build Phase 5B live execution unless a
tuned config first beats both-legs net-net out-of-sample through the honest
tuner. See CHANGELOG 0.54.0.

**Previous (2026-07-14, v0.53.2)**: Phase 4 (**engine dispatch**) is now
functionally complete. `premium_momentum` runs through the standard Backtest
Lab (single-run) AND the full multi-trial Optimizer search (Bayesian and
Grid) exactly like any other strategy — `optimizer.py::_evaluate_premium_trigger`
closes the last gap (0.53.1's Stage-2 fix was reachable but Stage 1's
per-trial scorer still used the stub `evaluate()`, unconditionally
disqualifying every trial before Stage 2 was ever reached). Also fixed along
the way: `premium_momentum`'s `parameter_schema` had no `min`/`max` on its
numeric fields (silently sampled `momentum_pct` as `0.37` instead of `15`,
and crashed the Grid method outright), and a subtler bug an adversarial
review caught — the Stage-1 preload read a `param_overrides` `"fixed"` string
value that no trial could ever actually receive (string params are excluded
from the search space before overrides are applied), which could silently
bias every trial's score against the wrong option window. **Verified live
against real local warehouse data**: a real 15-trial Bayesian job returns a
genuine non-disqualified best score (5 trades, 60% win rate, net P&L
+₹5,404.70); a different strategy's job run immediately after confirms zero
regression (identical to before). Full host suite: 3358 passed, 0 failed. See
CHANGELOG 0.53.2 for the full detail. Remaining, deliberately out of scope: a
declarative config-block builder UI, and the `opt_workers>1` parallel Optuna
path (pinned to sequential for this strategy — sequential is the documented
default anyway).

**Previous (2026-07-13, v0.53.0, Emergent handoff session)**: AI feasibility
accepts premium-native rules (option-premium momentum, locked strike, stepped
premium trail) with a mapped `premium_trigger_config` verdict, session gates +
position size map to `deployment_layer`, `lazy_leg_contingency` is honestly
scoped as Phase-5 future work (not a blanket reject or false accept), and the
Gemini 8000-token cutoff on Strategy Library AI actions is fixed
(`DEFAULT_MAX_TOKENS` 8192 → 32768, `py_author.py`'s hard cap removed). See
CHANGELOG 0.53.0 for the full detail.

Everything of substance is integrated on **`main`**, pushed to `origin/main` at `10f68d1` (v0.55.1)
on 2026-07-18. **Local `main` is ahead** by the v0.55.2 Flattrade-MCP commit `f67f463` plus any docs
commits — push only on explicit user request (see §4). `main` is currently the **sole branch**, but
at any given time a parallel session may create **1-2 active WIP branches** that haven't landed yet
— run `git branch -v` / `git log origin/main..main --oneline` and don't assume main is the whole
story before describing "current state" to anyone. The app has grown across `0.17.x → 0.55.x` (see
[`../CHANGELOG.md`](../CHANGELOG.md) for versioned detail — if the top entry looks more than a week
old, the changelog itself is probably behind `git log`; it has happened before). It runs in Docker;
backend code is baked into the image, so **rebuild the container after backend edits**.

**Host test baseline: 3486 passed, 4 xfailed** (`.venv\Scripts\python.exe -m pytest tests -q` from
the repo root; motor/route tests run inside the container instead — see §3).

Built subsystems (all verified present in `backend/app/`):

- **Data Warehouse** — `candles_1m` holds 1-minute OHLCV for the 3 indices (spot + ATM-band option contracts) + INDIAVIX. Daily ATM-band completeness model, holiday-aware NSE calendar, one-button Sync + auto-update. (`completeness.py`, `data_hygiene.py`, `nse_calendar.py`, `routers/warehouse.py`.)
- **Backtest Lab** — spot backtests + paired real-option-candle backtests; honest rupee-first metrics; optional exit/risk-control overlay (trailing / breakeven / daily caps). (`backtest.py`, `option_backtest.py`, `exit_controls.py`, `execution_policy.py`.) **Join contract (v0.55.1, load-bearing):** an option leg's `index_trade_id` must always be a position in the **full** spot-trade list the caller holds — never a filtered-list position. `simulate_paired_option_trades` enumerates whatever list it is given, so any caller that filters first (DTE filter, etc.) MUST remap afterwards, as `runtime.py::_run_paired_option_backtest` does; consumers should join by id or `signal_entry_ts`, never by array position. Pinned by `tests/test_paired_option_index_remap.py`; historical docs repaired by `backend/scripts/repair_option_leg_index.py`. The shared indicator enrichment (`indicators.py` / `indicator_groups.py`) re-warms the whole-frame indicators across intra-session warehouse gaps via a per-bar `gap_before` flag + `_reset_on_gap` wrapper (no-gap fast-path keeps gap-free windows byte-identical); see `docs/superpowers/specs/2026-07-05-intra-session-gap-indicator-reset-design.md`.
- **Optimizer** — Optuna TPE / Grid / Genetic search; Single exploration vs spot-fitted walk-forward OOS; historical option re-rank/OOS is research-only under a provenance gate; Single-run survivability is a stress screen, not annual certification. The UI includes a deterministic ₹2L evidence profile. (`optimizer.py`, `wfo.py`, `walkforward.py`, `survival.py`, `rerank_select.py`, `option_data_integrity.py`.)
- **Strategy Library** — builtin + drop-in plugin strategies; retire / delete lifecycle; multi-provider AI authoring wizard (Anthropic + Gemini; Spec + capability-aware + full-Python tiers). (`strategies/*`, `routers/strategies_admin.py`, `ai/*`.)
- **Paper Trading** — Full-feed point-in-time surfaces on entries/exits, top-of-book executable P&L after charges, fixed account-capital gate, session-complete forward metrics, and pre-registered promotion policy. (`paper_auto.py`, `paper_trading.py`, `forward_metrics.py`, `forward_validation.py`, `live_exit_monitor.py`.)
- **Live Trading (Flattrade)** — offline-first; L0–L3 gate chain; the executor is the **single real-order chokepoint**; margin pre-check; OCO/GTT catastrophe backstop; kill switches; per-token-latched recovery that re-runs on every fresh daily OAuth (not boot-only); exit executors resolve a raised-but-maybe-landed order against the broker book before ever blind-retrying; Greeks; auto-place only under the `LIVE_AUTOPLACE_ARMED` env gate **plus** a live-mode deployment **plus** caps **plus** the 15:00 IST entry cutoff (v0.56.0 — the per-deployment ARM ceremony and its EOD auto-disarm were removed; `live/enable` carries the preflight chain and the caps). **No resting manual-position timer** — the old 10-minute test-session auto-square was removed; 15:00 IST EOD square is the sole time-based backstop for a manual position (deployed strategies exit on their own rules + a resting OCO). (`live/executor.py`, `live/safety.py`, `live/margin.py`, `live/mode.py`, `live/arm_state.py`, `live/gtt.py`, `live/kill_switch.py`, `live/exit_claims.py`, `live/auto_square.py`, `routers/live_broker.py`.) Since v0.55.2 the daily OAuth callback also mirrors the fresh jKey into the **official Flattrade MCP** binary's session file (`live/mcp_session_sync.py`; recovery `backend/scripts/resync_mcp_session.py`) — AlphaForge remains the sole OAuth owner; see [`flattrade-mcp-integration.md`](flattrade-mcp-integration.md).
- **Premium-momentum strategy** (new) — a deployable strategy family driven by a **time-locked strike + real option-premium trigger** instead of a spot indicator: at a configurable reference time the evaluator locks the CE/PE strike from spot, captures each side's premium from fresh ticks, and the first side to cross a momentum threshold enters; exits use a new stepped X-Y trail guard mode alongside stop/target. Backtest is a self-contained option-native sim with a cost model and an honest (costs-mandatory, chronological-train/OOS-report) tuner; live/paper execution rides the *exact same* deploy/arm/guard rails as every other strategy — **there is no premium-momentum-specific arming gate, and none should ever be added** (a deliberate, explicit design decision — don't "helpfully" add one later). See [`STRATEGY_DEPLOYMENTS.md`](STRATEGY_DEPLOYMENTS.md) for the deployment-level detail. (`premium_momentum.py`, `premium_momentum_backtest.py`, `premium_momentum_tuner.py`, `premium_momentum_live.py`, `premium_lock_store.py`, `premium_pin.py`, `strategies/plugins/premium_momentum.py`, `routers/premium_momentum_routes.py`.) The shipped default (AlgoTest blueprint) parameters have **no edge** on 2026-H1 NIFTY — this is a capability, not (yet) a validated money-maker, and it has not been run through a real market-hours session. Since v0.55.0 the family also executes the **full multi-leg shape live/paper** (`leg_mode: "both"` CE+PE independent primaries; one-shot `lazy_enabled` reversal leg armed off STOP-class guard exits; `exit_time` per-deployment squares clamped below 15:00; `session_max_loss_rupees`/`session_max_profit_rupees` realized-only day-stop — live squares once, paper blocks only; `vix_min`/`vix_max` session gate) — the per-leg state lives in the same `premium_locks` doc (`pce/ppe/lce/lpe` field groups), exits/finalize/lazy-arming hang off the **live guard only** (paper exits ride the separate LiveExitMonitor and never touch locks), and restart recovery resolves leg symbols exclusively through the broker order book's `norenordno→tsym` join (never the persisted Upstox symbol — see CHANGELOG 0.55.0's review-closure note).

Routers mounted under `/api`: `research`, `strategies_admin`, `warehouse`, `journals`, `deployments`, `broker`, `live_broker`. Frontend pages: Dashboard, DataWarehouse, BacktestLab, Optimizer, StrategyLibrary, SavedPresets, LiveSignals, SignalJournal, PaperTrading, LiveTrading, PreTradeChecklist, PremiumMomentum.

## 3. Run & test quickstart

```bash
docker compose up -d --build backend frontend    # launch / rebuild
docker compose ps                                # backend + mongo healthy
curl -s localhost:8001/api/health                # {"db":"ok"}
```

Frontend → `http://localhost:3000`, backend → `http://localhost:8001` (routes under `/api`), mongo in container `alphaforge_mongo` (named volume `mongo_data`, NOT in the project / OneDrive folder). Rebuild the relevant container after editing that half.

**Tests run in two places** — the split matters:

- **Host tests (pure / contract).** `python -m pytest tests -q` from the repo root. These NEVER import `server.py` or the routers (motor/pymongo are absent on the host — those imports fail). They string-assert on the source via `tests/contract_corpus.py`. Use for the pure engines, contract pins, and JSX string-pins.
- **Container tests (motor / route).** Tests that touch motor or FastAPI routes must run **inside the backend container**:
  ```bash
  docker cp tests/. alphaforge_backend:/app/tests
  docker exec -w /app alphaforge_backend python -m pytest tests/<file> -q
  ```
- **Frontend "tests"** are pytest string-pins over the JSX source (run on the host with the contract tests).
- **There is no single green number — measure the DELTA, not the absolute.** Neither place
  is zero-failure today, and both counts are dominated by environment rather than defects.
  Measured 2026-08-30: **host** `python -m pytest tests -q` = 4,254 passed / 80 failed /
  56 collection errors (the errors are `motor` absent on the host, and most failures follow
  from that). **Container** (whole suite) = 4,870 passed / 309 failed / 68 errors, and those
  failures are overwhelmingly UI source-pin tests — `test_live_cockpit_ui`,
  `test_wizard_*_ui`, `test_backtest_performance_overview`, `test_safety_latch_ui_contract`
  — which read `frontend/` files that **do not exist inside the backend container**, plus
  the known `test_bootstrap_contract` launcher issue. Do NOT run the whole suite in the
  container and read 309 as regressions.

  The reliable method, and the one used for every fix in §2.0f/§2.0g: run your targeted
  subset, save the FAILED list, `git stash` your change, re-run the identical command on
  clean HEAD, and diff the two lists. Introduced-failures should be zero. A new test that
  passes both before AND after has not tested your fix — confirm it fails on clean HEAD.
- **Browser smoke** is the final check: open the app in Chrome and **hard-reload (Ctrl+Shift+R)** to drop the stale CRA bundle — client-side navigation does not reload the JS.

## 4. Standing conventions

- **Checkpoint before risky work.** Commit the current *validated* state (and tag it) before
  starting anything that could need reverting, and keep unvalidated work out of that commit so
  the checkpoint is genuinely last-known-good rather than a mix. Most recent:
  `checkpoint/pre-optimizer-perf-2026-08-30`.
- **Confirm before changing shared backtest/optimizer computation code.** `backtest.py`,
  `optimizer.py`, `option_backtest.py`, `wfo.py` and friends are used by EVERY strategy, so a
  change there silently reprices every future result. Show before/after evidence and get an
  explicit go-ahead first; UI-only or export-only changes do not need it.
- **Confirm before anything that could reach the broker or a deployment.** Deploy, arm, resume,
  live-enable. Static inspection and dry runs are fine; triggering is not. (See also the
  never-place-a-real-order rule below, which is absolute.)
- **Verify a fix across MULTIPLE saved runs, not one.** Runs differ by family
  (ordinary vs premium-native), by instrument, and by data shape. The positional-join bug in
  2.1(3) passed every check on the four runs first sampled and was only caught by sweeping all
  105 - dense-leg runs pass a positional join *by accident*. Prefer a corpus sweep over a sample.
- **Never call something fixed without running it.** Reading the diff is not evidence. Reproduce
  the failure first, then re-run the same path after the change - for UI work that means clicking
  it in the browser, not grepping the JSX. A test that passes both before and after the fix has
  not tested the fix; mutate the code and confirm the test actually fails.
- **Per-changeset push approval.** Commit freely; **push only when the user explicitly says so.** Nothing is auto-pushed. On the default branch, branch first.
- **Never place a real broker order.** The assistant never personally transmits or squares a real order, and never flips a deployment to live mode. Real live entries require the env gate `LIVE_AUTOPLACE_ARMED=1` **and** a deployment in **live mode** (set only by the user via Deploy-to-Live) within its caps and before the 15:00 IST entry cutoff. Offline-first: `LIVE_AUTOPLACE_ARMED` unset ⇒ dry-run logs, no transmit. **Auto-squares are no longer gated** — the software guard always transmits its exits (v0.56.0).
- **IST everywhere.** NSE session 09:15–15:30 IST with a 15:00 square-off; the system is **holiday-aware** (`nse_calendar.py`).
- **Verify India-specific facts against the code** — lot sizes and expiry cadence live in `instruments.py` / `nse_calendar.py` / `dte.py` and have rotated over time; do not hard-code from memory.
- **Never commit** `.env`, tokens, broker creds, or any credentials file.
- **Don't add a new strategy-specific live-arming gate without being asked.** The user explicitly
  changed the global frozen-forward gate in v0.56.2: it determines validation status, while a
  separate explicit unvalidated-live consent may authorize any technically compatible strategy.
  This must not be confused with the ordinary creation-warning acknowledgment. The default posture
  for a new strategy or
  feature is to ride the *existing* arm/gate/cap chain (§E in `DEVELOPER_GUIDE.md`), not to invent a
  parallel one "for safety" — extra gates that weren't requested have already had to be explicitly
  removed once (premium-momentum's spec amendment). If a feature genuinely needs new protection,
  propose it and let the user decide.
- **A subagent panel that returns 0 completed agents is not a passed check.** If an adversarial-review
  or verification panel dies on a session/token limit with nothing completed, treat it as **unverified**,
  say so, and either retry, do the check yourself, or ask — never report it as a clean pass.
- **Flattrade MCP: never call its `login` or `logout` tools.** The user's AlphaForge login is the
  ONLY login (one API key ⇒ one redirect URI ⇒ the MCP cannot OAuth on its own, and a second login
  would invalidate AlphaForge's live token). Recover a stale MCP session with
  `backend/scripts/resync_mcp_session.py --clean`, never by logging in through the MCP. The
  assistant also never places/modifies/cancels orders through it — same rule as the app's own
  executor. Full rules: [`flattrade-mcp-integration.md`](flattrade-mcp-integration.md) §5.
- **Never create a second Flattrade API key.** API V2 allows one per account; a second requires the
  paid registered-algo tier (₹5,000+GST/exchange, for >10 orders/sec). The user has declined it.

## 5. Where to go deep

Start with the consolidated [`DEVELOPER_GUIDE.md`](DEVELOPER_GUIDE.md), then reach for the reference docs:

| I need to… | Go to |
|---|---|
| **Trust a backtest / optimizer number** (read before relying on any result) | [`BACKTEST_INTEGRITY_AUDIT.md`](BACKTEST_INTEGRITY_AUDIT.md) |
| Resume from the completed Stage 1 checkpoint | [`STAGE1_INTEGRITY_SESSION_HANDOFF_2026-08-01.md`](STAGE1_INTEGRITY_SESSION_HANDOFF_2026-08-01.md) |
| Know what to work on next | [`AGENT_TODO.md`](AGENT_TODO.md) §1 board · [`CAPABILITY_PHASE_PLAN_2026-07.md`](CAPABILITY_PHASE_PLAN_2026-07.md) |
| Avoid a trap a previous agent already hit | [`../learning_log.md`](../learning_log.md) · [`BACKTEST_INTEGRITY_AUDIT.md`](BACKTEST_INTEGRITY_AUDIT.md) §7 |
| Onboard deep: run/build/test, safety model, warehouse model, India rules, research→deploy, gotchas | [`DEVELOPER_GUIDE.md`](DEVELOPER_GUIDE.md) |
| See capabilities + the end-to-end workflow at a glance | [`PROJECT_OVERVIEW.md`](PROJECT_OVERVIEW.md) |
| Understand the data-warehouse completeness model | [`DEVELOPER_GUIDE.md`](DEVELOPER_GUIDE.md) (model) · [`ARCHITECTURE.md`](ARCHITECTURE.md) (technical) |
| Understand the **live-trading safety model** (promotion, live mode, kill switches) | [`forward-validation-policy.md`](forward-validation-policy.md) · [`DEVELOPER_GUIDE.md`](DEVELOPER_GUIDE.md) · [`STRATEGY_DEPLOYMENTS.md`](STRATEGY_DEPLOYMENTS.md) · [`ARCHITECTURE.md`](ARCHITECTURE.md) |
| See the India trading rules (session, DTE, holidays, lots) | [`DEVELOPER_GUIDE.md`](DEVELOPER_GUIDE.md) · code: `nse_calendar.py` |
| Trace the module map, data flow, Mongo collections, live-execution gate chain | [`ARCHITECTURE.md`](ARCHITECTURE.md) |
| Look up a backend HTTP route | [`API_REFERENCE.md`](API_REFERENCE.md) |
| Use a specific page in the UI | [`USER_MANUAL.md`](USER_MANUAL.md) |
| Write a custom strategy plugin | [`STRATEGY_PLUGINS.md`](STRATEGY_PLUGINS.md) |
| Understand the deployment model (modes, gates, kill switches, live) | [`STRATEGY_DEPLOYMENTS.md`](STRATEGY_DEPLOYMENTS.md) |
| Install / launch the app | [`LOCAL_SETUP.md`](LOCAL_SETUP.md) · [`STARTUP_MANUAL.md`](STARTUP_MANUAL.md) |
| Drive the optimizer / decide which controls add value | [`optimizer-user-guide.md`](optimizer-user-guide.md) · [`optimizer-decision-guide.md`](optimizer-decision-guide.md) |
| Run a live-money readback | [`live-readback-checklist.md`](live-readback-checklist.md) |
| Run the first market-hours validation of Phase 5B (paper day → live day) | [`phase5b-market-validation-runbook.md`](phase5b-market-validation-runbook.md) |
| Check whether premium-momentum live work is justified by evidence | [`PREMIUM_MOMENTUM_EDGE_VERDICT_2026-07.md`](PREMIUM_MOMENTUM_EDGE_VERDICT_2026-07.md) (failed gate + pre-registered revival criterion) |
| Understand the 5B multi-leg design + its live↔backtest parity divergences | `superpowers/plans/2026-07-15-premium-momentum-phase5b-execution.md` |
| Reference the Flattrade broker API | [`Resources/flattrade-pi-api/INDEX.md`](Resources/flattrade-pi-api/INDEX.md) (+ `catalog.json`, `endpoints/`) |
| Use / debug the **Flattrade MCP** (44 tools, token sharing, runbook, hard rules) | [`flattrade-mcp-integration.md`](flattrade-mcp-integration.md) · design: `superpowers/specs/2026-07-18-flattrade-mcp-token-share-design.md` |
| See versioned history / agent capabilities | [`../CHANGELOG.md`](../CHANGELOG.md) · [`../CLAUDE.md`](../CLAUDE.md) |
| Decode an `L##`/`O##`/`S##` finding-ID cited in a commit message | [`audit-report-2026-07.md`](audit-report-2026-07.md) (historical — all 88 findings now resolved, kept for the ID cross-reference) |

---

_Operational gotchas (Upstox chunking, F&O publish lag, lightweight-charts effect-dep stability, the stale-bundle reload) live in [`DEVELOPER_GUIDE.md`](DEVELOPER_GUIDE.md) → Gotchas & Known Issues — read them before touching warehouse, chart, or live code._
