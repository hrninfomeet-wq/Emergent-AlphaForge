# AlphaForge Trading Lab

**A professional trading research platform for Indian markets (NIFTY 50, BANKNIFTY, SENSEX) — backtesting, parameter optimization, and live signal generation for option buying.**

> Built on React + FastAPI + MongoDB. Designed for serious retail traders who demand statistical honesty (walk-forward, confidence intervals, robustness scoring) and want a true prop-desk-grade tool.

---

## Status

| Phase | Title | Status |
|---|---|---|
| 1 | Core POC | ✅ Complete |
| 2 | V1 Lab (6 strategies + warehouse + multi-pane charts) | ✅ Complete |
| 3 | Auto-Optimizer (Optuna TPE + Grid + CMA-ES + heatmap + robustness) | ✅ Complete |
| 3.5 | User-feedback fixes (progress bar, presets, stop button, exports, view-best-in-lab) | ✅ Complete |
| 4 | Upstox OAuth + WS tick stream + Live Signals + Options backtest | ⏳ Not started |
| 5 | Probabilistic exit engine + meta-model + position sizing + Telegram | ⏳ Not started |
| 6 | Swing/positional extension | ⏳ Not started |
| 7 | Local Docker Compose deploy package | ✅ Complete (this commit) |

---

## What Works Today

1. **Data Warehouse** — yfinance ingestion for NIFTY/BANKNIFTY/SENSEX 1-minute candles, cache-first MongoDB persistence, per-day SHA-256 integrity hashes, visual coverage heatmap.
2. **6 Pluggable Strategies** — Confluence Scalper, VWAP Pullback, ORB, SMC Liquidity Sweep + FVG, Fibonacci Pullback, VWAP Mean Reversion. Drop-in custom Python plugins auto-discovered.
3. **Backtest Engine** — vectorized SPOT-mode backtest with realistic Indian intraday cost model (slippage + brokerage + STT + GST proxy), walk-forward IS vs OOS validation, statistical significance badge (Wilson 95% CI), signal funnel telemetry.
4. **Auto-Optimizer** — Optuna Bayesian (TPE), Grid, Genetic (CMA-ES) with walk-forward, parameter importance, 2D heatmap, robustness score (perturbation ±10/20%), top-N alternatives. One-click "Save best as Preset" + "View Best in Lab" + Stop button.
5. **Pre-Trade Checklist** — 3 configurable profiles (Conservative / Balanced / Aggressive) with 10+ filter sliders, anti-over-filter safeguard, regime gate, bar-close confirmation.
6. **Signal Journal** — click-to-load past runs, filter, bulk-delete, full audit trail of every backtest.
7. **Multi-pane synchronized charts** (TradingView Lightweight Charts) — price candles + equity curve + drawdown on shared time axis.
8. **Exports** — Config JSON, Result JSON, Trades CSV (Backtest Lab), Alts CSV (Optimizer).

---

## Quick Start (Local Docker)

See [`docs/LOCAL_SETUP.md`](docs/LOCAL_SETUP.md) for full instructions.

```bash
git clone <your-repo-url>
cd alphaforge-trading-lab
docker-compose up -d
# open http://localhost:3000
```

## Architecture

See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

## How to Add a Custom Strategy

See [`docs/STRATEGY_PLUGINS.md`](docs/STRATEGY_PLUGINS.md).

## API Reference

See [`docs/API_REFERENCE.md`](docs/API_REFERENCE.md).

## For the Next AI Agent / Developer

See [`docs/HANDOFF.md`](docs/HANDOFF.md) — **read this before changing anything**.

## Development Journey

See [`docs/DEVELOPMENT_JOURNEY.md`](docs/DEVELOPMENT_JOURNEY.md) — phases, decisions, and lessons learned.

---

## License & Disclaimer

This is a research tool. **Live signals are recommendations, not orders.** Trading options involves substantial risk. Backtest performance does NOT guarantee live performance — typical retail traders see 30-40% degradation due to slippage, latency, and discipline. The probabilistic engine (Phase 5) requires ≥6 months of warehouse history for statistical reliability.

**Top 1% option buyers** combine: ① a good system (this app), ② strict discipline (this app enforces it), ③ psychology (your job), ④ capital management, ⑤ patience.
