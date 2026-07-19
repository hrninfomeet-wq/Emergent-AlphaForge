# Architecture

Technical reference for AlphaForge Trading Lab. For a task-oriented onboarding
walkthrough (run/build/test, safety model, research→deploy flow, gotchas), read
[DEVELOPER_GUIDE.md](./DEVELOPER_GUIDE.md) first — this file is the deep
"where does X live and how does data move" companion.

Updated: 2026-07-19 · Verified against local `main` at `f67f463` (includes the live-safety-reconcile
hardening, the premium-momentum family incl. Phase 5B multi-leg, the v0.55.1 option-leg index remap,
and v0.55.2 Flattrade-MCP session sharing — check `git log` if this is more than a couple of weeks
old, doc passes have lagged commits before).

---

## 1. Purpose

AlphaForge is a **local-first** research and forward-testing terminal for Indian
index options (NIFTY / BANKNIFTY / SENSEX). It warehouses 1-minute market data on
disk, audits coverage, runs spot and paired-option backtests, optimizes parameters
(including honest walk-forward OOS), runs strategies forward against live 1-minute
closes, paper-trades clean signals at real option premiums, and — behind a
multi-gate chokepoint and explicit env arming — can place **real** broker orders
through Flattrade.

**Two brokers, two roles:**
- **Upstox** — market **data** (historical REST + V3 WebSocket ticks). Read-only.
- **Flattrade (Noren OMS)** — live **execution** (the only path that can place a
  real order). Requires static IP + daily OAuth; limit / SL-limit only.

---

## 2. Stack & Topology

| Layer | Technology | Role |
|---|---|---|
| Frontend | React (CRA + craco), Tailwind, shadcn/ui, TradingView Lightweight Charts | Trading terminal UI |
| Backend | FastAPI, Pydantic, pandas, NumPy, Motor (async MongoDB) | API, indicators, strategy execution, evaluators, live executor |
| Database | MongoDB 7 | Candles, contracts, audits, runs, presets, deployments, signals, paper/live trades |
| Data feed | Upstox REST + V3 WebSocket (protobuf) | Historical candles, quotes, live ticks |
| Broker | Flattrade PiConnect / Noren OMS | Real order execution (offline-first) |
| Runtime | Docker Compose | mongo + backend + frontend |
| Optimization | Optuna (TPE / CMA-ES) | Bayesian / Genetic / Grid search |
| AI authoring | Anthropic + Gemini (multi-provider) | Strategy authoring wizard |

### Containers & ports (`docker-compose.yml`)

```
┌─────────────┐    :3000    ┌──────────────┐   /api    ┌──────────────┐
│  frontend   │ ──────────► │   backend    │ ────────► │    mongo     │
│ (React/CRA) │             │  (FastAPI)   │  motor    │  (mongo:7)   │
│  :3000      │             │  :8001       │           │  :27017      │
└─────────────┘             └──────┬───────┘           └──────────────┘
                                   │
              ┌────────────────────┼─────────────────────┐
              ▼                    ▼                      ▼
       Upstox REST/WS        Flattrade Noren        yfinance fallback
       (market data)         (live execution)       (spot ingest)
```

- All backend routes are served under the **`/api`** prefix (parent
  `APIRouter(prefix="/api")` in `backend/server.py`; per-domain routers add the
  full literal path).
- `backend/.env` supplies `MONGO_URL`, `DB_NAME=alphaforge`, `CORS_ORIGINS`, and
  the offline-first live-execution env gates (§6). `FLATTRADE_MCP_SESSION_DIR`
  (set in `docker-compose.yml`) enables the Flattrade-MCP session sync — unset ⇒
  the sync is a no-op; optional `FLATTRADE_MCP_SESSION_TEMPLATE` overrides the
  session payload shape.
- The frontend is built with `REACT_APP_BACKEND_URL=http://localhost:8001`.
- Two volume mounts survive rebuild: the strategy **plugins** dir
  (`./backend/app/strategies/plugins`) is bind-mounted so drop-in `.py`
  strategies persist, and the **host `.flattrade` dir** →`/host-flattrade` so the
  backend can write the official Flattrade MCP binary's `session.json` (see
  `docs/flattrade-mcp-integration.md`; the path is machine-specific by design —
  this compose file is already single-machine). Mongo data persists in the
  `mongo_data` named volume.
- **Rebuild/run:** `docker compose up -d --build backend frontend`.

---

## 3. Backend Module Map

The app factory is `backend/server.py` (thin — ~240 lines): FastAPI app, CORS,
`startup`/`shutdown` hooks, and the background scheduler loops. Request models
live in `app/schemas.py`; shared singletons/helpers in `app/runtime.py`; routes in
`app/routers/*`; domain logic in `app/*`.

