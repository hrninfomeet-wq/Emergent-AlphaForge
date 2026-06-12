# Forward Surfaces Overhaul — Tasks & Ready-to-Paste Prompts (Opus 4.8 / Kiro)

- [x] Slice 1 — backend foundations (independent deployments, enriched ledger,
      purge routes, overview, option-stream auto-follow, mode model) — DONE
- [x] Slice 2 — Deployments command center at `/live` (cards, wizard, undeploy) — DONE
- [x] Slice 3 — Signals ledger page rebuild — DONE (2026-06-12)
- [x] Slice 4 — Paper trading journal rebuild — DONE (2026-06-12)
- [x] Slice 5 — Polish extras — DONE (2026-06-12): P&L calendar heat-grid,
      deploy-wizard preflight line, drift re-pin route+button, ATM±3 chain panel

Work ONE slice per session. Each slice: implement → `python -m pytest tests -q`
green → `cd frontend && npm run build` clean → `docker compose up -d --build` →
verify in the browser at localhost:3000 → update the contract tests you touched →
ONE tight CHANGELOG entry + HANDOFF status line → commit (do not push; the user
approves pushes). Then STOP and report.

---

## Prompt for Slice 3 (paste into Kiro as-is)

Read docs/HANDOFF.md, then .kiro/specs/forward-surfaces-overhaul/requirements.md
(section R3) and design.md fully before writing code.

Rebuild frontend/src/pages/SignalJournal.jsx as the "Signals" ledger using the
existing GET /api/signals/enriched endpoint (its exact params and row fields are
in design.md — verify against backend/server.py). Requirements: columns for IST
time, deployment, strategy, instrument, CE/PE, contract+expiry, spot entry,
entry premium, expandable entry-trigger reasons, exit time/premium/reason, P&L
in ₹ and premium points, score, state, blockers and paper_trade_error; server-
side filters (deployment via ?deployment= URL param, instrument, state,
clean/blocked, date range), server-side sort on the whitelisted columns,
pagination with total, CSV export via format=csv with the current filters,
auto-refresh ≤60s, and the deletion UI (row checkboxes → Delete selected;
Delete older than N days; per-deployment purge — all via POST
/api/signals/purge, all confirmed before deleting). Add the api.js methods you
need (listSignalsEnriched and purgeSignals already exist). Update
tests/test_signal_paper_lifecycle.py::test_frontend_exposes_live_and_paper_operational_views
to pin the new page's data-testids in the same commit. Follow every convention
in design.md (theme tokens, testids, IST, no new eslint warnings, tests must
not import server.py). Finish with pytest + npm build + docker compose up -d
--build + a browser check of /journal, one CHANGELOG entry, HANDOFF status
line, and a single commit. Do not push.

## Prompt for Slice 4 (paste into Kiro as-is)

Read docs/HANDOFF.md, then .kiro/specs/forward-surfaces-overhaul/requirements.md
(section R4) and design.md fully before writing code.

Rebuild frontend/src/pages/PaperTrading.jsx as a strategy-named trading journal
over the upgraded GET /api/paper/trades endpoint (params and row fields in
design.md; rows already carry deployment_name). Requirements: columns deployment/
strategy, contract (trading_symbol), CE/PE, lots×lot_size, entry time+price,
exit time+price, exit_reason, holding time, P&L ₹ and % of entry premium,
status; day-wise grouping with per-day subtotals; summary strip (today realized,
open MTM, open count, win rate, profit factor) plus a small equity sparkline of
cumulative realized P&L; filters (deployment via ?deployment=, status, date
range), sort, pagination, CSV export; auto-refresh ≤30s; replace the manual
type-a-price flow with one-click "Close @ market" using the trade's last_price
(prompt fallback when null), a confirmed "Close all open" button, and keep a
small manual-price fallback; purge UI for CLOSED trades via POST
/api/paper/trades/purge. Preserve or deliberately update the contract testids
(paper-trading-journal, paper-trade-table, mark-paper-trade, close-paper-trade,
risk-badge) in tests/test_signal_paper_lifecycle.py in the same commit. Trading
rules in design.md are non-negotiable (premium not spot; OPEN trades never
deletable; IST). Finish with pytest + npm build + docker rebuild + browser
check of /paper, one CHANGELOG entry, HANDOFF status line, single commit. Do
not push.

## Prompt for Slice 5 (paste into Kiro as-is)

Read docs/HANDOFF.md and .kiro/specs/forward-surfaces-overhaul/requirements.md
(section R5). Implement the polish items as SEPARATE small commits in this
order, each verified before the next: (1) P&L calendar heat-grid per deployment
on the Paper Trading page (day cells colored by realized ₹, computed client-
side from closed trades or via one new small aggregation route if needed);
(2) preflight data-realism line in the deploy wizard step 1 using GET
/api/deployments/preflight; (3) drift re-pin: a new POST
/api/deployments/{id}/repin-source route (recompute hash_strategy_source for
the deployment's plugin, update strategy_source_sha, clear drift_* fields,
keep an audit entry) plus a button on the deployment card's pause banner —
include backend tests for the route helper; (4) ATM±3 option-chain snapshot
panel on the Deployments page fed from /api/upstox/stream/ticks/latest. Skip
any item that turns out to need new heavy infrastructure — note it instead.
Same verification and commit discipline as slices 3–4. Do not push.

---

### Notes for the operator (Haroon)

- Run one prompt per Kiro session, in order. If a slice partially lands, the
  next session should start by reading HANDOFF's newest "Recent Work" section.
- After Slice 4, both journals + the command center fulfil the core-objective
  recording/filtering/export requirements end to end.
- Anything trading-critical beyond this spec (evaluator, optimizer, WFO,
  paper_auto internals) was deliberately kept OUT of these prompts — route it
  back to the senior agent.
