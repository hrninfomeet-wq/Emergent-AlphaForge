# Paper-Trading Deployment Controls — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Pause/resume/stop individual paper-deployed strategies + stop-all + fix "Close all open", from the Paper page.

**Architecture:** Reuse existing `pause`/`resume` endpoints + the robust `square_off_open_paper_trades`. Add a `deployment_id` filter to that square-off, two "stop" endpoints, API-client methods, and a "Live Deployments" control strip on `PaperTrading.jsx`. Spec: `docs/superpowers/specs/2026-06-19-paper-deployment-controls-design.md`. **Branch:** `feat/paper-deployment-controls`.

**Resolved facts (verified):** pause→`PAUSED` stops new entries (`deployment_evaluator.py:258,570` only evaluates ACTIVE); the live exit monitor `mark_open_deployment_trades` (paper_auto.py:508) marks/exits ALL `status=="OPEN"` trades regardless of deployment status, so PAUSE does NOT strand open positions. Existing global square-off endpoint: `POST /api/paper/square-off`. Pause/resume: `POST /api/deployments/{id}/pause|resume`.

**Standing constraints:** Host tests MUST NOT import `server`/`optimizer`/`runtime`/`paper_auto`. `paper_squareoff.py` is host-importable (it imports only `paper_trading.close_trade` + `nse_calendar`). Run host tests from repo root: `python -m pytest tests/...`. Commit only the named files (pathspec); never commit `CHANGELOG.md`/`docs/HANDOFF.md`. No push without approval.

---

## Task 1: deployment-scoped square-off (backend, host-TDD)

**Files:** Modify `backend/app/paper_squareoff.py`; Test: `tests/test_paper_squareoff.py` (create or extend if it exists — check first).

