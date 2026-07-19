# AlphaForge — Developer Guide

The single onboarding guide for a new engineer or AI agent. Read this once and you can take over
the project: how to run/build/test it, the architecture at a glance, the data model, the
**live-trading safety model**, India trading rules, the research → deploy flow, and the gotchas
that will bite you.

> AlphaForge Trading Lab is a **local-first** research + forward-test terminal for **Indian index
> options** (NIFTY / BANKNIFTY / SENSEX). React (CRA + craco) frontend + FastAPI backend + MongoDB
> (motor), all in Docker Compose. **Upstox = market DATA feed; Flattrade (Noren OMS) = live BROKER
> execution.** Everything is IST, holiday-aware, and gated so no real broker order is ever placed
> unless explicitly armed.

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

1. **[docs/HANDOFF.md](HANDOFF.md)** — "START HERE": current state, orientation, and a where-to-go-deep index.
2. **This guide** — the consolidated onboarding.
3. **[CHANGELOG.md](../CHANGELOG.md)** — versioned history (0.1.0 → 0.55.x, newest first). The repo +
   `tests/` are the source of truth, not any prior chat — and check `git log` against the top
   changelog entry before trusting it; doc passes have lagged real commits before.
4. **[docs/ARCHITECTURE.md](ARCHITECTURE.md)** — full module map, data flow, collections, the live gate chain.

Then, by task:

| You are working on… | Read |
|---|---|
| Backend routes | [docs/API_REFERENCE.md](API_REFERENCE.md) |
| The UI, per page | [docs/USER_MANUAL.md](USER_MANUAL.md) |
| A custom strategy | [docs/STRATEGY_PLUGINS.md](STRATEGY_PLUGINS.md) |
| Deployments / forward test | [docs/STRATEGY_DEPLOYMENTS.md](STRATEGY_DEPLOYMENTS.md) |
| The optimizer | [docs/optimizer-user-guide.md](optimizer-user-guide.md), [docs/Walk-forward (honest OOS) what it does exactly.md](<Walk-forward (honest OOS) what it does exactly.md>) |
| Live broker execution | This guide §E, [docs/live-readback-checklist.md](live-readback-checklist.md), [docs/Resources/flattrade-pi-api/INDEX.md](Resources/flattrade-pi-api/INDEX.md) |
| Install / daily launch | [docs/LOCAL_SETUP.md](LOCAL_SETUP.md), [docs/STARTUP_MANUAL.md](STARTUP_MANUAL.md) |
| Continuing from the 2026-07-13 Emergent handoff session | [docs/EMERGENT_SESSION_NOTES.md](EMERGENT_SESSION_NOTES.md) — what landed, what's deferred, non-negotiables preserved |
| Agent capabilities / PDF tooling | [CLAUDE.md](../CLAUDE.md) |

**Golden rule:** ground every change in the actual code. Verify routes exist, module names are
right, numbers are real — do not trust a doc (including this one) over the source.

---

## B. Run / build / test workflow

### The stack

Three services in `docker-compose.yml`:

| Service | Port | Container | Notes |
|---|---|---|---|
| `frontend` | `3000` | `alphaforge_frontend` | React + nginx; `REACT_APP_BACKEND_URL=http://localhost:8001` baked at build |
| `backend` | `8001` | `alphaforge_backend` | FastAPI; **all routes under `/api`**; env from `backend/.env` |
| `mongo` | `27017` | `alphaforge_mongo` | `mongo:7`, named volume `mongo_data` (NOT in the project folder / OneDrive) |

```bash
docker compose up -d --build              # build + launch everything (or start-app.bat / start.sh)
docker compose up -d --build backend      # rebuild ONLY the backend after backend edits
docker compose up -d --build frontend     # rebuild ONLY the frontend after frontend edits
docker compose ps                         # confirm all three are up/healthy
curl -s localhost:8001/api/health         # expect {"db":"ok"} (or similar)
```

**Backend and frontend code is baked into the image** — you MUST rebuild the relevant container
after editing it. There is no hot-reload volume for app code (only `strategies/plugins` is mounted).

### The test pyramid

There are four tiers; run them in this order.

**1. Host pure/contract tests** (`tests/`, the bulk of the suite):

```bash
python -m pytest tests -q
```

These run on the **host** Python. Most tests **never import `server.py`** — motor/pymongo are
absent on the host, so importing the app would fail. Instead the pure logic modules
(`safety.py`, `mode.py`, `survival.py`, `nse_calendar.py`, `exit_controls.py`, `execution_policy.py`,
…) are directly unit-tested, and route/UI shape is pinned by **string-asserting on source** via
`tests/contract_corpus.py` (`backend_api_text()` over server + schemas + runtime + routers;
`warehouse_page_text()` over the warehouse page + components). When you pin a route/testid it can
live in any router/component file. Some tests **do** import motor (e.g.
`test_deployment_evaluator.py`, `test_live_idempotency.py`) — those run inside the container.

