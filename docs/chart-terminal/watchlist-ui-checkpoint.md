# Watchlist UI and persistence checkpoint — 2026-11-04

Complete in source and isolated browser acceptance; not deployed to production.

- Left panel adds symbols via shared search, renames list and display labels,
  reorders with accessible up/down controls, deletes, and loads the active chart.
  Optional chain callback is ready for item 4; no nonfunctional chain button shown.
- Backend remains source of truth. Saves serialize; concurrent edits return 409
  and reload for review. Lost acknowledgements reconcile without blind retries.
- One watchlist EventSource uses a batch lease on existing Upstox ownership.
  Freshness ages independently, silent/error streams become stale/offline, and
  unavailable contract identity is explicit. Invalid timestamps cannot crash UI.
- Search instances use unique ARIA IDs. Add dialog traps focus and returns it.
- CI production build compiled. Root selected frontend run: 20 passed across
  ChartWatchlist, useChartWatchlist, ChartTerminal and chartLayouts. The initially
  attempted standalone InstrumentSearch.test.jsx did not exist; its shared-search
  ARIA behavior is covered by ChartWatchlist.test.jsx. Corrected command passed.
- Root selected backend run: 41 passed across watchlist, chart terminal, stream
  leases and multistream. No broker/network execution tests.

Chrome acceptance on localhost:8002 with the real store/router and dedicated Mongo
DB alphaforge_chart_terminal_fixture_20261104: added RELIANCE, NIFTY, SENSEX;
renamed list to Market focus and RELIANCE label to Reliance focus; moved NIFTY up;
deleted SENSEX; clicked row to load active chart. After stop/start of the fixture
process and page reload, revision 7 retained [NIFTY, Reliance focus]. Full response
SHA256 before/after was identical:
e12b45e6675f36915d0e3ea7ff9d7e7e8d32452e9cec9a0af1ba9adbe41047ac.
Quotes were visibly live synthetic data and timestamped; no actual market claim.
Screenshot: C:/Users/haroo/AppData/Local/Temp/alphaforge-terminal-evidence/watchlist-restart.png.

Remaining deployment gate: production start-app.bat restart and real-session
acceptance have not run. Production service restarts need a current exposure
check per HANDOFF because the existing software exit guard must stay running.
Use PROGRESS.md for item 4 (option chain); no option-chain implementation yet.
