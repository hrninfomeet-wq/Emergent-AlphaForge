# Chart terminal — resume here

**2026-11-02 extension deployed:** multiple indicator instances (EMA20/EMA50 etc),
saved indicator settings, arrows/rays/ruler/vertical line and drawing color/width/
dash controls. Frontend only rebuilt; backend stayed running with expired broker
session. Frontend/backend captured-source badge mismatch is expected. See
[tools extension evidence](tools-extension-checkpoint.md). Earlier matching-build
and connected-session statements below are historical.

## Current result — locally deployed and Chrome checked, 2026-11-01

Confirmed: operator explicitly confirmed a flat account and authorized startup,
rebuild and verification. Before both app recreations, authenticated Flattrade
positions/orders, guard registry and conditional orders were empty. Mode was
LIVE_OFFLINE, zero live deployments armed, and all nine deployments were paper.
Backend/frontend/conditional-watchdog were rebuilt and recreated; Mongo container
`eb10b0b16368fc752e98efef3c931211c332d233c3222840168c3168fe1c5fab`
and its `2026-11-01T10:48:15.574942141Z` start time stayed unchanged.

Confirmed: frontend/backend captured-source SHA is
`5acdeeaff66eaa34ea5586019a12482f2c2c9029270d31c381d9c129e3b47995`.
Host fingerprint matches it; chart data/stream/positions/router, shared stream and
runtime files also match the running backend by SHA-256. Both broker sessions
remained connected. No broker login/logout or order action was performed.

Acceptance completed:
- Real API candles: NIFTY/BANKNIFTY/SENSEX use warehouse+Upstox; NIFTY 100 and
  NSE/BSE RELIANCE load on demand. NIFTY futures and NIFTY/SENSEX option samples
  returned candles. These are coverage samples, not proof for every listed symbol.
- All eight timeframes returned sorted, unique bars. Chrome verified 1m/5m/daily,
  instrument search, pagination from 600 to 1,200 bars, all requested indicators
  (equity volume/VWAP; index volume disabled), and fullscreen rendering.
- Isolated synthetic preview verified horizontal/trend/Fibonacci creation and
  reload persistence, separate per-contract drawings, entry/target price lines,
  and a visibly stale on-chart paper P&L chip. No live positions were opened to
  test overlays. The preview has no broker login; it was closed and its helper
  processes stopped after checks.
- Browser inspection found/fixed light-theme heading contrast and canvas stacking
  that hid the P&L badge. Fixed badge was visually checked, then rebuilt locally.
- Leaving Charts released its viewer lease (count 0), removed the temporary
  RELIANCE key and retained all 11 execution-owned keys. No console errors in
  the final real chart. `/charts` is left open in the user's Chrome tab.
- Final selected backend run: 112 passed. Node model/indicator run: 18 passed.
  React selection: 19 passed. CI=true build and Docker production builds pass.

Remaining acceptance: an open-market session must verify ticks, reconnect/gap
repair under actual traffic, volume reconciliation and live/paper overlay changes
when naturally occurring positions exist. Off-static-IP behavior has unit/service
separation evidence, but was not network-tested by disconnecting the user's broker.
Do not call this trading-ready based on closed-market checks.

Confirmed: `/api/live-feed/health` reports MARKET_CLOSED. Existing
`live_feed_health.decide_feed_actions()` stops the feed outside trading hours;
manual Stream startup therefore stops again on this Sunday. No supervisor policy
was changed. Historical charts remain usable; authenticate again when tokens
expire. Do not repeatedly restart the feed to force a Sunday live test.

Evidence screenshots (local temporary artifacts):
`C:/Users/haroo/AppData/Local/Temp/alphaforge-chart-evidence/real-nifty-chart.jpg`
and `synthetic-position-overlay.jpg` in the same directory. No commit or push.

The older progress notes below are historical and superseded by this result.

## Authorization and scope

Operator approved proper implementation after the research report. Three stored
indices are shortcuts/history sources, not a chart universe limit. Other current
NSE/BSE symbols load on demand and are cached in memory only. Private local use;
Upstox data works independently of Flattrade static-IP login. No chart trading.

Branch: `codex/chart-terminal`. Preserve the large pre-existing dirty worktree;
do not stash/reset or commit unrelated changes. Plan:
`docs/superpowers/plans/2026-10-31-chart-terminal.md`.

## Completed source work (2026-11-01 continuation)

