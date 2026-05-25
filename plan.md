# AlphaForge Trading Lab — Updated plan.md

## Objectives (Updated)
- Build a local-first and cloud-ready **React + FastAPI + MongoDB** trading lab for Indian indices (**NIFTY 50, BANKNIFTY, SENSEX**).
- Prove the full quant workflow end-to-end:
  - ingest candles → store locally → indicators → strategy plugins → backtest → walk-forward IS/OOS → robust metrics + equity/drawdown
- Provide a modular **Python strategy plugin system** usable for **backtest / optimize / live**.
- Provide a **one-click Auto-Optimizer** (Bayesian + Grid + Genetic) that automatically tunes parameters and measures robustness.
- Provide disciplined live options BUY recommendations (Upstox WS) with full **audit trail**, **configurable pre-trade checklist**, and **paper trading**.
- Ensure the entire stack can be run locally on a PC via **Docker Compose**, with complete handover documentation for a new AI agent.

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
**Status:** ✅ COMPLETE (delivered early)

### What was delivered
- `docker-compose.yml` (mongo + backend + frontend).
- `backend/Dockerfile` (Python 3.11 + uvicorn).
- `frontend/Dockerfile` (node build → nginx serve) + `frontend/nginx.conf`.
- `start.sh` (Mac/Linux) + `start.bat` (Windows).
- `backend/.env.example` + `frontend/.env.example`.
- Root `.gitignore`.

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
**Status:** ⏳ NOT STARTED (Next major milestone; likely needs dedicated session)

### User stories
1. As a user, I can complete Upstox OAuth and start WS tick streaming.
2. As a user, I see live options BUY signals (CE/PE) with suggested strike and full context.
3. As a user, I can configure the pre-trade checklist (profiles + thresholds) and see why signals were blocked.
4. As a user, I can one-click **Deploy to Paper** and track live P&L.
5. As a user, every signal is logged with a full snapshot for later audit.
6. As a user, I can backtest **weekly expiry options** (paired INDEX+OPTION legs) using real option candles.

### Implementation steps (revised)
- **Upstox OAuth**
  - Add endpoints:
    - `/api/upstox/auth/start` (redirect)
    - `/api/upstox/auth/callback` (exchange code → token)
  - Store token in Mongo (`upstox_tokens`) with refresh handling.
- **Upstox WS (tick stream)**
  - Subscribe to underlying + dynamic ATM±5 options universe.
  - Auto-reconnect with exponential backoff.
  - Persist ticks to Mongo time-series collection (new: `ticks`).
- **Options universe + contracts**
  - Daily/weekly expiries selection and strike selection.
  - Store instruments/contract metadata (new: `contracts`).
- **Signal lifecycle state machine (persistent)**
  - WATCHING → FORMING → CONFIRMED → TRIGGERED → ACTIVE → EXITED → AUDITED.
  - Store every signal snapshot (new: `signals`).
- **Paper trading engine**
  - One-click Take/Skip/Deploy to Paper.
  - Persist paper trades + mark outcomes.
- **OPTIONS BACKTEST (port semantics from reference Node repo)**
  - Use real expired-option candles via Upstox V3 expired-instruments API.
  - Paired INDEX + OPTION legs.
  - Two risk modes:
    - `spot_points` (INDEX_ONLY exit)
    - `option_premium_pct` (OPTION_ONLY exit)
  - Store options candles in Mongo (new: `options_1m`).
- **Frontend**
  - Replace Live Signals placeholder with real-time UI (SSE or WS from backend).
  - Replace Paper Trading placeholder with journal + replay.
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
**Status:** ⏳ PENDING

### Goal
Save all current project files to the user’s GitHub account.

### Implementation steps
- Use `support_agent` (preferred) to:
  1. Identify target GitHub repo name and ownership.
  2. Initialize git in `/app`.
  3. Commit with message `AlphaForge v0.7.0 (Phases 1-3.5 + Phase 7 + docs)`.
  4. Push to user repo.

### Success criteria
- Repo contains:
  - backend + frontend + docs + docker-compose + scripts
  - tags or releases (optional)
  - README visible and correct

---

## Next Actions (Immediate)
1. ✅ (Done) Complete handover-grade documentation + local deployment package.
2. ⏳ Push the project to the user’s GitHub.
3. ⏳ Start Phase 4 (Upstox OAuth + WS + Options backtest) in a dedicated session to manage token budget and integration complexity.
