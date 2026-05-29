# User Manual

Updated: 2026-05-29

This guide explains how to use AlphaForge as a local research and forward-testing app.

## Start The App

```bash
docker compose up -d --build
```

Open `http://localhost:3000`.

One-click launchers:

- Windows: double-click `start.bat`.
- Mac/Linux: `./start.sh`.

## Theme

Use the top-right Theme dropdown:

- `System` — follows your OS.
- `Black` — dark terminal mode.
- `White` — light mode for better readability.

## Market Header

The market header appears at the top of every page with primary instruments first (NIFTY 50, SENSEX, BANKNIFTY, GOLD FUT, BTCUSD, USDINR, GIFT NIFTY, MIDCPNIFTY) and a collapsible Global Markets section.

The header prefers fresh Upstox WebSocket ticks when the local stream is running; otherwise it falls back to REST quotes. A failed tile shows an error state without breaking the rest of the header.

To start the WS stream:

1. Connect Upstox from Data Warehouse.
2. Click `Stream` in the market header. Status changes to `live ticks`.
3. Click `Stop` to close the stream.

The WebSocket stream is read-only market data. It does not place orders.

## Data Warehouse

The Data Warehouse page is your data discipline hub. Use it before any serious research.

### Upstox connection

Confirm Upstox is connected at the top of the page. The Quote button validates a live REST quote during market hours.

### Index ingest (1-minute candles)

For long ranges (12–18 months), use the background job path:

1. Select instrument.
2. Set From and To dates.
3. Leave Chunk as Auto. The chunker uses 7-day chunks for spot to avoid the Upstox `400 Invalid date range` on Feb→Mar boundaries.
4. Click Ingest.
5. Watch progress in the run panel. It reports `total_chunks`, `completed_chunks`, `progress_pct`, `total_fetched`, `candles_added`, `candles_updated`, `matched_existing`, `failed_chunks`.

For small ranges, the synchronous endpoint is fine.

### Data Hygiene workflow (recommended)

The Data Hygiene workflow handles bulk data refresh against the project's default scope (2024-11-27 → today, NIFTY+BANKNIFTY+SENSEX, ATM CE+PE only, sample=1m). It is the easiest way to bring the warehouse up to date.

1. `POST /api/data-hygiene/plan` (or the UI button) computes the diff vs current warehouse state.
2. Review the prioritized actions per instrument: spot ingest, contract sync, option candle fetch.
3. `POST /api/data-hygiene/execute` submits the actions in dependency order (spot → contracts → option_candles).
4. `GET /api/data-hygiene/status` shows recent hygiene runs.

Re-running is safe; partial failures resume cleanly.

### Option Data Planner

For targeted option fetches outside of Data Hygiene:

1. Confirm spot candles already exist for the period.
2. Confirm expired option contract metadata is synced for the period.
3. Choose underlying, From / To, expiry mode (`Next available`), Sample (`1` for high-accuracy, `15` for fast estimates), moneyness (`ATM` default), CE/PE legs.
4. Set Max contracts (default 500).
5. Click Preview.
6. Inspect Planned coverage, Need fetch, Missing meta, planned contracts, estimated API calls.
7. Click Fetch Missing.
8. Watch the background job progress.
9. Click Preview again to confirm the month is ready when Planned coverage = 100%, Need fetch = 0, Missing meta = 0.

Recommended for long ranges: fetch month by month with Sample = 1, ATM only, CE + PE, Missing only enabled, Max contracts = 500.

### Coverage and audits

- **Option Coverage Heatmap** shows stored option candles by date and contract count. Diagnostic only.
- **Raw Option Universe Audit** shows broad coverage by contract metadata. It can show many missing contracts even when the planner-selected ATM window is covered. Trust the planner Preview, not raw audit alone.
- **Data Trust Audit** for index candles by date and integrity hash.

Holiday handling: the audit recognizes NSE holidays, Budget Saturday sessions (2025-02-01, 2026-02-01), and the Diwali Muhurat session (limited evening session, 60 candles).

## Backtest Lab

1. Select instrument, strategy, mode, date window.
2. Choose pre-trade profile.
3. Enable costs for realistic results.
4. Keep walk-forward enabled for robustness checks.
5. (Optional) Enable Pair signals with option candles to test option premium execution.
6. Click Run Backtest.

For paired option backtests, slippage is automatically applied (ATM 0.5pt, OTM1/ITM1 1pt, OTM2+ 2pt, expiry-day 30-min 2x). Override per backtest via the slippage config field.

Read results carefully:

- Strong P&L with low trade count is not reliable. Wilson CI and the significance badge highlight this.
- Walk-forward divergence flag means OOS underperformed IS by more than 30%.
- Option pairing coverage shows how many index trades had matching option candles.

## Optimizer

1. Pick strategy and date window.
2. Choose method: Bayesian (TPE), Grid, or Genetic (CMA-ES).
3. Choose objective: `risk_adjusted` (default), `sharpe`, `profit_factor`, `total_pnl_pts`, `win_rate`, `neg_max_dd`.
4. Set n_trials.
5. Run.
6. Review robustness, parameter importance, heatmap, top-N alternatives.
7. Click Apply as Preset to save the best params.
8. Click View Best in Lab to see the full backtest with trades and walk-forward.

The Stop button cancels gracefully. The worker checks the cancel flag every 5 trials and preserves best-so-far.

## Pre-Trade Checklist

Three profiles ship: Conservative, Balanced, Aggressive. Each has 10+ filters. The signal-pass counter at the bottom of the panel updates as you tune. The anti-over-filter safeguard warns when filters are too strict.

