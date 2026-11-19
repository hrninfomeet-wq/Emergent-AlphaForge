# P3 broker order-book UI checkpoint

## Scope

Updated `frontend/src/components/live/ExecutionReceipts.jsx` and its React tests
to display current broker order-book evidence separately from stored receipt
fills and journal evidence. The component accepts the parent-supplied
`brokerOrders`, `brokerOrdersError`, and `brokerOrdersReadAt` props. It does not
add a broker request or polling path. A local 5-second clock only recalculates
freshness and is cleaned up on unmount.

The observed state displays quantity, average, broker status, snapshot time and
age, instruction differences, and a diagnostic gross estimate when supplied.
That estimate explicitly uses the journal exit mark; it does not establish final
exit fills or current exposure. Non-observed states do not render broker fill
values. Existing durable approval unknown and broker-held protection limits
remain visible.

## Checkpoint status

- Component and tests are edited against the shared
  `receiptBrokerEvidence` helper and `{orders:[...]}` snapshot contract.
- Added cases for observed-to-stale aging, failed book reads, missing today's
  order-book row, and no additional receipt API call during aging.
- Confirmed: focused React suite passes, **6 tests** (including the three
  requested broker-book edge cases); command was
  `node.exe node_modules/@craco/craco/dist/bin/craco.js test --watchAll=false --runInBand --runTestsByPath src/components/live/ExecutionReceipts.test.jsx --moduleNameMapper='{"^@/(.*)$":"<rootDir>/src/$1"}'`
  from `frontend/`.
- The local clock only triggers a render; each render passes `Date.now()` into
  the helper so a just-arrived parent snapshot is not misclassified as future.

## Review risks

Confirmed limit: the helper marks reads older than 30 seconds stale and suppresses
fill details for unavailable, stale, missing, or conflicting evidence. If the
parent passes the wrong snapshot or last-success timestamp, the UI can withhold
valid evidence; it still cannot turn that missing evidence into zero fills.
