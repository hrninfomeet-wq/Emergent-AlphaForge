# Handoff

Updated: 2026-05-27

This file is for the next AI model, developer, or trading-system reviewer. Read this before editing code.

## Present Status

AlphaForge is a local React + FastAPI + MongoDB trading research app for Indian index options. The local Docker stack is the source of truth.

Working locally:

- Frontend: `http://localhost:3000`
- Backend: `http://localhost:8001`
- MongoDB: Docker service `alphaforge_mongo`
- Theme modes: System, Black, White
- Index data warehouse with Upstox/yfinance ingest, integrity audit, and developer clear tools
- Upstox OAuth and encrypted token storage
- Upstox live market quote snapshot for index validation
- Persistent market header with primary Indian/global instruments and collapsible global markets, using fresh Upstox WebSocket ticks when the local stream is running and REST/fallback quotes otherwise
- Upstox V3 read-only market-data WebSocket stream manager with start/stop/status APIs, reconnect/backoff, protobuf tick decoding, sanitized tick persistence, and market-header tick preference
- Upstox 1-minute historical index ingest with automatic chunk guidance
- Background Upstox index ingest jobs for large 12-18 month imports with progress polling
- Option contract sync/store
- Expired option-contract metadata backfill backend route
- Expired option-contract backfill operator UI in Data Warehouse
- Option candle fetch/store
- Option Data Planner with preview-first moneyness/leg/expiry selection, ATM-only default, compact planned coverage, and fast Sample 1 contract lookup
- Background Option Data Planner fetch jobs with selected-date contract windowing and progress polling
- Option Coverage Heatmap for visual `options_1m` stored-candle coverage by date and contract count
- Raw Option Universe Audit for broad contract/date-level option candle diagnostics
- Spot backtesting plus paired option-candle execution
- Offline signal lifecycle foundation with auditable state transitions
- Paper trading journal foundation with manual mark/close, stop/target auto-close, and P&L tracking
- Optimizer, presets, signal journal, and pre-trade checklist
- Strategy Deployment management foundation from saved presets/backtest results

Not complete:

- Full live-session hardening of the Upstox WebSocket tick stream after several real market sessions
- Strategy Deployment evaluator/runner from saved preset/backtest result
- Automated live strategy evaluation from ticks
- Production paper trading execution engine with live tick marks, trailing stops, daily risk controls, and replay
- Real-account validation of expired-contract metadata backfill with Upstox Plus access
- Production-grade option slippage/liquidity model

## Important Rules

- Never commit `.env`, access tokens, broker secrets, or user account data.
- Do not hard-code expiry weekdays. Use stored contract metadata.
- Always preview option warehouse downloads before fetching.
- Keep `all routes under /api`.
- Use Docker Compose for local verification.
- Use tests for trading behavior, data integrity, and UI contracts.

## Architecture Snapshot

Backend:

- `backend/server.py` - FastAPI routes and orchestration.
- `backend/app/warehouse.py` - index candle persistence, coverage, audit, clear.
- `backend/app/upstox_client.py` - OAuth, token storage, Upstox REST historical calls, WebSocket authorize URL.
- `backend/app/upstox_stream.py` - read-only Upstox V3 market-data WebSocket stream manager, protobuf tick decoder, sanitized tick persistence.
- `backend/app/upstox_index_ingest.py` - background Upstox index ingest jobs, bulk candle persistence, and progress updates.
- `backend/app/market_header.py` - normalized market header quote aggregation with Upstox-first/fallback sources.
- `backend/app/chunking.py` - automatic broker request chunk guidance.
- `backend/app/option_contract_store.py` - local option contract metadata.
- `backend/app/expired_contract_backfill.py` - expired option contract metadata backfill orchestration.
- `backend/app/option_candles.py` - option candle normalization/persistence.
- `backend/app/option_coverage.py` - option warehouse coverage summaries for visual heatmap.
- `backend/app/option_data_audit.py` - option candle coverage audit and option-candle clear helper.
- `backend/app/option_data_planner.py` - preview planner for option warehouse downloads.
- `backend/app/option_plan_response.py` - compact API response shaping for option planner coverage.
- `backend/app/option_warehouse_jobs.py` - background option candle fetch jobs, selected-date task planning, and bulk persistence.
- `backend/app/option_backtest.py` - pair index trades with option candles.
- `backend/app/signal_lifecycle.py` - signal state machine and audit events.
- `backend/app/paper_trading.py` - paper trade creation, risk-aware mark-to-market, stop/target auto-close, and close helpers.
- `backend/app/strategy_deployments.py` - deployment document builder and validation for saved preset/backtest-run sources.
- `backend/app/backtest.py`, `optimizer.py`, `walkforward.py` - research engine.
- `docs/STRATEGY_DEPLOYMENTS.md` - agreed design and current status for forward testing, shadow mode, paper mode, recommendation mode, and manual approvals.

