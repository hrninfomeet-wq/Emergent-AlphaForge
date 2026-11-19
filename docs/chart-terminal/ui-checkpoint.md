# Chart Terminal UI checkpoint

- Scope: new page, new charts components, read-only chart API adapter; shared route/nav/package integration belongs to root.
- Read: AGENTS, HANDOFF, work board, integrity audit, approved chart plan.
- Verified dependency contract: installed `lightweight-charts-drawing` 0.6.2 `dist/index.d.ts` and runtime implement DrawingManager, semantic `{kind,points,style}`, change/tool events, clear/destroy. Lightweight Charts typings expose addSeries with pane index, pane height, price lines.
- Decision-critical UI check: old symbol/timeframe responses, streams, and local drawing persistence must never cross identities. Generation guards, AbortController cleanup, response identity checks, immediate render-state guard implemented in data hook; tests pending.
- Added chart API adapter (GET and EventSource only), data hook, freshness/filter/drawing-envelope helpers. UI canvas/page and executable tests pending.
- Drawing envelope: schema_version 1, canonical full instrument identity including expiry, normalized semantic anchors. Corrupt/foreign envelopes disable writes and retain original storage. Quota failure remains visible. Plugin runtime objects are not the durable schema.
- No broker order, session, protected trading control, restart, or deployment action.
- Next: canvas/tools, indicators, search and positions UI; focused React and persistence tests, then root build/browser review.

## Root verification after resumption — 2026-11-01

Page/canvas/search/settings/positions source is complete. Root ran 19 passing
React tests and a passing production build. The chart route is lazy loaded;
existing screens do not eagerly load the drawing plugin. Live P&L chips render
inside the chart area. Protection slice labels include SL/Target and quantity.
REST/SSE watermarks prevent delayed stream bars from undoing corrected OHLC.
One hook-dependency build error was corrected and the same CI build passed.
Superseded by the rollout below.

## Chrome acceptance — 2026-11-01

Real charts, search/timeframes, pagination, all starter indicators and fullscreen
were exercised. Synthetic preview checked drawing creation/reload persistence
and stale paper P&L/price lines without opening positions. Fixed light-theme
heading contrast and canvas stacking that hid the P&L chip. Preview closed and
helper stopped. Final real tab has no console errors and is left at `/charts`.
Real moving ticks/positions remain an open-market acceptance gate. See the main
implementation checkpoint for source fingerprint and API coverage evidence.
