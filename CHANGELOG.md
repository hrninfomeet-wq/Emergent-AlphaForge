# Changelog

All notable changes to AlphaForge Trading Lab.

## [0.13.x] — Honest Walk-Forward Optimization (2026-06-10)

400 backend tests pass. The single optimizer's result is in-sample by definition; this release adds the honest mode.

- **Walk-forward optimization** (`backend/app/wfo.py`, `POST /api/optimize/wfo`): chronological train/test windows in TRADING days present in the data (rolling or anchored, holiday-aware by construction); per-window Optuna TPE re-optimization on the train slice only; each window's best evaluated on its UNSEEN test slice; all OOS trades stitched into one out-of-sample equity curve — the number to believe.
- Analyses: **walk-forward efficiency** (OOS pnl/day ÷ IS pnl/day; ≥0.7 strong, <0.4 likely overfit), **OOS consistency** (share of OOS-positive windows), **param stability** (rel_spread of each chosen param across windows — wandering params are fitted to noise).
- Final deployable params come from the most recent train window and are saved as `best_params` plus a full `best_backtest_run_id`, so Save-as-Preset / View-Best-in-Lab / deployment flows work unchanged.
- Leak-safety: indicators are computed once on the full frame and sliced per window — verified causal (trailing windows only) in `app/indicators.py`, which also gives test windows realistic warmup history like live evaluation.
- Jobs persist in `optimization_jobs` with `kind="wfo"`: cancel at trial boundaries, pause/resume at window granularity, startup orphan-marking covered.
- UI: "Run type" selector (Single | Walk-forward) in the Optimizer page, window config block, WFO results panel (stitched-OOS headline + equity sparkline, color-coded WF efficiency, per-window table, param-stability bars), WFO tag in Job History.
- 22 unit tests (`tests/test_wfo.py`). Live smoke on real NIFTY data correctly exposed an overfit quick-run: WF efficiency −1.06, 0/3 windows OOS-positive.
- WFO v1 evaluates on spot; for option realism run the final preset through option re-rank or an option backtest afterwards.

## [0.12.x] — Optimizer Overhaul + Options-Buying Upgrades (2026-06-09)

378 backend tests pass. Local stack healthy. Backend changes require a container rebuild.

### Auto-Optimizer
- **Two-stage option re-rank** (`evaluation_mode: "spot" | "option_rerank"`, `rerank_top_k`, `option_config`): Stage 1 fast spot search; Stage 2 re-ranks the top-K candidates by REAL paired-option net rupee P&L. Option contracts + candles loaded once and simulated in-memory (`_option_rerank`); `simulate_paired_option_trades` now pre-groups candles by `instrument_key`. The legacy spot-only path is untouched for A/B. Live A/B showed spot-profitable params can be net-rupee LOSERS on options.
- **Pause / Resume / crash-resume**: `POST /api/optimize/jobs/{id}/pause` + `/resume`. Compact trial log + best-so-far flushed to the job doc; resume rehydrates and re-seeds the Optuna study (`_flush_trial_log`, `_rebuild_study`, `resume_optimization`). Startup reconcile now marks orphaned jobs `interrupted` (resumable), not failed. New statuses: `paused`, `interrupted`.
- **Optional guard rails** (single UI toggle, default ON): `min_trades` significance floor (default 10) + optional CE/PE `min_direction_share`. OFF = pure objective maximization (one-sided allowed).
- **Indicator-period search** (`optimize_indicator_periods`): RSI/MACD/ATR/EMA/ADX/CHOP/swing become tunable; enriched frames cached per indicator-period combo (fixes indicators being frozen at defaults).
- **net_pnl_inr** objective (net points × latest contract lot size).
- Trial budget raised to 5000 in the UI; heavy work moved to `asyncio.to_thread` so the API stays responsive; cancel skips heavy analysis for a fast Stop.
- UI: pre-trade profile selector (previously a dead backend↔frontend link), clone-config-to-setup from Job History, preset **delete** button, save-as-preset for paused/interrupted/failed, "no usable result" hint, setup config persisted to `localStorage`, removed the dead Mode selector.
- Spec authored at `.kiro/specs/optimizer-enhancements/` (requirements → design → tasks).

