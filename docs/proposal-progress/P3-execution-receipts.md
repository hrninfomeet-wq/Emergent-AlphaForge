# P3 — read-only execution receipts

## Current status

Implemented, rebuilt and reviewed in Chrome. Primary reviewed production
writers (`idempotency`, `order_sm`, `close_loop`, `runtime`, `approval_store`) and
derived the receipt from existing records. No persisted schema or order path was
changed. This does not claim a fresh broker fill reconciliation or repair the
existing close-loop partial-fill accounting gap.

## Evidence and implementation

- Isolated production `realized_fields` reproduction: ordered 100, reference
  entry 100 and exit 110 produces gross 1000; actual supplied entry 102 and
  quantity 60 produces 480. Difference 520. No user's trade was simulated.
- New pure `execution_receipts.py` allowlists instruction, fill and journal
  evidence. It separates recorded state from fill evidence, rejects malformed
  quantities/prices and contradictory order flags, and refuses a diagnostic P&L
  for duplicate journal rows, conflicting contracts, open journals or SELLs.
- Its diagnostic calculation uses entry fills plus the journal exit and is
  explicitly an estimate, never independently verified realized P&L. Existing
  journal/statistics/risk-cap calculations remain untouched.
- GET `/api/live-broker/execution-receipts` reads existing DB rows and the
  in-memory guard registry. It performs no broker request or write. Pagination
  defaults to 50 and is capped at 100 per request.
- Live Broker → account Trade book tab includes receipts for stored instructions,
  including manual/mock/unsubmitted records with mode/state labels. Refresh is
  on-demand; failed refresh clears old evidence. Guard registration is not
  represented as a healthy monitor or confirmed broker-held exit.
- Approval records are in-memory and are not durably linked into the order store.
  The UI explicitly says approval snapshot unavailable; it does not invent one.
- Historical missing order fills stay unavailable. No historical rows were
  changed, no new broker integration or order action was added.

## Checks and independent verification

Primary re-derived the critical estimate independently: 60 × (110 − 102) = 480;
480 − 1000 = −520. Production reproduction above and the new pure receipt agree.
The isolated tests exercise invalid/bool/non-finite/fractional/zero/overfilled
quantities, missing averages, foreign/duplicate rows, entry basis, contracts,
protection uncertainty, secret-field omission, pagination and GET route purity.

Latest root Python selection: `test_execution_receipts.py`,
`test_build_identity.py`, `test_live_trade_stats_route.py`: **45 passed**.
Root React selection across all four proposals: **25 passed in six suites**.
Root research Node fixtures: **9 passed**. All are offline/mocked; no broker
client or production DB exists in these tests. `git diff --check` passes.

React command uses `node node_modules/@craco/craco/dist/bin/craco.js test`
from frontend, `--watchAll=false --runInBand`, a pattern for ExecutionReceipts,
ResearchComparison, BuildIdentity, buildIdentity, LiveOrderTicket and
manualTicketCapabilities, and `--moduleNameMapper={"^@/(.*)$":"<rootDir>/src/$1"}`.
Invoke with argument arrays to node.exe; npm.cmd can reinterpret a pipe in the
test regex as a shell pipeline on Windows.

Chrome caught a false conflict from comparing the Upstox display symbol with the
Noren symbol. `auto_live.py` stores `noren_tsym` separately; the receipt now uses
that field. The added alias fixture passes, final API reports no false contract
conflicts in the 21 existing records, and the corrected expanded receipt was
reviewed in Chrome. Those records have no stored order-specific fill fields.

## Risks / remaining work

Critical limitation: stored cumulative fill quantity/average can be stale. If
treated as the current broker book, an operator could mistake partial or missing
fills for present exposure. The screen says freshness unverified. A complete
durable approval/fill/exit audit would require a separately approved persistence
design; no schema change was smuggled into this read-only proposal.

Core lesson: submitted instruction, fill evidence, journal estimate and active
protection are distinct facts. Confirmed approach: expose provenance and refuse
unsupported arithmetic. Dead end: using order quantity or contract-wide average
as proof of this entry's fills.
