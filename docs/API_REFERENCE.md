# API Reference

Updated: 2026-05-29

All routes are prefixed with `/api`. JSON request/response. CORS open in dev.

## Health and Dashboard

### `GET /api/`
Returns `{ app, status, version }`.

### `GET /api/health`
Returns `{ db: "ok" }` or 503.

### `GET /api/dashboard/summary`
Warehouse stats + strategy load count + backtest run count + latest backtest meta.

### `GET /api/market/header`
Persistent market header snapshot:

`{ source_mode, updated_at, items: [{ key, label, group, last_price, change, change_pct, timestamp, source, status }] }`

WS-first, REST-fallback per tile. Failed symbols return `status: "error"` for that tile only. Never returns tokens.

### `GET /api/market/header/stream`
Server-Sent Events feed of market header snapshots.

## Strategies

### `GET /api/strategies`
`{ items: [StrategyMeta] }`. Includes failed plugins with `is_loaded: false` + `error`.

### `GET /api/strategies/{strategy_id}`
Single strategy metadata.

## Data Warehouse

### `POST /api/warehouse/ingest`
Body: `{ instrument, days }`. yfinance fallback ingest.

### `GET /api/warehouse/coverage`
Per-instrument coverage with per-day breakdown for heatmap.

### `GET /api/warehouse/runs?limit=50`
Ingest run audit log (also surfaces hygiene and option fetch jobs).

### `GET /api/warehouse/audit/{instrument}?start_ts=...&end_ts=...`
Per-day index candle audit.

### `GET /api/warehouse/candles/{instrument}?limit=500`
Latest N candles for chart preview.

### `DELETE /api/warehouse/data/{instrument}?confirm=CLEAR`
Developer-only clear. `instrument=ALL` clears all three indices. Does not touch options.

## Upstox

### `GET /api/upstox/status`
Connection state. Tokens are not returned.

### `GET /api/upstox/auth/start` / `GET /api/upstox/auth/callback`
OAuth flow. Callback redirects to `FRONTEND_POST_AUTH_URL`.

### `POST /api/upstox/disconnect`
Removes the encrypted token doc.

### `GET /api/upstox/market-quote/{instrument}`
Sanitized live REST quote snapshot.

### WebSocket stream

#### `POST /api/upstox/stream/start`
Body: `{ instrument_keys?, mode?, persist_ticks? }`. Starts the read-only V3 market-data stream.

#### `POST /api/upstox/stream/stop`
Stops the local stream.

#### `GET /api/upstox/stream/status`
Sanitized status: running flag, session id, mode, subscribed count, latest tick count, reconnect count, last tick time, last error.

#### `GET /api/upstox/stream/ticks/latest?limit=50`
Recent sanitized tick snapshots from memory, falling back to stored `ticks`.

### Index ingest

#### `POST /api/upstox/warehouse/ingest`
Body: `{ instrument, from_date, to_date, chunk_days? }`. Synchronous ingest.

#### `POST /api/upstox/warehouse/ingest/jobs`
Same body. Background job. Returns the warehouse run document immediately. Use this for >1 month ranges.

Note: chunker uses **7-day chunks** for spot to avoid the Upstox `400 Invalid date range` on Feb→Mar boundaries.

#### `GET /api/upstox/warehouse/ingest/jobs/{run_id}`
Latest progress for a background ingest run.

### Option contracts

#### `GET /api/upstox/expiries/{instrument}`
List of available expiries (Upstox Plus required).

#### `GET /api/upstox/options/contracts/{instrument}?expiry=YYYY-MM-DD`
Read-only current contract lookup.

#### `POST /api/upstox/options/contracts/{instrument}/sync?expiry=YYYY-MM-DD`
Fetches current contracts and stores them in `option_contracts`.

#### `GET /api/upstox/expired-options/contracts/{instrument}?expiry=YYYY-MM-DD`
Read-only expired contract lookup.

#### `POST /api/upstox/expired-options/contracts/{instrument}/sync`
Body: `{ from_date, to_date, max_expiries, confirm_large_fetch }`. Backfills expired contract metadata.

### Option warehouse

#### `POST /api/upstox/options/warehouse/preview`
Preview-first planner. Default moneyness `["atm"]`. Returns compact summary plus per-row selected/fetch date counts.

#### `POST /api/upstox/options/warehouse/fetch`
Synchronous fetch guarded by `max_contracts`.

#### `POST /api/upstox/options/warehouse/fetch/jobs`
Background fetch using selected-date task planning.

