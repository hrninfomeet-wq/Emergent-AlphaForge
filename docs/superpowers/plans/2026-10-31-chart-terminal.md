# AlphaForge private chart terminal — implementation checkpoint

**Authorization:** The operator explicitly requested implementation after the research report, on 2026-10-31. The earlier research-only restriction is superseded. Personal local use; arbitrary supported symbols load on demand, without expanding warehouse storage. Off the registered IP, Upstox charting remains available and Flattrade may be disconnected. No chart order controls.

**Done test:** A separate Charts screen renders saved index history and on-demand instruments, streams correctly identified candles, provides the requested indicators/drawings, and shows read-only, source-labelled positions with truthful freshness. Changing or closing charts cannot remove execution subscriptions. Offline tests and browser checks pass; open-market acceptance is separately recorded.

**Estimate:** Four checkpointed sessions: data; chart tools; positions/integration; open-market acceptance. Continue independent work in the current session. Session count is an estimate, not an implementation guarantee.

## Pieces, ownership, checks

1. **Data access (decision-critical):** new `backend/app/chart_data.py` and tests. Read the existing warehouse for known index aliases, public Upstox master for all supported NSE/BSE indices/equities/current F&O, and authenticated REST candles on demand. Cache in memory only. Check: sorted unique UTC-second OHLC, IST bucket alignment, bounded retrieval, expired/disconnected token preserves saved history, generic equity/index/option identities resolve correctly.
2. **Shared-feed isolation (highest risk; root):** extend the existing Upstox stream with temporary reference-counted chart subscriptions. Never remove baseline/required keys; never restart the execution feed; exclude chart-only ticks from persistence. Check independently: multi-client acquire/release, reconnect, failure cleanup, required-key promotion, and unchanged existing stream tests.
3. **Chart math (agent):** pure `chartIndicators.js` and `chartModel.js`. Indicators are presentation calculations, not changes to strategy semantics. Check: independently calculated fixtures, Wilder seeds, session-reset VWAP, unavailable index volume, sorted/deduplicated finite bars, application-owned drawing identity.
4. **UI (agent):** `ChartTerminal.jsx`, `components/charts/*`, and `chartApi.js`; use installed Lightweight Charts 5 and the verified MIT drawing plugin 0.6.2. Check: changing symbols/timeframes cancels stale requests, chart teardown releases SSE, indicator panes, saved drawings, read-only positions, loading/error/empty/stale states and attribution.
5. **Integration (root):** new chart router/live aggregation/position adapters; small route/nav registration changes. Broker positions own actual live quantity/average; strategy/guard state supplies protection metadata; paper remains separate. Check: identity matching, no double count or premium lines on an underlying, missing/old broker state visibly stale, no order/mutation endpoints.
6. **Review and acceptance (root + independent review):** focused Python/JS tests, frontend production build, browser checks, current broker/execution state before any local restart. Preserve pre-existing dirty work. Check: source/running parity if deployment is safe; otherwise state the exact pending gate. Market-closed tests do not certify live feed latency or reliability.

## Stable data contracts

- Instrument: `{instrument_key,symbol,name,exchange,segment,kind,volume_available,warehouse_symbol?,expiry?,strike?,option_type?,lot_size?,tick_size?}`. `kind`: index/equity/option/future. Exchange tokens are not eternal contract identity; include expiry in drawings/overlays.
- History: `{instrument,timeframe,bars,source,warnings,next_before,has_more,as_of_ms,volume_available}`. Bar: `{time,open,high,low,close,volume}`; `time` UTC seconds; volume null when unavailable. Timeframes: 1m,3m,5m,15m,30m,1h,1d,1w. `before` UTC seconds is exclusive. No invented bars for gaps.
- GET `/api/charts/instruments?q=&limit=30`; GET `/api/charts/history?instrument_key=&timeframe=1m&limit=600&before=`.
- GET `/api/charts/stream?instrument_key=&timeframe=` SSE event `snapshot`: `{instrument_key,timeframe,bar?,tick?,connected,last_tick_age_ms,source,status,warnings,emitted_at_ms}`. Event `heartbeat` updates freshness. Event `resync` requests REST reconciliation after overflow/reconnect. No token disclosure.
- GET `/api/charts/positions?instrument_key=`: `{positions,warnings,as_of_ms}`. Position: `{id,origin:"broker"|"paper",symbol,instrument_key,quantity,entry_price,mark_price,pnl,stop_price?,target_price?,protection_source?,fresh,mark_age_ms?,broker_age_ms?,label?}`. A missing value stays null; unknown protection is not fabricated.
- JS `normalizeBars(rows)`, `mergeBars(history,updates,maxBars=10000)`; `INDICATOR_DEFINITIONS` array `{id,name,defaults}`; `computeIndicators(bars,configs,volumeAvailable)` where configs is array `{id,enabled,params}` returns `{series:[{id,name,pane,type:"line"|"histogram",data,color}],warnings}`. `pane` is `price` or an indicator id. Empty/warmup output stays empty. Defaults: EMA20,SMA20,BB20/2,RSI14,MACD12/26/9,Supertrend10/3,ATR14,ADX14,volume,VWAP.

## Non-goals and red flags

- No order placement, modifications, broker login/logout, trading guard changes, strategy changes, or expanding persistent storage to arbitrary chart symbols.
- Index volume is unavailable: VWAP/volume must say unavailable, never substitute an arithmetic mean.
- Streaming OHLC formed from ticks is provisional until REST reconciliation; dropped/late ticks and mid-bucket attachment must be labelled. Preserve extrema before browser coalescing.
- New charts must be bounded against account subscription limits and must not remove any execution-owned subscription. This is the critical independent review item.
- Drawing storage must retain semantic time/price anchors and contract identity, not only canvas pixels or plugin internals. Future backtest/strategy overlays remain possible.

## Progress / resumption

- [x] Research report delivered and implementation explicitly authorized.
- [x] Research/context and repository state recovered. Branch `codex/chart-terminal` created, pre-existing work preserved.
- [x] Verified drawing plugin npm metadata and official README; package not installed at this checkpoint.
- [x] Data access and tests.
- [x] Feed leases, live candles and tests.
- [x] Indicators/drawings/UI and tests.
- [x] Position adapters and integration.
- [x] Independent review findings corrected, build, safe running-app/browser check. Final evidence in `docs/chart-terminal/implementation-checkpoint.md`; reviewer post-fix rerun was unavailable, root regression checks passed.
- [ ] Open-market acceptance.

Use this file for detailed resumption; keep `docs/AGENT_TODO.md` as the live status index. Root reviews each agent output before accepting it. Do not report an unrun check as passed.