**2. Container motor/route tests** — for anything needing a live motor/Mongo:

```bash
docker cp tests/. alphaforge_backend:/app/tests
docker exec -w /app alphaforge_backend python -m pytest tests/test_deployment_live_routes.py -q
# or run the whole suite inside the container:
docker exec -w /app alphaforge_backend python -m pytest tests -q
```

**3. Frontend "tests"** — pytest **string-pins over the JSX source** (they assert the source
contains a testid / label / route, not a rendered DOM). They run in the same `pytest tests` pass.
Additionally the frontend must **compile clean** before you commit a FE change:

```bash
cd frontend && CI=true npm run build      # must succeed; a couple of pre-existing exhaustive-deps warnings are OK
```

**4. Chrome browser smoke** — the final human check. Rebuild the containers, hard-reload the page
(**Ctrl+Shift+R** — see the stale-bundle gotcha in §H), and click through the changed surface with
the devtools console open. `optimizer.py` and other optuna/motor modules are verified in the
**running stack**, not host-imported.

### Rebuild cadence & pre-commit expectations

- Edited backend → `docker compose up -d --build backend`.
- Edited frontend → `cd frontend && CI=true npm run build` **and** `docker compose up -d --build frontend`.
- Before committing: `python -m pytest tests -q` green **and** the FE compiles **and** a browser
  smoke of the changed surface with no console errors.
- `core.autocrlf=true` produces harmless CRLF warnings on commit — ignore them.

---

## C. Architecture at a glance

A short narrative; the full module map + data-flow diagrams are in **[docs/ARCHITECTURE.md](ARCHITECTURE.md)**.

- **Backend** (`backend/`): `server.py` is a thin app factory (startup/shutdown, scheduler wiring,
  CORS, health) that mounts the routers. `app/routers/{research,warehouse,journals,deployments,broker,strategies_admin,live_broker}.py`
  hold the HTTP routes (each `api = APIRouter()`). `app/runtime.py` holds shared singletons + route
  helpers. Import DAG: **server → routers → runtime → business modules** (no cycles; nothing imports
  `server`). Business modules do the real work: `completeness.py`, `data_hygiene.py`,
  `nse_calendar.py` (warehouse); `backtest.py` + `option_backtest.py` + `portfolio.py` +
  `execution_policy.py` + `exit_controls.py` (backtest); `optimizer.py` + `wfo.py` +
  `walkforward.py` + `survival.py` + `rerank_select.py` (optimizer); `deployment_evaluator.py` +
  `paper_auto.py` + `live_exit_monitor.py` (forward test); `auto_live.py` + `app/live/*` (live
  execution); `premium_momentum.py` + `premium_momentum_backtest.py` + `premium_momentum_tuner.py` +
  `premium_momentum_live.py` + `premium_lock_store.py` + `premium_pin.py` (the premium-momentum
  strategy family — time-locked-strike, premium-native-trigger backtest + live/paper execution;
  see §E and §G below and [STRATEGY_DEPLOYMENTS.md](STRATEGY_DEPLOYMENTS.md)).
- **Frontend** (`frontend/src/`): `pages/*.jsx` per page (Dashboard, BacktestLab, Optimizer,
  DataWarehouse, LiveTrading, PaperTrading, StrategyLibrary, journals, checklist); `components/*`
  per subsystem (`warehouse/`, `backtest/`, `live/`); `lib/jobs.jsx` is the global background-job
  tracker (survives navigation, persists run IDs to localStorage); `lib/api.js` is the axios client.
- **MongoDB collections** (motor): `candles_1m`, `options_1m`, `option_contracts`,
  `option_known_empty`, `warehouse_runs`, `option_coverage_cache`, `data_hygiene_latest`,
  `backtest_runs`, `optimization_jobs`, `presets`, `strategy_deployments`, `signals`,
  `paper_trades`, `live_trades`, `ticks`, `upstox_tokens`, `live_mode`, plus the live-order stores.
  See [docs/ARCHITECTURE.md](ARCHITECTURE.md) for the authoritative list.
- **External**: Upstox (OAuth daily-expiry + REST historical + V3 WebSocket ticks) for **data**;
  Flattrade / Noren PiConnect OMS for **live orders** (decoded reference at
  [docs/Resources/flattrade-pi-api/](Resources/flattrade-pi-api/)).

---

## D. Data warehouse model

The warehouse (`candles_1m` = 1-minute OHLCV for the 3 index spots + their ATM-band option
contracts + `INDIAVIX`; `options_1m` = option candles) is judged by **one** definition of
"complete". Read this before touching warehouse code.

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
  (375 for a regular session, 0 for weekend/holiday, reduced for Muhurat) drives the coverage
  heatmap so weekends/holidays are never flagged red. `market_status(now_ist)` is the single
  holiday-aware "is the market open?" source.
