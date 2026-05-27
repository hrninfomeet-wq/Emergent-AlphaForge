# AlphaForge Completion Roadmap Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Complete AlphaForge as a reliable local-PC trading research, data, backtesting, forward-testing, paper-trading, and broker-integration terminal.

**Architecture:** Build in verified vertical slices: data trust first, broker connectivity second, strategy/backtest correctness third, then live/paper execution and packaging. Each slice must leave the app runnable locally with Docker and must include a test/API/browser verification checkpoint.

**Tech Stack:** FastAPI, MongoDB, React, Docker Compose, Upstox REST/OAuth/WebSocket, pytest, CRA/CRACO build, in-app browser checks.

---

## Credit And Model Policy

- Use low or minimal reasoning for file search, status checks, small copy changes, formatting, docs updates, and repeated verification commands.
- Use medium reasoning for normal backend/frontend edits where the required behavior is already clear.
- Use high reasoning only for trading-critical design, broker API/WebSocket behavior, data integrity, order lifecycle, risk controls, strategy correctness, and architecture decisions.
- Use parallel/subagents only when tasks are independent: code review, frontend visual QA, docs audit, backend API review, or isolated strategy research.
- Avoid subagents for tightly coupled edits in the same files, credential handling, database mutation, or destructive actions.
- Prefer local project context first. Browse only for current broker/API documentation, changed vendor rules, or time-sensitive facts.
- Every development slice must end with explicit evidence: tests, build/compile, API smoke test, and browser check when UI changed.

## Current Verified Baseline

- Docker services: backend, frontend, and MongoDB are running locally.
- Backend health endpoint returns database OK.
- Python test suite currently passes.
- Data trust feature has been added: audit endpoint, audit UI, data-audit backtest result card, guarded warehouse clear endpoint, and Upstox gap-fill path for selected backtest windows.
- Frontend production build compiles with one existing React hook warning in `BacktestLab.jsx`.

## Phase Order Aligned To `plan.md`

### Checkpoint A: Reconfirm Completed Phases 1-3.5 And Phase 7

**Files:**
- Modify: `backend/app/warehouse.py`
- Modify: `backend/server.py`
- Modify: `frontend/src/pages/DataWarehouse.jsx`
- Modify: `frontend/src/pages/BacktestLab.jsx`
- Test: `tests/test_bootstrap_contract.py`
- Test: `tests/test_warehouse_audit_helpers.py`

- [ ] **Step 1: Verify current data-trust slice**

Run:
```powershell
python -m pytest tests -q
python -m compileall backend
corepack yarn build
docker compose ps
Invoke-RestMethod -Uri http://localhost:8001/api/health
```

Expected:
```text
19 passed
backend compile completes
frontend build completes, allowing the known hook warning
backend healthy, mongo healthy, frontend running
health returns {"db":"ok"}
```

- [ ] **Step 2: Smoke-test warehouse audit without deleting data**

Run:
```powershell
Invoke-RestMethod -Uri "http://localhost:8001/api/warehouse/audit/NIFTY?start_ts=1779047100000&end_ts=1779471000000"
```

Expected:
```text
summary.complete = true
summary.complete_days = 5
summary.expected_days = 5
summary.hash_mismatch_days = 0
```

- [ ] **Step 3: Smoke-test guarded clear endpoint**

Run:
```powershell
Invoke-RestMethod -Uri http://localhost:8001/api/warehouse/data/NIFTY -Method Delete
```

Expected:
```text
HTTP 400 with "Clear requires confirm=CLEAR"
```

### Phase 4a Hardening: Upstox OAuth And Historical Data Trust

**Files:**
- Modify: `backend/app/upstox_client.py`
- Modify: `backend/app/db.py`
- Modify: `backend/server.py`
- Modify: `frontend/src/pages/DataWarehouse.jsx`
- Test: `tests/test_upstox_*.py`

- [ ] **Step 1: Confirm current Upstox REST/OAuth status**

Run:
```powershell
Invoke-RestMethod -Uri http://localhost:8001/api/upstox/status
```

Expected:
```text
configured = true
connected = true or expired = true with reconnect path available
no token or secret fields are returned
```

### Phase 4b: Upstox WebSocket Tick Stream And Live Signal Foundation

**Files:**
- Create: `backend/app/upstox_ws.py`
- Modify: `backend/app/db.py`
- Modify: `backend/server.py`
- Modify: `frontend/src/pages/LiveSignals.jsx`
- Test: `tests/test_upstox_ws.py`

