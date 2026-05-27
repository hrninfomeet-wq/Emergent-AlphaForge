# API Reference

All routes prefixed with `/api`. JSON request/response. CORS enabled (`*` by default).

## Health & Dashboard

### `GET /api/`
Returns `{ app, status, version }`.

### `GET /api/health`
Returns `{ db: "ok" }` or 503.

### `GET /api/dashboard/summary`
Warehouse stats + strategy load count + backtest run count + latest backtest meta.

### `GET /api/market/header`
Returns the persistent header quote snapshot:

`{ source_mode, updated_at, items: [{ key, label, group, last_price, change, change_pct, timestamp, source, status }] }`.

The route prefers fresh Upstox WebSocket ticks when the local stream is running, then falls back to Upstox REST quote data and per-symbol fallback sources. A failed symbol returns `status: "error"` for that tile instead of failing the whole header. This endpoint does not return account data or tokens.

## Strategies

### `GET /api/strategies`
`{ items: [StrategyMeta] }`. Includes failed plugins with `is_loaded: false` + `error`.

### `GET /api/strategies/{strategy_id}`
Single strategy metadata.

## Data Warehouse

### `POST /api/warehouse/ingest`
Body: `{ instrument: "NIFTY"|"BANKNIFTY"|"SENSEX", days: 1..30 }`.  
Fetches from yfinance, upserts to MongoDB, recomputes per-day integrity hashes.  
Returns `{ run_id, status, candles_added, candles_updated, total_fetched }`.

### `GET /api/warehouse/coverage`
Per-instrument candle counts, date ranges, per-day breakdown for heatmap.

### `GET /api/warehouse/runs?limit=50`
Ingest run audit log.

### `GET /api/warehouse/candles/{instrument}?limit=500`
Latest N candles for chart preview.

## Upstox And Options

### `GET /api/upstox/status`
Returns broker configuration and connection status. Token values are not returned.

### `GET /api/upstox/market-quote/{instrument}`
Returns a sanitized live Upstox full market quote snapshot for `NIFTY`, `BANKNIFTY`, or `SENSEX`. Includes fields such as `last_price`, `timestamp`, `net_change`, and `ohlc`; it does not return account data or tokens.

### `POST /api/upstox/stream/start`
Body: `{ instrument_keys?, mode?, persist_ticks? }`.

Starts the read-only Upstox V3 market-data WebSocket stream. If `instrument_keys` is omitted, the app subscribes to the Upstox-backed market-header instruments. Default `mode` is `ltpc`; supported values are `ltpc`, `full`, `option_greeks`, and `full_d30`. The route requires a connected, non-expired Upstox OAuth token but never returns token data.

### `POST /api/upstox/stream/stop`
Stops the local WebSocket stream and returns sanitized stream status.

### `GET /api/upstox/stream/status`
Returns sanitized stream state: running flag, session id, mode, subscribed instrument count, latest tick count, reconnect count, last tick time, and last non-sensitive error.

### `GET /api/upstox/stream/ticks/latest?limit=50`
Returns recent sanitized tick snapshots from memory, falling back to stored `ticks`. Tick rows include `instrument_key`, `ts`, `received_ts`, `last_price`, `last_trade_quantity`, `close_price`, `source`, `mode`, and local session metadata. Raw broker frames are not exposed.

### `POST /api/upstox/warehouse/ingest`
Body: `{ instrument, from_date, to_date, chunk_days? }`. Fetches Upstox 1-minute index candles into `candles_1m`.

### `POST /api/upstox/warehouse/ingest/jobs`
Body: `{ instrument, from_date, to_date, chunk_days? }`.

Starts a background Upstox 1-minute index candle import and returns a warehouse run document immediately. Use this for large ranges such as 12-18 months so the browser request does not hit the frontend timeout. The run stores `total_chunks`, `completed_chunks`, `progress_pct`, `total_fetched`, `candles_added`, `candles_updated`, `matched_existing`, and `failed_chunks`.

### `GET /api/upstox/warehouse/ingest/jobs/{run_id}`
Returns the latest background ingest run status/progress.

