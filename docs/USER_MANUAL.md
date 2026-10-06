# User Manual

Updated: 2026-10-06

This guide explains how to use AlphaForge as a local research and forward-testing app.

## Start The App

```bash
docker compose up -d --build
```

Open `http://localhost:3000`.

One-click launchers:

- Windows: double-click `start-app.bat` (recommended). It starts Docker Desktop when needed, safely reuses an already-healthy stack or builds a stopped stack, waits for readiness, and opens the browser automatically. Use `start-app.bat --rebuild` only when there is no live broker exposure.
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

The feed label is green **live ticks** only when the WebSocket is connected, a tick arrived
within the last 30 s, and the header's quotes really are WS ticks. The other labels:

- amber: *reconnecting*, *no ticks* (connected, silent), *stale 45s* (the age of the last
  tick), *unverified*;
- grey: *connecting*, and *API fallback* (no stream; REST quotes, which is normal but not live);
- red: *offline* (the header API itself is unreachable).

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
   - **(Optional) Exit / risk controls** — an off-by-default panel (under Option Execution) to backtest with a premium **trailing stop**, **breakeven** lock, and soft **per-day caps** (loss ₹ / target ₹ / max trades). Trailing & breakeven need exit mode "Option premium SL/target" (fractions, e.g. 0.25 = give back 25% of peak); the ₹ caps need costs on; max-trades doesn't. Off ⇒ the run is byte-identical to before. The same overlay the Optimizer can auto-tune and the deploy wizard enforces.
6. Click Run Backtest.

For paired option backtests, slippage is automatically applied (ATM 0.5pt, OTM1/ITM1 1pt, OTM2+ 2pt, expiry-day 30-min 2x). Override per backtest via the slippage config field.

Read results carefully:

- Strong P&L with low trade count is not reliable. Wilson CI and the significance badge highlight this.
- Walk-forward divergence flag means OOS underperformed IS by more than 30%.
- Option pairing coverage shows how many index trades had matching option candles.
- **Trust scorecard** (green/amber, above the performance hero on an option run) — an **advisory** verdict that never blocks. It flags, in plain language: a **fragile** result (positive out-of-sample but **negative full-window** option-₹ — the trap to avoid deploying on the OOS number alone), an **account-ruin / equity-floor breach** (equity went to/below zero), and **low option-data coverage** (too few signals actually paired, excluding intentional cap-skips). The same verdict appears on the Optimizer's promoted best and feeds the deploy wizard's acknowledge-to-deploy gate. Option-₹ headline figures are full-window, not walk-forward validated — the scorecard says so.

## Optimizer

The Optimizer page runs automated parameter searches to find the best strategy configuration.

### Setup panel
- **Run type:** the first decision.
  - **Single** — one search over the whole window. Fast, but the result is in-sample by definition: parameters are picked on the same data they are scored on.
  - **Walk-forward (honest OOS)** — the honest mode. Splits the window into chronological train/test windows (trading days actually present in the data, so holiday-aware), re-optimizes on each train window only, scores each window's best on its unseen test slice, and stitches all OOS trades into one out-of-sample equity curve — the number to believe. Use this before deploying anything.
- **Strategy + Method + Objective + Trial budget:** pick your strategy, search method (Bayesian TPE recommended), objective, and how many trials to run (10–5000; note: more trials can increase overfitting risk for small parameter spaces). Walk-forward does not support Grid — Bayesian is used.
- **Evaluation mode:** the key decision.
  - **Spot points (fast)** — the original mode. Searches quickly by maximizing index-point P&L. Useful for exploration, but can give misleading results for option buying because it ignores theta/spread/costs.
  - **Option re-rank (research)** — Stage 1 runs the fast spot search; Stage 2 loads historical option minute bars once and re-scores a top-K shortlist by modelled net rupee (costs + spread + DTE). It is a valuable rejection screen, but the legacy option warehouse is not point-in-time/execution certified and the shortlist is not an option-native exhaustive search. A positive result may nominate a paper cohort; it cannot by itself establish forward validation or live readiness.
- **Option sub-panel** (shown when re-rank mode is active): moneyness, DTE filter, lots, exit mode (premium SL/target supports points or percent of premium — points take precedence), costs toggle.
- **Guard rails** (toggle, default ON): `Min trades` rejects obvious few-trade winners but is not a statistical-significance test; `Min CE/PE side %` prevents all-one-direction solutions when that matches the thesis (default 0 = off).
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
- **Re-rank table:** shows each candidate's modelled net rupee P&L on historical option bars, option win-rate, paired/total trade count, spot objective, and option-data coverage. Sorted by modelled option net rupee for research only.
- The "best" params and saved backtest run reflect the option-best selection, not the spot-best.

