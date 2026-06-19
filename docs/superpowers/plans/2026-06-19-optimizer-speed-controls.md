# Optimizer Speed Controls — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Parallel-workers UI toggle + convergence early-stop (default ON, `n_trials` = ceiling).

**Architecture:** Pure `early_stop.py` decision module (host-TDD) wired into the optimizer's three trial loops; new `OptimizerStartReq` fields; two `Optimizer.jsx` controls. Spec: `docs/superpowers/specs/2026-06-19-optimizer-speed-controls-design.md`. Branch: `feat/optimizer-speed-controls`.

**Standing constraints:** Host tests MUST NOT import `server`/`optimizer`/`runtime`/`paper_auto`. `early_stop.py` will be a pure module (no such imports) → host-testable. Commit only named files via pathspec; never commit `CHANGELOG.md`/`docs/HANDOFF.md`. Run tests from repo root: `python -m pytest tests/...`. No push/merge without explicit user instruction.

---

## Task 1: pure `early_stop.py` (host-TDD)

**Files:** Create `backend/app/early_stop.py`; Test: `tests/test_early_stop.py`.

- [ ] **Step 1 — failing tests** (`tests/test_early_stop.py`):
```python
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from app.early_stop import is_significant_improvement, should_early_stop

def test_first_trial_anchor_neg_inf_is_improvement():
    assert is_significant_improvement(0.5, float("-inf"), 0.001) is True

def test_relative_threshold_just_below_is_not_improvement():
    # 100 -> 100.05 is +0.05%, below 0.1% -> not significant
    assert is_significant_improvement(100.05, 100.0, 0.001) is False

def test_relative_threshold_just_above_is_improvement():
    assert is_significant_improvement(100.2, 100.0, 0.001) is True

def test_negative_anchor_uses_abs_magnitude():
    # anchor -100, min_delta 0.1% -> need > -100 + 0.1 = -99.9
    assert is_significant_improvement(-99.5, -100.0, 0.001) is True
    assert is_significant_improvement(-99.95, -100.0, 0.001) is False

def test_nan_new_value_is_not_improvement():
    assert is_significant_improvement(float("nan"), 1.0, 0.001) is False

def test_no_stop_before_warmup():
    assert should_early_stop(completed=50, last_improve_trial=0, warmup=200, patience=20) is False

def test_no_stop_within_patience():
    assert should_early_stop(completed=210, last_improve_trial=200, warmup=200, patience=20) is False  # 10 < 20

def test_stop_at_patience_boundary():
    assert should_early_stop(completed=220, last_improve_trial=200, warmup=200, patience=20) is True  # 20 >= 20

def test_patience_below_one_never_stops():
    assert should_early_stop(completed=1000, last_improve_trial=0, warmup=10, patience=0) is False
```

- [ ] **Step 2 — run, expect FAIL** (`ModuleNotFoundError`). `python -m pytest tests/test_early_stop.py -v`.

- [ ] **Step 3 — implement** `backend/app/early_stop.py`:
```python
"""Pure convergence early-stop decision for the optimizer. No I/O — the optimizer
loop supplies the running counters. `n_trials` is treated as a CEILING: the search
stops once the best objective has not SIGNIFICANTLY improved for `patience` trials,
after a `warmup`. Off (early_stop=False) -> the optimizer never calls should_early_stop."""
from __future__ import annotations
import math


def is_significant_improvement(new_value: float, anchor_value: float, min_delta: float) -> bool:
    """True when new_value beats anchor_value by at least a relative min_delta of
    |anchor|. First improvement (anchor == -inf) is always significant. NaN -> False."""
    try:
        nv = float(new_value)
    except (TypeError, ValueError):
        return False
    if math.isnan(nv):
        return False
    if anchor_value == float("-inf"):
        return True
    return nv > anchor_value + abs(anchor_value) * float(min_delta)


def should_early_stop(*, completed: int, last_improve_trial: int, warmup: int, patience: int) -> bool:
    """Stop once at least `warmup` trials have run AND `patience`+ trials have passed
    since the last significant improvement. patience<1 disables (returns False)."""
    if patience < 1 or completed < warmup:
        return False
    return (completed - last_improve_trial) >= patience
```

- [ ] **Step 4 — run, expect PASS.** Then full suite `python -m pytest tests/ -q` (was 798; +9 → 807).
- [ ] **Step 5 — commit** `backend/app/early_stop.py tests/test_early_stop.py` → `feat(optimizer): pure convergence early-stop decision module`.

## Task 2: `OptimizerStartReq` fields + contract

**Files:** Modify `backend/app/schemas.py`; Test: extend the optimizer-schema/contract test.

- [ ] **Step 1 — add fields** to `OptimizerStartReq` (after `opt_workers`):
```python
    early_stop: bool = True
    early_stop_warmup: int = 200
    early_stop_patience: int = 200
    early_stop_min_delta: float = 0.001
```
- [ ] **Step 2 — contract test:** the existing `tests/test_optimizer_indicator_keys.py` reads optimizer source as text; the schema is in `schemas.py`. Add/extend a small text-or-import-light assertion (mirror how OptimizerStartReq fields are pinned today — check `tests/` for an existing OptimizerStartReq test; if it constructs the model, add a case asserting the four defaults). Do NOT import server/optimizer.
- [ ] **Step 3 — run** `python -m pytest tests/ -q` green. **Commit** `backend/app/schemas.py <test>` → `feat(optimizer): early_stop config fields on OptimizerStartReq`.

