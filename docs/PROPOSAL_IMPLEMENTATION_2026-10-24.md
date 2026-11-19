# Proposal implementation — 2026-10-24

## Latest outcome — resumed receipt comparison is running

User resumed after usage reset and requested efficient completion. Primary found
that LiveDataProvider already exposes the order-book response, error and last
successful read time. The receipt can compare that existing snapshot without
additional broker calls or persisted schema changes.

- Primary owns the pure `receiptBrokerEvidence` helper and cockpit prop wiring.
  Eight Node fixtures pass: own partial fills, exact identity, duplicate/foreign
  rows, malformed/overfilled quantities, zero versus missing, stale/error/time
  boundaries, order instruction drift and estimated-exit limits.
- Luna completed receipt presentation and mocked React checks; root reviewed the
  code, corrected two vacuous old-label assertions and added the between-ticks
  freshness regression. Final selection: **29 React checks**, **17 Node fixtures**.
  The prior **45 Python checks** remain applicable; backend logic was unchanged.
  Fresh book evidence is
  separate from stored evidence; local clock redraws expire the comparison, not
  make network requests. Build/restart and Chrome review are complete.
- Current source SHA-256:
  `c8372540b14f5e5d784bd8dcd36b647fb1a3983ef57dac783d259555b6415125`;
  frontend `main.a72bbe9b.js`. Both running identities and captured checkout scope
  match; four changed served frontend sources match. Evidence:
  `proposal-progress/rollout-receipt-book.json` and
  `build-receipt-book-2026-10-24.log`.
- Chrome observed old FE/new BE mismatch before refresh, then matching identities
  after refresh. The expanded historical receipt reads `not in book`, not zero
  fills, using the cockpit's existing broker response. The broker is now shown
  connected (supersedes the earlier expired-session observation). No auth action
  was performed by the agent.
- Latest app checks: LIVE_OFFLINE, zero guards/live deployments/open live journal
  rows/unsubmitted intents. Protected/runtime/router sources unchanged from the
  running baseline. MongoDB was not restarted; no trading action was used.
- Durable historical approval/fill/exit persistence remains outside this
  schema-preserving extension. A concrete additive schema is ready for the
  operator's required approval:
  `proposal-progress/DURABLE_RECEIPT_SCHEMA_PROPOSAL.md` (not applied).

## Earlier outcome — first feature rollout and Chrome review

The four additive surfaces are running. Final evidence:
`proposal-progress/rollout-verification.json`, `research-browser-sample.json`,
the per-piece checkpoints and `build-final-2026-10-24.log`.

- Confirmed: final **45 Python checks**, **25 React checks in six suites** and
  **nine Node comparison fixtures** pass. Both final Docker builds completed.
- Confirmed: frontend bundle `main.923d57cd.js`, backend metadata and the captured
  checkout scope share SHA-256
  `542496391be03d4a31d30a3b6755b2adc689b27c8675f92fe6618e8661562541`.
  Ten served frontend source-map entries and four backend files match source.
- Confirmed: Chrome shows matching build identities, the NIFTY/SENSEX capability
  panel, receipts in Live Broker → Trade book, and the Backtest Lab comparison.
  The tab is left on the sampled Confluence comparison, marked Settings mismatch.
- Confirmed against saved full records: the sampled run and optimizer winner
  have different intervals, entry windows, spread costs and exit policies. This
  verifies the comparison, not the owner's identical-input regression report.
- Confirmed: receipt API returns 21 existing instructions; all 21 lack stored
  order-specific fill fields. Unknown is displayed explicitly. Approval snapshots
  are also unavailable in those order records. No fresh broker reconciliation,
  reconstructed approval history or partial-fill journal repair is claimed.
- Chrome review caught a display-symbol versus Noren-symbol mismatch warning.
  Corrected the read-only receipt to compare `noren_tsym`, pinned both conflict
  and display-alias cases, rebuilt, and verified the false warning is gone.
- Final state: LIVE_OFFLINE, zero guarded positions, live deployments, open live
  journal rows and unsubmitted intents. The nine ACTIVE paper deployments and
  21 order-intent rows remain. MongoDB was not restarted. No orders, live enable,
  kill-switch or authentication actions were used for validation.
- Protected existing code was compared to the running baseline before rollout:
  kill-switch, guard, runtime and deployments match; existing broker-router
  functions match. Only the new read-only receipt route was added there.
- No commit or push. Unrelated and pre-existing local edits preserved.

### Deliberate limits / still open

Build identity compares captured source, not resolved dependencies, runtime
configuration, mounted plugins or a continuously monitored host checkout. Root
verified this checkout at rollout; the UI does not pretend to monitor it later.

Research comparison reads saved evidence; it does not rerun candidates on a
frozen dataset. Dataset identity remains unknown without stored fingerprints.

