# P3 durable receipt view checkpoint

Frontend presentation checkpoint for the approved durable receipt evidence schema proposal.

## Scope

- `frontend/src/components/live/ExecutionReceipts.jsx` renders each API-provided `durable_evidence` list in its own expandable persisted-evidence section, separate from the current broker order-book snapshot.
- The view presents event capture time, source, identifiers, approved/submitted instruction fields, reported fill fields, exit attribution fields, evidence state and reason codes as supplied. It performs no P&L derivation and makes no latest-event or largest-fill assumption.
- Approval wording changes only when an `approval_decided` event is `observed` and has an `approval_id`; it explicitly separates the decision from placement and fills. Missing or unavailable evidence keeps approval history unknown.
- Receipt-level `capture_status.state === "degraded"` displays failed-write count and last failure time. Missing status displays unknown; event errors and raw payloads are not rendered.
- Existing broker snapshot presentation and its age behavior remain in place.

## Mocked component fixtures

`ExecutionReceipts.test.jsx` now covers an observed persisted approval and instruction, unavailable historical evidence, conflicting exit evidence with truncation, and degraded capture status with raw error suppression. Existing receipt, paging, broker-book failure and age fixtures remain in the file.

## Checkpoint boundary

This is a view-only checkpoint. It does not implement or change API, schema, persistence, broker requests, or protected trading controls. Combined frontend verification is being run by the parent task; this checkpoint does not claim those tests have passed.


2026-10-25 verification: root combined React invocation passed 36 tests across six suites; local production compilation passed. Capture telemetry now labels process scope and pending count, and warns that unacknowledged work can be lost. Cumulative snapshots are not summed or ordered as execution history. Running Chrome review remains pending Docker availability.
