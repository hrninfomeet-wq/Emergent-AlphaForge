# Project Overview

Updated: 2026-05-29

## What AlphaForge Is

AlphaForge is a local-first research and forward-testing terminal for Indian index options on NIFTY 50, BANKNIFTY, and SENSEX. It stores market data on disk, audits coverage, runs backtests, optimizes strategy parameters, and runs strategies forward against live 1-minute closes with a manual approval gate before any paper trade or recommendation is acted on.

It is not a guaranteed-profit system. It is the disciplined research and execution-prep stack a serious systematic options trader would build for themselves.

## Project Objective

End-to-end quant workflow:

1. Ingest clean index and option data into a local warehouse with integrity audits.
2. Build and tune strategies in a research lab with realistic costs and walk-forward validation.
3. Auto-optimize parameters with multiple search methods and robustness scoring.
4. Forward-test the optimized strategy on live 1-minute closes with full audit trail.
5. Approve, paper-trade, or skip every signal with a manual gate.
6. Review forward profitability per deployment before trusting a strategy with capital.

## Status Snapshot (2026-05-29)

| Area | Status |
|---|---|
| Local Docker stack | Working on Windows: MongoDB, FastAPI, React/nginx |
| Index data warehouse | NIFTY/BANKNIFTY/SENSEX 1m candles, ~138.8K each, 100% coverage 2024-11-27 → today |
| Option warehouse | NIFTY 1.44M / BANKNIFTY 1.69M / SENSEX 2.21M ATM CE/PE candles |
| NSE holiday calendar | 2024–2026 with Budget Saturdays + shifted-expiry exceptions |
| Live tick → 1m OHLC roller | Running, closes Upstox same-day historical gap |
| Strategy plugin system | Built-in + drop-in `.py` plugins |
| Backtest + walk-forward | Complete; statistical significance and regime detector wired |
| Optimizer | Bayesian, Grid, CMA-ES; robustness, importance, heatmap |
| Slippage + volatility | Slice 7 done; expiry-tail slippage + post-hoc detector |
| Strategy Deployments | 1m_close evaluator running, scheduler ON, drift detection ON |
| Pending Approval UI | Approve / Skip / Mark Blocked + auto-paper-trade on approval |
| Auto square-off | 15:00 IST every market day, override per deployment |
| Pre-flight + quality gates | Surfaced at deployment creation, ack required for warnings |
| Forward metrics aggregation | **Slice 10 — next** |
| Per-deployment kill switches | Slice 12 — pending |
| Phase 5 probability engine | Deferred until ≥6 months forward signal history |
| Phase 6 swing extension | Not started |

223 backend tests pass. The local stack is healthy. Latest GitHub commit on `main`: `ae4428f`.

## Capabilities Summary

### Data warehouse

- Index 1-minute candles for NIFTY, BANKNIFTY, SENSEX in `candles_1m`, audited per day in `integrity_hashes`.
- Option 1-minute candles in `options_1m` with OI preserved.
- Option contract metadata in `option_contracts` with strike, side, expiry date, lot size from Upstox.
- Background ingest jobs for large 12–18 month index ranges with progress polling.
- Background option fetch jobs that fetch only the selected contract dates rather than the full date range per contract.
- Option Data Planner with preview, ATM-only default, configurable OTM/ITM, expiry mode, sample interval, and max-contract guard.
- Option Coverage Heatmap and Raw Option Universe Audit for diagnostics.
- Data Hygiene workflow that diff's the desired warehouse against current state and submits dependency-ordered fetches.
- NSE holiday calendar with Budget Saturday and shifted-expiry support.

### Research

- 6 built-in strategies plus drop-in `.py` plugins.
- Backtest with realistic Indian intraday costs.
- Walk-forward IS/OOS with divergence flag.
- Statistical significance (Wilson 95% CI).
- Regime detector (ADX + Choppiness + ATR expansion).
- Pre-trade checklist with 3 profiles and live signal-pass counter.
- Optimizer (Bayesian / Grid / CMA-ES) with robustness scoring, parameter importance, top-N alternatives.
- Slippage model (ATM 0.5pt, OTM1/ITM1 1pt, OTM2+/ITM2+ 2pt, expiry-day 30-min 2x) wired into paired option backtests.
- Post-hoc volatility detector (5-min realized vs 30-day rolling baseline, spike threshold 2.5x).

### Forward testing

