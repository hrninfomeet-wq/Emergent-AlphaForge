# AlphaForge Trading Lab — Updated plan.md

## Deferred / Optional (revisit later)

These were explicitly deferred by the user; do not start them without confirmation.

- **Event-day blocking (RBI, FOMC, CPI, etc.)** — auto-pause deployments on calendar events. Requires a hand-curated event collection editable from the UI.
- **WebSocket reconnect storm investigation** — 3-hour live session on 2026-05-27 hit `reconnect_count = 15` with "All connection attempts failed" recovering. Needs root-cause work after evaluator ships.
- **Per-tick deployment evaluation** — only after `1m_close` mode is trusted, and only as a manual user switch.
- **Paper / recommendation modes for the deployment evaluator** — first slice ships strict `shadow` (journal only) mode.
- **Strategy Deployment evaluator → broker order execution** — never automatic; recommendation mode shows context, user clicks Take/Skip.
- **Online hosting / always-on uptime** — out of scope for now. Forward-test sessions will be intermittent because the local PC isn't always running. Forward metrics must be annotated with session completeness.

## Phase 4b — current work (2026-05-27)

User-confirmed slices in priority order. Each ships small, gets verified, then we move on.

1. **DONE:** 1m_close deployment evaluator (shadow mode, scheduler, time/option/score blockers, audit trail)
2. **DONE:** Approval UI — Pending Approval panel with Approve / Skip / Mark Blocked buttons; Evaluate-now button on each ACTIVE deployment; auto-refresh every 15s
3. **DONE:** Auto-square-off at 15:00 IST every market day (default ON, override per deployment with `risk.allow_overnight=true`); expiry-day cutoff at 15:00 IST blocks new signals on the deployment instrument's expiry day; `dte_filter` config (default 0-6) on deployments; `bar_ts` and `decision_ts` audit fields on every signal; `next_expiry_iso` recorded in audit context for traceability
4. **DONE:** Paper trade wiring on signal approval — when Approve fires AND `deployment.mode == "paper"`, auto-creates a paper trade with the chosen contract, lot size sourced from `option_contracts.lot_size` (Upstox-supplied), `risk.default_lots` (default 1) configurable per deployment. Trade carries `deployment_id` so square-off honors `allow_overnight`. Failure to create the trade does NOT roll back the approval — it journals a `paper_trade_error` for audit. UI shows mode badge on each pending signal so user knows whether Approve will create a trade.
5. **DONE:** Pre-flight data realism panel — `GET /api/deployments/preflight?instrument=...` returns a structured report with status (verified/warning/degraded), per-check details (spot coverage last 30 trading days, upcoming option expiries, active vs expired contracts, Upstox token state) and per-instrument structural break notes (NIFTY weekly day rotation, BANKNIFTY weekly discontinuation, SENSEX BSE Friday). Surfaced as a collapsible badge above the Create Deployment button. Informational only - never blocks creation. Also tightened the contract picker to filter expiry_date >= today, fixing the bug observed on 2026-05-28 where a live signal resolved to a Nov-2024 expired contract.
6. **DONE:** Data Hygiene workflow — `POST /api/data-hygiene/plan` computes the diff between desired warehouse state (default scope: 2024-11-27 -> today, NIFTY+BANKNIFTY+SENSEX, ATM CE+PE only) and what's actually stored. Returns prioritized fetch actions per instrument (spot, contracts, option_candles) with ETA hints. `POST /api/data-hygiene/execute` submits the suggested fetches as background jobs in dependency order (spot -> contracts -> option_candles). Re-running is safe; partial failures resume cleanly. `GET /api/data-hygiene/status` lists recent hygiene runs. 8 unit tests covering plan diff logic + execute order + error handling. Live verification: real diff against warehouse showed NIFTY 94.1% / BANKNIFTY 0% contracts / SENSEX 0% contracts, with correct action ordering.

