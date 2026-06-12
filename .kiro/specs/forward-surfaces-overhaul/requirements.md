# Forward Surfaces Overhaul — Requirements

Status: Slices 1–2 DONE (2026-06-12, Claude Fable 5). Slices 3–5 are for the next
agent (Opus 4.8 in Kiro). Read `docs/HANDOFF.md` first, then `design.md` and
`tasks.md` in this folder. The repository and tests are the truth, not chat history.

## Core objective (user's words, condensed)

Warehouse clean index + option data locally → backtest/optimize strategies →
save presets → deploy presets for live signal generation and paper trading
concurrently → every signal recorded as a full trade recommendation (instrument,
lot size, entry/exit time + price, trigger and exit conditions, P&L in ₹ and
points) → journals filterable/sortable/exportable for analysis. Real streamed
prices for entries/exits. No broker orders, ever.

## Decisions locked by the user (2026-06-12) — do not relitigate

1. **Independent deployments.** Multiple strategies trade concurrently; no
   highest-score arbitration. Exposure is governed per deployment by
   `risk.max_open_paper_trades`.
2. **Approval flow fully retired.** No Pending Approval UI, no approve/skip/
   mark-blocked/transition/manual-research-signal routes. Modes are exactly
   `signal_only` and `paper` (auto-trade). Legacy `shadow`/`recommendation`
   stored docs read as signal-only; the create path maps them.
3. **Deletion toolkit.** Row-select delete, older-than-X purge, per-deployment
   purge, and archive-with-purge all exist server-side (Slice 1). Slices 3–4
   surface them in the UI.
4. **Strategy editor in Strategy Library = later phase.** Not in this spec.

## Delivered in Slices 1–2 (verify, don't rebuild)

- Backend: `GET /api/signals/enriched` (signal⟷trade join + filters/sort/
  pagination/CSV), `POST /api/signals/purge`, upgraded `GET /api/paper/trades`
  (same treatment + `deployment_name`), `POST /api/paper/trades/purge`,
  `GET /api/deployments/overview`, archive `?purge=1`, option-stream
  auto-follow on deploy/resume (`radius_for_deployments`).
- Frontend: `/live` rebuilt as the Deployments command center (cards + 3-step
  deploy wizard + undeploy). 453 backend tests pass.

## R3 — Signals ledger (rebuild `frontend/src/pages/SignalJournal.jsx`)

The trade-recommendation record. One row per deployment signal showing:
date+time (IST), deployment name, strategy, instrument, CE/PE, contract
(strike+side, expiry), spot at entry, option entry premium, entry trigger
conditions (the signal's `reasons`, expandable), exit time, exit premium, exit
reason, P&L in ₹ and premium points, score, state, blockers (for non-clean),
`paper_trade_error` when a clean signal could not trade.

Acceptance:
- Server-side filters: deployment (preselect from `?deployment=` URL param —
  the command center links here), instrument, state, clean/blocked, date range.
- Sortable columns (server-side via `sort` param), pagination (`skip`/`limit`,
  show total), CSV export button (`format=csv` URL), auto-refresh ≤60s.
- Deletion UI: row checkboxes → "Delete selected"; "Delete older than N days";
  per-deployment purge. All confirm before deleting.
- Update the frontend contract test in `tests/test_signal_paper_lifecycle.py`
  (`test_frontend_exposes_live_and_paper_operational_views`) with the new
  page's testids; keep `listSignalsEnriched`/`purgeSignals` asserts green.

## R4 — Paper trading journal (rebuild `frontend/src/pages/PaperTrading.jsx`)

Acceptance:
- Columns: deployment/strategy name, contract (`trading_symbol`), CE/PE,
  lots × lot size, entry time+price, exit time+price, exit reason, holding
  time, P&L ₹ (and % of entry premium), status.
- Day-wise grouping with per-day subtotal rows; summary strip: today realized,
  open MTM, open count, win rate, profit factor; small equity sparkline from
  closed trades (cumulative realized P&L over time).
- Filters: deployment (`?deployment=` param), status, date range; sort; CSV.
- Live behavior: rows auto-refresh ≤30s during market hours. Replace manual
  type-a-price flow with: "Close @ market" (one click — use trade
  `last_price`; fall back to a prompt only when last_price is null) and a
  "Close all open" button (confirm). Keep a small manual price input as an
  off-hours fallback. Keep purge UI (CLOSED only).
- Keep these contract-test testids working: `paper-trading-journal`,
  `paper-trade-table`, `mark-paper-trade`, `close-paper-trade`, `risk-badge`
  (or update the test deliberately in the same commit).

## R5 — Polish (only after R3+R4 verified)

- P&L calendar heat-grid per deployment (day cells colored by realized ₹).
- "Replay in Backtest Lab" link from a signal row (`/backtest?run=` exists for
  runs; for signals link to the chart Locate tool or omit — judgment call).
- Drift re-pin: one-click re-pin `strategy_source_sha` on a drift-paused
  deployment (new small backend route + button on the pause banner).
- Data-realism (preflight) line inside the deploy wizard step 1 (the
  `GET /api/deployments/preflight` route exists; UI surface was dropped in
  the Slice-2 rebuild).
- ATM±3 option-chain snapshot panel on the Deployments page from stored ticks.

## Non-goals (entire spec)

Broker order execution; resurrecting approval/research-signal flows; per-tick
evaluation; full option-chain ingestion of all strikes; strategy editor UI.
