# Audit fixes checkpoint — 2026-10-23

## Current state — 2026-10-24 rollout complete

The operator approved both original safety diffs and the supplemental paper-stop
reporting correction, plus rebuilding after confirming the broker account flat.
The backend and frontend were rebuilt and restarted; MongoDB was not restarted.
All changes below are now in the running images. This replaces the earlier
local-only/pending-approval status; historical checkpoints remain for traceability.

- Confirmed: **443 selected isolated pytest checks passed in 26.34s**, including
  the new production paper-stop function and notification behavior. The earlier
  manual-ticket React component checks also passed. Both Docker builds completed;
  the frontend produced `main.d7598c8d.js`.
- Confirmed: immediately before restart, mode was LIVE_OFFLINE; guarded positions,
  live deployments, open live journal rows and unsubmitted intents were all zero.
  The operator's broker-flat confirmation supplies broker-side evidence; these app
  counts alone are not a broker position-book reconciliation.
- Confirmed: after restart, backend health returns HTTP 200 with `db:ok`, MongoDB
  remains healthy, and the paper/optimizer/deployment/live-cockpit page URLs return
  HTTP 200. The new paper-only POST route is registered in OpenAPI; it was **not
  invoked** against the user's active paper deployments.
- Confirmed: normalized hashes of every changed backend source plus the existing
  guard and kill-switch files match the running container. The served frontend
  source map matches every changed source and new helper; the served bundle carries
  the paper-only endpoint and partial-failure message.
- Confirmed: LIVE_OFFLINE remains selected, with zero guarded positions/live
  deployments/open live rows/unsubmitted intents. Nine paper deployments remain
  active and the order-intent collection remains at 21 rows. The existing global
  autoplace flag is still on; it was not changed and no live deployment was enabled.
- The app now reports the Flattrade session expired. No authentication, order,
  kill-switch or live-enable action was taken. No broker action was used as a test.
- Current images: backend `4677a582afbfb7025170341357462d72c0a82571f1ea449eb9447ac49d12988c`;
  frontend `16030a060d939bde3e10682baa919dc31efa317d90ab93e49047723217f875c0`.
  Prior images remain recorded for recovery; no images, volumes or data were deleted.

Evidence files under `audit-fixes/`: `rollout-preflight-2026-10-24.json`,
`rollout-before-restart-2026-10-24.json`, `rollout-after-restart-2026-10-24.json`,
`rollout-code-parity-2026-10-24.json` and `rollout-previous-images-2026-10-24.json`.
The final pytest selection is the recorded 406-case command below plus
`tests/test_paper_squareoff.py`, `tests/test_kill_switch_halt_reset_contract.py`
and `-p pytest_asyncio.plugin`; the paper-stop test file now has the added reporting
cases. Build: `docker compose build backend frontend`. Restart:
`docker compose up -d --no-build --no-deps backend frontend`.

Remaining: the original unreplicated incidents and execution-parity limits listed
below are still open. No live-money validation or market-session certification is
claimed. No commit or push was performed.

## Authorization and safety boundary

**2026-10-24 approval received:** the operator approved both linked safety diffs
and rebuilding, explicitly confirming the broker account has no open positions or
working orders. This supersedes the pending-approval status in the historical log
below. No order, live enable, kill-switch invocation or broker authentication is
authorized. The primary owns the restart and its checks.

Preflight saved at `audit-fixes/rollout-preflight-2026-10-24.json`: mode LIVE_OFFLINE,
zero live deployments across all statuses, zero open live journal rows, zero guarded
positions, closed-day session. The broker session is connected; the global autoplace
flag is already on but no deployment is live. Nine paper deployments are active.
No mode, deployment or broker state was changed by this check.

The approved paper-only patch is now applied exactly as shown. Its isolated tests
now execute the production route function rather than the proposal text. Testing
and image rollout are in progress; do not yet claim the running services changed.

**Supplemental approval received:** independent review reproduced failed closes
being counted as completed (four failing cases). The operator explicitly approved
`audit-fixes/proposals/paper-stop-reporting.patch`; it is applied. The route now
separates completed/failed/skipped results and the UI warns on incomplete closes.
The final focused run passed **75 tests**, including the actual paper square-off
helper, both concurrent-mode cases, empty/error/mixed responses, frontend message
behavior, existing freeze slicing, latch/reset contracts and guard tick behavior.
The UI fixture initially had an arrow-function object-return syntax error; correcting
that test harness produced the green result. No additional production change was
needed. The agent accepted the supplemental diff and verified Sonner warning support.

