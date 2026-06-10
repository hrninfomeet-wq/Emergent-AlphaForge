# Implementation Plan

## Overview

All three in-scope requirements implemented and verified. Requirement 4 (parallelism) has been **permanently removed from the plan** — see `requirements.md` Requirement 4 for the full reasoning. The speed improvements needed were achieved via non-parallel means: dict-record hot loop (~8.8x) and vectorized `detect_fvg`.

## Tasks

- [x] 1. Confirm backend already satisfies R1/R2 (no behavior change)
  - Verify `_objective_value()` treats `min_trades=0` and `min_direction_share=0` as no-guard and always disqualifies zero-trade trials
  - Verify `/optimize/start` validates `10 <= n_trials <= 5000`
  - _Requirements: 1.5, 1.6, 1.7, 2.2, 2.3, 2.4_

- [x] 2. R1 — Optional guard rails in Optimizer Setup panel
  - Add `guards_enabled: true` to the Optimizer `config` default
  - Add a "Guard rails" Switch (default ON); render Min trades + Min CE/PE side % inputs only while ON
  - In `start()`, send `min_trades`/`min_direction_share` from config when ON, else `0`/`0`
  - Keep strategy logic and the walk-forward/robustness result cards unchanged
  - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 1.7, 1.8, 1.9, 1.10_

- [x] 3. R2 — Raise trial budget to 5000 with guidance
  - Change trial-budget `NumberSliderInput` max from 1000 to 5000
  - Add caution note (overfitting risk; scale budget to search-space size)
  - _Requirements: 2.1, 2.5, 2.6, 2.7_

- [x] 4. R3 — Persist Optimization Setup config across navigation
  - Add `SETUP_KEY` + `DEFAULT_SETUP`; lazy-init `config` from localStorage merged onto defaults (corrupt/missing → defaults)
  - Add `useEffect([config])` that writes config to localStorage in try/catch (swallow quota/unavailable)
  - Ensure transient run state is excluded from persistence
  - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7_

- [x] 5. Verify
  - `npm run build` clean; `python -m pytest tests -q` green (378 passed)
  - Rebuilt frontend container; live-checked guards OFF accepted, budget 5000 accepted / 6000 rejected (400)
  - _Requirements: 1, 2, 3_

## Task Dependency Graph

```json
{
  "waves": [
    { "wave": 1, "tasks": ["1"], "description": "Backend confirmation gate" },
    { "wave": 2, "tasks": ["2", "3", "4"], "description": "Independent frontend slices (guards, budget, persistence)" },
    { "wave": 3, "tasks": ["5"], "description": "Build, test, and live verification" }
  ]
}
```

Tasks 2, 3, and 4 are independent of each other and can be done in any order; all depend on task 1's confirmation and feed task 5.

## Notes

- No backend code changes are expected; task 1 is a verification gate. If a gap is found, it becomes an explicit sub-change.
- Strategy plugin files must not be modified (Correctness Property P3).
- Container rebuild required for any backend change (none expected); frontend changes need `npm run build` + frontend image rebuild.