### Routers (`app/routers/`, all mounted under `/api`)

| Router | Surface |
|---|---|
| `research.py` | strategies list, warehouse ingest, backtest run/list, optimizer + WFO, presets, profiles, volatility audit |
| `warehouse.py` | Upstox OAuth/stream, data-hygiene plan/execute, coverage/audit, OHLC resample, holiday calendar, live-candle roller, option contracts/candles |
| `journals.py` | signals + paper-trades lifecycle (approve/skip/mark/close/square-off) |
| `deployments.py` | forward-test deployments (create/pause/resume/archive), preflight, quality, metrics, **live-arm** toggle |
| `strategies_admin.py` | strategy library lifecycle (retire/delete) + AI authoring wizard endpoints |
| `broker.py` | Flattrade OAuth/session, symbol resolution helpers |
| `live_broker.py` | **live execution surface** — positions/orders/limits, dry-run + preview, approvals, mode gate, arm-state, guard-status, GTT/OCO book, square, Greeks, kill-switch |

### Data warehouse (`app/`)

| Module | Responsibility |
|---|---|
| `warehouse.py` | Index candle persistence, coverage, holiday-aware audit, clear |
| `completeness.py` | **The one definition of option completeness: the daily ATM band.** Pure functions — a day is option-complete when every strike the spot range touched (rounded + padded) has stored CE+PE candles at the resolved expiry |
| `data_hygiene.py` | Diffs desired scope vs stored warehouse; emits a dependency-ordered plan (spot → contracts → option candles). Index-friendly aggregations (no `$lookup`) so the plan runs in ~6s |
| `nse_calendar.py` | Hand-curated NSE/BSE 2024–2026 holidays + Budget Saturdays + shifted-expiry days; `trading_days_in_range` / `is_market_holiday` / `calendar_for_year` |
| `warehouse_autoupdate.py` | Guarded plan→execute catch-up on startup, OAuth-connect, and a daily 18:00 IST timer |
| `warehouse_lookup.py` | Point-in-time spot + derived ATM + nearest-expiry CE/PE lookup (local reads only) |
| `warehouse_ohlc.py` | Server-side 1m→5m/15m/1h/1d resample on IST buckets + intraday gap detection |
| `option_*` (`_contract_store`, `_candles`, `_coverage`, `_coverage_cache`, `_data_audit`, `_data_planner`, `_plan_response`, `_warehouse_jobs`) | Option contract metadata, candle normalization, coverage summaries + precomputed cache, preview-first fetch planner, background fetch jobs |
| `options_universe.py` / `instruments.py` | ATM rounding, strike step, lot/expiry metadata per index |
| `upstox_client.py` / `upstox_stream.py` / `upstox_index_ingest.py` | OAuth + REST historical; V3 WebSocket (protobuf decode, sanitized ticks); background index ingest |
| `live_candle_roller.py` | Aggregates WS ticks into per-minute `candles_1m`; drops non-trading-day / off-session ticks |
| `live_option_universe.py` / `market_header.py` | Nearest-expiry ATM option subscription keys; normalized market-header quote |
| `vix.py` / `yfinance_source.py` / `chunking.py` | India VIX ingest + as-of join; yfinance spot fallback; chunk-size guidance |

### Research engine (`app/`)

