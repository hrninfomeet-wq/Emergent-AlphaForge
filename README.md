# AlphaForge Trading Lab

A **local-first research, paper and live-execution lab for Indian index options**. It keeps a
1-minute warehouse of spot and option candles, backtests and optimizes strategies against real
option premiums, runs deployments forward as signal-only or paper, and — only when the operator
enables it — places real orders through Flattrade. Everything runs on your own machine in Docker
Compose. **Upstox** supplies market data; **Flattrade** (Noren / PiConnect OMS) is the live broker.

> This is a research tool. Index options are high-risk. No strategy in this repo has a proven
> after-cost edge. All times are IST; the NSE session is 09:15–15:30 with a 15:00 square-off, and
> the calendar is holiday-aware (`backend/app/nse_calendar.py`).

## Status (2026-09-30)

The research, paper and live paths are built and covered by the local suite
(**6,586 passed, 4 xfailed, 0 failed** on the host `.venv`; there is no CI, so that suite is the
only evidence). Every edge hunt so far has failed a holdout or been killed earlier — see the
research verdicts below. The latest work, the Live Deployments uplift of 2026-09-26 → 09-30
(reconcile from the trade book, the caps governor, truthful status, a server trading clock, the
tighten-caps and flatten routes, restart attribution, signal retirement), is unit- and
mutation-tested but **has not yet run in a market session**. The next gate is that market-session
validation on the Flattrade-registered static IP. The checklist is
[docs/LIVE_VALIDATION_PLAN_2026-08.md](docs/LIVE_VALIDATION_PLAN_2026-08.md) §1, followed by the
rest of that plan. The gate stays open until the plan's §11 records an outcome. The resting broker
OCO is **off by default** (`LIVE_BROKER_OCO_ENABLED=0`), so the in-process software exit guard is
the only protection for an open position, and it runs only while the app runs. Detail:
[docs/HANDOFF.md](docs/HANDOFF.md) §2.

## Key capabilities

| Area | What it does | Where |
|---|---|---|
| Data warehouse | 1-minute spot candles for NIFTY, BANKNIFTY and SENSEX plus `INDIAVIX` (`candles_1m`), ATM-band option candles (`options_1m`) and the contract universe (`option_contracts`). Completeness model, holiday-aware calendar, auto-update, Data Hygiene check/fill. Current research and live work is on NIFTY and SENSEX weeklies. | `app/warehouse*.py`, `app/completeness.py`, `app/data_hygiene.py`, `app/instruments.py`, `app/routers/warehouse.py` |
| Backtest Lab | Spot backtests and paired real-option-candle backtests with rupee metrics, statutory costs and an exit/risk-control overlay. | `app/backtest.py`, `app/option_backtest.py`, `app/exit_controls.py` |
| Optimizer | Optuna TPE / grid / CMA-ES search, single-shot or walk-forward, with an option-premium re-rank of the top candidates. The trial objective is a spot-point proxy (see `docs/AGENT_TODO.md` items 1–2). | `app/optimizer.py`, `app/wfo.py`, `app/walkforward.py`, `app/rerank_select.py` |
| Strategy Library | One built-in (`confluence_scalper`) plus drop-in plugins in `backend/app/strategies/plugins/` (bind-mounted into the container), a retire/delete lifecycle, and an AI authoring wizard (Anthropic or Gemini). | `app/strategies/`, `app/ai/`, `app/routers/strategies_admin.py` |
| Deployments | Saved presets or library strategies deployed as signal-only, paper or live. Signals are journalled with a lifecycle (ACTIVE → EXITED; unactioned ones retire to AUDITED). | `app/strategy_deployments.py`, `app/deployment_evaluator.py`, `app/signal_lifecycle.py` |
| Paper trading | Tick-driven exits and new-bar entries at real option premiums. | `app/paper_*.py`, `app/live_exit_monitor.py` |
| Live trading (Flattrade) | One real-order chokepoint (the executor), margin pre-check, per-deployment and account caps, kill switches, a software exit guard, startup reconcile against the broker books, Greeks. | `app/live/`, `app/auto_live.py`, `app/live_deploy_governor.py`, `app/routers/live_broker.py`, `app/routers/deployments.py` |

