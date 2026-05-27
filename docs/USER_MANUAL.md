# User Manual

Updated: 2026-05-27

This guide explains how to use AlphaForge as a local research app.

## Start The App

```bash
docker compose up -d --build
```

Open `http://localhost:3000`.

## Theme

Use the top-right Theme dropdown:

- `System` - follows your operating system.
- `Black` - dark terminal mode.
- `White` - light mode for better readability.

If text is hard to read, switch to White first.

## Dashboard

The market header appears at the top of every page. It shows primary instruments first:

- NIFTY 50, SENSEX, BANKNIFTY, GOLD FUT, BTCUSD, USDINR, GIFT NIFTY, MIDCPNIFTY.

Click Global Markets to expand major global references such as Nasdaq Fut, Dow Fut, S&P Fut, Nikkei 225, Hang Seng, DAX, and Crudeoil.

Notes:

- The header can use the read-only Upstox WebSocket tick stream during live sessions. If the stream is stopped, stale, or unavailable, it falls back to API quote polling and fallback sources.
- Treat it as market context. Do not use it as proof that live signal execution is production-ready.

WebSocket stream:

1. Connect Upstox from Data Warehouse first.
2. In the market header, click `Stream`.
3. The status changes to `live ticks` when the local stream is running or fresh ticks are feeding the header.
4. Click `Stop` to close the local stream.
5. If the stream is unavailable or stale, the header continues with API fallback data.

The WebSocket stream is read-only market data. It does not place orders and it does not make strategy signals production-ready by itself.

Use Dashboard to see:

- Current build status.
- Latest backtest summary.
- Shortcuts to major workflows.

## Data Warehouse

Use this page before serious backtesting.

### Upstox Broker Data

Purpose: fetch index candles for NIFTY, BANKNIFTY, or SENSEX.

Steps:

1. Confirm Upstox shows connected.
2. Click Quote during market hours to confirm live REST market data is flowing.
3. Select instrument.
4. Select From and To date.
5. Leave Chunk as Auto unless troubleshooting.
6. Click Ingest.
7. For large imports, keep the page open and watch the progress panel.
8. Run Data Trust Audit for the same date range after the import completes.

Quote:

- Shows a live market snapshot from Upstox.
- Does not place orders.
- Useful when same-day historical candles are not yet available from the historical endpoint.

Chunk tips:

- Auto is recommended.
- Manual `1-3` is safer after broker failures.
- Manual `14-30` is faster but heavier.
- Large imports now run as background jobs. A 12-18 month index import should be started once, allowed to finish, and then audited instead of repeatedly clicking Ingest.

### Option Data Planner

Purpose: preview and fetch option premium candles needed for strategy testing.

Steps:

1. Fetch index candles first for the same date window.
2. Backfill or sync option contract metadata for the same historical window.
3. Select instrument and date range.
4. Choose Expiry mode.
5. Choose Sample.
6. Select moneyness and CE/PE legs.
7. Click Preview.
8. Review Planned coverage, Need fetch, Missing meta, planned contracts, and estimated API calls.
9. Click Fetch Missing only when the preview size is acceptable.
10. Keep the page open and watch the background progress panel.
11. Click Preview again after the fetch job completes. The selected data is ready when Planned coverage is 100%, Need fetch is 0, and Missing meta is 0.
12. Check Option Coverage Heatmap to visually confirm new stored option dates appeared.

Expiry:

- `Next available` uses stored contract expiries on or after each sampled spot date. This is the normal backtest mode.
- `Fixed date` forces one expiry for the entire date range. Use it only for expiry-specific research. It requires a fixed expiry date.

Sample:

- `15` means choose option strikes every 15 minutes from spot price. Good for fast planning.
- `1` means choose strikes every minute. Best for final strategy preparation, but it may select more contracts.

Moneyness:

- `ATM` - nearest strike to spot.
- `OTM1/OTM2/OTM3` - out-of-the-money strikes.
- `ITM1/ITM2` - in-the-money strikes.
- The default planner selection is `ATM` only. Add `OTM1` or `ITM1` only when the strategy really needs those contracts.

Max contracts:

- A safety guard. If the preview needs more contracts than this value, Fetch is blocked until you narrow the request or raise the limit.

Large option downloads:

- Option fetches run in background and fetch only the selected dates for each planned contract.
- For high-accuracy research, use `Sample = 1`. The planner is optimized for minute-level selection, but broker downloads can still be large, so watch the API-call estimate and max-contract guard.
- Start with `ATM` and both `CE`/`PE`, audit coverage, then expand to `OTM1`/`ITM1` and wider strikes only if your strategy needs them.
- Do not trust a long option backtest until the Option Data Planner shows the selected moneyness window is covered.
- For long periods, download in small date windows. A practical first pass is one calendar month at a time with `ATM`, `CE` and `PE`, `Sample = 1`, `Expiry = Next available`, `Missing only` enabled, and `Max contracts = 500`.
- If the background job succeeds but Planned coverage is below 100%, click Preview and inspect which rows still need fetch. Zero failed tasks plus missing rows usually means the broker returned empty candles for those contract/date pairs.

Recommended month-by-month option download:

1. Confirm Upstox is connected.
2. Confirm index spot candles already exist for the same period. The planner cannot select correct ATM strikes without spot data.
3. Confirm expired option contract metadata has been synced or backfilled for the same period.
4. Open Option Data Planner.
5. Select the underlying, for example `NIFTY`.
6. Set one calendar month only, for example `2025-01-01` to `2025-01-31`.
7. Set Expiry to `Next available`.
8. Set Sample to `1` for final research data.
9. Select `ATM`.
10. Select both `CE` and `PE`.
11. Keep `Missing only` enabled.
12. Keep `Max contracts` at `500` unless the preview says the selected month needs more.
13. Leave Chunk as Auto. If the broker fails or times out, retry the same preview with Chunk `1`, `2`, or `3`.
14. Click Preview.
15. Check `Missing meta`. It must be `0`. If it is greater than `0`, backfill expired contracts before fetching candles.
16. Check `Need fetch`, `Planned coverage`, and estimated API calls.
17. Click Fetch Missing.
18. Keep the app open and wait for the background job to finish.
19. Click Preview again for the same month.
20. The month is ready when `Planned coverage` is `100%`, `Need fetch` is `0`, and `Missing meta` is `0`.
21. Check Option Coverage Heatmap to visually confirm new stored option dates appeared.
22. Move to the next month and repeat.

Example sequence for a long NIFTY ATM download:

1. `2024-11-27` to `2024-12-31`
2. `2025-01-01` to `2025-01-31`
3. `2025-02-01` to `2025-02-28`
4. Continue one month at a time until the target end date.

Current practical finding:

- A small NIFTY `ATM` `CE`/`PE` batch for `2024-11-27` to `2024-12-31` completed successfully with 85,500 option candles fetched.
- After a successful fetch, a month can still show less than 100% Planned coverage if Upstox returns empty data for specific expired contract/date pairs. This is now visible through Preview instead of being hidden.
- Do not use Raw Option Universe Audit alone to judge an ATM download. It audits broad stored contract metadata, including contracts you did not request. Use Planned coverage for the selected strategy universe and Option Coverage Heatmap for stored-data visibility.

### Option Coverage Heatmap

Purpose: visually show how much option candle data is already stored in `options_1m`.

Read it like the index heatmap, but with one important distinction:

- It shows stored option candles by date and how many contracts have candles that day.
- Green means the stored contracts for that date have near-full 1-minute candles.
- It does not know which moneyness your strategy needs. Use Option Data Planner Planned coverage for that.

### Expired Option Contracts

Purpose: store old option-contract metadata before planning historical option candle downloads.

Steps:

1. Confirm Upstox shows connected and the account has expired-instruments access.
2. Select instrument and expiry date range.
3. Keep Max expiries conservative for the first run.
4. Click Backfill.
5. If the request is blocked by the max-expiry guard, narrow the date range or explicitly allow the larger request.

This is metadata only. It does not fetch option candles. Use Option Data Planner after the old contracts are stored.

### Data Trust Audit

Purpose: check whether stored index candles are complete.

Steps:

1. Select instrument and date range.
2. Click Audit.
3. Review complete, missing, incomplete, and hash mismatch days.

Developer Clear:

- Deletes stored warehouse data for the selected instrument or all instruments.
- Use carefully. It is useful during testing and data cleanup.

### Raw Option Universe Audit

Purpose: inspect broad option warehouse gaps by contract metadata, expiry, side, and date.

Important:

- This is not the same as the planner-selected moneyness check.
- A raw audit can show `0/500 contracts complete` after an ATM-only download because it is auditing many contracts that were never requested.
- To confirm whether default moneyness has been downloaded, use Option Data Planner and check Planned coverage.

Steps:

1. Sync or backfill option contracts first.
2. Fetch option candles with Option Data Planner.
3. Select underlying and date range.
4. Optionally filter by expiry or CE/PE side.
5. Click Audit.
6. Review complete, missing, incomplete, candle coverage, and contract rows.

How to know selected option data is saved:

- In Option Data Planner, use the same instrument, date range, expiry policy, moneyness, legs, and sample interval as your intended backtest.
- Click Preview.
- Ready means Planned coverage is 100%, Need fetch is 0, Missing meta is 0, and the stored/expected selected candle count matches.
- If Need fetch is greater than 0, click Fetch Missing and wait for the background job to finish. Then Preview again.
- If Missing meta is greater than 0, backfill or sync option contracts for the missing expiry window before fetching candles.

Known expired-option requirement:

- Expired option contracts use Upstox's expired historical candle endpoint.
- If a job reports `UDAPI1021` invalid instrument key format for keys like `NSE_FO|42939|28-11-2024`, the app is using the wrong normal historical endpoint.

Option Clear:

- Deletes stored option candles for the selected underlying.
- It keeps index candles and option contract metadata.
- Use it only for cleanup or re-fetch testing.

## Backtest Lab

Purpose: test a strategy over stored index data.

Steps:

1. Select instrument, strategy, mode, and date window.
2. Choose a pre-trade profile.
3. Enable costs for realistic results.
4. Keep walk-forward enabled for robustness checks.
5. Click Run Backtest.

Option execution:

- Enable Pair signals with option candles to test whether index signals would have produced option premium trades.
- Choose moneyness and lots.
- Leave Auto-fetch on for small missing option gaps.
- For larger windows, prepare data in Option Data Planner first.

Read results carefully:

- Strong-looking P&L with low trade count is not reliable.
- Check walk-forward divergence.
- Check option pairing coverage.
- Missing option candles reduce trust.

## Optimizer

Purpose: search for better strategy parameters.

Suggested use:

1. Start with a clean data window.
2. Use a reasonable trial count first.
3. Prefer risk-adjusted objective.
4. Review robustness and alternatives, not only best P&L.
5. Save a good result as a preset.
6. Re-test the preset in Backtest Lab.

## Strategy Library

Use this page to inspect available built-in strategies and parameter schemas.

For custom strategies, see `docs/STRATEGY_PLUGINS.md`.

## Signal Journal

Use this page to review and reload saved backtest runs.

## Live Signals And Paper Trading

These pages now have offline foundations, but they are not production-ready live trading systems yet.

Forward testing is organized through Strategy Deployments. A deployment is created from a saved preset or saved backtest result so the strategy id, params, source result, and future journal can be audited together.

First deployment behavior:

- Runs on completed 1-minute candles.
- Per-tick mode is a later manual switch after the strategy proves trustworthy.
- Default option selection is ATM.
- ATM, OTM1, and ITM1 are configurable.
- Every signal requires manual approval before paper deployment or trade recommendation action.
- Blocked signals are recorded and identifiable.
- The system prefers fewer cleaner signals over every weak setup.

Live Signals:

- Create and manage Strategy Deployments from saved presets or saved backtest results.
- Pause, resume, or archive a deployment.
- Create manual research signals without broker orders.
- Move signals through the audited lifecycle.
- Deploy a signal to Paper Trading.

Paper Trading:

- Shows paper trades created from signals.
- Stores optional stop and target levels when deploying from Live Signals.
- Lets you mark open trades to a manual last price.
- Auto-closes a paper trade when a mark hits the stored stop or target.
- Lets you close paper trades and store realized P&L.

Still pending:

- Multi-session hardening of the Upstox WebSocket tick stream.
- Automated deployment evaluation from 1-minute closes, then optional per-tick mode later.
- Deployment-generated clean/blocked signal journaling.
- Live tick mark-to-market, trailing stops, replay, and daily risk controls.

## Practical Workflow

Recommended sequence for a new study:

1. Set Theme to White or Black as preferred.
2. Connect Upstox.
3. Fetch index candles in Data Warehouse.
4. Run Data Trust Audit.
5. Use Option Data Planner and fetch required option candles.
6. Run Backtest Lab with option pairing.
7. Optimize only after data coverage is trusted.
8. Save presets and compare out-of-sample results.

## Common Issues

| Issue | What to do |
|---|---|
| Upstox fetch fails | Check connection status and reconnect OAuth if expired |
| Text is hard to read | Switch Theme to White |
| Option preview has many API calls | Reduce date range, increase Sample, select fewer moneyness/legs |
| Fixed expiry cannot preview | Enter the fixed expiry date |
| Backtest says insufficient candles | Ingest index data for that window first |
| Option pairing has missing candles | Use Option Data Planner and Fetch Missing |

## Trading Safety

- Do not trust a strategy from one backtest.
- Use walk-forward and forward testing.
- Confirm data completeness before judging results.
- Treat live signals as research until paper trading proves behavior.
- Options can lose money quickly; use strict risk limits.