6.5. **DONE:** Live tick -> 1m OHLC roller (`backend/app/live_candle_roller.py`). Subscribes to the WS broadcast and aggregates ticks into per-minute OHLC bars for NIFTY/BANKNIFTY/SENSEX. Bars are upserted into `candles_1m` so the deployment evaluator transparently fires on intraday bars during market hours. Closes a real gap discovered 2026-05-29: Upstox historical endpoint returns empty for the same trading day, which left the evaluator stuck on yesterday's data. New routes: `GET /api/live-candles/status`, `POST /api/live-candles/start`, `POST /api/live-candles/stop`. Auto-starts at backend startup right after WS auto-start. Auto-flushes on shutdown so partial buckets are not lost. 8 unit tests covering bucket aggregation, minute rollover, idempotent upserts, drop of unrelated instrument keys, multi-instrument independence, restart resume, shutdown flush.

7. **NEXT:** Slippage model with configurable defaults — ATM 0.5pt, OTM1 1pt, OTM2+ 2pt, expiry-day-30min 2x multiplier; user override per backtest. Plus a post-hoc volatility detector (5min realized / 30day average; >2.5x triggers wide-spread flag) since reliable event calendar is not feasible.
8. Strategy source SHA hash — auto-pause deployment with reason `strategy_source_drift` if the strategy file's hash changes after deployment creation
9. Acknowledgment checkbox on deployment creation when source backtest has worrying signs (walk-forward divergence > 30%, low trade count, etc.) — does NOT block, just forces conscious choice
10. Forward metrics aggregation per deployment — win-rate, avg P&L, profit factor — annotated with session completeness (≥70% of 10:00-15:00 IST = "complete"); surfaced in Strategy Library only when ≥10 complete sessions
11. **DONE:** Idempotency hardening — added unique partial index `signals(deployment_id, candle_ts)` (partial: only enforced when deployment_id is a string, so manual research signals are unaffected). Evaluator catches Mongo duplicate-key errors and treats them as `already_journaled`, advances `last_evaluated_ts` to prevent retry loops. Index created live on the running DB and added to `ensure_indexes()` so future boots reapply it. 1 unit test (155 total passing).
12. Per-deployment kill switches — auto-pause on `max_consecutive_losses`, `daily_loss_cutoff_pct`, `max_open_paper_trades`. User-configurable per deployment.

## Data hygiene baseline (pre-requisite for serious backtesting)

User-confirmed scope: store all three index spot + ATM option data for **2024-01-01 to 2026-05-26**.

- Spot 1-minute candles: NIFTY, BANKNIFTY, SENSEX
- Option contract metadata: every expiry in the range, including expired (use `/api/upstox/expired-options/contracts/{instrument}/sync`)
- Option 1-minute candles: ATM CE/PE, sample=1
- Audit trust gate: Planned coverage = 100%, Need fetch = 0, Missing meta = 0 before any backtest can use that window
- Annotate known structural breaks in the data realism panel:
  - NIFTY weekly expiry day rotated: Thu (until 2024-08) → Wed (2024-09 to 2025-03) → Tue (2025-04+)
  - BANKNIFTY weekly options discontinued 2024-11; only monthly after that
  - SENSEX weekly on BSE, Friday expiry, lower liquidity than NIFTY

## Audit trail invariants (every signal must carry)

- `bar_ts` — the candle minute the strategy evaluated against
- `decision_ts` — wall-clock when the evaluator decided
- `strategy_id`, `strategy_version`, `strategy_hash` (over id+version+params)
- `pretrade_profile_name` + full `pretrade_settings_snapshot` resolved at signal time
- `regime` at the time of evaluation
- `option_contract` chosen with strike + side + instrument_key
- `tracked_for_pnl` flag — false when `option_no_data` or `concurrency_lower_score` or `manual_block`
- All blockers as a list of human-readable strings


## Objectives (Updated)
- Build a local-first and cloud-ready **React + FastAPI + MongoDB** trading lab for Indian indices (**NIFTY 50, BANKNIFTY, SENSEX**).
- Prove the full quant workflow end-to-end:
  - ingest candles → store locally → indicators → strategy plugins → backtest → walk-forward IS/OOS → robust metrics + equity/drawdown