## Quick start (Windows + Docker)

Prerequisites: Docker Desktop, and a filled-in `backend\.env` (copy `backend\.env.example`, set a
stable `FERNET_KEY` and your Upstox data credentials). First-time install, env setup and
troubleshooting are in [docs/STARTUP_MANUAL.md](docs/STARTUP_MANUAL.md).

```bat
"<absolute path to checkout>\start-app.bat"
"<absolute path to checkout>\start-app.bat" --rebuild
```

The first form starts the stack, reusing containers that are already healthy; `--rebuild` forces a
rebuild of both images and briefly interrupts the software exit guard, so use it only with no open
live position. Other flags: `--check-only`, `--no-browser`, `--help`. The launcher starts
Docker Desktop if needed and opens the browser only after both services are healthy. Double-click
it, or call it by absolute path: the operator's PC sets `NoDefaultCurrentDirectoryInExePath=1`, so
a bare `start-app.bat` typed in the project folder is "not recognized". On Mac/Linux use
`./start.sh`.

Manual equivalent:

```bash
docker compose up -d --build     # build and start mongo + backend + frontend
docker compose ps                # alphaforge_mongo / alphaforge_backend healthy
```

| Service | Address |
|---|---|
| Frontend | `http://localhost:3000` (use `localhost`, not `127.0.0.1`: `CORS_ORIGINS` allows only `http://localhost:3000`) |
| Backend | `http://localhost:8001/api` (every route is under `/api`; health at `/api/health` returns `{"db":"ok"}`) |
| MongoDB | `127.0.0.1:27017`, container `alphaforge_mongo`, named volume `mongo_data` (no auth; loopback only) |

**Backend code is baked into the image.** Only `backend/app/strategies/plugins` is bind-mounted
(plus the machine-specific `C:/Users/haroo/.flattrade` -> `/host-flattrade`, the Flattrade MCP
session-sync target),
so after changing backend code run `docker compose up -d --build` (with no open live position:
recreating the backend pauses the guard). A plain restart, or
`start-app.bat` without `--rebuild` while the backend is healthy, keeps running the old code.
Never run `docker compose down -v`: it deletes the warehouse volume.

## Repository layout

```
.
├── backend/            FastAPI (server.py; app/, app/live/, app/strategies/, app/ai/, app/routers/), scripts/, Dockerfile
├── frontend/           React (CRA + craco): src/pages, src/components, src/lib, src/hooks
├── tests/              pytest suite (runs on the host .venv; frontend logic runs through node)
├── docs/               Documentation (index below)
├── memory/             Local notes (gitignored except .gitkeep)
├── docker-compose.yml  mongo + backend + frontend, all bound to 127.0.0.1
├── start-app.bat       Windows launcher (start.sh for Mac/Linux)
├── CHANGELOG.md        What shipped and what was measured, newest first
├── learning_log.md     Per-session lessons and dead ends
└── CLAUDE.md, AGENTS.md  Always-loaded notes for AI agents
```

## Documentation index

**Read in this order:** [docs/HANDOFF.md](docs/HANDOFF.md) →
[docs/AGENT_TODO.md](docs/AGENT_TODO.md) →
[docs/BACKTEST_INTEGRITY_AUDIT.md](docs/BACKTEST_INTEGRITY_AUDIT.md) → the manuals as needed.

### Start here

| Doc | Purpose |
|---|---|
| [docs/HANDOFF.md](docs/HANDOFF.md) | **START HERE.** Current state, traps, run and test, standing conventions, where to go deep |
| [docs/AGENT_TODO.md](docs/AGENT_TODO.md) | The only live work board. The next market-session checklist is [docs/LIVE_VALIDATION_PLAN_2026-08.md](docs/LIVE_VALIDATION_PLAN_2026-08.md) §1, which AGENT_TODO points to |
| [docs/agent-takeover-prompt.md](docs/agent-takeover-prompt.md) | Paste-in prompt for a new AI agent |
| [docs/PROJECT_OVERVIEW.md](docs/PROJECT_OVERVIEW.md) | What the app is and the research → deploy workflow at a glance |
| [CHANGELOG.md](CHANGELOG.md) | What shipped, with what was measured |
| [learning_log.md](learning_log.md) | Lessons per session: what worked, dead ends |
| [CLAUDE.md](CLAUDE.md) · [AGENTS.md](AGENTS.md) | Always-loaded agent notes: Flattrade MCP rules, PDF tooling, decoded broker API |

