# Changelog

All notable changes to AlphaForge Trading Lab.

## [0.18.x] — Forward Surfaces Overhaul, Slice 3: Signals Ledger (2026-06-12)

453 backend tests pass. Frontend builds clean (no new eslint warnings).

- **`/journal` rebuilt as the Signals Ledger** (`frontend/src/pages/SignalJournal.jsx`), the trade-recommendation record built on `GET /api/signals/enriched`. One row per deployment signal joined with its paper trade: IST time, deployment, strategy, instrument, CE/PE, contract (strike+side) + expiry, spot at entry, entry premium, expandable entry-trigger `reasons`, exit time/premium/reason, P&L in ₹ and premium points, score, state, blockers and `paper_trade_error`.
- **Server-side filter / sort / paginate / CSV**: deployment (preselected from `?deployment=` URL param, the command-center deep-link), instrument, state, clean/blocked, IST date range; clickable sort on the whitelisted columns (time, instrument, score, state); skip/limit pagination with total; CSV export honoring the current filters (`format=csv`); 45s auto-refresh.
- **Deletion toolkit** (via `POST /api/signals/purge`, all confirmed): row-checkbox "Delete selected", "Delete older than N days", and per-deployment purge. Paper trades are never touched by this route; OPEN trades are unaffected.
- Contract tests updated in the same commit: `test_signal_paper_lifecycle.py::test_frontend_exposes_live_and_paper_operational_views` now pins the ledger page's testids; `test_option_coverage.py::test_backtest_run_journal_moved_to_backtest_lab` updated for the rebuilt page (still asserts it is not the backtest-run table).

## [0.17.x] — Forward Surfaces Overhaul, Slices 1–2 (2026-06-12)

453 backend tests pass. Slices 3–5 (Signals ledger, Paper journal, polish) are spec'd for the next agent in `.kiro/specs/forward-surfaces-overhaul/`.

- **Independent deployments** (user decision): removed the highest-score-wins concurrency rule — every ACTIVE deployment journals and trades its own signals, enabling honest multi-strategy A/B. Exposure control = per-deployment `max_open_paper_trades`.
- **Approval flow fully retired**: deleted `POST /signals` (manual research), `/signals/{id}/transition`, `/approve`, `/skip`, `/mark-blocked`, `/signals/{id}/paper` and their request models + api.js methods. Modes are now `signal_only` | `paper` (legacy `shadow`/`recommendation` map to signal_only on create; old stored docs render as signal-only). `manual_approval_required` is stamped False on new deployments.
- **Signals ledger API** (`GET /api/signals/enriched`): signal⟷trade join — entry premium, exit premium/reason, P&L ₹ + premium points, trigger `reasons`, blockers, `paper_trade_error` — with server-side filters (deployment/strategy/instrument/state/clean/date-range), whitelisted sort, pagination, CSV export.
- **Paper trades API upgraded**: same filter/sort/pagination/CSV treatment + `deployment_name` on every row; `events` excluded from lists.
- **Deletion toolkit**: `POST /api/signals/purge` and `POST /api/paper/trades/purge` (ids / per-deployment / older-than-X; OPEN trades never deletable), and `POST /api/deployments/{id}/archive?purge=1` (undeploy + purge journals, keeping OPEN trades for the marker/square-off).
- **Deployments overview API** (`GET /api/deployments/overview`): per-deployment today (clean/blocked signals, open trades, open MTM, realized) + lifetime (closed trades, realized ₹, win rate) + account totals in one call.
- **Option stream auto-follow**: on deployment create/resume, the live option subscription re-derives its strike radius from ACTIVE paper deployments' moneyness policies (`radius_for_deployments`, +1 drift headroom, clamp 1–5) and restarts the read-only stream — best-effort, never blocks the deployment.
- **`/live` rebuilt as the Deployments command center**: per-strategy cards (mode chip, status + auto-pause reason, today's signals/open MTM/realized, lifetime ₹ and win rate, links to its signals/trades) with Pause/Resume/Evaluate and **Undeploy** (archive ± purge); header totals (today MTM, open trades, signals today) with 30s auto-refresh; a 3-step deploy wizard (preset + readiness/quality evidence → execution prefilled from the preset's policy → kill switches & ack). Pending Approval panel and the manual research-signal console are gone.
- Contract tests rewritten to pin the new surface and assert retired routes stay gone; +3 universe-radius tests. Verified against the live market session (overview/enriched/CSV endpoints + the new page and wizard in Chrome).

