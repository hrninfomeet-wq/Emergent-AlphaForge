# Durable receipt evidence — schema proposal, APPROVED and rolled out

Purpose: retain future approval, order-fill and exit evidence across restarts and
day-scoped broker books. Existing missing historical evidence cannot be recreated.
This is the remaining persistence portion of proposal P3, beyond the running
read-only receipt and current-book comparison.

## Exact proposed addition

Add one MongoDB collection, `execution_receipt_events`. Append events rather than
rewriting the trade journal. Existing collections and saved results stay intact.

| Field | Content |
|---|---|
| `event_key` | Deterministic deduplication key; unique index |
| `schema_version` | Integer 1 |
| `kind` | `approval_decided`, `instruction_submitted`, `order_fill_snapshot`, or `exit_fill_evidence` |
| `recorded_at` | UTC capture time |
| `source` | Explicit source, such as app approval, order event or broker book |
| `client_order_id`, `norenordno` | Order identifiers when known; missing remains null |
| `approval_id`, `deployment_id` | Existing app identifiers when applicable; never approval tokens |
| `instruction` | Exchange, broker symbol, side, product, order type, quantity, limit/trigger prices and validity actually approved/submitted |
| `fill` | Reported cumulative quantity, weighted average and broker event/fill time when supplied; never position-book day averages |
| `exit_evidence` | Explicit linked exit order/fill identifiers, quantity, price and attribution method; absent if not proven |
| `evidence_state` | Observed, conflicting or incomplete, with bounded reason codes |

Only allowlisted fields are stored. No API/session/approval tokens, credentials,
account IDs, raw broker payloads or unrelated personal data. No historical backfill
from guesses. Deduplicate repeated cumulative snapshots; do not sum repeated
`fillshares` reports. Unknown event time remains unknown.

## Integration boundary

- Record the existing approval decision and immutable instruction; this does not
  add permission, bypass a gate or introduce a new approval mechanism.
- Capture evidence already observed by existing order-event/book paths. No
  additional broker polling or order calls.
- Receipt reads can show durable evidence with its capture time. Failed writes
  must remain visible as missing evidence, never be reported as recorded.
- Do not change journal P&L, kill-switch/latch behavior, risk caps, strategy logic,
  deployment validation or order routing. Any later accounting fix is a separate
  reviewed change with its own reproduction and protected-code approval as needed.
- No deletion/TTL of receipt evidence and no modification of existing rows in
  this initial addition. A retention policy can be decided separately.

## Completion checks after approval

Use fake DB/recorded fixtures only: approval-to-order identity, duplicates and
out-of-order fills, partial/canceled fills, cross-contract/re-entry refusal,
missing exit attribution, write failures, restart reads, secret-field omission
and proof that instrumentation adds no broker mutations. Review the exact code
diff before rollout; retain the existing no-live-action testing boundary.

Approval authorizes this additive persistence implementation, not live testing,
live deployment, historical P&L rewriting or protected-control changes.

## Approval checkpoint — 2026-10-24

Operator explicitly approved the proposed receipt storage in this chat. Implementation and isolated verification are in progress; deployment is not yet complete.

## Rollout checkpoint — 2026-10-25

Implemented and deployed after renewed flat-account confirmation. The collection and unique event-key index are verified; API and Edge UI checks pass. No historical/fake events were inserted. See `durable-rollout-verification.json` and the cumulative implementation checkpoint for capture limits and remaining accounting work.