### Manuals

| Doc | Purpose |
|---|---|
| [docs/STARTUP_MANUAL.md](docs/STARTUP_MANUAL.md) | First-time install, daily launch (`start-app.bat`), stop/restart/logs, troubleshooting |
| [docs/USER_MANUAL.md](docs/USER_MANUAL.md) | Per-page UI guide |
| [docs/DEVELOPER_GUIDE.md](docs/DEVELOPER_GUIDE.md) | Deep onboarding: build/test workflow, live-trading safety model, warehouse model, India rules, gotchas |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Stack, module map, data flow, Mongo collections, live gate chain |
| [docs/API_REFERENCE.md](docs/API_REFERENCE.md) | Backend HTTP routes (all under `/api`) |
| [docs/STRATEGY_PLUGINS.md](docs/STRATEGY_PLUGINS.md) | How to write a strategy plugin |
| [docs/STRATEGY_DEPLOYMENTS.md](docs/STRATEGY_DEPLOYMENTS.md) | Deployment model: modes, live enable/pause/stop, caps, kill switches |
| [docs/optimizer-decision-guide.md](docs/optimizer-decision-guide.md) | What each optimizer control is worth and how to read a result |

### Live safety and validation

| Doc | Purpose |
|---|---|
| [docs/LIVE_VALIDATION_PLAN_2026-08.md](docs/LIVE_VALIDATION_PLAN_2026-08.md) | The controlling market-session validation plan |
| [docs/live-readback-checklist.md](docs/live-readback-checklist.md) | Supervised real-money readback runbook; §E1 is the broker-OCO pairing readback |
| [docs/forward-validation-policy.md](docs/forward-validation-policy.md) | Paper → live promotion contract for the ₹2,00,000 account |
| [docs/flattrade-mcp-integration.md](docs/flattrade-mcp-integration.md) | The Flattrade MCP shares the broker session: never log in/out through it, never place orders through it |
| [docs/AUTONOMY_DEVELOPMENT_PLAN_2026-08.md](docs/AUTONOMY_DEVELOPMENT_PLAN_2026-08.md) | Spec for E1, the durable live execution episode ledger (next substantial development) |
| [docs/durable-static-ip-deployment.md](docs/durable-static-ip-deployment.md) | Design for hosting on an always-on VPS with a reserved IPv4 |
| [docs/live-cockpit-audit-2026-07-25.md](docs/live-cockpit-audit-2026-07-25.md) | Live Cockpit findings register (includes unverified claims) |
| [docs/audit-verification-2026-08-14.json](docs/audit-verification-2026-08-14.json) | Ledger behind "audit finding [n]" citations on the live-exit path |
| [docs/superpowers/specs/](docs/superpowers/specs/) | Design records: live-guard layers 1–2, optimizer spawn pool, live controls, Live Deployments uplift handoff |

### Research verdicts (closed; do not re-run without new evidence)

