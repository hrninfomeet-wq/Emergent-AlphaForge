# P3 durable receipt store checkpoint

Scope: new `backend/app/live/receipt_events.py` and fake-collection contract tests.
No broker, database connection, runtime startup, capture hooks, or accounting edits.

Implemented: allowlisted event builder; normalized finite numbers and cumulative
fill snapshots; bounded scalar identities; deterministic keys excluding capture
time; atomic insert with the same key as Mongo `_id`; bounded fail-safe writes and
idempotent unique-index setup. Invalid envelope input is rejected. Invalid nested
evidence becomes incomplete/conflicting evidence with bounded reason codes.

Confirmed: offline checks pass (checked with
`$env:PYTHONPATH='backend'; & '.venv/Scripts/python.exe' -m pytest tests/test_receipt_events.py tests/test_execution_receipts.py -q -p no:cacheprovider`:
68 passed). The new store suite contains 40 cases. Fixtures cover duplicate and
out-of-order cumulative snapshots, partial/canceled fills, zero and malformed
numbers, explicit/missing exit identity, first-capture retention after writer
restart, concurrent duplicate attempts, forged keys, secrets at each nesting
level, bounded failures, repeatable indexes, and sanitizer rebuild stability.

Critical risk: receipt consumers must retain the cumulative interpretation and
must not treat append success as proof of accounting correctness. Unknown broker
event times remain absent. The first capture survives duplicate inserts.

Lesson: sanitize again at the writer boundary; trusting a prebuilt event allows
forged keys and raw payload fields to enter an otherwise allowlisted ledger.
Confirmed approach: rebuilding at append preserves safe content and deterministic
keys while removing injected fields (checked by the forged-key and nested-secret
tests). Dead end: system Python cannot collect the tests because it has no
`pymongo`; the repository `.venv` runs the fake-only suites without any DB access.

Integration contract: `build_event` returns only the proposed allowlisted schema;
invalid kind/source/capture timestamp/state raises `ValueError`. Other malformed
evidence is retained only in sanitized form and flagged incomplete/conflicting.
Capture timestamps must include a timezone; supplied event times are preserved
and missing event times stay absent. `ReceiptEventStore.append` returns only
`{"status": "recorded" | "duplicate" | "unavailable"}`. `ensure_indexes` returns a
boolean, suppressing bounded startup failures. Each async DB operation has a
0.75-second timeout. No exception text is emitted.

## Capture-hook review checkpoint — 2026-10-25

Added only `tests/test_receipt_capture.py`; capture implementation remains root's
ownership. Confirmed: 89 offline cases pass (checked with repository `.venv`,
`PYTHONPATH=backend`, and
`python -m pytest tests/test_receipt_capture.py tests/test_receipt_events.py tests/test_execution_receipts.py -q -p no:cacheprovider`).
The capture suite contains 21 cases, all using fake collections and an injected
fake `app.db`; no broker, live database, UI, or server action was executed.

Confirmed: scheduling returns while the fake writer remains blocked; bounded
store failures are visible without exception payloads; approval evidence links
to actual returned `cid`/`norenordno`; caller mutation does not change captured
instructions; book queries exclude foreign/mock orders; conflicting identities
remain conflicting; cumulative out-of-order, partial, canceled, and repeated
snapshots persist without summation. Exit fixtures require an explicit unique OCO
entry tag, refuse same-symbol re-entry guesses, and mark missing fill IDs as
incomplete (checked by the named capture tests).

Review finding resolved by root: the initial direct offline reproduction with
approved entry quantity 100 and tagged exit fill quantity 101 returned `observed`
without reasons. Root added capture-side overfill detection. Confirmed: the
boundary fixtures now preserve quantities 99 and 100 as observed, and quantity
101 becomes conflicting with `overfill` (checked by
`test_exit_quantity_boundary_against_approved_entry`). The persistence builder
alone does not infer this exit bound; capture and readers own attribution checks.

Final focused verification: 103 combined offline cases pass using the same
three-suite command above, including 35 capture cases. Added boundaries cover
unknown/raw/structured status omission, COMPLETE quantity mismatches, zero-fill
positive-average contradictions, full fills and canceled partial fills. The
new status filter initially raised on dict/list values; the failing fixtures
reproduced that, and root's explicit string guard makes both incomplete evidence
without exceptions (checked by
`test_unknown_or_structured_status_is_omitted_without_raw_payload`).

Lesson: successful explicit identity linkage is necessary but does not establish
quantity validity; test those claims separately. Confirmed approach: inject a
minimal fake `app.db` module before exercising deferred hooks, preventing lazy
imports from constructing a database client. Dead end avoided: no test imports
the real database module or calls a broker to verify capture behavior.
