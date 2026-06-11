# User Manual

Updated: 2026-06-12

This guide explains how to use AlphaForge as a local research and forward-testing app.

## Start The App

```bash
docker compose up -d --build
```

Open `http://localhost:3000`.

One-click launchers:

- Windows: double-click `start-app.bat` (recommended) or `start.bat`.
- Mac/Linux: `./start.sh`.

For detailed startup, troubleshooting, and manual Docker steps, see `docs/STARTUP_MANUAL.md`.

## Theme

Use the top-right Theme dropdown:

- `System` — follows your OS.
- `Black` — dark terminal mode.
- `White` — light mode for better readability.

## Market Header

The market header appears at the top of every page with primary instruments first (NIFTY 50, SENSEX, BANKNIFTY, GOLD FUT, BTCUSD, USDINR, GIFT NIFTY, MIDCPNIFTY) and a collapsible Global Markets section.

The header prefers fresh Upstox WebSocket ticks when the local stream is running; otherwise it falls back to REST quotes. A failed tile shows an error state without breaking the rest of the header. Each tile draws a day-range bar (session low → high with a current-price marker), backfilled from the last quote that carried day OHLC when ticks alone drive the price.

To start the WS stream:

1. Connect Upstox from Data Warehouse.
2. Click `Stream` in the market header. Status changes to `live ticks`.
3. Click `Stop` to close the stream.

The WebSocket stream is read-only market data. It does not place orders.

## Data Warehouse

The Data Warehouse page is your data discipline hub. Use it before any serious research. It is organized into sections: **Connection**, **Data Hygiene**, **Index Data**, **Option Data**, **Verify & Audit**, and **Diagnostics**.

### Connection (Upstox)

Confirm Upstox is connected at the top of the page. The header shows a **token-expiry countdown** (also in the global top bar): green > 2h, amber 30m–2h, red < 30m / expired. The Quote button validates a live REST quote during market hours.

### Data Hygiene (recommended — the hero panel)

Day-to-day the warehouse updates itself: it catches up to yesterday's close automatically on backend startup, on Upstox connect, and daily at 18:00 IST (today's bars come from the live roller). The Data Hygiene panel shows the auto-update status and a toggle.

To refresh manually:

1. Click **Check warehouse**. It runs the plan (~6s) and shows a per-instrument diff: spot / option-contracts / option-candle status, with action chips for anything missing. Scope is the project default (2024-11-27 → today, NIFTY+BANKNIFTY+SENSEX, ATM CE+PE, sample=1m) plus the India VIX series (`INDIAVIX`, baseline 2025-12-29) used for VIX-bucket context tagging and the pre-trade VIX filters; the panel shows the VIX coverage status with its own ingest control.
2. Click **Fill gaps** to submit the fetches in dependency order (spot → contracts → option_candles). Progress shows in the panel and the top bar and **survives navigating away and back**.
3. Click **Check warehouse** again to confirm gaps closed.

Re-running is safe; only missing data is fetched; partial failures resume cleanly.

### Index Data

Read-only coverage cards per index (candle count, date range, trading days) and the per-day coverage heatmap. Bulk index ingest is handled by Data Hygiene; for a one-off range use the Upstox ingest control in the Connection panel (Auto chunk uses 7-day chunks to avoid the Upstox Feb→Mar boundary error).

**Candlestick chart:** pick NIFTY / BANKNIFTY / SENSEX and a timeframe (1m / 5m / 15m / 1h / 1d, default 1d). Every timeframe loads the full stored warehouse range, so intraday charts should start from the same first stored trading day as the daily chart; 1m can be dense but is intentionally available for audit. The chart renders calendar-approved regular sessions only (09:15-15:30 IST) so weekend, holiday, and off-session rows do not become candles or false gap warnings. The chart axis is IST, session-open markers show where a trading day begins, and the footer reminds you that the regular session is 09:15-15:30. The top-left overlay always shows the selected candle's Open, High, Low, and Close, and the small monitor/moon/sun icon buttons switch the chart between System, Dark, and Light themes. The **Locate** tool takes an IST date + time, validates it against the loaded range (prompts if out of range), snaps a finer time to the bar that contains it, and marks that bar with an arrow. A gap banner lists completed trading days missing 1m candles; the current in-progress session is not treated as a gap until after 15:30 IST.

### Option Data

- **Option Data Planner** — targeted option fetches. Confirm spot + expired-contract metadata exist, then choose underlying, From/To, expiry mode (`Next available`), Sample (`1` for accuracy, `15` for fast), moneyness (`ATM` default), CE/PE legs, Max contracts (default 500). Click Preview, inspect Planned coverage / Need fetch / Missing meta, then Fetch Missing. Re-Preview to confirm 100% / 0 / 0. For long ranges fetch month by month with Sample=1, ATM, CE+PE, Missing only.
- **Backfill expired option contracts** — sync expired contract metadata before planning historical option candles.
- **Option Coverage Heatmap** — stored option candles by date and contract count (served from a fast cache).

