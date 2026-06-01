# Handoff

Updated: 2026-06-01

This is the entry point for the next AI agent or developer. Read it before editing code. The repository and tests are the source of truth — not any prior chat.

## Status In One Line

The Data Warehouse has been hardened end-to-end (fast, auto-updating, hygiene-managed, calendar-aware, verifiable, and chartable). **280 pytest tests pass.** The local Docker stack is healthy. The next planned product work is **Phase 4b Slice 10 — Forward metrics aggregation per deployment** (paused earlier to perfect the warehouse).

## Read Order For A New Agent

1. This file (`docs/HANDOFF.md`)
2. `plan.md`
3. `docs/PROJECT_OVERVIEW.md`
4. `docs/ARCHITECTURE.md`
5. `docs/API_REFERENCE.md`
6. The relevant code + tests for the next task.

## Recent Work — Warehouse Chart Trust UI (2026-06-01)

Focused pass on the Data Warehouse candlestick panel:

- The top-left chart overlay now shows explicit Open / High / Low / Close values with chart-theme-safe colors. It no longer relies on the app page theme, which made O/H/L hard to read on a dark chart.
- Added small icon-only chart theme controls: System, Dark, Light. This is local to the chart and does not change the whole app theme.
- Every timeframe now requests the full stored warehouse range. The earlier short-range behavior was a frontend `LOOKBACK_DAYS` optimization (`1m=3d`, `5m=7d`, `15m=21d`, `1h=90d`), not missing warehouse data.
- Chart time labels are formatted in IST through the Lightweight Charts tick formatter, with a footer reminder that the regular session is 09:15-15:30 IST.
- Added session-open markers so intraday multi-session views show where a new Indian market session begins.
- 1h resampling is anchored to 09:15 IST, not 09:00. Gap detection skips the current in-progress trading session until after 15:30 IST.
- Fixed a stale async request race: the slow default full-history `1d` load could finish after a quick `1m`/`5m` switch and overwrite the chart while the toolbar showed the newer timeframe. `loadSeqRef` now ignores older responses.

## Recent Work — Data Warehouse Hardening (2026-05-31)

A full pass to make the warehouse trustworthy and fast. All slices committed and pushed to `main`:

- **Perf:** option coverage served from `option_coverage_cache` (8s → ~200ms); the page renders on fast calls and loads the heatmap independently. (`190ba45`)
- **Quick wins:** removed the "Made with Emergent" badge + loader script + PostHog telemetry; removed the obsolete yfinance ingest panel; added the NSE holiday-calendar modal. (`23b07f9`)
- **Correctness:** Data Trust Audit is now holiday-aware (was counting NSE holidays as missing days). (`76fb99c`)
- **Persistent jobs:** `JobsProvider` above the router tracks ingest/fetch/hygiene jobs and persists run IDs to `localStorage`, so progress bars survive navigation; global active-jobs indicator in the top bar. (`6242b08`)
- **Data Hygiene UI:** the plan/execute/status workflow is surfaced as the hero panel; page regrouped into Connection / Data Hygiene / Index Data / Option Data / Verify & Audit / Diagnostics. The hygiene plan was optimized from a 120s+ timeout to ~6s by dropping a `$lookup` join. (`8f9c695`)
- **Auto-update:** warehouse catches up to yesterday's close on startup, on OAuth-connect, and daily at 18:00 IST; status + toggle in the UI. (`70e5b4a`)
- **Point lookup:** spot + ATM CE/PE for any date/time, read from the warehouse only, to cross-check against a broker terminal. (`d8bb4b5`)
- **Candlestick chart:** per-index chart (1m/5m/15m/1h/1d) with an OHLC crosshair legend, a date/time locator (validates + snaps to bucket + marks the bar), and a gap banner. The backend now filters chart candles/gaps to calendar-approved 09:15-15:30 IST regular sessions so weekend/holiday/off-session rows do not create false candles or gap warnings. (`7b16457`, `882092d`)
- **UI follow-ups:** Backtest Run Journal moved into Backtest Lab; Signal Journal repurposed as the deployment signal audit trail; OAuth token-expiry countdown in the top bar. (`2fcb9d0`)
- **Cleanup:** removed the redundant Raw Option Universe Audit panel (kept the clear-options action in Data Trust Audit; the `/options/audit` route stays for programmatic use). (`882092d`)

