# Isolated watchlist fixture checkpoint

The chart fixture uses the production `WatchlistStore` and watchlist router,
with fixture-only getter overrides and a synthetic quote manager. Conditional
orders still use `MemoryStore`, `PaperGateway`, and forbidden live gateway.
Unknown API paths still return 404. No production runtime is imported.

## Start command (root owns server startup)

```powershell
.\.venv\Scripts\python.exe tests/manual/chart_terminal_fixture.py --build frontend/build-chart-terminal-fixture --watchlist-db alphaforge_chart_terminal_fixture_20261104
```

Replace only the build directory with the actual isolated frontend output path.
Listen address remains `127.0.0.1:8002`. Without the database argument watchlist
storage is in memory. The argument accepts only the exact isolated name above;
`alphaforge` is rejected before constructing a Mongo client. URI is fixed to
`mongodb://127.0.0.1:27017`; production environment configuration is not read.
No Docker commands, database drops, or broker calls are part of the fixture.
Explicit Mongo mode displays “no broker; isolated test storage”. Startup checks
the selected collection so missing Mongo fails instead of pretending persistence.

## Checks completed

- Venv TestClient: real router GET/PUT roundtrip passed; unknown GET/POST API
  paths returned 404; synthetic snapshot and quote-lease cleanup passed.
- Import inspection before and after requests found no `app.runtime` import.
- Calling `open_collection('alphaforge')` raised `ValueError`.
- Dedicated Mongo fresh-client/fresh-store reload matched. The isolated database
  had no default document, so real `get()` initialized revision 0, empty list.
  No prior document was overwritten; no database was dropped.
- System Python lacks `pymongo`; use the project venv shown above.

## Remaining root acceptance

Build the frontend with the isolated backend URL, then start this command. Perform
add, rename, reorder, delete through the browser, stop only this fixture process,
restart with the same dedicated database flag, and compare the saved list. This
browser/process restart check has not been run by the fixture agent. Do not treat
this as production `start-app.bat` acceptance or broker safety validation.

Lesson: a persistent UI check needs an explicit isolated database plus real store
and router; an in-memory fixture alone cannot demonstrate restart persistence.
Confirmed approach: getter overrides let the real API run without runtime wiring.
Dead end: default system Python cannot run the fixture dependencies; use `.venv`.