- **Partial-day spot repair**: a day captured only partially (PC off mid-session) is re-fetched
  when its stored count is materially below `expected_candle_count`, bounded by
  `SPOT_REPAIR_LOOKBACK_DAYS=21`.
- **Canonical keys**: candles stored under the 2-part `SEGMENT|TOKEN` form
  (`instruments.canonical_instrument_key`); dated 3-part keys live only inside expired-endpoint
  URLs. **Expired routing keys off `expiry_date < today(IST)`**, not provenance.

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

---

## E. Live-trading safety model (read this twice)

This is the most important section. The Live Trading page (Flattrade / Noren OMS) can place **real
money orders**, but the system is **offline-first** and layered so a runaway order is structurally
impossible without a human deliberately arming it. Every guarantee below is enforced **in code** —
the file/line is cited so you can verify it.

### Who else holds the broker session: the Flattrade MCP (v0.55.2)

Before anything else in this section, know that **AlphaForge is no longer the only thing on this
broker account**. The user has the official **Flattrade Trading MCP** server installed (a separate,
closed-source binary giving an AI assistant conversational read/write access to the live account).
Because Flattrade's API V2 allows **one API key per account**, and that key holds the one redirect
URI AlphaForge owns, the MCP **cannot complete its own OAuth** — so AlphaForge is the **sole OAuth
owner** and mirrors its jKey into the MCP's session file after every login
(`live/mcp_session_sync.py`, gated by `FLATTRADE_MCP_SESSION_DIR`, wrapped so a sync failure can
never break the login).

Three consequences that matter when you touch live code:

1. **`live_broker_tokens` is now the session authority for two consumers.** If you change token
   storage, expiry handling, or the callback, keep the sync call intact (pinned by
   `tests/test_mcp_session_sync.py`) or the MCP silently goes stale.
2. **Positions opened through the MCP are invisible to the guard, OCO backstop, SL monitor and
   kill switch** — those reconcile against AlphaForge's own intent store, and a foreign order has
   no intent record. This is an explicit, user-accepted trade-off; never assume a broker position
   is AlphaForge-protected just because you can see it.
3. **The PiConnect rate budget is per key and now shared** (40 req/s, 200/min; orders 10/s,
   40/min). Guard/reconcile polling is safety-critical — MCP chatter competes with it.

**Never call the MCP's `login`/`logout` tools** (login would invalidate AlphaForge's token —
Flattrade is last-login-wins; logout wipes the shared session). Recovery is
`backend/scripts/resync_mcp_session.py --clean`. Full detail:
[`flattrade-mcp-integration.md`](flattrade-mcp-integration.md).

### The single order chokepoint

**All real entries go through `app/live/executor.py`.** There are exactly two public entry
functions and they share the **one and only** `client.place_order(...)` call site
(`_transmit_and_arm`, `executor.py`):

- `place_live_test_order(...)` — the **manual** single-shot ticket path.
- `place_deployed_order(...)` — the **live-deployment** auto-place path.

No other module may call `client.place_order` for an entry. If you add a live feature, route it
through the executor — do not open a second placement path.

### The one hard, offline-first env kill (v0.56.0: down from two)

Nothing transmits a real **entry** order unless the operator has explicitly flipped a host env var,
which defaults to OFF:

| Env var | Gates | Default | If unset |
|---|---|---|---|
| `LIVE_AUTOPLACE_ARMED` | **auto entries** (deployment path) | `0` | a live-mode deployment still only **dry-runs** — builds + validates the full intent, transmits nothing |

`executor._autoplace_armed()` reads `LIVE_AUTOPLACE_ARMED` and accepts only `1/true/yes/on`;
anything else (including unset) means dry-run. This is the "safe by default even if a deployment is
switched to live" backstop — and it is now the **sole** remaining transmit env gate.

**`LIVE_GUARD_ARMED` is REMOVED.** The software exit guard (stop/target/trailing squares and its
Layer-2 widening re-price) now **always transmits** — a deployed strategy's own exits are part of the
strategy, not an opt-in extra. This closes a dangerous split (audit finding L20) where real entries
could transmit while the automated exit guard only logged, leaving the broker OCO as the sole
automated backstop. Any surviving reference to `LIVE_GUARD_ARMED` — in env files, code comments, or
older docs — is stale: the variable no longer exists and nothing reads it.

### Manual path gate chain (`place_live_test_order`)

Enforced in this exact order (`executor.py`), any failure returns `placed:false` with **no broker
contact**:

0. **Long-only** — `side` must be `"B"` (a sell entry would open an unprotected naked short whose
   sell-to-close SL would *grow* the short). Rejected before any broker call.
