# Stage 1 Integrity — session handoff (2026-08-01)

> **Purpose:** the authoritative checkpoint for the Stage 1 Integrity session. Read this
> after [`HANDOFF.md`](HANDOFF.md) and before continuing the roadmap. The permanent defect
> register remains [`BACKTEST_INTEGRITY_AUDIT.md`](BACKTEST_INTEGRITY_AUDIT.md); the live
> work queue remains [`AGENT_TODO.md`](AGENT_TODO.md).

## 1. Decision-ready status

Stage 1 Integrity is **complete and published to `origin/main`**. The work closed every
confirmed MED optimizer finding, corrected Stage 1 Dashboard/workflow truth gaps, added
an advisory spot-data preflight, and preserved the user's right to promote any technically
competent finite strategy independently to paper or live. Research qualification remains
advisory; execution competence and live operational/risk checks remain hard boundaries.

Snapshot verified on 2026-08-01 before this documentation checkpoint:

| Area | Verified state |
|---|---|
| Git | Historical pre-publish snapshot: `origin/main` = `aedbf42`, with 14 local commits through `24adedd`; takeover package `9fd22c3` and the completed Stage-1 chain were subsequently pushed to `origin/main`. Verify the exact current head with the commands in §8. |
| Host suite | **4,354 passed, 4 xfailed, 0 failed** (4,358 collected) |
| Container behavioral set | **142 passed, 9 repository-layout tests deselected, 0 failed** |
| Build/runtime | Python compileall and host/Docker frontend builds passed; backend and Mongo healthy; frontend up; `/api/health` returned `{"db":"ok"}` |
| Real-money state | `live_trades` count = **0**; deployments with `mode="live"` = **0** |
| Broker activity in this session | No Flattrade login/logout, order placement/modification/cancellation, or deployment live-mode change |
| Strategy evidence | No strategy has a demonstrated edge; edge hunting remains parked by user decision |

Runtime state and test counts are checkout/time-specific. The next agent must re-run the
commands in §8 rather than copying these values into a new claim.

## 2. What the user decided

The operator may promote a Strategy Library definition, backtest configuration, optimizer
snapshot, or saved preset to paper and later choose live independently. Failed/incomplete
research screens, running optimizer state, zero-parameter strategies, and finite
guardrail/survival failures may warn and require acknowledgment, but do not veto a
technically executable configuration.

The hard boundary is competency and finiteness:

- strategy exists, is not retired, supports the instrument/timeframe, and passes schema;
- exact parameters and optimizer-added indicator metadata validate;
- AI-authored code passes deterministic repeat-smoke evaluation;
- parameters, metrics and emitted signals are recursively finite;
- live enablement still requires the existing broker, mode, drift, capital, margin,
  cutoff, guard and explicit-consent chain.

Do not reinterpret a positive backtest or optimizer winner as authority or evidence of
profitability. Deployment freedom and evidence quality are deliberately separate concepts.

## 3. Eight confirmed MED findings — closure record

| # | What was checked | Resolution and proof |
|---|---|---|
| 14 | Survival failure reasons could include untouched finalists | Already closed by HIGH #18: counts are restricted to evaluated finalists and evaluated/finalist/not-evaluated are separate. Existing regression: `tests/test_optimizer_verified_high_regressions.py`. |
| 17 | Option re-rank ignored cancel/pause/budget | Ordinary and premium re-rank loops use the shared stop callback and retain finite partial rankings. Commit `9865314`; regression in `tests/test_optimizer_medium_integrity.py`. |
| 20 | WFO final analysis ignored controls and overwrote stop state | WFO rechecks before analysis/save/pairing/finalization, preserves completed windows, and cannot rewrite a stop as `done`. Commit `1c3c5b8`; MED regression file. |
| 23 | Pinned dimensions could detach params from metrics | Already closed by HIGH #28: promotion carries the exact completed params+metrics tuple. Existing HIGH regression file. |
| 25 | Robustness denominator included no-op/duplicate perturbations | Clamped/rounded no-ops and duplicates are excluded; denominator is distinct effective trials. Commit `f7f0546`; MED regression file. |
| 26 | Negative maximizing objectives failed degradation math | Tolerance is symmetric around positive or negative baselines. Commit `5bf5582`; MED regression file. |
| 29 | Drawdown objective mixed incompatible units/scales | Uses `abs(max_drawdown) / sum(abs(trade_pnl))` in one consistent unit; serial, fork workers, WFO persistence and resume carry the denominator. Commits `9184a06` and `675d4fd`; MED plus parallel parity regressions. |
| 30 | Early-stop evidence reported the ceiling as completed trials | Persisted/UI evidence distinguishes actual completed trials, requested ceiling and auto-stop cause. Commit `2c31cc9`; MED regression file. |

