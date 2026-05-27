# Changelog

All notable changes to AlphaForge Trading Lab.

## [0.7.1] — Local Bootstrap Repair + Status Reconciliation
- Fixed backend syntax issue blocking local startup.
- Added missing backend/frontend env examples and frontend `yarn.lock`.
- Removed unavailable Emergent-only backend dependency and added runtime imports required locally.
- Verified Docker Desktop + Compose stack on Windows (`mongo`, `backend`, `frontend` healthy).
- Removed obsolete Compose `version` key.
- Updated dashboard/sidebar phase status to reflect Phase 4a scaffold and verified local deployment.
- Rewrote handoff/setup notes for local PC development instead of hosted Emergent-only operation.

## [0.7.0] — Phase 7: Local Deployment Package
- Added `docker-compose.yml` with mongo/backend/frontend services + persistent volume
- Added `backend/Dockerfile`, `frontend/Dockerfile` (multi-stage build → nginx)
- Added `frontend/nginx.conf` with gzip + SPA fallback + static caching
- Added `start.sh` (Mac/Linux) + `start.bat` (Windows) launchers
- Added `.env.example` templates for backend + frontend
- Added `docs/LOCAL_SETUP.md` step-by-step guide (Docker + Native)
- Added comprehensive documentation: `README.md`, `docs/ARCHITECTURE.md`, `docs/HANDOFF.md`, `docs/DEVELOPMENT_JOURNEY.md`, `docs/STRATEGY_PLUGINS.md`, `docs/API_REFERENCE.md`

## [0.6.5] — Phase 3.5: User-feedback Fixes
- Fixed: progress bar not filling during optimizer run (added `bg-info` Tailwind utility)
- Added: optimizer auto-saves best params as a full `backtest_run` (with trades + equity + walk-forward) linked via `optimization_job_id`
- Added: "View Best in Lab" button → navigates to /backtest?run=<id> with full trade table
- Added: 3 export buttons on Optimizer (Config JSON, Result JSON, Alts CSV)
- Added: "Saved Presets" panel in Optimizer left sidebar (click-to-load to Backtest Lab)
- Added: "Load preset (optimized params)" dropdown in Backtest Lab
- Added: URL deep-link `/backtest?preset=<name>` auto-applies preset
- Added: Stop button on running optimization (`POST /api/optimize/jobs/{id}/cancel`)
- Added: graceful cancellation — worker checks `cancelled` flag every 5 trials; best_so_far preserved
- Added: CANCELLED status badge (amber); robustness/importance/heatmap shown for cancelled too
- Tested: 100% pass via testing_agent_v3

## [0.6.0] — Phase 3: Auto-Optimizer
- New `/api/optimize/*` routes (start, list, get, delete, apply-as-preset)
- Optuna TPE (Bayesian), Grid Search (sampled), CMA-ES (Genetic) samplers
- 6 objectives: risk_adjusted (default), sharpe, profit_factor, total_pnl_pts, win_rate, neg_max_dd
- Walk-forward integrated; pre-compute indicators ONCE per job for 100× speedup
- Robustness scoring (±10/20% perturbation, % staying within 85% of best)
- Parameter importance (Optuna-native with sklearn, variance-based fallback)
- 2D heatmap of top-2 important params (8×8 grid)
- Top-N alternatives ranking
- Optimizer page with progress polling, best-so-far card, status badges, full result cards, job history
- Tested: 100% frontend / 92.3% backend pass (1 expected-fail edge case)

## [0.5.0] — Phase 2.5: BacktestLab Polish
- NumberSliderInput: combined slider + typeable number box (precise tuning)
- Date window picker on backtest config (start_ts / end_ts)
- Save with name + reload via "Load past run" dropdown
- Export Config JSON / Full Result JSON / Trades CSV from results header
- Signal Journal: filter, bulk-select, bulk-delete, click-row-to-load via /backtest?run=<id> deep-link

## [0.4.0] — Phase 2: V1 Full Lab
- 6 built-in strategies: Confluence Scalper, VWAP Pullback Scalp, ORB, SMC Liquidity Sweep+FVG, Fibonacci Pullback, VWAP Mean Reversion
- Custom strategy plugin auto-discovery (drop .py file → restart → it appears)
- Data Warehouse v2 with per-day SHA-256 integrity hashes + coverage heatmap UI
- Multi-pane TradingView Lightweight Charts v5 (price + equity + drawdown synced)
- Pre-Trade Checklist: 3 profiles, 10+ configurable filters, anti-over-filter safeguard
- Statistical significance badge (Wilson 95% CI)
- Regime detector (ADX + Choppiness + ATR expansion)
- Signal funnel telemetry per backtest
- Tested: 97.5% pass via testing_agent_v3

## [0.1.0] — Phase 1: POC
- Single-file E2E proof (`backend/test_core.py`)
- yfinance ingestion → MongoDB persistence
- Vectorized indicators (EMA, RSI, MACD, ATR, VWAP, ADX, Choppiness)
- Confluence Scalper port from reference Node.js repo
- Vectorized backtest with realistic Indian intraday cost model
- Walk-forward IS vs OOS validation
- Equity curve + drawdown series
- Statistical significance evaluation