- [ ] **Step 1 — failing test.** A fake async db holding 3 OPEN paper_trades across 2 deployments (`depA`×2, `depB`×1). Assert: `square_off_open_paper_trades(db, deployment_id="depA")` closes ONLY depA's 2 trades (returns 2 summaries) and leaves depB's trade OPEN; and `deployment_id=None` closes all 3 (today's behaviour, regression pin). Mirror the existing square-off test's fake-db/`latest_tick_lookup` shape if `tests/test_paper_squareoff.py` already exists.
- [ ] **Step 2 — run, expect FAIL** (`TypeError: unexpected keyword 'deployment_id'`). `python -m pytest tests/test_paper_squareoff.py -v`.
- [ ] **Step 3 — implement.** Add `deployment_id: Optional[str] = None` keyword to `square_off_open_paper_trades`. Build the query as `q = {"status": "OPEN"}` then `if deployment_id: q["deployment_id"] = deployment_id`, and use `db.paper_trades.find(q, {"_id": 0})`. Nothing else changes (price resolution, overnight skip, summaries, idempotency identical).
- [ ] **Step 4 — run, expect PASS.** Confirm both the scoped and the `None` (global, byte-identical) cases pass.
- [ ] **Step 5 — full suite** `python -m pytest tests/ -q` green. **Commit** (`backend/app/paper_squareoff.py tests/test_paper_squareoff.py`) `feat(paper): deployment-scoped square_off filter`.

## Task 2: stop + stop-all endpoints (backend)

**Files:** Modify `backend/app/routers/deployments.py`; Test: extend `tests/contract_corpus.py` / the deployments contract test (string-assert routes, no `server.py` import — match how existing deployment routes are asserted).

- [ ] **Step 1 — read** `pause_deployment`/`resume_deployment` (deployments.py:484-494) for the exact status-write pattern + how the router gets `db` and (if available) a live `latest_tick_lookup`. Read how `routers/journals.py:197` `manual_paper_square_off` obtains its `latest_tick_lookup` (reuse the same source; if none is readily importable without a cycle, pass `None` and rely on the flagged estimate fallback — note it).
- [ ] **Step 2 — implement** `POST /deployments/{id}/stop`: 404 if the deployment doesn't exist; call `square_off_open_paper_trades(db, deployment_id=id, reason="manual_stop_square_off", latest_tick_lookup=<same as journals>)`; THEN set `status="PAUSED"` (+ `updated_at`) via the same write `pause` uses. Return `{"squared_off": summaries, "status": "PAUSED"}`.
- [ ] **Step 3 — implement** `POST /deployments/stop-all`: `square_off_open_paper_trades(db, reason="manual_stop_all", latest_tick_lookup=...)` (global), then set every `status=="ACTIVE"` deployment to `PAUSED`. Return `{"squared_off": summaries, "paused_deployment_ids": [...]}`. (Place the literal `/deployments/stop-all` route BEFORE `/deployments/{deployment_id}` if FastAPI ordering requires it — verify it doesn't get captured by the `{deployment_id}` path; it won't if defined as a distinct path, but confirm.)
- [ ] **Step 4 — contract test** asserts both new routes are registered (string-assert, mirroring existing). `python -m pytest tests/ -q` green.
- [ ] **Step 5 — Commit** (`backend/app/routers/deployments.py` + the contract test file) `feat(deployments): stop (square-off+pause) and stop-all endpoints`.

## Task 3: API client methods (frontend)

**Files:** Modify `frontend/src/lib/api.js`.

- [ ] **Step 1 — read** the existing deployment calls in `api.js` (e.g. `listDeployments`, any pause/resume) to match the exact `api.*` style + base path.
- [ ] **Step 2 — add**: `pauseDeployment(id)` → POST `/deployments/${id}/pause`; `resumeDeployment(id)` → POST `/deployments/${id}/resume`; `stopDeployment(id)` → POST `/deployments/${id}/stop`; `stopAllPaper()` → POST `/deployments/stop-all`; `squareOffAll()` → POST `/paper/square-off`. (Some of pause/resume may already exist — reuse, don't duplicate.)
- [ ] **Step 3 — Commit** (`frontend/src/lib/api.js`) `feat(api): paper deployment pause/resume/stop/stop-all/square-off`.

## Task 4: Live Deployments control strip (frontend)

**Files:** Modify `frontend/src/pages/PaperTrading.jsx`.

- [ ] **Step 1 — fix `closeAllOpen`** (PaperTrading.jsx:373-392): replace the client-side per-trade loop with a single `await api.squareOffAll()` inside the existing confirm; on success toast the count from the response summaries and `await fetchRows()`. (This alone fixes the broken "Close all open".)
- [ ] **Step 2 — add the "Live Deployments" strip** above the Filters card (~line 447). Source rows from the already-fetched `deployments` filtered to `status !== "ARCHIVED"`. Per-deployment live open count + open MTM by grouping `livePos.items` on `deployment_id`. Each row: status badge (ACTIVE=emerald, PAUSED=amber), strategy id + name, `N open · MTM ₹x`, and buttons — **Pause** (if ACTIVE) / **Resume** (if PAUSED) calling `api.pauseDeployment`/`api.resumeDeployment`; **Stop & square-off** calling `api.stopDeployment` (confirm dialog). A header **Stop ALL paper trading** button → `api.stopAllPaper()` (confirm dialog). After any action: refetch deployments (`api.listDeployments`) + `fetchRows()`.
- [ ] **Step 3 — off-hours safety.** In the Stop / Stop-all confirm text, when there is no fresh live mark (e.g. `livePos.items` empty or all stale, or reuse cockpit `market_status` if already available in the page), add: "Market looks closed — open positions will close at an ESTIMATED price (last mark/entry), not a live fill." Keep it a plain `window.confirm` (consistent with the page's existing close flows).
- [ ] **Step 4 — build.** `cd frontend && npm run build` compiles (the one pre-existing exhaustive-deps warning is acceptable). Keep `data-testid`s on the new controls (`paper-deploy-strip`, `paper-deploy-pause`, `paper-deploy-resume`, `paper-deploy-stop`, `paper-stop-all`).
- [ ] **Step 5 — Commit** (`frontend/src/pages/PaperTrading.jsx`) `feat(paper): Live Deployments control strip (pause/resume/stop/stop-all) + fix Close-all-open`.

## Task 5: running-stack verification (controller-run)

**Files:** none.

- [ ] Docker rebuild backend + frontend (`docker compose up -d --build`). Health `db:ok`.
- [ ] API smoke: `POST /deployments/{id}/stop` on a deployment → its OPEN trades close (reason `manual_stop_square_off`) + `status==PAUSED`; `POST /deployments/{id}/resume` → ACTIVE; `POST /deployments/stop-all` → all open closed + all ACTIVE paused; `POST /paper/square-off` closes remaining.
- [ ] Browser smoke (Chrome): the Live Deployments strip renders the deployments with status + open count; Pause flips ACTIVE→PAUSED; Resume flips back; Stop closes that strategy's open trades + pauses; Stop-all works; "Close all open" now actually closes; no console errors.
- [ ] On PASS: **finishing-a-development-branch** — verify host tests, present merge/keep options. Do NOT push without explicit instruction.

---

## Completion
After Tasks 1-4 land host-green and Task 5 verifies on the stack: use **superpowers:finishing-a-development-branch**. This is paper-only; no broker orders; pause never strands open positions (exit monitor runs on all OPEN trades).