The execution receipt is an evidence display with a diagnostic entry-fill versus
journal comparison where existing records support it. **Full durable approval,
fill and exit reconciliation remains incomplete.** Adding missing persistence
requires the user's separate schema-change approval. Existing partial-fill P&L
accounting and original unreproduced incidents remain on the work board.

Capabilities describe the app's current restrictions, not newly certified broker
permissions. The Flattrade session remains expired; no live execution is certified.

User authorized implementing the remaining proposals and identified the Chrome
tab at `http://localhost:3000/`. This is an additive feature pass; it does not close
unreproduced incidents listed in `AUDIT_FIX_CHECKPOINT_2026-10-23.md`.

## Pieces and checks

1. **P1 running build identity** — Sol owns build metadata, Docker wiring and
   Layout. Done when independently built images identify their captured source
   and the UI distinguishes matching, mismatched and unavailable evidence.
2. **P2 research comparison** — Sol owns pure comparison helpers and Backtest Lab.
   Done when saved run/preset and full optimization winner compare effective
   inputs, keep missing evidence unknown and use the correct result envelope.
3. **P3 execution receipts** — primary owns this critical, money-decision piece.
   Done when stored instructions, order-specific fill evidence and journal
   estimates are separated, contradictions visible and no order/guard/risk
   policy changes occur. Independent review and isolated boundary tests required.
4. **P4 capability display** — Luna owns manual ticket presentation. Done when
   NIFTY/SENSEX app-supported choices and actual manual execution restrictions
   are visible without claiming broker/exchange permission from defaults.
5. **Integration** — primary reviews agent diffs, runs focused tests, builds,
   checks safe restart state and validates the served UI in Chrome. Done only
   after recording build/source parity and limits. No broker action as a test.

P3 has the highest cost of an incorrect result: it could drive a real-money
decision. P1 and P2 also drive decisions; P4 is informational and cannot change
execution permission. Agent details are saved in `proposal-progress/P*.md`.

## Earlier checkpoint (before integration build)

- P1 implementation and initial tests ready; agent checking polling/legacy cases.
- P2 pure comparison fixtures pass; integration and review pending.
- P4 implementation and six focused frontend tests ready for primary review.
- P3 source review confirms live journal closes can use ordered quantity and a
  mark exit. Isolated reproduction: ordered 100 at reference 100, exit 110 gives
  gross 1000; proven 60 at 102 gives 480, a 520 difference. These are synthetic
  inputs, not the user's trades. Production `realized_fields` was executed without
  DB, network or broker access. Receipt must not bless the former as reconciled.
- Existing `live_orders` retain submitted intent and optional cumulative
  `fillshares`/`avgprc`; approval queue is in-memory and not a durable approval
  audit. Missing approval/fill evidence must stay explicitly unavailable.
- No new feature build/restart yet. The earlier approved safety-fix rollout is
  already running; its evidence remains in the audit checkpoint.

## Boundaries

No real orders, live deployment, kill-switch action, authentication or secret
output. No strategy changes, schema migration, historical rewrite or risk-cap
calculation changes. Any new protected kill/latch/deploy-validation diff requires
separate review and user approval. Existing approval is not extended silently.

## Review/stop rule

Review each agent's actual diff and tests once it is ready; request corrections
for evidence or behavior failures. Stop this feature pass when the focused checks
and served UI checks pass, or record a concrete blocker. Do not invent missing
historical evidence or turn this into unrelated strategy work.

## Durable receipt storage — approved implementation in progress

The operator approved DURABLE_RECEIPT_SCHEMA_PROPOSAL.md. Primary owns capture integration and verification; Sol owns the allowlisted append-only store and Luna the evidence display.

1. Critical: capture must not interrupt execution (moves money if wrong). Done when failure injection and control-flow review show no new broker action or changed order outcome. Capture is bounded and scheduled outside the execution wait path.
2. Store and read durable events (drives decisions). Done when fake-store duplicate/restart/identity/missing-data checks pass and API serves bounded sanitized records.
3. UI displays evidence limits (drives decisions). Done when mocked rendering checks cover missing, conflicting and degraded capture.
4. Build, verify and save progress (costs redone work). Done when running source identities match, read-only Chrome checks pass, and this checkpoint is updated. No live tests or historical backfill.

## 2026-10-25 durable-storage checkpoint — implementation verified, rollout pending

Operator approval is recorded in `proposal-progress/DURABLE_RECEIPT_SCHEMA_PROPOSAL.md`.
Implemented the single additive `execution_receipt_events` collection, with a unique event key and append-only inserts. No existing journal/result/schema was rewritten.