### Backtest engine / data
- Backtest hot loop converted from per-row `df.iloc[i]` to pre-materialized dict records → ~8.8x faster row access (behavior identical; all strategies verified dict-safe).
- `indicators.detect_fvg` vectorized (was a GIL-holding O(n) Python loop that could stall the event loop on full-history runs).
- **Pre-run option preflight** (`POST /api/backtest/option-preflight?ingest_missing=`): would-pair coverage report + optional background ingest of missing option data; "Option Data Preflight" panel in Backtest Lab.
- Option pairing correctness: windowed contract query (`length=None` + expiry window) fixed near-zero pairing; expiry-mode selector; hardened against silent oldest-contract fallback. BANKNIFTY option-data gaps filled; coverage spans 2024-11-27 → present for all three indices.

### Trading logic (shared decision engine — backtest = paper = live)
- `dte.py` DTE filter; `option_costs.py` rupee cost model (brokerage + statutory + %-of-premium spread); `portfolio.py` premium-at-risk sizing + rupee equity; `market_context.py`/`vix.py`/`context_signals.py` regime/time/DTE/VIX tagging + S/R/round-level/divergence signals; `exit_engine.py` shared `intrabar_exit` for spot and option engines; `strategies/builtin/explosive_reversal.py` score-based detector. India VIX ingested as `INDIAVIX` from 2025-12-29.

## [0.11.x] — Per-Deployment Kill Switches + Forward Metrics + Live Option Universe (2026-06-01)

- Per-deployment kill switches (`max_consecutive_losses` → PAUSE, `daily_loss_cutoff_pct` → PAUSE, `max_open_paper_trades` → BLOCK) in `backend/app/deployment_kill_switch.py`, wired into the evaluator (paper deployments only).
- Forward metrics aggregation (`backend/app/forward_metrics.py`): session-gated (≥70% of 10:00-15:00 IST) deployment metrics; Strategy Library shows them after ≥10 complete sessions. Routes `GET /api/deployments/metrics`, `/deployments/{id}/metrics`.
- Live ATM option universe preview/restart for the read-only Upstox stream (`live_option_universe.py`, `GET /api/upstox/stream/options/universe`, `POST /api/upstox/stream/options/restart`).
- Warehouse chart trust UI (explicit OHLC overlay, IST axis, session markers, local chart theme).

## [0.10.x] — Data Warehouse Hardening (2026-05-31)

A focused pass to make the warehouse fast, trustworthy, self-maintaining, and inspectable. 272 backend tests pass.

### Performance
- New `backend/app/option_coverage_cache.py` + `option_coverage_cache` collection. `/api/options/coverage` served from a precomputed per-underlying summary (~8s → ~200ms) with a single-flight lock to prevent a startup stampede. Cache warmed on boot, refreshed after option-fetch jobs and after clearing option data.
- Data Warehouse page renders on the fast calls and loads the option heatmap independently.
- `compute_hygiene_plan` optimized from a 120s+ timeout to ~6s by replacing the `options_1m`→`option_contracts` `$lookup` join with a group on the embedded `underlying`/`expiry_date` fields, and aggregating spot coverage server-side.

### Correctness
- `warehouse.audit_integrity` is now holiday-aware (uses `nse_calendar.trading_days_in_range`); previously NSE holidays were counted as missing days. `summary.calendar_assumption == "nse_trading_calendar"`.

### Features
- **Data Hygiene UI** (`DataHygienePanel`): Check warehouse (plan) + Fill gaps (dependency-ordered execute), routed through the global job tracker. Data Warehouse page regrouped into Connection / Data Hygiene / Index Data / Option Data / Verify & Audit / Diagnostics sections.
- **Automatic warehouse catch-up** (`backend/app/warehouse_autoupdate.py`): runs on startup, on Upstox OAuth-connect, and daily at 18:00 IST; gated on Upstox connected; status + toggle UI; routes `GET/POST /api/warehouse/auto-update/{status,toggle,run}`.
- **Point-in-time lookup** (`backend/app/warehouse_lookup.py`, `GET /api/warehouse/lookup`): spot + derived ATM + nearest expiry + ATM CE/PE candles for a date/time, warehouse-only.
- **Candlestick chart** (`backend/app/warehouse_ohlc.py`, `GET /api/warehouse/ohlc/{instrument}`): server-side resample to 1m/5m/15m/1h/1d + intraday gap detection. `WarehouseChart` with OHLC crosshair legend, date/time locator (validate + snap + mark), gap banner.
- **NSE holiday-calendar modal** (`HolidayCalendarDialog`, `GET /api/calendar/holidays`).
- **Global background-job tracker** (`frontend/src/lib/jobs.jsx` `JobsProvider`): ingest/fetch/hygiene progress survives navigation (run IDs persisted to `localStorage`); active-jobs indicator in the top bar.
- **OAuth token-expiry countdown** in the global top bar (color-escalating) and the Upstox panel.

