# API Reference

Updated: 2026-09-30 (diffed against every route decorator in `backend/server.py` and
`backend/app/routers/*.py` on that date).

Every backend HTTP route, grouped by area. All routes are mounted under the `/api`
prefix: one `APIRouter(prefix="/api")` in `backend/server.py` includes eight
sub-routers (`research`, `strategies_admin`, `warehouse`, `journals`, `deployments`,
`broker`, `live_broker`, `premium_momentum_routes`), each an `APIRouter()` with no
prefix of its own. JSON in / JSON out (the `*/stream` routes are Server-Sent Events).
CORS allows the origins in the `CORS_ORIGINS` env var (comma-separated; the code
default is `*`, and `docker-compose.yml` sets `http://localhost:3000`, so browse at
`localhost:3000`, not `127.0.0.1:3000`). IST throughout; NSE session 09:15-15:30,
live new-entry cutoff and EOD square at 15:00.

**Route count: 198** (`broker` 22, `deployments` 29, `journals` 16, `live_broker` 47,
`premium_momentum_routes` 3, `research` 22, `strategies_admin` 17, `warehouse` 40,
`server.py` 2). The source of truth is the `@api.<verb>(...)` decorators. This file is
the map; for any route not given a shape below, read the route function and its
Pydantic body model (`app/schemas.py` or the router file).

**Changes since the 2026-07-01 edition.** 25 routes were added (marked **new** below).
No documented route was removed. Changed payloads: `GET /live-broker/test-session`
no longer returns `deadline` / `remaining_secs` (the manual 10-minute timer was removed
2026-07-09); `GET /deployments/{id}/live/status` gained `governor`, `entry_window`,
`last_entry` and `live_paused`; `POST /signals/purge` gained `blocked`;
`GET /live-broker/arm-state` gained `session`; `GET /live-broker/guard-status` gained
`health`; `GET /live-broker/greeks` gained `book`.

Related docs: [ARCHITECTURE.md](./ARCHITECTURE.md) (module map, collections, gate
chain) · [DEVELOPER_GUIDE.md](./DEVELOPER_GUIDE.md) · [USER_MANUAL.md](./USER_MANUAL.md)
· [STRATEGY_DEPLOYMENTS.md](./STRATEGY_DEPLOYMENTS.md) · the decoded broker API in
[Resources/flattrade-pi-api/INDEX.md](./Resources/flattrade-pi-api/INDEX.md).

---

## Health / dashboard / market header + analysis (`server.py`, `journals.py`, `broker.py`)

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/` | Root: `{ app, status, version }`. |
| GET | `/api/health` | DB ping: `{ db: "ok" }` or 503 when MongoDB is down. |
| GET | `/api/dashboard/summary` | Warehouse stats + strategy load count + backtest run count + latest backtest meta. |
| GET | `/api/market/header` | Persistent market-header snapshot (WS-first, REST-fallback per tile; failed tiles carry `status:"error"`; never returns tokens). |
| GET | `/api/market/header/stream` | SSE feed of market-header snapshots (on connect, on each tick debounced to ~10/s, 15s heartbeat). |
| GET | `/api/market/analysis` | **new.** Read-only market analysis for the live cockpit (`instrument`, default `NIFTY`): `{instrument, as_of, spot, structure, trend:{intraday,daily,weekly,monthly}, levels, options:{pcr_oi, max_pain, iv_rank_30d, iv_rank_source, atm_straddle, implied_move_pct, net_delta_rupee, net_theta_rupee, expiry, chain}, warnings}`. Server-cached ~8s. Degrades to nulls plus a code in `warnings` (whole-payload failure = `["analysis_unavailable"]`); never 500s. Portfolio Greeks are not here (see `/live-broker/greeks`). |
| GET | `/api/market/analysis/stream` | **new.** SSE of the same payload, pushed on the Upstox tick (at most 4/s); no broker calls (chain built from the in-memory tick map). |

## Data feed — Upstox connection + read-only market stream (`broker.py`)

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/upstox/status` | Upstox connection state (tokens never returned). |
| GET | `/api/upstox/auth/start` | Begin the Upstox OAuth flow. |
| GET | `/api/upstox/auth/callback` | OAuth callback; redirects to `FRONTEND_POST_AUTH_URL`. |
| POST | `/api/upstox/disconnect` | Remove the encrypted Upstox token doc. |
| GET | `/api/upstox/market-quote/{instrument}` | Sanitized live REST quote snapshot. |
| POST | `/api/upstox/stream/start` | Start the read-only V3 market-data WS stream. |
| POST | `/api/upstox/stream/stop` | Stop the local stream. |
| GET | `/api/upstox/stream/status` | Sanitized stream status (running, session, subscribed count, tick counts, reconnects, last error). |
| GET | `/api/upstox/stream/options/universe` | Preview the ATM-band option subscription universe without mutating the stream. |
| POST | `/api/upstox/stream/options/restart` | Restart the read-only stream with header instruments + the previewed option universe (no broker orders). |
| GET | `/api/upstox/stream/ticks/latest` | Recent sanitized tick snapshots (memory, falling back to stored `ticks`). |