#### `GET /api/upstox/options/warehouse/fetch/jobs/{run_id}`
Latest progress for a background option fetch run.

#### `POST /api/upstox/options/candles/ingest`
Direct option candle ingest for one contract/window.

## Data Hygiene (Slice 6)

### `POST /api/data-hygiene/plan`
Body: `DataHygieneScopeReq` (instruments, sides, sample, from_date, to_date — all optional, defaults to `2024-11-27 → today`, NIFTY+BANKNIFTY+SENSEX, ATM CE+PE).

Returns prioritized actions per instrument: spot ingest, contract sync, option candle fetch. Pure read; never fetches.

### `POST /api/data-hygiene/execute`
Body: `DataHygieneExecuteReq`. Submits the plan in dependency order (spot → contracts → option_candles). Re-running is safe; partial failures resume cleanly.

### `GET /api/data-hygiene/status?plan_id=...`
Recent hygiene run docs and progress.

## Live Candle Roller (Slice 6.5)

### `GET /api/live-candles/status`
Roller status: tick counts, active buckets, last error, running flag.

### `POST /api/live-candles/start`
Manually start the roller. No-op if running. Auto-starts at backend boot after WS auto-start.

### `POST /api/live-candles/stop`
Stop and flush in-progress buckets.

## Volatility (Slice 7)

### `POST /api/volatility/audit`
Body: `VolatilityAuditReq` with optional config override. Annotates spot 1m bars with realized 5-min vol vs 30-day baseline. Returns summary plus top-20 spike rows.

## Backtest

### `POST /api/backtest/run`
Body: `BacktestConfig`. Includes `instrument`, `mode`, `strategy_id`, `params`, `costs_enabled`, `walkforward`, `start_ts`, `end_ts`, `pretrade_filters`, `name`.

For paired option backtests, supply option fields and `slippage_config` (Slice 7) to override default slippage buckets.

Returns full result with `id`, `metrics`, `trades`, `equity_curve`, `walkforward`, `significance`, `signal_funnel`, `regime_distribution`, optional `option_results`.

### `GET /api/backtest/runs?limit=50`
Recent runs (lightweight).

### `GET /api/backtest/runs/{id}`
Full run.

### `DELETE /api/backtest/runs/{id}`
Remove a run.

## Optimizer

### `POST /api/optimize/start`
Body: `OptimizerStartReq` with `method ∈ {bayesian, grid, genetic}`, `objective ∈ {risk_adjusted, sharpe, profit_factor, total_pnl_pts, win_rate, neg_max_dd}`, `n_trials`, `costs_enabled`, `pretrade_filters`, `param_overrides`, `start_ts`, `end_ts`.

Returns `{ job_id, status: "queued" }`. Runs as a background task.

### `GET /api/optimize/jobs?limit=50`
Recent jobs (lightweight).

### `GET /api/optimize/jobs/{job_id}`
Full job. When done: `best_params`, `best_value`, `best_metrics`, `best_backtest_run_id`, `top_n_alternatives`, `parameter_importance`, `heatmap`, `robustness`.

### `POST /api/optimize/jobs/{job_id}/cancel`
Sets `cancelled=true`. Worker exits at next checkpoint (every 5 trials). Best so far preserved.

### `DELETE /api/optimize/jobs/{job_id}`
Remove a job.

### `POST /api/optimize/apply-as-preset/{job_id}?name=<preset_name>`
Save best params as a Preset.

## Presets and Profiles

### `GET /api/presets`
### `PUT /api/presets/{name}` body: `{ name, config }`
### `DELETE /api/presets/{name}`

### `GET /api/profiles`
Seeded: Conservative, Balanced, Aggressive.

### `PUT /api/profiles/{name}` body: `{ name, settings }`

## Strategy Deployments (Slices 1, 3, 5, 8, 9)

### `GET /api/deployments?status=&limit=`
List deployments.

### `POST /api/deployments`
Body: `DeploymentCreateReq`:

```json
{
  "name": "string",
  "source_type": "preset" | "backtest_run",
  "source_id": "string",
  "mode": "shadow" | "paper" | "recommendation",
  "confirmation_mode": "1m_close",
  "option_policy": {
    "moneyness": "atm" | "otm1" | "itm1",
    "dte_filter": [0, 1, 2, 3, 4, 5, 6]
  },
  "pretrade_profile": "string",
  "risk": {
    "default_lots": 1,
    "allow_overnight": false
  },
  "acknowledged_warnings": false
}
```