### UI cleanup
- Removed the "Made with Emergent" badge, `emergent-main.js` loader, and PostHog session-recording telemetry from `index.html`.
- Removed the obsolete yfinance ingest panel (kept read-only coverage cards).
- Backtest Run Journal moved into the Backtest Lab; Signal Journal repurposed as the deployment signal audit trail.
- Removed the redundant Raw Option Universe Audit panel (clear-options action relocated to Data Trust Audit; `/api/options/audit` route kept for programmatic use).

## [0.9.x] — Phase 4b Slices (Forward Testing Stack)

### Slice 9 — Deployment quality warnings + acknowledgment checkbox
- New module `backend/app/deployment_quality.py` with 5 checks: missing walk-forward, walk-forward IS/OOS divergence (OOS < IS × 0.7 OR explicit divergence flag), low trade count (< 30), weak Sharpe (< 0.5), large drawdown ratio (|max_dd|/total_pnl > 0.15)
- New route `GET /api/deployments/quality?source_type=...&source_id=...`
- `DeploymentCreateReq.acknowledged_warnings` required when warnings present (HTTP 400 `acknowledgment_required` otherwise)
- Quality snapshot stored on deployment as `quality_at_creation` plus `acknowledged_warnings` flag for audit
- Frontend: `QualityBadge` with severity-colored warning list and inline ack checkbox; Create button disabled until ack ticked when needed
- 15 new tests (223 total)

### Slice 8 — Strategy source SHA pinning + drift detection
- New module `backend/app/strategy_source_hash.py` — SHA-256 of plugin .py file, truncated to 16 hex
- Pin `strategy_source_sha` on deployment creation; evaluator compares pinned vs current on every tick
- On mismatch, auto-pause with `drift_reason="strategy_source_drift"` and full audit (pinned/current/timestamp)
- Pre-slice-8 deployments without a pinned SHA continue to operate (legacy compat)
- 14 new tests

### Slice 11 — Idempotency hardening (out-of-order)
- Unique partial index `signals_deployment_bar_unique` over `(deployment_id, candle_ts)` with `partialFilterExpression {deployment_id: {$exists: true, $type: "string"}}` so manual research signals are unaffected
- Evaluator catches Mongo duplicate-key (E11000) errors as `outcome="skipped"`, `reason="already_journaled"` and advances `last_evaluated_ts`
- Index added to `ensure_indexes()` and created live on running DB

### Slice 7 — Slippage model + post-hoc volatility detector
- New module `backend/app/slippage.py` with `SlippageConfig` (ATM 0.5pt, OTM1/ITM1 1pt, OTM2+/ITM2+ 2pt, expiry-day 30-min 2x)
- Wired into `simulate_paired_option_trades`; per-trade audit fields `raw_*`, `entry_slippage_pts`, `slippage_bucket`, `expiry_tail_applied`
- Override per backtest via `OptionBacktestReq.slippage_config`
- New module `backend/app/volatility.py` with `VolatilityConfig` (spike_threshold=2.5, realized_window=5, baseline=11250 bars). `annotate_volatility()` adds 4 columns
- New route `POST /api/volatility/audit`
- 30 new tests

### Slice 6.5 — Live tick → 1m OHLC roller
- New module `backend/app/live_candle_roller.py` subscribes to `UpstoxMarketStreamManager` broadcast and aggregates per-(instrument, minute) OHLC buckets
- Flushes on minute rollover via `persist_index_candles_bulk` to `candles_1m`
- Stale-bucket flush on 5s timeouts
- Subscribe-before-task-start to avoid producer/consumer race
- New routes `GET /api/live-candles/status`, `POST /api/live-candles/start`, `POST /api/live-candles/stop`
- Auto-starts on backend boot after WS auto-start; auto-flushes on shutdown
- 8 new tests
- Closes a real gap discovered 2026-05-29: Upstox historical endpoint returns empty for the same trading day

### Slice 6 — Data Hygiene workflow + NSE holiday calendar
- New module `backend/app/data_hygiene.py` computes diff vs desired warehouse (default 2024-11-27 → today, NIFTY+BANKNIFTY+SENSEX, ATM CE+PE, sample=1m)
- Returns prioritized actions per instrument (spot, contracts, option_candles) with ETA hints
- New module `backend/app/nse_calendar.py` with hand-curated NSE holidays for 2024–2026 plus `SPECIAL_SATURDAY_SESSIONS` (2025-02-01, 2026-02-01) and `SHIFTED_EXPIRY_DAYS` (e.g., 2026-01-14 SENSEX shift)
- New routes `POST /api/data-hygiene/{plan,execute}`, `GET /api/data-hygiene/status`
- Wired into hygiene plan so spot coverage no longer false-flags holidays
- 17 new tests

