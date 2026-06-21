# Paper Trading page redesign — design spec

- Date: 2026-06-21
- Status: Draft for review
- Route: `/paper` ([frontend/src/pages/PaperTrading.jsx](../../../frontend/src/pages/PaperTrading.jsx))
- Owner: Haroon

## 1. Context

The current Paper Trading page is a strategy-named journal: a summary strip, a P&L
calendar heat-grid, a Live Deployments control strip, and a day-grouped raw
`<table>` with a 2s live open-positions poll and a 30s table refresh. It already
captures most of the data we need but does not surface it as an analytics
dashboard, and the trade table groups by day (date is a group header, not a
per-row sortable field).

This redesign turns the page into an **infographic-led paper-trading dashboard**:
account value over time from a fixed starting capital, period P&L, per-strategy
stats, and a richer per-trade blotter — while reusing existing backend machinery
(`portfolio.py`, `forward_metrics.py`, the per-trade `events[]` mark history).

## 2. Goals (from requirements)

1. Redesign with infographics / stat visuals / charts.
2. Per-deployed-strategy stats for analysis.
3. Deployed capital in market + running P&L; **starting capital = ₹2,00,000**;
   account value evolving across days as trades close.
4. Lifetime / Weekly / Monthly P&L in addition to Daily.
5. Filter the trade list by date / strategy / etc.; **date on every row**
   (sortable/filterable), not a single day-group header — the table is redesigned.
6. Per-trade: Min P&L, Max P&L, Current/Running P&L, trade duration, a small P&L
   line chart, and the last SL & TP per the strategy.
7. Anything else useful (see §8).

## 3. Locked decisions

- **Capital model:** single shared pool. **Starting capital is configurable**
  (default ₹2,00,000), persisted, and editable from the UI. Account value =
  starting_capital + cumulative realized P&L across all strategies. Per-strategy
  figures are P&L attribution only (no separate capital per strategy). Changing
  the starting capital only rebases the equity-curve baseline; realized P&L is
  independent of it.
- **Account value basis:** realized daily equity curve (stepped per IST calendar
  day when trades close) PLUS a separate live mark-to-market figure that adds
  current open P&L. Booked vs floating are shown distinctly.
- **Per-trade sparkline:** downsampled (~30-point) P&L-over-time curve computed
  server-side from the per-minute `events[]` MARK history; not full
  minute-resolution, not a 3-point marker.
- **Blotter density:** compact `Max / Min / Now` triple per row + an expandable
  per-trade detail drawer for the full curve, SL/TP lines and friction. MFE/MAE
  added as server sort fields so power-sorting is still possible.
- **Architecture:** backend analytics layer, thin frontend (see §4).

## 4. Architecture

Backend computes; frontend renders. This matches the existing
"Python does the work, frontend consumes JSON" pattern and keeps logic inside the
Python host-test suite.

```
GET    /api/paper/account-config     -> { starting_capital }                                      (new)
PUT    /api/paper/account-config     -> set { starting_capital }; persisted                        (new)
GET /api/paper/analytics            -> account equity curve + period P&L + drawdown + exposure  (new)
GET /api/paper/strategy-stats       -> per-strategy attribution + forward-vs-backtest drift     (new)
GET /api/paper/trades  (extended)   -> existing list + compact per-trade analytics per row       (extend)
GET /api/paper/open-positions       -> unchanged (already carries unrealized_pnl, mfe/mae passthrough)
```

Starting capital is persisted (a small paper-account config document/row) and read
by `/api/paper/analytics`. Default ₹2,00,000 when unset.

### 4.1 Account equity curve + period P&L (`/api/paper/analytics`)

Reuse and adapt `app/portfolio.py::build_rupee_equity_curve` (already returns
starting_capital, ending_equity, net_pnl, total_return_pct, drawdown value/%,
daily Sharpe/Sortino, win/loss days, and a curve). Today it consumes backtest
"PAIRED" trades keyed on `option_pnl_value`; we add a thin adapter that feeds it
CLOSED paper trades keyed on `realized_pnl` and `closed_at` (IST day), starting
from the configured starting capital (default `DEFAULT_CAPITAL = 200_000` when
unset).

Returns:
- `starting_capital`, `account_value_realized` (latest), `account_value_mtm`
  (realized + current open MTM), `total_return_pct`.