| Module | Responsibility |
|---|---|
| `indicators.py` / `indicator_groups.py` | Vectorized, param-driven indicators (incl. vectorized FVG) |
| `regime.py` / `scenario_classifier.py` / `scenarios.py` | Regime detection (ADX/Choppiness/ATR); scenario-adaptive routing |
| `market_context.py` / `context_signals.py` / `cpr.py` | Regime/ToD/DTE/VIX bucket tagging; S/R, round levels, divergence scores; CPR levels |
| `backtest.py` | Strategy execution, metrics, statistical significance (materialized-records hot loop) |
| `option_backtest.py` | Paired INDEX+OPTION leg simulation (`simulate_paired_option_trades`); spot_exit / option_levels exit modes. Emits `index_trade_id` = the enumerate position **within the list it was handed**; making that a FULL-list position is the caller's duty (`runtime.py::_run_paired_option_backtest` remaps after a DTE filter — v0.55.1) |
| `exit_engine.py` / `exit_controls.py` / `exit_controls_level.py` / `execution_policy.py` | Shared intrabar exit; trailing/breakeven/daily-cap overlay; execution policy |
| `costs.py` / `option_costs.py` / `slippage.py` / `portfolio.py` | Indian intraday cost models (spot points + rupee option); slippage config; premium-at-risk sizing + rupee equity |
| `dte.py` / `volatility.py` / `vol_seasonality.py` | DTE filter; post-hoc realized-vol detector; vol seasonality |
| `optimizer.py` | Optuna TPE / Grid / CMA-ES; two-stage option re-rank; pause/resume/crash-resume; robustness/importance/heatmap |
| `wfo.py` / `walkforward.py` | Honest walk-forward (`kind="wfo"`): per-window re-optimization on train, OOS scoring on unseen test slices |
| `survival.py` / `survival_validate.py` / `rerank_select.py` / `early_stop.py` / `parallel_eval.py` | Survival gate; option re-rank selection; early stop; opt-in parallel trial workers |
| `strategies/` | `base.py` (registry + plugin loader), `adaptive_base.py`, `scenario_routing_base.py`, `session_features.py`, and the drop-in `plugins/` dir (incl. `plugins/premium_momentum.py` — inert `evaluate()`; the real logic lives in the evaluator branch below) |
| `ai/` | Multi-provider authoring: `llm_client.py`, `_anthropic.py`, `_gemini.py`, `spec_schema.py`, `compiler.py`, `capability.py`, `authoring_agent.py`, `py_author.py` + `py_sandbox.py` (guarded full-Python tier), `grounding.py` |
| `premium_momentum.py` / `premium_momentum_backtest.py` / `premium_momentum_tuner.py` | Time-locked-strike, premium-native-trigger strategy: pure walk/trail/cost helpers, the option-native self-contained backtest sim, and the costs-mandatory chronological-train/OOS-report tuner |

### Forward testing (paper) (`app/`)

| Module | Responsibility |
|---|---|
| `strategy_deployments.py` / `strategy_source_hash.py` | Deployment doc builder + validation + source resolution; SHA-256 plugin pin for drift detection |
| `deployment_preflight.py` / `deployment_quality.py` | Coverage/expiry/token preflight; 5 quality checks with ack-on-warning |
| `deployment_evaluator.py` | 1m_close evaluator + scheduler: time-of-day blocks, expiry cutoff, drift auto-pause, kill-switch checks, `risk_hints` capture, auto-paper hook |
| `deployment_kill_switch.py` | Per-deployment kill switches (consecutive-loss / daily-loss → PAUSE; max-open → soft BLOCK) |
| `paper_auto.py` / `paper_trading.py` / `paper_open_positions.py` / `paper_squareoff.py` / `paper_analytics.py` | Auto paper trading (premium resolution: live tick → fresh candle → refuse; never spot), per-minute marker, 15:00 IST square-off, R-multiple/blotter analytics |
| `forward_metrics.py` / `signal_lifecycle.py` / `preset_execution.py` | Session-gated deployment metrics; lifecycle state machine + audit events; preset replay |
| `premium_momentum_live.py` | Per-bar session state machine for `premium_momentum` (`pre_reference → lock+ref-capture → monitoring → triggered/done`); called from a dedicated branch in `deployment_evaluator.py`, not the generic `strategy.evaluate()` path. Since 5B also the both-legs engine (`leg_mode: "both"` — CE+PE independent primaries + lazy-leg lock pickup) |
| `premium_lock_store.py` | `premium_locks` collection accessor — create-once/duplicate-key-adopt lock, atomic trigger latch, entered/done state transitions, recovery source. 5B adds per-leg primitives (`pce/ppe/lce/lpe` field groups: `latch_trigger_leg`/`unlatch_trigger_leg`/`mark_entered_leg`/`mark_leg_exited`/`set_lazy_armed`/`legs_unresolved`) + the atomic fire-once `mark_day_stop` |
| `premium_pin.py` | `premium_pin_keys()` — today's locked option keys, unioned (cap-exempt) into every option-stream subscription rebuild so a locked strike never drops off the tick feed |

### Live execution (Flattrade) (`app/live/`)

The single-real-order chokepoint and its safety scaffolding. See §6 for the gate chain.

