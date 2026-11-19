# Chart session change — 2026-11-02

## Deployed and browser-checked

User confirmed "Account is flat; deploy now". Recreated only backend/frontend
with `docker compose up -d --no-deps backend frontend` on 2026-11-02 at
13:37 UTC. Mongo retained container `eb10b0b16368...` and start time
2026-11-01T10:48:15.574942141Z. Health returned `db: ok`. After startup the broker
session was connected, authenticated position/order books were empty, no guards
or conditional orders existed, and no live deployment was armed. No order or
broker-login action was taken.

Host/backend captured source matches
`b570bc7c6a5f260e04df336461c174d89469071d1aadf29498f9a3ebb7c573c5`;
Chrome reports the same frontend/backend fingerprint. Chrome shows
`+387.70 pts (+1.68%) · Today` on 1m, 5m and 1d; restored 1m afterward.
The tooltip exposes previous close 23,053.10 and quote time 02 Nov, 16:00 IST.
Independent Decimal arithmetic from the real SSE response confirms:
23,440.80 - 23,053.10 = 387.70 points;
387.70 / 23,053.10 * 100 = 1.681769480026547405771891850% → 1.68%.
Browser error log is empty. Screenshot:
`C:/Users/haroo/AppData/Local/Temp/alphaforge-chart-evidence/session-change-header.jpg`.

Market is closed and the feed badge says disconnected. This verifies the last
quote and timeframe independence, not continuously moving market-hours ticks.
Source tests cover price updates and invalid/missing-reference behavior.

The following preparation notes are historical; the approval/rollout gate is closed.

Request: replace the chart header's candle return with the ongoing trading day's
change in points and percent. The reference is the previous trading session's
close, not the current candle open or today's opening price.

Implementation:
- `chart_stream.stream_state` adds the existing sanitized `close_price` field.
  Upstox's decoder maps LTPC `cp` to this field; `market_header.normalize_quote`
  independently uses it as previous close. No extra subscription or REST polling.
- `sessionQuote` derives both the header price and return from the same matching
  quote. Timeframe and candle open do not enter the calculation. Missing/invalid
  reference shows `Day change unavailable`; invalid/foreign quotes cannot leak.
- Quote date is evaluated in Asia/Kolkata. Older quotes display their date instead
  of `Today`; the existing feed freshness badge remains visible. Header tooltip
  exposes the previous-close reference and quote time. Gains/losses use existing
  green/red styles; rounded zero has no misleading minus sign.

Confirmed checks: 23 Python tests (`test_chart_terminal.py` and
`test_chart_stream_leases.py`), 21 frontend tests (`chartPresentation.test.js`
and `useChartData.test.jsx`), CI=true production build. A memory-only mutation
removing `close_price` makes the new backend regression fail. A separate memory-only
frontend mutation replacing previous close with candle open also fails the
expected display assertion. Docker backend/frontend image builds pass; running
backend/frontend/Mongo container IDs and start times remain unchanged. Independent focused
agent review found no actionable defect and traced both previous-close mappings.

Critical numerical check: 21,231.10 - 21,000 = 231.10 points;
231.10 / 21,000 * 100 = 1.10047619047619%, displayed as +1.10%.
Positive/negative/zero/missing reference, wrong symbol, invalid timestamp and
IST midnight boundaries are covered. No trading or broker-login code changes.

At the read-only deployment gate, the API reported 2026-11-02 18:14 IST:
LIVE_OFFLINE, session_expired=true, zero armed deployments, no guards and no
conditional orders. Positions and orders returned HTTP 400, which is not proof
of flatness. User was asked asynchronously for current flat/no-pending-orders
confirmation per HANDOFF.md:340. No backend restart has occurred for this change.
The previous flat confirmation was for the prior rollout, not this session.

Rollout, real-browser header/timeframe checks and screenshot are complete above.
Live moving-tick acceptance cannot be claimed from an after-hours check.
With no cached quote or valid previous close,
the header intentionally shows unavailable rather than estimating from candles.

Resume here; do not redo the original research or replace the dirty worktree.