### Results (Walk-forward)
- **Stitched Out-of-Sample Result panel:** OOS net points, win rate, and the stitched OOS equity sparkline — performance measured only on data the optimizer never saw.
- **WF Efficiency** (OOS pnl/day ÷ IS pnl/day), color-coded: ≥0.7 green (the edge survives out of sample), <0.4 red (likely overfit). Negative means the OOS windows lost money.
- **Consistency:** the share of windows that were OOS-positive.
- **Parameter Stability bars:** red bars mark parameters that wander window-to-window — a sign they are fitted to noise, not structure.
- **Per-window table:** each window's chosen params and IS/OOS results.
- The deployable `best_params` come from the most recent train window, saved with a full backtest run, so Save-as-Preset / View-Best-in-Lab / deployments work exactly as for single runs. Job History tags these runs `walk-fwd`.
- **Option OOS (₹) block** (when "Option-aware OOS" is on): the stitched OOS trades paired with historical option minute bars—modelled net rupee after charges, pairing, and per-window consistency. A negative result rejects the spot edge. A positive result remains research-only because window re-optimization still searches on spot and legacy option history lacks point-in-time execution provenance.
- **Save as Preset stores the execution policy** (moneyness, DTE, exit mode, premium levels, costs) with the params. Loading the preset in Backtest Lab re-applies it; the deployment form prefills from it. The Rocket button on a preset row jumps straight to the deployment form with that preset preselected.

### After the run
- **Research/promotion evidence** — completed option runs carry a provenance verdict. Legacy option history yields `research_only`, so the button honestly saves a **Research Preset** rather than claiming live readiness. Any compatible preset can still be deployed for signals/paper immediately and can be explicitly enabled for real money through the separate unvalidated-live consent; the consent is audited and does not turn research-only evidence into validation.
- **View Best in Lab** — opens the saved best-result full backtest (with trades, equity curve, walk-forward) in the Backtest Lab.
- **Save as Preset** — saves the best params as a Preset (available in Backtest Lab and deployments). Works for completed, cancelled, paused, and interrupted jobs.
- **Clone config** — the copy icon on any Job History row repopulates the Setup panel with that job's configuration for re-running with tweaks.
- **Delete** the trash icon removes the job record.

## Pre-Trade Checklist

Three profiles ship: Conservative, Balanced, Aggressive. Each has 10+ filters. The signal-pass counter at the bottom of the panel updates as you tune. The anti-over-filter safeguard warns when filters are too strict.

## Strategy Library

Browse built-in strategies and parameter schemas. For drop-in custom plugins, see `docs/STRATEGY_PLUGINS.md`.

Strategy cards with closed paper trades show a **Forward** block per deployment plus the pre-registered phase: collecting, plumbing ready, or promotion ready. The UI shows complete sessions out of 60 and eligible trades out of 120. The old 10-session visibility badge is only a plumbing checkpoint. Passing every gate in [`forward-validation-policy.md`](forward-validation-policy.md) earns the forward-validated label; it is strongly recommended but can be overridden only through the explicit unvalidated real-money consent.

## Volatility Audit

`POST /api/volatility/audit` runs the post-hoc volatility detector on a date window. It annotates spot 1m bars with realized 5-min vol vs 30-day rolling baseline and flags `volatility_spike` when ratio ≥ 2.5x. Use this to identify high-volatility periods after the fact instead of relying on a calendar of scheduled events.

## Deploy Strategies (`/live`)

The sidebar's **Deploy Strategies** page shows every deployed strategy as a card and is the home
of deploy / pause / resume / **undeploy**. There is no approval flow anymore: paper
deployments trade every clean signal automatically; signal-only deployments
just journal. The old Pending Approval panel and the manual research-signal
console were removed. Real-money execution is enabled per deployment on the
**Live Broker** page (next chapter).

### 1. Deploy a strategy (3-step wizard)

1. Click **Deploy strategy** (or use the rocket button on a preset in the Optimizer — it lands here preselected).
2. **Step 1 — Source:** the toggle offers three sources. Whichever you pick is frozen into an immutable deployment snapshot, with the strategy file's SHA pinned.
   - **Saved preset** — a named preset: strategy params plus, when it was saved from an option run, the option execution policy (moneyness, DTE, exits, costs, exit/risk overlay), which prefills Step 2.
   - **Backtest run** — a saved run from the Backtest run journal. The picker lists the 200 most recent runs. The **Deploy** button on a Backtest Lab result (`/live?backtest=<run id>`) opens the wizard here with that run preselected, however old it is. The deployment takes the run's exact strategy params, so saving a preset first is optional, and its lots replay the run's option sizing policy (see *Sizing replay* in [`STRATEGY_DEPLOYMENTS.md`](STRATEGY_DEPLOYMENTS.md)). A run carries no preset-style execution policy, so the wizard says *review step 2 manually*: set moneyness, DTE, exits and costs yourself to match the run. The Step 2 *Sizing* box reads only a preset's execution block, so for a run it can show *fixed N lot(s)* even though the run's sizing is what gets pinned.
   - **Strategy Library** — any loaded, non-retired entry compatible with the 1-minute deployment evaluator. It exposes the instrument and the complete parameter schema for you to set. It has no backtest execution policy, so review every option, exit, cost and risk setting in the next steps.

   The Validation evidence card shows whether the honest pipeline ran (latest walk-forward efficiency/consistency + option-rupee proof, flagged when params differ).
