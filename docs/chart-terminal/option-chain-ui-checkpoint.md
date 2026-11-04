# Option chain UI checkpoint

Reviewed 2026-11-04. This checkpoint covers the dialog, data hook, read-only API
adapter and focused React tests. Root owns terminal integration and acceptance.

## Pieces and checks

1. Data identity and freshness (critical: drives a trading decision): confirmed
   against `backend/app/chart_options.py`, its router and the independent REST/WS
   comparison in `option-chain-research.md`. UI uses canonical contract metadata,
   preserves null versus numeric zero, labels vendor previous OI and raw IV units,
   and distinguishes REST capture from exchange quote time. No order endpoint.
2. Expiry and window selection (cost: redone work): nearest nonexpired ISO expiry
   uses the IST date, reloads at date rollover and preserves a valid selection.
   Lower/higher windows and ATM reset use the backend center/radius contract.
3. Request ownership (cost: redone work): old HTTP work is aborted and owned SSE
   closed on context replacement/unmount. Disposed handlers and mismatched
   identities cannot update current context; older emission timestamps are rejected.
4. Dialog rendering (cost: redone work): mirrored CE/PE metrics, ATM highlight,
   keyboard focus containment/restoration and chart-only selection covered by tests.
   The dialog renders in place, allowing the terminal's fullscreen root to contain
   it. Browser fullscreen acceptance remains root-owned.

## Review fixes and test evidence

Initial test run exposed a test fixture with only one strike while expecting
enabled paging; fixture corrected to contain multiple strikes. Review also found
that backend `stale` and `disconnected` states were ignored, manual refresh cleared
the last snapshot, and expiry rollover was absent. Those behaviors are corrected.
Failed refresh now retains the same snapshot and original capture timestamp while
showing an error and stale state. Only actual live updates receive the green badge.

Confirmed: the following command passed both suites with 15 tests, zero failures:

```powershell
# cwd: frontend; CI=true
corepack yarn test --watchAll=false --runInBand --runTestsByPath src/components/charts/ChartOptionChain.test.jsx src/components/charts/useChartOptionChain.test.jsx
```

Tests cover missing/zero values, chart handoff, keyboard interaction, controls,
empty/error responses, nearest expiry, metadata/expiry races, cleanup, null quote
replacement, stream silence, paging, failed-refresh retention, explicit backend
freshness states, IST rollover and out-of-order stream emissions.

## Limits and risk

Critical risk: vendor IV units and previous-OI baseline remain unspecified. If a
consumer interprets them as percentages or daily changes, the trading conclusion
can be wrong. UI therefore retains vendor labels and never scales IV.

Not done here: production build, browser layout/fullscreen check, real data
entitlements, market-hours behavior, or authenticated broker/data calls. Synthetic
fixtures establish rendering and lifecycle behavior, not vendor data correctness.
Automatic SSE transport reconnection uses EventSource; a server `stream_error`
ending a stream can require the visible Refresh action to recover.

Core lesson: transport health and quote freshness are separate facts. Confirmed
approach: compare UI state handling directly against backend fields and assert
preserved capture time under failure. Dead ends to avoid: treating a recent SSE
arrival as proof of fresh quotes, clearing the last snapshot during a failed
same-context refresh, or selecting expired contracts after IST rollover.