## Working Local Stack

| Service | Where | Notes |
|---|---|---|
| Frontend | `http://localhost:3000` | React + nginx, dark/light theme |
| Backend | `http://localhost:8001` | FastAPI, all routes under `/api` |
| MongoDB | container `alphaforge_mongo` | Persistent named volume `mongo_data` (NOT in the project folder / OneDrive) |
| Upstox | OAuth flow, REST historical, V3 WebSocket stream | OAuth expires daily; re-connect drives the auto-update |

The stack is launched with `docker compose up -d --build`. Use `start.bat` (Windows) or `start.sh` (Mac/Linux).

## What Is Working End-To-End

Data:

- Index 1-minute candles for NIFTY, BANKNIFTY, SENSEX in `candles_1m`, audited per day in `integrity_hashes`. Holiday-aware audit via `nse_calendar`.
- ATM CE/PE option candles in `options_1m` (NIFTY ~1.46M / BANKNIFTY ~1.69M / SENSEX ~2.21M; OI populated).
- Option contract metadata in `option_contracts` (strike, side, expiry_date, instrument_key, lot_size).
- NSE holiday calendar with budget-Saturday and shifted-expiry exceptions in `backend/app/nse_calendar.py`; surfaced as a holiday-calendar modal.
- Live tick → 1m OHLC roller closes the same-day historical gap (`backend/app/live_candle_roller.py`) and now drops non-trading-day/off-session ticks before they can create warehouse candles.
- Data Hygiene workflow (UI + backend) audits the warehouse against a default scope (2024-11-27 → today, ATM only) and submits dependency-ordered fetches; ~6s plan.
- Automatic warehouse catch-up (`backend/app/warehouse_autoupdate.py`) on startup, OAuth-connect, and daily 18:00 IST.
- Option coverage served from a precomputed cache (`option_coverage_cache`) for fast page loads.
- Point-in-time lookup (spot + ATM CE/PE at a date/time) and a per-index candlestick chart with gap detection, explicit OHLC, IST axis labels, session-open markers, and local chart theme controls, all warehouse-only.

Research:

- 6 built-in strategies plus a custom plugin loader (`backend/app/strategies/builtin/*.py`, `plugins/*.py`).
- Backtest with realistic costs, walk-forward IS/OOS, statistical significance, regime detection.
- Optimizer (Bayesian / Grid / CMA-ES) with robustness, importance, heatmap, top-N alternatives.
- Slippage model wired into paired option backtests (`backend/app/slippage.py`).
- Post-hoc volatility detector (`backend/app/volatility.py`) replaces the rejected event calendar.

Forward testing:

- Strategy Deployments persisted in `strategy_deployments`, created only from saved presets or backtest runs.
- 1-minute close evaluator (`backend/app/deployment_evaluator.py`) running on a background scheduler during NSE market hours.
- Deployment-generated CONFIRMED signals show in the Pending Approval panel; user clicks Approve / Skip / Mark Blocked.
- Approve auto-creates a paper trade when `deployment.mode == "paper"`, with lot size pulled from the option contract.
- Auto square-off at 15:00 IST every market day (override per deployment with `risk.allow_overnight=true`).
- Expiry-day cutoff at 15:00 IST blocks new signals on the deployment instrument's expiry day.
- Strategy source SHA is pinned on every new deployment; the evaluator auto-pauses on drift.
- Pre-flight data realism panel and deployment quality warnings with required acknowledgment surface known issues at deployment creation.
- Idempotency hardened with the partial unique index `signals(deployment_id, candle_ts)`.

Live data:

- Upstox V3 read-only market-data WebSocket stream auto-starts on backend boot.
- Market header prefers fresh ticks and falls back to REST quotes when ticks are stale or absent.

## What Is Not Done

