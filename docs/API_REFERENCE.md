# API Reference

All routes prefixed with `/api`. JSON request/response. CORS enabled (`*` by default).

## Health & Dashboard

### `GET /api/`
Returns `{ app, status, version }`.

### `GET /api/health`
Returns `{ db: "ok" }` or 503.

### `GET /api/dashboard/summary`
Warehouse stats + strategy load count + backtest run count + latest backtest meta.

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