Deep analysis found one adjacent defect during the first full-suite run: the serial path
emitted the new drawdown denominator while ordinary and WFO fork workers dropped it. That
would make optimizer rankings depend on execution mode. Commit `675d4fd` closes the parity
gap and pins exact serial/worker metrics.

Disputed LOW #31 remains explicitly separate. It was not silently promoted into Stage 1;
see the audit register before investigating it.

## 4. Other Stage 1 work completed

### Dashboard truth and performance — `502871a`

- `/api/dashboard/summary` now uses an inclusion projection, excluding large root and
  nested trades/equity/portfolio arrays.
- Latest Backtest KPIs use the shared dispatch-aware selector: ordinary results read
  `result.metrics`; premium-native results read `result.option_backtest.*`.
- Runtime payload measured **1,857 bytes in 348 ms**, versus the pre-fix **62,924 bytes in
  3,087 ms**. This is one measured local sample, not a performance SLA.
- Stale phase/roadmap/yfinance-only copy was removed; rupee versus point labels follow the
  authoritative envelope.

### Actionable operator guidance — `3d9f4c2`

- Async no-candle failures persist the same data-audit explanation as synchronous runs.
- Missing or malformed `FERNET_KEY` produces a boot warning without disclosing secrets.
- Navigation now says **Deploy Strategies** and **Live Broker**.
- Static-IP guidance accepts either supported configuration key and names the exact next
  action.

### Spot-data preflight — `8094683`

- New read-only `POST /api/backtest/spot-preflight?ingest_missing=false` audits the requested
  instrument/date range.
- **Check** never fetches data. **Ingest missing** delegates to the existing production
  audit → Upstox fill → re-audit helper, then the UI automatically rechecks.
- The panel is advisory. A deterministic competent run remains runnable/promotable without
  clicking preflight.
- Browser acceptance used NIFTY for 2026-07-30 and displayed 1/1 complete, 375/375 candles,
  zero missing/incomplete/hash-unverified sessions; Ingest was disabled and Run Backtest
  remained enabled.

## 5. Files changed and where to continue

| Concern | Primary source | Regression/contract coverage |
|---|---|---|
| Optimizer controls, robustness, early-stop and risk objectives | `backend/app/optimizer.py` | `tests/test_optimizer_medium_integrity.py`, `tests/test_optimizer_verified_high_regressions.py` |
| WFO control/persistence parity | `backend/app/wfo.py` | MED and parallel tests |
| Fork-worker metric parity | `backend/app/parallel_eval.py` | `tests/test_parallel_eval.py` |
| Dashboard summary | `backend/app/routers/journals.py` | `tests/test_dashboard_summary_contract.py`, `tests/test_journal_premium_rows.py` |
| Dashboard KPI routing | `frontend/src/lib/backtestMetrics.js`, `frontend/src/pages/Dashboard.jsx` | dashboard/journal contracts and frontend build |
| Spot preflight and async audit parity | `backend/app/routers/research.py` | `tests/test_spot_data_preflight.py`, `tests/test_backtest_paths_are_equivalent.py` |
| Backtest workflow | `frontend/src/lib/api.js`, `frontend/src/pages/BacktestLab.jsx` | spot-preflight and timeout contracts; rendered browser check |
| Operator/boot guidance | `backend/app/encryption.py`, `backend/server.py`, `frontend/src/components/Layout.jsx`, `frontend/src/components/live/LiveBanner.jsx` | `tests/test_stage1_operator_guidance.py`, `tests/test_bootstrap_contract.py` |

The implementation commits, in order, are:

`9865314`, `1c3c5b8`, `2c31cc9`, `f7f0546`, `5bf5582`, `9184a06`, `502871a`,
`3d9f4c2`, `8094683`, `675d4fd`, `24adedd`.

