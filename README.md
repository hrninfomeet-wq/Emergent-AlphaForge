# AlphaForge Trading Lab

A local-first research and forward-testing terminal for Indian index options on NIFTY 50, BANKNIFTY, and SENSEX. AlphaForge ingests clean market data, runs backtests with realistic costs and walk-forward, optimizes strategy parameters, and runs strategies forward against live 1-minute closes with a manual approval gate before any paper trade or recommendation.

This is a research tool. Options trading is high risk; treat every signal as a hypothesis until it survives walk-forward, forward testing, and paper trading.

## Status (2026-05-31)

| Area | Status |
|---|---|
| Local Docker stack | Working on Windows: MongoDB, FastAPI, React/nginx |
| Index data warehouse | NIFTY/BANKNIFTY/SENSEX 1m candles, ~100% coverage 2024-11-27 → today |
| Option data warehouse | NIFTY ~1.46M / BANKNIFTY ~1.69M / SENSEX ~2.21M ATM CE/PE candles (OI populated) |
| Data Hygiene | UI hero panel: check (plan ~6s) + dependency-ordered fill |
| Warehouse auto-update | On startup, OAuth-connect, and daily 18:00 IST |
| Option coverage page load | Cache-backed (~200ms) |
| Point-in-time lookup + chart | Spot/ATM lookup + per-index candlestick chart with gap detection |
| NSE holiday calendar | 2024–2026 with Budget Saturdays + shifted-expiry; holiday modal |
| Live tick → 1m OHLC roller | Running, closes Upstox same-day historical gap |
| Strategy plugin system | Built-in + drop-in `.py` plugins |
| Backtest + walk-forward | Complete with significance and regime detection |
| Optimizer | Bayesian / Grid / CMA-ES with robustness, importance, heatmap |
| Slippage + volatility | Expiry-tail slippage + post-hoc detector |
| Strategy Deployments | 1m_close evaluator running, drift detection ON |
| Pending Approval UI | Approve / Skip / Mark Blocked + auto-paper on approval |
| Auto square-off | 15:00 IST every market day, override per deployment |
| Pre-flight + quality gates | Surfaced at deployment creation, ack required |
| OAuth token-expiry countdown | In the global top bar |
| Forward metrics aggregation | Slice 10 — next |
| Per-deployment kill switches | Slice 12 — pending |

272 backend tests pass.

## Quick Start

```bash
docker compose up -d --build
```

Open:

- Frontend: `http://localhost:3000`
- Backend health: `http://localhost:8001/api/health`

Configuration:

- Copy `backend/.env.example` to `backend/.env`.
- Generate a stable `FERNET_KEY`:
  ```bash
  python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
  ```
- Add Upstox API credentials to `backend/.env` for live data.
- Never commit `.env`, access tokens, or broker account data.

## Verification

```bash
python -m pytest tests -q
cd frontend
npm run build
cd ..
docker compose up -d --build
docker compose ps
```

## Documentation

- [Project Overview](docs/PROJECT_OVERVIEW.md) — capabilities, status, workflows, lessons learned.
- [Handoff](docs/HANDOFF.md) — entry point for the next AI agent or developer. Includes operational lessons.
- [Architecture](docs/ARCHITECTURE.md) — module map, data flow, collections, design choices.
- [API Reference](docs/API_REFERENCE.md) — every backend route.
- [User Manual](docs/USER_MANUAL.md) — how to use the app.
- [Local Setup](docs/LOCAL_SETUP.md) — Docker and native installation.
- [Strategy Plugins](docs/STRATEGY_PLUGINS.md) — adding custom strategies.
- [Strategy Deployments](docs/STRATEGY_DEPLOYMENTS.md) — forward-testing model.
- [Plan](plan.md) — slice roadmap with done/next markers.

## Repository Structure

```
.
├── backend/
│   ├── app/                  Strategy modules, evaluators, data hygiene, NSE calendar, etc.
│   │   └── strategies/       Built-in strategies + drop-in plugins/
│   ├── server.py             FastAPI routes
│   ├── requirements.txt
│   └── Dockerfile
├── frontend/
│   ├── src/                  React app, Tailwind, shadcn/ui
│   ├── public/
│   └── Dockerfile
├── tests/                    223 backend tests (pytest)
├── docs/                     Project documentation
├── ltm/                      Project-local long-term memory (LTM workflow)
├── memory/                   Local notes (gitignored secrets)
├── .kiro/                    Kiro IDE steering files and hooks
├── docker-compose.yml
├── start.bat / start.sh      One-click launchers
├── plan.md                   Slice roadmap
└── README.md
```

## Project Conventions (Important)

- DTE filter default `[0..6]`. Auto square-off at 15:00 IST every market day. Time-of-day blocks 09:15–09:25 and 14:50–15:30 IST. Expiry-day cutoff at 15:00 IST.
- Lot size always read from `option_contracts.lot_size` (Upstox-supplied). Never hardcoded.
- Slippage defaults: ATM 0.5pt, OTM1/ITM1 1pt, OTM2+/ITM2+ 2pt, expiry-day 30-min 2x.
- Deployments can only be created from saved Presets or saved Backtest Runs. Direct deployment from a raw plugin is blocked.
- Walk-forward warns but does not block. The user makes a conscious choice via the ack checkbox.
- Manual approval gate before any paper trade or recommendation. No auto-execution.
- All routes under `/api`. Local Docker stack is the source of truth.

## Safety Note

Options can lose money quickly. AlphaForge surfaces realistic costs, walk-forward divergence, statistical significance, and forward session completeness so weak strategies are caught before capital is committed. The manual approval gate is intentional and must remain.