Frontend:

- `frontend/src/lib/theme.jsx` - System/Black/White theme state.
- `frontend/src/index.css` - design tokens for dark/light readability.
- `frontend/src/components/Layout.jsx` - shell, navigation, theme selector.
- `frontend/src/components/MarketHeader.jsx` - persistent market quote header and collapsible global markets.
- `frontend/src/pages/DataWarehouse.jsx` - Upstox ingest, data audit, option planner.
- `frontend/src/pages/BacktestLab.jsx` - strategy testing and option pairing.
- `frontend/src/pages/LiveSignals.jsx` - offline signal lifecycle console and Strategy Deployment management panel until evaluator/WebSocket strategy evaluation is wired.
- `frontend/src/pages/PaperTrading.jsx` - paper trading journal, risk badges, and manual mark/close controls.
- `frontend/src/pages/Optimizer.jsx` - parameter search workflow.

Database:

- `candles_1m` - index candles.
- `options_1m` - option premium candles.
- `option_contracts` - option metadata.
- `integrity_hashes` - per-day index candle audit.
- `warehouse_runs` - ingestion audit log.
- `backtest_runs`, `optimization_jobs`, `presets`, `pretrade_profiles`, `strategy_deployments`.

## Next Planned Steps

1. Validate expired option-contract metadata backfill with real broker access.
   - Backend route now exists: `POST /api/upstox/expired-options/contracts/{instrument}/sync`.
   - Data Warehouse operator controls now exist.
   - Real Upstox Plus validation is still required.
   - This is needed for reliable historical windows where expiry rules changed.
   - The current planner handles historical expiry changes only if those old contracts are already stored.

2. Improve option data integrity audit drill-downs.
   - Backend/UI now separate planner-selected coverage from raw universe audit.
   - Option Data Planner is the trust gate for the selected moneyness/legs/date window.
   - Raw Option Universe Audit checks a broad metadata slice by expiry, side, and max contracts. It can show many missing contracts even when the planner-selected ATM window is covered.
   - Next improvement: add per-contract day chips and a direct handoff from raw audit missing rows back into the planner.

3. Validate and harden Upstox WebSocket tick stream over several live sessions.
   - Backend stream exists: `POST /api/upstox/stream/start`, `POST /api/upstox/stream/stop`, `GET /api/upstox/stream/status`, `GET /api/upstox/stream/ticks/latest`.
   - It uses the Upstox V3 authorize URL, sends binary JSON subscriptions, decodes MarketDataFeed protobuf frames, stores sanitized ticks in `ticks`, and reconnects with backoff.
   - Market Header now prefers fresh WebSocket ticks and falls back to REST/API sources when ticks are absent or stale.
   - Next hardening: observe reconnects, stale-tick behavior, subscription failures, and tick latency during live market hours.

4. Build Strategy Deployment evaluator.
   - Design doc: `docs/STRATEGY_DEPLOYMENTS.md`.
   - Deployment model/routes and Live Signals management panel now exist.
   - Deployments can be created only from saved presets or saved backtest results.
   - First confirmation mode: `1m_close`.
   - Per-tick mode is a later manual user switch after trust is established.
   - Default option selection: ATM; configurable: ATM, OTM1, ITM1.
   - Every signal requires manual approval before paper deployment or recommendation action.
   - Blocked signals must be stored and identifiable.
   - Prefer fewer cleaner signals over recording every weak signal.

5. Wire automated live signal generation into the lifecycle.
   - Offline lifecycle exists: `WATCHING -> FORMING -> CONFIRMED -> TRIGGERED -> ACTIVE -> EXITED -> AUDITED`.
   - Next: feed it from deployment evaluation and strategy/pre-trade checks instead of manual research signals.

6. Harden paper trading.
   - Paper trade create/mark/close and stop/target auto-close exist.
   - Next: use live ticks/stored replay to simulate fills, trailing exits, and daily loss controls.

## Technical Tips