| Module | Responsibility |
|---|---|
| `executor.py` | **The SOLE `place_order` chokepoint** — `place_live_test_order` (manual single-shot) + `place_deployed_order` (armed deployment). No other module calls `client.place_order` for entry |
| `mode.py` | Mode gate (L3.1): `PAPER` / `LIVE_OFFLINE` / `LIVE_TEST` / `LIVE_ARMED`; `is_live_order_allowed` is fail-closed; `is_deployment_live_allowed` per-deployment arm predicate |
| `arm_state.py` | Pure `compute_arm_state` — collapses mode + per-deployment arm + the two env gates + connectivity into ONE verdict (`SAFE` / `DRY_RUN` / `LIVE`) |
| `safety.py` | Pure fail-closed checks: fat-finger cap (default-deny), price band, jdata validation (limit/SL-LMT only), `RateThrottle` (SEBI <10/s, cancels never throttled) |
| `margin.py` | Local `margin_verdict` + broker `GetOrderMargin` pre-trade gate (`broker_margin_verdict`) |
| `order_builder.py` / `broker_protocol.py` / `order_sm.py` | Server-side intent build (tick rounding, marketable buffer); `OrderIntent` + allowed prctyp/prd/ret; order state machine |
| `gtt.py` / `oco_levels.py` | **PC-down catastrophe backstop** — NRML-only resting GTT / OCO builders (block no margin; survive a dead PC); catastrophe band strictly wider than the software guard's stop |
| `kill_switch.py` | Account-level guardrails + `plan_squareoff` (pure) + `panic_squareoff` (executor, never raises) |
| `exit_claims.py` | Per-tsym asyncio-lock claim registry (TTL) — serializes the guard/kill-switch/manual square paths against each other so two exit paths can never double-sell the same position |
| `live_position_guard.py` / `live_sl_monitor.py` / `live_exit_monitor.py` / `auto_square.py` | In-process software SL guard (reads broker position book; transmits only when `LIVE_GUARD_ARMED=1`; `live_sl_monitor` trail modes incl. `stepped_xy` for premium-momentum); square execution (`auto_square` — no resting manual timer; EOD 15:00 IST is the sole time-based backstop for a manual position, see its module docstring) |
| `close_loop.py` / `reboot_reconcile.py` / `reconcile.py` | Write realized P&L + CLOSED back to `live_trades` on a real square; reboot reconciliation (empty position-book = UNKNOWN, never false-close) |
| `flattrade_client.py` / `flattrade_token.py` / `flattrade_symbol.py` / `mock_noren.py` | Real Noren client + daily OAuth + symbol resolve; `MockNoren` for tests |
| `mcp_session_sync.py` | One-way mirror of the fresh jKey into the official Flattrade MCP binary's `~/.flattrade/session.json` after each OAuth (superset payload, atomic write, skip-if-unchanged, never raises). AlphaForge stays the sole OAuth owner; the MCP never logs in itself. Gated by `FLATTRADE_MCP_SESSION_DIR`; recovery via `backend/scripts/resync_mcp_session.py` |
| `greeks.py` / `portfolio_greeks.py` / `option_premium.py` | Server-side Black-Scholes IV-from-premium + Greeks |
| `session_store.py` / `arm_state` stores / `overall_settings_store.py` / `approval_store.py` / `idempotency.py` | Mode/session/settings/approval persistence; client-order-id idempotency |
| `engine.py` (`app/live/`) / `auto_live.py` (`app/`) | `can_trade()` engine gate; the deployed auto-place entrypoint (env-gated transmit boundary) |

---

## 4. Frontend Structure (`frontend/src/`)

### Entry & shared libs

| File | Responsibility |
|---|---|
| `App.js` | Router (`react-router-dom`), theme provider, `JobsProvider` (global background-job tracker), toaster |
| `lib/theme.jsx` | System / Black / White theme state |
| `lib/jobs.jsx` | Global job tracker above the router; persists active run IDs to `localStorage` so ingest/fetch/hygiene progress survives navigation |
| `lib/api.js` | Axios wrapper for `/api/*` |
| `lib/time.js` / `lib/fmt.js` / `lib/utils.js` | IST time helpers, formatting, misc utils |
| `lib/backtestMetrics.js` / `paperAgg.js` / `exitReason.js` / `deploymentLiveness.js` | Client-side metric derivation |
| `lib/exports.js` / `optExports.js` | CSV/JSON export helpers |
| `index.css` | Design tokens (CSS variables) for both themes — no per-panel hex |

### Pages (`src/pages/`) → routes

| Route | Page | Purpose |
|---|---|---|
| `/` | `Dashboard.jsx` | Status cards |
| `/warehouse` | `DataWarehouse.jsx` | Connection, Data Hygiene, Index+Option data, Verify/Audit, lookup, chart |
| `/backtest` | `BacktestLab.jsx` | Spot + paired-option backtests + run journal |
| `/optimizer` | `Optimizer.jsx` | Optuna / Grid / Genetic + WFO workflow |
| `/strategies` | `StrategyLibrary.jsx` | Built-in + plugin browser, lifecycle, AI authoring |
| `/presets` | `SavedPresets.jsx` | Saved strategy configurations |
| `/checklist` | `PreTradeChecklist.jsx` | Pre-trade checklist profiles |
| `/journal` | `SignalJournal.jsx` | Deployment signal audit trail |
| `/paper` | `PaperTrading.jsx` | Paper trade journal + analytics |
| `/live` | `LiveSignals.jsx` | Pending-approval + deployment form (preflight/quality) |
| `/live-trading` | `LiveTrading.jsx` | Flattrade live dashboard (execution strip, blotter, GTT book, guard, Greeks, order ticket) |