### Slice 5 — Pre-flight data realism panel + active-expiry contract picker fix
- New module `backend/app/deployment_preflight.py` checks spot coverage last 30 days, upcoming option expiries, active vs expired contracts, Upstox token state
- Per-instrument structural break notes (NIFTY weekly day rotation, BANKNIFTY weekly discontinued Nov 2024, SENSEX BSE Friday)
- New route `GET /api/deployments/preflight?instrument=...`
- Tightened `_resolve_option_contract` to filter `expiry_date >= today` with new blocker `option_contract_no_active_expiry`
- Frontend: `PreflightBadge` collapsible above Create button
- 8 new tests

### Slice 4 — Paper trade auto-creation on signal approval
- Approve route auto-creates a paper trade when signal carries `deployment_id` AND `deployment.mode == "paper"`
- Trade uses `lot_size` from option contract (Upstox-supplied), `lots` from `deployment.risk.default_lots` (default 1)
- Stamps `deployment_id` and `source="paper_auto_on_approval"`
- Failure to create trade does NOT roll back approval — records `paper_trade_error`
- Frontend: mode badge on pending signal card, "Approve + Paper" button label when applicable
- New form fields: DTE filter input, default lots, allow-overnight checkbox

### Slice 3 — Auto-square-off at 15:00 IST + expiry-day cutoff + dte_filter
- New module `backend/app/paper_squareoff.py` background loop
- Closes all OPEN paper trades once per market day at 15:00 IST. Skips trades whose deployment has `risk.allow_overnight=true`
- Exit price priority: WS tick → last_price → entry_price (zero-PnL fallback)
- Idempotent
- Expiry-day cutoff: blocks new signals on the deployment instrument's expiry day at 15:00 IST (looked up from `option_contracts.expiry_date`, never weekday-hardcoded)
- New deployment fields: `option_policy.dte_filter` (default `[0,1,2,3,4,5,6]`), `risk.allow_overnight` (default false)
- Audit trail extended with `bar_ts`, `decision_ts`, `next_expiry_iso`
- 14 new tests

### Slice 2 — Approval UI (Approve / Skip / Mark Blocked)
- New routes `POST /api/signals/{id}/approve` (CONFIRMED → TRIGGERED → ACTIVE with audit), `/skip` (CONFIRMED → SKIPPED → AUDITED), `/mark-blocked` (any non-AUDITED → AUDITED + blockers)
- Frontend: `PendingApprovalPanel` above existing console showing only CONFIRMED deployment-generated signals with three buttons + optional note input
- Auto-refresh signals list every 15s
- Evaluate-now button on each ACTIVE deployment card
- 6 new tests

### Slice 1 — 1m_close deployment evaluator
- New module `backend/app/deployment_evaluator.py`
- Pulls last N candles, runs strategy.evaluate() on closed bar, applies pretrade filter, picks ATM/OTM1/ITM1 contract step-aware from option_contracts
- Journals clean (CONFIRMED) or blocked (AUDITED with blockers) signals
- Time-of-day windows: blocks 09:15–09:25 and 14:50–15:30 IST
- `option_no_data` flag when contract has no candle in last 5 min
- Idempotency via `last_evaluated_ts`
- Concurrency rule: keep highest-score per `(instrument, candle_ts)`
- Background scheduler in `server.py` wakes 10s after each minute boundary during NSE market hours
- New routes `POST /api/deployments/{id}/evaluate-on-close`, `POST /api/deployments/evaluate-active`
- 13 new tests

## [0.8.0] — Phase 4 Foundation
- Upstox OAuth + encrypted token storage
- Upstox V3 read-only WebSocket market-data stream with sanitized tick persistence
- Upstox 1m index historical ingest with automatic chunk guidance and background jobs
- Option contract sync, expired contract backfill, option candle fetch with OI preservation
- Option Data Planner with preview-first workflow, ATM-only default, indexed lookup
- Option Coverage Heatmap and Raw Option Universe Audit
- Persistent market header (NIFTY 50, SENSEX, BANKNIFTY, GOLD FUT, BTCUSD, USDINR, GIFT NIFTY, MIDCPNIFTY) with WS-first fallback
- Theme: System / Black / White via CSS variables
- Offline signal lifecycle (`WATCHING → FORMING → CONFIRMED → TRIGGERED → ACTIVE → EXITED → AUDITED`)
- Manual paper trading journal with stop/target auto-close
- Strategy Deployment management foundation (CRUD only — evaluator added in Slice 1)