## Live candle roller + feed health + diagnostics (`broker.py`)

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/live-candles/status` | Tick→1m roller status: tick counts, active buckets, last error, running flag. |
| POST | `/api/live-candles/start` | Manually start the roller (no-op if running); clears the supervisor suppression flag. |
| POST | `/api/live-candles/stop` | Stop the roller and flush in-progress buckets. |
| GET | `/api/live-feed/health` | Truthful pipeline health (token → stream → roller → fresh `candles_1m`), plus today's per-instrument minute `completeness`, `all_complete`, and the latest `candle_recovery` outcome. |
| GET | `/api/live-exit-monitor/status` | Paper exit-monitor status (tick-driven stop/target exits for paper trades). |
| POST | `/api/_diag/tick-replay` | **new.** Inject synthetic ticks (`rate_hz`, `duration_s`, `keys`) to measure tick-to-pixel latency out of hours. 403 unless `ALPHAFORGE_TICK_REPLAY=1`; writes only the reserved `HARNESS\|` key namespace, never persists. |
| POST | `/api/_diag/tick-replay/purge` | **new.** Drop every harness key from the live tick map: `{ purged }`. |

## Data Warehouse — spot ingest, OHLC, audit, calendar (`warehouse.py`)

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/warehouse/ingest` | yfinance-fallback ingest (`{ instrument, days }`). |
| GET | `/api/warehouse/coverage` | Per-instrument coverage with per-day breakdown for the heatmap. |
| GET | `/api/warehouse/runs` | Ingest-run audit log (also surfaces hygiene + option-fetch jobs). |
| POST | `/api/warehouse/intraday-backfill/{instrument}` | Backfill TODAY's 1m candles from the Upstox intraday endpoint (closes the morning gap when the roller was down). `ALL` = all three indices. |
| GET | `/api/warehouse/candles/{instrument}` | Latest N candles for chart preview. |
| GET | `/api/warehouse/lookup` | Point-in-time lookup (spot bar + derived ATM strike + nearest expiry + ATM CE/PE candles); reads stored data only, never the broker. |
| GET | `/api/warehouse/ohlc/{instrument}` | Server-side OHLC resampling of stored 1m candles (`timeframe ∈ {1m,5m,15m,1h,1d}`, IST buckets, 1h anchored to 09:15) + completed-day gap report. |
| GET | `/api/warehouse/audit/{instrument}` | Per-day index-candle audit; **holiday-aware** (expected days from `nse_calendar.trading_days_in_range`). |
| DELETE | `/api/warehouse/data/{instrument}` | Developer clear (`confirm=CLEAR`; `ALL` clears all three indices; does not touch options). |
| GET | `/api/warehouse/auto-update/status` | Auto-update worker state (enabled, in_progress, last run, history). |
| POST | `/api/warehouse/auto-update/toggle` | Enable/disable the automatic catch-up worker (`{ enabled }`). |
| POST | `/api/warehouse/auto-update/run` | Trigger a catch-up immediately; returns `{ summary, state }`. |
| GET | `/api/warehouse/vix/coverage` | India VIX warehouse coverage (count + date range + baseline start). |
| POST | `/api/warehouse/vix/ingest` | Fetch India VIX 1m candles from Upstox → persist as `INDIAVIX` (powers the vix_bucket volatility layer). |
| GET | `/api/calendar/holidays` | NSE/BSE holiday + special-session calendar (omit `year` for available years + current). |

## Data Hygiene / one-button sync (`warehouse.py`)

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/data-hygiene/plan` | Prioritized per-instrument action plan (spot ingest, contract sync, option-candle fetch). Pure read; never fetches. |
| POST | `/api/data-hygiene/execute` | Run a plan in dependency order (spot → contracts → option candles); re-runnable, resumes cleanly. |
| GET | `/api/data-hygiene/status` | Recent hygiene run docs + progress. |
| GET | `/api/data-hygiene/latest` | Last persisted hygiene plan (instant, no aggregation; null until first check). |
| POST | `/api/data-hygiene/catch-up` | Sequential per-instrument catch-up to the last closed session, then band-exact option gap fill (`dry_run`, `include_options` flags). Needs an Upstox token. |
| POST | `/api/warehouse/sync` | One-button sync — alias of `/data-hygiene/catch-up`. |

## Volatility audit (`warehouse.py`)

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/volatility/audit` | Annotate spot 1m bars with realized 5-min vol vs 30-day baseline; returns summary + top-20 spike rows. |

## Upstox index-history ingest (`warehouse.py`)

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/upstox/warehouse/ingest` | Synchronous spot ingest (`{ instrument, from_date, to_date, chunk_days? }`; 7-day chunks). |
| POST | `/api/upstox/warehouse/ingest/jobs` | Same body, background job; returns the run doc immediately (use for >1 month ranges). |
| GET | `/api/upstox/warehouse/ingest/jobs/{run_id}` | Progress for a background spot-ingest run. |

## Option warehouse — contracts + candle fetch (`warehouse.py`)

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/upstox/expiries/{instrument}` | Available expiries (Upstox Plus). |
| GET | `/api/upstox/options/contracts/{instrument}` | Read-only current contract lookup. |
| POST | `/api/upstox/options/contracts/{instrument}/sync` | Fetch current contracts → store in `option_contracts`. |
| GET | `/api/upstox/expired-options/contracts/{instrument}` | Read-only expired-contract lookup. |
| POST | `/api/upstox/expired-options/contracts/{instrument}/sync` | Backfill expired-contract metadata over a date range. |
| POST | `/api/upstox/options/warehouse/preview` | Preview-first planner (default moneyness `["atm"]`) with per-row selected/fetch counts. |
| POST | `/api/upstox/options/warehouse/fetch` | Synchronous option-candle fetch guarded by `max_contracts`. |
| POST | `/api/upstox/options/warehouse/fetch/jobs` | Background option-candle fetch using selected-date task planning. |
| GET | `/api/upstox/options/warehouse/fetch/jobs/{run_id}` | Progress for a background option-fetch run. |
| POST | `/api/upstox/options/candles/ingest` | Direct option-candle ingest for one contract/window. |