1. **Mode gate** (`mode.is_live_order_allowed`) — must be `LIVE_TEST` **with an unconsumed
   single-shot**. PAPER / LIVE_OFFLINE / LIVE_ARMED / missing / malformed all fail closed.
   `LIVE_ARMED` is an L4 concept and is explicitly rejected in the L3 gate.
2. **Fresh server-side dry-run** — `build_intent` with **`lots` hard-pinned to 1** and
   `fat_finger_cap` clamped to ≤ 1 (the `lots` param is not even exposed to callers), plus a margin
   verdict.
3. **`qty == lot_size`** defense-in-depth — confirm exactly one lot regardless of what
   `build_intent` computed (`not_one_lot` otherwise).
4. **Engine gate** — `engine.can_trade()` must return `(True, …)`.
5. **Idempotency claim** — `intent_store.claim_for_submit(cid)` must return `True` (prevents dup).
6. **THE ONLY `place_order` call.**
7. **Post-fill arm-or-abort** — `mark_submitted` → `consume_single_shot` (self-locks the single
   shot) → `arm`. If **any** post-fill step raises, `_abort_protect` drives a best-effort square +
   engine halt so **no unprotected live position can persist**.

### Live-deployment path gate chain (`place_deployed_order`)

Same skeleton, but authorized by the deployment's **mode**, not a per-session arm record — the ARM
ceremony was **removed in v0.56.0** by explicit user decision: deploying a strategy in live mode IS
the authorization, and the strategy's own entry/exit/SL/TP/trailing logic drives execution with the
resting broker OCO as the PC-down backstop.

- **Authorization** = `mode.is_deployment_live_allowed(deployment, now, connected=…)` — requires
  `deployment.mode == "live"`, the broker connected, and `now` before the **15:00 IST daily
  new-entry cutoff** (`mode.entry_cutoff_today_ist`, an alias for the pre-existing
  `armed_until_today_ist`). **Fail-closed** on any missing/malformed field or an unresolvable
  cutoff. There is no arm record and no arm expiry — `mode == "live"` persists across sessions
  until a human disables/stops it; only the entry side is time-boxed per day.
- **Lots** = `capped_lots`; `fat_finger_cap` = the **account ceiling** (`account_max_lots`, config
  default 20 via `max_lots_per_order`), and margin must cover the **full** `capped_lots * lot_size`.
  A broker `GetOrderMargin` pre-trade gate (`broker_margin_verdict`) fails **closed** on a broker
  reject.
- **Lot-cap defense-in-depth** — `capped_lots ≤ account_max_lots` and the built qty equals exactly
  `capped_lots * lot_size` (`not_within_lot_cap` otherwise).
- **Transmit boundary is offline-first** — unless `LIVE_AUTOPLACE_ARMED` is on, it returns the
  validated `would_send` jdata and transmits nothing.
- **Rate throttle** (`safety.RateThrottle`, real-transmit path only) — a token bucket capped at 9
  orders/sec to stay under the SEBI 10/sec limit. **Cancels/exits are never throttled** (throttling
  an exit would trap a losing position).

### Per-deployment caps governor (`app/live_deploy_governor.py`)

Before a deployment opens a new live trade, `check_live_caps` enforces (first match wins):

1. `daily_loss_cap` (₹ magnitude) → block **and pause**: sets `status="PAUSED"`, reverts
   `mode` to `"paper"`, and stamps `risk.live.last_block_reason="daily_loss"` — `status=PAUSED` is
   what actually stops re-entry (`evaluate_all` only iterates `{"status": "ACTIVE"}`); the
   mode/reason fields are audit trail, not authorization.
2. `max_lots_per_day` (rolling IST day) → block.
3. `max_concurrent` (open live trades) → block.

**Fails CLOSED for `mode == "live"` with no caps configured** (`reason: "live_caps_missing"`)
rather than the old allow-all fast path. `POST /live/enable` is the only route that can set
`mode="live"` and it already REQUIRES `lots`/`max_lots_per_day`/`max_concurrent` ≥ 1, but the
governor no longer trusts that as a given — a crafted or migrated doc with `mode == "live"` and no
caps is refused rather than traded unbounded.

`auto_live.py` calls the governor, then `is_deployment_live_allowed`, then
`executor.place_deployed_order`.

### Daily entry cutoff & kill switches

- **Daily new-entry cutoff**: `is_deployment_live_allowed` refuses a new entry from 15:00 IST
  (`mode.entry_cutoff_today_ist`) — the same instant the EOD auto-square runs — so a live
  deployment can never open a real position minutes before it would be squared. This replaces what
  the old `risk.live.armed_until` expiry enforced as a side effect of the (now-removed) per-session
  arm ceremony.
