# Repository publication checkpoint — 2026-11-19

Scope: operator authorized all outstanding project source, tests and supporting
records on local main and GitHub. No app restart, broker login or order action.
Earlier documents are historical checkpoints; PROGRESS.md and HANDOFF.md identify
the latest chart deployment. No new implementation is claimed for consolidated work.

## Verification performed

- Confirmed: selected Python run: **410 passed, 8 failed**. Use repository `.venv`,
  set `PYTHONPATH` to the absolute backend directory, then run:

```text
python -m pytest tests/test_build_identity.py tests/test_execution_receipts.py tests/test_manual_ticket_contract.py tests/test_receipt_capture.py tests/test_receipt_events.py tests/test_receipt_evidence.py tests/test_receipt_hooks.py tests/test_backtest_deploy_transfer.py tests/test_optimizer_override_audit.py tests/test_option_costs_dated_stt.py tests/test_paper_stop_proposal.py tests/test_live_order_page_routes.py tests/test_live_validate_and_build.py tests/test_option_costs.py tests/test_paper_pnl_basis_parity.py tests/test_live_order_builder.py tests/test_live_executor.py tests/test_live_idempotency.py tests/test_live_close_loop.py -q -p no:cacheprovider
```

- Confirmed: build-identity test expects two root build contexts; compose now has
  three including conditional-watchdog. Seven paper-stop fixture cases fail after
  the function's conditional-order integration: the isolated fixture does not
  substitute that dependency; a focused rerun reports `Event loop is closed` in
  the conditional-orders failure entry. These failures were preserved, not fixed
  or waived by a documentation/publication request. The entire suite was not run.
- Confirmed: **47 React tests across seven suites passed**: BuildIdentity,
  ResearchComparison, ExecutionReceipts, LiveOrderTicket, manualTicketCapabilities,
  buildIdentity and ChartTerminal. CRACO/Jest requires the CLI moduleNameMapper
  JSON `{"^@/(.*)$":"<rootDir>/src/$1"}`; webpack's alias alone is insufficient.
  Run with `--watchAll=false --runInBand --runTestsByPath` and those suite paths.
- Confirmed: **34 Node tests passed** using `node --test` on
  `tests/js/{backtestDeploySource,receiptBrokerEvidence,researchComparison}.test.mjs`
  (expand these three paths on shells without brace expansion).
- Confirmed: live backend modules compiled during independent source review.
  Existing live acceptance gaps remain in quick-trade-audit/REPORT.md: original
  exit-plan recovery, broker-held protection and reference-price exit anchoring.
  No real-money test or new execution-safety certification was performed.

## Publication hygiene

- Credential patterns scanned current project candidates and outgoing history;
  no token/private-key finding in outgoing history. Current matches were public
  API-reference examples and an explicitly redacted audit note. A separate exact
  comparison against local environment credential values found no candidate match.
  These checks are bounded scans, not a guarantee of the absence of every secret.
- Keep `.env`, tokens, node_modules, build output, logs and local memory ignored.
  Historical Markdown may reference ignored local logs/screenshots; those artifacts
  are not included. Tests and recorded conclusions remain available.
- The frontend build-manifest starter reports unknown provenance. Docker and the
  launcher generate actual build metadata; an old local build fingerprint is not
  published as a new release identity.
- Preserve all existing source content; normalize learning_log.md line endings to
  avoid a whole-file newline diff. No remote force push or history rewrite.

Final gate: fetch and push main normally, then compare remote/main and local/main
commit IDs and confirm a clean worktree. If rejected by newer remote work, preserve
both histories and reconcile before retrying.

## Publication result

Confirmed: consolidation commit `7f5e05a` was committed locally and pushed to
GitHub main without force. `git ls-remote origin refs/heads/main` matched local
main's full commit ID. Worktree was clean before saving this completion note.
The running app was not rebuilt. Python failures above remain open. Historical
patch/diff snapshots retain their original context whitespace, and source CSVs
retain trailing blank rows; the whitespace check reports these evidence files.
