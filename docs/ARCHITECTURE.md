# Architecture

Technical reference for AlphaForge Trading Lab: where things live and how data moves.
For state, traps and how to run/test, start at [HANDOFF.md](./HANDOFF.md); for the
task-oriented walkthrough (safety model, research→deploy flow, gotchas) read
[DEVELOPER_GUIDE.md](./DEVELOPER_GUIDE.md). Every HTTP route is in
[API_REFERENCE.md](./API_REFERENCE.md).

Updated: 2026-09-30 · verified against the code on local `main` that day (through the
2026-09-29 Live Deployments uplift and the signal-retirement sweep). Doc passes have
lagged commits before: if this date is more than a couple of weeks old, check
`git log --since=2026-09-30 -- backend/app frontend/src` before trusting a detail.

---

## 1. Purpose

AlphaForge is a **local-first** research and forward-testing terminal for Indian index
options. The traded focus is the NIFTY and SENSEX weeklies; BANKNIFTY spot is also
warehoused. It stores 1-minute spot and option candles in MongoDB, audits coverage,
runs spot and paired-option backtests, optimizes parameters (including honest
walk-forward OOS), runs deployed strategies forward on live 1-minute closes,
paper-trades clean signals at real option premiums, and can place **real** broker
orders through Flattrade.

A deployment is paper by default. It is live when `mode == "live"` (set only by
`POST /deployments/{id}/live/enable`), and a live entry is allowed only while the
deployment is not held, the Flattrade session is connected and unexpired, and it is
before the 15:00 IST cutoff, within per-deployment and account caps. Real entries also
need the host env `LIVE_AUTOPLACE_ARMED=1`; without it every entry is fully validated
and nothing is transmitted.

**Two brokers, two roles:**
- **Upstox** — market **data** (historical REST + V3 WebSocket ticks). Read-only.
- **Flattrade (Noren OMS / PiConnect)** — live **execution** (the only path that can
  place a real order). Static IP required, daily OAuth, limit / SL-limit only. The
  official Flattrade MCP shares this session (never call its login/logout; see
  [flattrade-mcp-integration.md](./flattrade-mcp-integration.md)).

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

- Containers `alphaforge_mongo`, `alphaforge_backend`, `alphaforge_frontend`. All three
  ports are published on **127.0.0.1 only**.
- All backend routes are served under **`/api`** (parent `APIRouter(prefix="/api")` in
  `backend/server.py`; the eight routers in `app/routers/` add the full literal path).
- Compose sets `MONGO_URL`, `DB_NAME=alphaforge`, `CORS_ORIGINS=http://localhost:3000`
  (browse at `localhost:3000`; host scripts should dial `127.0.0.1`),
  `ALPHAFORGE_TICK_REPLAY` (default `0`) and `FLATTRADE_MCP_SESSION_DIR`; the rest comes
  from `backend/.env` (secrets and the live env gates, §7). `FLATTRADE_MCP_SESSION_DIR`
  enables the Flattrade-MCP session sync (unset ⇒ no-op; optional
  `FLATTRADE_MCP_SESSION_TEMPLATE` overrides the payload shape).
- The frontend is built with `REACT_APP_BACKEND_URL=http://localhost:8001`.
- Two bind mounts: the strategy **plugins** dir (`./backend/app/strategies/plugins`) so
  drop-in `.py` strategies persist, and the host `.flattrade` dir → `/host-flattrade` so
  the backend can write the Flattrade MCP binary's `session.json` (machine-specific by
  design). Mongo data persists in the `mongo_data` named volume.
- **Code is baked into the image.** A plain restart runs stale code; deploy with
  `docker compose up -d --build`. Check a fix is in the container with
  `MSYS_NO_PATHCONV=1 docker exec alphaforge_backend grep -c <symbol> /app/app/<file>.py`.

---

## 3. The four core flows

Read these first; almost every live bug so far has been a break in one of them.

### 3.1 Candles: ticks → `live_candle_roller` → `candles_1m`, gaps closed by `candle_recovery`

- The Upstox V3 WebSocket (`upstox_stream.py`) feeds ticks to `live_candle_roller.py`,
  which aggregates them into per-minute bars in `candles_1m` and drops off-session /
  non-trading-day ticks. On each flushed bar it signals `bar_events.py`, which wakes the
  deployment evaluator at once (the evaluator still requires a newer bar in
  `candles_1m`, so entries stay on the CLOSED bar).
- The roller only aggregates ticks it witnesses, so a backend that boots mid-session
  has a hole before boot. **WebSockets cannot supply history.** `candle_gap.py` computes
  which minutes of a trading day are missing (completeness, not freshness);
  `candle_recovery.py` fills them from REST: Upstox V3 intraday first (today only, the
  three index keys only), Flattrade TPSeries as fallback (`flattrade_candles.py`). It
  never writes the in-progress minute and is idempotent (the gap assessment is the
  state). It runs at startup (`runtime.maybe_recover_candles(force=True)`) and on the
  feed-supervisor tick; its latest outcome is in `GET /live-feed/health`.
- There is no same-day candle source for OPTION contracts (Upstox intraday serves only
  the index keys). Live exits are unaffected: the guard marks from the broker position
  book.
- `live_data_gate.py` refuses a NEW `mode → live` activation when today's bars for the
  deployment's instrument cannot be verified. It judges the data, not the strategy, and
  never blocks an exit, the kill switch or the EOD square. It is intentional and is not
  one of the removed arming / research-qualification gates.
- Session shape is date- and segment-aware (`session_spec.py`): from 2026-08-03 the
  index freezes during the 15:15 closing auction while F&O trades to 15:40, so spot days
  are 375 bars and option days 385.

### 3.2 Signal → real order

```
deployment_evaluator (closed 1m bar)
  └─ signal CONFIRMED + clean, deployment mode == "live"
       └─ auto_live.auto_live_trade_for_signal
            (a) auto_live_enabled → live/mode.is_deployment_live_allowed
            (c0) account-wide caps          live_deploy_governor.check_account_caps
            (c)  per-deployment caps        live_deploy_governor.check_live_caps
            (e)  atomic signal claim        (paper ↔ live mutual exclusion)
            (f)  fresh option premium, else refuse
            (g)  capped lots + resolve_live_exit_plan
            (h)  live_deploy_context.arm_for(plan, ...)  +  _recheck_authorization fence
       └─ live/executor.place_deployed_order     ← the single place_order chokepoint (§7)
            └─ on fill: arm(intent, norenordno)
                 ├─ registers the position with live/live_position_guard (mandatory)
                 └─ resting broker OCO only if LIVE_BROKER_OCO_ENABLED (off by default)
       └─ live_trades row (OPEN), signal CONFIRMED → TRIGGERED → ACTIVE
```

`live_deploy_context.build_live_deploy_context` builds the broker collaborators once per
evaluator pass (None ⇒ live disabled for that pass). Refusals are written to the signal
as `live_trade_error`; a dry run (env gate off) writes `live_intended`. Both surface as
`last_entry` in `GET /deployments/{id}/live/status`.

### 3.3 Exits: the software guard

- `live/live_position_guard.py` is the real protection. It reads the **broker** position
  book, runs in-process while the app runs, skips cycles outside the options session,
  and always transmits its squares (no env gate since v0.56.0). Its health
  (`watching / idle / off_hours / blind / stalled / not_running`) is in
  `GET /live-broker/guard-status`.