## Task 3: wire early-stop into the optimizer loops (backend)

**Files:** Modify `backend/app/optimizer.py`. (Verified on running stack in Task 5 — not host-importable.)

- [ ] **Step 1 — read payload knobs** near `n_trials = int(payload.get("n_trials", 200))` (optimizer.py:789): add
```python
    early_stop = bool(payload.get("early_stop", True))
    es_warmup = int(payload.get("early_stop_warmup", 200) or 0)
    es_patience = int(payload.get("early_stop_patience", 200) or 0)
    es_min_delta = float(payload.get("early_stop_min_delta", 0.001) or 0.0)
    from app.early_stop import is_significant_improvement, should_early_stop
    early_stopped = False
```
- [ ] **Step 2 — init trackers** where `best_so_far`/`completed` are set for BOTH the fresh-start (~:932) and resume (~:899) paths:
```python
    anchor_value = best_so_far["value"]
    last_improve_trial = best_so_far["trial_num"] if best_so_far["trial_num"] >= 0 else 0
```
- [ ] **Step 3 — insert the check** at the END of each loop iteration/batch, AFTER `best_so_far` is updated, in ALL THREE loops (grid ~:962-964, bayesian-seq ~:999, bayesian-parallel ~:1048). Use this snippet (adapt variable names to each loop; for the parallel branch ensure `best_so_far["trial_num"]` is a completed-count, not an Optuna trial id — normalize to `completed` if needed):
```python
        if is_significant_improvement(best_so_far["value"], anchor_value, es_min_delta):
            anchor_value = best_so_far["value"]
            last_improve_trial = completed
        if early_stop and should_early_stop(completed=completed, last_improve_trial=last_improve_trial,
                                            warmup=es_warmup, patience=es_patience):
            early_stopped = True
            break
```
- [ ] **Step 4 — record outcome** where the job transitions to `analyzing` (optimizer.py:1066) and/or final finish: add `"early_stopped": early_stopped, "stopped_at_trial": completed, "trials_ceiling": n_trials` to the `_update_job` payload. Confirm it doesn't break results serialization.
- [ ] **Step 5 — `early_stop=False` is byte-identical:** when false, `should_early_stop` is never consulted (guarded by `if early_stop`), so the loop runs the full ceiling exactly as today. Note this for the Task 5 regression check.
- [ ] **Step 6 — `py_compile`** `python -m py_compile backend/app/optimizer.py`; full host suite `python -m pytest tests/ -q` still green (no host test imports optimizer; this just guards syntax). **Commit** `backend/app/optimizer.py` → `feat(optimizer): convergence early-stop wired into grid + sequential + parallel loops`.

## Task 4: `Optimizer.jsx` controls + payload (frontend)

**Files:** Modify `frontend/src/pages/Optimizer.jsx` (and `frontend/src/lib/api.js` only if the start payload is assembled there).

- [ ] **Step 1 — read** `Optimizer.jsx` to find the config state + where the start payload is built (the object posted to `/optimize/start`). Identify the `method`/`n_trials` controls to place the new ones beside.
- [ ] **Step 2 — Parallel workers:** add a number input (min 1, max 15) bound to a `optWorkers` state (default 1), shown only when `method === "bayesian"`, label "Parallel workers" + helper "experimental · non-deterministic · more RAM". Include `opt_workers: optWorkers` in the payload.
- [ ] **Step 3 — Auto-stop toggle:** add an "Auto-stop when converged" checkbox/switch bound to `earlyStop` state (default true) + helper "n_trials becomes a ceiling; stops when the best plateaus". Include `early_stop: earlyStop` in the payload. (Patience advanced field optional — only if it fits the form cleanly; defaults apply otherwise.)
- [ ] **Step 4 — build** `cd frontend && npm run build` → compiles (only the two pre-existing BacktestLab/BacktestChart warnings; nothing new in Optimizer.jsx). Add `data-testid`s `opt-parallel-workers`, `opt-early-stop`.
- [ ] **Step 5 — commit** `frontend/src/pages/Optimizer.jsx` (+ api.js if touched) → `feat(optimizer-ui): parallel-workers + auto-stop-when-converged controls`.

## Task 5: running-stack verification (controller-run)

**Files:** none.

- [ ] Docker rebuild backend + frontend. Health `db:ok`.
- [ ] **Early-stop fires:** `POST /optimize/start` bayesian, a quick strategy/short window, `n_trials=500, early_stop=true, early_stop_warmup=10, early_stop_patience=10` → poll to done → assert `n_trials_completed < 500` AND the job records `early_stopped=true` + `stopped_at_trial`.
- [ ] **Off = full ceiling:** same config with `early_stop=false, n_trials=30` → runs all 30 (`n_trials_completed==30`, `early_stopped` falsey).
- [ ] **Parallel coexists:** one run with `opt_workers=4, early_stop=true` → completes, `effective_workers` honored, early-stop still fires.
- [ ] **Frontend:** the two controls render in the Optimizer form (DOM check) and a started run's payload carries `opt_workers`/`early_stop`.
- [ ] On PASS: **superpowers:finishing-a-development-branch** — verify host tests, present merge/keep options. Do NOT push/merge without explicit user instruction.

---

## Completion
After Tasks 1-4 host-green + Task 5 verified: finishing-a-development-branch. Optimizer-only; no paper/broker impact; `early_stop=false` preserves today's behaviour exactly.