- **Manual kill**: `POST /deployments/{id}/live/stop` flattens that deployment's live positions,
  reverts `mode` to `"paper"`, and sets `status="PAUSED"` (the actual halt —
  `evaluate_all` only iterates `ACTIVE`); `POST /deployments/stop-all` does the same for every
  live deployment (selector `{"mode": "live"}`, not the old `{"risk.live.armed": True}`). Paper
  deployments have their own circuit breakers (`app/deployment_kill_switch.py`:
  `max_consecutive_losses`, `daily_loss_cutoff_pct`, `max_open_paper_trades`).

### The catastrophe backstop (PC-down)

Because a resting SL-LMT on a short option margin-rejects, the software exit guard
(`app/live/live_position_guard.py`, started in `server.py` lifespan) reads the **broker** position
book ~1.5s and squares in software via a margin-safe cancel-all-then-close — and **always
transmits** (the `LIVE_GUARD_ARMED` env gate was removed in v0.56.0: a deployed strategy's
stop/target/trailing exits are part of the strategy, not an opt-in). For the **PC-died** case, an
**NRML resting GTT/OCO** (`app/live/gtt.py`,
schema from the vision-verified PiConnect catalog) sits at the broker with no margin cost:
`GET /live-broker/gtt` lists it, `POST` builds + transmits **only on explicit `transmit=true`**,
`DELETE` cancels. The `ai_t` values were confirmed by reading the user's own placed orders back.

### Broker-truth integrity — a read failure is UNKNOWN, never flat

`BrokerReadError` (`live/broker_protocol.py`) is raised by the Flattrade readers whenever the Noren
API returns `stat != Ok` — **except** the documented "no data" empty-book signal, which is a real
zero, not a failure (`_is_no_data`/`_parse_book` in `flattrade_client.py`). Every consumer (kill
route, both square paths, the guard cycle, the executor's pre-transmit limit gates, the blotter)
treats a read error as **UNKNOWN**, not FLAT and not squared — the alternative (treating an
expired-session error response as an empty position book) is how a live position could go
completely unmonitored while everything *looks* green. If you add a new broker-read consumer, it
must fail closed (hold / block / mark UNKNOWN) on `BrokerReadError`, never coerce to flat.

### The kill switch is a true stop-all, and exits are serialized against double-selling

`kill_switch._run_kill_switch` trips the account-wide safety latch (`engine.can_trade()` goes false)
**before** it does anything else, halts the engine, and takes every live deployment out of live mode
(`_disarm_all_live_deployments`: selector `{"mode": "live"}`, sets `mode="paper"` +
`status="PAUSED"` — not the old `{"risk.live.armed": True}` filter, which would now silently match
nothing) — only then does it flatten. All three exit paths (the software guard, the kill switch,
and a manual/deployment square) funnel through `live/exit_claims.py`, a per-tsym asyncio-lock claim
registry with a TTL: a
second path trying to exit a tsym another path already claimed gets `exit_in_flight_elsewhere`
instead of racing a double-sell. Two additional double-sell windows were closed after being found
by adversarial review (2026-07-11/12), and the pattern they establish should be followed by any new
exit code:

- **Lost-ack adoption.** A `place_order` call that raised (timeout/network) may have actually landed
  at the broker. Before any retry, resolve `remarks == client_order_id` against the order book
  (`kill_switch._scan_order_by_remarks`): if it landed, **adopt it, never re-post**; if the book
  can't be read, **fail closed** (no retry) rather than guess. All three exit executors
  (`auto_square`, `reprice_exit_leg`, `panic_squareoff_verified`) follow this.
- **Cancel-confirm barrier.** Before placing a flatten/reprice order, every cancel it depends on must
  be independently confirmed **terminal** (one re-fetch) — don't place a square order on the
  assumption that a cancel you just sent has already landed.

### Recovery re-runs on every fresh token, not just at boot

`runtime.maybe_run_live_recovery` is triggered from three places — process boot, the OAuth callback
(so a token obtained *after* boot still triggers a recovery pass), and the supervisor loop (so a
transient failure gets retried) — gated by a **per-token latch** keyed on the token fingerprint. The
latch is only recorded as success when the run is truly **complete** (client present, no step
raised, the position book was actually readable) — an earlier version latched green on a merely
*attempted* run, which could leave a real position unguarded while recovery believed it had already
handled it. If you touch recovery, preserve the completeness-gated latch; a "ran but incomplete"
state must be retried, never remembered as done.

### The guard fails open, never silently drops a position

If `live_position_guard`'s square retries are exhausted, the position is **re-added** to the
registry (`readd`) in an escalated `square_stopped` state rather than quietly un-watched — the
broker-side OCO/GTT backstop and the position's `status.stuck` flag remain the safety net, and an
operator is expected to notice the escalation. The **EOD 15:00 IST square explicitly bypasses**
`square_stopped` (a no-OCO manual/rehydrated position must still get its end-of-day flatten even if
its earlier retries were exhausted).

### `auto_square.py`'s manual 10-minute timer is gone — EOD is the only manual backstop

A past design had a hard 10-minute auto-square cap for manual `LIVE_TEST` positions. It was
**removed** (see `docs/superpowers/specs/2026-07-09-remove-manual-livetest-10min-timer-design.md`):
deployed strategies already exit on their own rules plus a resting OCO, and for a manual position
the 15:00 IST EOD square is now the sole time-based backstop. `auto_square.build_sl_backstop_intent`
and `square_position` remain — the executor + SL-backstop builder — but do not resurrect a
resting-timer concept if you touch this file; read its module docstring first, it's deliberately
detailed about what was removed and why.

### Premium-momentum: a strategy driven by a locked strike + premium trigger, not spot

`premium_momentum` is architecturally different from every other strategy: instead of
`strategy.evaluate()` reading spot candles, `deployment_evaluator.py` has a **dedicated branch** for
`strategy_id == "premium_momentum"` that calls `premium_momentum_live.evaluate_premium_momentum_bar`
per bar. At a configurable reference time it locks the CE/PE strike from spot and captures each
side's premium from fresh WS ticks into a new `premium_locks` collection (unique per
`(deployment_id, session_date)`, create-once / duplicate-key-adopt for crash safety); the first side
whose premium crosses the momentum threshold journals a signal and — **only after that journal
succeeds** — the trigger is atomically latched (a failed latch downgrades the outcome so nothing
trades on a journaled-but-unlatched signal). Exits can use a new `stepped_xy` guard trail mode
(`live_sl_monitor.py`) — an AlgoTest-style discrete ratchet (raise the stop by Y for every X of
favorable move), sourced from `deployment.risk.exit_controls`. Backtest and live share correctness
through **shared pure helper functions** (`lock_reference_strike`, `momentum_triggered`,
`stepped_trail_stop`, …), not a shared loop — the backtest is a self-contained option-native sim
precisely to avoid the two-stage engine's spot-re-resolution of a drifting strike.