- Provide a modular **Python strategy plugin system** usable for **backtest / optimize / live**.
- Provide a **one-click Auto-Optimizer** (Bayesian + Grid + Genetic) that automatically tunes parameters and measures robustness.
- Provide disciplined live options BUY recommendations (Upstox WS) with full **audit trail**, **configurable pre-trade checklist**, and **paper trading**.
- Ensure the entire stack can be run locally on a PC via **Docker Compose**, with complete handover documentation for a new AI agent.

---

## Context, Credit, And Agent Usage Policy

This project should be developed in small verified slices so long chats and unnecessary model usage do not waste credits.

### When to compact or fork the chat
- Compact/export/fork at natural checkpoints: after a feature is verified, after docs are updated, before starting a major new subsystem, or when the chat becomes too long for quick reasoning.
- Do not fork in the middle of broker auth, database mutation, live-stream debugging, or a half-finished code edit.
- The new chat should treat the repository and docs as the source of truth, not the old conversation history.
- Start a new chat by reading, in order:
  1. `docs/HANDOFF.md`
  2. `plan.md`
  3. `docs/PROJECT_OVERVIEW.md`
  4. `docs/ARCHITECTURE.md`
  5. The relevant code/tests for the next planned task

### Credit-efficient execution rules
- Always inspect what is already implemented before changing code.
- Use local project docs, tests, and code first; browse only for current broker/API behavior, changed exchange rules, or other time-sensitive facts.
- Use low/minimal reasoning for status checks, file search, docs edits, formatting, and repeated verification.
- Use medium reasoning for normal backend/frontend implementation where the expected behavior is already clear.
- Use high reasoning only for trading-critical design: broker WebSocket behavior, data integrity, option expiry resolution, order lifecycle, risk controls, strategy correctness, and architecture decisions.
- Prefer targeted tests during development, then run the full backend test suite, frontend build, API smoke test, and browser check at the end of each feature slice.

### Subagent / specialist usage
- Use subagents only for independent workstreams: code review, frontend visual QA, backend API review, docs audit, or isolated strategy research.
- Do not use subagents for credential handling, tightly coupled edits in the same files, database clearing, live broker account actions, or destructive operations.
- Each subagent result must be reviewed before code changes are accepted into the main workspace.
- Keep handoff notes short and current so a new model can resume from files instead of reading a long chat.

---

## Phase 1 — Core POC (Isolation: data → backtest → walk-forward)
**Status:** ✅ COMPLETE

### What was delivered
- Single-file E2E proof: `backend/test_core.py`
- yfinance ingestion → MongoDB persistence → vectorized indicators → Confluence Scalper port → backtest with realistic costs → walk-forward IS/OOS → equity curve + significance

### Success criteria (met)
- Ingest persists and reuses candles.
- Backtest returns trades + key metrics + equity curve.
- Walk-forward returns IS/OOS + stitched OOS equity.
- Costs visibly change outcomes.

---

## Phase 2 — V1 App Development (Backtest Lab + Warehouse v2 + Plugins + Regime)
**Status:** ✅ COMPLETE

### What was delivered
- Backend:
  - Warehouse v2: `candles_1m`, `warehouse_runs`, `integrity_hashes`, coverage map.
  - Regime detection: ADX + Choppiness + ATR expansion.
  - Backtest engine: SPOT-mode, realistic cost proxy (NIFTY/SENSEX 1.5 pts, BANKNIFTY 4 pts).
  - Walk-forward validation + divergence warning.
  - Statistical significance badge (Wilson 95% CI).
  - 6 built-in strategies + custom plugin auto-discovery.
- Frontend:
  - Bloomberg-style dark dashboard.
  - Backtest Lab with multi-pane TradingView Lightweight Charts.
  - Data Warehouse page with coverage heatmap + ingest controls.
  - Strategy Library.
  - Pre-trade checklist page.
  - Signal Journal page.