Warehouse: complete for v1 (this session). Optional warehouse extras not yet built: option price sanity check (intrinsic floor / impossible-jump flagging), a `mongodump` backup button. OI is populated but a dedicated staleness check is not built.

Product roadmap (from `plan.md`):

- **Slice 10 (next): Forward metrics aggregation per deployment** — win-rate, avg P&L, profit factor, annotated with session completeness ≥70% of 10:00–15:00 IST. Surface in Strategy Library only after ≥10 complete sessions.
- Slice 12: Per-deployment kill switches (`max_consecutive_losses`, `daily_loss_cutoff_pct`, `max_open_paper_trades`).
- Phase 5 profitability boosters (Kaplan–Meier survival, meta-model, Kelly sizing, Telegram alerts) are deferred until ≥6 months of forward signal history exists.
- Phase 6 swing/positional extension is not started.
- No automatic broker order placement. The manual approval gate is intentional and must remain.

## Project Conventions (Important)

These were locked by the user during development. Do not change them without asking.

- DTE filter default: `[0, 1, 2, 3, 4, 5, 6]` on every deployment (full week + 2 days).
- Auto square-off at 15:00 IST every market day. `risk.allow_overnight=true` opts out per deployment.
- Slippage defaults: ATM 0.5pt, OTM1/ITM1 1pt, OTM2+/ITM2+ 2pt, expiry-day 30-min 2x multiplier.
- Time-of-day blocks on signal generation: 09:15–09:25 IST (first 10 min) and 14:50–15:30 IST (last 30 min).
- Expiry-day cutoff: 15:00 IST on the deployment instrument's expiry day, looked up from `option_contracts.expiry_date`. Never weekday-hardcoded.
- Data hygiene scope: 2024-11-27 → today, NIFTY+BANKNIFTY+SENSEX, ATM CE+PE only, sample=1m. Do not extend back to Jan 2024.
- Lot size: always read from `option_contracts.lot_size` (Upstox-supplied), never hardcoded.
- No event calendar. Reliable scheduled-event timestamps are unavailable. The post-hoc volatility detector replaces this.
- Session completeness: a forward session counts as "complete" only if data covered ≥70% of 10:00–15:00 IST.
- Walk-forward acceptance: the app warns but does not block. The user makes a conscious choice via the acknowledgment checkbox.
- All routes under `/api`. CORS open in dev.
- Never commit `.env`, access tokens, broker credentials, or `memory/test_credentials.md`.

## Operational Lessons (Discovered During Development)

These are the gotchas that bit us. Read this section before doing related work.

### Upstox

- Upstox returns `400 Invalid date range` on 30-day chunks crossing a Feb→Mar boundary. Use `chunk_days=7` for spot ingest. The chunker already uses 7.
- Upstox historical endpoint returns **empty for the same trading day**. Without the live tick → 1m roller the evaluator is stuck on yesterday's last bar. The roller closes this gap.
- `GLOBAL_INDICATOR|USDINR` is rejected by the REST quote endpoint (HTTP 400) but works on the WebSocket. Market header gracefully falls back per-tile.
- Expired option candles must be requested through `/v2/expired-instruments/historical-candle/{expired_key}/1minute/{to}/{from}`. Sending expired keys to the normal V3 endpoint returns `UDAPI1021`.
- The WS stream's subscribed instrument set is captured at connect time. Changing the subscription list in code does not auto-update an already-running stream — you must stop and restart.

### Index expiry calendar

- NIFTY weekly expiry day rotated: Thu (until 2024-08) → Wed (2024-09 to 2025-03) → Tue (2025-04+).
- BANKNIFTY weekly options were discontinued in November 2024. Only monthly expiries are available since.
- SENSEX is a weekly Friday expiry on BSE. It can shift to Wednesday when Thursday is a holiday — example: 2026-01-15 BMC/Maharashtra civic elections shifted SENSEX expiry to 2026-01-14. The `SHIFTED_EXPIRY_DAYS` set in `backend/app/nse_calendar.py` records these.
- 2025-02-01 and 2026-02-01 are Budget Saturday trading sessions. Both are listed in `SPECIAL_SATURDAY_SESSIONS`.
- 2025-10-21 Diwali Muhurat trading captured 60 candles (limited evening session). The audit recognizes this.
- 4 days in the warehouse have off-by-1 candle counts (374 candles total) caused by single-minute Upstox glitches. Treated as complete.