**There is no premium-momentum-specific arming gate.** It authorizes through the exact same
`mode == "live"` + `LIVE_AUTOPLACE_ARMED` + caps-governor chain as every other strategy — this was
an explicit user decision (an earlier spec draft had a 10-paper-session validation gate; it was
removed on request). **Do not add one back "for safety."** Locked strikes are pinned into
every option subscription-stream rebuild (both the auto-follow path in `runtime.py` and the manual
restart route in `routers/broker.py` — a new stream-rebuild site must union in `premium_pin_keys`
too, or a locked strike can silently drop off the tick feed mid-session). Recovery
(`rehydrate_premium_momentum`) re-registers guard entries for already-entered locks using the
**persisted entry premium**, and skips any lock whose order id or resolved trading symbol is
already in the registry's watched set — recovery/supervisor retries are routine, and without this
guard a re-run could double-watch one position under two keys (two independent stop evaluations,
two full-qty square orders on a fast gap).

Since v0.55.0 the family also executes **multi-leg** (`leg_mode: "both"`: CE+PE independent
primaries, one-shot lazy reversal leg off STOP-class guard exits, per-deployment `exit_time`
squares, realized-only day-stop, VIX gate) — see `STRATEGY_DEPLOYMENTS.md` → "Multi-leg mode
(Phase 5B)" for the config keys and the three load-bearing invariants (normalize_hhmm everywhere;
whole-doc finalize only when nothing is unresolved incl. a freshly-armed lazy leg; recovery symbols
come exclusively from the broker order-book join, never the persisted Upstox symbol). Two
architectural facts worth internalizing: the 5B **exit** machinery (lazy arming, exit_time,
per-leg finalize, recovery join) lives entirely in the **live guard** — paper exits ride the
separate LiveExitMonitor and never touch premium locks — and the family **failed its pre-registered
edge gate** (`docs/PREMIUM_MOMENTUM_EDGE_VERDICT_2026-07.md`); 5B exists as a user-decided pure
capability, with the verdict surfaced as an informational arm advisory.

### The one invariant that never changes

**The assistant never personally transmits or squares a real order.** It builds 100% of the code,
but authorizing real money — switching a deployment to `mode == "live"` via `POST
/deployments/{id}/live/enable`, flipping `LIVE_AUTOPLACE_ARMED`, or a manual Place click — is always
a human action. Never bypass the executor, never remove a gate, never default an env kill to on.
See [docs/live-readback-checklist.md](live-readback-checklist.md) before any live session.

---

## F. India trading rules & calendar

Locked conventions — verify against the code, don't guess:

- **IST everywhere.** All timestamps, cutoffs, and calendar logic use `Asia/Kolkata`
  (`IST = timezone(timedelta(hours=5, minutes=30))`).