### Success criteria (met)
- Multiple strategies available and reproducible outputs saved.
- Warehouse heatmap visible.
- Charts responsive.
- Testing agent passed core flows.

---

## Phase 2.5 — UX/Workflow Polish (Backtest Lab)
**Status:** ✅ COMPLETE

### What was delivered
- Combined **slider + manual numeric input** for parameter tuning (`NumberSliderInput`).
- Backtest date window selection (IST dates → start_ts/end_ts).
- Load past runs in Backtest Lab.
- Export buttons: Config JSON, Result JSON, Trades CSV.

---

## Phase 3 — Optimizer (Grid + Bayesian + Genetic + Walk-forward)
**Status:** ✅ COMPLETE

### What was delivered
- Backend:
  - Optimization job runner in `app/optimizer.py`.
  - Methods: **Bayesian (Optuna TPE)**, **Grid**, **Genetic (CMA-ES)**.
  - Objectives: risk_adjusted (default), sharpe, profit_factor, total_pnl_pts, win_rate, neg_max_dd.
  - Artifacts: parameter importance, robustness score, 2D heatmap, top-N alternatives.
  - Apply best as preset: `/api/optimize/apply-as-preset/{job_id}`.
- Frontend:
  - Full Optimizer page: progress polling, best-so-far, robustness, importance, heatmap, alternatives, job history.

### Success criteria (met)
- Optimizer completes N trials; stores artifacts; best params reproducibly improve metrics.

---

## Phase 3.5 — User Feedback Fixes (Optimizer + Presets + Stop)
**Status:** ✅ COMPLETE

### What was delivered
- **Progress bar fill fixed** (added `.bg-info` utility).
- **Stop/Cancel optimization**:
  - Backend endpoint: `POST /api/optimize/jobs/{job_id}/cancel`.
  - Worker checks cancel flag every ~5 trials; best so far preserved.
  - UI stop button visible while running.
- **Optimizer now saves best run as full backtest**:
  - `best_backtest_run_id` created automatically with trades + equity + walk-forward.
  - UI button: **View Best in Lab**.
- **Optimizer exports**:
  - Config JSON, Result JSON, Alternatives CSV.
- **Preset usability end-to-end**:
  - Preset created from optimizer visible in Optimizer sidebar.
  - Backtest Lab has **Load preset (optimized params)** dropdown.
  - Deep-link supported: `/backtest?preset=<name>`.
- **Signal Journal upgraded**:
  - filter + bulk delete + click-to-load run via `/backtest?run=<id>`.

---

## Phase 7 — Local Deployment Package (Windows-first)
**Status:** ✅ COMPLETE (delivered early; locally verified on Windows Docker Desktop)

### What was delivered
- `docker-compose.yml` (mongo + backend + frontend).
- `backend/Dockerfile` (Python 3.11 + uvicorn).
- `frontend/Dockerfile` (node build → nginx serve) + `frontend/nginx.conf`.
- `start.sh` (Mac/Linux) + `start.bat` (Windows).
- `backend/.env.example` + `frontend/.env.example`.
- Root `.gitignore`.
- `frontend/yarn.lock` for reproducible frontend Docker builds.
- Local bootstrap tests for syntax, env templates, dependency contract, and Docker Compose schema.

---

## Documentation Suite (New)
**Status:** ✅ COMPLETE

Delivered full handover-quality docs:
- `README.md`
- `docs/ARCHITECTURE.md`
- `docs/HANDOFF.md`
- `docs/DEVELOPMENT_JOURNEY.md`
- `docs/STRATEGY_PLUGINS.md`
- `docs/API_REFERENCE.md`
- `docs/LOCAL_SETUP.md`
- `CHANGELOG.md`

---

## Phase 4 — Upstox Live + Paper + Audit Trail + Options Backtest (BIGGEST)
**Status:** ⚠️ PARTIAL PHASE 4 SCAFFOLD (OAuth, REST historical ingest, read-only WebSocket tick stream foundation, option data workflows, offline signal lifecycle, paper-trading foundation, and Strategy Deployment management present; deployment evaluator not complete)