## Options — local reads (`warehouse.py`)

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/options/candles` | Local stored option-candle reads. |
| GET | `/api/options/coverage` | Stored option-candle summary by date for the heatmap (served from `option_coverage_cache`; `refresh=1` forces recompute). |
| GET | `/api/options/audit/{instrument}` | Raw broad audit by contract metadata (diagnostic/programmatic only; UI panel removed). |
| GET | `/api/options/contracts/{instrument}` | Local stored contract metadata. |
| DELETE | `/api/options/data/{instrument}` | Clear stored option candles only (`confirm=CLEAR`; index candles + contract metadata untouched). |

## Backtest Lab (`research.py`)

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/backtest/run` | Synchronous backtest (spot or paired-option via the `option_backtest` block); returns the full result. Kept for scripts. |
| POST | `/api/backtest/start` | Fire-and-forget backtest: inserts the run doc, launches the worker, returns `{ run_id, status }`; client polls the run. |
| POST | `/api/backtest/spot-preflight` | **new.** Audit the spot-data window of a `BacktestReq` (needs `start_ts` + `end_ts`, else 400): `{ before, after, ... }` audit summaries. `ingest_missing=1` runs the same audit → Upstox gap-fill → audit helper the backtest itself uses. Advisory; never gates a run. |
| POST | `/api/backtest/option-preflight` | Would-pair option-coverage report for an option-enabled config; `ingest_missing=1` submits a background fetch. |
| GET | `/api/backtest/runs` | Recent runs (lightweight; excludes trades/equity/walkforward). |
| GET | `/api/backtest/runs/{run_id}` | Full run doc. |
| DELETE | `/api/backtest/runs/{run_id}` | Remove a run. |

## Optimizer (`research.py`)

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/optimize/start` | Launch a search (`method ∈ bayesian\|grid\|genetic`; `evaluation_mode ∈ spot\|option_rerank`; survival guards). Returns `{ job_id, status:"queued" }`. |
| POST | `/api/optimize/wfo` | Honest walk-forward optimization (rolling/anchored windows, stitched OOS, optional `option_aware`). Returns `{ job_id, status:"queued", kind:"wfo" }`. |
| GET | `/api/optimize/jobs` | Recent jobs (lightweight projection). |
| GET | `/api/optimize/jobs/{job_id}` | Full job doc (`queued→running→analyzing→done\|cancelled\|paused\|interrupted\|failed`). |
| DELETE | `/api/optimize/jobs/{job_id}` | Remove a job. |
| POST | `/api/optimize/jobs/{job_id}/cancel` | Cancel at the next trial boundary (best-so-far still saved). |
| POST | `/api/optimize/jobs/{job_id}/pause` | Flush the compact trial log + best-so-far, then pause. |
| POST | `/api/optimize/jobs/{job_id}/resume` | Rehydrate a paused/interrupted/failed job and continue. |
| POST | `/api/optimize/apply-as-preset/{job_id}` | Save the job's best params (+ any option execution policy) as a Preset. |

## Premium-momentum / premium-trigger research (`premium_momentum_routes.py`)

All three are **new** to this file. Synchronous, option-native sims over stored candles; no broker calls.

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/premium-momentum/backtest` | `{ instrument, start_ts, end_ts, params }` → the `premium_momentum` backtest result. Returns an empty result (not an error) when the window has no spot candles. VIX gate data is loaded only when `vix_min`/`vix_max` is set. |
| POST | `/api/premium-momentum/tune` | `{ instrument, start_ts, end_ts, base_params, grid, train_frac=0.7 }` → chronological train/OOS tuning report. 400 on a non-tunable grid key (the allowed set is `TUNABLE_KEYS` in the router) or a window with no spot candles. |
| POST | `/api/premium-trigger/backtest` | `{ instrument, start_ts, end_ts, premium_trigger }` → the same sim dispatched from a declarative `PremiumTriggerConfig` (400 on an invalid config). Byte-identical to `/premium-momentum/backtest` for equivalent params (pinned by `tests/test_premium_trigger_dispatch_parity.py`). |

## Presets + Profiles (`research.py`)

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/presets` | List saved presets. |
| PUT | `/api/presets/{name}` | Upsert a preset (`{ name, config }`). |
| DELETE | `/api/presets/{name}` | Delete a preset. |
| POST | `/api/presets/{name}/rename` | Rename a preset (`new_name` query); re-points preset-sourced deployments. |
| GET | `/api/profiles` | Pre-trade profiles (seeded Conservative / Balanced / Aggressive). |
| PUT | `/api/profiles/{name}` | Upsert a profile (`{ name, settings }`). |

## Strategy Library + AI Authoring (`strategies_admin.py`)

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/strategies` | List strategy metadata (failed plugins included with `is_loaded:false` + `error`). |
| GET | `/api/strategies/catalog` | Authoring vocabulary: valid columns / ops / regimes / exit fields / param types (host-safe, no DB). |
| GET | `/api/strategies/{strategy_id}` | Single strategy metadata (+ `is_retired`). |
| GET | `/api/strategies/{strategy_id}/pipeline` | **new.** Authored→backtested→optimized→paper→live state in one call: `{ strategy_id, backtests, optimizations, presets, deployments, paper_deployments, live_ever_count, live_armed_count, stages }`. The first five counters are each `{count, latest}`; `live_armed_count` = deployments with `mode=="live"`. 404 if unknown. |
| GET | `/api/strategies/{strategy_id}/references` | **new.** Delete blast radius: `{ strategy_id, references, orphaned_total }` (presets + backtest runs + optimization jobs that would be orphaned). |
| POST | `/api/strategies/{strategy_id}/retire` | Retire a strategy (squares off its deployments; blocks new deploys). |
| POST | `/api/strategies/{strategy_id}/un-retire` | Clear the retired flag. |
| DELETE | `/api/strategies/{strategy_id}` | Delete a custom plugin file (must be retired + no non-archived deployments; built-ins rejected 403; orphaning saved artifacts needs `confirm=true`). |
| POST | `/api/strategies/reload` | Reload the plugin registry from disk. |
| POST | `/api/strategies/author/compile` | Validate + compile a spec to source WITHOUT installing (returns errors for the wizard). |
| POST | `/api/strategies/author/install` | Compile + write plugin file + reload + record provenance (409 on id clash unless `overwrite`). Rolls back on failure. |
| GET | `/api/strategies/author/providers` | Configured AI providers + the active default (env only). |
| POST | `/api/strategies/author/from-source` | Map pasted text / YouTube link → constrained `StrategySpec` + fidelity (FAST tier). |
| POST | `/api/strategies/author/converse` | Collaborative gate: parse source → per-rule feasibility → BUILD/ASK/ADVISE/REJECT. |
| POST | `/api/strategies/author/python-from-source` | Generate an arbitrary `StrategyBase` module via the POWERFUL tier (no install). |
| POST | `/api/strategies/author/python/validate` | Static-check the generated Python; if clean, smoke-test it. |
| POST | `/api/strategies/author/python/install` | Server re-validates (static + smoke), then writes + reloads + records provenance. |