- Premium stop / target / trail: `live/live_sl_monitor.py` delegates to
  **`exit_controls.effective_premium_stop`**, the same decider the option backtest
  (`option_backtest.py`) and the paper marker (`paper_auto.py`) use, so sim, paper and
  live ratchet one way. `arm_for` also carries `spot_exit`, `time_stop_minutes` and a
  deep-default 50% catastrophe floor if the plan carries no stop. `source="auto_live"`
  positions get the guard's 15:00 IST EOD square.
- Manual exits: `POST /deployments/{id}/live/flatten` (stays live, optional hold),
  `/live/stop` (flatten + demote + pause), `/deployments/stop-all`, and the kill switch.
  The guard and the flatten/stop routes square through `auto_square.square_position`;
  the kill switch uses `kill_switch.panic_squareoff_verified`. Exits on one tsym are
  serialized by the `exit_claims` per-tsym lock so two paths never double-sell. A
  submitted exit is not a fill: the guard finalizes after consecutive flat reads, and
  `close_loop.py` writes realized P&L and CLOSED to `live_trades`.
- After a restart, `rehydrate_from_broker` re-attaches only positions AlphaForge can
  prove it opened (`live/ownership.py`, fail-closed). A position is given back to its
  deployment (so its Flatten / Stop reach it) only when every non-CLOSED journal row for
  that tsym names one deployment and the held quantity does not exceed what those rows
  ordered (`resolve_rehydrate_attribution` / `attribution_for`); with exactly one row it
  is keyed by that row's `norenordno`, like the original arm, so the guard's marks and
  confirmed-flat close land on the row. Anything unproven stays a bare tsym-keyed,
  unattributed entry. Re-attached entries start
  `seen_filled`, keep the default stop (`source="rehydrated"`), and never arm the
  premium-momentum lazy leg.
- The broker OCO is only a PC-down backstop, and on this account it has not been able
  to rest: its stop leg reached the order book one second after the fill and was
  LPP-rejected (the `oivariable` leg pairing is swapped). It is off by default
  (`LIVE_BROKER_OCO_ENABLED`, since 2026-09-03). With it off there is **no** protection
  while the PC or backend is down.

### 3.4 Two symbol spaces

| Space | Field on `live_trades` | Example | Who uses it |
|---|---|---|---|
| Upstox | `trading_symbol` (+ `instrument_key`) | `NIFTY 24300 PE 18 AUG 26` | contracts, ticks, candles, paper |
| Noren (Flattrade) | `noren_tsym` (+ `exch`) | `NIFTY18AUG26P24300` | broker books, guard registry, blotter join, ownership, reconcile, GTT book |

