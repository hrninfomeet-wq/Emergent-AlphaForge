# API Reference

Updated: 2026-07-01

Every backend HTTP route, grouped by area. All routes are mounted under the
`/api` prefix (a single `APIRouter(prefix="/api")` in `backend/server.py`, which
includes seven sub-routers: `research`, `strategies_admin`, `warehouse`,
`journals`, `deployments`, `broker`, `live_broker`). JSON in / JSON out; CORS is
open in dev. IST throughout; NSE session 09:15–15:30 with a 15:00 square-off.

**Route count: 171** (source of truth: the `@api.<verb>(...)` decorators in
`backend/server.py` + `backend/app/routers/*.py`). This file is the map; for
request/response shapes read the route function and its Pydantic body model.

Related docs: [ARCHITECTURE.md](./ARCHITECTURE.md) (module map, collections,
gate chain) · [DEVELOPER_GUIDE.md](./DEVELOPER_GUIDE.md) · [USER_MANUAL.md](./USER_MANUAL.md)
· [STRATEGY_DEPLOYMENTS.md](./STRATEGY_DEPLOYMENTS.md) · the decoded broker API in
[Resources/flattrade-pi-api/INDEX.md](./Resources/flattrade-pi-api/INDEX.md).

---

## Health / dashboard / market header (`server.py`, `journals.py`, `broker.py`)

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/` | Root: `{ app, status, version }`. |
| GET | `/api/health` | DB ping: `{ db: "ok" }` or 503 when MongoDB is down. |
| GET | `/api/dashboard/summary` | Warehouse stats + strategy load count + backtest run count + latest backtest meta. |
| GET | `/api/market/header` | Persistent market-header snapshot (WS-first, REST-fallback per tile; failed tiles carry `status:"error"`; never returns tokens). |
| GET | `/api/market/header/stream` | Server-Sent Events feed of market-header snapshots. |

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

## Live candle roller + feed health (`broker.py`)

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/live-candles/status` | Tick→1m roller status: tick counts, active buckets, last error, running flag. |
| POST | `/api/live-candles/start` | Manually start the roller (no-op if running); clears the supervisor suppression flag. |
| POST | `/api/live-candles/stop` | Stop the roller and flush in-progress buckets. |
| GET | `/api/live-feed/health` | Truthful pipeline health (token → stream → roller → fresh `candles_1m`): is it delivering, or what's blocking it? |
| GET | `/api/live-exit-monitor/status` | Live paper-exit monitor status (tick-driven stop/target exits). |

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
| POST | `/api/backtest/option-preflight` | Would-pair option-coverage report for an option-enabled config; `ingest_missing=1` submits a background fetch. |
| GET | `/api/backtest/runs` | Recent runs (lightweight; excludes trades/equity/walkforward). |
| GET | `/api/backtest/runs/{run_id}` | Full run doc. |
| DELETE | `/api/backtest/runs/{run_id}` | Remove a run. |

