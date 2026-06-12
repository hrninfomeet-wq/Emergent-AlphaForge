# Quality Hardening — Spec (from the 2026-06-12 architecture review)

Read `docs/HANDOFF.md` first. Conventions, endpoint-contract style, testing
rules, and the non-negotiable trading rules are in
`.kiro/specs/forward-surfaces-overhaul/design.md` — they ALL apply here too
(theme tokens, kebab-case testids, IST, tests never import server.py, contract
tests updated in the same commit, pytest + npm build + docker rebuild + browser
check per slice, commit but never push).

Context: the senior agent fixed the warehouse root cause (daily ATM-band
completeness, `app/completeness.py`, CHANGELOG 0.23.x). These slices surface
that truth in the UI and close the remaining accepted review items.

## Slice A — Warehouse truth in the UI + retention

1. **DataHygienePanel band fields** (`frontend/src/components/DataHygienePanel.jsx`):
   the plan's `option_candles` block now carries `planned_pairs`,
   `missing_pairs`, `coverage_pct`, `judged_days`, `missing_by_month`,
   `missing_sample` (and no longer has `first/last_expiry_with_data`). Show:
   band coverage % with status color, missing strike-day count, a compact
   missing-by-month line, and an expandable missing-sample list
   (date · expiry · strike · side). Keep the existing check/fill flow untouched.
2. **Dashboard warehouse-health banner** (`frontend/src/pages/Dashboard.jsx`):
   one strip answering "can I trust today's data?" — last auto-update result +
   time (`GET /api/warehouse/auto-update/status`), per-index band coverage from
   a fresh `POST /api/data-hygiene/plan` (cache it client-side for the session;
   it costs ~5s — fetch lazily with a Check button, don't auto-run on mount),
   live stream running/stale (`GET /api/upstox/stream/status`), and OAuth token
   countdown state. Color: green when all verified/running, amber otherwise.
3. **Auto-update history**: surface `GET /api/warehouse/auto-update/status`
   `history[]` (last ~10 runs: started, status, reason, submitted count) in a
   collapsible list inside the DataHygienePanel.
4. **Journal retention**: a small "Retention" block in the Signals Ledger
   cleanup bar — one input "auto-purge blocked/AUDITED signals older than N
   days" persisted in `localStorage`, applied client-side on page load by
   calling the existing `POST /api/signals/purge` with
   `{older_than_days: N, states: ["AUDITED"]}` at most once per day
   (localStorage timestamp guard). No backend changes; confirm-free because the
   user opted in by setting N (empty = off, default off).

## Slice B — Research analytics the data already supports

1. **MAE/MFE distribution card** in Backtest Lab results (paired option trades
   carry `option_mfe_pts`/`option_mae_pts`): two small histograms + medians,
   with a one-line hint ("median MAE suggests stops tighter than X give up
   winners"). Client-side only.
2. **Monte Carlo card** in Backtest Lab results: resample the run's trade P&L
   sequence (1,000 shuffles, client-side) → distribution of max drawdown and
   ending P&L; show P5/P50/P95 drawdown and P(net<0). Cap input at 1,000 trades.
3. **Run comparison view**: in the Backtest Run Journal, allow selecting two
   runs → side-by-side params diff (highlight differing keys), metric table,
   and overlaid equity curves (normalize x to trade index). New component, no
   backend changes (`GET /api/backtest/runs/{id}` has everything).
4. **Volatility audit panel** (API exists, UI missing): a small panel on the
   Data Warehouse page calling `POST /api/volatility/audit` for an
   instrument/date-range; show spike count, spike share, top-10 spike bars
   table. Read-only.
5. **risk_hints in the Signals Ledger** detail row: show the captured
   `risk_hints` (spot pts / premium % / time stop) next to the trigger reasons.

## Slice C — server.py split (GATED: do not start until HANDOFF says the
senior agent has landed the execution-policy extraction; ask the user if unsure)

Mechanical refactor, zero behavior change: move Pydantic request models into
`backend/app/schemas.py` and split `backend/server.py` routes into
`backend/app/routers/{warehouse,research,deployments,journals,broker}.py`
using FastAPI APIRouter, keeping `server.py` as the app factory + scheduler
wiring + startup hooks. Preserve route paths/order exactly (literal routes
before `/{id}` routes). The existing contract tests assert route-decorator
strings in `server.py` — update them to scan the routers directory in the same
commit. Run the FULL suite + a docker rebuild + smoke of every page before
committing. One commit per router file is fine.

## Prompts

Slice A (paste into Kiro as-is):
"Read docs/HANDOFF.md and .kiro/specs/quality-hardening/spec.md (Slice A), and
.kiro/specs/forward-surfaces-overhaul/design.md for conventions. Implement
Slice A exactly: DataHygienePanel band-coverage fields, Dashboard
warehouse-health banner (lazy plan fetch behind a Check button), auto-update
history list, and the opt-in client-side AUDITED-signal retention purge.
Frontend-only; verify each surface in the browser; update any contract tests
you touch; one CHANGELOG entry + HANDOFF status line; single commit; do not
push."

Slice B (paste into Kiro as-is):
"Read docs/HANDOFF.md and .kiro/specs/quality-hardening/spec.md (Slice B), and
.kiro/specs/forward-surfaces-overhaul/design.md for conventions. Implement the
five Slice-B analytics items (MAE/MFE card, Monte Carlo card, run comparison
view, volatility audit panel, risk_hints in ledger detail) as SEPARATE commits
in that order, each browser-verified before the next. Client-side math only;
no backend changes except none. One CHANGELOG entry at the end + HANDOFF
status line; do not push."

Slice C: gated — request the prompt from the senior agent after the
execution-policy extraction lands.