- Strategy Deployments persisted in `strategy_deployments`, created only from saved presets or saved backtest runs.
- Strategy source SHA pinned at creation; auto-pause on drift.
- Pre-flight check (spot coverage, upcoming expiries, active vs expired contracts, Upstox token state).
- Deployment quality warnings (missing walk-forward, divergence, low trade count, weak Sharpe, large drawdown) with required user acknowledgment.
- 1m_close evaluator runs every minute boundary +10s during NSE market hours.
- Time-of-day blocks (09:15–09:25 and 14:50–15:30 IST) and expiry-day cutoff at 15:00 IST.
- DTE filter (default `[0..6]`) and `option_no_data` blockers.
- Concurrency rule: keep highest-score per `(instrument, candle_ts)`.
- Audit trail invariants: `bar_ts`, `decision_ts`, `strategy_id`, `strategy_version`, `strategy_hash`, `pretrade_settings_snapshot`, `regime`, `option_contract`, `tracked_for_pnl`, `blockers[]`.

### Approval and paper trading

- Pending Approval panel shows CONFIRMED deployment-generated signals with Approve / Skip / Mark Blocked.
- Approve auto-creates a paper trade when `deployment.mode == "paper"`, with lot size from `option_contracts.lot_size` and `lots` from `deployment.risk.default_lots` (default 1).
- Auto square-off at 15:00 IST every market day. `risk.allow_overnight=true` opts out per deployment.
- Failure to create the trade does not roll back the approval. The signal carries a `paper_trade_error`.

### Live data

- Upstox V3 read-only WebSocket market-data stream auto-starts on backend boot.
- Live tick → 1m OHLC roller subscribes to the broadcast and persists rolled bars into `candles_1m` so the evaluator fires on intraday bars.
- Market header prefers fresh ticks and falls back to REST quotes when ticks are stale or absent.

## Architecture

The system is a React + FastAPI + MongoDB stack running locally via Docker Compose. The backend exposes REST under `/api`, talks to Upstox over REST and a V3 WebSocket, and persists everything to MongoDB. The frontend is a single-page app with theme tokens and shadcn/ui components. See `docs/ARCHITECTURE.md` for the full module map, data flow diagram, and collection list.

## Key Workflows

### 1. Refresh the warehouse

1. Open Data Warehouse.
2. Confirm Upstox is connected.
3. Run Data Hygiene plan: `POST /api/data-hygiene/plan` (or the UI button when wired).
4. Execute the plan: `POST /api/data-hygiene/execute`. The flow is spot → contracts → option_candles in dependency order.
5. Re-run plan to confirm gaps closed.
6. Inspect Option Coverage Heatmap and the Trust Audit page.

### 2. Build and tune a strategy

1. Pick a strategy in Backtest Lab. Add params, costs, walk-forward.
2. Save the result. Open Optimizer with the same strategy + window.
3. Run Bayesian search with risk-adjusted objective. Review robustness and alternatives.
4. Apply best params as a Preset.

### 3. Deploy for forward testing

1. From Live Signals, click Create Deployment.
2. Choose source: a saved Preset or a saved Backtest Run.
3. The Pre-flight badge highlights data realism warnings.
4. The Quality badge surfaces walk-forward, trade-count, Sharpe, and drawdown warnings.
5. Tick the acknowledgment checkbox if any warnings are present (HTTP 400 otherwise).
6. Choose mode (`shadow`, `paper`, `recommendation`), DTE filter, default lots, and `allow_overnight` if desired.
7. Save. The deployment is `ACTIVE`. The scheduler picks it up on the next minute boundary +10s during market hours.

### 4. Approve or skip signals

1. The Pending Approval panel auto-refreshes every 15 seconds.
2. For each CONFIRMED signal, click Approve, Skip, or Mark Blocked.
3. Approve transitions CONFIRMED → TRIGGERED → ACTIVE and (in paper mode) auto-creates a paper trade.
4. Skip transitions CONFIRMED → SKIPPED → AUDITED.
5. Mark Blocked moves any non-AUDITED signal to AUDITED with the supplied note as a blocker.

### 5. Review forward performance

1. Inspect signals per deployment via `GET /api/deployments/{id}/signals`.
2. Inspect paper trades via `GET /api/paper/trades`.
3. Slice 10 will roll these into per-deployment win-rate, avg P&L, profit factor, and session completeness. Until that lands, the data is in the audit trail but not yet aggregated.

## What Is Not Done