| Doc | Purpose |
|---|---|
| [docs/BACKTEST_INTEGRITY_AUDIT.md](docs/BACKTEST_INTEGRITY_AUDIT.md) | **Read before trusting any backtest or optimizer number.** Permanent register |
| [docs/OPTIMIZER_VERDICT_2026-07.md](docs/OPTIMIZER_VERDICT_2026-07.md) | Does the optimizer earn its complexity |
| [docs/PREMIUM_MOMENTUM_EDGE_VERDICT_2026-07.md](docs/PREMIUM_MOMENTUM_EDGE_VERDICT_2026-07.md) | Premium-momentum edge hunt: gate failed on the holdout |
| [docs/POOLED_REGIME_VERDICT_2026-07.md](docs/POOLED_REGIME_VERDICT_2026-07.md) | Pooled NIFTY + SENSEX regime routing: killed at validation |
| [docs/PROFIT_LEVERAGE_ANALYSIS_2026-07.md](docs/PROFIT_LEVERAGE_ANALYSIS_2026-07.md) | Where profit could come from: ranked directions and their kill criteria |
| [docs/OPTION_BUYING_MICROSTRUCTURE_2026-08.md](docs/OPTION_BUYING_MICROSTRUCTURE_2026-08.md) | The option buyer's payoff and friction, measured |
| [docs/INTRADAY_OPTION_BUYING_CANDIDATES_2026-08.md](docs/INTRADAY_OPTION_BUYING_CANDIDATES_2026-08.md) | Intraday option-buying campaign: ATM baseline has no edge on either index; short side closed |
| [docs/atr-sigma-router-optimizer-results-2026-08-16.md](docs/atr-sigma-router-optimizer-results-2026-08-16.md) | `atr_sigma_router` optimizer winners, all failed out-of-sample |
| [docs/option-data-provenance.md](docs/option-data-provenance.md) | Historical option candles are research-grade, not point-in-time certified |

### Reference

| Doc | Purpose |
|---|---|
| [docs/Resources/flattrade-pi-api/INDEX.md](docs/Resources/flattrade-pi-api/INDEX.md) | Decoded Flattrade PiConnect API (58 endpoints, plus `catalog.json`) |
| [docs/audit-report-2026-07.md](docs/audit-report-2026-07.md) | Decoder for `L##` / `O##` / `S##` finding IDs in commit messages |
| [docs/NEXT_STAGE_ROADMAP_2026-07.md](docs/NEXT_STAGE_ROADMAP_2026-07.md) | 2026-07-31 roadmap; its Stage 2 Dashboard v2 and live-chart specs are still unbuilt |
| [docs/DTE_OPENING_SHOCK_STRATEGY.md](docs/DTE_OPENING_SHOCK_STRATEGY.md) | Operating spec for the `dte_opening_shock_breakout` plugin |
| [docs/NF_CE_PE_EXP2_Strategy_Spec.md](docs/NF_CE_PE_EXP2_Strategy_Spec.md) | Decoded AlgoTest "NF CE PE EXP2" strategy, the reference for the premium-momentum work |

Removed docs stay recoverable from git history (`git log --diff-filter=D --name-only -- docs/`).

## Testing

```bash
./.venv/Scripts/python.exe -m pytest tests/ -q -p no:cacheprovider   # whole suite, host, ~4 min
cd frontend && CI=true npx --no-install craco build                   # frontend gate: warnings fail it
```

Run the suite on the host `.venv`. It needs `node` for the tests that execute frontend logic
(`frontend/src/lib/*.js`). The backend container is built from `backend/` only, so it has no
`frontend/` or `tests/` tree. There is no CI. For UI changes, finish with a browser check at
`http://localhost:3000` after a hard reload (Ctrl+Shift+R) to drop the stale bundle.

## Safety note

**No real broker order is placed unless the operator enables live execution on a deployment.**
A deployed entry transmits only when `deployment.mode == "live"`, the broker session is connected,
the time is before the 15:00 IST entry cutoff, the per-deployment and account caps allow it, and
the `LIVE_AUTOPLACE_ARMED=1` environment gate is set. Without that gate the executor dry-runs and
sends nothing. Every real order passes through one executor chokepoint. Signal-only and paper
deployments never touch the broker. Exits are different: the software guard always transmits its
exits for positions it owns.

> The per-deployment ARM ceremony and the research-qualification gate were removed on explicit
> operator instruction (v0.56.0). Do not reintroduce either. The data-integrity gate that blocks
> a NEW live activation when today's candles cannot be verified is a different thing and is
> intentional — see [docs/HANDOFF.md](docs/HANDOFF.md) §4.2.
