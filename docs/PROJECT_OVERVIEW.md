# Project Overview

Updated: 2026-05-27

## What AlphaForge Is

AlphaForge is a local trading research app for Indian index-option work. It helps a user fetch and store index/option data, audit data quality, run backtests, optimize parameters, and prepare for live signal and paper-trading development.

## Present Status

Implemented:

- Local Docker stack for MongoDB, FastAPI, and React/nginx.
- Theme selector: System, Black, White.
- Index Data Warehouse with yfinance fallback and Upstox historical ingest.
- Automatic broker chunk guidance for index and option data.
- Background Upstox index ingest jobs for large date ranges with progress tracking.
- Data Trust Audit for index candle completeness and hashes.
- Developer clear-data option for stored warehouse data.
- Upstox OAuth, token storage, connection status, and disconnect.
- Upstox live market quote snapshot for index validation.
- Upstox V3 read-only WebSocket tick stream with start/stop/status APIs, protobuf decoding, sanitized tick persistence, reconnect/backoff, and market-header tick preference.
- Persistent market header showing NIFTY 50, SENSEX, BANKNIFTY, GOLD FUT, BTCUSD, USDINR, GIFT NIFTY, MIDCPNIFTY, and collapsible global markets.
- Option contract sync and local contract metadata store.
- Expired option-contract metadata backfill backend route and Data Warehouse operator UI.
- Option candle fetch/store with OI preservation.
- Option Data Planner with preview, ATM-only default moneyness, configurable ITM/OTM selections, CE/PE selection, expiry mode, sample interval, fetch guard, compact planned coverage, background fetch jobs, and selected-date contract windowing.
- Option Coverage Heatmap showing stored option candles by date and contract count.
- Raw Option Universe Audit showing broad contract-level option candle coverage for warehouse diagnostics.
- Backtest Lab for spot/index strategy testing.
- Paired option execution that maps index signals to option candles.
- Offline Live Signals console for auditable signal lifecycle transitions.
- Paper Trading journal with deploy-from-signal, stop/target risk, mark-to-market, close, and P&L.
- Strategy Deployment management foundation from saved presets/backtest results.
- Optimizer with Bayesian/Grid/CMA-ES workflows.
- Presets, journal, strategy library, and pre-trade checklist.

Partially complete:

- Historical option workflow. It works when needed contract metadata and candles exist locally. An expired-contract backfill path exists, but it still needs real-credential/Upstox Plus validation.
- Paper trading and live signal pages now have offline foundations but are not production-ready live systems.

Not complete:

- Multi-session live hardening of the Upstox WebSocket tick stream.
- Strategy Deployment evaluator/runner.
- Automated live signal evaluation from WebSocket ticks.
- Paper trade trailing stops, daily risk controls, live tick marks, and replay engine.
- Production option slippage/liquidity model.

## Architecture Summary

- Frontend: React, Tailwind, shadcn/ui components, Lightweight Charts.
- Backend: FastAPI, pandas, NumPy, Motor.
- Database: MongoDB.
- Broker: Upstox REST historical/quote APIs and read-only V3 WebSocket market-data stream.
- Deployment: Docker Compose on local PC.

More detail: [Architecture](ARCHITECTURE.md).

## Key Workflows

1. Index data:
   - Fetch index candles from Upstox.
   - Use the background ingest path for large 12-18 month ranges.
   - Validate live broker data with the Data Warehouse Quote button.
   - Watch the persistent market header for broad market context. It prefers fresh Upstox WebSocket ticks while the local stream is running, then uses Upstox-first REST quotes and fallback quote sources.
   - Store in `candles_1m`.
   - Audit completeness with `integrity_hashes`.

2. Option data:
   - Sync/store option contracts.
   - Use Option Data Planner to decide which option contracts are needed.
   - Treat the planner's Planned coverage panel as the trust check for selected moneyness/legs/date windows.
   - Fetch missing option candles into `options_1m` through background selected-date jobs.
   - Use Option Coverage Heatmap to visually inspect stored option candle dates.
   - Use Raw Option Universe Audit to find missing/incomplete contracts across a broader metadata slice.