- **Regular session 09:15–15:30 IST** = 375 one-minute candles
  (`nse_calendar.REGULAR_SESSION_CANDLES`, `SESSION_OPEN_MIN`, `SESSION_CLOSE_MIN`).
- **Signal window** (`deployment_evaluator.py`): entries blocked 09:15→09:25 (`BLOCK_OPEN_UNTIL`)
  and from 14:50 (`BLOCK_CLOSE_FROM`). **15:00 IST square-off** every trading day
  (`risk.allow_overnight` opts out); **expiry-day cutoff 15:00 IST** from
  `option_contracts.expiry_date` (never weekday-hardcoded).
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
   `search_exit_controls` sweeps exit configs per survivor. **Gate: only survivors advance.**
2. **Backtest Lab** (`backtest.py` + `option_backtest.py` + `portfolio.py`): re-run a config as a
   paired real-option-candle backtest with honest ₹-first metrics (CAGR/Calmar suppressed under a
   ~1-year window; Profit÷maxDD is the headline). Optional exit/risk-control overlay
   (`exit_controls.py`: trailing / breakeven / daily caps). **Gate: does the ₹ equity curve survive
   OOS + full-window?**
3. **Preset / run** → **Deployment** (`strategy_deployments`): a deployment is created only from a
   saved preset or a backtest run; creatable modes are `signal_only | paper` (`live` can never be
   requested at creation — it is reached only via the preflighted `/live/enable` route in step 5).
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

**Empirical note** (memory `option-buying-edge-hunt-2026`): a disciplined survival-gated sweep over
confluence / SEB / ORF found **no deployable option-buying survivor** — the bottleneck was
directional signal quality, not theta/moneyness. The framework refusing to promote a money-loser is
it working as intended.

---

## H. Gotchas & known issues

Real operational lessons pulled from code comments, HANDOFF, and CHANGELOG. Read the relevant one
before touching that area.

**Upstox (data):**
- 30-day chunks crossing Feb→Mar give `400 Invalid date range` — use `chunk_days=7`.
- Historical is **empty for the in-progress day** (the live candle roller closes it).
- F&O history publishes with a **lag** — never trust same-night completeness (hence the broker-empty
  ledger grace rule).
- Expired options need `/v2/expired-instruments/...` (normal V3 returns `UDAPI100011`).
- `GLOBAL_INDICATOR|USDINR` REST quote 400s but works on WS. WS subscription set is fixed at connect
  (stop + restart to change).
- **Stream ≠ roller**: the tick→candle roller auto-starts only at boot **if the Upstox token is
  valid**; the daily OAuth + the "Start Stream" button do **not** start it. No roller ⇒ 0 intraday
  candles ⇒ the evaluator's new-bar gate never opens (root cause of "paper deployment ACTIVE all
  day, 0 trades"). Confirm the roller is running, not just the stream.

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
- Historical saved runs were repaired by `backend/scripts/repair_option_leg_index.py` (reversible
  via `option_backtest.index_remap_backup`).

**Contract correctness:**
- Always filter `expiry_date >= today` when picking a live contract; `select_contract_for_signal`
  is exact-match-or-None (regression-pinned).
- **NSE/BSE reuse exchange tokens across expiry cycles.** ~30 canonical 2-part keys
  (`NSE_FO|46181`) in `options_1m` carry candles for two *different* contracts — verified
  strictly time-disjoint, so time-windowed reads are always correct, but any lookup by 2-part key
  that is NOT time-windowed or expiry-constrained can silently return the wrong strike. (Audited
  2026-07-18: 63,868 contracts, zero symbol↔strike/side/expiry mismatches, zero orphan candle
  keys — the warehouse itself is clean.)
- Some Upstox expired strikes have outlier tokens with 0 candles and no alternative — genuinely
  **broker-empty, not a remap bug** (verified). Do not "fix" by re-keying.

**Performance:**
- `options_1m` is 5M+ docs — **never aggregate it on a page-load path**. Use `option_coverage_cache`
  / index-friendly groupings, no `$lookup`.
- The paired-option backtest loads candles under `OPTION_CANDLE_LOAD_CAP` (raised to 4,000,000; a
  cap-hit now logs + surfaces `candles_capped`). An earlier oldest-first 1M cap silently **dropped
  the newest** candles (0.48.1).

**Frontend:**
- **CRA SPA client-navigation does NOT reload the JS bundle.** After a rebuild you must
  **hard-reload (Ctrl+Shift+R)** or you're testing a stale bundle. Verify the hash:
  `curl -s localhost:3000 | grep main.*.js`.
- lightweight-charts: keep effect deps **stable** (data refs, not freshly-built objects) or it
  disposes + recreates and races autoSize ("Object is disposed").