- **Slice 10 — Forward metrics aggregation per deployment.** Win-rate, avg P&L, profit factor, annotated with session completeness ≥70% of 10:00–15:00 IST, surfaced in Strategy Library when ≥10 complete sessions exist.
- **Slice 12 — Per-deployment kill switches.** `max_consecutive_losses`, `daily_loss_cutoff_pct`, `max_open_paper_trades`.
- Phase 5 — probability engine (Kaplan–Meier survival), meta-model, Kelly sizing, Telegram alerts. Deferred until ≥6 months forward history exists.
- Phase 6 — swing/positional extension on 1H/1D timeframes. Not started.
- Online hosting / always-on uptime. Local PC is the runtime. Forward sessions are intermittent — `session_completeness` is the right concept.
- No automatic broker order placement. The manual approval gate is intentional and must remain.

## Recommendations And Tips Discovered During Development

These are the practical lessons. Treat them as project conventions.

### Project-wide

- Trust data first. A polished UI is meaningless without clean candles, correct expiries, and realistic costs.
- Build small verified slices. Each slice gets backend tests, a UI surface, a live verification, and a commit before the next slice starts.
- Use stored option contract metadata for expiry resolution. Never hardcode weekday rules.
- Lot size always comes from `option_contracts.lot_size` (Upstox-supplied). It changes; do not pin it.
- Walk-forward acceptance: warn but never block. The user makes a conscious choice via the ack checkbox.
- Manual approval gate before any paper trade or recommendation. No auto-execution.
- The post-hoc volatility detector replaces an event calendar that we cannot reliably maintain.

### Data ingestion

- Upstox returns `400 Invalid date range` on 30-day chunks crossing Feb→Mar. Use `chunk_days=7` for spot ingest.
- Upstox historical endpoint returns empty for the same trading day. The live tick → 1m roller is what closes that gap.
- Expired option candles use `/v2/expired-instruments/historical-candle/{expired_key}/1minute/{to}/{from}`. Sending expired keys to the normal V3 endpoint returns `UDAPI1021`.
- `GLOBAL_INDICATOR|USDINR` is rejected by REST quote endpoint but works on the WebSocket. Keep market header per-tile error tolerant.
- Run option fetches month-by-month with `Sample = 1`, ATM only, both CE and PE, `Missing only` enabled, `Max contracts = 500`. Expand to OTM1/ITM1 only when the strategy needs them.

### Calendar handling

- NIFTY weekly expiry day rotated: Thu (until 2024-08) → Wed (2024-09 to 2025-03) → Tue (2025-04+).
- BANKNIFTY weekly options were discontinued in November 2024. Only monthly expiries are available since.
- SENSEX weekly Friday expiry can shift to Wednesday when Thursday is a holiday — example: 2026-01-15 BMC/Maharashtra civic elections shifted SENSEX expiry to 2026-01-14. The `SHIFTED_EXPIRY_DAYS` set in `nse_calendar.py` tracks these.
- 2025-02-01 and 2026-02-01 are Budget Saturday trading sessions.
- 2025-10-21 captured 60 candles from the limited Diwali Muhurat session. The audit recognizes this.
- 4 days have off-by-1 candle counts due to single-minute Upstox glitches. They are treated as complete.

### Forward testing

- The contract picker filters `expiry_date >= today`. Without it, live signals can resolve to long-expired contracts. The 2026-05-28 bug was caused by missing this filter; the blocker name is `option_contract_no_active_expiry`.
- Strategy source SHA is pinned on every new deployment. Pre-slice-8 deployments without a pinned SHA continue to operate (legacy compat).
- The unique partial index `signals_deployment_bar_unique` over `(deployment_id, candle_ts)` prevents duplicate journaling without affecting manual research signals.
- Time-of-day blocks (09:15–09:25 and 14:50–15:30 IST) and expiry-day cutoff (15:00 IST) are non-negotiable defaults.
- WS subscribed instruments are captured at connect time. A subscription change in code does not propagate to a running stream — restart it.

### UI

- The dark theme is the default; the white theme exists for readability. Theme tokens are CSS variables, not per-panel hex codes.
- All interactive controls carry `data-testid` attributes (kebab-case, role-based, not appearance-based).
- The market header prefers fresh ticks and falls back gracefully when a single tile fails.

## Verification

```bash
python -m pytest tests -q
cd frontend
npm run build
cd ..
docker compose up -d --build
docker compose ps
```

Health checks:

- `GET /api/health` → `{db: "ok"}`
- `GET /api/upstox/status` → connected
- `GET /api/live-candles/status` → running during market hours

## Where To Go Next

If you are a new agent picking this up:

1. Read `docs/HANDOFF.md`.
2. Read `plan.md` for the slice roadmap.
3. Read `docs/ARCHITECTURE.md` for the module map.
4. The next implementation work is **Slice 10 — Forward metrics aggregation per deployment**.