### Components (`src/components/`)

Grouped subfolders: `live/` (execution UI — `LiveDashboard`, `ExecutionStateStrip`,
`LiveBlotter`, `GttBook`, `GuardPanel`, `GreeksCard`, `PositionMonitor`,
`LiveOrderTicket`, `DeployToLivePanel`, `FeedHealthBanner`, `LiveDataProvider` —
the single polling consolidation point), `warehouse/`, `backtest/`, `paper/`,
`strategy/`, `journal/`, `charts/`, and `ui/` (shadcn primitives). Top-level shared
components: `Layout.jsx` (sidebar, theme, token countdown, active-jobs indicator),
`MarketHeader.jsx`, `DataHygienePanel.jsx`, `WarehouseChart.jsx`,
`HolidayCalendarDialog.jsx`, `TrustScorecard.jsx`, and metric/badge widgets.

---

## 5. MongoDB Collections

Collection names verified against `app/db.py` (`ensure_indexes`) and code accessors.

### Market data & warehouse

| Collection | Purpose |
|---|---|
| `candles_1m` | Index 1-minute OHLCV (spot + INDIAVIX). Unique `(instrument, ts)` |
| `options_1m` | Option premium 1-minute OHLCV + OI. Unique `(instrument_key, ts)` + `(underlying, expiry_date, strike, side, ts)` |
| `option_contracts` | Option metadata: instrument_key, expiry_date, strike, side, lot_size |
| `option_coverage_cache` | Precomputed per-underlying coverage summary (fast page loads) |
| `option_known_empty` | Memoized (contract, date) pairs the broker returned empty for (avoids re-fetch) |
| `integrity_hashes` | Per-day index candle counts + hashes. Unique `(instrument, date)` |
| `warehouse_runs` | Ingest / fetch audit log (spot, contracts, options, hygiene) |
| `data_hygiene_latest` | Latest hygiene plan/state snapshot |
| `ticks` | Sanitized live tick snapshots from the WS stream |
| `chain_snapshots` | Option-chain snapshots |
| `upstox_tokens` | Encrypted Upstox OAuth tokens |

### Research

| Collection | Purpose |
|---|---|
| `backtest_runs` | Backtest configs, trades, metrics, option results |
| `optimization_jobs` | Optimizer + WFO jobs + best results; statuses incl. `paused` / `interrupted`; carries `evaluation_mode`, `rerank`, transient `trial_log`, `wfo_windows` |
| `presets` | Saved strategy configurations (unique `name`) |
| `pretrade_profiles` | Conservative / Balanced / Aggressive + custom (unique `name`) |
| `strategy_lifecycle` | Per-strategy retire/delete lifecycle (unique `strategy_id`) |
| `app_settings` | Misc app settings |

### Forward test (paper) & signals

| Collection | Purpose |
|---|---|
| `strategy_deployments` | Forward-test deployment definitions (incl. `risk.live` arm block) |
| `signals` | Lifecycle state, blockers, audit events, `risk_hints`, `paper_trade_claim`. Unique partial index `signals_deployment_bar_unique` over `(deployment_id, candle_ts)` |
| `paper_trades` | Paper fills at option premium, MTM, realized/unrealized P&L, `risk`, `spot_exit`, source flag |
| `premium_locks` | `premium_momentum` per-session strike lock + ref-premium capture + trigger-latch state; in 5B both-mode also the per-leg (`pce/ppe/lce/lpe`) trigger/entry/exit fields, lazy-armed flags, and day-stop state. Unique `(deployment_id, session_date)` (`uniq_premium_lock_per_session`, created in `db.ensure_indexes`) |

### Live execution

| Collection | Purpose |
|---|---|
| `live_broker_tokens` | Daily Flattrade OAuth/session token. **Stored in PLAINTEXT** — `flattrade_token.py::save_token` writes `jKey` raw (no Fernet); the doc was previously mislabelled "encrypted", corrected 2026-07-19. Treat this collection, and the `~/.flattrade/session.json` file mirrored from it, as live credentials. **Also the session authority for the Flattrade MCP**: the same jKey is mirrored one-way on each login (`live/mcp_session_sync.py`) — the MCP never mints a token |
| `live_mode` | ModeStore singleton (`{mode, single_shot_consumed, test_session_id}`) |
| `live_test_sessions` | LIVE_TEST session records |
| `live_orders` | Recorded order intents + broker order-id linkage (idempotency) |
| `live_trades` | Deployed live position journal; realized P&L written on close (`close_loop`) |
| `live_safety_config` | Kill-switch / guardrail config + latch |
| `live_overall_settings` | Live overall control settings |

