# Architecture

## Stack

| Layer | Technology | Why |
|---|---|---|
| Frontend | React 19 + Tailwind + shadcn/ui + TradingView Lightweight Charts v5 + Recharts | Bloomberg-grade UI, industry-standard charts |
| Backend | FastAPI (async) + Pydantic v2 + Motor (async MongoDB) | Vectorized indicators run in process; async I/O for warehouse |
| Database | MongoDB | Time-series-friendly, flexible schema, replica-set compatible |
| Indicators | NumPy + pandas (vectorized) | 100× faster than per-row loops |
| Optimizer | Optuna 4.8 (TPE + CMA-ES samplers) + scikit-learn (importance) | Battle-tested Bayesian optimization |
| Data Source | yfinance (POC) → Upstox V3 (production, Phase 4) | yfinance: zero-config but 30-day 1m cap; Upstox: years of history |

## Module Map

```
/app/
├── backend/
│   ├── server.py              # FastAPI app + all /api routes
│   ├── test_core.py           # Phase 1 POC validation (single-file E2E)
│   ├── requirements.txt
│   ├── .env                   # MONGO_URL, DB_NAME, EMERGENT_LLM_KEY
│   └── app/
│       ├── db.py              # MongoDB client, indexes, serialize_doc
│       ├── models.py          # Pydantic request/response models
│       ├── indicators.py      # EMA, RSI, MACD, ATR, VWAP, ADX, Choppiness, Fibonacci, FVG, swing points
│       ├── regime.py          # TREND / TREND_EXPANDING / CHOP / VOLATILE_CHOP / MIXED classifier
│       ├── costs.py           # Indian intraday cost model (~1.5 pts/round-trip NIFTY)
│       ├── yfinance_source.py # NIFTY=^NSEI / BANKNIFTY=^NSEBANK / SENSEX=^BSESN
│       ├── warehouse.py       # Cache-first MongoDB warehouse v2 with integrity hashes
│       ├── backtest.py        # Vectorized backtest engine + metrics + Wilson CI badging
│       ├── walkforward.py     # Rolling train/test, OOS stitching, divergence detection
│       ├── optimizer.py       # Optuna TPE + Grid + CMA-ES + heatmap + importance + robustness
│       └── strategies/
│           ├── base.py        # StrategyBase + Signal + StrategyRegistry (auto-discovery)
│           ├── builtin/       # 6 built-in strategies
│           │   ├── confluence_scalper.py
│           │   ├── vwap_pullback_scalp.py
│           │   ├── opening_range_breakout.py
│           │   ├── smc_liquidity_sweep_fvg.py
│           │   ├── fibonacci_pullback.py
│           │   └── vwap_mean_reversion.py
│           └── plugins/       # Drop your custom .py files here — auto-discovered on startup
│
├── frontend/
│   ├── package.json
│   ├── .env                   # REACT_APP_BACKEND_URL
│   └── src/
│       ├── App.js             # Router + Layout shell
│       ├── index.css          # AlphaForge dark theme tokens + utilities
│       ├── lib/
│       │   ├── api.js         # axios wrapper (all backend calls)
│       │   ├── fmt.js         # locale-safe formatters (avoid toLocaleString — POSIX runtime fails)
│       │   ├── exports.js     # client-side JSON/CSV downloads
│       │   └── optExports.js  # optimizer-specific exports
│       ├── components/
│       │   ├── Layout.jsx           # Left rail + top bar
│       │   ├── RegimeBadge.jsx
│       │   ├── SignificanceBadge.jsx # 🟢 SIGNIFICANT / 🟡 BORDERLINE / 🔴 WEAK
│       │   ├── MetricCard.jsx
│       │   ├── NumberSliderInput.jsx # combined slider + typeable number input
│       │   └── charts/
│       │       ├── MultiPaneChart.jsx # price + equity + drawdown synced
│       │       └── MiniChart.jsx
│       └── pages/
│           ├── Dashboard.jsx
│           ├── BacktestLab.jsx
│           ├── Optimizer.jsx        # full Phase 3 UI
│           ├── StrategyLibrary.jsx
│           ├── DataWarehouse.jsx    # coverage heatmap + ingest
│           ├── PreTradeChecklist.jsx
│           ├── SignalJournal.jsx
│           ├── PaperTrading.jsx     # Phase 4 placeholder
│           └── LiveSignals.jsx      # Phase 4 placeholder
│
├── docs/                       # ← you are here
├── docker-compose.yml          # Phase 7 local deploy
├── start.sh / start.bat        # Phase 7 launchers
└── plan.md                     # full development plan
```

## MongoDB Collections

