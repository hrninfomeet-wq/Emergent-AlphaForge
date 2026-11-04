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

## Isolated option fixture acceptance support

`tests/manual/chart_option_fixture.py` injects synthetic catalogue/REST adapters
into the real `ChartOptionsService` and real `/api/charts/options` router. The
terminal fixture shares its quote manager with watchlists, resolves generated
options in existing chart history/streams, and preserves paper `NSE_FO|999`.
NIFTY and SENSEX each have two future expiries (current IST date plus three and
ten calendar days) and 91 strikes per expiry. ATM CE has literal zero metrics;
the next higher CE has missing OI/previous OI/volume/IV. Other quotes change each
second. Payload source is `synthetic_ui_fixture` with an explicit synthetic notice.

After root restarts the isolated fixture, get a current key from a chain response:

```text
GET /api/charts/options/chain?instrument_key=NSE_INDEX%7CNifty%2050
```

To force a visible streaming update, POST `/api/fixture/option-market` JSON:

```json
{"instrument_key":"<ce.instrument.instrument_key from a displayed row>","ltp":321.25,"oi":555,"volume":123}
```

`GET /api/fixture/option-state` reports active quote leases, their instrument keys
and overrides; closing the only chain/watchlist consumer must release its lease.
Values persist only in this fixture process. No production launch or broker calls.

Confirmed: repository `.venv/Scripts/python.exe` import check loaded neither
`app.runtime`, `app.encryption` nor `app.warehouse`; the Upstox client import is an
explicit forbidden-remote stub. ASGI checks passed both NIFTY and SENSEX expiry,
chain (91 total / 41 displayed), selected-option history, forced streaming
LTP/OI/volume update, single-lease acquisition and zero-lease cleanup. Default
system Python lacks `pymongo`; launch this fixture with the repository `.venv`.
Browser acceptance remains root-owned.

## ATM opening viewport follow-up

Root browser review found the initially highlighted ATM below the visible table.
The dialog now centers ATM using geometry relative to its own scroll container
on a new instrument/expiry/strike window. A window without ATM resets to the top.
The identity excludes quote values, capture times and ATM movement within the
same returned window, so ticking snapshots preserve the user's manual scroll.
No page scroll or fullscreen ownership changes. Root's provenance tooltips and
Spot (snapshot) label are preserved.

Confirmed: focused React rerun passes both suites, 16 tests. The new geometry
regression checks initial centering, manual scroll preservation after changed LTP
and capture time, top reset when paging away from ATM, and centering after ATM
reset. Browser geometry acceptance remains root-owned after the next build.