## Strategy Deployments (`deployments.py`)

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/deployments` | List deployments (`status`, `limit` filters). |
| POST | `/api/deployments` | Create a signal-only/paper deployment from `strategy`, `preset`, or `backtest_run` (freezes params + `strategy_source_sha`; runs the warning/ack gate; auto-paper defaults). Direct strategy requests use `source_instrument`, `source_timeframe=1m`, and `source_params`. |
| GET | `/api/deployments/preflight` | Pre-flight: spot coverage, upcoming expiries, active vs expired contracts, Upstox token, structural-break notes. |
| GET | `/api/deployments/quality` | Quality evaluation vs the source (severity-colored warnings). |
| GET | `/api/deployments/readiness` | Deployment-readiness evidence (latest WFO + option-rupee proof; informational, never blocks). |
| GET | `/api/deployments/metrics` | Session-gated forward metrics across deployments (`include_ineligible` for low-sample). |
| GET | `/api/deployments/{deployment_id}/metrics` | Forward metrics for one deployment (+ session-completeness summary). |
| GET | `/api/deployments/overview` | Command-center roll-up: `{ items, totals, as_of_ist, market_status }`. Each item has `today` (clean/blocked signals by the bar's `candle_ts`, realized P&L, `open_trades`, `open_unrealized`, `open_unverified`, `open_carried`, `open_carried_oldest`, and `other_book` = the deployment's rows in the collection of its non-current mode) and `lifetime`. OPEN rows from an earlier day are counted as carried, not as today's. |
| GET | `/api/deployments/{deployment_id}` | Single deployment doc. |
| PUT | `/api/deployments/{deployment_id}/paper-caps` | **new.** Paper deployments only (else 400). Body `{ lots_override?: 1..100, max_concurrent?: 1..50, daily_caps?: DailyCapsConfig, capital?: {amount > 0, basis: "fixed"\|"cumulative"} }`; a `null` field clears that cap; non-finite numbers 400. Writes `risk.<field>` and returns the updated deployment doc. |
| POST | `/api/deployments/{deployment_id}/pause` | Pause a deployment. Pausing a live deployment demotes it to paper (use `/live/pause` for a hold that keeps it live). |
| POST | `/api/deployments/{deployment_id}/resume` | Resume a paused deployment (best-effort option-stream realign; 409 if the strategy is retired). |
| POST | `/api/deployments/stop-all` | Stop everything: square off all open paper trades, flatten every `mode=="live"` deployment's live positions and demote it to paper, then pause every ACTIVE deployment. Returns `{ squared_off, squared_off_count, paused_deployment_ids, disarmed_live_deployment_ids, live_exit_reports }`. |
| POST | `/api/deployments/{deployment_id}/stop` | Square off THIS deployment's open paper trades (and, if live, submit exits for its live positions through the same path as `/live/stop`), then pause (which demotes live to paper). Response adds `squared_off`, `live_exit` (exit report). |
| POST | `/api/deployments/{deployment_id}/archive` | Undeploy (`purge=1` also deletes its signals + CLOSED trades; OPEN kept for square-off). |
| GET | `/api/deployments/{deployment_id}/signals` | Signals linked to this deployment (`limit` ≤ 500). |
| GET | `/api/deployments/{deployment_id}/timeline` | **new.** Read-only session timeline for one IST day. See shapes below. |
| POST | `/api/deployments/{deployment_id}/evaluate-on-close` | Run the 1m_close evaluator once for this deployment. |
| POST | `/api/deployments/evaluate-active` | Run the evaluator across every ACTIVE deployment. |
| POST | `/api/deployments/{deployment_id}/repin-source` | Re-pin to the strategy's current source after a drift pause (recomputes SHA, resumes if drift-paused). |

### Live (real-money) sub-routes

Authorization model (v0.56.0+): a deployment is live when `mode == "live"`. There is no
per-deployment arm record. An entry is allowed only while the deployment is live, not
held (`risk.live.paused`), the Flattrade session is connected and not expired, and it is
before 15:00 IST (`live/mode.py::is_deployment_live_allowed`). The legacy `armed` /
`armed_until` fields in status payloads are kept for compatibility: `armed` equals
`live_mode`, and `armed_until` is always `null`.

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/deployments/{deployment_id}/live/enable` | Switch to persistent LIVE mode; this body is the sole writer of the live caps. Requires ACTIVE / not retired / no drift pause, a connected and unexpired Flattrade session with a static IP configured, engine can-trade, required positive `lots` / `max_lots_per_day` / `max_concurrent` and a required positive `daily_loss_cap` (`_validate_live_cap_values`; the catastrophe band is optional), account lot/position ceilings, and strict `confirm=true`. 409 `incomplete_market_data` when today's candles for the deployment cannot be verified (`live_data_gate`, a data check, not an arming gate). Failed/missing forward evidence returns a consent challenge (`explicit_unvalidated_live_consent_required`) unless strict `accept_unvalidated_live=true`; the decision and evidence snapshot are persisted. CAS on write (409 `deployment_changed_during_enable`). |
| POST | `/api/deployments/{deployment_id}/live/pause` | **Reversible hold.** Sets `risk.live.paused`; blocks new live ENTRIES only. `mode` stays `live` and `status` stays `ACTIVE`, so open positions keep their guard/exits — paused is NOT flat. 409 if the deployment is not in live mode. |
| POST | `/api/deployments/{deployment_id}/live/resume` | Lift the hold. No consent is re-collected (authorization was never lost). Compare-and-swap on `updated_at`; 409 `deployment_changed_during_resume` if anything changed in flight. |
| POST | `/api/deployments/{deployment_id}/live/caps` | **new.** Tighten-only cap change that keeps the deployment live. See shapes below. |
| POST | `/api/deployments/{deployment_id}/live/flatten` | **new.** Exit this deployment's live positions and stay live (optional hold). See shapes below. |
| POST | `/api/deployments/{deployment_id}/live/disable` | Revert to paper mode (does NOT flatten open positions; they stay with the guard). Clears any hold; keeps the live caps for a later re-enable. Returns `{ deployment_id, mode:"paper", live }`. |
| POST | `/api/deployments/{deployment_id}/live/stop` | Submit exits for THIS deployment's live positions, then demote to paper AND pause (a user-initiated exit; transmits directly). Returns the exit report plus `disabled:true, paused:true, live`. An exit submission is not a fill; the legacy `squared_tsyms` / `squared_count` are always `[]` / `0`. |
| GET | `/api/deployments/live/status` | Batched live status: `ids=` comma-separated (deduplicated, at most 200) → `{ <deployment_id>: <per-id payload> }`. Unknown ids are omitted, never a 404. One account read is shared by the whole batch. |
| GET | `/api/deployments/{deployment_id}/live/status` | One deployment's live status (404 if unknown). See shapes below. |

