# API Reference

Updated: 2026-06-01

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
Per-day index candle audit. **Holiday-aware**: expected trading days come from `nse_calendar.trading_days_in_range` (skips weekends + NSE/BSE holidays, includes Budget Saturdays). `summary.calendar_assumption == "nse_trading_calendar"`.

### `GET /api/warehouse/candles/{instrument}?limit=500`
Latest N candles for chart preview.

### `GET /api/warehouse/lookup?instrument=...&date=YYYY-MM-DD&time=HH:MM`
Point-in-time warehouse lookup (reads only `candles_1m`, `options_1m`, `option_contracts` — never the broker). Returns the spot candle for that IST minute (exact or nearest bar within 5 min, `spot_exact` flag), the derived ATM strike (nearest to spot close, step per index), the nearest stored expiry on/after the date, and the ATM CE/PE candles with OI. Useful for cross-checking stored data against a broker terminal.

### `GET /api/warehouse/ohlc/{instrument}?timeframe=1d&start_ts=&end_ts=&include_gaps=true`
Server-side OHLC resampling of stored 1m candles. `timeframe ∈ {1m, 5m, 15m, 1h, 1d}` (resampled on IST-localized buckets; 1h is anchored to 09:15 IST). Returns `{ instrument, timeframe, bar_count, bars: [{ts, time, open, high, low, close, volume}], gaps: [{date, stored, expected, missing_count, missing_sample[]}], gap_day_count }`. `gaps` lists completed trading days with fewer than 375 stored minutes. Omit the window for full stored history; the Warehouse chart now does this for every timeframe.

### `GET /api/warehouse/auto-update/status`
Auto-update worker state: `{ enabled, in_progress, last_started_at, last_finished_at, last_status, last_reason, last_submitted_count, last_actions_planned, runs_count, history[] }`.

### `POST /api/warehouse/auto-update/toggle`
Body: `{ enabled: bool }`. Enable/disable automatic warehouse catch-up.

### `POST /api/warehouse/auto-update/run`
Trigger a catch-up immediately (manual). Returns `{ summary, state }`.

### `GET /api/calendar/holidays?year=YYYY`
NSE/BSE market-holiday calendar for the year (omit `year` for available years + current year). Returns `{ available_years: [...], calendar: { year, verified_through, holidays: [{date, label, weekday}], special_sessions: [{date, label, weekday}], holiday_count } }`.

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

#### `GET /api/upstox/stream/options/universe?underlyings=NIFTY,BANKNIFTY,SENSEX&radius=1&max_option_keys=60`
Preview the current live option subscription universe without mutating the stream. Uses live spot ticks first, falls back to latest stored `candles_1m`, then reads the nearest stored `option_contracts.expiry_date >= today`. Default radius `1` returns ATM +/- one strike for CE and PE.

#### `POST /api/upstox/stream/options/restart`
Body: `{ underlyings?, radius?, max_option_keys?, mode?, persist_ticks? }`. Restarts the read-only Upstox stream with the normal market-header instruments plus the previewed option universe. Use during market hours after current option contracts are synced. No broker orders are placed.

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
Body: `BacktestReq`. Includes `instrument`, `mode`, `strategy_id`, `params`, `costs_enabled`, `walkforward`, `start_ts`, `end_ts`, `pretrade_filters`, `name`, `trade_window_start/end`, and an `option_backtest` block (enabled, moneyness, lots, exit_mode, dte_filter, cost_config, sizing_config).

Returns full result with `id`, `metrics`, `trades`, `equity_curve`, `walkforward`, `significance`, `signal_funnel`, `regime_distribution`, optional `option_results`.

### `POST /api/backtest/option-preflight?ingest_missing=0|1`
Same body as `POST /backtest/run` with `option_backtest.enabled=true`. Returns a would-pair coverage report: `{ enabled, total_spot_trades, would_pair, missing_contract, missing_candle, coverage_pct, missing_contract_keys, expiry_mode, window }`. With `ingest_missing=1` and Upstox connected, submits a background option-warehouse fetch for the window.

### `GET /api/backtest/runs?limit=50`
Recent runs (lightweight).

### `GET /api/backtest/runs/{id}`
Full run.

### `DELETE /api/backtest/runs/{id}`
Remove a run.

## Optimizer

### `POST /api/optimize/start`
Body: `OptimizerStartReq`. Key fields:

| Field | Default | Notes |
|-------|---------|-------|
| `method` | `"bayesian"` | `bayesian` \| `grid` \| `genetic` |
| `objective` | `"risk_adjusted"` | `risk_adjusted` \| `sharpe` \| `profit_factor` \| `total_pnl_pts` \| `net_pnl_inr` \| `win_rate` \| `neg_max_dd` |
| `n_trials` | 200 | 10–5000 |
| `evaluation_mode` | `"spot"` | `"spot"` (original, fast) \| `"option_rerank"` (two-stage: spot search then re-rank top-K by real option net rupee) |
| `rerank_top_k` | 50 | 1–500; number of spot-best candidates re-evaluated on real option P&L |
| `option_config` | null | Required when `evaluation_mode="option_rerank"`: `{ moneyness, dte_filter, lots, exit_mode, option_target_pct, option_stop_pct, cost_config, entry_max_age_sec, exit_max_age_sec }` |
| `guards_enabled` (frontend→) | — | Translated to `min_trades` / `min_direction_share` in the payload |
| `min_trades` | 10 | 0 = no floor; disqualifies statistically meaningless samples |
| `min_direction_share` | 0.0 | 0 = off; minority CE/PE share floor (0.10 = 10% minimum) |
| `optimize_indicator_periods` | false | Also tunes RSI/MACD/ATR/EMA/ADX/CHOP/swing lengths; indicators recomputed per trial |
| `pretrade_filters` | `{}` | Apply same pre-trade profile used in live trading |
| `pretrade_profile` | `"None"` | Stored for display/clone; engine uses `pretrade_filters` |
| `param_overrides` | `{}` | Widen/narrow per-param bounds |
| `start_ts`, `end_ts` | null | IST epoch ms window |