| Collection | Indexes | Purpose |
|---|---|---|
| `candles_1m` | `(instrument, ts)` UNIQUE | 1-minute OHLCV candles |
| `warehouse_runs` | `(started_at, -1)` | Ingest run audit log |
| `integrity_hashes` | `(instrument, date)` UNIQUE | SHA-256 per-day OHLCV hash + candle count |
| `backtest_runs` | `(created_at, -1)` | Every backtest result with trades + equity + walk-forward + significance |
| `signals` | `(created_at, -1)` | (Reserved for Phase 4 live signal lifecycle) |
| `presets` | `(name)` UNIQUE | Named backtest configs (including optimizer-derived) |
| `pretrade_profiles` | `(name)` UNIQUE | Conservative / Balanced / Aggressive (seeded on startup) |
| `optimization_jobs` | `(created_at, -1)` | Optimizer jobs with best_params, heatmap, importance, robustness, best_backtest_run_id |

## Data Flow

```
[yfinance / Upstox]  →  ingest_yfinance / (Phase 4 ingest_upstox)
      ↓
[candles_1m + integrity_hashes]
      ↓
precompute_all_indicators (vectorized)  →  regime classifier
      ↓
┌─────────────────┬──────────────────┐
│                 │                  │
run_backtest    walk_forward    optimizer.run_optimization
      ↓             ↓                  ↓
[backtest_runs]                  [optimization_jobs]
                                       ↓ (auto-save best)
                                 [backtest_runs] ← linked via optimization_job_id
                                       ↓ (apply-as-preset)
                                 [presets]
```

## Strategy Plugin Contract

Every strategy inherits from `StrategyBase` (in `backend/app/strategies/base.py`):

- **Class attrs**: `id`, `name`, `version`, `description`, `supported_instruments`, `supported_modes`, `supported_timeframes`, `parameter_schema`.
- **`parameter_schema`**: `{name: {type: 'int'|'float'|'bool', min, max, default}}`. Used for UI form generation + optimizer bounds.
- **`evaluate(row, prev, params, ctx) -> Signal`**: pure function over a single bar. `ctx` includes `history_df`, `i` (current bar index), and ORB strategy gets `orb_hi`/`orb_lo` per session.
- **`Signal`**: `direction` ("CE"/"PE"/"NONE"), `score` (0-100), `reasons` (list), `blockers` (list — non-empty kills the signal), `spot_target_pts`, `spot_stop_pts`.

## Cost Model (SPOT mode)

- NIFTY/SENSEX: 1.5 pts round-trip (slippage + brokerage proxy + STT + GST)
- BANKNIFTY: 4.0 pts (larger underlying)
- See `app/costs.py`. Options-mode cost model is a Phase 4 task (percentage-of-premium).

## Regime Detection

`regime_label(adx, chop, atr_now, atr_avg)`:
- `TREND_EXPANDING`: ADX≥25 AND CHOP<40 AND ATR/ATR_avg≥1.15
- `TREND`: ADX≥25 AND CHOP<40
- `VOLATILE_CHOP`: ADX<20 AND CHOP>60 AND expanding
- `CHOP`: ADX<20 AND CHOP>60
- `MIXED`: everything else
- `UNKNOWN`: indicators not warmed up

Used as a filter (configurable per pre-trade profile).

## Walk-Forward

- 3 folds by default. Each fold: train on first 60%, test on remaining 40%, slide forward.
- Reports per-fold IS vs OOS metrics.
- Stitched OOS equity curve.
- **Divergence warning** if `abs(IS_winrate − OOS_winrate) > 15%`.

## Statistical Significance Badge

Uses Wilson 95% CI on win rate:
- `SIGNIFICANT`: n≥100 AND PF≥1.3
- `BORDERLINE`: n≥30 AND PF≥1.0
- `WEAK`: otherwise

## Optimizer

- **Methods**: `bayesian` (Optuna TPE), `grid` (sampled to budget), `genetic` (Optuna CMA-ES).
- **Objectives**: `risk_adjusted` (default = Sharpe / max(1, |MaxDD|/100)), `sharpe`, `profit_factor`, `total_pnl_pts`, `win_rate`, `neg_max_dd`.
- **Indicator pre-compute happens ONCE** for the whole optimization → 100× speedup.
- **Auto-cancellation**: every 5 trials, worker checks `optimization_jobs.cancelled` flag.
- **Auto-save best**: after completion, runs one final full backtest with best params + walk-forward → persisted to `backtest_runs` with `config.optimization_job_id` link.
- **Robustness**: each numeric param perturbed by ±10/20%; score = % of perturbations within 85% of best objective.
- **Heatmap**: 8×8 grid over top-2 important params.