- `deployed_capital` (Σ entry_value of OPEN trades), `open_pnl` (Σ unrealized).
- `max_drawdown_value` / `max_drawdown_pct`.
- `equity_curve`: array of `{ day, equity_value, drawdown_value }` per IST
  calendar day (flat on no-trade days so "with passing days" reads correctly).
- `period_pnl`: `{ today, week, month, lifetime }` realized ₹ (IST-bucketed),
  plus `win_rate`, `profit_factor`.
- `exposure`: `{ deployed_pct, by_instrument: { NIFTY, BANKNIFTY, SENSEX } }`.

Computed over the full CLOSED set (not the 500-row stats cap) so "lifetime" is
honest; the existing per-page list stays capped/paginated.

### 4.2 Per-trade analytics (extend `/api/paper/trades` rows)

Each row gains a compact `analytics` block, computed server-side:
- `mfe_value` / `mae_value`: max / min of `unrealized_pnl` over the trade's
  `events[]` MARK history (₹). For CLOSED trades the final realized value is
  included. Fallback: if `events[]` is absent, derive from entry/exit/last only.
- `running_pnl`: current `unrealized_pnl` (OPEN) or `realized_pnl` (CLOSED).
- `spark`: ~30 evenly-time-sampled `{ t, pnl }` points from `events[]`
  (Largest-Triangle-Three-Buckets or simple stride downsample).
- `duration_s`: created_at → closed_at/now.
- `sl` / `tp`: from `risk.stop_price` / `risk.target_price` (last known).

To avoid shipping the full `events[]` array to the browser, the API returns only
the downsampled `spark` + scalars. `events[]` is NOT added to the list payload.

Add `mfe`/`mae` to `_TRADES_SORT_FIELDS` (server) so the new columns are sortable.

Note: paper `mfe_pts`/`mae_pts` are currently never populated (only the backtest
path sets them). We compute MFE/MAE from `events[]` here; OPTIONAL hardening is to
also track running max/min directly in `paper_trading._mark_open_trade` so the
value survives any future `events[]` truncation. Phase 2.

### 4.3 Per-strategy stats (`/api/paper/strategy-stats`)

Group CLOSED+OPEN trades by `strategy_id` / `deployment_id`. Per strategy:
net P&L, trade count, win rate, profit factor, avg hold, max drawdown
(strategy-local), open count + open MTM, expectancy, and **contribution %**
(strategy net P&L ÷ total net P&L). Reuse `forward_metrics.py` where it already
computes forward stats; add the contribution + expectancy fields.

Forward-vs-backtest drift (the one heavier item, Phase 2-flaggable): compare each
deployment's live win-rate / profit-factor against the pinned backtest
expectation it was deployed from (deployment source is already pinned). Show as a
delta chip (e.g. "live PF 1.5 vs bt 1.9 ▼").

## 5. Frontend structure

Decompose the current single large file into focused components under
`frontend/src/components/paper/`:

- `AccountHero.jsx` — account value, return %, live MTM, deployed capital, open
  P&L, max drawdown + the equity curve (recharts `AreaChart`). Includes an
  editable **starting capital** control (popover/inline edit) that PUTs
  `/api/paper/account-config` and re-fetches analytics; confirms before rebasing.
- `PeriodPnlCards.jsx` — Today / Week / Month / Lifetime + win rate + PF
  (reuse `MetricCard`).
- `StrategyStatsTable.jsx` — per-strategy rows + contribution + drift chips
  (+ a small P&L-by-strategy bar via recharts).
- `DeploymentControlStrip.jsx` — extracted from the current page (logic
  unchanged): Pause/Resume/Stop + Stop-all.
- `PnlCalendar.jsx` — the existing heat-grid, extracted, made collapsible, joined
  by a small monthly-P&L bar chart.
- `TradeBlotter.jsx` — the redesigned table (see §6).
- `TradeDetailDrawer.jsx` — expandable per-trade detail (vaul drawer or inline
  expand row): larger P&L curve with SL/TP reference lines, entry/exit markers,
  MFE/MAE annotations, friction breakdown.
- `PaperTrading.jsx` — orchestrates fetches + layout only.

Charting: recharts (already installed) for equity curve, monthly bars, strategy
bars, and the detail-drawer P&L curve. Per-row sparkline stays a tiny inline SVG
(cheap, no per-row recharts instances) — matches the existing `Sparkline`.

