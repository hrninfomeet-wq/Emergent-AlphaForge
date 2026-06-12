# Forward Surfaces Overhaul — Design Notes for the Next Agent

You are building UI over endpoints that already exist and are tested. Do not
add new collections. Read the endpoint contracts below from the running code
(`backend/server.py`) if anything here looks stale — code wins.

## Endpoint contracts you build on (all shipped, Slice 1)

`GET /api/signals/enriched`
- Params: `deployment_id, strategy_id, instrument, state, clean (bool),
  date_from, date_to (YYYY-MM-DD IST), sort (bar_ts|updated_at|confidence|
  instrument|state, "-" prefix = desc), skip, limit (≤500), format=csv`.
- Row fields beyond the signal doc: `score, spot_entry, bar_ist,
  deployment_name, contract ("24500.0 CE"), contract_expiry, trade_status,
  entry_premium, exit_premium, exit_reason, closed_at, lots, quantity,
  pnl_value (₹, realized if closed else unrealized), pnl_premium_pts
  (pnl_value/quantity)`. Plus `reasons[]` (entry triggers), `blockers[]`,
  `risk_hints`, `paper_trade_error`, `tracked_for_pnl`.
- Response: `{items, count, total, skip, limit}`.

`POST /api/signals/purge` body `{ids?|deployment_id?|older_than_days?|states?}`
→ `{deleted}`. At least one of the first three required.

`GET /api/paper/trades` — same param pattern (`status, deployment_id,
strategy_id, instrument, date_from, date_to, sort (updated_at|created_at|
closed_at|realized_pnl|entry_price), skip, limit, format=csv`); rows carry
`deployment_name`; `{items, count, total}`. `events` is excluded from lists.

`POST /api/paper/trades/purge` `{ids?|deployment_id?|older_than_days?}` —
deletes CLOSED only.

`GET /api/deployments/overview` → `{items: [{deployment{...}, today{clean_signals,
blocked_signals, realized_pnl, open_trades, open_unrealized}, lifetime{
closed_trades, realized_pnl, win_rate}}], totals, as_of_ist}`.

`POST /api/deployments/{id}/archive?purge=1` → undeploy (+ journals purge).

Trade docs: `entry_price`/`exit_price` are option PREMIUM (never spot);
`exit_reason` ∈ stop_hit | target_hit | spot_target_hit | spot_stop_hit |
auto_square_off_15_00_IST | manual...; `spot_exit` holds spot-mirror levels;
`risk` holds premium stop/target; `signal_id`, `strategy_id`, `deployment_id`,
`lots`, `lot_size`, `quantity`, `created_at`, `closed_at` all present.

## Frontend conventions (match the codebase, the reviewer checks these)

- Theme via CSS variable token classes (`bg-bg-1/2/3`, `border-line`,
  `text-dim/dimmer`, `text-info`, emerald/rose accents). NEVER hex codes.
- Every interactive control gets a kebab-case role-based `data-testid`.
- `frontend/src/lib/api.js` is the only fetch layer; add methods there.
- Money: `₹` with `toLocaleString("en-IN")`; premium points 2dp; times IST.
- Tables: sticky `thead`, `text-xs`, row hover `bg-bg-2` — copy the patterns in
  the current `SignalJournal.jsx` before replacing it.
- CSV "export" = `window.open` on the endpoint URL with current filters +
  `format=csv` (the backend sets Content-Disposition). Build the query string
  from the same params object the table uses.
- Use `useSearchParams` for `?deployment=` preselect (see `/live` deep-link
  pattern in `LiveSignals.jsx`).
- Auto-refresh with `setInterval` in `useEffect` (30–60s); never poll in
  page-local state for long-running jobs (JobsProvider exists for that, not
  needed here).

## Testing + verification conventions (non-negotiable)

- `python -m pytest tests -q` must pass before any commit (453 as of Slice 2).
- Contract tests assert file contents: `tests/test_signal_paper_lifecycle.py`
  and `tests/test_strategy_deployments.py` pin api.js methods and page
  testids. Update them IN THE SAME COMMIT as a page rebuild — deliberately,
  not by weakening: pin the new surface, assert retired pieces stay gone.
- Tests must NEVER import `backend/server.py` (motor is not installed on the
  host). Pure helpers go in `backend/app/*` modules; route checks are
  string-asserts on the file text (see existing contract tests).
- `cd frontend && npm run build` must compile (3 pre-existing eslint
  exhaustive-deps warnings in BacktestLab/DataWarehouse/SignalJournal are
  known; add no new ones).
- Backend AND frontend code are baked into Docker images:
  `docker compose up -d --build` after changes, then verify
  `GET /api/health` and the live page at `http://localhost:3000`.
- Commit per slice with a detailed message; the user approves `git push`
  per changeset — never push unasked.

## Trading-domain rules (violating these is a critical bug)

- Option entries/exits are PREMIUM, never the spot index level.
- Lot size always from `option_contracts.lot_size` — never hardcoded.
- OPEN paper trades are never deletable; purge touches CLOSED only.
- IST everywhere in UI; market session 09:15–15:30, signal window 09:25–14:50,
  square-off 15:00.
- Do not resurrect approval routes/UI or the manual research-signal console.
- Old journaled docs (mode `shadow`/`recommendation`, approval audit fields)
  must keep rendering — treat any mode ≠ `paper` as signal-only when displaying.