- [ ] **Step 1: Add WebSocket tick-stream scaffold**

Implement a small module with:
```python
class UpstoxTickStream:
    async def connect(self) -> None: ...
    async def subscribe(self, instrument_keys: list[str]) -> None: ...
    async def close(self) -> None: ...
```

Verification:
```powershell
python -m pytest tests/test_upstox_ws.py -q
```

### Phase 4c: Historical Data Completeness And Exchange Calendar

**Files:**
- Modify: `backend/app/warehouse.py`
- Create: `backend/app/market_calendar.py`
- Modify: `frontend/src/pages/DataWarehouse.jsx`
- Test: `tests/test_market_calendar.py`
- Test: `tests/test_warehouse_audit_helpers.py`

- [ ] **Step 1: Replace weekday heuristic with exchange-calendar aware audit**

Expected behavior:
```text
Weekends ignored.
Known NSE/BSE holidays ignored.
Partial trading sessions can use configured expected candle count.
Audit output explains why a day is expected or skipped.
```

Verification:
```powershell
python -m pytest tests/test_market_calendar.py tests/test_warehouse_audit_helpers.py -q
```

### Phase 4d: Options Backtest Trust And Strategy Engine

**Files:**
- Modify: `backend/app/backtest.py`
- Modify: `backend/app/strategies/**`
- Modify: `backend/app/optimizer.py`
- Modify: `frontend/src/pages/BacktestLab.jsx`
- Test: `backend/test_core.py`
- Create: `tests/test_backtest_data_audit.py`

- [ ] **Step 1: Make backtests reject untrusted data by default**

Expected behavior:
```text
If selected date window is incomplete after auto-fill, backtest returns a clear error unless user explicitly allows incomplete data.
Result stores the data audit summary.
```

Verification:
```powershell
python -m pytest backend/test_core.py tests/test_backtest_data_audit.py -q
```

### Phase 4e: Forward Test And Paper Trading

**Files:**
- Create: `backend/app/paper_trading.py`
- Create: `backend/app/order_state.py`
- Modify: `backend/server.py`
- Create or modify: `frontend/src/pages/PaperTrading.jsx`
- Test: `tests/test_paper_trading.py`

- [ ] **Step 1: Add local paper order lifecycle**

Expected order states:
```text
CREATED -> OPEN -> PARTIAL/FILLED -> EXITED/CANCELLED/REJECTED
```

Verification:
```powershell
python -m pytest tests/test_paper_trading.py -q
```

### Phase 5: Profitability Boosters

**Files:**
- Create: `backend/app/probability_engine.py`
- Create: `backend/app/meta_model.py`
- Modify: `frontend/src/pages/LiveSignals.jsx`
- Test: `tests/test_probability_engine.py`

- [ ] **Step 1: Add transparent probability placeholders gated by sample size**

Expected behavior:
```text
Probability outputs refuse to show confident statistics until enough signal history exists.
The UI shows sample size and warning reason.
```

Verification:
```powershell
python -m pytest tests/test_probability_engine.py -q
```

### Phase 6: Swing And Positional Extension

**Files:**
- Modify: `backend/app/warehouse.py`
- Create: `backend/app/swing_backtest.py`
- Create: `backend/app/strategies/builtin/swing_*.py`
- Test: `tests/test_swing_backtest.py`

- [ ] **Step 1: Add higher-timeframe candle storage/resampling contract**

Expected behavior:
```text
Swing backtests use 1H/1D candles with gap-aware lifecycle handling.
```

Verification:
```powershell
python -m pytest tests/test_swing_backtest.py -q
```

### GitHub Delivery And Local Desktop Readiness

**Files:**
- Modify: `README.md`
- Modify: `docs/LOCAL_SETUP.md`
- Modify: `docker-compose.yml`
- Optional create: `scripts/start-alphaforge.ps1`

- [ ] **Step 1: One-command startup and backup guidance**

Expected behavior:
```text
User can start the stack, check health, back up Mongo data, and restore it using documented PowerShell commands.
```

Verification:
```powershell
docker compose up -d
docker compose ps
```

## Execution Rule

Work one project phase at a time using the numbering in `plan.md`. Do not begin the next phase until the current phase has fresh verification evidence and any defects are either fixed or explicitly logged as known limitations.