### Contract picker

- Always filter `option_contracts.expiry_date >= today` when resolving an ATM/OTM/ITM contract for a live signal. The 2026-05-28 bug where a Nov-2024 expired contract was selected was caused by missing this filter. Blocker name: `option_contract_no_active_expiry`.

### Strategy source drift

- `strategy_source_sha` is pinned on every new deployment. If the plugin .py file changes and you want the deployment to keep running, you must create a new deployment or explicitly re-pin (no UI for that yet).
- Pre-slice-8 deployments without a pinned SHA continue to operate. Drift detection is opt-in by deployment-creation timing.

### Idempotency

- The unique partial index `signals_deployment_bar_unique` over `(deployment_id, candle_ts)` is in `backend/app/db.ensure_indexes()`. The partial filter `{deployment_id: {$exists: true, $type: "string"}}` keeps manual research signals out of the constraint.
- The evaluator catches `E11000` duplicate-key errors and treats them as `outcome="skipped"`, `reason="already_journaled"`, then advances `last_evaluated_ts` to avoid retry loops.

### Quality gates

- The `acknowledged_warnings=true` flag is required at deployment creation when `deployment_quality.evaluate(...)` returns warnings. The 400 error code is `acknowledgment_required`. Pre-existing deployments are unaffected.
- Quality is evaluated against the source preset or backtest run. Five checks: missing walk-forward, walk-forward divergence (OOS < IS × 0.7 or explicit divergence flag), trade count < 30, Sharpe < 0.5, |max_dd|/total_pnl > 0.15.

### Performance (warehouse page) — learned 2026-05-31

- `options_1m` has 5M+ docs. Any aggregation over it on a page-load path is too slow. Option coverage is precomputed into `option_coverage_cache`; the data-hygiene plan groups on the embedded `underlying`/`expiry_date` fields (the `(underlying, expiry_date, strike, side, ts)` index supports it) instead of a `$lookup` join. If you add a new read-path aggregation, cache it or window it.
- `options_1m` candles already carry `underlying` and `expiry_date` (set at fetch time in `option_warehouse_jobs.persist_option_candles_bulk`), so you rarely need to join to `option_contracts`.
- The candlestick chart windows intraday timeframes (1m=3d, 5m=7d, 15m=21d, 1h=90d, 1d=full) so requests stay ~100ms. Full-history 5m is ~3s.

### Frontend background jobs — learned 2026-05-31

- Long-running job polling must live in `frontend/src/lib/jobs.jsx` (`JobsProvider`, mounted above the router in `App.js`), not in page-local state, or progress is lost on navigation. Active run IDs are persisted to `localStorage` (`alphaforge.activeJobs`, `alphaforge.activeHygiene`) and resumed on mount.
- The provider tracks single jobs (`upstox_ingest`, `option_fetch`) and the data-hygiene batch separately; pages subscribe to completion via `onJobComplete(kind, fn)`.

### Git on this repo

- `core.autocrlf=true`, so `git push`/`commit` print CRLF warnings — harmless. Splitting mixed-file commits by hunk requires `git apply --cached --recount --ignore-whitespace`.

## Architecture Snapshot

Backend modules of note:

- `backend/server.py` — FastAPI routes and orchestration.
- `backend/app/db.py` — Mongo client, `ensure_indexes()`, JSON-safe serialization.
- `backend/app/deployment_evaluator.py` — 1m_close forward evaluator + scheduler logic.
- `backend/app/deployment_preflight.py` — pre-flight data realism check.
- `backend/app/deployment_quality.py` — quality warnings (5 checks).
- `backend/app/data_hygiene.py` — warehouse fill plan + execute (index-friendly aggregations, ~6s).
- `backend/app/warehouse_autoupdate.py` — automatic catch-up (startup / OAuth / daily 18:00 IST).
- `backend/app/warehouse_lookup.py` — point-in-time spot + ATM CE/PE lookup.
- `backend/app/warehouse_ohlc.py` — OHLC resampling + intraday gap detection, filtered to calendar-approved regular sessions.
- `backend/app/option_coverage_cache.py` — precomputed option-coverage cache (fast page loads).
- `backend/app/nse_calendar.py` — holiday list, Budget Saturdays, shifted expiry days, labeled year calendar.
- `backend/app/live_candle_roller.py` — tick → 1m OHLC for same-day intraday; guards against non-trading-day/off-session warehouse writes.
- `backend/app/paper_squareoff.py` — 15:00 IST auto square-off loop.
- `backend/app/slippage.py` + `volatility.py` — execution realism.
- `backend/app/strategy_source_hash.py` — drift detection.
- `backend/app/option_data_planner.py` + `option_warehouse_jobs.py` — option fetch flow.
- `backend/app/upstox_client.py` + `upstox_stream.py` — broker REST + WebSocket.

Frontend of note:

- `frontend/src/lib/jobs.jsx` — global `JobsProvider` (background-job tracker, survives navigation).
- `frontend/src/components/Layout.jsx` — sidebar, top bar, active-jobs indicator, token-expiry countdown.
- `frontend/src/components/DataHygienePanel.jsx`, `WarehouseLookup.jsx`, `WarehouseChart.jsx`, `HolidayCalendarDialog.jsx`, `BacktestRunJournal.jsx`.
- `frontend/src/pages/DataWarehouse.jsx` — sectioned warehouse console (hygiene, index, options, verify, diagnostics).
- `frontend/src/pages/LiveSignals.jsx` — Pending Approval panel + Strategy Deployment form.
- `frontend/src/pages/SignalJournal.jsx` — deployment signal audit trail.
- `frontend/src/pages/BacktestLab.jsx`, `Optimizer.jsx`, `PaperTrading.jsx`.

Mongo collections in active use:

- `candles_1m`, `options_1m`, `option_contracts`, `integrity_hashes`, `warehouse_runs`, `option_coverage_cache`
- `backtest_runs`, `optimization_jobs`, `presets`, `pretrade_profiles`
- `strategy_deployments`, `signals` (with the unique partial index above), `paper_trades`
- `ticks`, `upstox_tokens`

See `docs/ARCHITECTURE.md` for the full module map.

## Verification Checklist

```bash
python -m pytest tests -q     # 280 pass as of 2026-06-01
cd frontend
npm run build
cd ..
docker compose up -d --build
docker compose ps
```

UI smoke checks:

- Theme selector switches System / Black / White cleanly.
- Data Warehouse: Data Hygiene "Check warehouse" returns per-instrument status; coverage heatmaps render fast; candlestick chart loads, O/H/L/C overlay is readable, axis is IST, chart theme icons switch, date/time locator marks a bar, and holiday-calendar modal opens.
- Top bar shows the OAuth token-expiry countdown.
- Live Signals page shows the deployment list and the Pending Approval panel.
- Creating a deployment with quality warnings is blocked until the ack checkbox is ticked.

Service health:

- `GET /api/health` returns `{db: "ok"}`.
- `GET /api/upstox/status` shows connected when OAuth is current.
- `GET /api/live-candles/status` shows the roller running during market hours.
- `GET /api/warehouse/auto-update/status` shows the last catch-up run.

## Recommendations For The Next Agent

- Read the relevant slice section in `plan.md` before starting. Each slice has done/not-done markers.
- Add tests next to the module you change. The `tests/` directory is the truth — `pytest -q` must pass before you commit.
- Keep changes small and verifiable. The user prefers small slices over big rewrites.
- Use the LTM workflow (`.kiro/steering/ltm-operations.md`) if asked to resume or recall.
- If a problem repeats, look at the operational-lessons section above before retrying.
- Push directly to `main` on `hrninfomeet-wq/Emergent-AlphaForge`. Use clean multi-line commit messages with bullet points.
- Use `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` for new local `FERNET_KEY` values.
- Never echo broker secrets or token values. Reference them by env-var name only.