- Use `rg` for search and read existing code before editing.
- Use `apply_patch` for manual edits.
- Run `python -m pytest tests -q` after backend changes.
- Run `npm run build` in `frontend` after UI changes.
- Rebuild Docker when verifying the browser app: `docker compose up -d --build backend frontend`.
- If Browser QA shows stale UI, rebuild frontend container and reload.
- If Upstox fetch fails, check OAuth status first at `/api/upstox/status`.
- Use `POST /api/upstox/warehouse/ingest/jobs` for large index history imports; the old synchronous ingest path is only suitable for smaller ranges.
- Use `/api/market/header` to inspect the persistent header quote snapshot. It returns tile-level `status` so one failed symbol should not break the entire header.
- Use Option Data Planner -> Planned coverage before trusting the selected ATM/OTM/ITM option set for a backtest. Trust state is: `Need fetch = 0`, `Missing meta = 0`, and planned coverage at or near `100%`.
- Use Data Warehouse -> Option Coverage Heatmap to see how much option data is stored visually by date. It answers “what exists in `options_1m`?”; it does not prove a specific moneyness plan is complete.
- Use `/api/options/audit/{instrument}` or Data Warehouse -> Raw Option Universe Audit for broad warehouse diagnostics. Do not treat `0/500 contracts complete` as proof that planner-selected data is absent.
- Use background option fetch jobs for large option windows. The planner now fetches selected contract dates rather than the entire broad date range per contract.
- Use `/api/upstox/market-quote/{instrument}` or the Data Warehouse Quote button to validate live REST market data during market hours.
- Use `/api/upstox/stream/start` to start the read-only Upstox V3 tick stream after OAuth is connected. Use `/api/upstox/stream/status` and `/api/upstox/stream/ticks/latest` to verify streaming without exposing credentials.
- WebSocket tick snapshots are sanitized before storage. Do not store or return raw broker frames or authorization URLs.
- Use `docs/STRATEGY_DEPLOYMENTS.md` before implementing deployment evaluation or live recommendation features.
- A same-day historical candle ingest can return empty even when live quotes work; use the quote endpoint for live-session validation.
- Leave option planner `Chunk` blank for Auto unless debugging broker failures.
- Use `Sample = 15` for quick option planning and `Sample = 1` for final strategy preparation. Sample 1 now uses indexed expiry/side/strike contract lookup, but long windows still create many broker fetch tasks.
- The option planner preview response is intentionally compact. It returns selected/fetch date counts and first/last dates, not every per-date count. Fetch jobs still keep exact selected-date ranges internally.
- Expired option candles must use Upstox `/v2/expired-instruments/historical-candle/{expired_key}/1minute/{to}/{from}`. Sending expired keys to the normal V3 historical endpoint returns `UDAPI1021` invalid key format.
- Upstox V3 Market Data Feed uses protobuf frames and binary subscription messages. The local decoder supports LTPC, first-level-with-greeks, and index/full feed LTPC extraction for the header/paper-trading foundation.

## Recommendations

- Keep development local-first until live streaming and paper trading are stable.
- Prefer correctness over attractive UI. A trading terminal must prove data quality and execution realism.
- Treat yfinance as a fallback only. Use Upstox for serious historical work.
- Do not trust a backtest until index data coverage, option candle coverage, expiry mapping, costs, and slippage assumptions are visible.
- Add small verified slices. Avoid broad refactors while Phase 4 is still evolving.

## Development Learnings

- Data completeness matters as much as strategy logic. Missing option candles can make a strategy look better or worse than reality.
- Expiry selection must be metadata-driven because exchange schedules and expiry conventions can change.
- Preview-before-fetch prevents accidental large broker workloads.
- Chunk size is an API reliability control, not a strategy setting.
- Local Docker verification is more reliable than the hosted Emergent preview for this project.
- Theme contrast must be token-based; hard-coded dark colors quickly become unreadable when adding light mode.
- Practical local finding on 2026-05-27: NIFTY had broad expired contract metadata stored from 2024-11-28 onward, but only a small set of option candles had been fetched. A raw universe audit over 18 months therefore reported many missing contracts. That is expected until the planner-selected moneyness rows are fetched.
- Practical local fetch result on 2026-05-27: first small NIFTY ATM CE/PE Sample 1 batch for `2024-11-27` to `2024-12-31` fetched successfully after routing expired keys to the expired historical endpoint. Job `ba628627-71b6-4a80-a407-3f491ae88362` saved `85,500` candles with zero failed tasks. Planned coverage for that window became `82.61%`; remaining gaps were empty broker responses for some selected `2024-12-26` expiry contracts/dates.
- Practical live stream validation on 2026-05-27: Upstox V3 WebSocket stream was started for `NSE_INDEX|Nifty 50`, `BSE_INDEX|SENSEX`, `NSE_INDEX|Nifty Bank`, `GLOBAL_INDEX|SGX NIFTY`, and `NSE_INDEX|NIFTY MID SELECT`. It received and persisted live ticks with zero reconnects/errors during the short validation window, and Market Header showed those instruments as `Upstox WS`.

## Verification Checklist

```bash
python -m pytest tests -q
cd frontend
npm run build
cd ..
docker compose up -d --build
docker compose ps
```

Also verify in the browser:

- Theme selector switches System, Black, White.
- Data Warehouse loads without unreadable text.
- Option Data Planner preview shows contract count and chunk guidance.
- Backtest Lab still opens and can run an existing strategy.