---

## 6. The Live-Execution Gate Chain (L0–L3)

**Invariant: exactly one function transmits a real entry order.** Every real
order goes through `app/live/executor.py`. No other module in the codebase calls
`client.place_order` for entry — the `_transmit_and_arm` helper contains the SOLE
`place_order` call. There are two sibling public entrypoints that both funnel
into it:

- `place_live_test_order` — the **manual single-shot** path (`LIVE_TEST` mode,
  lots hard-pinned to 1).
- `place_deployed_order` — the **armed-deployment** path (`capped_lots`, NRML so
  a resting GTT/OCO can attach).

### Gate order (identical for both paths, first failure wins)

```
Gate 0  side_must_be_buy      Long-only. A sell entry = unprotected naked short.
Gate 1  authorization         manual: LIVE_TEST + unconsumed single-shot
                              deployed: allow_fn() (per-deployment risk.live arm gate)
Gate 2  fresh dry-run         build_intent (server-side) + margin_verdict; lots
                              pinned/capped; fat_finger_cap default-DENY if absent
Gate 3  margin                broker-resolved lot size; deployed path ALSO runs a
                              broker GetOrderMargin pre-trade check (fail-CLOSED on
                              reject, fail-OPEN on transport hiccup)
Gate 4  all verdicts pass     intent non-None AND every verdict.ok == True
Gate 5  lot-cap defense       qty == exactly the resolved lot count (not-one-lot /
                              not-within-lot-cap otherwise)
Gate 6  engine.can_trade()    engine gate must return (True, ...)
─────── TRANSMIT BOUNDARY (offline-first) ───────────────────────────────────────
Gate 7  env arming            deployed path DRY-RUNS unless LIVE_AUTOPLACE_ARMED=1
                              (returns the validated `would_send` jdata, transmits
                              nothing). Manual click transmits regardless of env.
Gate 8  rate throttle         SEBI <10/s token bucket (real-transmit path only)
────────── THE ONLY place_order CALL ────────────────────────────────────────────
Gate 9  idempotency claim     intent_store.claim_for_submit(cid) → then place_order
        arm-or-abort          on fill: mark_submitted → consume_single_shot →
                              arm(SL backstop + auto-square + best-effort resting
                              OCO). If ANY post-fill step raises → best-effort
                              square + halt engine. No unprotected position persists.
```

### The env-kill pattern (offline-first)

Two host env vars are the master transmit switches. Both are read with the same
affirmative-only parser (`1/true/yes/on`; **anything else, including unset, means
DRY-RUN**), so the default posture is: build + fully validate every order, but
transmit nothing.

| Env var | Gates | Effect when OFF (default) |
|---|---|---|
| `LIVE_AUTOPLACE_ARMED` | deployed **entry** transmit boundary (`executor._autoplace_armed`, `deployments._live_autoplace_armed`) | Armed deployment DRY-RUNS: returns validated `would_send`, no real entry |
| `LIVE_GUARD_ARMED` | automatic **square** transmit (`runtime`, `live_position_guard`, `close_loop`) | Software guard LOGS intended squares but transmits none; a dry-run square returns `{squared: False, dry_run: True}` and is NOT journaled CLOSED |

`arm_state.compute_arm_state` collapses mode + per-deployment arm + both env gates
+ broker connectivity into ONE UI verdict — `SAFE` (nothing armed), `DRY_RUN`
(armed but env gate off), or `LIVE` (a real entry would transmit right now).

### Per-deployment arm & auto-disarm

`mode.is_deployment_live_allowed` is fail-closed: a deployment transmits only when
`risk.live.armed is True`, `now < risk.live.armed_until`, and the broker is
connected. `armed_until` defaults to **15:00 IST** (the EOD square cutoff), so an
arm self-expires the same trading day.

### PC-down catastrophe backstop (GTT / OCO)

A **GTT / OCO-GTT** rests on the **broker's** server, blocks no margin, and never
sits in the order book until triggered — so if the PC/backend dies mid-session it
still stops-out (and/or takes profit). Hard invariants (`gtt.py` + `oco_levels.py`):

- **NRML-only** (`prd == "M"`). MIS is auto-squared by the exchange, so a resting
  GTT for it is unnecessary and dangerous — every builder fails-closed for `prd != "M"`.
