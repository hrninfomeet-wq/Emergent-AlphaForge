# Handoff — START HERE

_Entry point for the next engineer or AI agent. This is the shortest useful orientation; the repository and `tests/` are the source of truth, not any prior chat._

**Read order:** this file → [`DEVELOPER_GUIDE.md`](DEVELOPER_GUIDE.md) (the consolidated deep onboarding — run/build/test, live-trading safety model, warehouse model, India rules, research→deploy, gotchas) → [`ARCHITECTURE.md`](ARCHITECTURE.md) (technical reference). Use the ["Where to go deep"](#5-where-to-go-deep) table below to jump straight to a topic.

---

## 1. Orientation

**AlphaForge Trading Lab** is a **local-first research + forward-test app for Indian index options** (NIFTY / BANKNIFTY / SENSEX). The loop: warehouse 1-minute spot + option candles → backtest / optimize strategies → save as presets → deploy for signal generation, paper trading, and (under hard gates) live Flattrade execution.

Stack: **React** (CRA + craco) frontend, **FastAPI** (Python) backend, **MongoDB** (motor), all in **Docker Compose**. Frontend `:3000`, backend `:8001` (**every route under `/api`**), mongo `:27017`. **Upstox** = market data feed; **Flattrade** (Noren / PiConnect OMS) = live broker execution.

## 2. Current state

Everything is integrated on **`main`** — there is no live stack of feature branches to track (past feature branches are merged and deleted). The app has grown across `0.17.x → 0.48.x` (see [`../CHANGELOG.md`](../CHANGELOG.md) for versioned detail). It runs in Docker; backend code is baked into the image, so **rebuild the container after backend edits**.

Built subsystems (all verified present in `backend/app/`):

- **Data Warehouse** — `candles_1m` holds 1-minute OHLCV for the 3 indices (spot + ATM-band option contracts) + INDIAVIX. Daily ATM-band completeness model, holiday-aware NSE calendar, one-button Sync + auto-update. (`completeness.py`, `data_hygiene.py`, `nse_calendar.py`, `routers/warehouse.py`.)
- **Backtest Lab** — spot backtests + paired real-option-candle backtests; honest rupee-first metrics; optional exit/risk-control overlay (trailing / breakeven / daily caps). (`backtest.py`, `option_backtest.py`, `exit_controls.py`, `execution_policy.py`.)
- **Optimizer** — Optuna TPE / Grid / Genetic search; single vs walk-forward (honest OOS); spot vs option re-rank; capital-aware **survival gate**; exit-control search. (`optimizer.py`, `wfo.py`, `walkforward.py`, `survival.py`, `rerank_select.py`.)
- **Strategy Library** — builtin + drop-in plugin strategies; retire / delete lifecycle; multi-provider AI authoring wizard (Anthropic + Gemini; Spec + capability-aware + full-Python tiers). (`strategies/*`, `routers/strategies_admin.py`, `ai/*`.)
- **Paper Trading** — live-tick-driven realism (tick exits, poll-for-new-bar entries). (`paper_auto.py`, `paper_trading.py`, `live_exit_monitor.py`.)
- **Live Trading (Flattrade)** — offline-first; L0–L3 gate chain; the executor is the **single real-order chokepoint**; margin pre-check; OCO/GTT catastrophe backstop; kill switches; Greeks; ARMED auto-place only under an env gate **plus** per-deployment ARM **plus** caps **plus** EOD auto-disarm. (`live/executor.py`, `live/safety.py`, `live/margin.py`, `live/mode.py`, `live/arm_state.py`, `live/gtt.py`, `live/kill_switch.py`, `routers/live_broker.py`.)

Routers mounted under `/api`: `research`, `strategies_admin`, `warehouse`, `journals`, `deployments`, `broker`, `live_broker`. Frontend pages: Dashboard, DataWarehouse, BacktestLab, Optimizer, StrategyLibrary, SavedPresets, LiveSignals, SignalJournal, PaperTrading, LiveTrading, PreTradeChecklist.

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

---

_Operational gotchas (Upstox chunking, F&O publish lag, lightweight-charts effect-dep stability, the stale-bundle reload) live in [`DEVELOPER_GUIDE.md`](DEVELOPER_GUIDE.md) → Gotchas & Known Issues — read them before touching warehouse, chart, or live code._