- **Do not shadow the global `window`** with a local variable (a `const window = useMemo(...)`
  crashed the chart's Fullscreen handler).
- Long-job polling lives in `lib/jobs.jsx` (survives navigation). The browser-screenshot tool
  intermittently times out on canvas-heavy pages — verify via DOM `find`/`read_page` + console.

**pandas / timestamps:**
- The `.venv` is **pandas 3.0.3**: `pd.date_range` yields **µs**-resolution, so `idx.asi8 //
  1_000_000` silently gives epoch-**seconds** not ms. **Pin the unit first**
  (`idx.as_unit("ms").asi8`) before any epoch-ms conversion (memory `pandas3-resolution-epoch-trap`).

**Live execution:**
- Every price is Decimal-rounded to the scrip tick size `ti` (tick-rounding bug, fixed live).
- The broker can return an order# then async-**REJECT** it (phantom-timer) — `/test-session` reads
  the order book back to resolve rejected entries.
- A resting SL-LMT on a short option **margin-rejects** (~₹1.8L naked-short SPAN an option-buyer
  lacks) — this is why the software guard reads the broker position book and squares in software,
  and why the PC-down net is an NRML GTT/OCO (no margin cost) rather than a resting SL.

**Testing:**
- There is a recurring, **documented false-fail class** in the container test run: tests that
  string-assert on source by reading `/app/backend/...` or `/app/frontend/...` paths (e.g.
  `TestGuardWiringContract`, several `test_premium_*` source-pin tests) fail inside the container
  because its layout is flattened relative to the host repo. This is NOT a regression — judge
  correctness by whether the **motor/route** tests pass in the container and the **same** tests pass
  on the **host**; don't chase these specific failures.
- `sklearn` is load-bearing for the optimizer even though nothing imports it directly — `optuna`
  lazy-imports it. Don't remove it as an apparently-unused dependency.
- Running the **full** suite inside the container will always show a handful of reds from the
  path-contract tests above — judge the container run by the motor/route subset you actually care
  about, not a full-suite pass/fail count.

**Premium-momentum specifics:**
- `option_premium.resolve_premium` returns the tick timestamp under the key **`"ts"`, in seconds**
  — not `"tick_ts"`, and not milliseconds. A caller that reads the wrong key or wrong unit silently
  breaks freshness checks (this was a real bug caught by review, not hypothetical).
- Any option-series lookup by `instrument_key` (backtest premium series, live pin sets, etc.) must
  canonicalize the key first (`instruments.canonical_instrument_key`) — expired-contract metadata
  carries dated 3-part keys while candles are stored under the plain 2-part form. Skipping this
  silently excluded 92/127 real sessions the first time it was missed.

**Subagent / workflow orchestration:**
- A Workflow or subagent panel that returns **0 completed agents** (all dead on a session/token
  limit) is **not** a passed check, even if the aggregate result looks like "0 issues found." Treat
  it as unverified and say so — don't report a false clean.
- When a subagent panel dies repeatedly on session/token limits, the token-efficient move is usually
  to finish the remaining work **inline** (the orchestrator already has full context; a fresh
  subagent has to re-derive it) rather than keep retrying the same dispatch.
- `Workflow({scriptPath, resumeFromRunId})` replays completed `agent()` calls from cache instantly —
  always resume this way after an interruption instead of re-running a whole script from scratch.

**Git:** `core.autocrlf=true` → harmless CRLF warnings on commit.

---

## I. Conventions

- **Per-changeset push approval.** Commit freely; **push only when the user explicitly says
  "push"**. Nothing is auto-pushed.
- **Branch workflow.** Feature work happens on a branch cut from `main`; merges/pushes are on
  explicit instruction. HANDOFF tracks the current branch stack.
- **Never place real broker orders unless explicitly armed.** The assistant never clicks Place /
  arms / squares. See §E.
- **Don't add a new arming gate without being asked.** A new strategy or feature should ride the
  existing arm/gate/cap chain (§E) by default; propose a new gate rather than assuming one is
  wanted — premium-momentum's spec explicitly had one removed on request.
- **IST everywhere**; NSE session 09:15–15:30 with 15:00 square-off; holiday-aware.
- **Premium-never-spot** fills; lot size from `option_contracts.lot_size`; OPEN trades never
  deletable.
- **Route every exit through `execution_policy.py`** (the single source of exit semantics, shared by
  sim + live; stop-first). Do not add a parallel exit decider.
- **Never commit** `.env`, tokens, broker creds, or any credentials file.
- **Batch docs**: one consolidated documentation pass per session, important info only (the user
  wants tokens saved on doc churn). Preserve source-PDF typos in the Flattrade reference verbatim
  (e.g. `Secondry`) — do not auto-"fix" them.
- **Tests before commit**: `python -m pytest tests -q` green + FE compiles + browser smoke. See §B.