### Shapes: live status, caps, flatten, timeline

**`GET /deployments/{id}/live/status`** (and each value of the batched route):

```
{ armed, live_mode, live_paused, armed_until: null,
  caps:  { lots, max_lots_per_day, max_concurrent, daily_loss_cap },   // stored values
  today: { orders, lots, realized_pnl },
  open_positions: [ { id, tsym, qty, entry_price, stop_level, target_level, seen_filled } ],
  last_entry: { signal_id, error, intended, at } | null,   // latest signal's live_trade_error / live_intended
  autoplace_armed, guard_armed,
  entry_window: { start, end, effective_end } | null,      // effective_end = min(end, 15:00)
  governor: { ... } }
```

`governor` is `live_deploy_governor.describe_live_caps`: the governor's own answer to "why
can't this trade now?", computed by the same precheck → measure → decide steps the entry
path runs. Read-only, never raises; anything unknowable is `null`, never `0`. The UI must
render it rather than re-derive enforcement.

```
governor: {
  applicable,                 // mode == "live"
  caps:  { lots, max_lots_per_day, max_concurrent, daily_loss_cap },
  next_entry_lots,            // auto_live.resolve_capped_lots against the account ceiling
  consumed: { lots_today, concurrent_now, realized_today, open_unrealized, day_pnl,
              loss_headroom, exposure_unknown, open_rows_prior_days,
              closed_today_null_realized } | null,
  verdict:         { allow, reason, pause } | null,   // deployment layer
  authorization:   { allow, reason } | null,          // is_deployment_live_allowed + session phase
  account_verdict: { allow, reason, pause } | null,
  account: { open_count, mtm, exposure_unknown,
             limits: { max_open_positions, daily_loss_limit, profit_lock_target, max_lots_per_order },
             stops:  { latched, engine_halted, engine_halt_reason } } | null,
  binding: { layer: "authorization"|"account"|"deployment", reason, pause } | null,  // first refusal, entry-path order
  error }                     // "describe_failed:<Type>" or null
```

Deployment-layer `verdict.reason` values: `ok`, `live_caps_missing`,
`invalid_daily_loss_cap`, `exposure_unknown`, `daily_loss_cap`, `lots_unmeasurable`,
`max_lots_per_day`, `max_concurrent`. The authorization verdict adds
`market_closed_today` / `before_market_open` when the gate passes but the session clock
says no session is running. `realized_pnl` and `day_pnl` are GROSS premium P&L (charges
are journalled separately on `live_trades` as `total_charges` / `net_realized_pnl`).

**`POST /deployments/{id}/live/caps`** — body (extra fields rejected), every field
optional but at least one required:
`{ lots?: int, max_lots_per_day?: int, max_concurrent?: int, daily_loss_cap?: number }`.

- Only an ACTIVE `mode=="live"` deployment (409 `deployment_not_live`). Broker readiness,
  the data gate and forward validation are NOT required, so it works with the broker down.
- Validated with `/live/enable`'s own validators (400 on bad values or
  `account_lot_ceiling_exceeded`; 503 if the account safety config is unreadable).
- Any loosening, even of one field, refuses the whole request and writes nothing: 409
  `caps_loosening_refused` with `loosened: [{field, current, requested}]`. Raise caps
  with Disable → re-Enable.