Returns `{ job_id, status: "queued" }`. Runs as a background asyncio task.

### `GET /api/optimize/jobs?limit=50`
Recent jobs (lightweight projection — excludes `trial_log`, `heatmap`, `robustness`, `rerank`, `param_space`, `top_n_alternatives`).

### `GET /api/optimize/jobs/{job_id}`
Full job document. Statuses: `queued → running → analyzing → done | cancelled | paused | interrupted | failed`.
When done/cancelled: `best_params`, `best_value`, `best_metrics`, `best_backtest_run_id`, `evaluation_mode`, `top_n_alternatives`, `parameter_importance`, `heatmap`, `robustness`, `rerank` (when `evaluation_mode=option_rerank`).
When paused/interrupted: `best_so_far`, `trial_log` (compact, for resume).

### `POST /api/optimize/jobs/{job_id}/cancel`
Sets `cancelled=true`. Worker breaks at the next trial boundary. Analysis (heatmap/robustness) is skipped; best-so-far is still saved.

### `POST /api/optimize/jobs/{job_id}/pause`
Sets `paused=true`. Worker flushes the compact trial log and best-so-far, then sets `status=paused` and exits. Can be Resumed.

### `POST /api/optimize/jobs/{job_id}/resume`
Re-launches the worker for a `paused` / `interrupted` / `failed` job. Rehydrates prior trial history, re-seeds the Optuna study, and continues from the last saved trial.

### `DELETE /api/optimize/jobs/{job_id}`
Remove a job.

### `POST /api/optimize/apply-as-preset/{job_id}?name=<preset_name>`
Save best params as a Preset. Accepts jobs with status `done`, `cancelled`, `paused`, `interrupted`, or `failed` — uses `best_params` with a fallback to `best_so_far.params`. Returns 400 if no params exist yet.

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
  "max_consecutive_losses": null,
  "daily_loss_cutoff_pct": null,
  "max_open_paper_trades": null,
  "acknowledged_warnings": false
}
```

Behavior:

- Resolves source preset/backtest run, freezes params, and stores `strategy_source_sha` (Slice 8).
- Calls `deployment_quality.evaluate(...)`. If warnings exist and `acknowledged_warnings=false`, returns `400 acknowledgment_required` with the warning detail.
- Stores `quality_at_creation` plus the ack flag for audit.
- Manual approval is always required for paper or recommendation mode.
- Kill switches (Slice 12, paper mode only) are merged into `risk`. `max_consecutive_losses` and `daily_loss_cutoff_pct` (negative %) auto-PAUSE the deployment; `max_open_paper_trades` soft-BLOCKs new signals while that many trades are open. Omit/null/0 disables a switch. A paused deployment stamps `kill_switch_reason`, `kill_switch`, and `kill_switch_inputs`.

### `GET /api/deployments/preflight?instrument=...`
Slice 5 pre-flight check. Returns spot coverage (last 30 trading days), upcoming option expiries, active vs expired contracts, Upstox token state, and per-instrument structural break notes.

### `GET /api/deployments/quality?source_type=...&source_id=...`
Slice 9 quality evaluation against the source. Returns severity-colored warnings.

### `GET /api/deployments/metrics?strategy_id=&include_ineligible=false&limit=100`
Session-gated forward metrics for deployments. By default returns only deployments visible in Strategy Library (`complete_session_count >= 10`). Pass `include_ineligible=1` for audit/debug views. Metrics are computed from closed `paper_trades`, but headline win-rate / avg P&L / profit factor include only trades whose entry session had at least 70% coverage in the 10:00-15:00 IST window.

### `GET /api/deployments/{deployment_id}/metrics`
Forward metrics for one deployment, including session-completeness summary and excluded incomplete-session trade count.

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

### `GET /api/options/coverage?underlying=...&refresh=`
Stored option candle summary by date for the heatmap. Served from the precomputed `option_coverage_cache` (~200ms vs ~8s for the raw aggregation). The cache is warmed on startup, refreshed after option-fetch jobs and after clearing option data. Pass `refresh=1` to force a recompute.

### `GET /api/options/audit/{instrument}?start_ts=...&end_ts=...&expiry=&side=&limit_contracts=`
Raw broad audit by contract metadata. Programmatic/diagnostic only — the UI panel was removed (the Data Hygiene panel, Option Coverage Heatmap, and Planner planned-coverage cover this). Use the Option Data Planner's planned coverage for the trust gate.

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