3. Backtest:
   - Load local index candles.
   - Compute indicators/regime.
   - Run strategy.
   - Optionally pair each index trade with option premium candles.

4. Optimize:
   - Run parameter search.
   - Save best run as a preset.
   - Re-test in Backtest Lab.

5. Forward test:
   - Create Strategy Deployments only from saved presets or saved backtest results.
   - Manage deployment status from Live Signals.
   - Start with `1m_close` confirmation.
   - Require manual approval for every paper trade or trade recommendation.
   - Journal clean and blocked signals separately.
   - Review profitability by deployment before trusting a strategy.

## Next Planned Steps

1. Expired option-contract backfill validation.
   - Backend route: `POST /api/upstox/expired-options/contracts/{instrument}/sync`.
   - Required for old dates where expiry rules changed.
   - Store contracts by underlying, expiry, strike, side, instrument key.
   - Data Warehouse operator workflow exists.
   - Validate with real Upstox Plus access.

2. Option data audit improvements.
   - Planner-selected coverage and raw universe audit now have separate UI wording.
   - Contract-level audit and option-data clear controls exist.
   - Add per-contract day drill-down and missing-contract handoff back into Option Data Planner.

3. Live WebSocket tick stream hardening.
   - Initial read-only stream manager is implemented.
   - Validate over several live sessions for reconnects, stale-tick fallback, latency, and subscription failures.
   - Keep REST polling as the off-hours/stale-tick fallback.

4. Live signal lifecycle.
   - Strategy Deployment model/routes and Live Signals management panel exist.
   - Next, implement the `1m_close` evaluator from `docs/STRATEGY_DEPLOYMENTS.md`.
   - First mode: `1m_close` shadow/forward testing.
   - Later mode: manual per-tick switch after trust is established.
   - Avoid signals without auditable source preset/backtest result, params, reasons, blockers, and option policy.

5. Paper trading.
   - Create/mark/close and stop/target auto-close foundation exists.
   - Add simulated fills, trailing exits, replay, and daily risk controls.

## Technical Tips

- Keep secrets in `backend/.env`; never in docs or commits.
- Rebuild Docker after backend/frontend source changes.
- Use `Sample = 15` in Option Data Planner for fast estimates; use `1` before serious testing. Sample 1 is optimized with expiry/side/strike lookup, but final high-accuracy option fetches can still involve many broker calls.
- Leave Chunk blank for Auto unless troubleshooting broker failures.
- Check Upstox status before fetch errors.
- Do not assume Tuesday/Thursday/monthly expiry rules. Read contract metadata.
- A raw universe audit can show many missing contracts when only ATM or a narrow moneyness set has been fetched. To know whether selected data is ready, check Planned coverage, Need fetch, Missing meta, and stored/expected selected candles in Option Data Planner.
- Expired option candles require the Upstox expired historical endpoint; the normal V3 historical endpoint rejects expired keys with `UDAPI1021`.
- Add `data-testid` for new interactive UI controls.
- Use CSS variables for visual changes; avoid hard-coded text/background colors.

## Recommendations

- Prioritize data trust before strategy improvements.
- Build option audit before claiming historical option backtests are dependable.
- Use smaller live/paper slices before implementing a full terminal.
- Keep strategy changes separate from infrastructure changes.
- Prefer conservative defaults and preview screens for broker/data operations.

## Development Learnings

- A fancy UI is not enough. Trading utility depends on clean data, realistic execution, and repeatable verification.
- Broker data work needs guardrails. Preview and chunk estimates prevent large accidental downloads.
- Expiry handling is a data problem, not a weekday formula.
- Option backtests need both contract metadata and premium candles; missing either invalidates results.
- Light/dark readability should be solved with design tokens, not ad hoc per-panel fixes.
- Documentation must describe current truth, not the original plan.

## Verification Commands

```bash
python -m pytest tests -q
cd frontend
npm run build
cd ..
docker compose up -d --build
docker compose ps
```