Previous image IDs are saved in `audit-fixes/rollout-previous-images-2026-10-24.json`.
The database contains no unsubmitted intents; its existing order-intent rows are
SUBMITTED. Image building is the next step; services have not yet restarted.

The operator authorized fixing the audit findings and cumulative progress saves on 2026-10-23. No real order, order simulation against the broker, live enable/deploy, live kill switch, broker login/logout, or credentials output is authorized. Use isolated fake/recorded inputs only. Closing-range forecasting is excluded.

The earlier explicit requirement remains: show a proposed diff and wait for approval before modifying kill-switch, latch, or deploy-validation code. Prepare protected patches separately; do not apply them. No database/schema changes or unrelated refactors. Do not restart/rebuild the connected stack without proving broker exposure safe and satisfying approval boundaries. Source changes alone do not update the running containers.

## Starting evidence

- HEAD: `ff9cbe3`; running backend image observed as `ba7623a3e45a`.
- Pre-existing edits: `docs/AGENT_TODO.md`, `learning_log.md`; untracked onboarding review and `docs/market-research/2026-10-08-*` files. Preserve them.
- Running guard starvation reproduced in isolated virtual time: continuous ticks, 15 seconds, no book reads or stop decision; checkout performs 10 reads and one stop decision. Guard and whole-lot slicer fixes already exist in checkout but not running image.
- Manual ticket: reachable confirmation displays lots/product/type which approval discards; supervised executor pins one lot/MIS/LIMIT. No order handler invoked.
- Paper Stop ALL label calls global route which also exits live deployments. Never invoked.
- Saved-run deployment prefill drops costs and DTE filters. Actual helper reproduction and running bundle parity checked during audit.
- Current default option-sale STT is 0.10%; official NSE schedule is 0.15% from 2026-04-01. Preserve explicitly recorded rates; do not rewrite old runs.
- CSV parity passed four sampled runs. Exact latch sequence and same-config optimizer regression not reproduced; do not invent fixes.

## Work packages and ownership

| ID | Owner | Task / done check | State |
|---|---|---|---|
| S1 | Root | Manual ticket preserves existing one-lot BUY/MIS/LIMIT capability; canonical confirmation and resolved order binding | CHECKED; IN RUNNING IMAGE |
| S2 | Root | Existing guard and whole-lot slicing checks pass; approved kill-switch rollout diff recorded | APPROVED; IN RUNNING IMAGE |
| S3 | Root | Paper-only Stop ALL plus accurate completion reporting; isolation and concurrent live-transition tests pass | APPROVED; IN RUNNING IMAGE |
| B1 | backtest_truth + Root | Full-detail source load, execution transfer and truthful preview; root checked four actual saved records | CHECKED; IN RUNNING IMAGE |
| C1 | optimizer_truth + Root | Dated default STT with explicit saved-rate preservation and exit-date forwarding | CHECKED; IN RUNNING IMAGE |
| O1 | architecture_map + Root | Revised conservative bounds audit + indicator/premium-native clarity; real-backend cross-checks | CHECKED; IN RUNNING IMAGE |
| V1 | Root | Review patches, integration checks, build, rollout, code parity and documentation | COMPLETE WITH STATED VALIDATION LIMITS |

Agents own their task files and a separate checkpoint under `docs/audit-fixes/`; only root updates this file, AGENT_TODO, HANDOFF, CHANGELOG and learning_log. Tests must not connect to Flattrade or mutate the running database. No broad test suite until its external side effects have been screened.

Delegation briefs and stopping rule: each agent had one reproduction pass, one narrow
implementation/check pass and one root review; O1 received a revision pass when review
found overconfident claims. Source and evidence were checkpointed before handoff.

- B1 / Sol: reproduce saved-run policy loss; fix full-detail loading and effective-policy transfer; test stale responses and request parity; leave validation algorithms and running deployments alone.
- C1 / Sol: verify the dated statutory source; preserve explicit saved rates; pass the exit timestamp through existing cost consumers; prove date boundaries without changing signals or stored records.
- O1 / Luna: explain toggle/bounds semantics using existing metadata; do not change search behavior or claim active bounds without evidence; cross-check the helper against the backend.
- Root: retain manual execution safety and protected proposals; independently review delegated diffs and their consumers; stop when local checks pass and further work requires protected approval or unobserved production evidence.