## Strategy Library

Browse built-in strategies and parameter schemas. For drop-in custom plugins, see `docs/STRATEGY_PLUGINS.md`.

## Volatility Audit

`POST /api/volatility/audit` runs the post-hoc volatility detector on a date window. It annotates spot 1m bars with realized 5-min vol vs 30-day rolling baseline and flags `volatility_spike` when ratio ≥ 2.5x. Use this to identify high-volatility periods after the fact instead of relying on a calendar of scheduled events.

## Live Signals (Strategy Deployments)

This is the forward-testing surface. Workflow:

### 1. Create a deployment

1. From Live Signals, click Create Deployment.
2. Choose source: a saved Preset or a saved Backtest Run.
3. The PreflightBadge collapses above the Create button. Expand it to review:
   - Spot coverage (last 30 trading days).
   - Upcoming option expiries.
   - Active vs expired contracts.
   - Upstox token state.
4. The QualityBadge surfaces walk-forward and metrics warnings:
   - Missing walk-forward validation.
   - Walk-forward IS/OOS divergence (OOS < IS × 0.7 or explicit divergence flag).
   - Low trade count (< 30).
   - Weak Sharpe (< 0.5).
   - Large drawdown ratio (|max_dd|/total_pnl > 0.15).
5. If warnings exist, tick the acknowledgment checkbox. Otherwise the Create button is disabled.
6. Choose mode (`shadow`, `paper`, `recommendation`), DTE filter (default `[0..6]`), default lots (default 1), and `allow_overnight` (default false).
7. Save. The deployment is `ACTIVE`.

### 2. Wait for signals

The 1m_close evaluator scheduler wakes 10 seconds after each minute boundary during NSE market hours. It:

- Pulls the latest closed 1-minute candle.
- Runs the strategy.
- Applies pre-trade filters.
- Picks the ATM/OTM1/ITM1 contract from `option_contracts` with `expiry_date >= today` and `dte_filter` honored.
- Applies time-of-day blocks (09:15–09:25 and 14:50–15:30 IST) and expiry-day cutoff (15:00 IST).
- Journals the signal: `CONFIRMED` if clean, `AUDITED` with `blockers[]` if rejected.

Each ACTIVE deployment also has an Evaluate-now button.

### 3. Approve, skip, or mark blocked

The Pending Approval panel auto-refreshes every 15 seconds and shows only CONFIRMED deployment-generated signals.

For each:

- **Approve** transitions the signal `CONFIRMED → TRIGGERED → ACTIVE`. When `deployment.mode == "paper"`, a paper trade is auto-created with `lot_size` from the option contract and `lots` from `risk.default_lots`. The button label changes to "Approve + Paper" in that case. A trade-creation failure does not roll back the approval — it journals a `paper_trade_error`.
- **Skip** transitions `CONFIRMED → SKIPPED → AUDITED`.
- **Mark Blocked** moves any non-AUDITED signal to AUDITED with the supplied note as a blocker.

### 4. Watch trades and square-off

Paper trades created from approvals appear in Paper Trading. They carry `deployment_id` so the auto square-off knows which to skip when `allow_overnight=true`.

The auto square-off background loop runs at 15:00 IST every market day. It:

- Closes all OPEN paper trades whose deployment does not have `allow_overnight=true`.
- Uses WS tick → last_price → entry_price as the exit price priority.
- Is idempotent (a re-run is a no-op).
- Can be triggered manually with `POST /api/paper/square-off`.

## Paper Trading

The Paper Trading page shows paper trades with risk badges (stop / target). You can manually mark to a last price (with optional `auto_close_on_risk`) and close trades manually.

## Practical Workflow

For a fresh study:

1. Start the stack with Docker Compose.
2. Run Data Hygiene plan + execute to bring the warehouse current.
3. Backtest a strategy in Backtest Lab.
4. Optimize if results are promising. Apply best as a Preset.
5. Re-test the preset.
6. Create a Strategy Deployment from the Preset. Acknowledge any quality warnings.
7. Let the evaluator run during market hours.
8. Approve / skip signals from the Pending Approval panel.
9. Watch paper trades. Auto square-off closes them at 15:00 IST.
10. Review forward signals and trades per deployment.

## Common Issues

| Issue | What to do |
|---|---|
| Upstox fetch fails | Re-do OAuth at `/api/upstox/auth/start`. Tokens expire. |
| Same-day historical returns empty | Expected. The live tick → 1m roller closes the gap during market hours. |
| Text hard to read | Switch Theme to White. |
| Option preview has many API calls | Reduce date range, use Sample=1, select fewer moneyness/legs, fetch month by month. |
| Backtest says insufficient candles | Run Data Hygiene plan + execute for the date window. |
| Live signal resolves to expired contract | Should not happen post-Slice 5. The blocker `option_contract_no_active_expiry` should fire. Check `option_contracts.expiry_date` for the instrument. |
| Deployment auto-paused with `strategy_source_drift` | The plugin .py file changed since the deployment was created. Create a new deployment to pin the new SHA. |
| `acknowledgment_required` 400 on deployment create | Quality warnings exist; tick the ack checkbox and retry. |

## Trading Safety

- Do not trust a strategy from one backtest. Use walk-forward, forward testing, and paper trading.
- The system warns about walk-forward divergence; do not silence the ack checkbox blindly.
- The manual approval gate is intentional. Do not build around it.
- Options can lose money quickly. Use strict per-trade risk and the daily loss controls (Slice 12 will add per-deployment kill switches).
