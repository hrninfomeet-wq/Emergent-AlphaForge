# AlphaForge onboarding review — 2026-10-21

Purpose: establish a checked starting point for future improvements to result fidelity and live-money safety. This is an onboarding review, not authorization to change trading behavior.

## Checkpoint and scope

- Confirmed: checkout is `main` at `ff9cbe3` (checked with `git branch --show-current` and `git log`). Existing changes to `learning_log.md` and untracked closing-range research files predate this session; preserve them.
- Read order completed: `HANDOFF.md`, `AGENT_TODO.md`, `BACKTEST_INTEGRITY_AUDIT.md`, recent `CHANGELOG.md`; domain traces continue below.
- No application source changes, service startup/rebuild, authentication, broker calls, deployment changes or order actions are authorized by this onboarding task. Closing-range forecasting is excluded.
- Approval boundary: show a proposed diff and wait for operator approval before modifying kill-switch, latch or deploy-validation code. Shared research-engine changes also require before/after evidence and approval under the handoff conventions.
- Confirmed: Docker inspection could not connect to `dockerDesktopLinuxEngine`; `GET http://127.0.0.1:8001/api/health` was refused. Current running-image parity and saved-record replay are not established. No services were started to overcome this limitation.

## Modular workflow and delegation

Each assignment is bounded to a document/source pass, a safe verification pass, and a report. Stop when the relevant evidence is recorded; do not expand into fixing defects or strategy tuning.

| Piece | Cost of a wrong conclusion | Assignment prompt | Done check | Status |
|---|---|---|---|---|
| Safety and deployment validation | Moves money; critical; primary agent retains ownership | Trace paper/live state writers and readers, derive entry-block conditions, inspect isolated tests, and verify behavior without touching a running session. | Gate map plus independent state checks; real-session limits named. | Complete within offline scope |
| Backtest actions | Drives future decisions | Trace each saved-run action from selected run through display/export/preset/deploy data; run inspected synthetic tests; distinguish historical fixes from present reproduction. | Evidence and bounded test result for issue 2. | Sol report reviewed; backend full-run retrieval checked separately |
| Optimizer and strategy scale | Drives future decisions | Trace controls, effective search space, incumbent seeds, ranking and units; inspect Explosive Reversal variants; run inspected offline tests. | Evidence status for issues 3–5; missing replay inputs named. | Sol report reviewed; requested behavioral toggle check supplied |
| Architecture and startup | Costs redone work | Verify stack, routes and data flow against source; check launcher default versus rebuild behavior. | Source-backed architecture map. | Luna report reviewed against launcher, dependencies and routers |
| Review and continuity | Costs redone work | Recheck load-bearing findings, record unresolved reproductions and risks, append the session lesson without altering prior history. | Reviewed report linked from the work board and learning log. | Complete |

Primary review must approve evidence or request revision before merging agent findings. Historical suite counts and past profit figures are not current verification.

## Safety findings

- Confirmed: `LiveEngine.can_trade()` refuses on any of the in-memory halt, persisted engine halt, or reset latch (`backend/app/live/engine.py:342`). The explicit reset route calls the persisted reset and then engine resume (`backend/app/routers/live_broker.py:1890`). Paper-panel Resume is a separate deployment-status operation.
- Confirmed: current parameter validation collects numeric search-range violations, while retaining type, finite-value and positive-floor failures (`backend/app/runtime.py:2400`). Creation puts range findings into the acknowledgment warning chain (`backend/app/routers/deployments.py:583`). Actual wizard behavior remains unverified.
- Confirmed: isolated local command `.venv/Scripts/python.exe -m pytest tests/test_kill_switch_halt_reset_contract.py tests/test_deploy_param_range_is_advisory.py -q -p no:cacheprovider`, with plugin autoload disabled, returned **23 passed**. The latch tests use fake collections and a mock read-only broker; the range tests are source assertions, not end-to-end proof.

**Confirmed: a paper-labelled control reaches a live-flatten route.** The visible button says `Stop ALL paper trading` (`frontend/src/components/paper/DeploymentControlStrip.jsx:172`), and its confirmation repeats that scope (`frontend/src/pages/PaperTrading.jsx:495`). The mounted callback calls `api.stopAllDeployments`, mapped to `/deployments/stop-all` (`frontend/src/lib/api.js:237`). That route selects live-mode deployments, calls `_square_live_positions_for_deployment`, demotes them and pauses active deployments (`backend/app/routers/deployments.py:1231`). This is a verified source/label mismatch, not an executed flatten. Do not use the paper page as a sandbox for this control.

