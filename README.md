# AlphaForge Trading Lab

A **local-first research and forward-testing terminal for Indian index options** (NIFTY 50, BANKNIFTY, SENSEX). AlphaForge maintains a clean 1-minute data warehouse, backtests strategies with honest rupee-first metrics and real option-candle execution, optimizes parameters with walk-forward (honest out-of-sample) validation, and runs surviving strategies forward — from signal-only journaling, to tick-driven paper trading, up to gated live execution through the Flattrade (Noren OMS) broker. Everything runs on your own machine in Docker; no real broker order is ever placed unless you explicitly arm it.

> This is a research tool. Index options are high-risk. Treat every signal as a hypothesis until it survives walk-forward, forward testing, and paper trading. All times are IST; the NSE session is 09:15–15:30 with a 15:00 square-off, and the calendar is holiday-aware.

## Key Capabilities

- **Data Warehouse** — 1-minute OHLCV for the 3 indices (spot + ATM-band option contracts) plus `INDIAVIX`, in the `candles_1m` collection. Daily ATM-band completeness model, holiday-aware NSE calendar (`nse_calendar.py`), one-button sync + auto-update, and Data Hygiene check/fill. (`app/completeness.py`, `app/data_hygiene.py`, `app/warehouse*.py`, `app/routers/warehouse.py`)
- **Backtest Lab** — spot backtests and paired **real-option-candle** backtests, honest rupee-first metrics, and an optional exit/risk-control overlay (trailing / breakeven / daily caps). (`app/backtest.py`, `app/option_backtest.py`, `app/exit_controls.py`, `app/execution_policy.py`)
- **Optimizer** — Bayesian (Optuna TPE) / Grid / Genetic search; single-shot **or** walk-forward (honest OOS); spot vs option re-rank evaluation; survival gate; exit-control search. (`app/optimizer.py`, `app/wfo.py`, `app/walkforward.py`, `app/survival.py`, `app/rerank_select.py`)
- **Strategy Library** — built-in strategies plus drop-in `.py` plugins, a retire/delete lifecycle, and a multi-provider AI authoring wizard (Anthropic + Gemini). (`app/strategies/*`, `app/routers/strategies_admin.py`, `app/ai/*`)
- **Paper Trading** — live-tick-driven paper realism: tick-based exits and poll-for-new-bar entries at real option premiums. (`app/paper_*.py`, `app/live_exit_monitor.py`)
- **Live Trading (Flattrade)** — offline-first; an L0–L3 gate chain with a single real-order chokepoint (the executor); margin pre-check; OCO/GTT catastrophe backstop; kill switches; Greeks. ARMED auto-place only under an env gate **and** per-deployment ARM **and** account caps, with EOD auto-disarm. (`app/live/*` — `executor.py`, `safety.py`, `margin.py`, `arm_state.py`, `gtt.py`, `kill_switch.py`; `app/routers/live_broker.py`)

## Quick Start

```bash
docker compose up -d --build backend frontend
```

Then open:

- Frontend — `http://localhost:3000`
- Backend API health — `http://localhost:8001/api/health`  *(all routes are under `/api`)*

MongoDB runs on `:27017`. First-time setup (copy `backend/.env.example` → `backend/.env`, generate a `FERNET_KEY`, add Upstox data credentials) is covered in **[docs/LOCAL_SETUP.md](docs/LOCAL_SETUP.md)**; the daily launch/OAuth routine is in **[docs/STARTUP_MANUAL.md](docs/STARTUP_MANUAL.md)**.

## Repository Layout

```
.
├── backend/            FastAPI app (app/, app/live/, app/strategies/, app/ai/, app/routers/), Dockerfile
├── frontend/           React (CRA + craco) app: src/pages, src/components, src/hooks
├── docs/               All project documentation (see index below)
├── tests/              Host + container test suite (pytest)
├── ltm/                Project-local long-term memory (LTM workflow)
├── memory/             Local notes / secrets (gitignored)
├── docker-compose.yml  mongo + backend + frontend
├── CHANGELOG.md        Versioned history (currently 0.48.x)
└── README.md
```

## Documentation Index

**New here? Read in this order:** `README.md` → **[docs/HANDOFF.md](docs/HANDOFF.md)** → **[docs/DEVELOPER_GUIDE.md](docs/DEVELOPER_GUIDE.md)** (deep onboarding) → **[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)** (technical detail).

| Doc | Purpose |
|---|---|
| [docs/HANDOFF.md](docs/HANDOFF.md) | **START HERE** — current state, how to run/test, table of contents into the deeper docs |
| [docs/DEVELOPER_GUIDE.md](docs/DEVELOPER_GUIDE.md) | **Deep onboarding** — run/build/test workflow, live-trading safety model, data-warehouse model, India trading rules, research→deploy flow, gotchas |
| [docs/PROJECT_OVERVIEW.md](docs/PROJECT_OVERVIEW.md) | What AlphaForge is and the end-to-end research→deploy workflow, at a glance |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Technical reference: stack, module map, data flow, MongoDB collections, the live-execution gate chain |
| [docs/API_REFERENCE.md](docs/API_REFERENCE.md) | Every backend HTTP route (all under `/api`) |
| [docs/USER_MANUAL.md](docs/USER_MANUAL.md) | Per-page UI guide |
| [docs/STRATEGY_PLUGINS.md](docs/STRATEGY_PLUGINS.md) | How to write a custom strategy plugin |
| [docs/STRATEGY_DEPLOYMENTS.md](docs/STRATEGY_DEPLOYMENTS.md) | The deployment model: modes, gates, kill switches, live |
| [docs/LOCAL_SETUP.md](docs/LOCAL_SETUP.md) | Install (Docker + native) |
| [docs/STARTUP_MANUAL.md](docs/STARTUP_MANUAL.md) | Daily launch + troubleshooting |
| [docs/optimizer-user-guide.md](docs/optimizer-user-guide.md) | Using the optimizer |
| ["docs/Walk-forward (honest OOS) what it does exactly.md"](docs/Walk-forward%20%28honest%20OOS%29%20what%20it%20does%20exactly.md) | What walk-forward validation actually does |
| [docs/live-readback-checklist.md](docs/live-readback-checklist.md) | Live market-hours readback runbook |
| [docs/Resources/flattrade-pi-api/INDEX.md](docs/Resources/flattrade-pi-api/INDEX.md) | Decoded Flattrade (PiConnect/Noren) broker API reference |
| [CHANGELOG.md](CHANGELOG.md) | Versioned history |
| [CLAUDE.md](CLAUDE.md) | Agent capabilities + always-loaded project notes |

## Testing

Pure/contract tests run on the host Python; motor/route tests run **inside the backend container** (`docker cp tests/. alphaforge_backend:/app/tests` then `docker exec -w /app alphaforge_backend python -m pytest ...`). Frontend "tests" are pytest string-pins over the JSX source. The final gate is a Chrome browser smoke — hard-reload (Ctrl+Shift+R) to drop the stale bundle. See [docs/DEVELOPER_GUIDE.md](docs/DEVELOPER_GUIDE.md) for the full run/build/test workflow.

## Safety Note

**No real broker order is ever placed unless the operator has explicitly armed live execution** — an environment gate (`LIVE_AUTOPLACE_ARMED=1`), a per-deployment ARM with account caps, and EOD auto-disarm all have to line up, and every real order passes through a single executor chokepoint behind an L0–L3 gate chain. Paper and signal-only deployments never touch a broker. The offline-first, no-broker-orders-by-default posture is intentional and must remain.