### Verify & Audit

- **Spot & ATM Option Lookup** — pick an index, date, and time (IST); see what the warehouse stored for that minute: spot OHLC, derived ATM strike, resolved expiry, and the ATM CE/PE candles with OI. Cross-check this against your broker terminal. Reads only the warehouse.
- **Data Trust Audit** — per-day index candle audit by integrity hash. **Holiday-aware**: NSE holidays, Budget Saturdays (2025-02-01, 2026-02-01), and the Diwali Muhurat session are recognized, so holidays are not counted as missing days. This panel also hosts the developer "Clear index" and "Clear options" maintenance actions.

### Diagnostics

Recent ingest / fetch / hygiene runs.

### Holiday calendar

The **Holiday Calendar** button (page header) opens a modal listing NSE/BSE holidays and special trading sessions for a selected year, with labels and weekdays.

## Backtest Lab

1. Select instrument, strategy, mode, date window, and trade window (default 09:25–15:00, no entries in first 10 min or last 30 min).
2. Choose pre-trade profile.
3. Enable costs for realistic results.
4. Keep the **walk-forward split check** enabled. (Naming note: this replays the SAME parameter set in-sample vs out-of-sample as a stability check — it does not re-optimize. The honest re-optimizing version is the Optimizer's Run type "Walk-forward (honest OOS)".)
5. **(Optional) Enable Option Execution** — pair signals with real option candles.
   - **Moneyness** defaults to ATM, which matches the data the warehouse maintains automatically. Other moneyness levels need their option data fetched first.
   - **DTE filter** is a multi-select: tick any combination of DTE 0–6 (e.g. 0+1+2 for the 0–2 DTE buying window); ALL = no restriction.
   - **Lots** is ignored while Capital & position sizing is enabled — the sizing panel then controls the lot count (the input is disabled with a note).
   - In premium-at-risk sizing without a premium stop (e.g. exit mode "Mirror spot exit"), the per-trade rupee risk uses the Assumed stop % — an estimate; the panel shows an amber note when this applies.
   - Before running, click **Check option data** (the preflight panel) to see what % of your signals have option candles available. If coverage is below 80%, click **Ingest missing & recheck** (requires Upstox connected) to fetch and store the missing contracts.
6. Click Run Backtest.

For paired option backtests, slippage is automatically applied (ATM 0.5pt, OTM1/ITM1 1pt, OTM2+ 2pt, expiry-day 30-min 2x). Override per backtest via the slippage config field.

Read results carefully:

- Strong P&L with low trade count is not reliable. Wilson CI and the significance badge highlight this.
- Walk-forward divergence flag means OOS underperformed IS by more than 30%.
- Option pairing coverage shows how many index trades had matching option candles.

## Optimizer

The Optimizer page runs automated parameter searches to find the best strategy configuration.

### Setup panel
- **Run type:** the first decision.
  - **Single** — one search over the whole window. Fast, but the result is in-sample by definition: parameters are picked on the same data they are scored on.
  - **Walk-forward (honest OOS)** — the honest mode. Splits the window into chronological train/test windows (trading days actually present in the data, so holiday-aware), re-optimizes on each train window only, scores each window's best on its unseen test slice, and stitches all OOS trades into one out-of-sample equity curve — the number to believe. Use this before deploying anything.
- **Strategy + Method + Objective + Trial budget:** pick your strategy, search method (Bayesian TPE recommended), objective, and how many trials to run (10–5000; note: more trials can increase overfitting risk for small parameter spaces). Walk-forward does not support Grid — Bayesian is used.
- **Evaluation mode:** the key decision.
  - **Spot points (fast)** — the original mode. Searches quickly by maximizing index-point P&L. Useful for exploration, but can give misleading results for option buying because it ignores theta/spread/costs.
  - **Option re-rank (realistic)** — the recommended mode. Stage 1 runs the fast spot search; Stage 2 loads the window's option candles *once* and re-scores the top-K candidates by **real paired-option net rupee** (costs + spread + DTE). Picks the option-best params. Use this before deploying or trusting a result.
- **Option sub-panel** (shown when re-rank mode is active): moneyness, DTE filter, lots, exit mode (premium SL/target supports points or percent of premium — points take precedence), costs toggle.
- **Guard rails** (toggle, default ON): `Min trades` prevents statistically meaningless results; `Min CE/PE side %` prevents all-one-direction solutions (default 0 = off). Turn guard rails OFF to let the optimizer purely maximize your chosen objective.
- **Optimize indicator periods:** also tunes RSI/MACD/ATR/EMA/ADX lengths. Slower but searches the real space.
- **Pre-trade profile:** apply the same filter you use in live trading so optimized params reflect what you'll actually trade.
- **Walk-forward windows** (shown when Run type is Walk-forward): train days (default 60), test days (default 20), step days (default = test days), rolling vs anchored, trials per window (default 40), max windows (default 12 — with more, the oldest are dropped so deployable params always come from the newest data).
- **Setup persists** across navigation — your settings are saved automatically to localStorage and restored when you return to the page.

### Running
1. Click **Auto-Optimize**. The job runs in the background; you can navigate away. Walk-forward shows window k/N progress.
2. Click **Pause** to pause mid-run — progress is saved to the DB and you can Resume later from exactly that point. (Walk-forward pauses at window granularity: completed windows persist, a half-finished window re-runs.)
3. Click **Stop** to cancel (best-so-far is saved; heavy analysis is skipped so it stops quickly).
4. If the backend restarts mid-run, jobs are marked **Interrupted** — click **Resume** from Job History.

### Results (Spot mode)
- Best-so-far card updates live with params + key metrics + direction split (CE vs PE).
- Robustness score: % of ±10/20% perturbations that stay within 85% of the best objective.
- Parameter importance bar chart.
- 2D heatmap of the top-2 most-important parameters.
- Top-10 alternatives table.

### Results (Option re-rank mode)
- **Re-rank table:** shows each candidate's net rupee P&L on real options, option win-rate, paired/total trade count, spot objective, and option-data coverage %. Sorted by option net rupee — this is the realistic ranking.
- The "best" params and saved backtest run reflect the option-best selection, not the spot-best.

### Results (Walk-forward)
- **Stitched Out-of-Sample Result panel:** OOS net points, win rate, and the stitched OOS equity sparkline — performance measured only on data the optimizer never saw.
- **WF Efficiency** (OOS pnl/day ÷ IS pnl/day), color-coded: ≥0.7 green (the edge survives out of sample), <0.4 red (likely overfit). Negative means the OOS windows lost money.
- **Consistency:** the share of windows that were OOS-positive.
- **Parameter Stability bars:** red bars mark parameters that wander window-to-window — a sign they are fitted to noise, not structure.
- **Per-window table:** each window's chosen params and IS/OOS results.
- The deployable `best_params` come from the most recent train window, saved with a full backtest run, so Save-as-Preset / View-Best-in-Lab / deployments work exactly as for single runs. Job History tags these runs `walk-fwd`.
- Walk-forward evaluates on spot points (v1). For option-rupee realism, run the resulting preset through an Option re-rank optimization or an option backtest before deploying.

### After the run
- **View Best in Lab** — opens the saved best-result full backtest (with trades, equity curve, walk-forward) in the Backtest Lab.
- **Save as Preset** — saves the best params as a Preset (available in Backtest Lab and deployments). Works for completed, cancelled, paused, and interrupted jobs.
- **Clone config** — the copy icon on any Job History row repopulates the Setup panel with that job's configuration for re-running with tweaks.
- **Delete** the trash icon removes the job record.

## Pre-Trade Checklist

Three profiles ship: Conservative, Balanced, Aggressive. Each has 10+ filters. The signal-pass counter at the bottom of the panel updates as you tune. The anti-over-filter safeguard warns when filters are too strict.

## Strategy Library

Browse built-in strategies and parameter schemas. For drop-in custom plugins, see `docs/STRATEGY_PLUGINS.md`.

Strategy cards with closed paper trades show a **Forward** block per deployment: win rate, average P&L, total P&L, profit factor, and the complete-session count. Deployments with fewer than 10 complete sessions carry an amber **"low sample"** badge (n/10 sessions) — shown immediately so you can monitor a trial from day one, but treat it as preliminary, not evidence.

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
7. In **paper mode**, a green block appears: **"Auto paper trade on every clean signal"** (checked by default). Leave it on to have every clean signal open a paper trade by itself. The two optional fallback target/stop fields apply only when the strategy provides no exit hints of its own, and take either unit via the toggle: **₹ points of premium** (matches the Backtest Lab's premium SL/target points mode) or **% of entry premium**.
8. Optionally set the **kill switches**: max consecutive losses (auto-PAUSE), daily loss cutoff % (auto-PAUSE), max open paper trades (soft BLOCK that self-clears as trades close). Paper deployments only.
9. Save. The deployment is `ACTIVE`.

### 2. Wait for signals

The 1m_close evaluator scheduler wakes 10 seconds after each minute boundary during NSE market hours. It:

- Pulls the latest closed 1-minute candle.
- Runs the strategy.
- Applies pre-trade filters.
- Picks the ATM/OTM1/ITM1 contract from `option_contracts` with `expiry_date >= today` and `dte_filter` honored.
- Applies time-of-day blocks (09:15–09:25 and 14:50–15:30 IST) and expiry-day cutoff (15:00 IST).
- Journals the signal: `CONFIRMED` if clean, `AUDITED` with `blockers[]` if rejected.

Each ACTIVE deployment also has an Evaluate-now button.

### 3a. Auto paper trading (paper mode with auto-paper on)

For paper deployments with `auto_paper` enabled, every clean CONFIRMED signal opens a paper trade automatically — no clicking:

- The signal appears in the Signal Journal already `ACTIVE` with a `paper_trade_id`.
- The trade appears in Paper Trading at a **real option premium** entry (live tick first, else a stored option candle at most 5 minutes old — e.g. ~₹150, never the ~23,900 index level), with the contract's lot size and the strategy's stop/target levels.
- Exits mirror the backtest: built-in strategies define exits in spot points, so when the index hits the strategy's target/stop, the option closes at its current premium (`spot_target_hit`/`spot_stop_hit`). Premium-% levels apply when set. A background marker checks every open trade once a minute during market hours; whatever is left closes at the 15:00 IST square-off (`auto_square_off_15_00_IST`).
- If no usable premium exists (no live tick, no fresh candle), **no trade opens** — the signal carries a journaled `paper_trade_error` and remains approvable from the Pending Approval panel.

### 3b. Approve, skip, or mark blocked (everything else)

The Pending Approval panel auto-refreshes every 15 seconds and shows the CONFIRMED deployment-generated signals that did not auto-trade: shadow and recommendation deployments, paper deployments with auto-paper off, and auto-trade refusals.

For each:

- **Approve** transitions the signal `CONFIRMED → TRIGGERED → ACTIVE`. When `deployment.mode == "paper"`, a paper trade is created with `lot_size` from the option contract, `lots` from `risk.default_lots`, and the entry at the **resolved option premium** (same resolution as auto-paper). If no premium is available, the approve returns HTTP 409 and the signal stays CONFIRMED so you can retry. The button label changes to "Approve + Paper" in that case. If auto-paper already created the trade, Approve never duplicates it.
- **Skip** transitions `CONFIRMED → SKIPPED → AUDITED`.
- **Mark Blocked** moves any non-AUDITED signal to AUDITED with the supplied note as a blocker.

### 4. Watch trades and square-off

Paper trades — auto-created or approved — appear in Paper Trading. They carry `deployment_id` so the auto square-off knows which to skip when `allow_overnight=true`. During market hours a per-minute marker marks open deployment trades to the latest option tick and fires their exits (`stop_hit`/`target_hit` on premium levels, `spot_target_hit`/`spot_stop_hit` on spot-mirror levels); trades without a live tick are left untouched rather than closed at a stale price.

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
4. Optimize if results are promising — finish with a Walk-forward (honest OOS) run and check WF efficiency, consistency, and param stability before trusting it. Apply best as a Preset.
5. Re-test the preset (use Option re-rank or an option backtest for rupee realism).
6. Create a Strategy Deployment from the Preset in paper mode with auto-paper on. Acknowledge any quality warnings.
7. Let the evaluator run during market hours — clean signals paper-trade themselves; approve/skip whatever lands in Pending Approval.
8. Watch paper trades. The per-minute marker fires stops/targets; auto square-off closes the rest at 15:00 IST.
9. Review forward results in Strategy Library (low-sample badge until 10 complete sessions) and per deployment.

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
| Signal has `paper_trade_error` instead of a trade | No usable option premium at signal time (no live tick, no fresh stored candle). The signal stays approvable; check the option stream / warehouse coverage. |
| Approve returns 409 `option_entry_price_unavailable` | Same cause from the manual path. The signal stays CONFIRMED — approve again once live option data is available. |
| Deployment auto-paused with `kill_switch_reason` | A kill switch tripped (consecutive losses or daily loss cutoff). Review the trades before resuming. |

## Trading Safety

- Do not trust a strategy from one backtest. Use walk-forward optimization (the honest OOS mode), forward testing, and paper trading.
- The system warns about walk-forward divergence; do not silence the ack checkbox blindly.
- Auto paper trading exists to audit signal quality without manual clicking — it never places broker orders, and nothing in this app ever will. Shadow and recommendation modes keep the manual gate.
- Options can lose money quickly. Use strict per-trade risk and the per-deployment kill switches (max consecutive losses, daily loss cutoff, max open trades).
- Forward P&L is trustworthy only for deployments created after 2026-06-11; older approval-created trades entered at the spot index level (a since-fixed bug) and their P&L should be ignored.
