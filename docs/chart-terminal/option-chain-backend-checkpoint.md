# Option-chain backend checkpoint

Confirmed: scoped tests pass with `$env:PYTHONPATH='backend'; .venv/Scripts/python.exe -m pytest tests/test_chart_options.py -q` (7 passed). The bare command from the repository root fails collection because `app` is not on Python's import path. No real broker request, order, production restart, or commit was made.

The service validates underlying/expiry/strike/side and unique option keys against the contract catalogue; uses IST date for nearest expiry even across a cached midnight; preserves null versus zero; caches and coalesces REST requests; retains original capture time and backs off failed refreshes; leases a bounded strike window through the existing shared manager; and releases it on generator close. Tests exercise disconnected, missing timestamp, recovered live updates, raw REST IV retention and the vendor-previous OI baseline label. Source was independently checked against manager lease methods and `tick_age_ms`.

Risks: actual vendor catalogue and real market-hour update behavior are not verified. Vendor IV units and previous-OI baseline remain unknown; if interpreted as percent or daily change, a user could misread the data. A stream-level exception produces a `stream_error` and closes the generator; caller reconnection behavior must be checked in integration. Chain expiry refresh requires client reconnection after selected expiry rolls. Broker and trading code were not modified.

Core lesson: use distinct instrument keys for distinct expiries in test fixtures; duplicate fixture identities correctly fail the guard. Confirmed approach: isolate quote timestamps from retrieval timestamps and bound failed refresh retries. Dead end: running pytest from root without PYTHONPATH cannot import the backend package. Root owns integration, independent review, shared learning log and any commit.

## Real-schema replay and provenance follow-up

Confirmed: replayed the root-provided sanitized real Upstox capture through injected catalogue and REST functions on 2026-11-04 IST. NIFTY selected 2026-11-09 with 123 total strikes; SENSEX selected 2026-11-05 with 182. Radius 40 returned 81 rows for each. This was a local replay, not a new API request or market-hours acceptance.

Confirmed: `PYTHONPATH=backend` targeted pytest now reports 10 passed. New tests cover malformed chain envelopes, 163-key maximum lease with cancellation cleanup, and fresh ticks containing no applicable quote fields. Each leg now carries `field_sources` for LTP/OI/volume; a tick with no usable values retains REST provenance and is waiting rather than live. Spot and ATM derive from REST spot, now explicitly labeled `spot_source=upstox_rest_v2` and `spot_quote_time_ms=null`. Root must ensure the UI exposes that distinction. Partial stream updates retain REST provenance on unchanged fields.

Critical risk remains: vendor IV units and previous-OI baseline are unknown; if mislabeled they could mislead decisions. Real response replay checks schema compatibility only, not current market freshness or trading behavior. Lesson: quote freshness is field-specific; an envelope timestamp does not prove every displayed value came from that envelope.