## [0.7.1] — Local Bootstrap Repair + Status Reconciliation
- Fixed backend syntax issue blocking local startup
- Added missing backend/frontend env examples and frontend `yarn.lock`
- Removed unavailable Emergent-only backend dependency and added runtime imports required locally
- Verified Docker Desktop + Compose stack on Windows (`mongo`, `backend`, `frontend` healthy)
- Removed obsolete Compose `version` key
- Updated dashboard/sidebar phase status to reflect Phase 4a scaffold and verified local deployment
- Rewrote handoff/setup notes for local PC development instead of hosted Emergent-only operation

## [0.7.0] — Phase 7: Local Deployment Package
- Added `docker-compose.yml` with mongo/backend/frontend services + persistent volume
- Added `backend/Dockerfile`, `frontend/Dockerfile` (multi-stage build → nginx)
- Added `frontend/nginx.conf` with gzip + SPA fallback + static caching
- Added `start.sh` (Mac/Linux) + `start.bat` (Windows) launchers
- Added `.env.example` templates for backend + frontend
- Added `docs/LOCAL_SETUP.md` step-by-step guide (Docker + Native)
- Added comprehensive documentation: `README.md`, `docs/ARCHITECTURE.md`, `docs/HANDOFF.md`, `docs/STRATEGY_PLUGINS.md`, `docs/API_REFERENCE.md`

## [0.6.5] — Phase 3.5: User-feedback Fixes
- Fixed: progress bar not filling during optimizer run (added `bg-info` Tailwind utility)
- Added: optimizer auto-saves best params as a full `backtest_run` (with trades + equity + walk-forward) linked via `optimization_job_id`
- Added: "View Best in Lab" button → navigates to `/backtest?run=<id>`
- Added: 3 export buttons on Optimizer (Config JSON, Result JSON, Alts CSV)
- Added: "Saved Presets" panel in Optimizer left sidebar
- Added: "Load preset (optimized params)" dropdown in Backtest Lab
- Added: URL deep-link `/backtest?preset=<name>` auto-applies preset
- Added: Stop button on running optimization (`POST /api/optimize/jobs/{id}/cancel`)
- Added: graceful cancellation — worker checks `cancelled` flag every 5 trials
- Added: CANCELLED status badge

## [0.6.0] — Phase 3: Auto-Optimizer
- New `/api/optimize/*` routes (start, list, get, delete, apply-as-preset)
- Optuna TPE (Bayesian), Grid Search (sampled), CMA-ES (Genetic) samplers
- 6 objectives: risk_adjusted (default), sharpe, profit_factor, total_pnl_pts, win_rate, neg_max_dd
- Walk-forward integrated; pre-compute indicators ONCE per job for 100× speedup
- Robustness scoring (±10/20% perturbation, % staying within 85% of best)
- Parameter importance + 2D heatmap of top-2 important params
- Top-N alternatives ranking
- Optimizer page with progress polling, best-so-far card, status badges, full result cards, job history

## [0.5.0] — Phase 2.5: BacktestLab Polish
- NumberSliderInput: combined slider + typeable number box
- Date window picker on backtest config
- Save with name + reload via "Load past run" dropdown
- Export Config JSON / Full Result JSON / Trades CSV
- Signal Journal: filter, bulk-select, bulk-delete, click-row-to-load via `/backtest?run=<id>` deep-link

## [0.4.0] — Phase 2: V1 Full Lab
- 6 built-in strategies: Confluence Scalper, VWAP Pullback Scalp, ORB, SMC Liquidity Sweep+FVG, Fibonacci Pullback, VWAP Mean Reversion
- Custom strategy plugin auto-discovery (drop `.py` file → restart)
- Data Warehouse v2 with per-day SHA-256 integrity hashes + coverage heatmap UI
- Multi-pane TradingView Lightweight Charts v5 (price + equity + drawdown synced)
- Pre-Trade Checklist: 3 profiles, 10+ configurable filters, anti-over-filter safeguard
- Statistical significance badge (Wilson 95% CI)
- Regime detector (ADX + Choppiness + ATR expansion)
- Signal funnel telemetry per backtest

## [0.1.0] — Phase 1: POC
- Single-file E2E proof
- yfinance ingestion → MongoDB persistence
- Vectorized indicators (EMA, RSI, MACD, ATR, VWAP, ADX, Choppiness)
- Confluence Scalper port
- Vectorized backtest with realistic Indian intraday cost model
- Walk-forward IS vs OOS validation
- Equity curve + drawdown series
- Statistical significance evaluation