### User stories
1. As a user, I can complete Upstox OAuth and start WS tick streaming.
2. As a user, I see live options BUY signals (CE/PE) with suggested strike and full context.
3. As a user, I can configure the pre-trade checklist (profiles + thresholds) and see why signals were blocked.
4. As a user, I can one-click **Deploy to Paper** and track live P&L.
5. As a user, every signal is logged with a full snapshot for later audit.
6. As a user, I can backtest **weekly expiry options** (paired INDEX+OPTION legs) using real option candles.
7. As a user, I can create a forward-test Strategy Deployment only from a saved preset or saved backtest result.

### Implementation steps (revised)
- **Upstox OAuth**
  - ✅ Endpoints exist:
    - `/api/upstox/auth/start` (redirect)
    - `/api/upstox/auth/callback` (exchange code → encrypted token storage)
  - ⚠️ Needs real-credential validation and refresh/reconnect hardening.
- **Upstox REST historical ingest**
  - ✅ 1m index candle ingest scaffold exists and writes to the same warehouse.
  - ⚠️ Needs credential validation, rate-limit testing, and data-quality checks.
- **Upstox WS (tick stream)**
  - ✅ Initial read-only V3 market-data stream implemented.
  - ✅ Start/stop/status/latest-ticks APIs exist.
  - ✅ Uses Upstox V3 authorized WebSocket URL, binary JSON subscription message, protobuf tick decoding, reconnect/backoff, and sanitized `ticks` persistence.
  - ✅ Market Header prefers fresh ticks and falls back to REST/API sources when ticks are stale or unavailable.
  - ⏳ Needs multi-session live hardening and latency/reconnect observation.
  - ⏳ Dynamic ATM±5 options universe subscription is still pending.
- **Options universe + contracts**
  - Daily/weekly expiries selection and strike selection.
  - Store instruments/contract metadata (new: `contracts`).
  - Option Data Planner is the selected-moneyness trust gate. Default planning is ATM only; OTM/ITM selections are explicit.
  - Raw Option Universe Audit is a broad warehouse diagnostic and may show missing contracts that were never selected by the planner.
- **Signal lifecycle state machine (persistent)**
  - ✅ Offline foundation exists: WATCHING → FORMING → CONFIRMED → TRIGGERED → ACTIVE → EXITED → AUDITED.
  - ✅ Stores signal transition history in `signals`.
  - ⏳ Needs Strategy Deployment evaluator to create signals from strategy output rather than manual research input.
- **Paper trading engine**
  - ✅ Manual deploy-to-paper foundation exists.
  - ✅ Persist paper trades, mark outcomes, close trades, and auto-close on stored stop/target.
  - ⏳ Needs live tick/stored replay marks, trailing exits, and daily risk controls.
- **Strategy Deployments**
  - ✅ Design documented in `docs/STRATEGY_DEPLOYMENTS.md`.
  - ✅ Backend model/routes persist deployments in `strategy_deployments`.
  - ✅ Live Signals panel creates deployments from saved presets or saved backtest results.
  - ✅ Pause/resume/archive status controls exist.
  - First confirmation mode: completed `1m_close`.
  - Per-tick evaluation is a later manual switch after the strategy is trusted.
  - Every signal requires manual approval before paper deployment or recommendation action.
  - Default option moneyness: ATM; configurable: ATM, OTM1, ITM1.
  - Blocked signals must be recorded and identifiable.
  - Prefer fewer cleaner signals over every weak setup.
  - ⏳ Needs evaluator to produce clean/blocked signals from completed 1-minute candles.
- **OPTIONS BACKTEST (port semantics from reference Node repo)**
  - Use real expired-option candles via Upstox V3 expired-instruments API.
  - Paired INDEX + OPTION legs.
  - Two risk modes:
    - `spot_points` (INDEX_ONLY exit)
    - `option_premium_pct` (OPTION_ONLY exit)
  - Store options candles in Mongo (new: `options_1m`).