- Stored caps the governor already rejects → 409 `stored_caps_invalid`.
- Write is a compare-and-swap on `updated_at` + `mode` + `status` (409
  `deployment_changed_during_caps_update`). Applies to signals evaluated after the write;
  an entry already in flight is re-checked by the transmit fence (ARCHITECTURE.md §7,
  the gate chain).
- 200: `{ deployment_id, mode, status, live_paused, changed: [field...], caps: {...}, advisories: [{code, message}] }`.
  `risk.live.last_caps_change = {at, from, to}` is recorded. An advisory
  `already_at_<daily_loss_cap|max_lots_per_day|max_concurrent>` says the new cap is
  already reached.

**`POST /deployments/{id}/live/flatten`** — body optional, `{ hold?: bool = false }`
(strict bool; extra fields rejected).

- Squares this deployment's guard-registry positions through the same margin-safe path as
  `/live/stop`, but never changes `mode` or `status`. `hold=true` first sets
  `risk.live.paused` so the next signal cannot re-enter.
- Outside market hours: 409 `market_closed` with `phase`; nothing is sent.
- A contract also held by another registry entry is skipped, not squared; an entry
  already squaring is not re-sent.
- 200 (each `*_tsyms` is a list of Noren symbols):

  ```
  { deployment_id, exit_submitted_tsyms, already_flat_tsyms, cancel_confirmed_tsyms,
    flat_confirmation_pending_tsyms, deferred_tsyms, failed_tsyms, skipped_shared_tsyms,
    already_squaring_tsyms, unguarded_open_tsyms, complete, fill_confirmed: false,
    held, mode, status }
  ```

  `unguarded_open_tsyms` = OPEN journal rows the guard does not hold for this deployment
  (never squared automatically). `complete` is false while anything failed, was deferred,
  was skipped as shared, or is unguarded. `fill_confirmed` is always `false`: the guard
  finalizes after consecutive flat reads.

**`GET /deployments/{id}/timeline`** — query `date=YYYY-MM-DD` (IST; default today).
Returns `{ date, events: [{ ts, kind, label, detail, source }], gaps: [str] }`, ascending.
`kind` is one of `signal`, `refused`, `intended`, `state`, `entry`, `exit`, `order`,
`hold`, `disabled`, `caps`.
Sources: `signals` (by `candle_ts` in the IST day), `live_trades`, `live_orders`, and the
deployment's latest hold / disable / caps-change points. `gaps` always states what is not
recorded anywhere (skipped entries, halt/latch history, order-outcome times, overwritten
point events). Never 500s: a bad date or unreadable source returns 200 with an empty or
partial list and the reason in `gaps`.

## Signals ledger (`journals.py`)

Legacy manual signal endpoints were retired 2026-06-12 (deployments journal +
auto-trade their own signals). A signal's bar is stored as `candle_ts` (epoch-ms);
the API keeps `bar_ts` as its public name for that minute (sort key and output field).

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/signals` | Raw recent signal records (compat/debug). |
| GET | `/api/signals/enriched` | Trade-recommendation ledger: deployment signals joined with their paper trades. Filters `deployment_id`, `strategy_id`, `instrument`, `state`, `clean` (true = clean only, false = blocked only), `date_from`/`date_to` (IST, applied to `candle_ts`); `sort` (`bar_ts` default desc, `updated_at`, `confidence`, `instrument`, `state`); `skip`/`limit` ≤ 500; `format=csv` (columns include `blocked`, `paper_trade_id`, `trade_status`). |
| POST | `/api/signals/purge` | Delete journaled signals; never touches trades. Body `{ ids?, deployment_id?, older_than_days?, states?, blocked? }`. At least one of `ids` / `deployment_id` / `older_than_days` is required (400 otherwise); criteria AND together. `older_than_days` compares `updated_at`. `blocked: true` = only signals that failed the pre-trade filter, `false` = only clean ones (AUDITED now also holds clean signals retired after their session, so journal retention passes `blocked: true`). Returns `{ deleted }`. |

## Paper Trading (`journals.py`)

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/paper/account-config` | Read paper starting capital. |
| PUT | `/api/paper/account-config` | Set paper starting capital (`> 0`). |
| GET | `/api/paper/analytics` | Account analytics (equity curve, R-multiple, drawdown; open positions marked to the latest tick). |
| GET | `/api/paper/strategy-stats` | Per-strategy / per-deployment paper stats (+ per-deployment `drift`). |
| GET | `/api/paper/deployment-stats` | **new.** `deployment_id` (required) → day/week/month/year buckets of capital, P&L extremes, drawdown, peak deployed capital (`paper_analytics.deployment_period_stats`). |
| GET | `/api/paper/trades` | Paper-trade journal (rich filters; `format=csv`). |
| GET | `/api/paper/open-positions` | OPEN positions with unrealized P&L from the latest tick (lightweight, ~2s poll). |
| GET | `/api/paper/open-positions/stream` | **new.** SSE of the same payload on each Upstox tick (coalesced); no broker calls. |
| POST | `/api/paper/trades/purge` | Delete CLOSED paper trades only. |
| POST | `/api/paper/trades/{trade_id}/mark` | Update unrealized P&L; auto-close if the mark hits the stored stop/target. |
| POST | `/api/paper/trades/{trade_id}/close` | Close a trade (`{ exit_price, reason }`) and store realized P&L. |
| POST | `/api/paper/square-off` | Force-close all OPEN paper trades (idempotent; the same `square_off_open_paper_trades` sweep the 15:00 IST loop uses). |

