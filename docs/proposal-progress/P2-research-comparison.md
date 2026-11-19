# P2 — Saved research comparison

> Final integration update: root reviewed this implementation, rebuilt both images, verified served source parity and checked the feature in Chrome. See `../PROPOSAL_IMPLEMENTATION_2026-10-24.md` and `rollout-verification.json`. Earlier local-only/build-pending notes below are retained as historical checkpoints.

Deliverable: a read-only Backtest Lab workflow comparing a saved preset/run with a saved optimizer winner, for interpreting performance differences. Done when effective-setting, ordinary/premium envelope and stale-selection fixtures pass.

## Pieces and checks

1. Critical — effective-input extraction (drives an operator decision): read full backtest results rather than optimizer requests. Done when ordinary requested/effective disagreement and premium-native fixtures select the real policies, and missing fields remain unknown. Independent check: compare extraction against runtime.py, option_backtest.py and premium_trigger_dispatch.py writers.
2. Pure comparison (costs redone work): shared-setting mismatches are separate from searched parameter differences; dates alone never certify dataset identity. Done when same/different/missing fixtures pass.
3. Read-only UI (costs redone work): select saved source and job, load full detail and linked winner, show settings/params/bounds/seeds/rerank evidence. Done when cancellation suppresses old successes/errors and component fixtures pass.
4. Review/checkpoint (costs redone work): targeted tests, inspect diff, hand off to root. Done when commands/results/limitations are recorded here. Root owns browser/recorded-data validation and shared learning log.

Scope: no API edits, optimizer/strategy changes, services, HTTP/broker calls, job submission, DB writes, commits or pushes. Preserve all pre-existing dirty files. Only BacktestLab integration plus new helper/component/tests.

## Evidence and progress

- Read HANDOFF, work board, integrity audit, audit-fix checkpoint and repository AGENTS.md.
- Confirmed: job history omits param_space/rerank; full getOptJob is required. best_backtest_run_id links to full getBacktestRun. Preset list already returns full configs (research router).
- Confirmed: incumbent_seeds records enqueued (possibly partial/dropped) parameters; it does not prove execution. rerank.ranked is truncated to a saved slice, so absence does not prove exclusion (optimizer.py writers).
- Primary continuation: helper, component and Backtest Lab integration reviewed.
  The agent reached its usage limit after saving implementation/tests. Root ran
  all nine Node comparison fixtures and all four React component fixtures; they
  pass. Cross-checks against premium dispatch's saved sizing, costs, engine params
  and the existing backtest-metrics envelope agree. Build/UI checks pending.

## Risks

- Critical: absent resolved policy or dataset/source fingerprints cannot prove identical evaluation inputs; if treated as equal, the operator could attribute a data/cost difference to optimization. Unknown remains explicit.
- A preset has a declared policy, not an executed result; no performance delta is inferred for it.

## Lesson / approaches / dead ends

Core lesson: full saved effective results are evidence; request echoes and missing
metadata are not. Confirmed approach: fetch the full job and linked run, suppress
late selection responses, separate searched params from common policies. Avoid
using equal dates as proof of equal candle data or seed enqueueing as trial proof.