3. **Step 2 — Execution** (prefilled from a preset's execution policy; a backtest run or a library entry leaves it at the defaults): mode — **Paper (auto-trade every clean signal)** or **Signal only** — plus moneyness, multi-select DTE filter, lots, and (paper mode) the auto-paper toggle with fallback exits in ₹ points or % of premium (used only when the strategy gives no exit hints).
4. **Step 3 — Risk & go:** kill switches (max consecutive losses / daily loss cutoff % → auto-PAUSE; max open trades → soft block), allow-overnight, and the quality-warning acknowledgment when the preset has warnings. Click **Deploy** — signals start with the next market minute, and the live option stream re-aligns its strikes to your deployments automatically.

### 2. Read the cards

Each card: mode chip (PAPER AUTO-TRADE / SIGNAL ONLY), status with any auto-pause reason (kill switch or source drift), today's clean/blocked signals, open trades and open MTM, today ₹, lifetime ₹ and win rate, plus **Signals →** and **Trades →** links into the filtered journals. The header strip totals today's MTM across all deployed strategies. Multiple strategies run concurrently and independently — two strategies firing on the same bar both trade their own signals.

A card whose strategy file changed since it was pinned is auto-paused with `strategy_source_drift`; **Re-pin & resume** accepts the new code as the baseline.

**Pause on a card is not a live hold.** Any move out of ACTIVE (card Pause, Undeploy, drift pause) demotes a live deployment to paper, and going live again needs the full Enable Live Execution step. To stop new live entries but stay live, use **Pause** in the Live Broker's Live Deployments pane.

### 3. What happens each market minute

The evaluator wakes 10s after each minute close (09:15–15:30 IST; signal window 09:25–14:50; expiry-day cutoff 15:00): runs every ACTIVE deployment's strategy, applies pre-trade filters, picks the contract (`expiry_date >= today`, DTE filter honored), and journals CONFIRMED (clean) or AUDITED (blocked, with reasons). For paper deployments with auto-paper on, every clean signal opens a paper trade at the **real option premium** (live tick, else a stored candle ≤5 min old — never the spot level); the strategy's exits are mirrored live (index hits the spot target/stop → option closes at its premium; premium-% levels apply when set), checked by the paper exit monitor (`LiveExitMonitor`, every ~1.5 s during market hours and woken by ticks, at most every 0.2 s); no usable premium → no trade, with `paper_trade_error` journaled on the signal.

A clean signal is routed to a trade only inside its own bar's pass. Nothing revisits it afterwards, so a CONFIRMED signal that was never traded is retired later (see *Signal Journal*).

### 4. Undeploy

**Undeploy** on a card stops signal generation and paper trading for that strategy. You'll be offered an optional purge of its journaled signals and CLOSED trades (OPEN trades are kept so the marker/square-off can finish them). Pause/Resume is the non-destructive alternative.

### 5. Square-off

The auto square-off background loop runs at 15:00 IST every market day. It:

- Closes all OPEN paper trades whose deployment does not have `allow_overnight=true`.
- Uses WS tick → last_price → entry_price as the exit price priority.
- Is idempotent (a re-run is a no-op).
- Can be triggered manually with `POST /api/paper/square-off`.

## Live Broker (`/live-trading`)

The sidebar's **Live Broker** page (header **LIVE COCKPIT**) is the real-money terminal for
Flattrade. Live authorization is per deployment: `mode == "live"`, a connected Flattrade session
and the time before the 15:00 IST entry cutoff, within that deployment's caps.
`LIVE_AUTOPLACE_ARMED=1` must also be set, or live entries dry-run.

> None of the live controls in this chapter (the Live Deployments pane, tighten caps, Flatten,
> timeline, alerts) has yet run in a market session. Watch the first live day closely.

### Page layout, top to bottom

| Area | What it shows / does |
|---|---|
| **Command bar** | Market pill (a server holiday overrides the client's weekday clock), **Upstox** (data) and **Flattrade** (exec) chips with Login / Reconnect / Disconnect, **Kill switch ↓** (scrolls to the panel; it does not fire), **Configure** (opens the drawer) |
| **Execution strip** | The one verdict: *LIVE — entries transmit real orders* / *Armed (dry-run)* / *Safe*. It shows `entries:` and `auto-squares:` as TRANSMIT, dry-run, or **BLOCKED — no broker**, plus the session countdown. A red **Flattrade session expired** banner sits above it when the token is gone. **Stand down** appears only while the manual ticket is in LIVE_TEST |
| **Safety latch banner** | The account daily-loss latch. It blocks every new live entry until reset, and the reset is two-step |
| **Recovery banner** | Overnight recovery has not yet succeeded for the current Flattrade token. A carried-over position may be unguarded |
| **Alert rail** | Pre-open readiness verdict, unguarded broker positions, live positions with no backstop, positions re-attached with a default stop, feed health, the OAuth result |
| **Live Deployments pane** | What is trading and the controls to hold or stop it (below) |
| Left column | Market Pulse, Market Analysis, Greeks card (priced off the broker book) |
| Right column | Risk KPIs: Day P&L, Open Pos, Guard, Avail Margin, Working Ord, and **Day Stop**, the *worst* live deployment's cap, not a pooled sum. Then Open Positions with a reconcile chip, the **Kill switch**, **Software Guard**, Quick Trade and the deployment summary |
| Account tabs | Funds & Margin, Holdings, Order book, Trade book |
| **Configure** drawer | GTT / OCO backstop (PC-down net, off by default) and Overall controls (basket SL / trail) |

**Session countdown.** It is counted against the server's clock, not this PC's. It reads, by
phase:

- *market opens HH:MM · in …*
- *entries close HH:MM · …* (amber in the last 15 minutes)
- *no new entries · EOD square HH:MM · …*
- *session over*, *market holiday (name)* or *market closed (weekend)*, each followed by
  *· next session YYYY-MM-DD HH:MM*

*clock stale* means the arm-state poll that carries the clock has been failing for over a
minute. Other pages read the same clock from `GET /api/live-broker/session-clock`.

### Enable real-money execution

In the Live Deployments pane, expand **N not-live deployments — enable live execution** and click
**Enable Live Execution** on the row. Choose lots/signal, max lots/day, max concurrent positions,
the mandatory positive daily-loss cap, and the optional catastrophe OCO band. Values must fit the
account lot and open-position ceilings shown in the dialog.

If the forward policy passes, review the summary and tick **Yes, enable real-money
trading for this deployment**. If it has not passed (including missing evidence),
the dialog shows the failed checks and the checkbox instead reads **Yes, I
explicitly approve unvalidated real-money trading**; that evidence override is
persisted. (Until 2026-09-08 this step also required typing `ENABLE`; the checkbox
replaced it, and now renders on both paths.)

The optional catastrophe OCO band is **inactive by default** — the resting broker
OCO has been off since 2026-09-03 (`LIVE_BROKER_OCO_ENABLED`). Values you enter are
stored but have no effect until it is switched on, and there is no PC-down net
meanwhile. Broker OAuth,
engine/kill-switch, capital ceilings, order safety, and exit protection remain hard
gates. `LIVE_AUTOPLACE_ARMED=1` is also required for automatic entries to transmit;
otherwise the backend dry-runs them.

A drift-paused not-live row offers **Re-pin & resume paper**. It resumes paper only; live must be
enabled again separately.

### Live Deployments pane

**Header:** *N can trade · N blocked · N held · N not live · ₹ today*. The counts come from the
caps governor's answer to "can this place an entry now?", not from the mode. The header also
holds the **Alerts** toggle and **Stop ALL live**. Rows sort as: holding positions, then held,
then idle.

**One row per live deployment:**

| Element | Meaning |
|---|---|
| Dot | Pulsing red: can place an entry right now. Amber: held. Dim red: blocked |
| `today N ord · N lots · ₹` | **Cumulative** for the IST day, closed trades included. It does not count anything still working |
| `· N open` | Positions open now and registered with the software guard |
| `· MTM ₹` | Live mark-to-market. It is re-marked on the Upstox tick; `(broker)` means it is priced off the 15 s broker book |
| **Headroom** `lots x/y open x/y loss ₹x/₹y` | Consumed against each cap, as the governor measures it. Loss = today's realized **plus** open unrealized. Amber at 75%, red at 100%. `(no cap)` means the cap is unset, and `—` means unknown (a stale mark), never zero |
| `entries until HH:MM` | This deployment's own entry window closes before the 15:00 cutoff |
| **blocked: …** chip | The first refusal in the entry path's order (authorization → account → deployment), e.g. *broker session not connected (or expired)*, *after the 15:00 IST entry cutoff*, *market closed today*, *before the 09:15 open*, *account safety latch is set — reset required*, *daily loss cap hit*, *daily lot cap reached*, *max concurrent positions open*, *exposure unknown — a position mark is stale*. *entry state unknown* means the governor did not answer, and that never reads as "can trade" |
| **entry refused: …** chip | Why the last live entry was not placed, e.g. *no fresh premium*, *pre-trade gate*, or *not sent — changed in flight: lot size lowered 2 → 1*. The last one means the caps were tightened while the order was being built. A refusal from an earlier day is dimmed and dated |
| **held — no new entries** chip | The hold is on. Open positions stay open and guarded |

**Row controls:**

| Control | What happens | Confirmation |
|---|---|---|
| **Pause / Resume** | A hold: no new entries. The deployment stays live and ACTIVE, and its positions keep their exits. Resume needs no re-consent | One click |
| **Flatten** | Sends real exit orders (marketable limit, re-priced until filled) for this deployment's positions and stays **live**. *also HOLD new entries* is ticked by default, which makes the button **Flatten & hold**. Outside market hours nothing is sent. A contract shared with another position is not sent | Dialog listing each contract, qty and ≈ value |
| **Tighten caps** (expanded row) | Lower lots / entry, max lots / day, max open, daily loss cap. Any increase is refused whole and nothing is written; raising a cap needs Disable → re-Enable. It applies to signals evaluated after the write, and a toast warns when the new cap is already reached | **Apply** |
| **Disable** | Back to PAPER. Open positions are **not** closed; they stay open and guarded | Dialog |
| **Stop** | Flattens, then demotes to paper **and** pauses. Going live again needs Enable Live Execution from scratch | Dialog listing the contracts |
| **Stop ALL live** | Squares every open PAPER trade, pauses every active deployment (paper too), disables and flattens every live deployment | Type `STOP ALL` |
| **Kill switch** (right column) | Latches the account and halts the engine first, then disables live deployments and flattens the whole broker book, with a per-leg report | Type `KILL` |

Exit toasts report the broker's exit report and never claim "flattened". A broker acceptance is
not a fill. The possible parts are: *exit submitted … awaiting fill confirmation*, *already
flat*, *FAILED — still open*, *deferred*, *NOT sent — contract shared with another position*,
and *open in the journal but not held by the guard … check the broker*. The toast is red
whenever something did not go clean.

**Expanded row** (chevron):

- the tighten-caps form;
- the open positions: contract, qty, entry, last, stop, *to stop* (points and %, red under 10%)
  and target. `(unfilled)` marks an entry not yet seen filled;
- the last intended entry;
- warnings for OPEN journal rows from an earlier day (they hold a concurrency slot until the
  startup reconcile proves them flat) and for trades closed today with no journalled P&L;
- **Today's timeline**: collapsed, fetched only when opened, with a **refresh** button
  (`GET /api/deployments/{id}/timeline`). It lists the day's signals, refusals, entries, exits,
  orders and the latest hold / disable / caps events. The footnote lists what is recorded
  nowhere (skipped entries, halt/latch history), so a quiet stretch is not mistaken for
  "nothing happened".

**Alerts** are opt-in and off by default. When on, the page raises a toast for fills, exits,
refused entries, blocked entries and safety halts. *desktop* asks the browser for notification
permission when you tick it, and *sound* plays a tone. Alerts fire **only while this tab is
open**; nothing alerts if the PC is down. Opening the page on an existing book never alerts. The
setting is stored in this browser.

### Software Guard health

The guard pill (Software Guard card and the Guard KPI) reports what the guard is actually doing:

| State | Meaning |
|---|---|
| **WATCHING** (green) | Cycling over registered positions |
| **IDLE** | Cycling, nothing registered to guard |
| **OFF HOURS** | Outside the options session: weekends, before 09:15, and after the F&O close (15:40 IST since 2026-08-03). The guard skips cycles by design. Registered positions resume being watched at the next open. This is normal in the evening and at weekends |
| **STALLED** (red) | No guard cycle completed for 30 s during the session |
| **BLIND** (red) | Cycling, but it cannot read the position book (e.g. the Flattrade token expired). Stops and targets cannot fire |
| **NOT RUNNING** (red) | The guard task is not running |
| UNKNOWN | The backend did not report health |

With positions open and the pill BLIND, STALLED or NOT RUNNING, a red note says the positions
are unprotected. The broker OCO is off, so there is no broker-side backstop: reconnect
Flattrade, or manage the position in the Flattrade terminal. An amber **N default-stop** chip
marks positions re-attached after a restart with a deep-default catastrophe stop instead of your
original levels.

### Pre-open readiness alert

At **08:45 IST** the backend checks the coming session and stores a verdict. It is report-only
and never blocks trading. The alert rail shows it only when there is something to say.

- **Today's verdict:** red *Pre-open check (08:45 IST today): NOT READY* when there is a blocker,
  amber *warnings* otherwise.
  - Blockers: Upstox not connected or its token expired. Flattrade not connected or its session
    expired, counted only while a deployment is live.
  - Warning: warehouse actions pending.
  - A blocker that the broker chips now show as fixed is dropped and listed under *Since
    resolved*.
- **An earlier day's verdict** (no check recorded today) is shown muted, as *Previous pre-open
  check — <date>, NOT today's*.
- A non-trading day shows nothing.

The verdict is served by `GET /api/live-broker/preopen-readiness`.

## Signal Journal (`/journal`)

Two lanes:

- **Paper** (the default) is the signals ledger. Each deployment signal is joined with its paper
  trade (`GET /api/signals/enriched`).
- **Live** shows the deployments' live trades from the live blotter, the same feed as the Live
  Broker page. It is filtered by the deployment selector and refreshes every 30 s.

Manual research signals (no deployment) are not listed.

**Filters and sort.** Filter by deployment (kept in the URL as `?deployment=`, which is how the
cards' **Signals →** links land here), instrument, state, *Clean + blocked / Clean only / Blocked
only*, and From / To dates (IST). Dates and the **Time (IST)** column are the signal's **bar**
minute (`candle_ts`), not when the row was last updated. Sortable columns: Time, Instr., Score
and State. The page shows 100 rows and refreshes every 45 s.

**Columns:** Time (IST), Deployment, Strategy, Instr., Side (CE/PE), Contract (strike · side ·
expiry), Spot, Entry ₹ (option premium), Exit (premium · reason, or *open*), P&L ₹, P&L pts, Score,
State and Notes. Notes shows, in order of precedence: `paper_trade_error` (red),
`paper_trade_skip`, `live_trade_error`, then the blockers. Expanding a row shows the entry
triggers, the risk hints (spot target/stop pts, target/stop %, time stop), blockers, errors,
`tracked_for_pnl`, lots and qty.

**State chips:**

| Chip | Meaning |
|---|---|
| **CONFIRMED** (amber) | Passed the filter, and its own bar is being routed right now, or a trade is linked and the state write lagged |
| **UNVERIFIED** (red) | A trade sink claimed the signal but no trade was recorded on it. A trade may exist, so check the paper/live blotter |
| **EXPIRED** | Passed the filter but was never traded, and its bar has passed. A CONFIRMED signal is acted on only in its own bar's pass, so it shows as EXPIRED 15 minutes after its bar (or on a later day). After the sweep it is stored as AUDITED and still shows EXPIRED |
| **NOT ACTED ON** | Clean, but the paper or live sink refused it. The hover text names the refusal |
| **RETIRED** | Its linked paper trade no longer exists. It was retired from ACTIVE by the 2026-09-29 cleanup |
| **TRIGGERED / ACTIVE / EXITED** | Routed to a trade: TRIGGERED then ACTIVE when the trade opens. Every paper or live close moves ACTIVE → EXITED |
| **AUDITED** (plain) | **Blocked** by the pre-trade filter. The blockers are in Notes |

**The retirement sweep.** At backend boot, and in the 15:00 IST daily square-off pass, the backend
moves every CONFIRMED signal that is over 15 minutes past its bar and has no claim or trade link
to AUDITED (reason `unactioned_bar_passed`). It never touches a signal a sink may still route.
The first run, on 2026-09-29, retired 1,366.

**CSV** exports up to 500 rows of the current filters and sort. Columns: `bar_ist`,
`deployment_name`, `strategy_id`, `instrument`, `direction`, `state`, `blocked`,
`paper_trade_id`, `trade_status`, `score`, `contract`, `spot_entry`, `entry_premium`,
`exit_premium`, `exit_reason`, `pnl_value`, `pnl_premium_pts`, `lots`, `quantity`, `reasons`,
`blockers`. Use `blocked`, not `state`, to tell a blocked row from a retired clean one, because
both are stored as AUDITED.

**Cleanup** (`POST /api/signals/purge`, each with a confirm). Paper trades are never deleted.

- **Delete selected** deletes the ticked rows.
- **Purge old** deletes **every** signal, of any state, last updated more than N days ago.
- **Purge this deployment** deletes all of the filtered deployment's signals.
- **Auto-purge blocked older than N days** is the opt-in retention. It is stored in this browser,
  empty means off, and it runs at most once per IST day when the page opens. It deletes
  **blocked** signals only. Clean signals that were never traded (EXPIRED / NOT ACTED ON) are
  kept as trade-recommendation history.

## Paper Trading (`/paper`)

The Paper Trading page is the analytics dashboard and blotter over the paper-trade journal. Paper
trades are simulated on real streamed option premiums (never the spot level), lot size comes from
the contract, and every time is IST. Paper trades themselves never reach the broker. The
deployment controls on this page are a different matter: they act on live deployments too (see
the warning below).

Sections, top to bottom:

| Section | What it shows / does |
|---|---|
| **Feed chip** (header, right) | Green *Live · tick* when open positions are marked from the tick stream, or *Live · 2s poll* when the page fell back to polling. Amber *Estimated / stale* when no open position has a fresh mark, which includes having no open positions at all |
| **Account hero** | *Account value (realized)*: starting capital plus all closed trades' P&L, with the total return %. Then *Open P&L*, *Live MTM* (realized value plus open P&L, re-marked on the tick), *Deployed in market* (entry premium × qty of the open trades) and *Max drawdown*. Below is the realized equity curve, one point per IST close day |
| **Starting capital** (pencil next to *start*) | Default ₹2,00,000. Optional *account-wide entry ceiling*: when on, a new paper trade from any deployment must fit inside the capital, or it is skipped and journaled. The basis is *fixed* or *cumulative*; a *ceiling* badge shows while it is on |
| **Period cards** | Today, This week (from Monday), This month, Lifetime, Win rate and Profit factor, from closed trades' realized P&L across every deployment, bucketed by IST close day |
| **Per-strategy table** | Net P&L, Charges, Trades, Win%, PF, Avg R (P&L ÷ the trade's risk amount), *vs backtest*, Exit mix, Expectancy (net ÷ closed trades), Open (count · MTM) and Contrib. (share of total net). Click a row to filter the blotter to that strategy. The chevron opens a drill-down by Daily / Weekly / Monthly / Yearly bucket (trades, net, min/max P&L, min/max capital, max drawdown, max deployed, required capital) with a cumulative-P&L and account-value chart. Refreshes every 60 s |
| **Feed banner** | Appears only while a deployment is ACTIVE and the live data feed is offline (Upstox not connected: **Connect Upstox**), stalled (no live candles: **Restart feed**) or warming up |
| **Live Deployments strip** | Every non-archived deployment, whatever its mode (controls below) |
| **Overall Controls — paper basket** | Basket stop-loss, target and trailing (None / Lock / Lock & Trail / Overall Trail SL) across ALL open paper positions, in ₹ of basket MTM or % of the basket's entry premium. A breach squares the whole paper basket. The paper exit monitor evaluates it (~1.5 s). If any open leg lacks a live mark, the basket counts as stale and nothing fires. The *Re-entry* chip is stored with the settings, but no runtime acts on it (checked 2026-10-06) |
| **P&L calendar** | Mon–Fri heat grid of the last ~16 weeks, each cell coloured by that IST day's realized ₹ (hover for ₹ and trade count). Covers up to 500 trades of the current filter |
| **Exit reasons** | Share of closed trades per bucket: target, stop, end-of-day, manual, other. Uses the same filtered set as the calendar |
| **Journal toolbar and Trades blotter** | Below (filters, cleanup, columns) |

**The vs backtest chip** compares the deployment's session-gated forward results with its pinned
option-₹ backtest: *WR live vs base* and *₹/trade live vs base*, green when both are up and red when
both are down. *no baseline* means there is no comparable backtest with the same params.
*insufficient sample* means fewer than 10 complete forward sessions.

The per-strategy table has one row per **strategy**, not per deployment. Trades from two
deployments of the same strategy are pooled into one row, and that row's name, *vs backtest* chip
and drill-down belong to one of those deployments.

### Live Deployments strip

Each row shows a status dot and label, the deployment name and strategy id, and *N open · MTM ₹*.
The label describes the **data feed**, not real money. *ACTIVE · LIVE* means fresh candles are
arriving. The other labels are *ACTIVE · STARTING*, *ACTIVE · FEED OFFLINE*, *ACTIVE · NO LIVE
CANDLES*, *ACTIVE · MARKET CLOSED* and *PAUSED*. A paused row shows its pause reason with the date
it was recorded.

| Control | What happens |
|---|---|
| **Caps** | The paper caps editor (`PUT /api/deployments/{id}/paper-caps`): lots per signal (replaces the source's sizing replay), max concurrent positions, daily loss cap ₹, max trades per day, and a capital ₹ entry gate with a *fixed* or *cumulative* basis. Empty means no cap. An entry whose premium outlay does not fit the capital is skipped and journaled |
| **Pause** / **Resume** | Stops / restarts new signals for the deployment. Open trades keep being marked and exited |
| **Re-pin & resume** | Shown for a `strategy_source_drift` pause: accepts the strategy's current code and resumes |
| **Stop** | Squares off the deployment's open positions and pauses it |
| **Stop ALL paper trading** | `POST /api/deployments/stop-all`: squares every open paper trade and pauses every ACTIVE deployment |

> **These buttons reach live deployments too.** The strip lists live deployments alongside paper
> ones. **Pause** or **Stop** on a live deployment demotes it to paper, and **Stop** first sends
> real exit orders for its broker positions. **Stop ALL paper trading** calls the same route as the
> Live Broker's **Stop ALL live**: besides the paper work above, it flattens every live deployment
> with real exit orders and demotes it to paper. To stop new live entries but stay live, use
> **Pause** in the Live Broker's Live Deployments pane.

Paper positions closed by Stop, Stop ALL or Close all open take the latest tick, else the last
mark, else the entry price. When the page has no live marks (for example out of market hours), the
confirmation warns that they will close at an **estimated** price.

### Journal toolbar

- *a–b of N* trades for the current filters, plus a removable *strategy:* chip when the
  per-strategy table set a filter.
- **Deployment** selector (kept in the URL as `?deployment=`, which is how the deployment cards'
  **Trades →** links land here), **Instrument**, and **From / To** dates (IST, on the trade's
  entry time).
- **Close all open** (`POST /api/paper/square-off`) closes **every** open paper trade, whatever
  the filters say.
- **CSV** exports up to 500 rows of the current filters and sort. **Refresh** re-fetches now.
- **Cleanup (closed only)**, each with a confirm: **Delete selected** (tick closed rows in the
  blotter), **Purge old** (CLOSED trades last updated more than N days ago, default 30) and
  **Purge this deployment** (needs the deployment filter). OPEN trades can never be deleted.

### Trades blotter

100 rows per page (Prev / Next), newest entry first. Rows refresh every 5 s. Open rows' P&L%, Net
P&L and P&L curve also re-mark on every tick from the open-positions stream
(`GET /api/paper/open-positions/stream`). The stream pushes on each Upstox tick, coalesced to about
10 a second, and falls back to a 2 s poll if it drops. It makes no broker calls. A dimmed value is
the last known mark for a contract whose feed has gone quiet.

**Columns:** Entry Date/Time, Strategy (the deployment name), Contract, Side (CE/PE), Entry Price,
Exit Price (*live* while open), Exit Date/Time, Duration, Qty (lots × lot size), SL / TP, Max P&L
(MFE), Min P&L (MAE), P&L%, Charges, Net P&L, P&L curve (sparkline), Status and Exit Reason.
Drag a header edge to resize or a header to reorder. The layout is stored in this browser, and
*reset layout* restores it; the maximize button gives the table the full window.

- **Sort** by Entry Date/Time, Entry Price, Exit Price, Exit Date/Time, Max P&L, Min P&L or Net
  P&L. The first click sorts descending, the next ascending.
- **Header filters:** Strategy, Side (CE/PE), Status (Open/Closed) and Exit Reason (Target
  achieved / Manual / End of day / Stoploss hit / Others).
- **Net P&L** on a closed row is the stored realized P&L. With the deployment's execution
  friction on (the wizard default) it is net of slippage on both legs, and also of statutory
  charges only when the friction **cost** toggle is on (off by default); with friction off it is
  gross. Charges are always computed when the trade closes: hover **Charges** for the breakdown
  and the net after charges. On an open row it is the unrealized P&L at the latest mark.
- **Click a row** for the detail drawer: the intra-trade P&L curve with SL and TP lines, MFE, MAE,
  running P&L, the last SL / TP, gross and friction, total charges with their breakdown, and the
  net after charges.
- **@ market** (Exit Reason column of an open row) closes that trade at its last mark, as
  `manual_close_at_market`. With no mark it asks you for the exit premium. A premium that looks
  implausible (e.g. a spot level) asks for confirmation before booking. A trade that an automatic
  exit already closed just refreshes.

Exits normally need no clicking: the paper exit monitor fires stops, targets, trailing and
spot-mirror exits (see *What happens each market minute* above), and the 15:00 IST square-off
closes the rest. The API still has `POST /api/paper/trades/{id}/mark` for a manual mark, but the
page no longer has a button for it.

## Practical Workflow

For a fresh study:

1. Start the stack with Docker Compose.
2. Run Data Hygiene plan + execute to bring the warehouse current.
3. Backtest a strategy in Backtest Lab.
4. Optimize if results are promising — finish with a Walk-forward (honest OOS) run and check WF efficiency, consistency, and param stability before trusting it. Apply best as a Preset.
5. Re-test the preset (use Option re-rank or an option backtest for rupee realism).
6. Create a Strategy Deployment from the Preset in paper mode with auto-paper on. Acknowledge any quality warnings.
7. Let the evaluator run during market hours — clean signals paper-trade themselves; the Deployments cards show live activity.
8. Watch paper trades. The paper exit monitor (~1.5 s, tick-woken) fires stops/targets; auto square-off closes the rest at 15:00 IST.
9. Review forward results in Strategy Library (low-sample badge until 10 complete sessions) and per deployment.

## Common Issues

| Issue | What to do |
|---|---|
| Upstox fetch fails | Reconnect Upstox (Data Warehouse, or the Upstox chip on Live Broker; `/api/upstox/auth/start`). Tokens expire daily. |
| Same-day historical returns empty | Expected. The live tick → 1m roller closes the gap during market hours. |
| Text hard to read | Switch Theme to White. |
| Option preview has many API calls | Reduce date range, use Sample=1, select fewer moneyness/legs, fetch month by month. |
| Backtest says insufficient candles | Run Data Hygiene plan + execute for the date window. |
| Live signal resolves to expired contract | Should not happen post-Slice 5. The blocker `option_contract_no_active_expiry` should fire. Check `option_contracts.expiry_date` for the instrument. |
| Deployment auto-paused with `strategy_source_drift` | The plugin .py file changed since the deployment was pinned. **Re-pin & resume** on the card accepts the new code (a live deployment comes back as paper). Or create a new deployment. |
| `acknowledgment_required` 400 on deployment create | Quality warnings exist; tick the ack checkbox and retry. |
| Signal has `paper_trade_error` instead of a trade | No usable option premium at signal time (no live tick, no fresh stored candle). Nothing retries it: the Journal shows it NOT ACTED ON. Check the option stream / warehouse coverage. |
| Deployment auto-paused with `kill_switch_reason` | A kill switch tripped (consecutive losses or daily loss cutoff). Review the trades before resuming. |
| Journal full of EXPIRED rows | Normal. A clean signal is only acted on in its own bar's pass; untraded ones expire and are retired to AUDITED. Filter *Blocked only* / *Clean only* to separate them. |
| Live row shows `blocked: …` | Read the chip: it is the first thing refusing entries (broker session, 15:00 cutoff, market closed, a cap, the account latch). The dot pulses red only when an entry could fire now. |
| Guard pill reads OFF HOURS | Normal outside 09:15–15:40 IST and at weekends: the guard skips cycles by design. STALLED / BLIND / NOT RUNNING during the session are the real alarms. |
| Paper page chip reads *Estimated / stale* | Normal with no open paper positions or out of market hours. In the session, with positions open, it means no fresh option tick is marking them: check the Upstox stream. |
| Red "Flattrade session expired" banner | Log in to Flattrade from the Live Broker command bar (tokens clear ~06:00 IST). Until then nothing reaches the broker, exits included. |

## Trading Safety

- Do not trust a strategy from one backtest. Use walk-forward optimization (the honest OOS mode), forward testing, and paper trading.
- The system warns about walk-forward divergence; do not silence the ack checkbox blindly.
- Auto paper trading never places broker orders; signal-only mode only journals. A deployment in `live` mode can place real Flattrade orders when `LIVE_AUTOPLACE_ARMED=1`, so use the explicit confirmation and capital ceilings deliberately.
- Options can lose money quickly. Use strict per-trade risk and the per-deployment kill switches (max consecutive losses, daily loss cutoff, max open trades).
- Forward P&L is trustworthy only for deployments created after 2026-06-11; older approval-created trades entered at the spot index level (a since-fixed bug) and their P&L should be ignored.
