# AlphaForge Trading Lab — plan.md

## Objectives
- Rebuild the existing Node/SQLite research dashboard into a local-first **React + FastAPI + MongoDB** trading lab.
- Prove the **core workflow** first: ingest candles → store locally → run vectorized backtest → walk-forward OOS → metrics + equity/drawdown series.
- Provide a modular **strategy plugin system** (drop-in Python) usable for backtest/optimize/live.
- Add disciplined live options BUY recommendations (Upstox WS) with full **audit trail** and configurable pre-trade checklist.

---

## Phase 1 — Core POC (Isolation: data → backtest → walk-forward)
**Why:** hardest/failure-prone (data correctness + backtest accounting + OOS) must be solid before UI/build-out.

### User stories
1. As a user, I can fetch **NIFTY 1m** data (last ~30 days) from yfinance and persist it locally.
2. As a user, I can run a **single strategy plugin** on stored candles and get trades + summary.
3. As a user, I automatically get **walk-forward IS vs OOS** results and divergence warnings.
4. As a user, I get realistic P&L with **slippage + costs** applied.
5. As a user, I can rerun the backtest offline without re-fetching data.

### Implementation steps
- Web research (quick): best practices for vectorized backtesting, walk-forward splits, and slippage modeling.
- Backend (FastAPI):
  - Minimal models: Candle, Trade, BacktestConfig, BacktestResult.
  - Mongo time-series collections: `candles_1m` (yfinance source).
  - Data ingest endpoint: `/api/poc/ingest` (symbol, range, interval=1m).
  - Strategy plugin loader (single folder) + schema validation.
  - Vectorized backtest runner (1 strategy): generate entries/exits, compute equity + drawdown.
  - Walk-forward: rolling 80/20 (configurable) returning stitched OOS equity.
  - Costs model: spread/slippage proxy + brokerage bundle.
- Frontend (minimal React page):
  - Buttons: Ingest → Run Backtest.
  - Display: key metrics, IS vs OOS table, equity/drawdown series JSON preview.
- Tests: unit tests for ingest normalization, backtest determinism, walk-forward split.
- Gate: do not proceed until POC produces stable results end-to-end.

### Success criteria
- Ingest persists and reuses candles (no refetch needed for rerun).
- Backtest returns: trade list, win rate, PF, max DD, expectancy, equity curve.
- Walk-forward returns IS/OOS metrics and stitched OOS equity.
- Costs/slippage visibly change results (toggle on/off).

---

## Phase 2 — V1 App Development (Backtest Lab + Warehouse v2 + Plugins + Regime)

### User stories
1. As a user, I can choose **instrument + mode (Scalp/Intraday/Swing) + strategy plugin(s)** and run a backtest.
2. As a user, I can **enable/disable strategies** per run and save the run as a named preset.
3. As a user, I can view multi-pane charts: **price + entries/exits + equity + drawdown** synced by time.
4. As a user, I can open Data Warehouse and see a **coverage heatmap** and fill missing ranges.
5. As a user, I can see a **statistical significance badge** and OOS warnings on every run.

### Implementation steps
- Data Warehouse v2:
  - Collections: `candles_1m`, `candles_htf` (resampled), `options_1m` (placeholder until Upstox), `contracts`, `warehouse_runs`, `integrity_hashes`.
  - Integrity: per-day hash + gap detection + dedup report.
  - Incremental sync + token-bucket limiter (even for yfinance/future Upstox).
- Strategy system:
  - Plugin registry + UI strategy picker (multi-select).
  - Built-in strategies (initial set): VWAP Pullback, ORB, Confluence Scalper, SMC Sweep+FVG, Fib Pullback, VWAP Mean Reversion.
  - Custom plugin: drop-in folder (Phase 2) + optional UI upload stub (feature-flag).
- Regime detector (ADX + CHOP + ATR expansion) used for labeling and later gating.
- Backtest Lab UI:
  - Run config panel, results summary, trade table, export run.
  - Charts via Lightweight Charts + Recharts (heatmaps later).
- Run `testing_agent_v3` end-to-end.

### Success criteria
- Multiple strategies selectable per run, reproducible outputs saved.
- Warehouse heatmap shows gaps; “fill range” ingests only missing data.
- Charts render and stay responsive on large ranges.
- Testing agent passes core flows.

---

## Phase 3 — Optimizer (Grid + Bayesian + Genetic + Walk-forward)