## [0.16.x] — Pipeline Alignment: Preset Execution Policy, Readiness, Option-Aware WFO (2026-06-12)

449 backend tests pass. Implements the accepted alignment recommendations (1, 2, 4, 5); recommendation 3 (retiring the legacy spot evaluation) deferred by user decision.

- **Presets carry their execution policy** (`app/preset_execution.py`): apply-as-preset now stores `config.execution` (moneyness, DTE filter, exit mode, premium levels, lots, costs) derived from the job's `option_config`, plus `source_job_kind`. Backtest Lab re-applies the policy on preset load (option pairing auto-enabled under the same terms); the deployment form prefills option policy + auto-paper premium fallbacks from it (pts/pct mapped, guarded so the 15s refresh never clobbers manual edits). Old presets keep working (no execution block → unchanged behavior).
- **Deployment readiness** (`GET /api/deployments/readiness?source_type=&source_id=`): informational evidence card in the deployment form next to the quality gate — latest completed honest-WFO for the strategy (efficiency, OOS-positive windows, params-match, option-OOS ₹ when present) and latest option-rupee evidence (exact-params re-rank job preferred, else option-paired backtest run). Missing evidence shows as the next step to run, making the canonical pipeline (WFO → option rupee → deploy) visible at the decision point.
- **Deploy deep-link**: each preset row in the Optimizer gets a Deploy button → `/live?preset=NAME` preselects it as the deployment source.
- **Option-aware walk-forward (WFO v2)** (`WfoStartReq.option_aware` + `option_config`; default ON in the UI form): after stitching, the OOS trades are paired ONCE with real option candles (same engine + windowed data loading as the re-rank) and the results panel shows an "Option OOS (₹)" block — net rupee, win rate, charges, pairing %, per-window rupee chips, and rupee consistency. Pairing failures or data gaps degrade to an honest error/low-coverage note; the spot stitch is never affected. Window re-optimization itself stays on spot (per-window option evaluation remains future work).
- **Optimizer DTE filter is a multi-select** (ALL / 0–6 chips, was a single-token dropdown capped at DTE3); legacy saved setups and job clones coerce automatically. The option sub-panel is shared by re-rank and WFO rupee check (top-K hidden for WFO).
- New tests: 4 WFO option-OOS pure helpers (window bucketing, summary shaping) + 5 preset-execution derivation; 449 total.

## [0.15.x] — Backtest Lab / Optimizer Alignment Fixes (2026-06-12)

440 backend tests pass. Course-alignment pass over the research surfaces (user review of 2026-06-12).

- **Multi-select DTE filter** (Backtest Lab): the DTE dropdown is now a chip multi-select (ALL / 0–6), e.g. tick 0+1+2 for the 0–2 DTE buying window. Backend `normalize_dte_filter` accepts a single token or a list everywhere it's used (backtest run, option preflight, optimizer re-rank); old saved runs with `"dte2"`-style tokens still clone correctly.
- **ATM default moneyness** (Backtest Lab UI + `OptionBacktestReq`): was OTM1, which contradicted the warehouse's auto-maintained ATM-only scope, the Optimizer default, and the deployment default.
- **Premium exits now replicable live**: deployments accept `auto_paper_target_pts`/`auto_paper_stop_pts` (₹ of premium) alongside the existing `_pct` fallbacks — points take precedence over percent, the same rule as the backtest's `option_levels` mode. The Live Signals form gets a ₹-points/percent unit toggle; `compute_auto_risk_levels` resolves hint-pct → dep-pts → dep-pct per leg (stop still floors at ₹0.05).
- **Optimizer re-rank premium exits in points**: the option sub-panel gains the same Points/Percent toggle as the Backtest Lab (the backend already accepted `option_target_pts`/`option_stop_pts`; the UI never sent them).
- **"Walk-forward" naming split**: the Backtest Lab toggle/panel is now "Walk-forward split check (same params, IS vs OOS)" with a tooltip pointing to the Optimizer's honest re-optimizing WFO — two different things no longer share one name.
- **Lots input clarity**: the Option Execution "Lots" input is disabled (with a note) while Capital & position sizing is on, since the sizing panel controls the lot count in that case — previously it was silently ignored.
- **Sizing estimate visibility**: premium-at-risk sizing without a premium stop (e.g. spot-mirror exit mode) shows an amber note that per-trade rupee risk uses the Assumed stop % (an estimate, not an exact bound).
- **Live-parity note** in the premium SL/target panel: backtest exit settings do not travel with presets; the deployment fallback fields are the live equivalent.
- **Unit audit (no fixes needed)**: verified pnl pts→₹ via quantity (lots × contract lot_size), slippage points + half-spread per side, sizing ₹ vs ₹ budget, `net_pnl_inr` = cost-adjusted points × lot size, WFO efficiency = pts/day ÷ pts/day, marker routes premium levels to the option tick and spot-mirror levels to the index tick, paper P&L = (price − entry) × quantity. No unit mismatches found.
- 8 new tests (3 multi-DTE, 5 premium-pts fallback).