`auto_live` stores both explicitly (the Noren symbol comes from the executor's result).
Never compare one space with the other: on 2026-08-14 reconcile compared
`trading_symbol` with the Noren-keyed position book, so every open live trade looked
flat and a real open position was journalled CLOSED with a null P&L. A row with no
`noren_tsym` is left OPEN (unknown never closes).

---

## 4. Backend Module Map

The app factory is `backend/server.py` (~350 lines): FastAPI app, CORS, the
`startup`/`shutdown` hooks and the background task launches (§11). Request models live
in `app/schemas.py`; shared singletons, loops and helpers in `app/runtime.py`; routes in
`app/routers/*`; domain logic in `app/*`.

### Routers (`app/routers/`, all mounted under `/api`)

| Router | Surface |
|---|---|
| `broker.py` | Upstox connection + read-only stream, market header / analysis (+ SSE), live-candle roller, live-feed health, paper exit-monitor status, `_diag` tick replay |
| `warehouse.py` | spot ingest (yfinance + Upstox), coverage/audit/OHLC, holiday calendar, data hygiene / catch-up / auto-update, VIX, option contracts + candle fetch, option local reads |
| `research.py` | backtest run/start/preflights/runs, optimizer + WFO jobs, presets, pre-trade profiles |
| `premium_momentum_routes.py` | premium-momentum backtest + tune, config-driven premium-trigger backtest |
| `strategies_admin.py` | strategy library (list/catalog/pipeline/references, retire/delete, reload) + AI authoring wizard |
| `journals.py` | dashboard summary, signals ledger + purge, paper trades / analytics / square-off |
| `deployments.py` | deployments (create/pause/resume/stop/archive), preflight, quality, readiness, metrics, overview, paper caps, timeline, and the live sub-routes (enable / pause / resume / caps / flatten / disable / stop / status) |
| `live_broker.py` | Flattrade OAuth/session, broker books, marks (+ SSE), call meter, holdings, trade stats/history, dry-run / preview / approvals / manual place, mode, arm-state, session clock, pre-open readiness, recovery status, guard status, Greeks, safety config, overall settings, GTT/OCO, kill switch |

### Data warehouse & live data (`app/`)

| Module | Responsibility |
|---|---|
| `warehouse.py` | Index candle persistence, coverage, holiday-aware audit, clear |
| `completeness.py` | **The one definition of option completeness: the daily ATM band.** Pure: a day is option-complete when every strike the spot range touched (rounded + padded) has stored CE+PE candles at the resolved expiry |
| `data_hygiene.py` | Diffs desired scope vs stored warehouse; emits a dependency-ordered plan (spot → contracts → option candles). Index-friendly aggregations (no `$lookup`) |
| `nse_calendar.py` / `session_spec.py` | Hand-curated NSE/BSE holidays + special sessions (`YEAR_LAST_VERIFIED`); date- and segment-aware session bounds (CAS split from 2026-08-03) |
| `warehouse_autoupdate.py` | Guarded plan→execute catch-up on startup, OAuth-connect, and a daily 18:00 IST timer |
| `warehouse_lookup.py` / `warehouse_ohlc.py` | Point-in-time spot + ATM + nearest-expiry lookup; 1m→5m/15m/1h/1d resample on IST buckets + gap detection |
| `option_*` (`_contract_store`, `_candles`, `_coverage`, `_coverage_cache`, `_data_audit`, `_data_planner`, `_plan_response`, `_warehouse_jobs`) + `expired_contract_backfill.py` | Option contract metadata, candle normalization, coverage summaries + precomputed cache, preview-first fetch planner, background fetch jobs, expired-contract backfill |
| `options_universe.py` / `instruments.py` | ATM rounding, strike step, lot/expiry metadata per index |
| `upstox_client.py` / `upstox_stream.py` / `upstox_index_ingest.py` / `encryption.py` | OAuth + REST historical/intraday; V3 WebSocket (protobuf, sanitized ticks); background index ingest; Fernet for Upstox tokens at rest |
| `live_candle_roller.py` / `bar_events.py` | Ticks → per-minute `candles_1m`; bar-flushed wake-up for the evaluator (§3.1) |
| `candle_gap.py` / `candle_recovery.py` / `flattrade_candles.py` | Missing-minute assessment; same-day REST gap fill (Upstox intraday, TPSeries fallback) (§3.1) |
| `live_feed_health.py` / `live_data_gate.py` / `preopen_readiness.py` | Pure feed-liveness model (`candles_1m` freshness); data gate on new live activations; the 08:45 IST readiness verdict (persisted to `preopen_readiness`) |
| `chain_recorder.py` / `chain_snapshot.py` | Timer loop recording point-in-time option-chain snapshots into `chain_snapshots` — the one dataset that cannot be backfilled |
| `live_option_universe.py` / `market_header.py` | Nearest-expiry ATM option subscription keys; normalized market-header quote |
| `vix.py` / `yfinance_source.py` / `chunking.py` | India VIX ingest + as-of join; yfinance spot fallback; chunk-size guidance |

### Research engine (`app/`)

| Module | Responsibility |
|---|---|
| `indicators.py` / `indicator_groups.py` / `indicator_param_catalog.py` | Vectorized, param-driven indicators (incl. vectorized FVG); optimizer-addable indicator dimensions |
| `features/` / `data_columns.py` / `option_flow.py` | Opt-in `FeatureGroup` registry (pure pandas over the frame); load-time warehouse-backed columns; ATM CE/PE volume and OI per spot bar |
| `regime.py` / `scenario_classifier.py` / `scenarios.py` | Regime detection (ADX/Choppiness/ATR); scenario-adaptive routing |
| `market_context.py` / `context_signals.py` / `cpr.py` | Regime/ToD/DTE/VIX bucket tagging; S/R, round levels, divergence scores; CPR levels |
| `entry_window.py` | The single source of the entry window (default 09:25-14:50 IST), used by `backtest.py` and the evaluator |
| `backtest.py` | Strategy execution, metrics, statistical significance (materialized-records hot loop) |
| `option_backtest.py` | Paired INDEX+OPTION leg simulation (`simulate_paired_option_trades`); spot_exit / option_levels exit modes. Emits `index_trade_id` = the position **within the list it was handed**; making that a FULL-list position is the caller's duty (`runtime.py::_run_paired_option_backtest` remaps after a DTE filter — v0.55.1). Join legs by id / timestamp, never array position |
| `exit_engine.py` / `exit_controls.py` / `exit_controls_level.py` / `execution_policy.py` | Shared intrabar exit; the trailing/breakeven/daily-cap overlay and `effective_premium_stop` (§3.3); `exit_controls_level.py` is kept as a test parity anchor; execution policy |
| `costs.py` / `option_costs.py` / `slippage.py` / `live_friction.py` / `portfolio.py` | Indian intraday cost models (spot points + rupee option); slippage; the friction model shared by the option backtest and the live paper path; premium-at-risk sizing + rupee equity |
| `dte.py` / `volatility.py` / `vol_seasonality.py` | DTE filter; post-hoc realized-vol detector; vol seasonality |
| `optimizer.py` / `incumbent_seed.py` / `bounds_unit.py` / `optimizer_provenance.py` / `analyze_budget.py` | Optuna TPE / Grid / CMA-ES; two-stage option re-rank; pause/resume/crash-resume; robustness/importance/heatmap. Incumbent seeding (defaults, prior best, validated preset); opt-in `pct_of_index` bounds for point parameters; what a deleted job leaves behind; Analyzing-stage budget |
| `wfo.py` / `walkforward.py` | Honest walk-forward (`kind="wfo"`): per-window re-optimization on train, OOS scoring on unseen test slices |
| `survival.py` / `survival_validate.py` / `rerank_select.py` / `early_stop.py` / `parallel_eval.py` | Survival gate; option re-rank selection; early stop; opt-in parallel trial workers (spawn pool — never fork out of the server) |
| `forward_validation.py` / `option_data_integrity.py` / `option_screen.py` | Pre-registered forward-validation policy (session as the unit); promotion gate for option evidence; option-buying screen used by research scripts |
| `strategies/` | `base.py` (registry + plugin loader), `adaptive_base.py`, `scenario_routing_base.py`, `session_features.py`, `builtin/`, and the drop-in `plugins/` dir (incl. `plugins/premium_momentum.py` — inert `evaluate()`; the real logic lives in the evaluator branch) |
| `premium_momentum.py` / `premium_momentum_backtest.py` / `premium_momentum_tuner.py` / `premium_trigger_config.py` / `premium_trigger_dispatch.py` | Time-locked-strike, premium-native-trigger strategy: pure walk/trail/cost helpers, the option-native backtest sim, the costs-mandatory train/OOS tuner, and the declarative config dispatched to the same sim |
| `ai/` | Multi-provider authoring: `llm_client.py`, `_anthropic.py`, `_gemini.py`, `spec_schema.py`, `compiler.py`, `capability.py`, `authoring_agent.py`, `strategy_author.py`, `source_ingest.py`, `grounding.py`, and the guarded full-Python tier `py_author.py` + `py_sandbox.py` + `_py_smoke_driver.py` |

**Authoring stack — built and tested; extend it, do not rebuild it.**
- Spec mode compiles a validated `StrategySpec` deterministically: no eval/exec in the
  generated code, literals emitted via `repr()`, column names only after the
  `allowed_columns()` whitelist (`ai/compiler.py`).
- Full-Python mode runs an AST structural allowlist, then a real subprocess smoke test
  with a 1 GiB `RLIMIT_AS` cap (`ai/py_sandbox.py`). This prevents accidents; it is not a
  security jail, and human review is the gate.
- Feasibility is decided deterministically by `ai/capability.py::classify_rule`; the LLM
  only parses facts.
- Install has real rollback (`routers/strategies_admin.py::_write_plugin_with_rollback`):
  it restores the previous file when an overwrite fails and removes the orphan when a
  new install fails.
- The Gemini output-truncation fix is pinned by `tests/test_gemini_token_budget.py`.

### Forward testing (paper) & signals (`app/`)

| Module | Responsibility |
|---|---|
| `strategy_deployments.py` / `strategy_source_hash.py` | Deployment doc builder + validation + source resolution; SHA-256 plugin pin for drift detection |
| `deployment_preflight.py` / `deployment_quality.py` | Coverage/expiry/token preflight; quality checks with ack-on-warning |
| `deployment_evaluator.py` | 1m_close evaluator: entry window, expiry cutoff, drift auto-pause, kill-switch checks, `risk_hints`, the auto-paper hook and the live tee (`auto_live`) |
| `deployment_kill_switch.py` | Per-deployment kill switches (consecutive-loss / daily-loss → PAUSE; max-open → soft BLOCK); skips `exit_day_unknown` rows |
| `signal_lifecycle.py` | Signal state machine (`WATCHING → FORMING → CONFIRMED → TRIGGERED → ACTIVE → EXITED`, `SKIPPED`, terminal `AUDITED`) + audit events. Helpers: `signal_bar_ms` (a signal's bar = `candle_ts`, else `context.candle.ts`); `exit_linked_signal` (every paper/live close moves its signal ACTIVE → EXITED, idempotent); `expire_unactioned_signals` (boot + 15:00 sweep: CONFIRMED signals >15 min past their bar with no claim or trade link → AUDITED, reason `unactioned_bar_passed`, own `updated_at` kept) |
| `paper_auto.py` / `paper_trading.py` / `paper_open_positions.py` / `paper_squareoff.py` / `paper_analytics.py` | Auto paper trading (premium: live tick → fresh candle → refuse; never spot), the open-trade marker `mark_open_deployment_trades` (stop / target / spot-mirror / time-stop, ratcheted through `exit_controls`), 15:00 IST square-off, R-multiple/blotter analytics |
| `live_exit_monitor.py` | Runs the paper marker every ~1.5s and on ticks during market hours (despite the name, a paper component; it never places broker orders) |
| `paper_capital.py` / `paper_overall_controls.py` | Entry-time capital constraint for paper deployments; basket overall controls for paper (reuses the live evaluator) |
| `overview_open.py` / `trade_time.py` | OPEN-row accounting for the command-centre cards and live trade stats/history (carried from an earlier day vs today; verified vs unverified by guard-mark freshness; the other book's lines); compare `closed_at` as instants (two string formats exist) |
| `forward_metrics.py` / `preset_execution.py` | Session-gated deployment metrics; preset replay |
| `premium_momentum_live.py` | Per-bar session state machine for `premium_momentum` (`pre_reference → lock+ref-capture → monitoring → triggered/done`), called from a dedicated branch in `deployment_evaluator.py`. Since 5B also the both-legs engine (`leg_mode: "both"` — CE+PE primaries + lazy-leg lock pickup) |
| `premium_lock_store.py` | `premium_locks` accessor — create-once/duplicate-key-adopt lock, atomic trigger latch, entered/done transitions, per-leg (`pce/ppe/lce/lpe`) primitives + the fire-once `mark_day_stop` |
| `premium_pin.py` | `premium_pin_keys()` — today's locked option keys, unioned (cap-exempt) into every option-stream subscription rebuild |

### Live execution — orchestration (`app/`)

| Module | Responsibility |
|---|---|
| `auto_live.py` | The deployed auto-place entrypoint (§3.2): authorization, account + deployment caps, atomic claim, fresh premium, capped lots, `resolve_live_exit_plan`, and `_recheck_authorization` — the transmit-fence callback |
| `live_deploy_context.py` | `build_live_deploy_context` (real broker collaborators per pass) and `arm_for` (guard registration + optional OCO) |
| `live_deploy_governor.py` | Per-deployment caps (`max_concurrent`, `max_lots_per_day`, `daily_loss_cap`) and account caps. Split into `precheck_live_caps → measure_exposure → decide_live_caps`; `check_live_caps` enforces, `describe_live_caps` is the read-only view (the `governor` key of live status), `evaluate_risk_supervision` drives the risk-supervisor loop |
| `live_exit_preview.py` | What exits will actually be in force, computed before going live |
| `live_timeline.py` | Read-only session timeline for one deployment (`GET /deployments/{id}/timeline`) |
| `live_marks.py` / `live_marks_service.py` / `live_mark_cache.py` | Broker position book re-marked against Upstox ticks; the book is read at most once per 15s through a shared single-flight cache; a stale book is never tick-marked. `LiveMarksService.book()` also feeds the Greeks card |
| `market_analysis.py` / `market_analysis_build.py` | Pure analysis primitives + the ~8s-cached cockpit payload (`GET /market/analysis`) |
| `tick_stream.py` / `tick_replay.py` | Shared SSE coalescer for tick-driven payloads; opt-in synthetic tick injector for latency measurement |

### Live execution — broker layer (`app/live/`)

The single real-order chokepoint and its safety scaffolding. See §7 for the gate chain.

| Module | Responsibility |
|---|---|
| `executor.py` | **The SOLE `place_order` chokepoint** — `place_live_test_order` (manual single-shot) + `place_deployed_order` (deployment). `_transmit_and_arm` holds the only `client.place_order` call |
| `mode.py` | Mode gate: `PAPER` / `LIVE_OFFLINE` / `LIVE_TEST` (`LIVE_ARMED` is refused); `is_live_order_allowed` is fail-closed; `is_deployment_live_allowed` = `mode=="live"` + not held (`risk.live.paused`) + connected + before `entry_cutoff_today_ist` (15:00 IST) |
| `session_clock.py` | `describe_session`: one server-side trading clock (phase, market open, entry cutoff, EOD square, derivatives close, closing-auction start, next event), each also as epoch-ms. Pure; derived from the functions that enforce each boundary |
| `arm_state.py` | Pure `compute_arm_state` — collapses mode + `LIVE_AUTOPLACE_ARMED` + connectivity + live-deployment count into ONE verdict (`SAFE` / `DRY_RUN` / `LIVE`) |
| `safety.py` | Pure fail-closed checks: fat-finger cap (default-deny), price band, jData validation (limit/SL-LMT only), `RateThrottle` (SEBI <10/s, cancels never throttled) |
| `margin.py` | Local `margin_verdict` + broker `GetOrderMargin` pre-trade gate (`broker_margin_verdict`) |
| `order_builder.py` / `broker_protocol.py` / `order_sm.py` | Server-side intent build (tick rounding, marketable buffer); `OrderIntent` + allowed prctyp/prd/ret; order state machine |
| `gtt.py` / `oco_levels.py` / `oco_verify.py` | PC-down backstop — NRML-only GTT / OCO builders (catastrophe band strictly wider than the guard's stop); "an accepted OCO is not a resting OCO" readback. Off by default (§3.3) |
| `kill_switch.py` | Account-level guardrails + `plan_squareoff` (pure) + `panic_squareoff` (never raises) |
| `exit_claims.py` | Per-tsym asyncio-lock claim registry (TTL) — serializes guard / kill-switch / manual square paths so two exits never double-sell |
| `live_position_guard.py` / `live_sl_monitor.py` / `auto_square.py` | The software exit guard (§3.3) + `guard_health`; per-position SL/TP/trail monitor (incl. `stepped_xy`), delegating to `exit_controls`; square execution (no manual timer; EOD 15:00 IST is the time backstop) |
| `ownership.py` | Which broker positions AlphaForge can prove it opened (intent store first, order-book `norenordno`/`remarks` join second; fail-closed) and `resolve_rehydrate_attribution` / `attribution_for` (§3.3) |
| `close_loop.py` / `reboot_reconcile.py` / `reconcile.py` | Write realized P&L + CLOSED to `live_trades` on a real square (`realized_pnl` GROSS; `total_charges` / `net_realized_pnl` alongside); boot reconcile — exits proven from the trade book (own fills, carry-free, filled quantity), an unreadable position book = UNKNOWN, a confirmed-empty book (two reads) closes only earlier-day rows, a stale OPEN row closed without a proven price gets `exit_day_unknown`, a today-entry with no fill stays OPEN unless its order ended unfilled (`never_filled`); broker diff report |
| `flattrade_client.py` / `flattrade_token.py` / `flattrade_symbol.py` / `_net.py` / `mock_noren.py` | Real Noren client + daily OAuth + symbol resolve; IPv4 outbound binding for the static-IP whitelist; `MockNoren` test fixture |
| `broker_call_meter.py` | Counts every Flattrade REST call (the rate budget is shared with the Flattrade MCP) |
| `mcp_session_sync.py` | One-way mirror of the fresh jKey into the MCP binary's `session.json` after each OAuth (atomic, skip-if-unchanged, never raises). AlphaForge stays the sole OAuth owner; recovery via `backend/scripts/resync_mcp_session.py` |
| `greeks.py` / `portfolio_greeks.py` / `option_premium.py` / `greeks_book.py` | Server-side Black-Scholes IV-from-premium + Greeks; `classify_broker_book` → `flat / open / unknown` so the Greeks card prices what the broker holds, not the guard registry |
| `live_blotter.py` / `overall_controls.py` / `atm_suggest.py` | Deployment-attributed blotter (pure join); basket-level overall controls evaluator; ATM strike suggestion |
| `session_store.py` / `overall_settings_store.py` / `approval_store.py` / `idempotency.py` | Test-session, overall-settings persistence; the in-memory approval queue (one-shot token + TTL, lost on restart); intent store over `live_orders` (unique `client_order_id`) |
| `engine.py` | `can_trade()` engine gate (halt state) |

---

## 5. Frontend Structure (`frontend/src/`)

### Entry & shared libs

| File | Responsibility |
|---|---|
| `App.js` | Router (`react-router-dom`), theme provider, `JobsProvider` (global background-job tracker), toaster |
| `lib/theme.jsx` | System / Black / White theme state |
| `lib/jobs.jsx` | Global job tracker above the router; persists active run IDs to `localStorage` so ingest/fetch/hygiene progress survives navigation |
| `lib/api.js` / `lib/apiError.js` / `lib/apiHealth.js` | Axios wrapper for `/api/*`; error-message extraction; the sidebar API-health dot |
| `lib/time.js` / `lib/fmt.js` / `lib/istWhen.js` / `lib/utils.js` | IST time helpers, formatting, misc utils |
| `lib/sessionClock.js` (+ `components/live/useServerClock.js`) | Countdowns against the server's session clock, never the browser's |
| Live view logic: `lib/marketFeedHealth.js`, `preopenReadinessView.js`, `greeksView.js`, `liveDeploymentView.js`, `liveTimelineView.js`, `liveTradeView.js`, `liveStopState.js`, `liveCockpitActions.js`, `liveNotify.js`, `overviewOpenView.js`, `signalDisplay.js`, `deploymentState.js`, `exitPreview.js` | Pure functions behind the live and journal surfaces. Frontend logic lives here so pytest can execute it through `node` (drive the code; never assert on JSX source) |
| `lib/backtestMetrics.js` / `paperAgg.js` / `exitReason.js` / `deploymentLiveness.js` / `lastEvaluated.js` | Client-side metric derivation |
| `lib/exports.js` / `optExports.js` / `deployPayload.js` / `strategyParams.js` | CSV/JSON export helpers; deployment payload + param validation |
| `index.css` | Design tokens (CSS variables) for both themes — no per-panel hex |

### Pages (`src/pages/`) → routes

| Route | Page | Purpose |
|---|---|---|
| `/` | `Dashboard.jsx` | Status cards |
| `/warehouse` | `DataWarehouse.jsx` | Connection, Data Hygiene, Index+Option data, Verify/Audit, lookup, chart |
| `/backtest` | `BacktestLab.jsx` | Spot + paired-option backtests + run journal |
| `/optimizer` | `Optimizer.jsx` | Optuna / Grid / Genetic + WFO workflow |
| `/premium-momentum` | `PremiumMomentum.jsx` | Premium-momentum backtest / tune |
| `/strategies` | `StrategyLibrary.jsx` | Built-in + plugin browser, lifecycle, AI authoring |
| `/presets` | `SavedPresets.jsx` | Saved strategy configurations |
| `/checklist` | `PreTradeChecklist.jsx` | Pre-trade checklist profiles |
| `/journal` | `SignalJournal.jsx` | Deployment signal audit trail (+ live lane) |
| `/paper` | `PaperTrading.jsx` | Paper trade journal + analytics |
| `/live` | `LiveSignals.jsx` | "Deploy Strategies": the deployments command center (create, preflight/quality, cards) |
| `/live-trading` | `LiveTrading.jsx` | "Live Broker": `LiveDataProvider` → `LiveErrorBoundary` → `LiveCockpit` |

### Components (`src/components/`)

`live/LiveCockpit.jsx` composes the `live/cockpit/` panels (`CommandBar`, `AlertRail`,
`MarketPulse`, `MarketAnalysis`, `RiskKpis`, `QuickTrade`, `DeploymentSummary`,
`AccountTabs`, `ConfigDrawer`) with `LiveDeploymentStrip` (per-deployment live controls,
`DeployToLivePanel`, `SessionTimeline`), `ExecutionStateStrip`, `SafetyLatchBanner`,
`RecoveryStatusBanner`, `KillSwitchPanel`, `GuardPanel` and `GreeksCard`.
`LiveDataProvider` is the single polling point for the cockpit. Also mounted:
`LiveBlotter` + `LiveTradeStats` (AccountTabs; `LiveBlotter` also in the journal's live
lane), `GttBook` + `OverallSettingsPanel` (ConfigDrawer), `LiveOrderTicket` (QuickTrade),
`FeedHealthBanner` (AlertRail and the Paper page). **Not mounted:** `PositionMonitor.jsx`
(the old manual test-order panel; `LiveDataProvider` still polls the test session for it
— wire-or-delete is an open decision) and `LiveBanner.jsx` (kept because a test reads
it). Other folders: `warehouse/`, `backtest/`, `paper/`, `strategy/`, `journal/`,
`charts/`, and `ui/` (shadcn primitives). Top-level shared components: `Layout.jsx`
(sidebar, theme, token countdown, active-jobs indicator), `MarketHeader.jsx`,
`DataHygienePanel.jsx`, `WarehouseChart.jsx`, `HolidayCalendarDialog.jsx`,
`TrustScorecard.jsx`, and metric/badge widgets.

---

## 6. MongoDB Collections

Names verified against `app/db.py` (`ensure_indexes`) and the code's `db.<name>` accessors.

### Market data & warehouse

| Collection | Purpose |
|---|---|
| `candles_1m` | Index 1-minute OHLCV (spot + INDIAVIX). Unique `(instrument, ts)` |
| `options_1m` | Option premium 1-minute OHLCV + OI. Unique `(instrument_key, ts)` + `(underlying, expiry_date, strike, side, ts)`. Its `instrument_key` is 2-part while `option_contracts` stores a 3-part one: join by identity (`underlying` + `expiry_date` + `strike` + `side` + `ts`) |
| `option_contracts` | Option metadata: instrument_key, trading_symbol, expiry_date, strike, side, lot_size |
| `option_coverage_cache` | Precomputed per-underlying coverage summary (fast page loads) |
| `option_known_empty` | Memoized (contract, date) pairs the broker returned empty for |
| `integrity_hashes` | Per-day index candle counts + hashes. Unique `(instrument, date)` |
| `warehouse_runs` | Ingest / fetch audit log (spot, contracts, options, hygiene) |
| `data_hygiene_latest` | Latest hygiene plan/state snapshot |
| `ticks` | Sanitized live tick snapshots from the WS stream |
| `chain_snapshots` | Point-in-time option-chain snapshots written by `chain_recorder` |
| `upstox_tokens` | Encrypted (Fernet) Upstox OAuth tokens |
| `preopen_readiness` | One 08:45 IST readiness verdict per `session_date` |

### Research

| Collection | Purpose |
|---|---|
| `backtest_runs` | Backtest configs, trades, metrics, option results. `config.option_backtest` is what was asked; the top-level `option_backtest` is what ran |
| `optimization_jobs` | Optimizer + WFO jobs + best results; statuses incl. `paused` / `interrupted`; carries `evaluation_mode`, `rerank`, transient `trial_log` (cleared at completion), `wfo_windows` |
| `presets` | Saved strategy configurations (unique `name`) |
| `pretrade_profiles` | Conservative / Balanced / Aggressive + custom (unique `name`) |
| `strategy_lifecycle` | Per-strategy retire/delete lifecycle (unique `strategy_id`) |
| `app_settings` | Misc app settings (incl. paper starting capital) |

### Forward test (paper) & signals

| Collection | Purpose |
|---|---|
| `strategy_deployments` | Deployment definitions: `mode` (`paper` / `live`), `status` (`ACTIVE` / `PAUSED` / `ARCHIVED`), pinned `strategy_source_sha`, frozen params, `risk` (paper caps `lots_override` / `max_concurrent` / `daily_caps` / `capital`; `risk.live` = `lots`, `max_lots_per_day`, `max_concurrent`, `daily_loss_cap`, catastrophe band, hold `paused` / `paused_at`, `last_caps_change`, `disabled_at`, `last_block_reason`), account-safety snapshot and evidence-consent audit |
| `signals` | One per deployment per evaluated bar. **The bar is `candle_ts`** (epoch-ms; unique partial index `signals_deployment_bar_unique` over `(deployment_id, candle_ts)`). `context` is the evaluator's audit block: `candle` (incl. `ts`, `ist_time`), `bar_ts`, `decision_ts`, strategy hash / version / source SHA, frozen params, pre-trade snapshot, regime. There is **no top-level `bar_ts`** — query and sort on `candle_ts` (`signal_lifecycle.signal_bar_ms` reads it). Also `state`, `events`, `blocked` / `blockers`, `risk_hints`, `option_contract`, `paper_trade_claim`, `paper_trade_id` / `live_trade_id`, and `live_trade_error` / `live_intended` for live refusals / dry runs |
| `paper_trades` | Paper fills at option premium, MTM, realized/unrealized P&L, `risk`, `spot_exit`, `exit_controls`, source flag |
| `premium_locks` | `premium_momentum` per-session strike lock + ref-premium capture + trigger latch; in 5B both-mode also the per-leg (`pce/ppe/lce/lpe`) fields, lazy-armed flags and day-stop state. Unique `(deployment_id, session_date)` |

### Live execution

| Collection | Purpose |
|---|---|
| `live_broker_tokens` | Daily Flattrade OAuth/session token. **Stored in PLAINTEXT** — `flattrade_token.py::save_token` writes `jKey` raw. Treat it, and the `~/.flattrade/session.json` mirrored from it, as live credentials. Also the session authority for the Flattrade MCP (one-way mirror on each login) |
| `live_mode` | ModeStore singleton (`{mode, single_shot_consumed, test_session_id}`) |
| `live_test_sessions` | LIVE_TEST session records |
| `live_orders` | Intent store, written BEFORE any broker POST: `client_order_id` (unique — the real idempotency guard), `deployment_id`, `mode`, `intent` (the full `OrderIntent`; its `tsym` is the Noren symbol sent), `state` (from `INTENT`), `norenordno` (set on submit), `ts_intent`. Primary source for ownership after a restart |
| `live_trades` | Deployed live position journal: `deployment_id`, `signal_id`, `trading_symbol` (Upstox) **and** `noren_tsym` + `exch` (Noren), `norenordno`, `cid`, lots / quantity, `entry_price`, `status`, `marked_at` / `unrealized_pnl` (guard marks), and on close `exit_price`, `exit_reason`, `realized_pnl` (**GROSS** premium P&L — the caps and day-stop basis), `total_charges`, `net_realized_pnl`, optional `exit_day_unknown` |
| `live_safety_config` | Account guardrails + stop-loss latch + engine-halt fields |
| `live_overall_settings` | Overall (basket) control settings, one doc per scope (`_id` = scope; default `overall`, the Paper page uses `paper`) |

The manual order-approval queue (`approval_store.py`) is in memory only.

---

## 7. The Live-Execution Gate Chain

**Invariant: exactly one function transmits a real ENTRY order.** Every real entry goes
through `app/live/executor.py`, where `_transmit_and_arm` contains the SOLE entry
`place_order` call. (Exits place orders elsewhere by design: `auto_square` and the kill
switch's `panic_squareoff*`.) Two public entrypoints funnel into it:

- `place_live_test_order` — the **manual single-shot** path (`LIVE_TEST` mode, lots
  hard-pinned to 1; reached from `POST /live-broker/order/place`).
- `place_deployed_order` — the **deployment** path (`capped_lots`, NRML), called only
  by `auto_live` with the checks of §3.2 already passed.

### Gate order (first failure wins)

```
Gate 0  side_must_be_buy      Long-only. A sell entry = unprotected naked short.
Gate 1  authorization         manual: LIVE_TEST + unconsumed single-shot
                              (+ Gate 1.5: never open a manual position the guard can't close)
                              deployed: allow_fn() → is_deployment_live_allowed
Gate 2  fresh dry-run         build_intent (server-side) + margin_verdict; lots
                              pinned/capped; fat_finger_cap default-DENY if absent
Gate 3  margin                broker-resolved lot size; an unreadable account (limits read
                              fails) blocks fail-CLOSED; deployed path also runs a broker
                              GetOrderMargin check (fail-CLOSED on reject, fail-OPEN on a
                              transport hiccup)
Gate 4  all verdicts pass     intent non-None AND every verdict.ok == True
Gate 5  lot-cap defense       qty == exactly the resolved lot count
Gate 6  engine.can_trade()    engine gate must return (True, ...)
─────── TRANSMIT BOUNDARY (offline-first, deployed path) ─────────────────────────
        env arming            DRY-RUNS unless LIVE_AUTOPLACE_ARMED=1: returns the
                              validated `would_send` jData, transmits nothing
Gate 7  transmit fence        recheck_fn() = auto_live._recheck_authorization on FRESH
                              state: deployment re-read (exists, ACTIVE), authorization
                              at the current clock, fresh `lots` < built size →
                              `caps_tightened:lots a->b`, check_live_caps re-run →
                              `caps:<reason>`. Refusal = `stale_authorization:<why>`;
                              a recheck error fails closed
Gate 8  rate throttle         SEBI <10/s token bucket (a dry run never spends a token)
────────── _transmit_and_arm: THE ONLY place_order CALL ─────────────────────────
        record_intent → engine.can_trade() once more → claim_for_submit(cid) → place_order
        ack lost (timeout / bad body): claim kept, engine HALTED, result `indeterminate`
        on accept: mark_submitted → (manual: consume_single_shot) → arm(intent, norenordno)
        if ANY post-accept step raises → best-effort square + halt. No unprotected
        position persists.
```

Upstream of the executor, `auto_live` also refuses on account caps, deployment caps
(governor), a lost claim, a stale or absent premium, and (premium-momentum) a trigger
that no longer holds; each refusal is journalled on the signal.

### Env switches (offline-first)

| Switch | Gates | Default posture |
|---|---|---|
| `LIVE_AUTOPLACE_ARMED` (host env, affirmative-only parser `1/true/yes/on`) | the deployed **entry** transmit boundary | Off ⇒ every live-deployment entry DRY-RUNS |
| `mode == "live"` (per deployment) | deployed entry authorization; set only by `POST /live/enable` | Non-live deployments route to paper |
| `LIVE_BROKER_OCO_ENABLED` (host env) | the best-effort resting OCO placed by `arm_for` | Off since 2026-09-03 (§3.3) |

There is no `LIVE_GUARD_ARMED` any more (removed v0.56.0): the software guard always
transmits its squares. `arm_state.compute_arm_state` collapses mode, the entry env gate,
connectivity and the count of live deployments that would transmit now into ONE UI
verdict — `SAFE`, `DRY_RUN` or `LIVE` (`GET /live-broker/arm-state`, which also returns
the session clock).

### Deployment live authorization (no arm record)

- `mode.is_deployment_live_allowed` is fail-closed: `mode == "live"`, not held
  (`risk.live.paused`, checked before connectivity so a hold stays visible), broker
  connected, and before **15:00 IST** (`entry_cutoff_today_ist`, the same instant the
  EOD square runs). The per-session ARM ceremony and `risk.live.armed_until` were
  removed in v0.56.0; the legacy `armed` / `armed_until` status fields are compatibility
  echoes.
- `_set_deployment_status` demotes `live → paper` on ANY transition out of ACTIVE, so
  Pause / Stop revoke live authorization. The reversible alternative is the **hold**
  (`POST /live/pause` / `/live/resume`): entries only, the deployment stays live and
  ACTIVE and its open book stays guarded.
- Caps change only two ways: `POST /live/enable` (the sole writer of a complete set) and
  `POST /live/caps` (tighten-only, compare-and-swap; an in-flight entry is caught by the
  transmit fence).
- The deployment's own entry window (default end 14:50) applies in the evaluator before
  the 15:00 gate; live status reports `entry_window.effective_end`.
- The server trading clock (`live/session_clock.describe_session`,
  `GET /live-broker/session-clock`) is the single source for every countdown; the
  authorization and arm-state views treat `closed_day` / `pre_open` as "cannot trade now"
  even when the gate itself passes.

### Caps, kill switches & supervision

- `live_deploy_governor` enforces per-deployment `max_concurrent`, `max_lots_per_day`
  and `daily_loss_cap` (loss measured as realized + open unrealized, GROSS; unknown
  exposure refuses) and account caps from `live_safety_config`.
- `_risk_supervisor_loop` (runtime) re-evaluates caps on a timer so a held position
  running against a limit still trips it. It blocks entries (halts / latches / pauses)
  and never squares.
- `kill_switch.py`: account guardrails (`evaluate_guardrails`, unknown P&L fails safe to
  the stop-loss latch that only an explicit reset clears), `plan_squareoff` (pure), and
  `panic_squareoff` (never raises; flatten intents bypass fat-finger + throttle).
  Per-deployment kill switches (`deployment_kill_switch.py`) PAUSE on consecutive-loss /
  daily-loss and soft-BLOCK on max-open.

### PC-down catastrophe backstop (GTT / OCO)

> **⚠ OFF BY DEFAULT SINCE 2026-09-03.** The resting broker OCO does not rest: its stop
> leg reached the ORDER BOOK one second after the fill and was LPP-rejected, because
> the `oivariable` x/y → leg pairing is SWAPPED (leg1/`x` is the ABOVE slot). It is
> opt-in via `LIVE_BROKER_OCO_ENABLED` (default `0`). **With it off there is NO PC-down
> net** — the in-process software guard is the only protection and it runs only while
> the app runs. Re-enable only after the pairing readback in
> `docs/live-readback-checklist.md` §E1.

Design intent when enabled (`gtt.py` + `oco_levels.py`): **NRML-only** (`prd == "M"`,
every builder fails closed otherwise); the catastrophe band is derived strictly wider
than the software guard's stop so the guard always exits first, and if no safe gap
exists it degrades to guard-only with a `no_broker_backstop` alert. On boot,
`reboot_reconcile` closes the holes a PC death opens (stale `live_trades` OPEN rows,
dangling OCO legs). An unreadable position book is UNKNOWN, never flat; a
confirmed-empty book (two reads 1.5 s apart) closes only rows entered on an earlier IST
day, stamped `exit_day_unknown` (HANDOFF.md §2.6).

---

## 8. Data-Warehouse Completeness Model

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
        │  BOTH CE+PE, at    │  no $lookup           │
        │  resolved expiry   │                       │
        └────────────────────┴───────────────────────┘
```

- **`completeness.py` (pure):** `strike_band(day_low, day_high, step, pad_steps)`
  returns every tradable strike the spot range touched (using the SAME
  `options_universe.round_to_step` as the planner, then padded), and
  `expected_pairs_for_day` yields the `(day, expiry, side, strike)` keys the
  warehouse must hold — both legs, at the nearest-on/after expiry. A day is
  complete iff every expected pair has stored candles.
- **`nse_calendar.py`:** hand-curated NSE/BSE holidays (+ Budget Saturdays,
  shifted-expiry days). All warehouse audits and gap detection filter through it, so a
  market holiday is never counted as a missing day. Review and bump
  `YEAR_LAST_VERIFIED` each new calendar year (the session clock reports
  `calendar_verified: false` past it).
- **`data_hygiene.py`:** diffs the desired scope (from `completeness`) against
  what's stored and emits a **dependency-ordered** plan — spot first, then
  contracts, then option candles. Aggregations group directly on the embedded
  `underlying` / `expiry_date` fields in `options_1m` (no `$lookup`).
- **`warehouse_autoupdate.py`:** on startup, on OAuth-connect, and daily at 18:00
  IST, catches the warehouse up to yesterday's close via plan→execute (today's
  bars come from the live roller and `candle_recovery`). Gated on Upstox connected +
  not-expired; single in-flight guard; user-toggleable.

Expiry cadence and lot sizes are **metadata-driven** — read from `option_contracts`
/ `nse_calendar`, never weekday-hardcoded. Verify against the code, not memory.

---

## 9. Key Data Flows

### Market data → warehouse

```
Upstox historical REST ─┐                        ┌─► integrity_hashes (per-day hash)
yfinance (spot fallback)├─► candles_1m ──────────┤
Upstox WS ticks ────────┘   options_1m           └─► option_coverage_cache (precomputed)
   │  live_candle_roller: ticks → today's 1m bars (+ bar_events wake-up)
   │  candle_recovery: today's gaps ← Upstox V3 intraday / Flattrade TPSeries
   └─► ticks (sanitized snapshots)
Upstox option chain (timer) ─► chain_snapshots (chain_recorder)
```

### Research → deploy

```
candles_1m ─► backtest.py ─► backtest_runs ─┐
options_1m ─► option_backtest.py            ├─► preset / backtest_run ─┐
              optimizer.py / wfo.py ────────┘                          │
Strategy Registry ─► direct immutable 1m-compatible snapshot ─────────┤
                                                                      ▼
                                             strategy_deployments (source SHA pinned)
```

### Deploy → paper (default) or live

```
strategy_deployments (status ACTIVE)
        │  1m_close evaluator (woken by each flushed bar; 2s poll as the floor)
        ▼
     signals (candle_ts) ──► auto-paper (premium resolution) ──► paper_trades ──► 15:00 IST square-off
        │                          (live_exit_monitor ~1.5s: stop/target/spot-mirror/time-stop)
        └──► mode==live, not held, connected, <15:00 ─► auto_live (§3.2)
               ─► executor.place_deployed_order (§7; dry-run unless LIVE_AUTOPLACE_ARMED)
               ─► live_trades + software guard registry (§3.3)
```

---

## 10. Critical Design Choices

- **Local-first.** The local Docker stack is the source of truth. There is no
  application-wide auth boundary, so it is never exposed publicly; the documented
  static-IP design keeps it loopback-bound behind an SSH tunnel
  ([durable-static-ip-deployment.md](./durable-static-ip-deployment.md)).
- **Audit-first.** Every signal carries its bar (`candle_ts`) and an immutable audit
  block (`context`: bar and decision timestamps, strategy id/version/hash/source SHA,
  frozen params, pre-trade snapshot, regime), plus contract and blockers.
- **Single real-order chokepoint.** One `place_order` site in `executor.py`; every other
  path is blocked from placing entries.
- **Offline-first / default-DRY-RUN entries.** `LIVE_AUTOPLACE_ARMED` defaults OFF;
  unless set, the system fully validates entries and transmits none. Exits are not
  env-gated.
- **Long-only live entries.** A sell entry would open an unprotected naked short (the SL
  backstop is always sell-to-close) — rejected before any broker contact.
- **Fail closed on unknowns.** Unknown exposure refuses entries; an unreadable or stale
  broker book is UNKNOWN (never flat), and the reconcile closes rows against an empty
  book only after two confirmed-empty reads; an unprovable owner stays unattributed; a status
  view shows `null`, never `0`, for what it cannot know.
- **One decider per rule.** When two modules must agree, one calls the other: live,
  paper and sim share `exit_controls.effective_premium_stop`; the governor's display
  view runs the enforcing code; the session clock derives the cutoff from the gate's own
  function.
- **Option entries are premium, never spot.** Both the auto and approve paths resolve a
  real option premium (live tick → fresh stored candle); if none is available the trade
  is refused and journalled. An atomic per-signal claim prevents double-trades and
  keeps paper and live mutually exclusive.
- **Strict source provenance.** Deployments are created from a loaded, 1m-compatible
  Strategy Library entry, saved preset, or saved backtest run. A library selection
  becomes an immutable config snapshot; an unregistered raw file remains blocked.
- **Strategy source SHA pinned.** Drift between pinned and current SHA auto-pauses the
  deployment with full audit.
- **Idempotent journaling.** Unique partial index on `(deployment_id, candle_ts)` plus
  E11000 handling in the evaluator.
- **Time-of-day discipline.** Default entry window 09:25-14:50 IST (`entry_window.py`,
  shared by backtest and evaluator); live new-entry cutoff 15:00; paper square-off at
  15:00 IST (with `allow_overnight` opt-out).
- **Honest OOS lives in WFO.** The single optimizer result is in-sample by definition;
  WFO re-optimizes per train window and scores only unseen test slices. Indicators are
  computed once and sliced per window (safe — every indicator is causal).
- **Lot size & expiry are metadata.** Read from `option_contracts` / `nse_calendar`,
  never hardcoded.
- **Precompute, don't scan on read.** Option coverage is served from
  `option_coverage_cache`; the hygiene plan avoids `$lookup`. Any new read-path
  aggregation over `options_1m` (millions of docs) must be cached or windowed.
- **Shared broker rate budget.** The broker book is read at most once per 15s and
  shared by every stream and tab; ticks re-mark it in memory. Every Flattrade REST call
  is counted (`GET /live-broker/call-meter`).
- **Background jobs survive navigation.** A `JobsProvider` above the router tracks
  ingest/fetch/hygiene jobs, persisting run IDs to `localStorage`.
- **CSS variable theming.** Dark and white themes are tokenized; per-panel hex is
  forbidden.

---

## 11. Operational Notes

**Startup (`server.py`), in order:** ensure indexes; discover strategy plugins; seed
pre-trade profiles; reconcile orphaned optimizer jobs (→ `interrupted`, resumable) and
orphaned backtest runs (`queued` / `running` → `failed`, no resume); square paper trades
stranded by a missed square-off; `expire_unactioned_signals`; warm the option-coverage
cache (background); if the Upstox token is valid — start the WS stream, the candle
roller, the paper exit monitor, and a forced candle recovery (background); start the
live position guard unconditionally; launch live recovery (`maybe_run_live_recovery`:
resume pending intents, re-attach the guard, reboot reconcile, re-arm auto-square;
status at `GET /live-broker/recovery-status`); then the background loops below and the
warehouse auto-update (once now, then daily ~18:00 IST).

| Loop (`app/runtime.py` unless noted) | What it does |
|---|---|
| `_deployment_evaluator_loop` | Wakes on each flushed bar (2s poll as the floor) and, inside 09:15-15:30 IST, evaluates ACTIVE deployments once per NEW closed bar (journal signals, auto-open paper / route live) and keeps the ATM option band subscribed. It does not own exits. The 15:00 IST paper square-off and the unactioned-signal sweep run once a day on the first cycle at or after 15:00 on a trading day, above the market-hours gate, so a backend that wakes late still squares |
| `live_exit_monitor` (`live_exit_monitor.py`) | Paper exits: every ~1.5s (and on ticks, at most every 0.2s) during market hours it runs `paper_auto.mark_open_deployment_trades` — stop / target / spot-mirror / time-stop against the live premium. Never places broker orders |
| `_live_feed_supervisor_loop` | During market hours with a valid Upstox token: keeps the stream and roller running, reconciles the paper exit monitor, and runs candle recovery. Never touches credentials |
| `_risk_supervisor_loop` | Re-evaluates live caps on a timer; entries only, never squares |
| `_preopen_readiness_loop` | 08:45 IST readiness verdict → `preopen_readiness` (a PC that is off at 08:45 has no verdict for that day) |
| `chain_recorder_loop` (`chain_recorder.py`) | Records option-chain snapshots |

- The live position guard reads the broker position book, not the Upstox stream. It
  skips cycles outside the options session (health `off_hours`), no-ops with nothing
  registered, and always transmits its squares.
- The roller, stream and paper exit monitor cannot start until the daily Upstox OAuth is
  done; the feed supervisor starts them once it is.
- The unique signal index is (re)applied on every boot via `ensure_indexes()`.

---

## See Also

- [HANDOFF.md](./HANDOFF.md) — current state, traps, run/test, conventions
- [DEVELOPER_GUIDE.md](./DEVELOPER_GUIDE.md) — run/build/test workflow, safety model
  narrative, India trading rules, research→deploy flow, gotchas
- [API_REFERENCE.md](./API_REFERENCE.md) — every backend HTTP route
- [STRATEGY_DEPLOYMENTS.md](./STRATEGY_DEPLOYMENTS.md) — deployment modes, gates,
  kill switches, live
- [STRATEGY_PLUGINS.md](./STRATEGY_PLUGINS.md) — writing a custom strategy plugin
- [optimizer-decision-guide.md](./optimizer-decision-guide.md) — what walk-forward
  actually does, and which optimizer controls earn their cost
- [live-readback-checklist.md](./live-readback-checklist.md) — real-money readback runbook
- [flattrade-mcp-integration.md](./flattrade-mcp-integration.md) — the shared broker
  session with the Flattrade MCP
- [Resources/flattrade-pi-api/INDEX.md](./Resources/flattrade-pi-api/INDEX.md) —
  decoded Flattrade broker API reference