## 6. Present product status and red lines

- **Capability:** warehouse, backtest, optimization, Strategy Library/AI authoring, paper,
  and gated Flattrade live execution are integrated. Capability Phase 0, 1 and 2 plus
  roadmap Stage 1 are complete.
- **Evidence:** three campaigns failed holdout. Do not promise profitability or restart
  edge research unless the user explicitly reopens it.
- **Backtest trust:** any paired-option result saved before 2026-07-30 is wrong and must be
  re-run. Premium-native results use the `option_backtest` envelope; the root metrics/trades
  are an intentional zero stub.
- **Live:** all four pre-real-money code blockers are fixed, but no broker-facing live
  validation has happened. The documented remaining prerequisite is a Flattrade-registered
  static IP plus market-hours validation. The user alone may enable live or transmit.
- **Flattrade MCP:** never call login/logout and never place/modify/cancel orders. Read tools
  are allowed as a sparse independent broker-truth witness.
- **Git:** commit green checkpoints; push only with per-changeset user approval.

## 7. Recommended next action and roadmap

### Next action: Gate A during an NSE market session

Run [`phase5b-market-validation-runbook.md`](phase5b-market-validation-runbook.md) in
**PAPER + READ-ONLY** posture. Record feed state, one-minute candle rollover, analysis
degradation/recovery, paper signals/exits, EOD behavior and reconciliation. Stop on any
unexplained broker activity, stale authorization, non-finite value or mismatch. Do not
enable live. This gate cannot be completed while the market is closed.

### If market-hours validation cannot be run: Stage 2 Dashboard v2

Implement only the decision-surface slice in
[`NEXT_STAGE_ROADMAP_2026-07.md`](NEXT_STAGE_ROADMAP_2026-07.md): system trust strip,
research queue, deployment queue, compact live-market card and action queue. Keep order
controls in the Live Broker cockpit. Every value must come from runtime state, use the
shared KPI adapter, and say unavailable rather than inventing state.

### Later stages

1. Stage 2a bounded transient live index chart; keep it separate from Dashboard v2.
2. Stage 2b arbitrary option chart only after subscription ownership/reference counting.
3. Stage 3 reproducible experiment ledger.
4. Stage 4 real-LLM author → install → backtest → optimize → paper acceptance test.
5. Stage 5 edge research only if the parked decision is explicitly lifted.
6. Stage 6 operational live-capital validation only after static IP, Gate A and the user's
   explicit authorization; use the one-lot readback checklist when the user decides.

## 8. Exact takeover sequence

Before changing code, the next agent should:

1. Read `docs/HANDOFF.md` §2, this document, `docs/BACKTEST_INTEGRITY_AUDIT.md`, then
   `docs/AGENT_TODO.md` ★ START HERE.
2. Run `git status --short`, `git log origin/main..main --oneline`, `git stash list`, and
   confirm the branch/remote heads. Do not assume the commit-ahead count remains current.
3. Run `.venv\Scripts\python.exe -m pytest tests -q` from the repository root and compare
   with the 4,354/4 baseline.
4. For router/Motor behavior, rebuild/copy tests and run the selected files inside
   `alphaforge_backend`; do not interpret missing `/frontend` source-layout tests as backend
   behavior failures.
5. Run Python compileall and `npm run build` from `frontend/` for the half being changed.
6. Rebuild changed containers and verify `/api/health` plus a hard-refreshed browser at
   `http://localhost:3000` (not `127.0.0.1`, because the bundle targets the canonical
   localhost API origin).
7. Give the user a short state readback and a binary-check plan before editing.

## 9. Known residuals

- Disputed optimizer LOW #31 is unresolved and intentionally outside Stage 1.
- Market-hours Gate A is pending; after-hours health does not prove feed/candle behavior.
- Full real-broker validation is pending the registered static IP.
- The real-LLM authoring loop has not been accepted end to end with a funded provider call.
- A cosmetic `favicon.ico` 404 appears in the browser and does not affect the workflow.
- Deferred live-safety/cockpit audit items remain in
  `docs/superpowers/plans/2026-07-25-live-safety-four-fixes.md` and
  `docs/live-cockpit-audit-2026-07-25.md`; verify each against current source before acting.
