# Paper-Trading Deployment Controls — Design

**Goal:** Give the user direct control over deployed paper-trading strategies from the Paper page: pause/resume an individual strategy, stop a strategy (square off its open positions then pause), stop ALL paper trading at once, and fix the broken "Close all open" button.

**Architecture:** Reuse the existing per-deployment `pause`/`resume` endpoints and the existing robust server-side square-off (`square_off_open_paper_trades`). Add one capability — a *deployment-scoped* square-off — plus two thin "stop" endpoints, and a new "Live Deployments" control strip on the Paper page. Pausing already stops new entries (the evaluator only processes `status=="ACTIVE"` — `deployment_evaluator.py:258,570`).

**Tech stack:** FastAPI + Motor (backend), React (frontend), pytest host tests + contract corpus.

---

## Confirmed semantics (user-approved 2026-06-19)
- **Pause** → set deployment `status=PAUSED`. No new entries; existing open positions keep running to their own stop/target (the live exit monitor still marks/exits them).
- **Stop** → square off this deployment's OPEN positions (if any) FIRST, then set `status=PAUSED`. No fresh entries until Resume.
- **Resume** → set `status=ACTIVE`. Fresh entries resume normally.
- **Stop ALL paper trading** → square off ALL open positions + pause every ACTIVE deployment.

## What already exists (reuse, do not rebuild)
- `POST /api/deployments/{id}/pause` → sets PAUSED. `POST /api/deployments/{id}/resume` → sets ACTIVE. (`routers/deployments.py:484,490`)
- `square_off_open_paper_trades(db, *, latest_tick_lookup, reason, now_ist)` — force-closes ALL OPEN paper trades; idempotent; resolves exit price tick→last_mark→entry_fallback (flags estimates via `exit_price_source`/`exit_price_stale`); skips `risk.allow_overnight` trades. (`paper_squareoff.py:102`)
- `POST /api/paper/square-off` — global manual square-off endpoint. (`routers/journals.py:197`)
- Paper page already fetches deployments (`PaperTrading.jsx:146`) and polls live open positions every 2s (`livePos`).

## Components / changes

### 1. Backend — deployment-scoped square-off (`paper_squareoff.py`)
Add an optional `deployment_id: Optional[str] = None` keyword to `square_off_open_paper_trades`. When provided, the OPEN-trades query filters `{"status": "OPEN", "deployment_id": deployment_id}`; when `None`, behaviour is byte-identical to today (global). Everything else (price resolution, overnight skip, idempotency, summaries) is unchanged.

### 2. Backend — "stop" endpoints (`routers/deployments.py`)
- `POST /api/deployments/{id}/stop` → calls `square_off_open_paper_trades(db, deployment_id=id, reason="manual_stop_square_off", latest_tick_lookup=<live tick lookup>)`, THEN sets `status=PAUSED` (same write path as `pause`). Returns `{squared_off: [...summaries], status: "PAUSED"}`. Ordering matters: square off first, then pause (so the pause can't be seen as "done" before positions are closed).
- `POST /api/deployments/stop-all` → square off ALL open (global `square_off_open_paper_trades(..., reason="manual_stop_all")`) THEN set every `status=="ACTIVE"` deployment to `PAUSED`. Returns `{squared_off, paused_deployment_ids}`.
- Use the SAME `latest_tick_lookup` the runtime uses for the auto-15:00 square-off (so live fills, not estimates, when the market is open). If the runtime's tick source isn't importable from the router without a cycle, pass `latest_tick_lookup=None` (the price resolver falls back to last_mark/entry-estimate, already flagged) and note it as a follow-up.

### 3. Frontend — API client (`lib/api.js`)
Add: `pauseDeployment(id)`, `resumeDeployment(id)`, `stopDeployment(id)` (→ `/deployments/{id}/stop`), `stopAllPaper()` (→ `/deployments/stop-all`), `squareOffAll()` (→ `/paper/square-off`).

### 4. Frontend — "Live Deployments" control strip (`pages/PaperTrading.jsx`)
A panel above the Filters card. Lists every non-ARCHIVED deployment (ACTIVE + PAUSED) with:
- status dot/badge (ACTIVE green / PAUSED amber), strategy id + deployment name,
- live **open count** and **open MTM** for that deployment (derived by grouping the existing `livePos.items` by `deployment_id`),
- buttons: **Pause**/**Resume** (toggle by status) and **Stop & square-off**.
- A master **Stop ALL paper trading** button on the strip header.
Each destructive action (`Stop`, `Stop all`) uses a `window.confirm`. **Off-hours safety:** when the market is closed (reuse the cockpit's `market_status`, or simply detect "no fresh open marks"), the confirm text warns that open positions will close at an **estimated** price (last mark / entry), not a live fill.
Fix `closeAllOpen` to call `api.squareOffAll()` (the robust endpoint) instead of the client-side per-trade loop that silently skips unmarked trades. After any action, refresh deployments + `fetchRows()` + `openPositions()`.

### 5. (Optional, same strip) per-deployment archive
Reuse existing `POST /deployments/{id}/archive` as a small "Remove" action so duplicate/legacy deployments (e.g. the redundant confluence pairs) can be cleared. Guard: only allow archive when the deployment has 0 OPEN trades (archive should not strand open positions). Mark OPTIONAL — include only if it doesn't expand scope.

## Data flow
Paper page → control strip reads `deployments` (status) + `livePos` (open count/MTM per `deployment_id`). Button → `api.*` → backend endpoint → Mongo (`strategy_deployments.status` and/or `paper_trades` closes) → UI refetches deployments + trades + open positions.

## Error handling
- Square-off is idempotent (only OPEN trades touched); a concurrent auto-square-off that already closed a trade is a no-op.
- Confirm dialogs on Stop / Stop-all; off-hours estimate warning.
- Endpoint 404 on unknown deployment id; surface `detail` in a toast.
- Pause/Resume are status-only writes; safe to repeat.

## Testing
- **Host (pytest, no server import):** `square_off_open_paper_trades` with `deployment_id` set closes ONLY that deployment's OPEN trades and leaves others OPEN; with `deployment_id=None` it is byte-identical to today (regression). Overnight-skip + idempotency still hold under the filter. Use a fake async db (mirror existing square-off tests if present).
- **Contract corpus:** the three new routes (`/deployments/{id}/stop`, `/deployments/stop-all`, and the reused ones) are registered; string-assert in `tests/contract_corpus.py` style (no `server.py` import).
- **Frontend:** `cd frontend && npm run build` compiles clean.
- **Running stack (controller):** docker rebuild; Pause a deployment → status PAUSED, evaluator skips it (no new entries) but an open trade still marks/exits; Stop a deployment with an open trade → trade closes (reason `manual_stop_square_off`) + status PAUSED; Resume → ACTIVE; Stop-all → all open closed + all paused; "Close all open" now actually closes.

## Risks / verify-items
1. **Live exit monitor on PAUSED deployments:** confirm `LiveExitMonitor`/`mark_open_deployment_trades` exits OPEN trades regardless of the deployment's PAUSED status (so a paused-but-not-stopped strategy's open positions still hit their stops/targets). If it gates on ACTIVE, that's a bug to fix in this work (Pause must not strand open positions without exits). VERIFY at implementation.
2. **Square-off books P&L** — destructive; confirm dialogs + off-hours estimate warning required.
3. **Tick lookup wiring** — if the live tick source can't be passed to the router cleanly, fall back to estimate-priced closes (already flagged) and note as follow-up; do not block the feature.
4. No broker orders — paper only (unchanged).