## Live Broker — Flattrade connection + read-only books (`live_broker.py`)

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/flattrade/status` | Flattrade token connection status (never raises; no-token = `connected:false`). |
| GET | `/api/flattrade/auth/start` | Flattrade OAuth login URL (400 if creds unset). |
| GET | `/api/flattrade/auth/callback` | OAuth callback; resolves uid, saves the token, redirects to the frontend. |
| POST | `/api/flattrade/disconnect` | Delete the stored Flattrade token. |
| GET | `/api/live-broker/positions` | Broker net position book (400 if not connected). |
| GET | `/api/live-broker/marks` | **new.** Broker position book re-marked against the live Upstox tick: `{ positions, day_pnl, unrealized, realized, tick_keys, marked, count, broker_age_ms, broker_stale, broker_error, emitted_at_ms, source:"poll" }`. The broker book is read at most once per 15s through a shared cache; `refresh=true` forces a read. A stale book is never tick-marked; past the staleness bound it 400s. |
| GET | `/api/live-broker/marks/stream` | **new.** SSE of the same payload (`source:"stream"`), pushed on each tick (coalesced), 15s heartbeat. |
| GET | `/api/live-broker/call-meter` | **new.** Flattrade REST calls made by this process (the rate budget shared with the Flattrade MCP): `{ window_s, calls_in_window, calls_per_min, by_route_window, by_route_total, total }`. `reset=true` returns `{ reset:true, before }` and zeroes the window. |
| GET | `/api/live-broker/holdings` | **new.** Broker demat holdings (`prd`, default `C`): `{ holdings, count }`. 400 if not connected; a failed read raises rather than reading as "no holdings". |
| GET | `/api/live-broker/orders` | Broker order book. |
| GET | `/api/live-broker/trades` | Broker trade book (filled orders). |
| GET | `/api/live-broker/limits` | Account limits / margin. |
| GET | `/api/live-broker/margin-probe` | Read-only NRML margin readback for a prospective 1× BUY LMT leg (GetOrderMargin). |
| GET | `/api/live-broker/reconcile` | Fetch broker orders + positions → reconcile diff report. |
| GET | `/api/live-broker/blotter` | Deployment-attributed live blotter (`live_trades` joined to the broker position book by `noren_tsym`; degrades gracefully). |
| GET | `/api/live-broker/trade-stats` | **new.** Journal statistics over `live_trades` (no broker calls): `{ period_pnl, per_strategy, trade_count, closed_count }`. Each `per_strategy` row carries `open_unverified`, `open_carried`, `open_carried_oldest` so an OPEN journal row is never read as a live position. |
| GET | `/api/live-broker/trade-history` | **new.** Paginated raw `live_trades` (newest first; `limit` ≤ 500, `skip`, `status=OPEN\|CLOSED`): `{ items, count, total, skip, limit }`. OPEN rows are annotated with `open_state` (`verified`/`unverified` by guard mark freshness), `mark_age_s`, `carried`, `entry_day_ist`. `exit_price` / `realized_pnl` may be null on CLOSED rows (never fabricated). |
| GET | `/api/live-broker/symbol/resolve` | Preview Noren symbol resolution for a given option contract. |
| GET | `/api/live-broker/order-rules/{underlying}` | Exchange rules for the UI (products / order-types / freeze / tick / lot / expiry). |
| GET | `/api/live-broker/greeks` | Portfolio net Δ (₹/point) / net Θ (₹/day) over the positions the BROKER holds (read through the shared marks cache, no extra broker call). `book = { state: flat\|open\|unknown, open_count, guarded_count, unguarded, error, stale }`. Zeros only for a book that was read and is flat; unreadable or unpriced → `null` figures. |
| GET | `/api/live-broker/guard-status` | Software exit-guard state: `{ armed, mode, count, rehydrated_count, guarded: [{tsym, qty, entry_price, stop_level, target_level, peak, seen_filled, source}], health: {state, label, reason} }` (`armed` is always true, kept for compatibility; `source: "rehydrated"` = re-attached after a restart with default levels). `health.state ∈ not_running \| off_hours \| stalled \| blind \| idle \| watching` (`off_hours` = outside the options session, where the guard skips cycles by design), or `unknown` when the guard status itself could not be read. |
| GET | `/api/live-broker/preopen-readiness` | **new.** Latest stored 08:45 IST pre-open verdict: `{ verdict: {session_date, evaluated_at, trigger, ready, reason, blockers, warnings, live_deployment_count} \| null, today, is_today, days_ago, error }`. `verdict: null` = the check never ran (not "all clear"). Mongo only; never raises. |
| GET | `/api/live-broker/recovery-status` | **new.** Live-recovery latch for the current Flattrade token: `{ succeeded, token_fingerprint, last_attempt_at, last_result }` (`succeeded` once resume_pending + guard rehydrate + reboot reconcile + auto-square re-arm complete). |
| GET | `/api/live-broker/session-clock` | **new.** The server trading clock (`live/session_clock.describe_session`). See below. |
| GET | `/api/live-broker/atm-suggest` | Nearest-ATM strike + front expiry + its premium (live tick → last candle). |
| POST | `/api/live-broker/option-premium` | Current premium for a contract (live tick ≤120s → last `options_1m` candle → none); read-only, always 200. |

**`GET /live-broker/session-clock`** (also embedded as `session` in `/live-broker/arm-state`):

```
{ server_now_ms, now_ist, ist_date, is_trading_day, day_kind, holiday_label, calendar_verified,
  phase,                      // closed_day | pre_open | after_eod | after_cutoff | session
  market_open_ist, market_open_ms, entry_cutoff_ist, entry_cutoff_ms,
  eod_square_ist, eod_square_ms, derivatives_close_ist, cas_start_ist,
  next_event: { kind: market_open|entry_cutoff|eod_square|next_session_open, at_ms, date? } | null }