The primary independently derived the entry gate as `allowed = not(memory_halt or persisted_halt or latch)`, then executed the real `LiveEngine.can_trade()` against all boolean combinations using a synthetic config reader and no broker client. Confirmed: all **8 combinations** agreed; only all-clear permitted entry. Separately, the real `_set_deployment_status` function was AST-extracted without importing application runtime and executed against an in-memory collection: `live/ACTIVE -> paper/PAUSED -> paper/ACTIVE`. Resume did not restore live mode.

Confirmed: the same isolated extraction executed the current parameter validator with a synthetic positive numeric schema having search bounds 10–200. Values 5 and 205 returned range findings; 10 and 200 passed without findings; 0, -5, infinity, NaN, boolean and numeric text were rejected. These **10 cases** check the helper, not the mounted wizard, acknowledgment submission or every strategy's feasibility.

## Architecture verified for this task

Confirmed against dependency files, router registration and the referenced source paths:

| Layer | Current structure and truth-bearing boundary |
|---|---|
| UI | React + CRA/craco, Tailwind; pages registered in `frontend/src/App.js`. Current paper route is `/paper`, not `/paper-trade`; deploy wizard is `/live`, broker cockpit `/live-trading`. |
| API/storage | FastAPI and Motor/MongoDB; pandas/NumPy computation and Optuna optimization. Compose publishes frontend, API and MongoDB on loopback. `backend/server.py:350` currently mounts nine routers, including scalping; older docs list eight. |
| Warehouse | `candles_1m`, `options_1m`, `option_contracts`; warehouse ingestion, hygiene and completeness feed research. Upstox is the principal data provider; Flattrade execution and historical fallback code are separate capabilities. No feed was contacted here. |
| Research | `backtest.py`, `option_backtest.py`, `premium_trigger_dispatch.py`, `optimizer.py`, WFO modules; saved evidence in `backtest_runs` and `optimization_jobs`. Spot, paired-option and premium-native results have different envelopes and units. |
| Preset/deployment | `presets` carries parameters and execution settings. Deployment creation resolves its source and pins parameters/version/source evidence in `strategy_deployments`; evaluator consumes closed bars. Paper and live share deployment administration, but use different trade journals. |
| Execution/exits | `auto_live` and `live_deploy_context` feed `live/executor`; `live/live_position_guard` manages exits. `exit_controls.effective_premium_stop` is the shared exit decider described in the architecture. Account entry stops are distinct from paper deployment loss governors. |

Confirmed: `start-app.bat:203` reuses healthy services and can rebuild only the frontend. `--rebuild` takes the full-stack build path (`:263` onward). Thus the owner's statement that launching always rebuilds both is not true of current source. Running-image freshness remains unknown.

## Owner-reported issues, in original order

1. **Latch/kill-switch interaction — not reproduced end to end.** Confirmed: the current reset contract and isolated state checks pass. Paper Resume changes deployment status; it does not directly trip/reset the account latch. The paper loss governor explicitly skips non-paper modes (`deployment_kill_switch.py:201`). This does not rule out re-latching by a supervisor or a stale running build. Needed: a safely isolated replay of the exact sequence with each stop's value/reason before and after each transition. Never reproduce it against live exposure.

2. **Journal actions — reported mismatch not reproduced on saved records.** Confirmed: pane/CSV use `displayTrades` and `joinOptionLegs`, with sparse legs joined by identity (`backtestMetrics.js`, `exports.js:177`); local synthetic tests pass. Config exports requested config plus `params_applied`; Result exports the full document. Preset uses the loaded run's applied params and resolved sizing; Deploy passes a run ID and the backend re-fetches its full document (`runtime.py:2530`, `strategy_deployments.py:95`). Likely: reopened form sizing or wizard prefill can diverge from the saved effective execution, because `BacktestLab.jsx:873` restores requested sizing and `LiveSignals.jsx:502` consumes execution settings omitted by the summary list projection (`research.py:537`). These are future reproduction targets, not confirmed runtime defects.

3. **Confluence optimization regression — not reproduced.** Confirmed: current fresh-study code seeds presets/prior winners/defaults, but Grid enumerates its own combinations and option reranking considers a spot-selected shortlist (`optimizer.py:2085`, `:2135`, `:2405`). Seeds therefore do not guarantee the old option winner is compared in the final stage. Likely: differing effective configuration or shortlist exclusion can explain a worse outcome, because either changes the actual comparison. I don't know the cause of the owner's specific regression. To find out: replay the old preset/run and new job on identical recorded inputs and compare config, `param_space`, `incumbent_seeds`, `trial_log`, rerank coverage and selected run. Meanwhile: Confirmed, the mechanisms above exist in the current source; button causation is unproven.