Behavior:

- Resolves source preset/backtest run, freezes params, and stores `strategy_source_sha` (Slice 8).
- Calls `deployment_quality.evaluate(...)`. If warnings exist and `acknowledged_warnings=false`, returns `400 acknowledgment_required` with the warning detail.
- Stores `quality_at_creation` plus the ack flag for audit.
- Manual approval is always required for paper or recommendation mode.

### `GET /api/deployments/preflight?instrument=...`
Slice 5 pre-flight check. Returns spot coverage (last 30 trading days), upcoming option expiries, active vs expired contracts, Upstox token state, and per-instrument structural break notes.

### `GET /api/deployments/quality?source_type=...&source_id=...`
Slice 9 quality evaluation against the source. Returns severity-colored warnings.

### `GET /api/deployments/{id}`
Single deployment doc.

### `POST /api/deployments/{id}/pause` `POST /api/deployments/{id}/resume` `POST /api/deployments/{id}/archive`
Status controls.

### `GET /api/deployments/{id}/signals?limit=100`
Signals linked to this deployment.

### `POST /api/deployments/{id}/evaluate-on-close`
Slice 1. Run the 1m_close evaluator once for this deployment. Used by the scheduler and the Evaluate-now button.

### `POST /api/deployments/evaluate-active`
Run the evaluator across every ACTIVE deployment. Used by the scheduler and on-demand.

## Signals (Slices 2, 4)

### `GET /api/signals?state=&limit=`
List recent signal records.

### `POST /api/signals`
Create a manual research signal.

### `POST /api/signals/{id}/transition`
Body: `{ to_state, reason, snapshot }`. Guarded lifecycle transitions.

### `POST /api/signals/{id}/approve`
Body: `SignalApprovalReq` (optional note).

CONFIRMED → TRIGGERED → ACTIVE. When the signal carries `deployment_id` and `deployment.mode == "paper"`, auto-creates a paper trade with `lot_size` from `option_contracts` and `lots` from `deployment.risk.default_lots`. Trade carries `deployment_id` and `source="paper_auto_on_approval"`. A failure to create the trade does not roll back the approval — it journals a `paper_trade_error`.

### `POST /api/signals/{id}/skip`
CONFIRMED → SKIPPED → AUDITED.

### `POST /api/signals/{id}/mark-blocked`
Any non-AUDITED → AUDITED with the supplied note as a blocker.

### `POST /api/signals/{id}/paper`
Manual paper deploy. Body: `{ lots, entry_price?, stop_price?, target_price? }`.

## Paper Trading (Slices 3, 4)

### `GET /api/paper/trades?status=&limit=`
List paper trades.

### `POST /api/paper/trades/{id}/mark`
Body: `{ last_price, auto_close_on_risk? }`. Updates unrealized P&L. Auto-closes if mark hits stored stop or target.

### `POST /api/paper/trades/{id}/close`
Body: `{ exit_price, reason }`. Closes the trade and stores realized P&L.

### `POST /api/paper/square-off`
Force-close all OPEN paper trades immediately. Idempotent. Used by the 15:00 IST background loop and by the manual button.

## Options (Local Reads)

### `GET /api/options/candles?instrument_key=...&...`
Local stored option candle reads.

### `GET /api/options/coverage?underlying=...`
Stored option candle summary by date for the heatmap.

### `GET /api/options/audit/{instrument}?start_ts=...&end_ts=...&expiry=&side=&limit_contracts=`
Raw broad audit by contract metadata. Use Option Data Planner's planned coverage for the trust gate; this is a diagnostic.

### `GET /api/options/contracts/{instrument}`
Local stored contract metadata.

### `DELETE /api/options/data/{instrument}?confirm=CLEAR`
Clear stored option candles only (does not touch index candles or contract metadata).

## Error Conventions

| Code | Meaning |
|---|---|
| 400 | Bad request (invalid instrument, out-of-range params, no candles in window, `acknowledgment_required`, expired option key sent to wrong endpoint) |
| 404 | Resource not found |
| 409 | Conflict (occasional duplicate-source rejections, idempotency edge cases) |
| 503 | Service unavailable (MongoDB down) |

Note: when a forward signal cannot land due to E11000 on the unique partial index `signals_deployment_bar_unique`, the evaluator does not bubble a 409 — it logs the row as `outcome="skipped"`, `reason="already_journaled"` and advances `last_evaluated_ts`.
