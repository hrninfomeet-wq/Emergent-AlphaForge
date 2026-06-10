# Design Document

## Overview

Three in-scope enhancements to the Auto-Optimizer, all implemented. Requirement 4 (parallelism) has been **permanently removed from the plan** — see `requirements.md` Requirement 4 for the reasoning.

The backend already supports the behaviors needed for Requirements 1 and 2; the work is primarily in `frontend/src/pages/Optimizer.jsx`, with a confirmation that `backend/server.py` validation and `backend/app/optimizer.py` guard logic already match the requirements.

## Architecture

```
Optimizer.jsx (Optimization Setup Panel)
  ├─ config state (React useState)  ──persist──►  localStorage["alphaforge.optimizer.setupConfig"]  (R3)
  ├─ Guard rails toggle (guards_enabled, default ON) (R1)
  │     └─ when OFF → start() sends min_trades=0, min_direction_share=0
  ├─ Trial budget input (max 5000 + caution note) (R2)
  └─ start() → POST /api/optimize/start (OptimizerStartReq)
                  └─ optimizer.py _objective_value(): min_trades=0 → no floor; share=0 → no one-sided guard
                     zero-trade trials always score _DISQUALIFY (never selected)
```

## Components and Interfaces

### R1 — Optional guard rails
- Add `guards_enabled: true` to the Optimizer `config` (default ON).
- Render a `Switch` labeled "Guard rails" at the top of the guard section. The existing Min trades + Min CE/PE side % inputs render only WHILE the toggle is ON.
- In `start()`: if `guards_enabled` send `min_trades` and `min_direction_share` from config; else send `min_trades: 0` and `min_direction_share: 0`.
- Backend is unchanged: `_objective_value()` already treats `min_trades=0` and `min_direction_share=0` as "no guard", and always returns `_DISQUALIFY` for zero-trade trials regardless. Strategy logic is untouched. Walk-forward and robustness cards remain as-is.

### R2 — Trial budget to 5000
- Change the `NumberSliderInput` `max` from `1000` to `5000` for the trial budget.
- Add a short caution note: higher budgets increase overfitting risk; scale budget to search-space size.
- Backend `/optimize/start` already validates `10 <= n_trials <= 5000` and returns an HTTP 400 with the range message — no change.

### R3 — Persist setup config
- Add module constant `SETUP_KEY = "alphaforge.optimizer.setupConfig"` and a `DEFAULT_SETUP` object.
- Initialize `config` via a lazy `useState` initializer that reads + parses localStorage, shallow-merging onto `DEFAULT_SETUP` (so newly added fields always have a default). Corrupt/missing JSON → defaults (caught).
- A `useEffect([config])` writes `JSON.stringify(config)` to localStorage inside try/catch (quota/availability failures are swallowed).
- `config` holds only setup fields; transient run state (`currentJobId`, `currentJob`, `jobs`) is separate React state and is never persisted.

## Data Models

### Setup_Config (frontend-only, persisted to localStorage)
The existing Optimizer `config` object, serialized as JSON under key `alphaforge.optimizer.setupConfig`:

| Field | Type | Default | Notes |
|-------|------|---------|-------|
| instrument | string | "NIFTY" | |
| mode | string | "SCALP" | |
| strategy_id | string | "confluence_scalper" | |
| method | string | "bayesian" | |
| objective | string | "risk_adjusted" | |
| n_trials | int | 150 | 10–5000 |
| costs_enabled | bool | true | |
| guards_enabled | bool | true | NEW — R1 master toggle |
| min_trades | int | 10 | applied only when guards_enabled |
| min_direction_pct | number | 0 | percent 0–50; → share /100 |
| optimize_indicator_periods | bool | false | |
| param_overrides | object | {} | |
| start_date / end_date | string | "" | |
| name | string | "Optimization run" | |

Transient run state (`currentJobId`, `currentJob`, `jobs`) is NOT part of Setup_Config and is never persisted.

### OptimizerStartReq (backend, unchanged)
`min_trades` (default 10) and `min_direction_share` (default 0.0) already exist; `start()` sets both to 0 when `guards_enabled` is false.

## Correctness Properties

### Property 1: Guards off ⇒ pure objective
When `guards_enabled` is false, the start payload carries `min_trades=0` and `min_direction_share=0`, so `_objective_value()` applies no floor and no one-sided disqualification; trial ranking is determined solely by the selected objective.
**Validates: Requirements 1.5, 1.7**

### Property 2: Zero-trade never wins
Regardless of guard state, a zero-trade trial scores `_DISQUALIFY` and can never be selected as best.
**Validates: Requirements 1.6**

### Property 3: Strategy invariance
No strategy plugin file is modified; identical params produce identical signals before and after this change.
**Validates: Requirements 1.9**

### Property 4: Persistence round-trip
A Setup_Config written on change and re-read on mount yields the same field values; a missing/corrupt store yields defaults without error.
**Validates: Requirements 3.1, 3.2, 3.4**

### Property 5: Budget bounds
The UI cannot submit `n_trials` outside 10–5000; the backend independently rejects out-of-range values.
**Validates: Requirements 2.1, 2.3**

## Error Handling


- localStorage read parse error → fall back to `DEFAULT_SETUP`, overwrite stored value on next change, no user-facing error.
- localStorage write failure (quota/unavailable) → caught and ignored; in-memory config keeps working.
- Out-of-range trial budget → backend 400 surfaced via the existing toast in `start()`'s catch.

## Testing Strategy
- Backend: existing `pytest tests -q` must stay green (no backend behavior change expected; confirms guard/validation logic intact).
- Frontend: `npm run build` must pass clean.
- Manual/live verification: (a) guards OFF allows a one-sided/low-trade best to be selected; (b) trial budget accepts 5000; (c) settings survive navigating away and back.