```

Every boundary is also an absolute epoch-ms so the browser counts down against the
server's instants. The entry cutoff comes from the gate's own function
(`mode.entry_cutoff_today_ist`), the EOD square from the guard's configured time
(`null` if unreadable), the trading day from the holiday-aware `nse_calendar`. On
failure: `{ error: "session_clock_failed:<Type>" }`.

## Live Broker — order gates + manual placement (`live_broker.py`)

The executor (`app/live/executor.py`) is the single real-order chokepoint. Manual
entries reach it through `/live-broker/order/place` (directly or via an approved
approval); deployed entries reach it from the evaluator (`auto_live` →
`executor.place_deployed_order`), not through any HTTP route. See the gate chain in
[ARCHITECTURE.md](./ARCHITECTURE.md) §7.

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/live-broker/order/dry-run` | Build an OrderIntent + run all safety checks WITHOUT placing. |
| POST | `/api/live-broker/order/preview` | Choke-point as a dry-run (exchange/tick/freeze/order-type checks, no placement). |
| POST | `/api/live-broker/order/approvals` | Validate a ticket and, if it passes, queue it for explicit approval (returns a one-shot token). The queue is in-memory (lost on restart) with a TTL. |
| GET | `/api/live-broker/order/approvals` | List pending approvals (token never exposed). |
| POST | `/api/live-broker/order/approvals/{approval_id}/approve` | Redeem the one-shot token → place the approved BUY entry via `/order/place` (reverts to pending on any non-placement). |
| POST | `/api/live-broker/order/approvals/{approval_id}/reject` | Decline a pending approval (never placeable afterwards). |
| POST | `/api/live-broker/order/place` | The manual ENTRY path — place one real 1-lot option BUY through all gates (requires `LIVE_TEST` + unconsumed single-shot). |
| POST | `/api/live-broker/order/square` | Manually square the open test position (exit-only) and revert to LIVE_OFFLINE. Deployed positions are exited by the guard, `/live/flatten`, `/live/stop` or the kill switch. |
| POST | `/api/live-broker/kill-switch` | Panic square-off of all open orders + positions (transmits), revert mode, record `kill_switch`. |
| GET | `/api/live-broker/test-session` | Manual test-session state (heartbeat, status, entry order; auto-detects a rejected/cancelled entry and reverts to LIVE_OFFLINE). No `deadline` / `remaining_secs`: the 10-minute timer was removed; the software guard's stop and the 15:00 EOD square protect the position. |

## Live Broker — mode, arm-state, safety, GTT/OCO, overall controls (`live_broker.py`)

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/live-broker/mode` | Current mode doc (`PAPER` / `LIVE_OFFLINE` / `LIVE_TEST`, single-shot state). |
| PUT | `/api/live-broker/mode` | Transition mode (`LIVE_TEST` needs `confirm=true` + connected broker + engine can-trade; `LIVE_ARMED` → 400). |
| GET | `/api/live-broker/arm-state` | The single "will a signal place a REAL order right now?" verdict (`compute_arm_state` over mode, `LIVE_AUTOPLACE_ARMED`, connected-and-not-expired session, and the count of ACTIVE live deployments that pass `is_deployment_live_allowed` inside a session phase), plus `session` (the session clock above). Never raises. |
| GET | `/api/live-broker/safety-config` | Current live-trading guardrails (daily loss limit, profit lock, max open, max lots/order). |
| PUT | `/api/live-broker/safety-config` | Update numeric guardrails. |
| POST | `/api/live-broker/safety-config/reset-latch` | Explicitly reset the broker stop-loss latch. |
| GET | `/api/live-broker/overall-settings` | Overall-controls config for a scope (basket SL/target/trailing/re-entry). |
| PUT | `/api/live-broker/overall-settings` | Persist a validated overall-controls config (fail-closed). |
| GET | `/api/live-broker/gtt` | List the broker GTT/OCO book (best-effort; empty list + note if not connected). |
| POST | `/api/live-broker/gtt` | Build (and, with `transmit=true`, transmit) a NRML-only GTT/OCO catastrophe backstop; preview otherwise. The automatic per-entry OCO is off by default (`LIVE_BROKER_OCO_ENABLED`). |
| DELETE | `/api/live-broker/gtt/{al_id}` | Cancel a GTT/OCO by alert id (`kind=gtt\|oco`). |

---

## Error conventions

| Code | Meaning |
|---|---|
| 400 | Bad request (invalid instrument, out-of-range params, no candles in window, `acknowledgment_required`, live guard not satisfied, expired option key to the wrong endpoint). Live-broker read routes turn broker failures into 400 rather than 500. |
| 403 | Forbidden (deleting a built-in strategy; `_diag` routes when not enabled). |
| 404 | Resource not found. |
| 409 | Conflict: id already exists, retire-before-delete, drift/retired guards, and the live CAS / state guards (`deployment_changed_during_*`, `deployment_not_live`, `caps_loosening_refused`, `stored_caps_invalid`, `market_closed`, `incomplete_market_data`). Structured 409s carry `detail: {code, message, ...}`. |
| 500 | Unexpected server error (rare; status and diagnostic routes are written to degrade instead). |
| 502 / 503 | Upstream/AI provider failure (502) · service unavailable — MongoDB down, AI authoring not configured, or the account safety config unreadable (503). |

Note: when a forward signal cannot land due to `E11000` on the unique partial
index `signals_deployment_bar_unique`, the evaluator does not bubble a 409 — it
logs the row as `outcome="skipped"`, `reason="already_journaled"` and advances
`last_evaluated_ts`.