## Resume instructions

Read this file, individual agent checkpoints, then `git diff --stat`. Do not discard existing edits. Re-check agent state before duplicating work. Confirm actual code and recorded outputs before marking any task done. Finish non-protected work while protected approval remains pending. Never present local fixes as running fixes.

## Progress log

- Initial checkpoint saved before implementation and delegation. Re-read handoff, work board, integrity audit, repository instructions and changelog. No running service changes.
- S1 reproduced nine unsupported-ticket failures before edits. Local policy now refuses unsupported tickets before approval/broker reads and rechecks older approvals. MARKET removed from API capability tables. Independent Sol/Luna review found raw confirmation/default mismatches and resolved-symbol/quantity/price drift; root corrected confirmation rendering and added comparison against approved execution fields before broker methods. Targeted S1+B1+O1+C1 tests: 52 passed at the latest resume checkpoint; further combined/build review pending.
- S2 existing guard, whole-lot builder and related focused checks passed as part of a 126-test isolated run. No guard/kill/latch implementation edited. Saved actual running-to-checkout kill-switch diff at `audit-fixes/proposals/existing-kill-switch-rollout.patch`; applying it to the running service requires approval and safe rollout.
- S3 paper-only Stop ALL change is an UNAPPLIED patch at `audit-fixes/proposals/paper-only-stop-all.patch`. Two isolated proposal tests pass, including a deployment changing to live between read and update. Plain `git apply --check` encountered the repository's mixed line endings; `git apply --ignore-space-change --check` passes. Production global-stop path and paper button remain unchanged pending approval.
- B1 agent saved source/helper/tests; interrupted before final 49-test combined check. Root ran new cross-package checks (52 passed), continues independent source/real-record review. Do not treat interrupted agent status as final validation.
- C1 dated STT source changes and six narrow callers are local; agent reports 101 focused tests. Explicit rates retained; no historical DB records rewritten. Root reviewed core schedule and gross/net policy; combined rerun pending.
- O1 initial helper was rejected in review for overclaiming effective bounds. Revised UI uses existing indicator catalog, labels unverified overrides conservatively and notes premium-native forced-OFF behavior. Six helper/backend cross-check tests reported green; root integration check included them.
- Two agents reached usage limits. Their checkpoints and source edits survived; root continues remaining work. No service restart/rebuild, live enable, order operation, broker authentication, or production database write performed.

- Integration checkpoint: 401 isolated pytest tests passed (20.31s), including manual contract, proposal safety, existing guard/slicing, saved-run actions/transfer, optimizer bounds/seeds and cost consumers. `CI=true npm run build` completed successfully; only existing bundle-size advisory and Node deprecation notice. `git diff --check` passed. These are host/local-source checks, not a running-stack deployment.
- Read-only recorded-data check: all four audit sample IDs loaded through the actual new transfer helper. Sparse run `26195a4f` retains costs=true, DTE=[2,3,4,5], 5 lots and 09:25–14:45; both premium samples retain costs=true, unbounded DTE, 5 lots and 09:31–14:50. No data written.
- Approval requested asynchronously for the linked protected diffs, with separate choices to keep the running stack untouched or approve rebuilding only after the operator confirms no broker positions/working orders. NO ANSWER IS NOT APPROVAL. Keep pending until a user reply.

## Final local review checkpoint

- Confirmed: the final combined isolated pytest run returned **406 passed in 20.11s**. The five additional cases reject missing/malformed approved-order snapshots before execution; they close a path where a null snapshot could disable comparison. No application runtime, order transport or production collection was used by these checks.
- Confirmed: the actual React ticket component passed **3 JSDOM tests**: normalized confirmation, changed resolved quantity and fractional/stale lots. The API module is replaced entirely; mode arming and approval redemption are forbidden-call stubs. Tests stop before final confirmation.
- Confirmed: `CI=true npm run build` passed for the final production frontend sources. The later additions were the UI test file and the backend snapshot check. `git diff --check` passed after integration; existing line-ending notices are not failures.
- Root reviewed Sol's B1/C1 changes and Luna's O1 revision. O1's first claim that bounds were active was rejected; the accepted revision reports saved overrides and the completed job's effective space. Independent S1 review identified normalization and resolution drift; the final source and regression tests address both.
- No commit, push, backend restart, image rebuild, deployment, broker request or database write was performed. Files and these checkpoints are the cumulative save.

