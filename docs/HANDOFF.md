# Handoff

Updated: 2026-05-29

This is the entry point for the next AI agent or developer. Read it before editing code. The repository and tests are the source of truth — not any prior chat.

## Status In One Line

Phase 4b is 10 of 12 slices done. The local Docker stack is healthy. 223 backend tests pass. Next planned work is **Slice 10 — Forward metrics aggregation per deployment**.

## Read Order For A New Agent

1. This file (`docs/HANDOFF.md`)
2. `plan.md`
3. `docs/PROJECT_OVERVIEW.md`
4. `docs/ARCHITECTURE.md`
5. The slice 10 entry in `plan.md` and the supporting modules listed in the architecture map.

## Working Local Stack

| Service | Where | Notes |
|---|---|---|
| Frontend | `http://localhost:3000` | React + nginx, dark/light theme |
| Backend | `http://localhost:8001` | FastAPI, all routes under `/api` |
| MongoDB | container `alphaforge_mongo` | Persistent volume |
| Upstox | OAuth flow, REST historical, V3 WebSocket stream | OAuth must be re-done after token expiry |

The stack is launched with `docker compose up -d --build`. Use `start.bat` (Windows) or `start.sh` (Mac/Linux) for the same flow.

## What Is Working End-To-End

Data:

- Index 1-minute candles for NIFTY, BANKNIFTY, SENSEX in `candles_1m`, audited per day in `integrity_hashes`.
- ATM CE/PE option candles in `options_1m` (NIFTY 1.44M / BANKNIFTY 1.69M / SENSEX 2.21M as of 2026-05-29).
- Option contract metadata in `option_contracts` (NIFTY 17,979 / BANKNIFTY 6,273 / SENSEX 30,794 contracts).
- NSE holiday calendar with budget-Saturday and shifted-expiry exceptions in `backend/app/nse_calendar.py`.
- Live tick → 1m OHLC roller closes the same-day historical gap (`backend/app/live_candle_roller.py`).
- Data Hygiene workflow audits the warehouse against a default scope (2024-11-27 → today, ATM only) and submits dependency-ordered fetches.

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

- Slice 10: Forward metrics aggregation per deployment (win-rate, avg P&L, profit factor, annotated with session completeness ≥70% of 10:00–15:00 IST). Surface in Strategy Library only after ≥10 complete sessions.
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

## Architecture Snapshot

Backend modules of note:

- `backend/server.py` — FastAPI routes and orchestration.
- `backend/app/db.py` — Mongo client, `ensure_indexes()`, JSON-safe serialization.
- `backend/app/deployment_evaluator.py` — 1m_close forward evaluator + scheduler logic.
- `backend/app/deployment_preflight.py` — pre-flight data realism check.
- `backend/app/deployment_quality.py` — quality warnings (5 checks).
- `backend/app/data_hygiene.py` — warehouse fill plan + execute.
- `backend/app/nse_calendar.py` — holiday list, Budget Saturdays, shifted expiry days.
- `backend/app/live_candle_roller.py` — tick → 1m OHLC for same-day intraday.
- `backend/app/paper_squareoff.py` — 15:00 IST auto square-off loop.
- `backend/app/slippage.py` + `volatility.py` — execution realism.
- `backend/app/strategy_source_hash.py` — drift detection.
- `backend/app/option_data_planner.py` + `option_warehouse_jobs.py` — option fetch flow.
- `backend/app/upstox_client.py` + `upstox_stream.py` — broker REST + WebSocket.

Frontend pages of note:

- `frontend/src/pages/LiveSignals.jsx` — Pending Approval panel + Strategy Deployment form with PreflightBadge and QualityBadge.
- `frontend/src/pages/DataWarehouse.jsx` — ingest, audit, planner.
- `frontend/src/pages/BacktestLab.jsx`, `Optimizer.jsx`, `PaperTrading.jsx`.

Mongo collections in active use:

- `candles_1m`, `options_1m`, `option_contracts`, `integrity_hashes`, `warehouse_runs`
- `backtest_runs`, `optimization_jobs`, `presets`, `pretrade_profiles`
- `strategy_deployments`, `signals` (with the unique partial index above), `paper_trades`
- `ticks`, `upstox_tokens`

See `docs/ARCHITECTURE.md` for the full module map.

## Verification Checklist

```bash
python -m pytest tests -q
cd frontend
npm run build
cd ..
docker compose up -d --build
docker compose ps
```

UI smoke checks:

- Theme selector switches System / Black / White cleanly.
- Data Warehouse coverage heatmap renders.
- Live Signals page shows the deployment list and the Pending Approval panel.
- Creating a deployment with quality warnings is blocked until the ack checkbox is ticked.

Service health:

- `GET /api/health` returns `{db: "ok"}`.
- `GET /api/upstox/status` shows connected when OAuth is current.
- `GET /api/live-candles/status` shows the roller running during market hours.

## Recommendations For The Next Agent

- Read the relevant slice section in `plan.md` before starting. Each slice has done/not-done markers.
- Add tests next to the module you change. The `tests/` directory is the truth — `pytest -q` must pass before you commit.
- Keep changes small and verifiable. The user prefers small slices over big rewrites.
- Use the LTM workflow (`.kiro/steering/ltm-operations.md`) if asked to resume or recall.
- If a problem repeats, look at the operational-lessons section above before retrying.
- Push directly to `main` on `hrninfomeet-wq/Emergent-AlphaForge`. Use clean multi-line commit messages with bullet points.
- Use `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` for new local `FERNET_KEY` values.
- Never echo broker secrets or token values. Reference them by env-var name only.