### User stories
1. As a user, I can auto-optimize **one instrument + one mode + one strategy** with a trial budget.
2. As a user, I can choose optimization method: **Grid / Optuna Bayesian / CMA-ES Genetic**.
3. As a user, I see **live best-so-far params** and OOS score while optimizing.
4. As a user, I can view **parameter importance + robustness score**.
5. As a user, I can save best params as a preset and re-run backtest instantly.

### Implementation steps
- Optimization jobs stored in Mongo: config, trials, best result, artifacts.
- Objective functions: risk-adjusted return (default), PF, DD, Sharpe.
- Walk-forward as default evaluation; prune bad trials.
- Heatmaps (2D slice) + top-N leaderboard.
- Run `testing_agent_v3`.

### Success criteria
- Optimizer completes N trials without crashing; stores artifacts.
- Best preset reproducibly improves OOS metric vs baseline.

---

## Phase 4 — Upstox Live + Paper + Audit Trail + Configurable Checklist

### User stories
1. As a user, I can complete Upstox OAuth and start WS tick streaming.
2. As a user, I see live signals with strike suggestions and full context.
3. As a user, I can configure the **pre-trade checklist** (presets + per-filter thresholds) and see blocked reasons.
4. As a user, I can one-click **Deploy to Paper** and track live P&L.
5. As a user, every signal is logged with a full snapshot for later audit.

### Implementation steps
- Upstox OAuth + token storage (local env) + WS reconnect/backoff.
- Dynamic universe: underlying + ATM±5 options; persist ticks.
- Signal lifecycle state machine persisted: Watching→…→Audited.
- Pre-trade checklist engine:
  - Fully configurable thresholds + toggles; Conservative/Balanced/Aggressive presets.
  - Anti-overfilter safeguard: warn when trade frequency collapses.
- Paper journal + replay hooks.
- Run `testing_agent_v3`.

### Success criteria
- Stable WS streaming + no REST hammering.
- Signals, blocks, and paper trades all auditable and queryable.

---

## Phase 5 — Profitability Boosters (Probability Engine + Meta-model)

### User stories
1. As a user, each signal shows **time-bound probabilities** for targets/stops.
2. As a user, target/stop suggestions adapt to **VIX + DTE + regime**.
3. As a user, I can see “what-if” P&L if I took all/filtered signals.
4. As a user, the system adapts signal quality scoring by strategy+regime.
5. As a user, I can receive optional Telegram alerts for confirmed signals.

### Implementation steps
- Probability engine:
  - Placeholder synthetic in earlier phases; now real Kaplan–Meier from logged outcomes.
  - Similarity filters: instrument, regime, VIX bucket, DTE, time-of-day, setup signature.
- Meta-model: choose strategy per regime; prevent redundant ensembles.
- Position sizing: Kelly fraction + daily loss limit integration.
- Run `testing_agent_v3`.

### Success criteria
- Probabilities computed from sufficient historical samples with transparency.
- Signals show EV/robustness; what-if reports match stored outcomes.

---

## Phase 6 — Swing Extension

### User stories
1. As a user, I can backtest swing strategies on **1H/1D** with gap handling.
2. As a user, positions can span days with multi-day lifecycle tracking.
3. As a user, swing-specific strategies are selectable and optimizable.
4. As a user, the warehouse stores daily candles with integrity.
5. As a user, swing results include regime segmentation.

### Implementation steps
- Add higher TF ingestion/resampling + swing risk/exit models.
- Add swing plugins + walk-forward tuned for longer windows.
- Run `testing_agent_v3`.

### Success criteria
- Swing runs are reproducible; gaps handled; audit trail intact.

---

## Phase 7 — Local Deployment Package (Windows-first)

### User stories
1. As a user, I can run `docker-compose up` and open the app locally.
2. As a user, I can configure env keys via `.env` templates.
3. As a user, I can backup/restore the warehouse via export/import.
4. As a user, I can update the app without losing data.
5. As a user, troubleshooting steps resolve common OAuth/WS/data issues.

### Implementation steps
- Docker Compose: frontend, backend, mongo; volumes for persistence.
- Scripts: `start.bat`, `start.sh`; docs: `LOCAL_SETUP_GUIDE.md`.
- Release checklist + final `testing_agent_v3`.

### Success criteria
- Fresh Windows machine can run locally in <15 minutes.
- Data persists across restarts; OAuth redirect URIs documented.

---

## Next Actions (Immediate)
1. Implement Phase 1 POC backend + minimal frontend.
2. Run POC with real NIFTY data; fix until stable.
3. Only then proceed to Phase 2.