## Optimizer (`research.py`)

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/optimize/start` | Launch a search (`method ∈ bayesian|grid|genetic`; `evaluation_mode ∈ spot|option_rerank`; survival guards). Returns `{ job_id, status:"queued" }`. |
| POST | `/api/optimize/wfo` | Honest walk-forward optimization (rolling/anchored windows, stitched OOS, optional `option_aware`). Returns `{ job_id, status:"queued", kind:"wfo" }`. |
| GET | `/api/optimize/jobs` | Recent jobs (lightweight projection). |
| GET | `/api/optimize/jobs/{job_id}` | Full job doc (`queued→running→analyzing→done|cancelled|paused|interrupted|failed`). |
| DELETE | `/api/optimize/jobs/{job_id}` | Remove a job. |
| POST | `/api/optimize/jobs/{job_id}/cancel` | Cancel at the next trial boundary (best-so-far still saved). |
| POST | `/api/optimize/jobs/{job_id}/pause` | Flush the compact trial log + best-so-far, then pause. |
| POST | `/api/optimize/jobs/{job_id}/resume` | Rehydrate a paused/interrupted/failed job and continue. |
| POST | `/api/optimize/apply-as-preset/{job_id}` | Save the job's best params (+ any option execution policy) as a Preset. |

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
| POST | `/api/strategies/{strategy_id}/retire` | Retire a strategy (squares off its deployments; blocks new deploys). |
| POST | `/api/strategies/{strategy_id}/un-retire` | Clear the retired flag. |
| DELETE | `/api/strategies/{strategy_id}` | Delete a custom plugin file (must be retired + no non-archived deployments; built-ins rejected 403). |
| POST | `/api/strategies/reload` | Reload the plugin registry from disk. |
| POST | `/api/strategies/author/compile` | Validate + compile a spec to source WITHOUT installing (returns errors for the wizard). |
| POST | `/api/strategies/author/install` | Compile + write plugin file + reload + record provenance (409 on id clash unless `overwrite`). |
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
| POST | `/api/deployments` | Create a deployment (freezes params + `strategy_source_sha`; runs the quality gate; auto-paper defaults). |
| GET | `/api/deployments/preflight` | Pre-flight: spot coverage, upcoming expiries, active vs expired contracts, Upstox token, structural-break notes. |
| GET | `/api/deployments/quality` | Quality evaluation vs the source (severity-colored warnings). |
| GET | `/api/deployments/readiness` | Deployment-readiness evidence (latest WFO + option-rupee proof; informational, never blocks). |
| GET | `/api/deployments/metrics` | Session-gated forward metrics across deployments (`include_ineligible` for low-sample). |
| GET | `/api/deployments/{deployment_id}/metrics` | Forward metrics for one deployment (+ session-completeness summary). |
| GET | `/api/deployments/overview` | Command-center roll-up: per-deployment today/lifetime + account totals. |
| GET | `/api/deployments/{deployment_id}` | Single deployment doc. |
| POST | `/api/deployments/{deployment_id}/pause` | Pause a deployment. |
| POST | `/api/deployments/{deployment_id}/resume` | Resume a paused deployment (best-effort option-stream realign). |
| POST | `/api/deployments/stop-all` | Stop everything: square off all open paper, pause every ACTIVE deployment, and disarm + flatten every armed-live deployment. |
| POST | `/api/deployments/{deployment_id}/stop` | Square off THIS deployment's open paper positions, then pause. |
| POST | `/api/deployments/{deployment_id}/archive` | Undeploy (`purge=1` also deletes its signals + CLOSED trades; OPEN kept for square-off). |
| GET | `/api/deployments/{deployment_id}/signals` | Signals linked to this deployment. |
| POST | `/api/deployments/{deployment_id}/evaluate-on-close` | Run the 1m_close evaluator once for this deployment. |
| POST | `/api/deployments/evaluate-active` | Run the evaluator across every ACTIVE deployment. |
| POST | `/api/deployments/{deployment_id}/repin-source` | Re-pin to the strategy's current source after a drift pause (recomputes SHA, resumes if drift-paused). |

### Live-arming (real-money) sub-routes

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/deployments/{deployment_id}/live/enable` | Switch to LIVE mode — real-money auto-placing (v0.56.0: no session ARM; persists until disabled). Guards: ACTIVE, not retired, not drift-paused, broker connected, engine can-trade, caps + `daily_loss_cap` required, `confirm=true` StrictBool. |
| POST | `/api/deployments/{deployment_id}/live/disable` | Revert to paper mode (does NOT flatten open positions). |
| POST | `/api/deployments/{deployment_id}/live/stop` | Flatten THIS deployment's open live positions, then disarm (user-initiated exit — transmits directly). |
| GET | `/api/deployments/live/status` | Batched live status for many deployments in one call (`ids=` comma-separated). |
| GET | `/api/deployments/{deployment_id}/live/status` | One deployment's live arm state, caps, today's counters, open live positions, transmit gates. |