- New catalogue/history service `backend/app/chart_data.py` (data agent owns).
- Temporary chart feed leases in `upstox_stream.py`: reference counts, bounded
  viewers, subscription budget, cleanup, filtered queue/drop count, chart-only
  tick persistence exclusion. Small `runtime.py` check distinguishes execution
  ownership from temporary chart coverage.
- New `chart_stream.py`, `chart_positions.py`, `routers/charts.py` (root owns).
  Broker quantity/average remain authoritative; paper ledger is separate;
  conditional and legacy guard levels are read-only metadata. Matching by
  contract expiry is required; stale books are not re-marked with new ticks.
- Frontend page/components/API adapter, pure indicators/drawing schema written.
  New `/charts` route and Overview nav registered. UI agent owns chart files.
- Installed pinned MIT `lightweight-charts-drawing@0.6.2` via
  `corepack yarn add --exact`; package diff is one dependency plus five lock lines.
  Plain `yarn` is unavailable on host; use `corepack yarn`.

## Checks and review

- Root stream/terminal selected pytest last run: 24 passed. Two additional
  watermark/product regression cases were subsequently added and need rerun.
- Math agent: 18 Node tests passed; UI agent: 17 React tests passed before final
  watermark fix. Root must rerun the combined final selection.
- First production build failed one React hook dependency warning in
  `useChartData.js`; UI agent is fixing this with REST/SSE ordering.
- Independent reviewer found and root fixed: failed chart send falsely treated
  as execution coverage; recycled future token match; conditional intraday
  protection attached to carry-forward rows. Re-review in progress.
- REST/SSE race fix in progress: accumulator carries `history_as_of_ms`, ignores
  queued ticks before its REST seed, peeks newer shared history cache before
  publishing; browser must ignore older-seed SSE bars after a newer REST result.
- Data agent is improving latest-saved fallback, saved-history precedence,
  special-session handling, and adding the synchronous `peek_history` method.

## Remaining gates

1. Complete agent handoffs and review fixes; run root combined Python/Node/React
   checks and frontend production build with CI=true.
2. Check current arm-state, deployment modes, broker positions, guard state,
   conditional orders before any backend recreation. Do not infer a flat
   account from an expired/unreadable broker. No login/logout/order actions.
3. Rebuild local app only when safe; Mongo stays running. Verify running source
   parity and Chrome `/charts` behavior, history/search/timeframes/drawings/
   indicators/error states. Do not report unrun browser checks as complete.
4. Open-market acceptance remains a distinct check; closed-market UI tests
   cannot establish live latency, continuity, volume completeness or reliability.
5. Update AGENT_TODO/HANDOFF/CHANGELOG/learning_log with final facts. No push.

## Latest resumption checkpoint — 2026-11-01

Source integration and offline checks are complete. The root reran the combined
selection: **112 Python checks**, **19 React checks**, **18 Node checks**, all
passed. The CI=true production build passed after the hook fix and lazy chart
route split. Subsequent small changes add a cache watermark fast path and surface
server stream errors; their final focused verification is recorded below when run.

Real public-master search passed: NIFTY 100/200, SENSEX50, NSE/BSE RELIANCE and
current futures returned without catalogue warnings. Authenticated candles,
Mongo-backed history, Chrome rendering and market-session streaming are not yet
accepted. The shared-feed review found four defects; root fixed each and added
regressions (including actual async stream correction, not just helper tests).
Additional option strike/side contradiction checks and on-chart P&L chips added.

**Running-app blocker:** Docker daemon pipe is absent; API :8001 refuses
connections. No container restart was performed. An asynchronous question asks
the operator to confirm no open Flattrade positions/pending live orders and allow
Docker startup/rebuild. This follows HANDOFF.md §3's required flat-account check;
no response is not approval. Once answered, resume at remaining gate 2, then
rebuild only app services, leave Mongo data intact, and verify Chrome/source parity.
No need to reimplement any source or repeat the research.

## Tool/environment notes

Python tests: `.venv/Scripts/python.exe -m pytest ... -q -p no:cacheprovider`.
Node math: `node --test tests/js/chartModel.test.mjs tests/js/chartIndicators.test.mjs`.
Frontend build: set `$env:CI='true'`; `corepack yarn build` in `frontend`.
App images bake source; merely restarting serves stale code. Browser origin is
`http://localhost:3000`, host API is `http://127.0.0.1:8001/api`.

Learning: test subscription ownership separately from membership; a displayed
symbol is not an execution-owned feed. Unknown identity or stale state must
remain unknown rather than becoming a moving P&L number.
