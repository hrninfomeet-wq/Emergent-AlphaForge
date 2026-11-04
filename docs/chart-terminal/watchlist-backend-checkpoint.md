# Watchlist backend checkpoint

Baseline: HEAD 0548405; existing unrelated dirty files are preserved. The shared stream tracks baseline, required, and chart owners, with 50 full_d30 / 1500 other key ceilings. Instrument resolution filters expiry. Critical check: compare persisted contract identity against the current catalogue before any quote lease; never replace a retained identity during edits.

Work in progress: persistent Mongo CAS, batch leases, SSE and offline tests. Production services and broker sessions are untouched.

## Source checkpoint

- Mongo document `_id=default`, revision compare-and-swap and stored canonical identities; retained entries are never re-resolved on rename/delete. New entries resolve through chart catalogue, duplicates and attempts to replace a retained key without its saved ID fail.
- `GET/PUT /charts/watchlist`, `GET /charts/watchlist/stream?revision=N` once parent includes router. Stream polls once per second and copies the shared tick map once, checks current identity before leasing and periodically thereafter, reports unavailable items, and cleans up on cancellation. Changed revision emits `watchlist_changed` and exits.
- Batch owner sets participate in configure/reconnect and respect shared ceiling, while `_persistent_ticks` remains baseline/required only. Failed sends reserve uncertain subscription budget pending unsubscribe/reconnect.
- `.venv\Scripts\python.exe -m pytest tests/test_chart_watchlist.py tests/test_chart_stream_leases.py tests/test_chart_multistream.py -q`: 23 passed, one third-party warning. Expanded test_chart_terminal run exposed its pre-existing all-chart-routes-GET assertion after parent integrated the authorized PUT; parent was notified to revise that assertion.
- No real Mongo restart or live-feed test claimed. The separate fixture owner is testing isolated Mongo and browser integration. No production restart or broker action.