## Signals ledger (`journals.py`)

Legacy manual signal endpoints were retired 2026-06-12 (deployments journal +
auto-trade their own signals). Remaining routes:

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/signals` | Raw recent signal records (compat/debug). |
| GET | `/api/signals/enriched` | Trade-recommendation ledger: deployment signals joined with their paper trades (rich filters; `format=csv`). |
| POST | `/api/signals/purge` | Delete matching signals (`ids`/`deployment_id`/`older_than_days`/`states`); never touches trades. |

## Paper Trading (`journals.py`)

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/paper/account-config` | Read paper starting capital. |
| PUT | `/api/paper/account-config` | Set paper starting capital (`> 0`). |
| GET | `/api/paper/analytics` | Account analytics (equity curve, R-multiple, drawdown; open positions marked to the latest tick). |
| GET | `/api/paper/strategy-stats` | Per-strategy / per-deployment paper stats. |
| GET | `/api/paper/trades` | Paper-trade journal (rich filters; `format=csv`). |
| GET | `/api/paper/open-positions` | OPEN positions with unrealized P&L from the latest tick (lightweight, ~2s poll). |
| POST | `/api/paper/trades/purge` | Delete CLOSED paper trades only. |
| POST | `/api/paper/trades/{trade_id}/mark` | Update unrealized P&L; auto-close if the mark hits the stored stop/target. |
| POST | `/api/paper/trades/{trade_id}/close` | Close a trade (`{ exit_price, reason }`) and store realized P&L. |
| POST | `/api/paper/square-off` | Force-close all OPEN paper trades (idempotent; used by the 15:00 IST loop + manual button). |

## Live Broker — Flattrade connection + read-only books (`live_broker.py`)

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/flattrade/status` | Flattrade token connection status (never raises; no-token = `connected:false`). |
| GET | `/api/flattrade/auth/start` | Flattrade OAuth login URL (400 if creds unset). |
| GET | `/api/flattrade/auth/callback` | OAuth callback; resolves uid, saves the token, redirects to the frontend. |
| POST | `/api/flattrade/disconnect` | Delete the stored Flattrade token. |
| GET | `/api/live-broker/positions` | Broker net position book (400 if not connected). |
| GET | `/api/live-broker/orders` | Broker order book. |
| GET | `/api/live-broker/trades` | Broker trade book (filled orders). |
| GET | `/api/live-broker/limits` | Account limits / margin. |
| GET | `/api/live-broker/margin-probe` | Read-only NRML margin readback for a prospective 1× BUY LMT leg (GetOrderMargin). |
| GET | `/api/live-broker/reconcile` | Fetch broker orders + positions → reconcile diff report. |
| GET | `/api/live-broker/blotter` | Deployment-attributed live blotter (`live_trades` joined to the broker position book; degrades gracefully). |
| GET | `/api/live-broker/symbol/resolve` | Preview Noren symbol resolution for a given option contract. |
| GET | `/api/live-broker/order-rules/{underlying}` | Exchange rules for the UI (products / order-types / freeze / tick / lot / expiry). |
| GET | `/api/live-broker/greeks` | Portfolio Greeks (net Δ / net Θ) — read-only, server-side BS IV-from-premium. |
| GET | `/api/live-broker/guard-status` | Software exit-guard state (armed vs dry-run + per-position stop/target/peak/fill). |
| GET | `/api/live-broker/atm-suggest` | Nearest-ATM strike + front expiry + its premium (live tick → last candle). |
| POST | `/api/live-broker/option-premium` | Current premium for a contract (live tick ≤120s → last `options_1m` candle → none); read-only, always 200. |

## Live Broker — order gates + placement chokepoint (`live_broker.py`)

The executor is the SINGLE real-order chokepoint; entries reach the broker ONLY
via `/live-broker/order/place`. See the gate chain in
[ARCHITECTURE.md](./ARCHITECTURE.md) and the safety model in
[DEVELOPER_GUIDE.md](./DEVELOPER_GUIDE.md).

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/live-broker/order/dry-run` | Build an OrderIntent + run all safety checks WITHOUT placing. |
| POST | `/api/live-broker/order/preview` | Choke-point as a dry-run (exchange/tick/freeze/order-type checks, no placement). |
| POST | `/api/live-broker/order/approvals` | Validate a ticket and, if it passes, queue it for explicit approval (returns a one-shot token). |
| GET | `/api/live-broker/order/approvals` | List pending approvals (token never exposed). |
| POST | `/api/live-broker/order/approvals/{approval_id}/approve` | Redeem the one-shot token → place the approved BUY entry (reverts to pending on any non-placement). |
| POST | `/api/live-broker/order/approvals/{approval_id}/reject` | Decline a pending approval (never placeable afterwards). |
| POST | `/api/live-broker/order/place` | **The only ENTRY chokepoint** — place one real option order through all gates (requires LIVE_TEST + unconsumed single-shot). |
| POST | `/api/live-broker/order/square` | Manually square the open test position (exit-only) and revert to LIVE_OFFLINE. |
| POST | `/api/live-broker/kill-switch` | Panic square-off of all open orders + positions (L3 transmits), revert mode, record `kill_switch`. |
| GET | `/api/live-broker/test-session` | Test-session state (deadline, remaining_secs, heartbeat, status; auto-detects a rejected entry). |

