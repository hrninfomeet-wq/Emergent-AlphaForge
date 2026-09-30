# AlphaForge — Developer Guide

The deep onboarding guide for a new engineer or AI agent: how to run/build/test the app, the
architecture at a glance, the data model, the **live-trading safety model**, India trading rules,
the research → deploy flow, and the gotchas that will bite you.

> AlphaForge Trading Lab is a **local-first** research + paper + live terminal for **Indian index
> options** (NIFTY / SENSEX weeklies; BANKNIFTY is in the warehouse). React (CRA + craco) frontend
> + FastAPI backend + MongoDB (motor), all in Docker Compose. **Upstox = market DATA feed; Flattrade
> (Noren / PiConnect OMS) = live BROKER execution** (static IP, daily OAuth, limit / SL-limit only).
> Everything is IST and holiday-aware. No real entry order is transmitted unless a human has
> switched a deployment to `mode == "live"` **and** the host env gate `LIVE_AUTOPLACE_ARMED` is on.

*Last verified against the code: 2026-09-30.* None of the live-deployment changes of
2026-09-26 → 09-30 described in §E has run in a market session yet.

---

## Table of contents

- [A. Orientation & read order](#a-orientation--read-order)
- [B. Run / build / test workflow](#b-run--build--test-workflow)
- [C. Architecture at a glance](#c-architecture-at-a-glance)
- [D. Data warehouse model](#d-data-warehouse-model)
- [E. Live-trading safety model](#e-live-trading-safety-model-read-this-twice)
- [F. India trading rules & calendar](#f-india-trading-rules--calendar)
- [G. Research → deploy flow](#g-research--deploy-flow)
- [H. Gotchas & known issues](#h-gotchas--known-issues)
- [I. Conventions](#i-conventions)

---

## A. Orientation & read order

Start here, in order:

1. **[docs/HANDOFF.md](HANDOFF.md)** — START HERE: current state, traps, run/test, conventions,
   where-to-go-deep. §2.6 is the most recent live work.
2. **[docs/AGENT_TODO.md](AGENT_TODO.md)** — the only live board (open gates, backlog, decisions).
3. **[docs/BACKTEST_INTEGRITY_AUDIT.md](BACKTEST_INTEGRITY_AUDIT.md)** — the permanent register;
   read before relying on any backtest or optimizer number.
4. **This guide** — how the system works and how not to break it.
5. **[docs/ARCHITECTURE.md](ARCHITECTURE.md)** — module map, data flow, collections.

History lives in **[CHANGELOG.md](../CHANGELOG.md)** (newest first; recent work sits under
dated `[Unreleased]` entries above the last versioned release, 0.58.0) and lessons in
**[learning_log.md](../learning_log.md)**. The repo + `tests/` are the source of truth, not any
prior chat — check `git log` against the top changelog entry before trusting a doc.

Then, by task:

| You are working on… | Read |
|---|---|
| Backend routes | [docs/API_REFERENCE.md](API_REFERENCE.md) (verify against the `@api.*` decorators — it lags) |
| The UI, per page | [docs/USER_MANUAL.md](USER_MANUAL.md) |
| A custom strategy | [docs/STRATEGY_PLUGINS.md](STRATEGY_PLUGINS.md) |
| Deployments / forward test | [docs/STRATEGY_DEPLOYMENTS.md](STRATEGY_DEPLOYMENTS.md), [docs/forward-validation-policy.md](forward-validation-policy.md) |
| The optimizer | [docs/optimizer-decision-guide.md](optimizer-decision-guide.md) |
| Live broker execution | This guide §E, [docs/live-readback-checklist.md](live-readback-checklist.md), [docs/Resources/flattrade-pi-api/INDEX.md](Resources/flattrade-pi-api/INDEX.md) |
| Validating live in a market session | [docs/AGENT_TODO.md](AGENT_TODO.md) (checklist at the top), [docs/LIVE_VALIDATION_PLAN_2026-08.md](LIVE_VALIDATION_PLAN_2026-08.md) |
| The Live Deployments pane (2026-09 uplift) | [HANDOFF §2.6](HANDOFF.md), [the uplift handoff spec](superpowers/specs/2026-09-26-live-deployments-uplift-handoff.md) |
| The Flattrade MCP (shared broker session) | [docs/flattrade-mcp-integration.md](flattrade-mcp-integration.md) |
| Install / daily launch | [docs/STARTUP_MANUAL.md](STARTUP_MANUAL.md) |
| **Trusting a backtest/optimizer number** | [docs/BACKTEST_INTEGRITY_AUDIT.md](BACKTEST_INTEGRITY_AUDIT.md) |
| Agent capabilities / PDF tooling | [CLAUDE.md](../CLAUDE.md) |

**Golden rule:** ground every change in the actual code. Verify routes exist, module names are
right, numbers are real — do not trust a doc (including this one) over the source.

---

## B. Run / build / test workflow

### The stack

Three services in `docker-compose.yml`, every port bound to **loopback only** (`127.0.0.1:…`):

| Service | Port | Container | Notes |
|---|---|---|---|
| `frontend` | `3000` | `alphaforge_frontend` | React + nginx; `REACT_APP_BACKEND_URL=http://localhost:8001` baked at build |
| `backend` | `8001` | `alphaforge_backend` | FastAPI; **all routes under `/api`**; env from `backend/.env`; `CORS_ORIGINS=http://localhost:3000`; no auth layer |
| `mongo` | `27017` | `alphaforge_mongo` | `mongo:7`, **no auth**, named volume `mongo_data` (NOT in the project folder / OneDrive) |

```bash
docker compose up -d --build              # build + launch everything (or start-app.bat --rebuild)
docker compose up -d --build backend      # rebuild ONLY the backend after backend edits
docker compose up -d --build frontend     # rebuild ONLY the frontend after frontend edits
docker compose ps                         # confirm all three are up/healthy
curl -s http://127.0.0.1:8001/api/health  # {"db":"ok"}
```

**Backend and frontend code is baked into the image** — only `backend/app/strategies/plugins` is
bind-mounted. A plain restart (and `start-app.bat` without `--rebuild`, its default) runs **stale
code**, so a verified fix looks broken. After a rebuild, prove the new code is in the container —
a rebuild that silently no-ops has happened here:

```bash
MSYS_NO_PATHCONV=1 docker exec alphaforge_backend grep -c <symbol> /app/app/<file>.py
```

(or import the changed module in-container and print a value). `MSYS_NO_PATHCONV=1` stops Git
Bash on Windows rewriting `/app/...` into a Windows path.

### The test pyramid

**There is no CI** (no `.github/workflows`): the local suite is the only evidence.

**1. The full suite on the host `.venv` — the gate.**

```bash
./.venv/Scripts/python.exe -m pytest tests/ -q -p no:cacheprovider
```

The `.venv` is Python 3.12 with motor and **pandas 3.0.3**, and runs the whole suite (~4 min).
Use that interpreter: under a Python without motor, the motor-dependent tests (marked
`requires_motor`, e.g. in `test_deployment_evaluator.py`, `test_live_option_premium.py`) **skip**
instead of failing — they then need the container run below.
Dated result, 2026-09-30: **6,586 passed, 4 xfailed, 0 failed** (6,590 collected); HANDOFF §2
carries the current baseline. Most tests exercise pure modules directly (`safety.py`, `mode.py`,
`live_deploy_governor.py`, `exit_controls.py`, `nse_calendar.py`, …) or drive routes against
fake DBs. Some older tests pin route/UI shape by **string-asserting on source** via
`tests/contract_corpus.py` (`backend_api_text()`, `warehouse_page_text()`) — they prove presence,
not behaviour.

**`node` must be on `PATH`.** 28 test files execute frontend logic (`frontend/src/lib/*.js`, plus
the assets in `tests/frontend/`) through node, and they **skip** — not fail — when node is
absent. A green run without node says nothing about frontend logic. New frontend logic belongs in
`frontend/src/lib/*.js` with a node-executed test, never a grep of the JSX.

**2. The frontend build gate** — must compile clean before a frontend commit:

```bash
cd frontend && CI=true npx --no-install craco build     # same as `npm run build` (craco build)
```

`CI=true` makes every ESLint warning a build failure. PowerShell equivalent:
`cd frontend; $env:CI='true'; npx --no-install craco build`.

**3. Container run (optional).** `docker cp tests/. alphaforge_backend:/app/tests` then
`docker exec -w /app alphaforge_backend python -m pytest tests/<file> -q`. A full-suite container
run always shows path-contract false failures and skips the node tests (see §H Testing) — judge it
by the files you care about.

**4. Browser smoke** — rebuild, open **`http://localhost:3000`** (not `127.0.0.1:3000`, see §H),
hard-reload (**Ctrl+Shift+R**), click through the changed surface with the devtools console open.

### Rebuild cadence & pre-commit expectations

- Edited backend → `docker compose up -d --build backend`, then prove it landed (above).
- Edited frontend → the build gate **and** `docker compose up -d --build frontend`.
- Before committing: host suite green **and** the frontend build gate **and** a browser smoke of
  the changed surface with no console errors.
- `core.autocrlf=true` produces harmless CRLF warnings on commit — ignore them.

### Operational scripts (`backend/scripts/`)

| Script | Where it runs | Purpose |
|---|---|---|
| `resync_mcp_session.py --clean` | host `.venv` | Recover a stale Flattrade MCP session from AlphaForge's token. Defaults to `localhost:27017` — pass `--mongo-url mongodb://127.0.0.1:27017` |
| `audit_cas_session_coverage.py` | host `.venv` | Read-only: does the warehouse hold the post-CAS session shape (§F)? |
| `purge_off_session_candles.py` | host `.venv` | Delete stored candles outside a real session; dry run unless `--apply`, `--restore <file>` |
| `backfill_realized_from_recorded_fills.py` | `docker exec -i alphaforge_backend python scripts/…` (fills on stdin) | Backfill `realized_pnl` on one closed live trade from a recorded trade book; dry run unless `--apply` |
| `backfill_signal_exits.py` | `docker exec alphaforge_backend python scripts/…` | ACTIVE signals whose trade is CLOSED → EXITED; dry run unless `--apply`; idempotent |
| `screen_option_buying.py`, `screen_candidate_a_flow.py`, `run_pooled_regime_campaign.py` + `verdict_pooled_regime.py` | container or host | Research harnesses — keep: they reproduce the closed verdicts and campaign screens. `pooled_regime_*.json` beside them are gitignored inputs |

Scripts run via `docker exec` are baked into the image too: rebuild after pulling.

---

## C. Architecture at a glance

A short narrative; the full module map + data-flow diagrams are in **[docs/ARCHITECTURE.md](ARCHITECTURE.md)**
(its module list lags the code — `ls backend/app` is authoritative).

- **Backend** (`backend/`): `server.py` is a thin app factory (startup/shutdown, boot
  reconciliation, scheduler wiring, CORS, health) that mounts **eight** routers:
  `app/routers/{research,warehouse,journals,deployments,broker,strategies_admin,live_broker,premium_momentum_routes}.py`
  (each `api = APIRouter()`). `app/runtime.py` holds shared singletons, the supervisor loops and
  route helpers. Import DAG: **server → routers → runtime → business modules** (no cycles; nothing
  imports `server`). Business modules by area:

  | Area | Modules |
  |---|---|
  | Warehouse / data | `completeness.py`, `data_hygiene.py`, `nse_calendar.py`, `session_spec.py`, `warehouse.py`, `candle_gap.py` (pure completeness), `candle_recovery.py` (same-day gap fill), `flattrade_candles.py` (TPSeries fallback), `live_data_gate.py`, `live_candle_roller.py`, `live_feed_health.py`, `option_flow.py` |
  | Backtest | `backtest.py`, `option_backtest.py`, `portfolio.py`, `execution_policy.py`, `exit_controls.py`, `entry_window.py` |
  | Optimizer | `optimizer.py`, `wfo.py`, `walkforward.py`, `survival.py`, `rerank_select.py`, `incumbent_seed.py`, `parallel_eval.py` |
  | Forward test | `deployment_evaluator.py`, `paper_auto.py`, `live_exit_monitor.py`, `paper_squareoff.py`, `signal_lifecycle.py`, `deployment_kill_switch.py` |
  | Live execution | `auto_live.py`, `live_deploy_governor.py`, `live_deploy_context.py`, `live_timeline.py`, `live_marks.py`, `preopen_readiness.py`, `overview_open.py`, and `app/live/*` (executor, guard, reconcile, ownership, session clock, kill switch, …) |
  | Premium-momentum family | `premium_momentum.py`, `premium_momentum_backtest.py`, `premium_momentum_tuner.py`, `premium_momentum_live.py`, `premium_lock_store.py`, `premium_pin.py` (§E, §G, [STRATEGY_DEPLOYMENTS.md](STRATEGY_DEPLOYMENTS.md)) |
  | Market context | `market_analysis.py`, `market_analysis_build.py`, `market_header.py`, `vix.py`, `regime.py` |

- **Frontend** (`frontend/src/`): `pages/*.jsx` per page (Dashboard, BacktestLab, Optimizer,
  DataWarehouse, LiveTrading, LiveSignals, PaperTrading, SignalJournal, StrategyLibrary,
  SavedPresets, PremiumMomentum, PreTradeChecklist); `components/*` per subsystem (`warehouse/`,
  `backtest/`, `live/`, `paper/`, `journal/`, …); `lib/*.js` holds the pure view logic the node
  tests execute (`liveDeploymentView.js`, `sessionClock.js`, `signalDisplay.js`, `liveNotify.js`,
  …); `lib/jobs.jsx` is the global background-job tracker; `lib/api.js` is the axios client.
- **MongoDB collections** (motor): warehouse — `candles_1m`, `options_1m`, `option_contracts`,
  `option_known_empty`, `option_coverage_cache`, `integrity_hashes`, `warehouse_runs`,
  `data_hygiene_latest`, `chain_snapshots`, `ticks`; research — `backtest_runs`,
  `optimization_jobs`, `presets`; forward test — `strategy_deployments`, `strategy_lifecycle`,
  `signals`, `paper_trades`, `premium_locks`; live — `live_trades` (the journal), `live_orders`
  (the intent store), `live_mode`, `live_test_sessions`, `live_safety_config`,
  `live_overall_settings`, `live_broker_tokens`, `preopen_readiness`; plus `upstox_tokens`,
  `pretrade_profiles`, `app_settings`.
- **External**: Upstox (daily OAuth + REST historical + V3 WebSocket ticks) for **data**;
  Flattrade / Noren PiConnect OMS for **live orders** (decoded reference at
  [docs/Resources/flattrade-pi-api/](Resources/flattrade-pi-api/)).
- **AI authoring stack — built; do not rebuild it.** Spec mode compiles deterministically
  (`ai/compiler.py`: no `eval`/`exec` in generated code, literals emitted via `repr()`, column names
  only after the `allowed_columns()` whitelist). Full-Python mode uses an AST allowlist plus a real
  subprocess smoke test under a 1 GiB `RLIMIT_AS` cap (`ai/py_sandbox.py`) — accident prevention,
  not a jail; human review is the gate. Feasibility is decided deterministically by
  `ai/capability.classify_rule`, never by the LLM. Plugin install rolls back cleanly
  (`routers/strategies_admin._write_plugin_with_rollback`: restore the previous file on an
  overwrite failure, remove the orphan on a new-install failure).

---

## D. Data warehouse model

The warehouse (`candles_1m` = 1-minute OHLCV for the 3 index spots + `INDIAVIX`; `options_1m` =
option candles for the ATM-band contracts) is judged by **one** definition of "complete". Read
this before touching warehouse code.

- **Daily ATM-band completeness** (`app/completeness.py`) is the single truth. A day is
  option-complete when **every strike its spot low→high touched** (nearest `round_to_step` ±1 pad),
  for **both legs** (CE + PE), at the day's resolved (next-available) expiry, has candles. The old
  per-day/per-expiry presence check was the "verified-but-incomplete" bug.
- **Fetch is driven by the same band it's judged against** — `data_hygiene.build_band_fetch_plan`
  → `missing_band_pairs` → exact `(day, expiry, side, strike)` tasks. Never derive a separate
  moneyness selection for the fetch.
- **Broker-empty ledger** (`option_known_empty`): some band strikes are genuinely unavailable at
  Upstox (late-listed strikes never archived). After a band fetch, `record_broker_empty_pairs`
  ledgers requested-but-absent pairs **whose task did not fail AND are before the latest closed
  session** (F&O history publishes with a lag — never ledger a same-night session). Ledgered pairs
  are excluded from `missing_pairs` and shown as "broker-empty" so status reaches **verified**
  honestly.
- **Holiday-aware calendar** (`app/nse_calendar.py`): hand-curated NSE/BSE holidays 2024–2026 +
  Budget-Saturday special sessions + Muhurat short sessions + shifted-expiry days. `expected_candle_count`
  drives the coverage heatmap so weekends/holidays are never flagged red. `market_status(now_ist)`
  is the single holiday-aware "is the market open?" source. Session length is date- and
  segment-aware since the CAS change (§F, `session_spec.py`).
- **Partial-day spot repair**: a day captured only partially (PC off mid-session) is re-fetched
  when its stored count is materially below `expected_candle_count`, bounded by
  `SPOT_REPAIR_LOOKBACK_DAYS=21`.
- **Canonical keys**: candles stored under the 2-part `SEGMENT|TOKEN` form
  (`instruments.canonical_instrument_key`); dated 3-part keys live only inside expired-endpoint
  URLs. **Expired routing keys off `expiry_date < today(IST)`**, not provenance.
- **Writes are last-writer-wins.** `warehouse.persist_candles_df` is an unconditional `$set`
  upsert on the unique `(instrument, ts)` index — no merge, no "more complete bar wins". Never let
  a backfill write the in-progress minute (`candle_recovery` enforces this as its invariant 1).

### Sync / auto-update / top-up

- **One-button sync** = `POST /api/warehouse/sync` (alias of `/data-hygiene/catch-up`): catch up new
  sessions + band sweep for spot-current instruments + VIX top-up.
- **Auto-update** (`warehouse_autoupdate.py`) runs on startup, on Upstox OAuth-connect, and daily at
  18:00 IST.
- **Instant status**: `/api/data-hygiene/plan` persists to `data_hygiene_latest`;
  `/api/data-hygiene/latest` serves it so the page shows health on load.
- **To top up** you need a valid Upstox token (daily OAuth). Connect Upstox, then click **Sync now**
  on the Data Warehouse page (or `POST /api/data-hygiene/catch-up`). Rolling scope = 9 months
  (floor 2024-11-27), NIFTY + BANKNIFTY + SENSEX, daily ATM band.
- **Same-day spot gaps** (PC booted mid-session): `runtime.maybe_recover_candles` runs at startup
  and from the live-feed supervisor — `candle_gap` assesses, `candle_recovery` fetches (Upstox V3
  intraday, Flattrade TPSeries fallback), persists and re-assesses. **Index spots only** (see §H).

---

## E. Live-trading safety model (read this twice)

This is the most important section. The Live Trading page and the Live Deployments pane
(Flattrade / Noren OMS) can place **real money orders**, but the system is **offline-first** and
layered so a runaway order is structurally impossible without a human deliberately enabling it.
Every guarantee below is enforced **in code** — the module is cited so you can verify it.

### Who else holds the broker session: the Flattrade MCP (v0.55.2)

Before anything else in this section, know that **AlphaForge is not the only thing on this
broker account**. The user has the official **Flattrade Trading MCP** server installed (a separate,
closed-source binary giving an AI assistant conversational read/write access to the live account).
Because Flattrade's API V2 allows **one API key per account**, and that key holds the one redirect
URI AlphaForge owns, the MCP **cannot complete its own OAuth** — so AlphaForge is the **sole OAuth
owner** and mirrors its jKey into the MCP's session file after every login
(`live/mcp_session_sync.py`, gated by `FLATTRADE_MCP_SESSION_DIR`, wrapped so a sync failure can
never break the login).

Three consequences that matter when you touch live code:

1. **`live_broker_tokens` is the session authority for two consumers.** If you change token
   storage, expiry handling, or the callback, keep the sync call intact (pinned by
   `tests/test_mcp_session_sync.py`) or the MCP silently goes stale.
2. **Positions opened through the MCP are invisible to the guard, OCO backstop, SL monitor and
   kill switch** — ownership is proven from AlphaForge's own intent store and journal, and a foreign
   order has neither. This is an explicit, user-accepted trade-off; never assume a broker position
   is AlphaForge-protected just because you can see it.
3. **The PiConnect rate budget is per key and shared** (40 req/s, 200/min; orders 10/s,
   40/min). Guard/reconcile polling is safety-critical — MCP chatter competes with it.

**Never call the MCP's `login`/`logout` tools** (login would invalidate AlphaForge's token —
Flattrade is last-login-wins; logout wipes the shared session). Never place / modify / cancel
orders through it. Recovery is `backend/scripts/resync_mcp_session.py --clean`. Full detail:
[`flattrade-mcp-integration.md`](flattrade-mcp-integration.md).

### The single order chokepoint

**All real entries go through `app/live/executor.py`.** There are exactly two public entry
functions and they share the **one and only** `client.place_order(...)` entry call site
(`_transmit_and_arm`):

- `place_live_test_order(...)` — the **manual** single-shot ticket path.
- `place_deployed_order(...)` — the **live-deployment** auto-place path.

No other module may call `client.place_order` for an entry. If you add a live feature, route it
through the executor — do not open a second placement path.

### The env gates (v0.56.0: one hard entry kill)

| Env var | Gates | Default | If unset |
|---|---|---|---|
| `LIVE_AUTOPLACE_ARMED` | **auto entries** (deployment path) | `0` | a live-mode deployment only **dry-runs** — builds + validates the full intent, transmits nothing |
| `LIVE_BROKER_OCO_ENABLED` | the **PC-down resting broker OCO** | `0` | no OCO is attempted; `oco_al_id` stays null and `auto_live` journals `oco_error="no_broker_backstop"`. **There is no PC-down net.** Off since 2026-09-03 — see §E1 of the readback checklist |

`executor._autoplace_armed()` reads `LIVE_AUTOPLACE_ARMED` and accepts only `1/true/yes/on`;
anything else (including unset) means dry-run. It is the only env gate on deployment ENTRY
transmission (the manual path is gated by the `LIVE_TEST` single-shot mode instead). The software
exit guard is gated by neither — it always transmits.

**`LIVE_GUARD_ARMED` is REMOVED.** The software exit guard (stop/target/trailing squares and its
Layer-2 widening re-price) **always transmits** — a deployed strategy's own exits are part of the
strategy, not an opt-in extra. This closed a dangerous split (audit finding L20) where real entries
could transmit while the automated exit guard only logged. `guard_armed` (live status) and `armed`
(`GET /live-broker/guard-status`) are constant `True`, kept for payload compatibility — use
guard `health` below for what the guard is doing. (The live-status `armed` field just mirrors
`mode == "live"`.) Any other reference to `LIVE_GUARD_ARMED` is stale.

### Manual path gate chain (`place_live_test_order`)

Enforced in this order (`executor.py`); any failure returns `placed:false` and **no order
reaches the broker**:

- **Gate 0 — long-only**: `side` must be `"B"` (a sell entry would open an unprotected naked short).
- **Gate 1 — mode** (`mode.is_live_order_allowed`): must be `LIVE_TEST` **with an unconsumed
  single-shot**. PAPER / LIVE_OFFLINE / missing / malformed all fail closed.
- **Gate 1.5 — guard interlock**: refuses (`guard_not_armed`) unless the caller asserts an
  automated close exists. Structurally satisfied in production (the guard always transmits);
  retained so a future caller without a guard cannot open an unclosable position.
- **Gates 2–4 — fresh server-side dry-run**: `build_intent` with **`lots` hard-pinned to 1** and
  `fat_finger_cap` clamped to ≤ 1, a margin verdict, and every verdict must pass.
- **Gate 5 — `qty == lot_size`** defense-in-depth (`not_one_lot` otherwise).
- **Gate 6 — engine**: `engine.can_trade()` must return `(True, …)`.
- **Gates 7–8 + post-fill**: `record_intent` → idempotency claim → **THE ONLY `place_order`
  call** → `mark_submitted` → `consume_single_shot` → `arm`. If **any** post-fill step raises,
  `_abort_protect` drives a best-effort square + engine halt so **no unprotected live position
  can persist**.

### Live-deployment path (`auto_live.auto_live_trade_for_signal` → `place_deployed_order`)

Authorized by the deployment's **mode**, not an arm record — the per-session ARM ceremony was
**removed in v0.56.0** by explicit user decision: enabling a deployment in live mode IS the
authorization, and the strategy's own entry/exit/SL/TP/trailing logic drives execution.
`risk.live.armed` / `armed_until` are never written any more (`armed_until` is always `null` in the
status payload); nothing may read them as authorization.

In `auto_live` (caps, contract and premium refusals are written to the signal as
`live_trade_error`, so a deployment that "never placed" has a reason on screen):

1. **Authorization** — `mode.is_deployment_live_allowed(deployment, now, connected=…)`: requires
   `mode == "live"`, **no live HOLD** (`risk.live.paused` → `live_paused`), the broker connected,
   and `now` before the **15:00 IST new-entry cutoff** (`mode.entry_cutoff_today_ist`, an alias of
   the older `armed_until_today_ist`). Fail-closed on anything missing, malformed or unresolvable.
2. **Account caps** (`live_deploy_governor.check_account_caps`, all deployments) — sticky latch,
   `max_open_positions`, and the account daily-loss guardrail (a loss breach trips the latch and
   halts the engine).
3. **Deployment caps** (`check_live_caps`, below). A pausing verdict writes `status="PAUSED"` +
   `mode="paper"` + `risk.live.last_block_reason="daily_loss"` in one update.
4. Option contract present → **atomic claim** (`paper_trade_claim`, so one signal is traded by
   paper OR live, never both) → a **fresh WS option premium** for the entry band (stale / absent →
   refuse; never spot, never a candle).

Then in `place_deployed_order` (`executor.py`, gates numbered as in the code):

- **Gate 1** re-runs the authorization callable; **Gates 2–4** build the intent at `capped_lots`
  with `fat_finger_cap` = the **account ceiling** (`max_lots_per_order`, default 20), margin must
  cover the full size, and the broker's `GetOrderMargin` fails **closed** on a reject (open on a
  transport error); **Gate 5** lot-cap defense-in-depth (`not_within_lot_cap`); **Gate 6**
  `engine.can_trade()`.
- **Transmit boundary** — unless `LIVE_AUTOPLACE_ARMED` is on, return the validated `would_send`
  jdata and transmit nothing.
- **Gate 7 — the transmit fence** (see below), then **Gate 8** the rate throttle
  (`safety.RateThrottle`, 9 orders/s under the SEBI 10/s limit; **cancels/exits are never
  throttled**), then the one `place_order`.

### The transmit fence (G2, 2026-09-29)

Gate 1 decides on the signal-time deployment doc and a frozen clock, and several broker
round-trips follow. `auto_live._recheck_authorization` (passed as `recheck_fn`) runs immediately
before transmit — no `await` remains between it and the send — and re-reads the deployment:

- `status != "ACTIVE"` → refuse; `is_deployment_live_allowed` on the **fresh doc with a fresh
  clock** (catches Stop / Disable / HOLD / demotion / crossing the cutoff mid-flight);
- fresh `lots` below the size the order was built at → `stale_authorization:caps_tightened:lots a->b`
  (a raise keeps the smaller built size);
- `check_live_caps` re-run on the fresh doc, fresh rows and the fence clock →
  `stale_authorization:caps:<reason>` (also catches this deployment's own sibling entry journalled
  while this one was in flight);
- the recheck itself raising → `stale_authorization:recheck_failed`. Fail closed.

### Per-deployment caps governor (`app/live_deploy_governor.py`)

Caps live under `deployment.risk.live`: `lots` (the per-entry order size, not a cap),
`max_lots_per_day`, `max_concurrent`, `daily_loss_cap` (₹ magnitude). The checks are split into
pure steps — **`precheck_live_caps` → `measure_exposure` → `decide_live_caps`** (and
`decide_account_caps` for the account layer) — which enforcement (`check_live_caps` /
`check_account_caps`) and the read-only **`describe_live_caps`** both run, so the screen shows the
governor's own numbers, never a second derivation.

Deployment verdicts (first match wins; `pause=True` pauses and demotes the deployment):

| Reason | Pause | When |
|---|---|---|
| `live_caps_missing` | yes | `mode == "live"` with no cap configured — fail closed, never the old allow-all fast path |
| `invalid_daily_loss_cap` | yes | non-finite cap (NaN would silently disable the breaker) |
| `exposure_unknown` | no | loss cap set and an OPEN row's mark is missing or older than 120 s (`MARK_STALE_AFTER_SECONDS`) |
| `daily_loss_cap` | yes | realized today + open unrealized ≤ −cap |
| `lots_unmeasurable` / `max_lots_per_day` | no | a poisoned `lots` row / today's lots + this entry > cap |
| `max_concurrent` | no | open live trades ≥ cap |

- **Loss basis is GROSS.** `realized_pnl` (`live/close_loop.realized_fields`, the one P&L formula
  for every close path) is `qty × (exit − entry)`; charges are journalled separately. By operator
  decision a ₹5,000 cap means ₹5,000 of premium move, excluding charges. Rows flagged
  `exit_day_unknown` never count toward today (below).
- **`describe_live_caps`** is the `governor` key on `GET /deployments/live/status?ids=…` (and the
  per-id route): caps, consumed, `verdict`, `authorization` (the entry path's first check, plus
  `market_closed_today` / `before_market_open` from the session clock), `account_verdict`, and
  `binding` — the first refusal in the entry path's own order (authorization → account →
  deployment). It has no engine parameter, writes nothing and never raises; anything unknowable is
  `null`, never 0. Render it; never re-derive enforcement in the UI.
- **The risk supervisor** (`runtime._risk_supervisor_loop`, 10 s) runs the same exposure math on a
  timer (`evaluate_risk_supervision`) so a HELD position bleeding past a limit trips something
  without a new signal: account breach → latch + halt; per-deployment breach →
  `_supervisor_pause_for_loss_cap` (PAUSED → demoted, `last_block_reason="daily_loss_cap"`). It
  **never squares**, and unknown exposure yields no action. ⚠ This pause was a `TypeError` from
  2026-08-06 until `35a33fe` (2026-09-29) and has not yet fired in a market session.
- `POST /deployments/{id}/live/enable` is the only route that sets `mode="live"`; it requires
  `lots`/`max_lots_per_day`/`max_concurrent` ≥ 1 and runs the preflight chain (ACTIVE, not retired,
  not drift-paused, broker ready, engine can trade, the **live data gate**, and forward validation
  or explicit `accept_unvalidated_live` consent).

### Operator controls — what each one does to mode, status and positions

| Control | Route | Mode | Status | Open positions |
|---|---|---|---|---|
| Enable live | `POST /deployments/{id}/live/enable` | → `live` | unchanged | — |
| **Hold** (Pause live) | `POST /deployments/{id}/live/pause` / `…/live/resume` | stays `live` | stays ACTIVE | untouched and guarded; entries refused (`live_paused`). Resume is a pure clear — nothing to re-consent |
| **Tighten caps** | `POST /deployments/{id}/live/caps` | stays `live` | stays ACTIVE | untouched; applies to signals evaluated after the write |
| **Flatten** | `POST /deployments/{id}/live/flatten` (`{"hold": true}` optional) | stays `live` | unchanged | squared via the margin-safe path |
| Disable live | `POST /deployments/{id}/live/disable` | → `paper` | unchanged | NOT flattened; still guarded |
| Stop | `POST /deployments/{id}/live/stop` | → `paper` | → PAUSED | flattened |
| Stop ALL | `POST /deployments/stop-all` | every live → `paper` | every ACTIVE → PAUSED | live flattened, paper squared |
| Pause (generic) | `POST /deployments/{id}/pause` | live → `paper` | → PAUSED | — |
| Kill switch | `POST /live-broker/kill-switch` | every live → `paper` | those → PAUSED | account latch + engine halt FIRST, then flatten |

- **Any transition out of ACTIVE demotes `live → paper`** (`runtime._set_deployment_status`, the
  v0.56.0 invariant), so a later Resume can never silently re-authorize real money. `status=PAUSED`
  is what stops re-entry (`evaluate_all` only iterates ACTIVE). The HOLD exists precisely because
  it touches neither field.
- **Tighten-only caps.** Any increase — even one field of a request that lowers others — is
  refused whole (409 `caps_loosening_refused`, nothing written); raising a cap is Disable →
  re-Enable. The write is a compare-and-swap on `updated_at` + ACTIVE + live (a racing Stop wins,
  409). Broker readiness and the data gate are NOT required: tightening must work with the broker
  down. An in-flight entry is fenced by G2 above.
- **Flatten is honest by construction**: out of market hours it refuses (409 `market_closed`) and
  sends nothing; a contract shared with another registry entry is skipped (squaring clamps to the
  account's netqty on that scrip); OPEN journal rows the guard does not hold for this deployment
  are listed as `unguarded_open_tsyms`, never squared; `fill_confirmed` is always `false` — a
  submitted exit is not a fill.
- **`GET /deployments/{id}/timeline?date=`** — read-only, never raises: the IST day's signals,
  refusals, entries, exits, orders and latest hold/disable/caps events, plus `gaps` naming what is
  recorded nowhere (entries skipped while held / after the cutoff / broker down; halt-latch history).
- Frontend alerts (`lib/liveNotify.js`) are **opt-in, default off**, and fire only while the tab is
  open. Stop / Stop ALL toasts are derived from the exit report, never assumed.

### The trading clock (`live/session_clock.describe_session`)

One pure, server-side answer for every countdown: the entry cutoff from the gate's own
`entry_cutoff_today_ist`, the EOD square from the guard's own configured `eod_square_ist`
(a separate 15:00 constant, equal to the cutoff only by coincidence), the trading day from the
holiday-aware calendar, the session bounds from `session_spec`. Every boundary is an absolute
epoch-ms; `phase` ∈ `closed_day` / `pre_open` / `after_eod` / `after_cutoff` / `session`, plus
`next_event`. Served by `GET /live-broker/session-clock` and on the arm-state poll. The binding
entry end for most deployments is **not** 15:00: the deployment's entry window (default
09:25–14:50, `entry_window.resolve_entry_window`) is applied by the evaluator first — live status
reports `entry_window.effective_end`.

### The catastrophe backstop (PC-down)

> **⚠ OFF BY DEFAULT SINCE 2026-09-03.** The resting broker OCO does not rest: its
> stop leg reached the ORDER BOOK one second after the fill and was LPP-rejected,
> because the `oivariable` x/y -> leg pairing is SWAPPED (leg1/`x` is the ABOVE
> slot). It is now opt-in via `LIVE_BROKER_OCO_ENABLED` (default `0`). **With it
> off there is NO PC-down net** — the in-process software guard is the only
> protection and it runs only while the app runs. Re-enable only after the pairing
> readback in `docs/live-readback-checklist.md` §E1.

Because a resting SL-LMT on a short option margin-rejects, the software exit guard
(`app/live/live_position_guard.py`, started in `server.py` lifespan) reads the **broker** position
book every 1.5 s during the options session and squares in software via a margin-safe
cancel-all-then-close — and **always transmits**. The NRML resting GTT/OCO tooling
(`app/live/gtt.py`, schema from the vision-verified PiConnect catalog) remains: `GET /live-broker/gtt`
lists, `POST` builds + transmits **only on explicit `transmit=true`**, `DELETE /live-broker/gtt/{al_id}`
cancels.

### Guard health — say what the guard is actually doing

`live_position_guard.guard_health` (pure, from the guard's own `status()`), surfaced as `health`
on `GET /live-broker/guard-status`:

| State | Meaning |
|---|---|
| `not_running` | the guard task is not running — nothing is watching stops |
| `off_hours` | outside the options session; the guard skips cycles **by design** (registered positions resume being watched at the next open). Checked before `stalled` — it used to read STALLED every evening |
| `stalled` | no cycle completed for 30 s (`GUARD_STALLED_AFTER_SECONDS`) |
| `blind` | cycling, but every cycle stops at an unreadable book (e.g. the daily token expired) — no stop or target can fire |
| `idle` | cycling with nothing registered |
| `watching` | cycling over registered positions |

The same rule governs every status surface: **unknown is shown as unknown, never as "live",
"none" or 0.** Broker "connected" means stored AND not expired. Other surfaces built on that rule
(2026-09-29): market-header feed health (`lib/marketFeedHealth.js`, stream `connected` +
`last_tick_age_s`), the 08:45 pre-open readiness verdict (`GET /live-broker/preopen-readiness`,
wrapped with `is_today` / `days_ago`; `verdict: null` means "never ran", not "all clear"), the
Greeks card priced off the broker book (`live/greeks_book.py`), the sidebar API dot
(`lib/apiHealth.js`), overview OPEN rows (`overview_open.py`).

### Broker-truth integrity — a read failure is UNKNOWN, never flat

`BrokerReadError` (`live/broker_protocol.py`) is raised by the Flattrade readers whenever the Noren
API returns `stat != Ok` — **except** the documented "no data" empty-book signal, which is a real
zero, not a failure (`_is_no_data`/`_parse_book` in `flattrade_client.py`). Every consumer (kill
route, both square paths, the guard cycle, the executor's pre-transmit limit gates, the blotter,
reconcile) treats a read error as **UNKNOWN**, not FLAT and not squared. If you add a new
broker-read consumer, it must fail closed (hold / block / mark UNKNOWN) on `BrokerReadError`, never
coerce to flat.

### The kill switch is a true stop-all, and exits are serialized against double-selling

`routers/live_broker._run_kill_switch` trips the persistent safety latch (`engine.can_trade()`
goes false) **before** any broker or DB read that could raise, halts the engine, and takes every
live deployment out of live mode (`_disarm_all_live_deployments`: selector `{"mode": "live"}`, sets
`mode="paper"` + `status="PAUSED"`) — only then does it flatten. Each step is independently
try-guarded. All exit paths (the software guard, the kill switch, a manual/deployment square)
funnel through `live/exit_claims.py`, a per-tsym asyncio-lock claim registry with a TTL: a second
path trying to exit a tsym another path already claimed gets `exit_in_flight_elsewhere` instead of
racing a double-sell. Two further windows were closed by adversarial review (2026-07-11/12); follow
the pattern in any new exit code:

- **Lost-ack adoption.** A `place_order` call that raised (timeout/network) may have actually landed
  at the broker. Before any retry, resolve `remarks == client_order_id` against the order book
  (`kill_switch._scan_order_by_remarks`): if it landed, **adopt it, never re-post**; if the book
  can't be read, **fail closed** (no retry) rather than guess. All three exit executors
  (`auto_square`, `reprice_exit_leg`, `panic_squareoff_verified`) follow this.
- **Cancel-confirm barrier.** Before placing a flatten/reprice order, every cancel it depends on must
  be independently confirmed **terminal** (one re-fetch).

`LiveEngine.can_trade()` blocks on **any** of three stops: the in-memory `halted`, the persisted
`engine_halted` (so a restart cannot clear a halt), and the `blocked_until_reset` latch. Neither
halt may be read as a proxy for the latch — most halt reasons never trip it.
`POST /live-broker/safety-config/reset-latch` clears both the persisted state and the in-memory
halt; a half-completed reset leaves the desk stopped, never half-open.

### Recovery after a restart

`runtime.maybe_run_live_recovery` is triggered from process boot, the Flattrade OAuth callback (a
token obtained *after* boot still triggers a pass), and the supervisor loop (a transient failure
gets retried) — gated by a **per-token latch** recorded only when the run is truly **complete**
(client present, no step raised, the position book actually readable). A "ran but incomplete"
state must be retried, never remembered as done. What a run does:

- **Adoption fails closed** (`live/ownership.resolve_owned_tsyms`). The guard re-attaches only
  positions AlphaForge can prove it opened — from the intent store (`live_orders`, `intent.tsym` is
  the Noren symbol that was POSTed) or the order-book `norenordno`/`remarks` join. An intent whose
  journal row is CLOSED, or whose contract expired, confers no ownership. SHORT positions are never
  adopted (the guard's stop logic is long-only). A hand-placed position must never be adopted: on
  2026-08-04 one was, and was squared for real money 24 s later.
- **Restart attribution (G1, 2026-09-29).** `ownership.resolve_rehydrate_attribution` maps each
  owned tsym to its non-CLOSED journal rows; `attribution_for` accepts it only when every row names
  the **same deployment** and the held quantity is ≤ what those rows ordered. Then
  `rehydrate_from_broker` keys the entry by the row's `norenordno` (single row) with its
  `deployment_id`, exactly like the original arm — so the deployment's Flatten / Stop reach it and
  the guard's marks and confirmed-flat close land on the row. Anything unproven stays a bare,
  unattributed tsym entry. Levels are NOT restored: the deep-default 50% premium stop,
  `source="rehydrated"`. A re-attached entry starts `seen_filled` (else a position that went flat
  before the first cycle would journal as `never_filled`), and a recovered (default-level) stop
  **never arms the premium-momentum lazy leg**.
- **Reconcile proves exits from the trade book** (`live/reboot_reconcile._match_close`). An exit
  price is attributed only on proof: SELLs tagged `oco:<norenordno>`, or the SELLs
  `_proven_round_trip` attributes to this entry (its own fills in today's book, nothing interleaved,
  no carried-forward position — `_carry_free` requires `cfbuyqty`/`cfsellqty` = 0 on every
  position-book row) summing **exactly** to the entry's own **filled** quantity. P&L is measured
  from the entry's own fills, never the blended `daybuyavgprc`. Anything else closes without a
  price — never fabricated. A same-day entry with no fill stays OPEN (its order may still be
  working) unless the order book shows it ended unfilled → `never_filled`.
- **Stale OPEN rows close on proof, stamped `exit_day_unknown`.** A confirmed-empty position book
  (two reads 1.5 s apart) closes rows entered on an earlier IST day; with an unreadable book only
  rows whose contract has provably expired close (`live_marks.expired_before`, calendar proof). A
  row closed flat without a proven price whose entry day is before today is flagged too.
  `exit_day_unknown` rows are excluded from today's realized P&L (`daily_realized_summary`,
  `trade_time`) and from the governor's diagnostics — a later price backfill must never be charged
  to today's day-stop. (Motivating case: a 2026-09-16 row sat OPEN for eleven days holding a
  `max_concurrent` slot.)

### The guard never silently drops a position

If `live_position_guard`'s square retries are exhausted (`max_square_retries`, default 25), the
entry **stays registered** in an escalated `square_stopped` state (escalation stat + operator log)
rather than being un-watched; `status().stuck` counts such positions. The **EOD 15:00 IST square
explicitly bypasses** `square_stopped` (`ignore_square_stopped=True`): a no-OCO manual or
rehydrated position must still get its end-of-day flatten.

### `auto_square.py`'s manual 10-minute timer is gone — EOD is the only manual backstop

This section is the record of that removal (2026-07-09, user decision). The manual `LIVE_TEST` arm
once scheduled a hard 10-minute auto-square (`SQUARE_HORIZON_SEC = 600`, `deadline_iso` /
`is_due`, `live_broker._schedule_auto_square`). It was test-only scaffolding and is deleted: a
manual position is now protected by the software guard's premium stop (deep-default 50% when the
order carries none), the **15:00 IST EOD square** as its "never left open" backstop, and manual
Square / Kill. `GET /live-broker/test-session` still exists (and still reads the order book back
to detect a rejected entry) but no longer returns `deadline` / `remaining_secs`. Deployed
strategies never had the timer — they exit on their own rules under the software guard (the
resting broker OCO is opt-in and off by default). `auto_square.build_sl_backstop_intent` and
`square_position` remain — the executor + SL-backstop builder. Do not resurrect a resting-timer
concept; read the module docstring first.

### Premium-momentum: a strategy driven by a locked strike + premium trigger, not spot

`premium_momentum` is architecturally different from every other strategy: instead of
`strategy.evaluate()` reading spot candles, `deployment_evaluator.py` has a **dedicated branch**
that calls `premium_momentum_live.evaluate_premium_momentum_bar` per bar. It routes on
**capability** (`is_premium_trigger_strategy` — the strategy declares itself premium-native), never
on a block attached to the deployment, so an ordinary strategy can never have its `evaluate()`
silently bypassed. At a configurable reference time it locks the CE/PE strike from spot and captures each side's
premium from fresh WS ticks into `premium_locks` (unique per `(deployment_id, session_date)`,
create-once / duplicate-key-adopt for crash safety); the first side whose premium crosses the
momentum threshold journals a signal and — **only after that journal succeeds** — the trigger is
atomically latched (a failed latch downgrades the outcome so nothing trades on a
journaled-but-unlatched signal). `auto_live` re-checks the trigger against the fresh entry tick
before placing (`premium_trigger_not_met` releases the claim and the latch). Exits can use the
`stepped_xy` guard trail mode (`live_sl_monitor.py`) — an AlgoTest-style discrete ratchet (raise
the stop by Y for every X of favorable move), sourced from `deployment.risk.exit_controls`. Backtest
and live share correctness through **shared pure helper functions** (`lock_reference_strike`,
`momentum_triggered`, `stepped_trail_stop`, …), not a shared loop.

**There is no premium-momentum-specific arming gate.** It authorizes through the exact same
`mode == "live"` + `LIVE_AUTOPLACE_ARMED` + caps-governor chain as every other strategy — an
explicit user decision (an earlier spec draft had a 10-paper-session validation gate; it was
removed on request). **Do not add one back "for safety."** Locked strikes are pinned into every
option subscription-stream rebuild (the auto-follow path in `runtime.py` and the manual restart
route in `routers/broker.py` — a new stream-rebuild site must union in `premium_pin_keys` too, or a
locked strike can silently drop off the tick feed mid-session). Recovery
(`runtime.rehydrate_premium_momentum`) re-registers guard entries for already-entered locks using
the **persisted entry premium**, and skips any lock whose order id or resolved trading symbol is
already watched — without this a re-run could double-watch one position under two keys.

Since v0.55.0 the family also executes **multi-leg** (`leg_mode: "both"`: CE+PE independent
primaries, a one-shot lazy reversal leg off STOP-class exits, per-deployment `exit_time` squares,
realized-only day-stop, VIX gate) — see `STRATEGY_DEPLOYMENTS.md` → "Multi-leg mode (Phase 5B)" for
the config keys and the load-bearing invariants. **Paper and live both run it** since v0.56.4: the
shared pure predicate `premium_momentum_live.lazy_arm_side` gates lazy arming on both rails (live
via the guard-close hook in `runtime.py`, paper via `paper_auto._maybe_arm_paper_lazy_leg`, each
classifying its own stop reasons), and paper honours `exit_time` through `risk_hints.square_at_ist`.
Recovery symbols come exclusively from the broker order-book join, never the persisted Upstox
symbol. The family **failed its pre-registered edge gate**
([PREMIUM_MOMENTUM_EDGE_VERDICT_2026-07.md](PREMIUM_MOMENTUM_EDGE_VERDICT_2026-07.md)); 5B exists as
a user-decided pure capability, with the verdict surfaced as an informational advisory on
`/live/enable`.

### The one invariant that never changes

**The assistant never personally transmits or squares a real order.** It builds 100% of the code,
but authorizing real money — switching a deployment to `mode == "live"` via `POST
/deployments/{id}/live/enable`, raising a cap, flipping `LIVE_AUTOPLACE_ARMED` or
`LIVE_BROKER_OCO_ENABLED`, or a manual Place click — is always a human action. Never bypass the
executor, never remove a gate, never default an env gate to on. See
[docs/live-readback-checklist.md](live-readback-checklist.md) before any live session.

---

## F. India trading rules & calendar

Locked conventions — verify against the code, don't guess:

- **IST everywhere.** All timestamps, cutoffs, and calendar logic use `Asia/Kolkata`
  (`IST = timezone(timedelta(hours=5, minutes=30))`).
- **Sessions are date- and segment-aware** (`session_spec.py`). Before 2026-08-03 every segment
  ran 09:15–15:30 IST = 375 one-minute candles (`nse_calendar.REGULAR_SESSION_CANDLES`). From
  **2026-08-03** (SEBI Closing Auction Session, `CAS_EFFECTIVE_ISO`): the **index** stops trading at
  15:15 — its candles from 15:15 are frozen (zero range) until the auction close prints as one jump
  bar, still 375 bars; **options** trade to **15:40** = 385 bars. Never make a session rule
  retroactive — dates before the effective date must keep the old shape.
- **Entry window** (`entry_window.py`, applied by `deployment_evaluator.py`): default 09:25–14:50
  (`BLOCK_OPEN_UNTIL` / `BLOCK_CLOSE_FROM`), per deployment via `risk.trade_window_start/end`.
  **15:00 IST square-off** every trading day (`risk.allow_overnight` opts out); the live new-entry
  cutoff is also 15:00 (§E); **expiry-day cutoff 15:00 IST** from `option_contracts.expiry_date`
  (never weekday-hardcoded).
- **NSE/BSE holidays** are hand-curated in `nse_calendar.py` for 2024–2026 (review + extend each
  January; bump `YEAR_LAST_VERIFIED`). Budget-Saturday sessions, Muhurat short sessions, and
  shifted-expiry days are modeled.
- **Lot sizes & strike steps** (`app/instruments.py::UNDERLYING_META`, verify here — do **not**
  guess):

  | Index | Instrument key | Strike step | Lot size |
  |---|---|---|---|
  | NIFTY | `NSE_INDEX\|Nifty 50` | 50 | **65** |
  | BANKNIFTY | `NSE_INDEX\|Nifty Bank` | 100 | **35** |
  | SENSEX | `BSE_INDEX\|SENSEX` | 100 | **20** |

  Lot size for a **trade** is always read from `option_contracts.lot_size` (never hardcoded); the
  table above is the strike-band metadata. `INDIAVIX` (`NSE_INDEX|India VIX`) is ingested for
  **context only** and is never treated as an option underlying.
- **Expiry cadence**: read `expiry_date` from `option_contracts`, never re-derive by weekday.
  Weekly cadences differ per index (NIFTY / SENSEX weeklies, BANKNIFTY monthly) and the exchange
  shifts an expiry when the day is a holiday (`SHIFTED_EXPIRY_DAYS`). `select_contract_for_signal`
  is exact-match-or-None (no nearest fallback) and always filters `expiry_date >= today`.
- **DTE** (`app/dte.py`): DTE 0 = expiry day (0DTE), DTE n = n trading days before expiry; default
  filter `[0..6]`. Sessions after the last known expiry return `None` (unknown DTE).
- **Fills are premium-never-spot**; slippage scales with moneyness (ATM 0.5pt / OTM1·ITM1 1pt /
  OTM2+·ITM2+ 2pt / expiry-day last-30-min 2×); **OPEN trades are never deletable**.

---

## G. Research → deploy flow

The pipeline turns a hypothesis into a forward test, with a decision gate at each step. Nothing
auto-promotes a money-loser.

```
Warehouse (data)
   │  band-complete 1m spot + option candles
   ▼
Optimizer  ──► Backtest Lab  ──► Preset / run  ──► Deployment  ──► Paper  ──► Live (Flattrade)
   │               │                 │                 │            │           │
   TPE/Grid/GA     honest ₹-first    saved config      signal_only  auto-open   auto-place within
   ± walk-forward  metrics + exit    (from a preset    | paper       at real     caps (env gate +
   ± survival gate overlay           or a run)          modes        premium      mode=="live")
```

1. **Optimizer** (`optimizer.py` + `wfo.py`/`walkforward.py`): Optuna TPE / Grid / Genetic search,
   single or **walk-forward** (honest OOS), spot vs **option re-rank** evaluation. The **survival
   gate** (`survival.py`, default-off) is the overfit guard: each surviving finalist is evaluated
   per-OOS-fold on the **₹-capital option-equity curve** (absolute ₹ floor → DD% cap → risk-of-ruin);
   **zero survivors ⇒ `done_no_survivor`** (never promotes a disqualified candidate). Optional
   `search_exit_controls` sweeps exit configs per survivor. **Gate: only survivors advance.** Read
   [BACKTEST_INTEGRITY_AUDIT.md](BACKTEST_INTEGRITY_AUDIT.md) for the optimizer's open findings.
2. **Backtest Lab** (`backtest.py` + `option_backtest.py` + `portfolio.py`): re-run a config as a
   paired real-option-candle backtest with honest ₹-first metrics (CAGR/Calmar suppressed under a
   ~1-year window; Profit÷maxDD is the headline). Optional exit/risk-control overlay
   (`exit_controls.py`: trailing / breakeven / daily caps). **Gate: does the ₹ equity curve survive
   OOS + full-window?**
3. **Preset / run** → **Deployment** (`strategy_deployments`): a deployment is created from a
   saved preset, a backtest run or a library strategy (`source_type` = `preset` | `backtest_run` |
   `strategy`, each snapshotted — `runtime._load_deployment_source`); creatable modes are
   `signal_only | paper` (`live` can never be requested at creation — it is reached only via the
   preflighted `/live/enable` route in step 5).
   The strategy-source SHA is pinned; the evaluator auto-pauses on drift.
4. **Paper** (`deployment_evaluator.py` + `paper_auto.py` + `live_exit_monitor.py`): runs on a
   1-minute-close scheduler in market hours; `risk.auto_paper` (default ON) opens a paper trade per
   clean CONFIRMED signal at real option premium; the live exit monitor (~1.5s) drives tick-level
   stop/target/spot-mirror/time-stop exits; 15:00 square-off. Forward metrics gate on ≥70%-covered
   10:00–15:00 sessions; low sample surfaces under an amber badge. **Gate: does forward P&L match
   the backtest?**
5. **Live** (`auto_live.py` + `app/live/*`): only after paper earns confidence, `POST
   /deployments/{id}/live/enable` switches a deployment's `mode` to `"live"` — the preflighted,
   caps-required route described in §E — so its own signals route through the executor chokepoint
   under the env gate + caps governor + daily entry cutoff. **Gate: a human enables it; the
   assistant never does.**

**Premium-native strategies** (e.g. `premium_momentum`) follow the same 5-step flow but with their
own backtest engine (`premium_momentum_backtest.py`, option-native self-contained sim rather than
the spot-then-paired-option two-stage engine) and their own tuner (`premium_momentum_tuner.py`).
That tuner is a reusable **honest-tuning pattern** worth following for any future tunable strategy:
costs are **mandatory** to enable before tuning (the tuner refuses otherwise), parameter selection
happens on a **chronological TRAIN split only**, results always report the **OOS** slice the
selection never saw, and an **overfit flag** fires automatically when the train-best config's OOS
result diverges sharply from its train result. Its first real run selected a config that looked
like +408 points on train and was actually −418 points OOS — flagged correctly, by design, not by
luck. Don't build a "just pick the best backtest number" tuner for a new strategy; copy this shape.

Two AlgoTest-fidelity caveats the premium backtest carries: (1) AlgoTest's exact momentum order
type (stop vs stop-limit) is undocumented, so the trigger is approximated as the **first 1m bar
whose premium close** crosses the threshold (`premium_momentum.walk_premium_momentum`); (2) PE-side
`ITM1` = ATM + one strike step is the standard reading and what
`options_universe.strike_offset_for_moneyness` encodes, but it is not a verbatim AlgoTest quote.

**Closed research questions** (do not re-run without new evidence): the verdict docs in `docs/`
(`OPTIMIZER_VERDICT_2026-07`, `POOLED_REGIME_VERDICT_2026-07`, `PREMIUM_MOMENTUM_EDGE_VERDICT_2026-07`,
`PROFIT_LEVERAGE_ANALYSIS_2026-07`, `OPTION_BUYING_MICROSTRUCTURE_2026-08`,
`INTRADAY_OPTION_BUYING_CANDIDATES_2026-08` — the unconditioned ATM baseline is NO_EDGE on both
indices) carry pre-registered kill criteria. The framework refusing to promote a money-loser is it
working as intended.

### Signal lifecycle (`app/signal_lifecycle.py`)

Every evaluator decision is a `signals` doc moving through `WATCHING → FORMING → CONFIRMED →
TRIGGERED → ACTIVE → EXITED`; `TRIGGERED` may instead go to `SKIPPED`, and every non-terminal
state may go to the terminal `AUDITED` (`ALLOWED_TRANSITIONS`; `transition_signal` appends an
event per move).

- **A signal's bar is `candle_ts`** (and `context.candle.ts`). `bar_ts` exists only on the
  evaluation AUDIT record — real signal docs do not carry it. Read the bar with
  `signal_bar_ms(sig)`; the Signal Journal's date filter and sort, and the overview's "Signals
  today", key on `candle_ts`. (The journal API still names the field `bar_ts`.)
- **Every close moves its signal ACTIVE → EXITED.** `exit_linked_signal` is the one place a trade
  close reaches back to its signal — called by the paper marker, the paper square-off sweep and
  every live close (`live/close_loop.py`). Idempotent, conditional on `state == ACTIVE`, and it
  **never raises** into the close path.
- **Unactioned CONFIRMED signals are retired.** A CONFIRMED signal is acted on only inside its own
  bar's evaluator pass; nothing revisits it. `expire_unactioned_signals` (at boot in `server.py` and
  in the 15:00 sweep in `runtime.py`; operator-approved) moves CONFIRMED signals more than 15 min
  past their bar with no claim or trade link to AUDITED, reason `unactioned_bar_passed`, keeping the
  signal's own `updated_at` (it is read as the refusal time and as the purge age). Blocked
  signals, unreadable bars and anything with `paper_trade_claim` / `paper_trade_id` /
  `live_trade_id` are never touched. The journal shows these as EXPIRED / NOT ACTED ON / RETIRED
  chips (`lib/signalDisplay.js`).
- **Retention deletes blocked noise only.** AUDITED now also holds clean retired history, so the
  journal's auto-purge calls `POST /signals/purge` with `states: ["AUDITED"], blocked: true`; the
  route's `blocked` filter exists for exactly this.

---

## H. Gotchas & known issues

Real operational lessons pulled from code comments, HANDOFF, and CHANGELOG. Read the relevant one
before touching that area.

**Host / Windows (the operator's machine):**
- The operator's terminal is **Windows PowerShell 5.1**: `A && B` is a ParserError and **neither**
  command runs. Give one command per line, or `A; B`, or `A; if ($?) { B }`. `&&` works only in
  pwsh 7+, cmd and bash. Env vars are `$env:NAME='x'`, not `NAME=x cmd`.
- **Host scripts and probes dial `127.0.0.1`, never `localhost`** — `localhost` resolves to `::1`
  first and stalls ~2 s against IPv4-only Docker (e.g. `mongodb://127.0.0.1:27017`,
  `http://127.0.0.1:8001`). `resync_mcp_session.py` and `screen_option_buying.py` default to
  `localhost`; pass `--mongo-url mongodb://127.0.0.1:27017`.
- **The browser is the opposite: use `http://localhost:3000`, never `127.0.0.1:3000`.** The bundle
  targets `http://localhost:8001` and `CORS_ORIGINS` allows only `http://localhost:3000`
  (`docker-compose.yml`, `backend/.env.example`), so from `127.0.0.1:3000` every API call is
  CORS-blocked and the pages look empty.
- Git Bash rewrites `/app/...` paths in `docker exec` arguments — prefix `MSYS_NO_PATHCONV=1`.
- Mongo has **no auth** and the API has no auth layer: both are reachable only because the ports
  are bound to loopback. Any port remap must keep the `127.0.0.1:` prefix.

**Upstox (data):**
- 30-day chunks crossing Feb→Mar give `400 Invalid date range` — use `chunk_days=7`.
- Historical is **empty for the in-progress day** (the live candle roller and `candle_recovery`
  cover today).
- F&O history publishes with a **lag** — never trust same-night completeness (hence the broker-empty
  ledger grace rule).
- Expired options need `/v2/expired-instruments/...` (normal V3 returns `UDAPI100011`).
- `GLOBAL_INDICATOR|USDINR` REST quote 400s but works on WS. WS subscription set is fixed at connect
  (stop + restart to change).
- **Stream ≠ roller.** No roller ⇒ 0 intraday candles ⇒ the evaluator's new-bar gate never opens
  ("paper deployment ACTIVE all day, 0 trades"). The live-feed supervisor
  (`runtime._live_feed_supervisor_loop`) starts the stream + roller in market hours whenever the
  Upstox token is valid; with a missing/expired token it does nothing. Confirm the roller is
  running, not just the stream.

**Same-day candles & completeness:**
- **Freshness ≠ completeness.** `live_feed_health` (`FRESH_THRESHOLD_SEC = 120`) measures only the
  age of the newest bar — a 10:31 boot reads LIVE within two minutes with 09:15–10:31 absent.
  Completeness is `candle_gap.assess_completeness`. `live_data_gate` blocks a NEW live activation
  (`/live/enable` → 409 `incomplete_market_data`) while today's bars cannot be verified. That gate
  judges the DATA, not the strategy — it is intentional and is not one of the removed arming gates.
- **No same-day source for OPTION 1m candles.** Upstox intraday (`fetch_intraday_1m`) accepts only
  the three index keys, and the TPSeries fallback maps index tokens only. Live exits are unaffected
  (the guard marks from the broker position book), but intraday option-premium reads and same-day
  paper/backtest parity are. TPSeries and Upstox also disagree on the OPEN of ~2 bars in 60 (max
  1.85 pts; the 09:15 open convention), so a day assembled from both carries mixed conventions —
  Upstox stays primary.
- **Two `datetime` string formats coexist in `candles_1m`.** The roller writes
  `2026-08-14 09:50:00` (IST); Upstox ingest stores the vendor ISO `2026-08-14T09:15:00+05:30`.
  Lexically the space form sorts before the `T` form, so the two sort inverted. Every read path
  sorts and filters on `ts` (epoch ms, unique with `instrument`) — do the same; never sort or
  range-filter on `datetime`.
- **Late-tick hazard (latent).** The roller buckets on `tick.get("received_ts") or tick.get("ts")`
  (`live_candle_roller.py`). A tick for an already-flushed minute M would prematurely flush the
  in-progress M+1 bucket as a partial bar and seed a degenerate O=H=L=C bar for M that overwrites
  the complete one (last-writer-wins upsert). The roller docstring's "the upsert guarantees
  idempotency" is true of the write, not the value. It is guarded only **empirically** (0 backward
  jumps across 20,433 NIFTY `received_ts` values on 2026-08-14), not structurally: `ts` and
  `received_ts` are both broker-side clocks (p50 254 ms / p95 1.7 s apart) and `ingest_ts` is the
  only local clock. Keying the roller on `ingest_ts` would make it structural.

**Trade↔option-leg joins (v0.55.1 — cost a whole debugging session):**
- `simulate_paired_option_trades` sets `index_trade_id` from `enumerate()` over **the list it is
  given**. Any caller that filters spot trades first (the DTE filter in
  `runtime.py::_run_paired_option_backtest`, `wfo._pair_oos_with_options`,
  `optimizer._option_rerank`) therefore produces leg ids in **filtered-list space**.
- Surfacing those ids next to the **full** trade list — which the saved run doc and the Backtest
  Lab Trades pane do — misattributes every leg after the first dropped trade. The visible symptom
  is nonsense that looks like a *pairing* bug (a CE row showing a PE leg at a non-ATM strike) but
  is purely a display join. **Suspect index alignment before you suspect the selector or the
  warehouse.**
- Rule: remap to full-list positions before returning (runtime.py does), and join by
  `index_trade_id`/`signal_entry_ts`, **never by array position**. `wfo.py` and the option
  preflight carry guard comments because they are safe only by not re-joining today.
- Historical saved runs were repaired by a one-off script (removed; git `a2294d7`, CHANGELOG
  0.55.1) that joined on `signal_entry_ts` + upper-cased direction (skipping any doc where that key
  was not unique and total). It is reversible via `option_backtest.index_remap_backup` =
  `{"trades"|"skipped_trades": {"<pos>": <old index_trade_id>}}`; no code reads that field.

**Contract correctness:**
- Always filter `expiry_date >= today` when picking a live contract; `select_contract_for_signal`
  is exact-match-or-None (regression-pinned).
- **NSE/BSE reuse exchange tokens across expiry cycles.** ~30 canonical 2-part keys
  (`NSE_FO|46181`) in `options_1m` carry candles for two *different* contracts — verified
  strictly time-disjoint, so time-windowed reads are always correct, but any lookup by 2-part key
  that is NOT time-windowed or expiry-constrained can silently return the wrong strike. Join
  contracts by identity (underlying, expiry, strike, side), not by exchange token.
- **Flattrade BFO (SENSEX) symbols share no format with NFO (NIFTY)** — `SENSEX2691774300PE` vs
  `NIFTY23JUN26C25000`; the BFO weekly symbol carries no year and a single-character month
  (1–9, then O/N/D). `live_marks.py` parses both; never parse one with the other's parser.
- Some Upstox expired strikes have outlier tokens with 0 candles and no alternative — genuinely
  **broker-empty, not a remap bug** (verified). Do not "fix" by re-keying.

**Performance:**
- `options_1m` is 5M+ docs — **never aggregate it on a page-load path**. Use `option_coverage_cache`
  / index-friendly groupings, no `$lookup`.
- The paired-option backtest loads candles under `OPTION_CANDLE_LOAD_CAP` (4,000,000; a cap-hit
  logs + surfaces `candles_capped`). An earlier oldest-first 1M cap silently **dropped the newest**
  candles (0.48.1).
- **Never fork out of the server** — the optimizer pool uses `spawn` (a fork from uvicorn
  segfaulted); spawn workers inherit no strategy registry, so they must load it themselves.

**Frontend:**
- **CRA SPA client-navigation does NOT reload the JS bundle.** After a rebuild you must
  **hard-reload (Ctrl+Shift+R)** or you're testing a stale bundle. Verify the hash:
  `curl -s http://127.0.0.1:3000 | grep -o 'main\.[a-z0-9]*\.js'`.
- lightweight-charts: keep effect deps **stable** (data refs, not freshly-built objects) or it
  disposes + recreates and races autoSize ("Object is disposed").
- **Do not shadow the global `window`** with a local variable (a `const window = useMemo(...)`
  crashed the chart's Fullscreen handler).
- Long-job polling lives in `lib/jobs.jsx` (survives navigation). The browser-screenshot tool
  intermittently times out on canvas-heavy pages — verify via DOM `find`/`read_page` + console.

**pandas / timestamps:**
- The `.venv` is **pandas 3.0.3**: `pd.date_range` yields **µs**-resolution, so `idx.asi8 //
  1_000_000` silently gives epoch-**seconds** not ms. **Pin the unit first**
  (`idx.as_unit("ms").asi8`) before any epoch-ms conversion.
- `closed_at` strings mix `+05:30` and `+00:00` offsets — compare parsed instants
  (`app/trade_time.py`), never strings.

**Live execution:**
- Every price is Decimal-rounded to the scrip tick size `ti` (tick-rounding bug, fixed live).
- The broker can return an order# then async-**REJECT** it — `/live-broker/test-session` reads the
  order book back to resolve rejected entries.
- A resting SL-LMT on a short option **margin-rejects** (~₹1.8L naked-short SPAN an option-buyer
  lacks) — this is why the software guard reads the broker position book and squares in software.
- Join the journal to the broker in the **broker's symbol space** (`noren_tsym`), never the Upstox
  `trading_symbol`: on 2026-08-14 that wrong join journalled an open live position as CLOSED.

**Testing:**
- **Container false-fail class**: tests that string-assert on source by reading `/app/backend/...`
  or `/app/frontend/...` paths fail inside the container because its layout is flattened relative
  to the host repo, and the node-driven tests skip there. Not a regression — the host `.venv` run
  is the gate.
- `sklearn` is load-bearing for the optimizer even though nothing imports it directly — `optuna`
  lazy-imports it. Don't remove it as an apparently-unused dependency.
- A test that checks by AST that a call's **name** appears is not a test of the call: the risk
  supervisor's pause passed three arguments to a two-argument function for seven weeks under such a
  test. Execute the code path.
- When a mutant survives, suspect the fixture before the code: twice in the 2026-09 live work a
  fixture that stored a shallow copy of the deployment doc let a mid-flight write rewrite the
  "stale" doc too, so the race under test never happened.

**Premium-momentum specifics:**
- `option_premium.resolve_premium` returns the tick timestamp under the key **`"ts"`, in seconds**
  — not `"tick_ts"`, and not milliseconds. A caller that reads the wrong key or wrong unit silently
  breaks freshness checks.
- Any option-series lookup by `instrument_key` (backtest premium series, live pin sets, etc.) must
  canonicalize the key first (`instruments.canonical_instrument_key`) — expired-contract metadata
  carries dated 3-part keys while candles are stored under the plain 2-part form. Skipping this
  silently excluded 92/127 real sessions the first time it was missed.

**Subagent / workflow orchestration:**
- A Workflow or subagent panel that returns **0 completed agents** (all dead on a session/token
  limit) is **not** a passed check, even if the aggregate result looks like "0 issues found." Treat
  it as unverified and say so — don't report a false clean.
- When a subagent panel dies repeatedly on session/token limits, the token-efficient move is usually
  to finish the remaining work **inline** rather than keep retrying the same dispatch.
- `Workflow({scriptPath, resumeFromRunId})` replays completed `agent()` calls from cache instantly —
  always resume this way after an interruption instead of re-running a whole script from scratch.

**Git:** `core.autocrlf=true` → harmless CRLF warnings on commit.

---

## I. Conventions

- **Per-changeset push approval.** Commit freely; **push only when the user explicitly says
  "push"**. Nothing is auto-pushed.
- **Branch workflow.** Feature work happens on a branch cut from `main`; merges/pushes are on
  explicit instruction. HANDOFF tracks the current branch state.
- **Never place real broker orders.** The assistant never clicks Place / enables live / squares /
  flips an env gate, and never uses the Flattrade MCP's login, logout or order tools. See §E.
- **Don't add a new arming gate without being asked.** A new strategy or feature rides the existing
  mode / env-gate / caps chain (§E) by default; propose a new gate rather than assuming one is
  wanted — premium-momentum's spec explicitly had one removed on request. Data-integrity gates
  (`live_data_gate`) are a different thing and stay.
- **Restrictive without ceremony, permissive with it.** Hold, tighten, flatten and stop need no
  re-consent; anything that widens real-money exposure (enable, raise a cap) goes through the
  full `/live/enable` ceremony.
- **Unknown is never 0, never "live", never flat.** Every safety gate and status surface fails
  closed and says what it does not know.
- **IST everywhere**; holiday- and session-aware (§F).
- **Premium-never-spot** fills; lot size from `option_contracts.lot_size`; OPEN trades never
  deletable.
- **Route every exit through `execution_policy.py`** (the single source of exit semantics, shared by
  sim + live; stop-first). Do not add a parallel exit decider. When two modules must agree, make
  one call the other.
- **Store raw, derive late.** For data columns, a derived value frozen into a column freezes
  today's definition.
- **Never commit** `.env`, tokens, broker creds, or any credentials file.
- **Batch docs**: one consolidated documentation pass per session, important info only. Preserve
  source-PDF typos in the Flattrade reference verbatim (e.g. `Secondry`) — do not auto-"fix" them.
- **Tests before commit**: host `.venv` suite green + frontend build gate + browser smoke. See §B.
