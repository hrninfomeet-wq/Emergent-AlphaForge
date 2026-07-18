# Handoff — START HERE

_Entry point for the next engineer or AI agent. This is the shortest useful orientation; the repository and `tests/` are the source of truth, not any prior chat._

**Read order:** this file → [`DEVELOPER_GUIDE.md`](DEVELOPER_GUIDE.md) (the consolidated deep onboarding — run/build/test, live-trading safety model, warehouse model, India rules, research→deploy, gotchas) → [`ARCHITECTURE.md`](ARCHITECTURE.md) (technical reference). Use the ["Where to go deep"](#5-where-to-go-deep) table below to jump straight to a topic.

---

## 1. Orientation

**AlphaForge Trading Lab** is a **local-first research + forward-test app for Indian index options** (NIFTY / BANKNIFTY / SENSEX). The loop: warehouse 1-minute spot + option candles → backtest / optimize strategies → save as presets → deploy for signal generation, paper trading, and (under hard gates) live Flattrade execution.

Stack: **React** (CRA + craco) frontend, **FastAPI** (Python) backend, **MongoDB** (motor), all in **Docker Compose**. Frontend `:3000`, backend `:8001` (**every route under `/api`**), mongo `:27017`. **Upstox** = market data feed; **Flattrade** (Noren / PiConnect OMS) = live broker execution.

## 2. Current state

**Latest (2026-07-17, v0.55.0)**: **Phase 5B — live/paper multi-leg
premium-momentum execution is BUILT** (both-legs mode, one-shot lazy
reversal off STOP-class guard exits, per-deployment exit_time squares
clamped below the 15:00 EOD, realized-only session day-stop, VIX gate) as a
**pure capability by explicit user decision** — the family's failed edge
gate (0.54.2) travels with every multi-leg deployment as an informational
`premium_edge_verdict` arm advisory (never a gate; there is still NO
strategy-specific arming gate of any kind). first_to_trigger/single-leg
deployments are byte-identical to Track B (source-pinned). The independent
review of the recovery path caught and closed a HIGH defect before any
live exposure: recovery matched Upstox trading_symbols against the
Noren-keyed broker position book (different symbol spaces → every open leg
reads "gone" on restart → false finalize with money open); the fix joins
through the broker order book's norenordno→tsym and treats an unresolvable
order as skip-never-exit. Full suite 3478 passed, 0 failed. NOT yet
validated in a real market-hours session. See CHANGELOG 0.55.0 and the
plan's parity-divergence table before touching any of these seams.
**NEXT STEP (planned 2026-07-20)**: first market-hours validation, paper
mode — follow `docs/phase5b-market-validation-runbook.md` exactly; it also
scopes what paper CANNOT prove (the guard-side 5B exits: lazy arming,
exit_time, recovery join — those need the later 1-lot live day).

**Previous (2026-07-15, v0.54.2)**: **the premium-momentum edge hunt is CLOSED
with a failed gate.** Phase 5A.2 added the session day-stop + India VIX gate
overlays (backtest-only) and a byte-identical sweep-perf fix, then the
pre-registered ~600-config campaign ran on a three-way chronological split:
the validation-best config (+₹103.5k on the friendly 2025-Q4 slice) lost
−₹153.8k on the untouched 2026 holdout at 1%/side — worse than the untuned
baseline. Robust three-period NO. **Phase 5B (live multi-leg execution) is
NOT to be built on current evidence** — the revival kill-criterion is
pre-registered in `PREMIUM_MOMENTUM_EDGE_VERDICT_2026-07.md` (read it before
proposing any premium-momentum live work). The hunting tools remain in the
app (16 tunable keys through the honest tuner). Also in 0.54.1: the Backtest
Lab now surfaces premium-native results in the main view (they were hidden
in the collapsed Advanced section), the option preflight reports honest
per-session coverage for this strategy, and the option form's lots/costs
reach the dispatch.

**Previous (2026-07-14, v0.54.0)**: **Phase 5A** — the full AlgoTest "EXP2"
contingency shape (both-legs mode, one-shot lazy reversal leg with a fresh
strike + snapshot at the stop-out bar, entry cutoff, hard exit time,
%-of-entry stepped trail) is built and adversarially reviewed in the
**backtest engine only**, reachable via the `/premium-momentum` page +
backtest/tune routes. Backtest-only by mechanism, not promise: the plugin
schema and `PremiumTriggerConfig` were deliberately NOT extended, so
deployments structurally cannot carry the new params — live/general-Optimizer
support is Phase 5B. **The 5B gate was run and FAILED at EXP2 defaults**
(2026-H1 NIFTY, costs on): the lazy legs are gross-positive but net-negative
after friction, and the full EXP2 config is worse than plain both-legs
(−₹69.5k vs −₹60.6k on 2 lots); notable structural finding — both-legs mode
massively outperforms first-to-trigger (−₹60.6k vs −₹140.2k), though all
configs remain net-negative. Do NOT build Phase 5B live execution unless a
tuned config first beats both-legs net-net out-of-sample through the honest
tuner. See CHANGELOG 0.54.0.

**Previous (2026-07-14, v0.53.2)**: Phase 4 (**engine dispatch**) is now
functionally complete. `premium_momentum` runs through the standard Backtest
Lab (single-run) AND the full multi-trial Optimizer search (Bayesian and
Grid) exactly like any other strategy — `optimizer.py::_evaluate_premium_trigger`
closes the last gap (0.53.1's Stage-2 fix was reachable but Stage 1's
per-trial scorer still used the stub `evaluate()`, unconditionally
disqualifying every trial before Stage 2 was ever reached). Also fixed along
the way: `premium_momentum`'s `parameter_schema` had no `min`/`max` on its
numeric fields (silently sampled `momentum_pct` as `0.37` instead of `15`,
and crashed the Grid method outright), and a subtler bug an adversarial
review caught — the Stage-1 preload read a `param_overrides` `"fixed"` string
value that no trial could ever actually receive (string params are excluded
from the search space before overrides are applied), which could silently
bias every trial's score against the wrong option window. **Verified live
against real local warehouse data**: a real 15-trial Bayesian job returns a
genuine non-disqualified best score (5 trades, 60% win rate, net P&L
+₹5,404.70); a different strategy's job run immediately after confirms zero
regression (identical to before). Full host suite: 3358 passed, 0 failed. See
CHANGELOG 0.53.2 for the full detail. Remaining, deliberately out of scope: a
declarative config-block builder UI, and the `opt_workers>1` parallel Optuna
path (pinned to sequential for this strategy — sequential is the documented
default anyway).

**Previous (2026-07-13, v0.53.0, Emergent handoff session)**: AI feasibility
accepts premium-native rules (option-premium momentum, locked strike, stepped
premium trail) with a mapped `premium_trigger_config` verdict, session gates +
position size map to `deployment_layer`, `lazy_leg_contingency` is honestly
scoped as Phase-5 future work (not a blanket reject or false accept), and the
Gemini 8000-token cutoff on Strategy Library AI actions is fixed
(`DEFAULT_MAX_TOKENS` 8192 → 32768, `py_author.py`'s hard cap removed). See
CHANGELOG 0.53.0 for the full detail.

Everything of substance is integrated on **local `main`**, but local `main` is currently **ahead of
`origin/main` by dozens of commits** (unpushed — push only on explicit user request, see §4). There
is no *long-lived* stack of feature branches, but at any given time there may be **1-2 active WIP
branches** from a parallel session that haven't landed on main yet — run `git branch -v` and don't
assume main is the whole story before describing "current state" to anyone. The app has grown
across `0.17.x → 0.52.x` (see [`../CHANGELOG.md`](../CHANGELOG.md) for versioned detail — if the top
entry looks more than a week old, the changelog itself is probably behind `git log`; it has happened
before). It runs in Docker; backend code is baked into the image, so **rebuild the container after
backend edits**.

Built subsystems (all verified present in `backend/app/`):

- **Data Warehouse** — `candles_1m` holds 1-minute OHLCV for the 3 indices (spot + ATM-band option contracts) + INDIAVIX. Daily ATM-band completeness model, holiday-aware NSE calendar, one-button Sync + auto-update. (`completeness.py`, `data_hygiene.py`, `nse_calendar.py`, `routers/warehouse.py`.)
- **Backtest Lab** — spot backtests + paired real-option-candle backtests; honest rupee-first metrics; optional exit/risk-control overlay (trailing / breakeven / daily caps). (`backtest.py`, `option_backtest.py`, `exit_controls.py`, `execution_policy.py`.) The shared indicator enrichment (`indicators.py` / `indicator_groups.py`) re-warms the whole-frame indicators across intra-session warehouse gaps via a per-bar `gap_before` flag + `_reset_on_gap` wrapper (no-gap fast-path keeps gap-free windows byte-identical); see `docs/superpowers/specs/2026-07-05-intra-session-gap-indicator-reset-design.md`.
- **Optimizer** — Optuna TPE / Grid / Genetic search; single vs walk-forward (honest OOS); spot vs option re-rank; capital-aware **survival gate**; exit-control search. (`optimizer.py`, `wfo.py`, `walkforward.py`, `survival.py`, `rerank_select.py`.)
- **Strategy Library** — builtin + drop-in plugin strategies; retire / delete lifecycle; multi-provider AI authoring wizard (Anthropic + Gemini; Spec + capability-aware + full-Python tiers). (`strategies/*`, `routers/strategies_admin.py`, `ai/*`.)
- **Paper Trading** — live-tick-driven realism (tick exits, poll-for-new-bar entries). (`paper_auto.py`, `paper_trading.py`, `live_exit_monitor.py`.)
- **Live Trading (Flattrade)** — offline-first; L0–L3 gate chain; the executor is the **single real-order chokepoint**; margin pre-check; OCO/GTT catastrophe backstop; kill switches; per-token-latched recovery that re-runs on every fresh daily OAuth (not boot-only); exit executors resolve a raised-but-maybe-landed order against the broker book before ever blind-retrying; Greeks; ARMED auto-place only under an env gate **plus** per-deployment ARM **plus** caps **plus** EOD auto-disarm. **No resting manual-position timer** — the old 10-minute test-session auto-square was removed; 15:00 IST EOD square is the sole time-based backstop for a manual position (deployed strategies exit on their own rules + a resting OCO). (`live/executor.py`, `live/safety.py`, `live/margin.py`, `live/mode.py`, `live/arm_state.py`, `live/gtt.py`, `live/kill_switch.py`, `live/exit_claims.py`, `live/auto_square.py`, `routers/live_broker.py`.)
- **Premium-momentum strategy** (new) — a deployable strategy family driven by a **time-locked strike + real option-premium trigger** instead of a spot indicator: at a configurable reference time the evaluator locks the CE/PE strike from spot, captures each side's premium from fresh ticks, and the first side to cross a momentum threshold enters; exits use a new stepped X-Y trail guard mode alongside stop/target. Backtest is a self-contained option-native sim with a cost model and an honest (costs-mandatory, chronological-train/OOS-report) tuner; live/paper execution rides the *exact same* deploy/arm/guard rails as every other strategy — **there is no premium-momentum-specific arming gate, and none should ever be added** (a deliberate, explicit design decision — don't "helpfully" add one later). See [`STRATEGY_DEPLOYMENTS.md`](STRATEGY_DEPLOYMENTS.md) for the deployment-level detail. (`premium_momentum.py`, `premium_momentum_backtest.py`, `premium_momentum_tuner.py`, `premium_momentum_live.py`, `premium_lock_store.py`, `premium_pin.py`, `strategies/plugins/premium_momentum.py`, `routers/premium_momentum_routes.py`.) The shipped default (AlgoTest blueprint) parameters have **no edge** on 2026-H1 NIFTY — this is a capability, not (yet) a validated money-maker, and it has not been run through a real market-hours session.

Routers mounted under `/api`: `research`, `strategies_admin`, `warehouse`, `journals`, `deployments`, `broker`, `live_broker`. Frontend pages: Dashboard, DataWarehouse, BacktestLab, Optimizer, StrategyLibrary, SavedPresets, LiveSignals, SignalJournal, PaperTrading, LiveTrading, PreTradeChecklist, PremiumMomentum.

## 3. Run & test quickstart

```bash
docker compose up -d --build backend frontend    # launch / rebuild
docker compose ps                                # backend + mongo healthy
curl -s localhost:8001/api/health                # {"db":"ok"}
```

Frontend → `http://localhost:3000`, backend → `http://localhost:8001` (routes under `/api`), mongo in container `alphaforge_mongo` (named volume `mongo_data`, NOT in the project / OneDrive folder). Rebuild the relevant container after editing that half.

**Tests run in two places** — the split matters:

- **Host tests (pure / contract).** `python -m pytest tests -q` from the repo root. These NEVER import `server.py` or the routers (motor/pymongo are absent on the host — those imports fail). They string-assert on the source via `tests/contract_corpus.py`. Use for the pure engines, contract pins, and JSX string-pins.
- **Container tests (motor / route).** Tests that touch motor or FastAPI routes must run **inside the backend container**:
  ```bash
  docker cp tests/. alphaforge_backend:/app/tests
  docker exec -w /app alphaforge_backend python -m pytest tests/<file> -q
  ```
- **Frontend "tests"** are pytest string-pins over the JSX source (run on the host with the contract tests).
- **Browser smoke** is the final check: open the app in Chrome and **hard-reload (Ctrl+Shift+R)** to drop the stale CRA bundle — client-side navigation does not reload the JS.

## 4. Standing conventions

- **Per-changeset push approval.** Commit freely; **push only when the user explicitly says so.** Nothing is auto-pushed. On the default branch, branch first.
- **Never place a real broker order unless explicitly armed.** The assistant never personally transmits or squares a real order. Real live entries require the env gate `LIVE_AUTOPLACE_ARMED=1` **and** a per-deployment ARM within caps; auto-squares require `LIVE_GUARD_ARMED=1`. Offline-first: unset ⇒ dry-run logs, no transmit.
- **IST everywhere.** NSE session 09:15–15:30 IST with a 15:00 square-off; the system is **holiday-aware** (`nse_calendar.py`).
- **Verify India-specific facts against the code** — lot sizes and expiry cadence live in `instruments.py` / `nse_calendar.py` / `dte.py` and have rotated over time; do not hard-code from memory.
- **Never commit** `.env`, tokens, broker creds, or any credentials file.
- **Don't add a new live-arming gate without being asked.** The default posture for a new strategy or
  feature is to ride the *existing* arm/gate/cap chain (§E in `DEVELOPER_GUIDE.md`), not to invent a
  parallel one "for safety" — extra gates that weren't requested have already had to be explicitly
  removed once (premium-momentum's spec amendment). If a feature genuinely needs new protection,
  propose it and let the user decide.
- **A subagent panel that returns 0 completed agents is not a passed check.** If an adversarial-review
  or verification panel dies on a session/token limit with nothing completed, treat it as **unverified**,
  say so, and either retry, do the check yourself, or ask — never report it as a clean pass.

## 5. Where to go deep

Start with the consolidated [`DEVELOPER_GUIDE.md`](DEVELOPER_GUIDE.md), then reach for the reference docs:

| I need to… | Go to |
|---|---|
| Onboard deep: run/build/test, safety model, warehouse model, India rules, research→deploy, gotchas | [`DEVELOPER_GUIDE.md`](DEVELOPER_GUIDE.md) |
| See capabilities + the end-to-end workflow at a glance | [`PROJECT_OVERVIEW.md`](PROJECT_OVERVIEW.md) |
| Understand the data-warehouse completeness model | [`DEVELOPER_GUIDE.md`](DEVELOPER_GUIDE.md) (model) · [`ARCHITECTURE.md`](ARCHITECTURE.md) (technical) |
| Understand the **live-trading safety model** (gate chain, ARM, kill switches) | [`DEVELOPER_GUIDE.md`](DEVELOPER_GUIDE.md) · [`STRATEGY_DEPLOYMENTS.md`](STRATEGY_DEPLOYMENTS.md) · [`ARCHITECTURE.md`](ARCHITECTURE.md) |
| See the India trading rules (session, DTE, holidays, lots) | [`DEVELOPER_GUIDE.md`](DEVELOPER_GUIDE.md) · code: `nse_calendar.py` |
| Trace the module map, data flow, Mongo collections, live-execution gate chain | [`ARCHITECTURE.md`](ARCHITECTURE.md) |
| Look up a backend HTTP route | [`API_REFERENCE.md`](API_REFERENCE.md) |
| Use a specific page in the UI | [`USER_MANUAL.md`](USER_MANUAL.md) |
| Write a custom strategy plugin | [`STRATEGY_PLUGINS.md`](STRATEGY_PLUGINS.md) |
| Understand the deployment model (modes, gates, kill switches, live) | [`STRATEGY_DEPLOYMENTS.md`](STRATEGY_DEPLOYMENTS.md) |
| Install / launch the app | [`LOCAL_SETUP.md`](LOCAL_SETUP.md) · [`STARTUP_MANUAL.md`](STARTUP_MANUAL.md) |
| Drive the optimizer / read walk-forward OOS | [`optimizer-user-guide.md`](optimizer-user-guide.md) · [`Walk-forward (honest OOS) what it does exactly.md`](<Walk-forward (honest OOS) what it does exactly.md>) |
| Run a live-money readback | [`live-readback-checklist.md`](live-readback-checklist.md) |
| Reference the Flattrade broker API | [`Resources/flattrade-pi-api/INDEX.md`](Resources/flattrade-pi-api/INDEX.md) (+ `catalog.json`, `endpoints/`) |
| See versioned history / agent capabilities | [`../CHANGELOG.md`](../CHANGELOG.md) · [`../CLAUDE.md`](../CLAUDE.md) |
| Decode an `L##`/`O##`/`S##` finding-ID cited in a commit message | [`audit-report-2026-07.md`](audit-report-2026-07.md) (historical — all 88 findings now resolved, kept for the ID cross-reference) |

---

_Operational gotchas (Upstox chunking, F&O publish lag, lightweight-charts effect-dep stability, the stale-bundle reload) live in [`DEVELOPER_GUIDE.md`](DEVELOPER_GUIDE.md) → Gotchas & Known Issues — read them before touching warehouse, chart, or live code._