- **Frontend**
  - ✅ Live Signals placeholder replaced with offline lifecycle console.
  - ✅ Paper Trading placeholder replaced with paper journal and risk badges.
  - ✅ Strategy Deployment management UI added to Live Signals.
  - ⏳ Wire Live Signals to deployment evaluator and, later, WebSocket ticks.
  - ⏳ Add paper replay UI.
- **Testing**
  - Run `testing_agent_v3` end-to-end.

### Reference (must read before implementing)
User’s legacy repo: https://github.com/hrninfomeet-wq/project-deepseek-version
- `server/research/pluginHistoricalBacktest.js`
- `server/paper/paperBroker.js`
- `server/live/liveSession.js`
- `server/upstox/oauth.js`, `server/upstox/websocket.js`

### Success criteria
- Stable WS streaming (no REST hammering).
- Live signals auditable + replayable.
- Strategy Deployments are auditable back to saved preset/backtest result and frozen params.
- First forward testing runs on completed 1-minute candles and requires manual approval.
- Options backtest matches the paired-leg semantics from legacy repo.

---

## Phase 5 — Profitability Boosters (Probability Engine + Meta-model)
**Status:** ⏳ NOT STARTED

### User stories
1. Each signal shows **time-bound probabilities** for targets/stops.
2. Targets/stops adapt to **VIX + DTE + regime**.
3. “What-if” P&L reports for taking all vs filtered signals.
4. Adaptive quality scoring by strategy+regime.
5. Optional Telegram alerts.

### Implementation steps (revised)
- **Probability engine (Kaplan–Meier survival analysis)**
  - Requires ≥6 months of signal history.
  - Similarity filters: instrument, regime, VIX bucket, DTE, time-of-day, setup signature.
- **Meta-model**
  - Learn which strategies to enable per regime (logistic regression / LightGBM).
- **Position sizing**
  - Kelly fraction + equity-curve learning + daily loss cutoff.
- **Event calendar filter**
  - RBI/FOMC/CPI auto-block window.
- **India VIX overlay**
  - Adjust target/stop multiples by VIX percentile.
- Run `testing_agent_v3`.

### Success criteria
- Probabilities computed from sufficient historical samples (transparent warnings when sample is insufficient).
- Signal EV/robustness visible.

---

## Phase 6 — Swing/Positional Extension
**Status:** ⏳ NOT STARTED

### User stories
1. Backtest swing strategies on **1H/1D** with gap handling.
2. Positions span days with multi-day lifecycle.
3. Swing-specific strategies selectable and optimizable.
4. Warehouse stores daily candles with integrity.
5. Swing results include regime segmentation.

### Implementation steps
- Add higher TF ingestion/resampling + swing risk/exit models.
- Add swing plugins + walk-forward tuned for longer windows.
- Run `testing_agent_v3`.

### Success criteria
- Swing runs reproducible; gaps handled; audit trail intact.

---

## GitHub Delivery (Outstanding)
**Status:** ⏳ LOCAL CHANGES PENDING PUSH

### Goal
Push the local repair/bootstrap changes back to the user’s GitHub repo when the user is ready.

### Implementation steps
1. Review `git diff`.
2. Commit with a message such as `Repair local bootstrap and update handoff status`.
3. Push to `hrninfomeet-wq/Emergent-AlphaForge`.

### Success criteria
- Repo contains:
  - backend + frontend + docs + docker-compose + scripts
  - tags or releases (optional)
  - README visible and correct

---

## Next Actions (Immediate)
1. ✅ Local Docker stack verified on this PC.
2. ✅ Handoff/status docs reconciled with current code reality.
3. ⏳ Validate Upstox OAuth locally with real credentials.
4. ⏳ Harden Phase 4b WebSocket tick stream and build live signal evaluator.
5. ⏳ Push local bootstrap/status repair changes to GitHub when approved.