## 6. Trade blotter redesign

- Flat, sortable table (no day-group header). **Date is a per-row column** and
  the primary sort (desc default), matching server `created_at` sort.
- Filters: date range (from/to), strategy/deployment, instrument, status, plus a
  client text search over contract/strategy. Server-side filter/sort/paginate is
  retained.
- Columns: Date+time · Strategy/contract · Side (CE/PE) · Entry→Exit price ·
  Duration · SL/TP · **Max/Min/Now** (MFE/MAE/running ₹) · **P&L sparkline** ·
  Net P&L (₹, net, with gross/friction subline) · P&L % · Status · Actions.
- Row → expands `TradeDetailDrawer`.
- Keep: selection + purge (CLOSED only), CSV export, pagination, the OPEN-row
  inline close controls (@market / manual / stale badges).

## 7. Number / color / accessibility conventions

- Unify ₹ formatting on one `en-IN` helper (`Intl.NumberFormat('en-IN', { style:
  'currency', currency: 'INR', maximumFractionDigits: 0 })`) → correct
  lakh/crore grouping. Replace the two divergent helpers (`inr()` via `fmtNum`
  vs the LiveSignals `toLocaleString("en-IN")`).
- P&L is never color-only: pair red/green with a sign (+/−) so it's accessible.
- `font-mono tabular-nums` on all price/P&L columns (kept) to stop digit jitter
  on live updates.
- Live feed-health chip: reuse `live_stale` to show when live P&L is trustworthy
  vs estimated.

## 8. Extra analytics included (req. 7)

All reuse existing data:
- Forward-vs-backtest drift per strategy (Phase 2 if wiring the pinned backtest
  expectation proves involved).
- Expectancy (₹/trade) & per-trade R-multiple (P&L ÷ initial risk from sizing
  `risk_per_unit`).
- Exit-reason breakdown (stop / target / EOD / manual) — small bar/donut.
- Exposure / concentration (deployed % of pool + per-instrument split).
- Live feed-health chip.
- Risk-discipline flags (`risk_exceeded` sizing breaches; loss > intended risk).

## 9. Reused / kept (not rebuilt)

- `portfolio.py` equity-curve + risk metrics (adapted to paper trades).
- `forward_metrics.py` per-deployment metrics.
- `events[]` per-minute mark history (already written by the evaluator).
- `risk.stop_price` / `risk.target_price` (SL/TP) and `spot_exit` variants.
- Live open-positions feed (2s) and 30s table refresh cadence.
- Deployment controls, calendar heat-grid, CSV, purge, pagination, friction
  breakdown.

## 10. Testing

- Backend: host tests for the analytics adapter (equity curve from paper trades,
  period bucketing in IST, exposure, per-strategy attribution, MFE/MAE + spark
  downsample from a synthetic `events[]`). Target parity with `portfolio.py`'s
  existing equity math.
- Frontend: component render tests + a smoke test that the page renders with the
  new endpoints mocked (consistent with existing `data-testid` conventions).

## 11. Phasing

- Phase 1: configurable starting capital (account-config endpoint + UI) + account
  hero + equity curve + period cards + redesigned blotter with Max/Min/Now +
  sparkline + SL/TP + duration + unified ₹ formatting + feed-health chip +
  exposure. (Covers requirements 1–6 and most of 7.)
- Phase 2: per-strategy drift vs backtest, expectancy/R-multiple, exit-reason
  breakdown, optional `_mark_open_trade` MFE/MAE hardening.

## 12. Non-goals

- No live broker orders (that is the separate Flattrade Live Trading page).
- No change to how signals are generated/auto-deployed or to the evaluator.
- No new charting dependency (recharts + lightweight-charts already cover it).

## 13. Risks / open questions

- `events[]` size: minute marks over multi-day OPEN trades can grow the trade
  doc; the API ships only the downsampled spark, but if `events[]` itself becomes
  a storage concern we downsample/cap at write-time (Phase 2).
- Confirm `events[]` is persisted+retained for all paper trades (the evaluator
  appends MARK events; verify nothing prunes them).
- Branch: this work is unrelated to the current `feat/option-aware-optimization`
  branch — implement on a fresh branch off `main`.
