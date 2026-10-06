# Handoff — START HERE

_Entry point for the next engineer or AI agent. The repository, `tests/` and `git log` are the
source of truth; this file says where to look and what will bite you. Rewritten 2026-09-30._

**Read order (the same chain everywhere):** [`../README.md`](../README.md) → **this file** →
[`agent-takeover-prompt.md`](agent-takeover-prompt.md) → [`AGENT_TODO.md`](AGENT_TODO.md) (the only
live work board) → the domain doc you need from [§5](#5-where-to-go-deep). Before trusting any number
the app produces, read [`BACKTEST_INTEGRITY_AUDIT.md`](BACKTEST_INTEGRITY_AUDIT.md).

---

## 1. Orientation

**AlphaForge Trading Lab** is a local-first research, paper and live-trading lab for Indian index
options — NIFTY and SENSEX weekly options (BANKNIFTY is also supported). The loop: warehouse 1-minute
spot + option candles → backtest / optimize / walk-forward a strategy → save a preset → deploy it
(signal-only or paper by default; live only when the operator enables it) → the software exit guard
manages what it opened.

Stack: **React** (CRA + craco) frontend `:3000`, **FastAPI** backend `:8001` (**every route under
`/api`**), **MongoDB** (motor) `:27017`, all in **Docker Compose** with every port bound to
`127.0.0.1`. **Upstox** = market data (historical + streaming). **Flattrade** (Noren / PiConnect OMS)
= live execution: static IP required, daily OAuth, limit / SL-limit orders only. The official
Flattrade MCP shares the same broker session (§4.1). Technical architecture:
[`ARCHITECTURE.md`](ARCHITECTURE.md).

### 1.1 Where things live

**Pages** (`frontend/src/App.js` → `pages/`; nav labels in `components/Layout.jsx`):

| Route | Page | What it is |
|---|---|---|
| `/backtest` | Backtest Lab | Run a backtest; the **Backtest run journal** lists saved runs, each with a Trades pane and Config / Result / Trades.csv / Save-as-preset / Deploy |
| `/optimizer` | Optimizer | Optimization setup + its own saved-job history (separate from the Backtest journal) |
| `/presets` | Saved Presets | Named strategy params + option execution policy — the deployable artifact |
| `/warehouse` | Data Warehouse | 1-minute spot + option candle coverage, gap fill, hygiene |
| `/live` | Deploy Strategies (`LiveSignals.jsx`) | The deploy wizard: preset or library entry → deployment |
| `/live-trading` | Live Broker (`LiveTrading.jsx`) | Execution cockpit: Live Deployments pane, positions, guard health, kill switch |
| `/paper` · `/journal` | Paper Trading · Signal Journal | Paper blotter/analytics · every signal and its lifecycle |

Others: `/` Dashboard, `/strategies` Strategy Library, `/checklist` Pre-Trade Checklist,
`/premium-momentum`.

**Backend:** `backend/app/` (engines at the top level, `live/` for broker execution, `routers/` for
the eight routers mounted under `/api`: `research`, `strategies_admin`, `warehouse`, `journals`,
`deployments`, `broker`, `live_broker`, `premium_momentum_routes`), `backend/app/strategies/{builtin,plugins}`,
`backend/scripts/` (host scripts). Pure frontend logic lives in `frontend/src/lib/*.js` and is tested
by executing it under node.

**Data** (MongoDB db `alphaforge`, container `alphaforge_mongo`, named volume `mongo_data` — NOT
inside the project/OneDrive folder):

| Collection | Holds |
|---|---|
| `backtest_runs` | every saved backtest — `metrics`/`trades` (spot) **and** `option_backtest.*` (the real result for a premium-native run); what the Backtest journal lists |
| `optimization_jobs` | `config`, `param_space` (the bounds actually searched), `incumbent_seeds`, `best_params`, `best_metrics`, `trial_log`, `top_n_alternatives` |
| `presets` | saved presets (`config.params` + `config.execution`) |
| `candles_1m` / `options_1m` / `option_contracts` | the warehouse: spot candles (incl. INDIAVIX), option candles, contract universe |
| `strategy_deployments` | deployments (`mode`: signal_only / paper / live; `risk.live.*` caps and hold) |
| `signals` · `paper_trades` · `live_trades` | signal lifecycle · paper journal · real-money journal |
| `premium_locks` | premium-momentum per-session strike locks and leg state |

Read directly: `docker exec alphaforge_mongo mongosh alphaforge --quiet --eval '<js>'` (in Git Bash a
JS regex literal starting with `/` gets path-mangled — use `new RegExp("...")`).

### 1.2 The four flows worth knowing before you touch anything

1. **Candles.** Ticks → `live_candle_roller` → `candles_1m`. The roller only aggregates ticks it
   witnesses, so a mid-session boot leaves a hole; `candle_recovery` closes it from REST at startup
   and from the feed supervisor (Upstox V3 intraday first, Flattrade TPSeries fallback). **WebSockets
   cannot supply history**, and Upstox intraday serves only the three index keys — there is no
   same-day source for OPTION 1-minute candles.
2. **Signal → order.** `deployment_evaluator` (closed 1-minute bar) → `auto_live`
   (`resolve_live_exit_plan`) → `live_deploy_context.arm_for` → `live/executor` (the **single
   real-order chokepoint**) → `live/live_position_guard` registers the position.
3. **Exits.** The in-process software guard is the real protection: premium stop, target and the
   `exit_controls` overlay via `exit_controls.effective_premium_stop` — the SAME decider the backtest
   sim and paper use (`live/live_sl_monitor` delegates to it). The broker OCO was only ever a PC-down
   backstop, it did not rest on this account, and it is now off by default (§2.5 T1).
4. **Two symbol spaces.** Upstox `trading_symbol` (`NIFTY 24300 PE 18 AUG 26`) vs Noren `noren_tsym`
   (`NIFTY18AUG26P24300`). Joining them wrongly is a recurring real-bug source — it once journalled
   an open live position as CLOSED. SENSEX (BFO) symbols share no format with NIFTY's.

## 2. Current state (2026-09-30)

> **Git:** `origin/main` = `13f06f4` (pushed 2026-09-29). Local `main` is ahead by `6c949ad`
> (signal retirement) plus the 2026-09-30 cleanup/docs changes — the operator approves every push
> (`git log origin/main..main --oneline` shows what is local). Only `main` exists; the three merged
> agent worktrees were removed 2026-09-30.
> **Suite:** 6,586 passed, 4 xfailed, 0 failed on the host `.venv` (2026-09-30). There is no CI.
>
> **⚠ There is NO PC-down net.** The resting broker OCO is off by default since 2026-09-03
> (`LIVE_BROKER_OCO_ENABLED`); the software guard is the only exit protection and runs only while
> the backend runs.
>
> **⚠ Nothing built since 2026-09-26 has run in a market session.** The broker token was expired
> and the operator was off the static IP for the whole of that work.

### 2.1 What the app does today

| Subsystem | What it does | Key code (`backend/app/`) |
|---|---|---|
| Data Warehouse | 1-minute OHLCV for the indices + INDIAVIX (`candles_1m`) and ATM-band option contracts (`options_1m`); holiday-aware NSE calendar; completeness model; Sync + auto-update; any-day gap recovery | `completeness.py`, `data_hygiene.py`, `nse_calendar.py`, `candle_recovery.py`, `routers/warehouse.py` |
| Backtest Lab | Spot backtests, paired real-option-candle backtests and premium-native (option-only) runs; rupee-first metrics; optional exit/risk overlay | `backtest.py`, `option_backtest.py`, `exit_controls.py`, `execution_policy.py`, `premium_trigger_dispatch.py` |
| Optimizer | Optuna TPE / Grid / Genetic; Stage 1 ranks on spot, top-K re-ranked on real option ₹; walk-forward; incumbent seeding; survival screen | `optimizer.py`, `wfo.py`, `walkforward.py`, `survival.py`, `rerank_select.py`, `incumbent_seed.py` |
| Strategy Library | Builtin + drop-in plugins; retire/delete lifecycle; AI authoring (Anthropic + Gemini; Spec, capability-aware and full-Python tiers) | `strategies/`, `routers/strategies_admin.py`, `ai/` |
| Deployments | Immutable snapshot (version, params, source SHA pinned); evaluated on the closed 1-minute bar; modes signal_only / paper / live | `deployment_evaluator.py`, `routers/deployments.py` |
| Paper | Tick-woken exits, top-of-book P&L after charges, account-capital gate, forward metrics and the pre-registered promotion policy | `paper_auto.py`, `paper_trading.py`, `live_exit_monitor.py`, `forward_metrics.py`, `forward_validation.py` |
| Live (Flattrade) | Auto entries need `LIVE_AUTOPLACE_ARMED=1` **and** `mode == "live"` **and** broker connected **and** before the 15:00 IST entry cutoff, within per-deployment and account caps; tick-primary software guard; boot/OAuth reconcile; kill switch; Greeks; pre-open readiness at 08:45 | `live/executor.py`, `auto_live.py`, `live/mode.py`, `live_deploy_governor.py`, `live/live_position_guard.py`, `live/reboot_reconcile.py`, `live/kill_switch.py`, `preopen_readiness.py` |
| Premium momentum | Time-locked strike + real option-premium trigger; multi-leg live/paper (both legs, one-shot lazy reversal, `exit_time`, realized-only day-stop, VIX gate). A capability — **no demonstrated edge** | `premium_momentum*.py`, `premium_lock_store.py`, `strategies/plugins/premium_momentum.py` |

**Real money:** the operator has enabled live trading. `live_trades` holds 13 journal rows on seven
days, 2026-08-04 → 2026-09-16, all CLOSED (checked 2026-09-30). **No strategy has a demonstrated
edge** — see the closed verdicts in §5.2.

**Live posture — check it, do not assume it.** Last verified 2026-09-29: no deployment was in live
mode (the Live Deployments pane read "0 can trade · 9 not live") and the Flattrade session had
expired. Before anything that could reach the broker or restart the backend, read the current state:
`curl -s http://127.0.0.1:8001/api/live-broker/arm-state` (connected / session expired / whether an
entry or exit would transmit), `curl -s http://127.0.0.1:8001/api/deployments/overview` (each
deployment's `mode`), and the two position reads in §3.

### 2.2 Verified vs not verified

| Verified | How |
|---|---|
| Everything in §2.6 | unit tests + mutation checks + the browser pane (2026-09-29/30), NOT a market session |
| Signal retirement | ran against the real database: 1,366 CONFIRMED → AUDITED (first run 2026-09-29), 649 orphan ACTIVE → AUDITED, 56 ACTIVE → EXITED by backfill |
| Pre-open readiness | `ready:true` against real brokers |
| Candle recovery | closes prior-day gaps too: 2026-08-13 had sat at 368/375 bars on all three instruments; after recovery all six instrument-days checked were 375/375 |

**Never run in a market session:** the whole Live Deployments uplift (reconcile proof, restart
attribution, transmit fence, caps/flatten writes, session clock, status surfaces), premium-momentum
multi-leg on the live guard, the risk supervisor's daily-loss pause (a `TypeError` until `35a33fe`),
and the failure branches of the 2026-07/08 live-integrity work (refusing to adopt an unowned
position, a lost ACK treated as indeterminate). Fill-based P&L has run: the real trades since
2026-08-14 journal `entry_fill_price`, and later ones `total_charges` / `net_realized_pnl`.

### 2.3 The next gate

1. **Market-session validation of the live controls on the static IP.** The checklist is
   [`LIVE_VALIDATION_PLAN_2026-08.md`](LIVE_VALIDATION_PLAN_2026-08.md) §1 (U1–U8), the one
   canonical copy, followed by the rest of that plan and
   [`live-readback-checklist.md`](live-readback-checklist.md). The one-time cloud reminder fired
   2026-10-06 09:00 IST. The gate stays open until the plan's §11 records an outcome. A
   journal-vs-broker realized-P&L difference reads `ESTIMATED_EXIT`, not PASS (plan U7 / L6).
2. **Next development:** E1 — a durable live execution episode ledger with a fail-closed admission
   reservation ([`AUTONOMY_DEVELOPMENT_PLAN_2026-08.md`](AUTONOMY_DEVELOPMENT_PLAN_2026-08.md),
   tracked in `AGENT_TODO.md`).

### 2.4 Checkpoints

Tags `checkpoint/pre-optimizer-perf-2026-08-30` (`cd6521e`) and the newer
`checkpoint/validated-3-5-6-2026-08-30` are the most recent rollback points. Tag again before risky
work (§4.3).

### 2.5 The traps most likely to cost money or a day

**Live / money**

- **T1 — No PC-down net.** On 2026-09-03 the broker OCO's stop leg reached the ORDER BOOK one second
  after the fill (a resting GTT never does) and was LPP-rejected. `LIVE_BROKER_OCO_ENABLED` is now
  off by default (`live_deploy_context._broker_oco_enabled`). Re-enable only after the pairing
  readback in `live-readback-checklist.md` §E1.
- **T2 — Pause/Stop demote live → paper.** `runtime._set_deployment_status` demotes `mode` on ANY
  transition out of ACTIVE (the v0.56.0 invariant), so getting back needs the full enable ceremony.
  The reversible hold is `risk.live.paused`, checked in `live/mode.is_deployment_live_allowed`
  (entries only; the book stays guarded).
- **T3 — Three different stops on entries.** `LiveEngine.can_trade()` refuses on the in-memory
  `halted`, the persisted `config.engine_halted`, or the `blocked_until_reset` latch. A halt is not
  the latch; check all three when an entry is refused.
- **T4 — Two 15:00 literals, and the window ends at 14:50.** The entry cutoff (the gate's own
  function) and the guard's EOD square are separate literals; the default entry window ends 14:50
  (`entry_window.DEFAULT_ENTRY_END`). The session clock reads each from its own source.
- **T5 — Join contracts by identity, never by exchange token.** Tokens are recycled across expiries
  (47291 is three different contracts). Premium-momentum leg recovery joins through the broker order
  book's `norenordno → tsym`, never the persisted Upstox symbol; an unresolvable order is
  skip-never-exit.
- **T6 — A cache that serves last-good must back off AND refuse** (`live_mark_cache.SnapshotCache`,
  in front of the broker position book). A failure must advance a backoff clock (286 broker
  calls/min were measured against a 12/min baseline, on a key shared with the MCP); past
  `max_stale_s` it raises `StaleSnapshotError`; a cold cache refuses rather than returning an empty
  (= flat-looking) book; never re-mark a stale book against live ticks; HTTP 200 is not "usable" —
  gate on `broker_stale`.
- **T7 — Never re-derive Noren's MTM.** `live_marks` applies a tick delta to the broker's own
  `urmtom`; Noren's base price differs between carry-forward and intraday positions.
- **T8 — `ingest_ts` is the only local clock.** `ts` and `received_ts` are both broker-side (p50
  254 ms / p95 1.7 s apart). Staleness gates and latency measurement key off `ingest_ts`.
- **T9 — Three layers on three clocks; do not collapse them.** Display = tick (SSE
  `/live-broker/marks/stream`, `/paper/open-positions/stream`, ~10/s coalesced, heartbeat event every
  15 s). Exits = tick-woken, 200 ms floor, 1.5 s broker read as backstop; `_premium_for()` falls back
  to broker `lp` on every degraded path (the guard must survive a dead Upstox feed), and
  `_fast_premium_pass` must never read the broker. **Entries stay on the CLOSED 1-minute bar**
  (`bar_events` only removes polling delay); tick-driven entries would repaint signals and break
  backtest parity. Only ATM±3 strikes are WS-subscribed (`OPTION_CHAIN_BASELINE_RADIUS = 3`).
  `_evaluator_wait` is the evaluator loop's pacing seam — change it without updating the tests and
  the suite HANGS instead of failing. Out-of-hours measurement: `ALPHAFORGE_TICK_REPLAY=1` +
  `POST /api/_diag/tick-replay`.
- **T10 — The live window must reach 09:15.** Session-anchored indicators (e.g. `vwap`) are grouped
  over the rows the evaluator hands them; at the default `live_lookback_bars = 200`
  (`strategies/base.py`) the window stops reaching the open after ~12:34 and live signals silently
  diverge from the backtest (+17 pts = 2.12 ATR VWAP error by 14:49, `fc424a1`). The
  session-anchored strategies declare 400; any new one must too. No backtest can show this.
- **T11 — Safety gates must fail closed.** `detect_drift` once returned "no drift" whenever a hash
  was missing (`fa2b65d`); six deployment rows in the database had no drift protection. Ask which answer is the ALLOW
  answer, then check that every "cannot verify" path returns the DENY one.

**Backtest / optimizer**

- **T12 — Every paired-option backtest saved before 2026-07-30 is wrong.** Legacy option candles
  were keyed by an absent `contract_key` → pandas `NaN` → `bool(float("nan")) is True` → every
  candle keyed `"nan"` (one Confluence config paired 10 of 253 signals before `dcaf722`, 253 of 253
  after). Premium-native runs were not affected. Re-run anything you intend to rely on.
- **T13 — Two envelopes, twice.** For an ORDINARY strategy the truth is `result.metrics` /
  `result.trades`; for a PREMIUM-NATIVE one those are a zero-filled stub and the real result is
  `result.option_backtest.*` — route on `option_backtest.dispatch == "premium_trigger_config"`
  (backend `is_premium_trigger_strategy`; frontend `isPremiumNative()` / `resultKpis()` in
  `lib/backtestMetrics.js`). Separately, `run.config.option_backtest` is what was ASKED and
  `run.option_backtest` is what RAN: the optimizer rewrites sizing (a run that requested 5 lots
  traded 100).
- **T14 — Option legs are sparse: never join by array position.** A signal with no option data
  produces no leg; join by `index_trade_id` (a position in the FULL spot-trade list) or
  `signal_entry_ts`. A caller that filters first (DTE filter) must remap after the sim, as
  `runtime._run_paired_option_backtest` does (v0.55.1; saved runs were repaired by a one-off script,
  since removed — `a2294d7`, CHANGELOG 0.55.1). Frontend `joinOptionLegs()`. Pinned by
  `tests/test_paired_option_index_remap.py` and `tests/test_backtest_lab_action_buttons.py`.
- **T15 — `parameter_schema` min/max is the optimizer's SEARCH RANGE, not a feasibility limit.**
  `param_overrides` exist to widen it, so promoted values routinely sit outside it. Out-of-range is
  an acknowledgeable warning; only genuine infeasibility blocks (`39e5f4f`,
  `tests/test_deploy_param_range_is_advisory.py`).
- **T16 — The optimizer ranks trials on a SPOT proxy.** `net_pnl_inr` = `total_pnl_pts × lot_size`
  ranks identically to points; only the top-K finalists are re-scored on real option ₹, and the
  re-rank loader has a 4,000,000-row cap (surfaced as `rerank_coverage`). Deliberately open (audit
  O1/O7). `optimize_indicator_periods` defaults to false; a leftover `param_overrides` can widen a
  bound silently — both are surfaced in the UI, not auto-removed.

**General shape of T13–T16:** a stored field means different things in different states and
something read it under a fixed label. Before trusting a displayed number, check what writes it.

**Former section numbers.** Older docs, commits and `learning_log.md` cite HANDOFF §2.0b–§2.0h and
§2.1–§2.3 from before this rewrite. Their still-live content is now: §2.0b/§2.0d (live-integrity and
takeover fixes) → §2.2 "never run in a market session"; §2.0c (the 2026-08-14 live session) → §1.2,
§4.2 (data-integrity gate) and §4.4; §2.0e (live window, fail-open gate) → T10, T11; §2.0f/§2.0g
(Backtest Lab buttons, optimizer units) → T13, T16; §2.1(1)–(4) → T12–T15; §2.1b–d (display,
latency, cache) → T6–T9; §2.0h → §2.6. The release narrative (§2.2, §2.3) is in
[`../CHANGELOG.md`](../CHANGELOG.md).

### 2.6 The Live Deployments uplift, 2026-09-26 → 09-30 (formerly §2.0h)

Spec: [`superpowers/specs/2026-09-26-live-deployments-uplift-handoff.md`](superpowers/specs/2026-09-26-live-deployments-uplift-handoff.md).
Every phase has a CHANGELOG entry; every new rule was mutation-checked.

| Area | Where | What to know |
|---|---|---|
| Reconcile exit pricing | `live/reboot_reconcile._match_close` / `_proven_round_trip` / `_carry_free` | An exit price is attributed only on PROOF: this entry's own fills in today's trade book, account flat on the contract at entry, no interleaved order, one product, no carried-forward position (`cfbuyqty`/`cfsellqty`), SELLs summing to what FILLED. P&L from the entry's OWN fills, never the blended `daybuyavgprc`. |
| Stale OPEN rows | same module | A confirmed-empty book (2 reads, 1.5 s apart) closes rows entered on an EARLIER IST day; an unreadable book closes only EXPIRED contracts; a same-day OPEN row beside an empty book keeps recovery INCOMPLETE unless the order book says it ended unfilled (→ `never_filled`). Such closes carry `exit_day_unknown` and are excluded from `daily_realized_summary`. Recovery is complete only at `ok` / `flat_confirmed`. |
| One P&L formula | `live/close_loop.realized_fields` | Used by every close path and backfill; also journals `total_charges` / `net_realized_pnl`. Past-day fills: `backend/scripts/backfill_realized_from_recorded_fills.py` (dry-run, attestation, provenance). |
| Guard ownership | `live/ownership.resolve_owned_tsyms` | An intent whose journal row is CLOSED, or whose contract expired, no longer confers ownership. |
| Restart attribution (G1) | `live/ownership.resolve_rehydrate_attribution` / `attribution_for` | A recovered position re-attaches keyed by its journal row's `norenordno` with its `deployment_id` — only when provable (one deployment's OPEN rows, held qty ≤ ordered). It starts `seen_filled`; levels reset to the default stop, and that recovered stop never arms the premium-momentum lazy leg. |
| Governor | `live_deploy_governor` | `precheck_live_caps → measure_exposure → decide_live_caps / decide_account_caps`, shared by enforcement and read-only `describe_live_caps` (the `governor` key on `GET /deployments/live/status`). Loss = realized + open unrealized; unknown is null, never 0. |
| Transmit fence (G2) | `auto_live._recheck_authorization` | Re-decides on the fresh doc/rows/clock: refuses when fresh `lots` < built size and re-runs `check_live_caps` (`stale_authorization:caps_tightened:lots a->b`, `stale_authorization:caps:<reason>`). |
| Live writes | `POST /deployments/{id}/live/caps`, `/live/flatten` | Caps: tighten-only (loosening → 409, nothing written), CAS on `updated_at` + ACTIVE + live. Flatten: squares and STAYS live (optional hold), refuses out of hours, skips shared contracts, names unguarded rows. `/live/pause` writes only its leaves. |
| Truthful status | `live/arm_state`, `live_position_guard.guard_health`, `/deployments/overview` | Connected = stored AND not expired. Guard health: `watching` / `idle` / `off_hours` / `blind` / `stalled` / `not_running` (OFF HOURS outside the options session, where the guard skips cycles by design). Stale marks excluded from MTM (`open_unverified`). |
| Trading clock | `live/session_clock.describe_session`, `GET /live-broker/session-clock` | The browser counts against `performance.now()`-anchored server instants. |
| Status surfaces (8) | `lib/marketFeedHealth.js`, `GET /live-broker/preopen-readiness`, `live/greeks_book.py` + `LiveMarksService.book()`, `lib/apiHealth.js`, `overview_open.py`, `lib/signalDisplay.js` | Market header from stream `connected` + `last_tick_age_s`; dated pre-open verdict; Greeks priced off the broker book (flat / open / unknown / unguarded); sidebar dot from a real `/api/health` poll; the overview reports carried OPEN rows (`open_carried`) and a demoted deployment's real-money `live_trades` separately (never summed, never dropped); pause/latch reasons carry their date; Live Trade Stats marks an OPEN row unverified unless the guard is marking it. |
| Pane logic | `lib/liveDeploymentView.js`, `sessionClock.js`, `liveNotify.js`, `liveTimelineView.js` | Pure, node-executed. Timeline `GET /deployments/{id}/timeline?date=`. Alerts are opt-in, default off. Stop toasts say "exit submitted — awaiting fill confirmation", never "flattened". |
| Signals | `signal_lifecycle` | A signal's bar is `candle_ts` (+ `context.candle.ts`); `bar_ts` exists only on the evaluation AUDIT record (`signal_bar_ms`). Every paper/live close moves ACTIVE → EXITED (`exit_linked_signal`). `expire_unactioned_signals` (boot + the 15:00 sweep, operator-approved) retires CONFIRMED signals >15 min past their bar with no claim/trade link → AUDITED (`unactioned_bar_passed`, own `updated_at` kept). Journal chips EXPIRED / NOT ACTED ON / RETIRED; retention purge deletes BLOCKED signals only (`POST /signals/purge` `blocked`); CSV gained `blocked` / `paper_trade_id` / `trade_status`. |

**Found and fixed on the way:** the risk supervisor's daily-loss pause was a TypeError from
2026-08-06 to 2026-09-29 and never paused anything (`35a33fe`; its only test checked by AST that the
call's name appeared); a stale OPEN row from 09-16 held a concurrency slot for 11 days; the 09-16
partial-fill trade was backfilled to ₹822 from the recorded trade book (operator-approved).

## 3. Run & test

The commands below are Git Bash syntax (the agent's Bash tool). **The operator's terminal is Windows
PowerShell 5.1, where `&&` is a ParserError and neither command runs** — give the operator one command
per line, or `A; if ($?) { B }`.

```bash
docker compose up -d --build                        # deploy: build + start the whole stack
docker compose ps                                   # mongo / backend / frontend healthy
curl -s http://127.0.0.1:8001/api/health            # {"db":"ok"}
```

- **Rebuild, never just restart.** Backend code is baked into the image (only
  `backend/app/strategies/plugins` is bind-mounted) and the frontend is a built bundle served by
  nginx, so a restart runs STALE code and a verified fix looks broken. Confirm what is running:
  `MSYS_NO_PATHCONV=1 docker exec alphaforge_backend grep -c <symbol> /app/app/<file>.py` (Git Bash
  mangles the path without the prefix), or import the changed module in-container and print a value
  — a rebuild that silently no-ops has happened here.
- **`start-app.bat`** is the operator's launcher. Call it by ABSOLUTE path
  (`NoDefaultCurrentDirectoryInExePath=1` on this box makes a bare `call foo.bat` read as "not
  recognized"). By default it does NOT rebuild a healthy backend (to keep the running guard alive);
  `--rebuild` rebuilds the full stack and warns that it briefly interrupts the guard. Flags:
  `--check-only`, `--no-browser`, `--rebuild`.
- **Browser at `http://localhost:3000`, never `127.0.0.1:3000`.** `CORS_ORIGINS` allows only
  `http://localhost:3000`, so every API call from `127.0.0.1` is CORS-blocked and pages look empty.
  Hard-reload (Ctrl+Shift+R) after a frontend rebuild — client navigation keeps the stale bundle.
- **Host scripts dial `127.0.0.1`, never `localhost`** (e.g. `--mongo-url mongodb://127.0.0.1:27017`):
  `localhost` resolves to `::1` first and stalls ~2 s against IPv4-only Docker. MongoDB has no auth
  and listens on loopback only.
- **Before a rebuild, check that no live position is open** (read-only):
  `curl -s http://127.0.0.1:8001/api/live-broker/positions` (the broker's book) and
  `curl -s http://127.0.0.1:8001/api/live-broker/guard-status` (what the guard holds). Recreating the
  backend stops the software guard until boot recovery re-attaches it; a recovered position comes back
  at the DEFAULT 50% catastrophe stop with `source="rehydrated"` (its strategy levels are not
  restored), re-keyed to its journal row and deployment only when that is provable (§2.6, G1). With
  the broker OCO off by default (T1), nothing protects it in between.
- **Host test environment** (the `.venv` at the repo root, not `backend/.venv`): Python 3.12,
  `py -3.12 -m venv .venv` then `.venv/Scripts/python.exe -m pip install -r backend/requirements.txt`
  (it carries pytest and pytest-asyncio). The working one has pandas 3.0.3, motor 3.3.1, pytest 9.0.3.
  Node must be on PATH for the `frontend/src/lib` tests.
- **Tests — there is no CI; the local suite is the only evidence.**
  `./.venv/Scripts/python.exe -m pytest tests/ -q -p no:cacheprovider` on the host (~4 min; node is
  required for the `frontend/src/lib` tests). Baseline 2026-09-30: **6,586 passed, 4 xfailed, 0
  failed.** Do not judge a change by a suite run inside the backend container: the image is built
  from `backend/` only (no `tests/`, no `frontend/`), so a suite copied in reds every test that
  reads `frontend/`.
- **Frontend gate:** `cd frontend && CI=true npx --no-install craco build` — `CI=true` turns warnings
  into errors (PowerShell: `$env:CI = "true"` then `npx --no-install craco build` from `frontend/`).
- **Prove the test bites.** Reproduce the failure first; save the FAILED list, re-run the identical
  command on clean HEAD and diff — use a throwaway worktree (`git worktree add --detach <tmp> HEAD`,
  run there, then `git worktree remove <tmp>`), not `git stash`: the stash is shared with every
  worktree and session on this repo. A new test that passes both before and after has not
  tested the fix — mutate the code and confirm it fails.
- **Determinism replay is the strongest backtest regression check:** replay saved runs against their
  STORED configs and require identical `trade_count` / `win_rate` / `profit_factor` /
  `total_pnl_pts` / `max_dd_pts`.
- Launch, first-time install and troubleshooting: [`STARTUP_MANUAL.md`](STARTUP_MANUAL.md); deeper
  workflow: [`DEVELOPER_GUIDE.md`](DEVELOPER_GUIDE.md) §B and §H.

## 4. Standing conventions

### 4.1 Safety rules — non-negotiable

1. **Flattrade MCP: never call `login` / `logout`.** One API key ⇒ one redirect URI owned by
   AlphaForge, and Flattrade is last-login-wins, so a second login silently kills AlphaForge's live
   token. Recover a stale MCP session with `backend/scripts/resync_mcp_session.py --clean`. Positions
   the MCP opens are invisible to AlphaForge's guard and kill switch. Read tools are fine; keep them
   sparse (shared rate budget). Full rules: [`flattrade-mcp-integration.md`](flattrade-mcp-integration.md).
2. **Never place, modify, cancel or square a real broker order** — through the app or the MCP.
   Never refresh Flattrade OAuth while `LIVE_AUTOPLACE_ARMED` is on (standing decision,
   [`AGENT_TODO.md`](AGENT_TODO.md) §0 item 7).
3. **Never flip a deployment to live mode.** Going live is the operator's act (Deploy-to-Live →
   `POST /deployments/{id}/live/enable`, the only writer of live mode). Confirm before anything that
   could reach the broker or a deployment (deploy, resume, enable); static inspection and dry runs
   are fine.
4. **Push only with per-changeset operator approval.** Commit freely at green milestones.
5. **Never commit** `.env`, tokens, broker credentials or MCP client configs. **Never create a second
   Flattrade API key** (a second needs the paid registered-algo tier; the operator declined it).

### 4.2 Money and gate invariants

- **`realized_pnl` is the caps basis and stays GROSS.** The operator confirmed a ₹5,000 cap means
  ₹5,000 of premium move, excluding charges. `live/close_loop.realized_fields` also journals
  `total_charges` and `net_realized_pnl`; making caps net-of-charges is an operator policy call, never
  a side effect.
- **Deploying live IS the authorization** (v0.56.0): `mode == "live"` + connected + before the 15:00
  IST cutoff, within caps. `LIVE_AUTOPLACE_ARMED` is the single env master switch for automated
  entries (unset ⇒ dry-run, no transmit). The software guard always transmits its exits.
- **Do not add live-ARMING or research-qualification gates.** Both were removed on explicit
  instruction; forward-validation status and guardrail/survival verdicts are advisory, and an
  unvalidated deployment goes live only with the audited `accept_unvalidated_live` consent. There is
  no premium-momentum-specific gate and none should be added. If a feature genuinely needs new
  protection, propose it and let the operator decide.
- **The data-integrity activation gate is intentional** (`live_data_gate.check_live_data_gate`). It
  blocks only a NEW `mode → live` transition when today's candles cannot be verified, and never exits,
  the kill switch, the 15:00 square or an already-live deployment. It judges the DATA, not the
  strategy — do not strip it under the rule above.
- **IST everywhere**; NSE session 09:15–15:30 with the 15:00 square-off (options trade to 15:40
  since the 2026-08-03 closing-auction change — `session_spec.py`, DEVELOPER_GUIDE §F);
  holiday-aware (`nse_calendar.py`). Lot sizes and expiry weekdays have rotated — read them from
  `instruments.py` / `nse_calendar.py` / `dte.py` and contract data, never from memory.

### 4.3 Working rules

- **Checkpoint before risky work:** commit the validated state and tag it; keep unvalidated work out.
- **Confirm before changing shared computation code** (`backtest.py`, `optimizer.py`,
  `option_backtest.py`, `wfo.py`, …): it reprices every future result. Show before/after evidence.
- **Verify across many saved runs, not one** — dense-leg runs passed the positional-join bug by
  accident; only a 105-run sweep caught it.
- **Never call something fixed without running it.** For UI work that means driving it, not grepping
  JSX.
- **A subagent panel that returns 0 completed agents is not a passed check** — say it is unverified.

### 4.4 Lessons that generalise

Each was earned from a defect that shipped green. Narrative: [`../learning_log.md`](../learning_log.md).

| Rule | The failure that taught it |
|---|---|
| **A contract cannot be validated against a mock that shares the implementation's assumption.** Hit the real other side of an interface once. | `jData` percent-encoding: the impl used `urlencode`, its test `parse_qs`; production answered HTTP 400 to every Flattrade call (`23d422b`). |
| **When two modules must agree, make one call the other** — do not translate. | Live passed a nested `exit_controls` to a flat-schema consumer and silently ran with no trail. On 2026-08-14 the same deployment's paper trade trailed and booked +₹4,882.69 while live ran to −₹1,651-worth (`20c9750`). The fix delegates to `effective_premium_stop`. |
| **Drive the code; never grep source for behaviour.** | A 15:00 scheduler call passed an argument to the wrong function; the risk-supervisor pause was a TypeError for 7 weeks — both "tested" by source assertions. |
| **Test frontend logic through node.** Put it in `frontend/src/lib/*.js` and execute it. | A card used a `text-warn` class this theme does not define; it would have rendered colourless and no grep could show it. |
| **Suspect the fixture before the code.** | Four false failures: a wrong epoch constant, a stub missing `prd`, a clock mismatch, an assumed field. Live-route fixtures that omitted the source pin hid the fail-open drift gate. |
| **Ask which answer is the ALLOW answer.** | `detect_drift` and the evaluator's `if pinned_sha:` were both documented as conservative and both failed open. |
| **Audit your own commits with the machinery you use on others'.** | `358fcc3` and `58ef491` fixed regressions an agent had introduced itself; both had passed the full suite, and an adversarial audit caught them. |
| **Verify a claim before repeating it.** | An audit agent reported the per-position Square as unreachable; it squares the manual test position, and deployed exits were never affected. |

## 5. Where to go deep

Research verdicts are **closed questions** with pre-registered kill or revival criteria, several cited
from code; they are never deleted. Dated session records and audits are historical and not
maintained — trust `git log` and CHANGELOG over them.

### 5.1 Kept docs

| Doc | Use it for |
|---|---|
| [`AGENT_TODO.md`](AGENT_TODO.md) | The live board: next gate, open items, decisions |
| [`agent-takeover-prompt.md`](agent-takeover-prompt.md) | Copy-paste prompt for a fresh agent session |
| [`DEVELOPER_GUIDE.md`](DEVELOPER_GUIDE.md) | Deep onboarding: run/build/test, live safety model (§E), warehouse, India rules, research → deploy, gotchas (§H) |
| [`ARCHITECTURE.md`](ARCHITECTURE.md) | Module map, data flow, Mongo collections, live gate chain |
| [`API_REFERENCE.md`](API_REFERENCE.md) | Backend HTTP routes |
| [`STRATEGY_DEPLOYMENTS.md`](STRATEGY_DEPLOYMENTS.md) | Deployment model, modes, kill switches, live path, premium-momentum multi-leg and its backtest ↔ live parity |
| [`STRATEGY_PLUGINS.md`](STRATEGY_PLUGINS.md) | Writing a strategy plugin |
| [`USER_MANUAL.md`](USER_MANUAL.md) · [`PROJECT_OVERVIEW.md`](PROJECT_OVERVIEW.md) | Using each page · capabilities at a glance |
| [`STARTUP_MANUAL.md`](STARTUP_MANUAL.md) | Install, launch, troubleshoot |
| [`BACKTEST_INTEGRITY_AUDIT.md`](BACKTEST_INTEGRITY_AUDIT.md) | Permanent register: trusting a backtest / optimizer number |
| [`optimizer-decision-guide.md`](optimizer-decision-guide.md) | Which optimizer controls add value |
| [`option-data-provenance.md`](option-data-provenance.md) | Why historical option results are research-only |
| [`forward-validation-policy.md`](forward-validation-policy.md) | The ₹2,00,000 forward-validation (promotion) policy |
| [`live-readback-checklist.md`](live-readback-checklist.md) | Real-money readback, incl. §E1 OCO re-enable |
| [`LIVE_VALIDATION_PLAN_2026-08.md`](LIVE_VALIDATION_PLAN_2026-08.md) | Phased market-session validation plan |
| [`AUTONOMY_DEVELOPMENT_PLAN_2026-08.md`](AUTONOMY_DEVELOPMENT_PLAN_2026-08.md) · [`NEXT_STAGE_ROADMAP_2026-07.md`](NEXT_STAGE_ROADMAP_2026-07.md) | E1 episode-ledger spec · Stage 2 roadmap |
| [`durable-static-ip-deployment.md`](durable-static-ip-deployment.md) | The approved always-on static-IP host design |
| [`flattrade-mcp-integration.md`](flattrade-mcp-integration.md) | The Flattrade MCP: token sharing, runbook, hard rules |
| [`Resources/flattrade-pi-api/INDEX.md`](Resources/flattrade-pi-api/INDEX.md) | Decoded Flattrade API (58 endpoints; `catalog.json`, `endpoints/`) |
| [`live-cockpit-audit-2026-07-25.md`](live-cockpit-audit-2026-07-25.md) | /live-trading findings register: 38 claims still UNVERIFIED — a source register; the work board is AGENT_TODO, which tracks it as one item |
| [`audit-report-2026-07.md`](audit-report-2026-07.md) | Decoder for `L##` / `O##` / `S##` IDs in commit messages (historical; O1/O7 remain open by design) |
| [`audit-verification-2026-08-14.json`](audit-verification-2026-08-14.json) | Decoder for "finding [n]" cited in code and tests (e.g. `live_exit_preview.py`, `exitPreview.js`) |
| [`NF_CE_PE_EXP2_Strategy_Spec.md`](NF_CE_PE_EXP2_Strategy_Spec.md) · [`DTE_OPENING_SHOCK_STRATEGY.md`](DTE_OPENING_SHOCK_STRATEGY.md) | Strategy specs (EXP2 blueprint; DTE opening shock and its required run config) |
| `superpowers/specs/` | Kept designs: live-guard Layer 1 confirm-flat and Layer 2 re-price (2026-07-09), optimizer spawn pool (2026-08-13), live controls + Market Pulse (2026-09-08, §4 deferred), Live Deployments uplift (2026-09-26) |
| [`../learning_log.md`](../learning_log.md) · [`../CHANGELOG.md`](../CHANGELOG.md) | Lessons per session · what shipped, per release |

### 5.2 Closed research verdicts — do not re-litigate without new evidence

| Question | Verdict | Where |
|---|---|---|
| Is the optimizer's winner option-profitable? | It optimizes a spot proxy; confluence's optimized arm lost −₹2,07,190 on options in-sample | [`OPTIMIZER_VERDICT_2026-07.md`](OPTIMIZER_VERDICT_2026-07.md) |
| EXP2 / `NF_CE_PE_EXP2_Base` edge | No demonstrated edge; failed its untouched holdout | [`BACKTEST_INTEGRITY_AUDIT.md`](BACKTEST_INTEGRITY_AUDIT.md) §6 |
| Premium-momentum family (~600 configs) | GATE FAILED on the 2026 holdout; none beats plain both-legs | [`PREMIUM_MOMENTUM_EDGE_VERDICT_2026-07.md`](PREMIUM_MOMENTUM_EDGE_VERDICT_2026-07.md) |
| Where could edge come from? | Ranked levers A–F; B (pooled regime) was run and killed, A (short side) was closed | [`PROFIT_LEVERAGE_ANALYSIS_2026-07.md`](PROFIT_LEVERAGE_ANALYSIS_2026-07.md) |
| Pooled multi-index regime routing | KILLED at validation; NIFTY has no gross edge | [`POOLED_REGIME_VERDICT_2026-07.md`](POOLED_REGIME_VERDICT_2026-07.md) |
| Does an ATM option buyer face a positive payoff? | No: MFE/MAE 0.90–0.95 before costs | [`OPTION_BUYING_MICROSTRUCTURE_2026-08.md`](OPTION_BUYING_MICROSTRUCTURE_2026-08.md) |
| `atr_sigma_router` optimizer winners | All four failed out-of-sample | [`atr-sigma-router-optimizer-results-2026-08-16.md`](atr-sigma-router-optimizer-results-2026-08-16.md) |
| Intraday option buying (Candidates A/B, short side) | Unconditioned ATM baseline NO_EDGE on both indices; Candidate A rejected; every defined-risk short vertical negative (24/24) | [`INTRADAY_OPTION_BUYING_CANDIDATES_2026-08.md`](INTRADAY_OPTION_BUYING_CANDIDATES_2026-08.md) §11, §14, §16 |
| SENSEX VWAP fade / continuation | No variant survives a four-quarter split; shipped as capability only | CHANGELOG "SENSEX VWAP Mean Reversion" (2026-09-02) and the plugin docstring |

### 5.3 Removed docs

The 2026-09-30 cleanup removed superseded takeover notes and session handoffs, completed plans and
specs, and one-off scripts. Their still-live facts were folded into the docs above. When an older
record (CHANGELOG, `learning_log.md`, a code comment) cites a file that no longer exists, recover it
from git:
`git log --diff-filter=D --format=%h -1 -- docs/<file>` gives the deleting commit `<sha>`, then
`git show <sha>^:docs/<file>`.
