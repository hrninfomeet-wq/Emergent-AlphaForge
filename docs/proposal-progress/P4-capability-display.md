# P4 — NIFTY/SENSEX manual ticket capability display

> Final integration update: root reviewed this implementation, rebuilt both images, verified served source parity and checked the feature in Chrome. See `../PROPOSAL_IMPLEMENTATION_2026-10-24.md` and `rollout-verification.json`. Earlier local-only/build-pending notes below are retained as historical checkpoints.

## Status

Implemented locally for root review. No commit or push.

## Change

- Added `frontend/src/components/live/manualTicketCapabilities.js`, a pure presentation helper for exchange, lot size, freeze quantity, tick, available app choices, and manual placement scope.
- Integrated a concise capability panel into `frontend/src/components/live/LiveOrderTicket.jsx`.
- The panel distinguishes selectable app rules from the manual executor's actual supported instruction: one-lot BUY / MIS / LIMIT.
- CO/BO are described as unavailable in the app's order rules. The UI does not claim NIFTY exchange prohibition or current broker guarantees.
- Stop handling is identified as using the software guard; the panel says it does not verify broker-held exit protection.
- API-returned figures are labeled app-reported, with running build and broker state unverified. Local fallback values are labeled offline and unverified.
- Incomplete API payloads are labeled as a mix of partial app rules and offline defaults.
- No API/schema, backend, strategy, or execution changes.

## Evidence and limits

- `backend/app/routers/live_broker.py` serves `GET /live-broker/order-rules/{underlying}` from `flattrade_symbol.rules_for`.
- `backend/app/live/flattrade_symbol.py` defines NIFTY as NFO and SENSEX as BFO, with lot/freeze/tick and product/order-type lists; its comments say CO/BO are off in v1 and flag that exchange figures need live-broker reverification.
- `backend/app/live/manual_ticket.py` restricts the manual instruction to one-lot BUY/MIS/LIMIT (and its supported limit buffer). The UI continues to permit other app-listed choices for preview.
- This display does not establish that the running backend matches the checkout, that figures are current at the exchange/broker, or that a broker-held exit is active. It makes no claim that BO/CO are exchange-prohibited for NIFTY.

## Focused checks

Command, run from `frontend`:

```powershell
$env:CI='true'; npm test -- --watchAll=false --runInBand --testPathPattern='(LiveOrderTicket|manualTicketCapabilities)\.test\.(jsx|js)' '--moduleNameMapper={"^@/(.*)$":"<rootDir>/src/$1"}'
```

Observed: 2 test suites passed, 6 tests passed. The UI test uses mocked API responses; no HTTP request, broker, database, service operation, build, auth, order, or deployment action was used.

An initial test invocation without the repository's `@/` Jest alias failed to resolve `@/lib/api`; rerunning with the documented `moduleNameMapper` passed. A separate first attempt to supply the same alias via a malformed PowerShell argument was stopped before results; it is superseded by the passing command above.

## Review notes

- Risk if the displayed API figures are mistaken for current broker facts: operator could act on stale lot/freeze/tick settings. Source and build uncertainty are explicit in the UI; revalidation against the actual broker remains outside this proposal.
- Review should confirm the concise panel fits the existing ticket layout and that root's adjacent changes in `LiveOrderTicket.jsx` remain preserved.