## [0.14.x] — Auto Paper Trading on Signals + Low-Sample Forward Metrics (2026-06-11)

432 backend tests pass.

- **Auto paper trading** (`backend/app/paper_auto.py`): paper-mode deployments with `risk.auto_paper` (new `DeploymentCreateReq` fields, default ON for new deployments) open a paper trade for every clean CONFIRMED signal automatically — no manual approval — so signal outcomes are auditable. Hook runs in `evaluate_active_deployments` AFTER the concurrency rule.
- **Entry price = real option premium**: live WS tick → `options_1m` candle ≤5 min old → refuse and journal `paper_trade_error` on the signal. Fixes a pre-existing bug where approval-created trades opened at the SPOT index level while marks/square-off use option premium, corrupting P&L. The manual approve route now uses the same resolution and never duplicates a trade that auto_paper already created.
- **Strategy-defined exits**: the evaluator captures each signal's `risk_hints` (target_pct/stop_pct/spot pts/time stop); auto trades compute stop/target from those hints first, deployment-level `auto_paper_target_pct`/`auto_paper_stop_pct` second (LONG-premium semantics, stop floored at ₹0.05).
- **Per-minute live marker** (`mark_open_deployment_trades`): every minute during market hours, OPEN paper trades are marked to the latest option tick and auto-closed on stop/target (existing `mark_trade_to_market` machinery); the linked signal transitions to EXITED. Without this, stop/target levels only ever fired on manual marks. Tickless trades are left untouched (no stale-price closes).
- **Spot-mirror exits** (post-review fix): the builtin strategies define exits as SPOT INDEX POINTS, not premium-% — exactly what the backtest's `spot_exit` mode simulates. Auto trades now carry `spot_exit` levels (direction-aware: CE target above entry spot, PE below) and the marker closes the option at its current premium when the UNDERLYING hits the level (`spot_target_hit`/`spot_stop_hit`). Without this, trades from every current strategy would have had no intraday exit at all.
- **Race hardening** (post-review fixes): an atomic claim on the signal (`claim_signal_for_paper_trade`) guarantees the evaluator hook and the manual approve route can never both open a trade for one signal; the approve route resolves premium and claims BEFORE any state transition (premium unavailable → 409 and the signal stays CONFIRMED/re-approvable instead of dangling ACTIVE); the marker's writes are conditional on `status=OPEN` so a concurrent manual close is never clobbered; the legacy `risk.stop_price`/`target_price` fallback in the approve route was removed (spot-level units vs premium entry → instant bogus stop_hit).
- Kill switches govern auto trades unchanged (paper mode only; `max_open_paper_trades` blocks the signal so no trade opens).
- **Low-sample forward metrics**: Strategy Library now requests `include_ineligible=1` and shows deployments with <10 complete sessions under an amber "low sample" badge (with n/10 sessions + trade count) instead of hiding them — per user decision, since the PC rarely runs full market sessions.
- UI: auto-paper controls in the Live Signals deployment form (paper mode only: toggle + fallback target/stop % of premium).
- 32 new tests (28 unit in `tests/test_paper_auto.py` + 4 evaluator integration).

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