- Primary implemented capture hooks, bounded reads, identity/conflict checks and hook integration tests. Sol implemented/reviewed the storage helper and capture tests. Luna implemented the separate persisted-evidence display; primary reviewed it and corrected the pending-count API contract.
- Approval capture retains only the approved instruction and approval ID. After the existing placement helper returns, a separate linked capture uses the actual returned client/order IDs. This never treats approval as a fill.
- ACK capture is scheduled only after the existing intent-store submission write. Capture does not await DB work in the executor/protection path. Queue size and task/write deadlines are bounded; failures and dropped captures are visible in process-local telemetry.
- Existing cockpit order/trade-book reads are observed without new broker polling. Exact tracked live order identity is required. Exit evidence requires a unique explicit OCO entry tag, matching contract/product/side and a broker fill ID; missing/contradictory facts remain flagged. There is no generic same-symbol/manual-exit inference.
- Events are cumulative observations, never summed. The bounded reader revalidates stored fields and hashes, refuses corrupt payloads, flags contradictory same-time/same-fill observations, and reports missing or truncated history. It does not replace journal P&L or risk-cap inputs.
- Read/display telemetry is explicitly current-process best effort. Only acknowledged writes survive restart. A crash can lose queued captures; no history-completeness guarantee is made. Book capture currently depends on existing cockpit reads, not an added continuous recorder. Full exit reconciliation and partial-fill accounting remain open.

Checks completed this session:

- `proposal-progress/durable-tests-python.log`: 320 selected Python checks pass, including new store/capture/read/hook tests and existing idempotency, approval, manual ticket, L3 route, stats and build identity checks. Dependency/Pydantic deprecation warnings remain.
- 36 React checks across six focused suites pass; 17 Node checks pass.
- `proposal-progress/durable-control-flow-review.json`: every router function except orders/trades/approve is AST-identical to the pre-storage baseline. Removing only the additive receipt hooks makes those three function bodies identical too. No new kill/latch/deployment-validation code change.
- `proposal-progress/durable-integration.diff` records the existing-file integration diff for review. New modules: `receipt_events.py`, `receipt_capture.py`, `receipt_evidence.py`.
- Review found oversized exit quantities and malformed statuses; fixtures reproduced them, fixes are in place, and boundaries pass.
- Broader tests exposed stale manual-order tests expecting MARKET and mocking the superseded placement entry point. Updated those tests to the already-approved LIMIT-only behavior and exact approved-entry forwarding, with fail-fast DB/default-placement guards. The initial stale harness attempted a database connection and failed before the execution mode gate; no real broker order was sent. New capture tests use fake collections exclusively.

Not done: Docker image build/restart, live Mongo index verification and Chrome smoke check for durable storage. Docker Desktop has no running daemon, ports 3000/8001 are unavailable, and Mongo state cannot be inspected (`proposal-progress/before-durable-build.json`). The earlier running-image evidence is historical, not verification of this new source. User was asked to start Docker and confirm the broker remains flat before rollout. Local frontend production compilation passed (exit 0); `proposal-progress/durable-local-frontend-build.log` records its output. `durable-local-build-verification.json` confirms the compiled bundle contains the current shared source fingerprint. This is local-build evidence, not a running-container claim.

Resume: once Docker is available, inspect mode/guard/live deployments/open live journal/unsubmitted intents and broker flat confirmation; build both images from the current shared source fingerprint; restart backend/frontend only, leaving Mongo running; verify collection unique index, read-only receipt API and Chrome build identity. Do not generate live orders or fake production receipt rows to test storage. Original unreproduced incidents remain open.

## 2026-10-25 durable-storage rollout — complete

The operator started Docker and explicitly reconfirmed no open broker positions or working orders, then confirmed Flattrade and Upstox connected. Fresh checks before restart showed LIVE_OFFLINE, zero guarded positions, live deployments, open live journal rows and unsubmitted intents. The software exit guard remains enabled as before; no guard/latch/control was changed.

Both images built and backend/frontend alone were recreated with `docker compose up -d --no-build --no-deps backend frontend`. Mongo container ID and start time were unchanged. The new collection exists with unique `event_key_1`. No fake events or historical backfill were inserted.

Confirmed running evidence: `proposal-progress/durable-rollout-verification.json`, build log `durable-docker-build.log`, pre-restart state `before-durable-restart.json`, and `durable-protected-source-parity.json`. Shared source fingerprint: `fbe29b83e58ad4a1bdd831858b3bd596581de0e0e09a5c4d4f5349513b5a870c`; served bundle `/static/js/main.1a4d4787.js`. The running backend's receipt/integration files match the checkout, and both displayed build identities match this source fingerprint.

The user's current tab was in Edge (Chrome was not running). Read-only browser check after reload confirmed both connections, Trade book > Execution receipts, process-local capture status and expanded persisted evidence. The existing 21 instructions are readable; zero durable events are stored, as expected without new qualifying observations. The historical sample correctly says approval/fills unknown and no persisted events. This does not demonstrate a real fill capture or certify historical trades; fake-store tests cover write/restart behavior and no live test was performed.

This completes the approved additive storage rollout. Full exit-fill reconciliation/partial-fill journal accounting, original unreproduced reports and owner-led market validation remain open. Capture remains best effort on existing cockpit reads; a crash before write acknowledgment can lose pending evidence. No real orders, live deployments, broker authentication or new protected-control changes were performed.