- The catastrophe band is derived **strictly wider** than the in-process software
  guard's stop (`guard_stop + MIN_GAP_PP`, capped at ~5%-premium floor), so the
  live guard always exits first and the OCO is a pure last resort that never
  same-premium double-fires. If no safe gap exists it degrades to guard-only and
  raises a `no_broker_backstop` alert rather than clamping up to the guard level.
- On boot, `reboot_reconcile` closes the two holes a PC-death opens (stale
  `live_trades` OPEN row; dangling/orphaned OCO leg) — treating an **empty**
  position book as UNKNOWN, never "flat", so it never false-closes a live position.

### Kill switches & guardrails

`kill_switch.py`: account-level guardrails (`evaluate_guardrails`, fail-safing
unknown P&L to a broker-stop-loss latch that only an explicit reset clears),
`plan_squareoff` (pure — what WOULD be cancelled/flattened), and `panic_squareoff`
(the executor; never raises; flatten intents bypass fat-finger + throttle so the
engine can always exit). Per-deployment kill switches
(`deployment_kill_switch.py`) PAUSE on consecutive-loss / daily-loss and soft-BLOCK
on max-open.

---

## 7. Data-Warehouse Completeness Model

The warehouse's guarantee is the **daily ATM band** — the fix for the class of bug
where hygiene reported "verified" yet backtests hit `MISSING_ENTRY_CANDLE` on the
most volatile sessions (spot sweeps several strikes intraday; per-day/per-expiry
coverage judged those as covered).

```
                       nse_calendar.py
                   (holiday-aware trading days)
                             │
        ┌────────────────────┼─────────────────────┐
        ▼                    ▼                       ▼
  completeness.py       data_hygiene.py        warehouse.py
  (PURE band model)     (DB diff + plan)       (persist/audit)
        │                    │                       │
        │  expected_pairs    │  plan (spot →         │  coverage,
        │  per day =         │  contracts →          │  holiday-aware
        │  every strike the  │  option candles)      │  audit
        │  spot RANGE touched│  index-friendly       │
        │  (rounded+padded), │  aggregations,        │
        │  BOTH CE+PE, at    │  no $lookup (~6s)     │
        │  resolved expiry   │                       │
        └────────────────────┴───────────────────────┘
```

- **`completeness.py` (pure):** `strike_band(day_low, day_high, step, pad_steps)`
  returns every tradable strike the spot range touched (using the SAME
  `options_universe.round_to_step` as the planner, then padded), and
  `expected_pairs_for_day` yields the `(day, expiry, side, strike)` keys the
  warehouse must hold — both legs, at the nearest-on/after expiry. A day is
  complete iff every expected pair has stored candles.
- **`nse_calendar.py`:** hand-curated NSE/BSE 2024–2026 holidays (+ Budget
  Saturdays, shifted-expiry days). All warehouse audits and gap detection filter
  through it, so a market holiday is never counted as a missing day. Review +
  bump `YEAR_LAST_VERIFIED` each new calendar year.
- **`data_hygiene.py`:** diffs the desired scope (from `completeness`) against
  what's stored and emits a **dependency-ordered** plan — spot first, then
  contracts, then option candles. Aggregations group directly on the embedded
  `underlying` / `expiry_date` fields in `options_1m` (no `$lookup`) so the plan
  runs in ~6s instead of a 120s+ timeout.
- **`warehouse_autoupdate.py`:** on startup, on OAuth-connect, and daily at 18:00
  IST, catches the warehouse up to yesterday's close via plan→execute (today's
  bars come from the live roller). Gated on Upstox connected + not-expired; single
  in-flight guard; user-toggleable.

Expiry cadence and lot sizes are **metadata-driven** — read from `option_contracts`
/ `nse_calendar`, never weekday-hardcoded. Verify against the code, not memory.

---

## 8. Key Data Flows

### Market data → warehouse

```
Upstox historical REST ─┐                 ┌─► integrity_hashes (per-day hash)
yfinance (spot fallback)├─► candles_1m ───┤
Upstox WS ticks ────────┘   options_1m    └─► option_coverage_cache (precomputed)
   │  (live_candle_roller aggregates ticks → today's 1m bars)
   └─► ticks (sanitized snapshots)
```

### Research → deploy

```
candles_1m ─► backtest.py ─► backtest_runs ─┐
options_1m ─► option_backtest.py            ├─► (preset OR backtest_run)
              optimizer.py / wfo.py ────────┘         │  strict provenance
                                                       ▼
                                          strategy_deployments (source SHA pinned)
```

### Deploy → paper (default) or live (armed)