4. **Explosive Reversal instrument disparity — scale mechanism confirmed; P&L disparity not replayed.** Confirmed: legacy `explosive_reversal` emits absolute point exits and currently declares NIFTY-only deployment support (`explosive_reversal.py:93`, `:216`); research on other instruments remains possible. ATR and SENSEX-specific variants already exist. Likely: fixed point geometry contributes to the disparity because relative stop/target distances change with instrument scale. No strategy logic was changed, and no profitability or cross-index transfer claim follows from this observation.

5. **Indicator toggle/advanced bounds — blanket no-op hypothesis not supported by isolated execution.** Confirmed: real Confluence schema plus real space builder yields **11 dimensions OFF and 19 ON**; OFF still searches the strategy-declared EMA periods. ON adds the other catalog periods (`optimizer.py:606`). A min/max override and a fixed override changed the effective space; changed period values changed synthetic indicator output. Closing the advanced panel does not clear stored overrides. These checks establish helper semantics, not browser-to-saved-job fidelity. A toggle label that the user reads as freezing every indicator period needs explicit clarification before any behavior change.

6. **Deploy search bounds — desired numeric-validator behavior already present locally; wizard incident not reproduced.** Confirmed: positive out-of-search-range values yield findings, while tested infeasible/type/non-finite inputs fail. Creation adds those findings to the existing `acknowledged_warnings` chain (`deployments.py:583–653`), and the UI contains the acknowledgment control (`LiveSignals.jsx:1296`). No deployment was submitted. Any proposed validation change still requires a displayed diff and approval.

## Reproducible offline checks

All were inspected before execution. No broker, production database or application startup was used. Set `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`; tests below use synthetic data, pure functions, fake collections or Node-based UI helpers.

| Command after `.venv/Scripts/python.exe -B -m pytest` | Observed result |
|---|---|
| `tests/test_kill_switch_halt_reset_contract.py tests/test_deploy_param_range_is_advisory.py -q -p no:cacheprovider` | 23 passed; initial command omitted `-B` |
| `tests/test_backtest_lab_action_buttons.py tests/test_premium_native_result_surfacing.py -q -p no:cacheprovider` | 48 passed; bytecode disabled through environment |
| `--noconftest -p no:cacheprovider -q tests/test_incumbent_seed.py tests/test_bounds_unit.py tests/test_optimizer_bounds_instrument_guard.py` | 81 passed |
| `--noconftest -p no:cacheprovider -q tests/test_indicator_equivalence.py` | 7 passed |

Additional in-memory demonstrations: entry-state truth table, pause/resume transition, validator boundaries, real Confluence space extraction, indicator recomputation, Grid seed exclusion and rerank shortlist exclusion. These are bounded observations; no full-suite, production replay or live certification is claimed.

## Risks and next-session boundary

- **Critical — live safety:** Confirmed source/label mismatch on Stop ALL paper trading. Treating it as paper-only can trigger real exits when live deployments exist. No corrective diff was applied.
- Confirmed: broker OCO defaults off (`live_deploy_context.py:58`); software exits depend on the backend. I don't know current live exposure or deployed protection settings. To find out: operator-controlled read-only broker/guard reconciliation on the registered static IP. Meanwhile: Confirmed, this session neither started nor rebuilt the backend.
- Confirmed source-level P&L gap: `_live_guard_on_close` passes no proven filled quantity (`runtime.py:398`), and `realized_fields` falls back to journal quantity (`live/close_loop.py:83`). If a partial fill reaches this path, reported realized P&L can use ordered rather than filled quantity. This matches the work board's existing open item; no new incident was replayed.
- Likely: some owner symptoms may describe a stale running build, because historical fixes exist and launcher defaults preserve a healthy image. If wrong, reproducing exact records will expose the surviving defect; do not close an issue based on this inference.
- Not done: exact UI/database reproduction of issues 1–4 and 6, or end-to-end search-control verification for issue 5 — local services unavailable and saved comparison records not accessed. Next work starts with recorded evidence and a safe isolated reproduction, not a live enable/reset/kill action.

Self-review: the conclusion-changing claim would be that offline/source evidence certifies the running app. It does not: image parity, real saved artifacts, concurrent supervisors and broker state were not observed. Findings above explicitly separate executable helper checks, source wiring and unverified runtime effects. No source fix, strategy tuning, schema change, rename, commit or push was performed.

Core lesson: trace the actual data envelope and action scope behind each screen label. Confirmed approaches: isolated executable checks plus source tracing; separate requested configuration from resolved execution. Dead ends to avoid: trusting historical fixes as current reproduction, assuming the paper page is isolated from live controls, and attributing a weaker optimizer winner to the button before comparing effective inputs and ranking stages.