## Live Broker — mode, arm-state, safety, GTT/OCO, overall controls (`live_broker.py`)

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/live-broker/mode` | Current mode doc (`PAPER` / `LIVE_OFFLINE` / `LIVE_TEST`, single-shot state). |
| PUT | `/api/live-broker/mode` | Transition mode (LIVE_TEST needs `confirm=true` + connected broker + engine can-trade). |
| GET | `/api/live-broker/arm-state` | The single "will a signal place a REAL order right now?" verdict (live-mode deployment count + `LIVE_AUTOPLACE_ARMED` + connectivity; v0.56.0). |
| GET | `/api/live-broker/safety-config` | Current live-trading guardrails (daily loss limit, profit lock, max open, max lots/order). |
| PUT | `/api/live-broker/safety-config` | Update numeric guardrails. |
| POST | `/api/live-broker/safety-config/reset-latch` | Explicitly reset the broker stop-loss latch. |
| GET | `/api/live-broker/overall-settings` | Overall-controls config for a scope (basket SL/target/trailing/re-entry). |
| PUT | `/api/live-broker/overall-settings` | Persist a validated overall-controls config (fail-closed). |
| GET | `/api/live-broker/gtt` | List the broker GTT/OCO book (best-effort; empty list + note if not connected). |
| POST | `/api/live-broker/gtt` | Build (and, with `transmit=true`, transmit) a NRML-only GTT/OCO catastrophe backstop; preview otherwise. |
| DELETE | `/api/live-broker/gtt/{al_id}` | Cancel a GTT/OCO by alert id (`kind=gtt|oco`). |

---

## Error conventions

| Code | Meaning |
|---|---|
| 400 | Bad request (invalid instrument, out-of-range params, no candles in window, `acknowledgment_required`, live guard not satisfied, expired option key to the wrong endpoint). |
| 403 | Forbidden (e.g. deleting a built-in strategy). |
| 404 | Resource not found. |
| 409 | Conflict (id already exists, retire-before-delete, drift/retired guards on arming, duplicate-source edge cases). |
| 500 | Unexpected server error (rare; live-broker read routes deliberately degrade to 400/best-effort instead). |
| 502 / 503 | Upstream/AI provider failure (502) · service unavailable — MongoDB down or AI authoring not configured (503). |

Note: when a forward signal cannot land due to `E11000` on the unique partial
index `signals_deployment_bar_unique`, the evaluator does not bubble a 409 — it
logs the row as `outcome="skipped"`, `reason="already_journaled"` and advances
`last_evaluated_ts`.