Exact final pytest selection (set `PYTHONDONTWRITEBYTECODE=1` and `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`):

```powershell
.venv\Scripts\python.exe -B -m pytest tests/test_manual_ticket_contract.py tests/test_paper_stop_proposal.py tests/test_live_validate_and_build.py tests/test_live_order_builder.py tests/test_freeze_lot_slicing.py tests/test_live_guard_tick_primary.py tests/test_live_cockpit_actions.py tests/test_live_cockpit_ui.py tests/test_backtest_deploy_transfer.py tests/test_backtest_lab_action_buttons.py tests/test_premium_native_result_surfacing.py tests/test_optimizer_override_audit.py tests/test_optimizer_bounds_instrument_guard.py tests/test_bounds_unit.py tests/test_incumbent_seed.py tests/test_option_costs_dated_stt.py tests/test_option_costs.py tests/test_live_friction.py tests/test_live_charges_journaled.py tests/test_premium_momentum.py tests/test_paper_pnl_basis_parity.py tests/test_live_symbol_space_and_segment.py -q -p no:cacheprovider --tb=short
```

UI command, from `frontend` with `CI=true`:

```powershell
npm test -- --watchAll=false --runInBand --testPathPattern=LiveOrderTicket.test.jsx '--moduleNameMapper={"^@/(.*)$":"<rootDir>/src/$1"}'
```

## Original reports: closure limits

1. **Latch sequence:** not reproduced end to end; no latch change. Isolated state/reset checks are evidence about those functions, not a live-session certification.
2. **Saved-run actions:** Trades pane/CSV agree in the four sampled saved runs; no CSV fix claimed. The separately reproduced deployment-prefill loss is fixed locally. Full paper-vs-backtest execution parity is not established by prefill alone: strategy exit hints can still take precedence over fallback option levels.
3. **Confluence regression:** identical-config old-preset/new-job replay remains outstanding. No optimizer ranking or strategy changes made.
4. **Explosive Reversal disparity:** stored performance and absolute-point geometry do not prove a code defect. No price-scaling or strategy change made.
5. **Search controls:** existing backend toggle/bounds behavior was demonstrated; UI now describes declared periods, catalog expansion and unverified overrides accurately. No new optimizer job was run.
6. **Deployment bounds:** existing advisory-range/acknowledgment behavior passed isolated checks. The reported final-wizard block was not reproduced; parameter feasibility/range validation was not changed.

## Valuable next additions (proposals only)

1. **Visible running-build identity.** Show backend/frontend revision and whether they match the checked-out source. This directly addresses the observed gap between corrected source and an older running guard.
2. **Compare a preset and optimizer winner on identical inputs.** Display data interval, effective costs/sizing, searched bounds and option-rerank inclusion side by side before interpreting a profit difference. This makes the unresolved Confluence comparison testable.
3. **Execution receipt with fill reconciliation.** Keep approved symbol/quantity/limit alongside broker-confirmed filled quantity, average price and the active exit protection; mark estimated journal values explicitly. This targets the existing partial-fill/P&L gap.
4. **NIFTY/SENSEX capability display.** Derive ticket choices and exit support from the same exchange/broker capability source, including unsupported BO/CO cases, instead of implying that every displayed preview can execute. Actual broker capability changes need current evidence and separate authorization.

These are product proposals grounded in this audit, not implemented features or
claims of improved profitability. Resolve the current rollout and truthfulness gaps
before adding more strategies or automatic execution authority.

## Risks and next authorized action

- **Critical: isolated checks and code parity do not certify live execution.** The corrected guard, slicer and paper-only route are now running, but no live order or flatten was used to test them. Any live validation remains operator-controlled under the standing constraints.
- Approval and rollout are complete for the recorded diffs. No further protected changes are authorized merely by this rollout; keep the normal shown-diff approval boundary for future work.
- The app reports an expired broker session. The operator owns broker authentication; no agent login/logout or token output is permitted.
- Existing partial-fill realized-P&L and statutory exchange-fee schedule work remain separate open items. This change fixes dated STT only; it neither certifies all fees nor rewrites old records.
- Next: reproduce a remaining owner report using identical recorded inputs, or follow the next requested improvement. Do not relabel unresolved reports as fixed because the rollout passed.