### `GET /api/upstox/options/contracts/{instrument}?expiry=YYYY-MM-DD`
Read-only current option contract lookup.

### `POST /api/upstox/options/contracts/{instrument}/sync?expiry=YYYY-MM-DD`
Fetches current option contracts and stores normalized metadata in `option_contracts`.

### `GET /api/upstox/expired-options/contracts/{instrument}?expiry=YYYY-MM-DD`
Read-only expired option contract lookup. Requires broker access to the expired-instruments API.

### `POST /api/upstox/expired-options/contracts/{instrument}/sync`
Body: `{ from_date, to_date, max_expiries, confirm_large_fetch }`. Fetches expired option contracts for expiries in the inclusive date range and stores normalized metadata in `option_contracts`. If the range exceeds `max_expiries`, the request returns `status: "blocked"` unless `confirm_large_fetch` is true.

### `POST /api/upstox/options/warehouse/preview`
Previews option contracts and candles needed for a spot-history window before broker downloads. The default moneyness is `["atm"]` unless the request provides a broader list.

The response is compact for long ranges: each item includes selected/fetch date counts and first/last dates, but not full per-date maps. Use `summary.planned_coverage_pct`, `summary.missing_data_contracts`, `summary.missing_contract_count`, `summary.stored_selected_date_candles`, and `summary.expected_candles_per_selected_dates` to decide whether the planner-selected option set is ready.

### `POST /api/upstox/options/warehouse/fetch`
Fetches previewed missing option candles into `options_1m`, guarded by `max_contracts`.

### `POST /api/upstox/options/warehouse/fetch/jobs`
Body: same as option warehouse preview/fetch.

Starts a background option candle fetch using the previewed plan. It fetches only the selected dates for each planned contract, grouped into compact date ranges, instead of blindly fetching the whole requested date window for every contract. The run tracks `planned_contracts`, `fetch_contracts`, `total_tasks`, `completed_tasks`, `progress_pct`, `total_fetched`, `candles_added`, `candles_updated`, `matched_existing`, and failed task details.

### `GET /api/upstox/options/warehouse/fetch/jobs/{run_id}`
Returns the latest background option fetch status/progress.

### `GET /api/options/contracts/{instrument}`
Reads locally stored option contracts.

### `GET /api/options/candles`
Reads locally stored option candles by instrument key or option metadata filters.

### `GET /api/options/coverage`
Query: optional `underlying`.

Returns `{ instruments, source }` where each instrument includes total stored option candles, unique contract count, first/last stored option date, and per-date candle/contract coverage for the Option Coverage Heatmap.

### `GET /api/options/audit/{instrument}`
Query: `start_ts`, `end_ts`, optional `expiry`, optional `side`, optional `limit_contracts`.

Audits locally stored option candles by contract and date for a broad raw contract metadata slice. Expected per-day counts come from the underlying index candle sessions in the same window when available, with a weekday-session fallback. Returns `{ summary, items }`, where each item includes instrument key, expiry, strike, side, stored/expected candles, coverage percent, missing days, incomplete days, and status.

This route is a warehouse diagnostic, not the selected-moneyness trust gate. For ATM/OTM/ITM readiness, call `/api/upstox/options/warehouse/preview` with the exact planner settings and check planned coverage.

### `DELETE /api/options/data/{instrument}?confirm=CLEAR`
Clears locally stored option candles for one underlying or `ALL`. It does not delete index candles or option contract metadata.

## Live Signals And Paper Trading

### `GET /api/deployments`
Query: optional `status`, optional `limit`.

Returns Strategy Deployment definitions created from saved presets or saved backtest results.

### `POST /api/deployments`
Body: `{ name, source_type, source_id, mode, confirmation_mode, option_moneyness, pretrade_profile, risk }`.

Creates a Strategy Deployment from an audited source artifact. `source_type` must be `preset` or `backtest_run`. First production path uses `confirmation_mode: "1m_close"` and always stores `manual_approval_required: true`.

### `GET /api/deployments/{deployment_id}`
Returns one deployment definition.

### `POST /api/deployments/{deployment_id}/pause`
Sets deployment status to `PAUSED`.