```
strategy_deployments
        │  1m_close evaluator (scheduler, ~10s after each bar)
        ▼
     signals ──► auto-paper (premium resolution) ──► paper_trades ──► 15:00 IST square-off
        │                                                (per-minute marker: stop/target/spot-mirror)
        └──► (risk.live armed + LIVE_AUTOPLACE_ARMED) ─► executor.place_deployed_order ─► live_trades
                                                          (gate chain §6; arm = SL guard + resting OCO)
```

---

## 9. Critical Design Choices

- **Local-first.** The local Docker stack is the source of truth; online hosting
  is out of scope.
- **Audit-first.** Every signal carries `bar_ts`, `decision_ts`,
  strategy id/version/hash, frozen params, pretrade snapshot, regime, contract,
  blockers.
- **Single real-order chokepoint.** One `place_order` site in `executor.py`; every
  other path is blocked from placing entries.
- **Offline-first / default-DRY-RUN.** Two env gates (`LIVE_AUTOPLACE_ARMED`,
  `LIVE_GUARD_ARMED`) default OFF; unless set, the system fully validates orders
  and transmits nothing.
- **Long-only live entries.** A sell entry would open an unprotected naked short
  (the SL backstop is always sell-to-close) — rejected before any broker contact.
- **Option entries are premium, never spot.** Both the auto and approve paths
  resolve a real option premium (live tick → fresh stored candle); if none is
  available the trade is refused and journaled. An atomic per-signal claim
  prevents double-trades.
- **Strict source provenance.** Deployments can be created only from saved presets
  or saved backtest runs; direct deployment from a raw plugin is blocked.
- **Strategy source SHA pinned.** Drift between pinned and current SHA auto-pauses
  the deployment with full audit.
- **Idempotent journaling.** Unique partial index on `(deployment_id, candle_ts)`
  plus E11000 handling in the evaluator.
- **Time-of-day discipline.** Default blocks first 10 min / last 30 min; expiry
  cutoff and auto square-off at 15:00 IST (with `allow_overnight` opt-out).
- **Honest OOS lives in WFO.** The single optimizer result is in-sample by
  definition; WFO re-optimizes per train window and scores only unseen test
  slices. Indicators are computed once and sliced per window (safe — every
  indicator is causal).
- **Lot size & expiry are metadata.** Read from `option_contracts` /
  `nse_calendar`, never hardcoded.
- **Precompute, don't scan on read.** Option coverage is served from
  `option_coverage_cache` (~200ms vs ~8s); the hygiene plan avoids `$lookup`
  (~6s vs 120s+). Any new read-path aggregation over `options_1m` (millions of
  docs) must be cached or windowed.
- **Background jobs survive navigation.** A `JobsProvider` above the router tracks
  ingest/fetch/hygiene jobs, persisting run IDs to `localStorage`. Polling loops
  never live in page-local state.
- **CSS variable theming.** Dark and white themes are tokenized; per-panel hex is
  forbidden.

---

## 10. Operational Notes

- The 1m_close evaluator wakes ~10s after each minute boundary during NSE hours;
  the same loop passes the live tick map into `evaluate_active_deployments` and
  runs the per-minute open-trade marker so stop/target/spot-mirror exits fire
  intraday.
- The paper square-off loop runs at 15:00 IST every market day (idempotent).
- The live candle roller and live exit monitor auto-start after WS auto-start at
  boot (only if the Upstox token is valid) and flush on shutdown.
- The live position guard starts unconditionally at boot (reads the broker
  position book, not the Upstox stream); it no-ops offline and only transmits a
  square when `LIVE_GUARD_ARMED=1`.
- Orphaned optimization jobs left `queued`/`running`/`analyzing` by a restart are
  reconciled to `interrupted` (resumable) on boot.
- The unique partial index is created live and reapplied on boot via
  `ensure_indexes()`.

---

## See Also

- [DEVELOPER_GUIDE.md](./DEVELOPER_GUIDE.md) — run/build/test workflow, safety
  model narrative, India trading rules, research→deploy flow, gotchas
- [API_REFERENCE.md](./API_REFERENCE.md) — every backend HTTP route
- [STRATEGY_DEPLOYMENTS.md](./STRATEGY_DEPLOYMENTS.md) — deployment modes, gates,
  kill switches, live
- [STRATEGY_PLUGINS.md](./STRATEGY_PLUGINS.md) — writing a custom strategy plugin
- [Walk-forward (honest OOS) what it does exactly.md](./Walk-forward%20%28honest%20OOS%29%20what%20it%20does%20exactly.md)
  — WFO deep dive
- [live-readback-checklist.md](./live-readback-checklist.md) — real-money readback runbook
- [Resources/flattrade-pi-api/INDEX.md](./Resources/flattrade-pi-api/INDEX.md) —
  decoded Flattrade broker API reference
