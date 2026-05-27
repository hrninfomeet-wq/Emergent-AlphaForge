# AlphaForge Trading Lab

AlphaForge is a local-first trading research terminal for Indian index markets: NIFTY, BANKNIFTY, and SENSEX. It stores market data locally, validates coverage, runs strategy backtests, supports parameter optimization, and is being extended toward option-premium backtesting, live signals, and paper trading through Upstox.

This is a research and execution-prep tool, not a guaranteed-profit system.

## Current Status

Updated: 2026-05-27

| Area | Status |
|---|---|
| Local Docker app | Working on Windows with MongoDB, FastAPI, and React/nginx |
| Data Warehouse | Index 1-minute candles, integrity audit, clear-data tools |
| Upstox OAuth | Working locally when credentials are configured |
| Upstox index history | Working with automatic chunk guidance |
| Option contracts | Current contract sync/store implemented |
| Option candles | Historical option candle fetch/store implemented |
| Option Data Planner | Preview-first option warehouse workflow implemented |
| Option Data Audit | Contract/date-level option candle coverage implemented |
| Backtest Lab | Spot backtesting plus paired option-candle execution |
| Optimizer | Optuna/Grid/CMA-ES workflow implemented |
| Theme | System, Black, and White modes implemented |
| Market Header | Fresh WebSocket ticks with REST/API fallback |
| Signal lifecycle | Offline auditable lifecycle foundation implemented |
| Paper trading | Manual paper journal with mark/close and stop/target auto-close foundation |
| Strategy Deployments | Management foundation implemented; evaluator not complete |
| Live WebSocket signals | Tick stream foundation implemented; automated signal evaluator pending |

## Quick Start

```bash
docker compose up -d --build
```

Open:

- Frontend: `http://localhost:3000`
- Backend health: `http://localhost:8001/api/health`

Configuration:

- Copy `backend/.env.example` to `backend/.env`.
- Put real broker secrets only in `backend/.env`.
- Do not commit `.env`, access tokens, or broker account data.

## Key Documents

- [Project Overview](docs/PROJECT_OVERVIEW.md) - current capability, status, next steps, tips, recommendations, and lessons learned.
- [Architecture](docs/ARCHITECTURE.md) - backend/frontend/database module map and data flow.
- [Handoff](docs/HANDOFF.md) - concise notes for the next AI agent or developer.
- [User Manual](docs/USER_MANUAL.md) - how to use the app features.
- [Local Setup](docs/LOCAL_SETUP.md) - local Docker and environment setup.
- [Strategy Plugins](docs/STRATEGY_PLUGINS.md) - adding custom strategies.
- [Strategy Deployments](docs/STRATEGY_DEPLOYMENTS.md) - forward-testing and live-recommendation design plus current management foundation.

## Verification

Recommended checks before claiming a change is ready:

```bash
python -m pytest tests -q
cd frontend
npm run build
cd ..
docker compose up -d --build
docker compose ps
```

## Safety Note

Options trading is high risk. Backtests can degrade in live trading because of slippage, liquidity, missed candles, overfitting, and trader behavior. Treat every signal as a hypothesis until tested on clean data, forward testing, and paper trading.