### `POST /api/deployments/{deployment_id}/resume`
Sets deployment status to `ACTIVE`.

### `POST /api/deployments/{deployment_id}/archive`
Sets deployment status to `ARCHIVED`.

### `GET /api/deployments/{deployment_id}/signals`
Returns signal records linked to that deployment. The evaluator that creates deployment-generated signals is still pending.

### `GET /api/signals`
Query: optional `state`, optional `limit`.

Returns recent signal lifecycle records with audit events.

### `POST /api/signals`
Creates an offline/manual research signal. Body includes `instrument`, `direction`, `strategy_id`, `entry_price`, `confidence`, optional `reasons`, optional `option_contract`, and optional `context`.

### `POST /api/signals/{signal_id}/transition`
Body: `{ to_state, reason, snapshot }`. Moves a signal through the guarded lifecycle. Invalid transitions return 400.

### `POST /api/signals/{signal_id}/paper`
Body: `{ lots, entry_price?, stop_price?, target_price? }`. Deploys the signal to a paper trade and records the transition history. This is paper-only and does not place broker orders.

### `GET /api/paper/trades`
Query: optional `status`, optional `limit`. Returns paper trades.

### `POST /api/paper/trades/{trade_id}/mark`
Body: `{ last_price, auto_close_on_risk? }`. Updates unrealized P&L for an open paper trade. If risk auto-close is enabled and the mark hits the stored stop or target, the trade closes and realized P&L is stored.

### `POST /api/paper/trades/{trade_id}/close`
Body: `{ exit_price, reason }`. Closes a paper trade and stores realized P&L.

## Pre-Trade Profiles

### `GET /api/profiles`
List all profiles (seeded: Conservative, Balanced, Aggressive).

### `PUT /api/profiles/{name}`
Body: `{ name, settings: {...} }`. Upserts.

## Backtest

### `POST /api/backtest/run`
Body: `BacktestConfig` (see `backend/app/models.py`).  
Key fields: `instrument`, `mode`, `strategy_id`, `params`, `costs_enabled`, `walkforward`, `start_ts`, `end_ts`, `pretrade_filters`, `name`.  
Returns full result with `id`, `metrics`, `trades`, `equity_curve`, `walkforward`, `significance`, `signal_funnel`, `regime_distribution`.

### `GET /api/backtest/runs?limit=50`
List past runs (without heavy fields).

### `GET /api/backtest/runs/{id}`
Full single run.

### `DELETE /api/backtest/runs/{id}`
Remove a run.

## Presets

### `GET /api/presets`
### `PUT /api/presets/{name}`  body: `{ name, config }`
### `DELETE /api/presets/{name}`

## Optimizer

### `POST /api/optimize/start`
Body: `{ instrument, mode, strategy_id, method (bayesian|grid|genetic), objective (risk_adjusted|sharpe|profit_factor|total_pnl_pts|win_rate|neg_max_dd), n_trials (10-5000), costs_enabled, pretrade_filters, param_overrides, start_ts, end_ts, name }`.  
Returns `{ job_id, status: "queued" }`. Job runs async in background task.

### `GET /api/optimize/jobs?limit=50`
List recent jobs (lightweight).

### `GET /api/optimize/jobs/{job_id}`
Full job state including (when done): `best_params`, `best_value`, `best_metrics`, `best_backtest_run_id`, `top_n_alternatives`, `parameter_importance`, `heatmap`, `robustness`.

### `POST /api/optimize/jobs/{job_id}/cancel`
Sets `cancelled=true`. Worker exits gracefully at next check (every 5 trials). Best so far is preserved.

### `DELETE /api/optimize/jobs/{job_id}`
Remove a job.

### `POST /api/optimize/apply-as-preset/{job_id}?name=<preset_name>`
Saves the best params as a Preset for reuse in Backtest Lab.

## Error Codes

| Code | Meaning |
|---|---|
| 400 | Bad request (invalid instrument / out-of-range n_trials / no candles in window) |
| 404 | Resource not found |
| 503 | Service unavailable (MongoDB down) |
