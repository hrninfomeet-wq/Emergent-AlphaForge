# Optimizer Multi-Core (opt-in parallel trials) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make optimizer (and WFO) bayesian trial runs ~2.5-5× faster via an opt-in fork process pool, with `opt_workers=1` (default) byte-identical to today.

**Architecture:** One pure module `app/parallel_eval.py` (fork `ProcessPoolExecutor`, COW-shared `raw_df` module global, top-level picklable worker, single-active-job guard) + a `workers>1` batched ask/tell branch in `optimizer.py` (and later `wfo.py`) that the parent drives while workers only run backtests. `opt_workers<=1` bypasses all new code.

**Tech Stack:** Python 3.11 (Linux Docker), Optuna 4.8.0 (`study.ask`/`study.tell`/`TPESampler(constant_liar=True)` — confirmed in `requirements.txt`), `concurrent.futures.ProcessPoolExecutor` + `multiprocessing` fork context, pytest.

**Spec:** `docs/superpowers/specs/2026-06-18-optimizer-multicore-design.md`. **Branch:** `feat/optimizer-multicore` (off `feat/backtest-exit-controls` tip 23ee545).

**Standing constraints:** Host tests must NOT import `server.py`/`optimizer.py`/`runtime.py`/`paper_auto.py`. The dev host is Windows (no `fork`) → host pytest only exercises the sequential fallback; the **real parallel gate runs inside the Docker container** (`docker exec`). Do NOT push without approval. Leave the user's uncommitted files untouched. **Note:** the audit's `_MAX_ENRICHED_CACHE` move is NOT needed — the worker uses `enrich_with_cache` (per-group cache, already in the pure `indicator_groups.py`); it never references the optimizer's full-frame cache constant.

---

## File Structure
- **Create** `backend/app/parallel_eval.py` — the fork-pool helper (pure, host-importable). Owns: `effective_workers`, `_worker_evaluate`, `start_pool`/`shutdown_pool`, `parallel_backtest`.
- **Create** `tests/test_parallel_eval.py` — host tests (bounding, sentinel, picklability, sequential fallback equality).
- **Modify** `backend/app/optimizer.py` — `opt_workers` payload read + the `workers>1` batched branch in the bayesian path; `shutdown_pool()` in `finally`.
- **Modify** `backend/app/wfo.py` — reuse the helper in the inner window loop with `slice_bounds` (P3).
- **Create** `tests/container/test_multicore_gate.py` — container-only gate (byte-identity@1, parallel==seq, resume@>1, cancel-mid-batch). Run via `docker exec`, NOT host pytest collection.

---

## PHASE 1 — `parallel_eval.py` + host tests

### Task 1: The parallel-evaluation helper

**Files:**
- Create: `backend/app/parallel_eval.py`
- Create: `tests/test_parallel_eval.py`

- [ ] **Step 1: Write the module**

```python
# backend/app/parallel_eval.py
"""Opt-in fork-based parallel backtest evaluation for the optimizer/WFO trial loop.

The PARENT keeps the Optuna study and does all ask/tell; this module only runs
backtests in worker processes. raw_df is COW-inherited via fork (never pickled).
Only ONE parallel job runs at a time (module-global pool); a concurrent second
parallel job transparently falls back to sequential (start_pool returns None).
"""
from __future__ import annotations
import os
import threading
import multiprocessing
from concurrent.futures import ProcessPoolExecutor
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

from app.backtest import run_backtest
from app.indicator_groups import enrich_with_cache
from app.strategies.base import get_registry

# Worker-process globals (set in the parent before fork; COW-inherited).
_RAW_DF: Optional[pd.DataFrame] = None
_WORKER_CACHES: Dict[str, Dict] = {}

# Parent-process pool state (single active parallel job at a time).
_POOL: Optional[ProcessPoolExecutor] = None
_POOL_LOCK = threading.Lock()


def fork_available() -> bool:
    return "fork" in multiprocessing.get_all_start_methods()


def effective_workers(requested: Any) -> int:
    """Clamp the requested worker count to a safe value. 1 (sequential) when
    requested<=1, fork is unavailable, or os.cpu_count() is unknown."""
    try:
        req = int(requested or 1)
    except (TypeError, ValueError):
        req = 1
    if req <= 1 or not fork_available():
        return 1
    cpu = os.cpu_count() or 1
    cap = req
    env = os.environ.get("AF_OPT_WORKERS")
    if env:
        try:
            cap = min(cap, int(env))
        except ValueError:
            pass
    return max(1, min(cap, cpu - 1))


def _init_worker() -> None:
    global _WORKER_CACHES
    _WORKER_CACHES = {}


def _worker_evaluate(strategy_id: str, merged: Dict[str, Any], slice_bounds: Optional[Tuple[int, int]],
                     instrument: str, costs: bool, pretrade: Dict[str, Any]) -> Tuple[Optional[Dict[str, Any]], Dict[str, Any]]:
    """Top-level, picklable. Reads _RAW_DF from the (fork-inherited) module global;
    re-derives the strategy from the fork-inherited registry. Returns (metrics|None, merged)."""
    try:
        frame = _RAW_DF if slice_bounds is None else _RAW_DF.iloc[slice_bounds[0]:slice_bounds[1]]
        strategy = get_registry().get(strategy_id)
        enr = enrich_with_cache(frame, merged, _WORKER_CACHES)
        res = run_backtest(enr, strategy, merged, instrument=instrument, costs_enabled=costs, pretrade_filters=pretrade)
        metrics = dict(res["metrics"])
        trades = res.get("trades", []) or []
        ce = sum(1 for t in trades if str(t.get("direction", "")).upper() == "CE")
        metrics["ce_count"] = int(ce)
        metrics["pe_count"] = int(len(trades) - ce)
        return (metrics, merged)
    except Exception:
        return (None, merged)


def start_pool(raw_df: pd.DataFrame, workers: int) -> Optional[ProcessPoolExecutor]:
    """Create the fork pool with raw_df COW-shared. Returns the pool, or None if a
    parallel job is already active (caller then runs sequentially). Forks workers
    eagerly (warmup) inside the lock so they snapshot _RAW_DF before it can change."""
    global _RAW_DF, _POOL
    if workers <= 1 or not fork_available():
        return None
    with _POOL_LOCK:
        if _POOL is not None:
            return None  # another parallel job owns the pool -> caller falls back to sequential
        _RAW_DF = raw_df
        ctx = multiprocessing.get_context("fork")
        pool = ProcessPoolExecutor(max_workers=workers, mp_context=ctx, initializer=_init_worker)
        # Force every worker to fork NOW (snapshot _RAW_DF) before releasing the lock.
        list(pool.map(_noop, range(workers)))
        _POOL = pool
    return pool


def shutdown_pool() -> None:
    global _POOL
    with _POOL_LOCK:
        if _POOL is not None:
            _POOL.shutdown(cancel_futures=True)
            _POOL = None


def _noop(_x: int) -> int:
    return 0


def parallel_backtest(pool: Optional[ProcessPoolExecutor], param_sets: List[Tuple[str, Dict[str, Any], Optional[Tuple[int, int]]]],
                      *, instrument: str, costs: bool, pretrade: Dict[str, Any]) -> List[Tuple[Optional[Dict[str, Any]], Dict[str, Any]]]:
    """Run param_sets [(strategy_id, merged, slice_bounds), …]. Results are returned
    in SUBMISSION ORDER. When pool is None, runs sequentially in-process (identical
    to the per-trial path) — the fallback for opt_workers<=1 / fork-unavailable /
    concurrent-job."""
    if pool is None:
        return [_worker_evaluate(sid, m, sb, instrument, costs, pretrade) for (sid, m, sb) in param_sets]
    futs = [pool.submit(_worker_evaluate, sid, m, sb, instrument, costs, pretrade) for (sid, m, sb) in param_sets]
    return [f.result() for f in futs]  # f.result() iterated in submission order preserves order
```

- [ ] **Step 2: Write host tests**

```python
# tests/test_parallel_eval.py
import sys, pickle
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app import parallel_eval as pe
from app.strategies.base import get_registry
from app.indicators import precompute_all_indicators
from app.regime import classify_regime_series
from tests._adaptive_testutil import make_sessions


def _fixture_df():
    base = [100 + (i % 17) - (i % 5) * 0.7 for i in range(120)]
    return make_sessions([base, [x + 3 for x in base], [x - 2 for x in base]], start_date="2025-01-06")


def test_effective_workers_clamps_and_falls_back(monkeypatch):
    assert pe.effective_workers(1) == 1
    assert pe.effective_workers(0) == 1
    assert pe.effective_workers("x") == 1
    monkeypatch.setattr(pe, "fork_available", lambda: False)
    assert pe.effective_workers(8) == 1  # no fork -> sequential
    monkeypatch.setattr(pe, "fork_available", lambda: True)
    monkeypatch.setattr(pe.os, "cpu_count", lambda: 4)
    assert pe.effective_workers(8) == 3  # cpu-1
    monkeypatch.setenv("AF_OPT_WORKERS", "2")
    assert pe.effective_workers(8) == 2  # env cap


def test_worker_function_is_top_level_picklable():
    # A ProcessPoolExecutor submit requires the callable be importable by qualified name.
    assert pickle.loads(pickle.dumps(pe._worker_evaluate)) is pe._worker_evaluate


def test_worker_returns_sentinel_on_bad_params():
    get_registry().auto_discover()
    pe._RAW_DF = _fixture_df()
    pe._WORKER_CACHES = {}
    # A param dict that makes enrichment/backtest raise (e.g. nonsense ema length) -> (None, merged) sentinel, not a crash.
    metrics, merged = pe._worker_evaluate("confluence_scalper", {"ema_fast": -5}, None, "NIFTY", True, {})
    assert merged == {"ema_fast": -5}
    # metrics is None on failure OR a real dict if it happened to run; the contract is: never raises.


def test_parallel_backtest_sequential_fallback_equals_inprocess():
    # pool=None path: must equal a direct _worker_evaluate call, in order.
    get_registry().auto_discover()
    pe._RAW_DF = _fixture_df()
    pe._WORKER_CACHES = {}
    strat = get_registry().get("confluence_scalper")
    param_sets = [("confluence_scalper", strat.merged_params({}), None),
                  ("confluence_scalper", strat.merged_params({"ema_fast": 5, "ema_slow": 13}), None)]
    out = pe.parallel_backtest(None, param_sets, instrument="NIFTY", costs=True, pretrade={})
    assert len(out) == 2
    assert out[0][1] == param_sets[0][1] and out[1][1] == param_sets[1][1]  # order + merged preserved
    assert out[0][0] is not None and "trade_count" in out[0][0]
```

- [ ] **Step 3: Run — expect PASS (host; sequential fallback on Windows is fine)**

Run: `python -m pytest tests/test_parallel_eval.py -v`
Expected: all pass. (On Windows host, `fork_available()` is False so the bounding test's no-fork branch and the sequential `parallel_backtest(None, …)` path are exercised; on Linux the same tests pass identically.)

- [ ] **Step 4: Full host suite stays green**

Run: `python -m pytest tests/ -q`  → Expected: ~745+ passed.

- [ ] **Step 5: Commit**

```bash
git add backend/app/parallel_eval.py tests/test_parallel_eval.py
git commit -m "feat(optimizer): parallel_eval fork-pool helper (opt-in; sequential fallback) + host tests"
```

---

## PHASE 2 — Optimizer bayesian wiring

### Task 2: Read `opt_workers` from the payload

**Files:** Modify `backend/app/optimizer.py` (guard-params area, ~line 776-791).

- [ ] **Step 1: Add the payload read** next to the other guard params (after `optimize_indicator_periods = bool(payload.get("optimize_indicator_periods", False))`):

```python
        opt_workers = int(payload.get("opt_workers", 1) or 1)  # opt-in multi-core; 1 = sequential (default)
```

- [ ] **Step 2: py_compile**

Run: `python -m py_compile backend/app/optimizer.py` → no output.

- [ ] **Step 3: Commit**

```bash
git add backend/app/optimizer.py
git commit -m "feat(optimizer): read opt_workers payload knob (default 1)"
```

### Task 3: The `workers>1` batched branch

**Files:** Modify `backend/app/optimizer.py` — the bayesian `else` branch (the `objective_fn` + the `for i in range(completed, n_trials)` loop, ~lines 947-984) and the `finally`.

- [ ] **Step 1: Add the import + compute effective workers (bayesian-only) + inline sampler**

At the top imports add: `from app.parallel_eval import effective_workers, start_pool, shutdown_pool, parallel_backtest`.

**Parallelism is bayesian-only** — grid is already independent (untouched) and genetic/CMA-ES ask/tell is out of scope. Compute `_workers` ONCE, right after the study exists (after the resume-vs-fresh `if/else` that ends ~line 912, before `async def _maybe_pause`):

```python
        _workers = effective_workers(opt_workers) if method == "bayesian" else 1
```

For the FRESH-run study creation (~line 906-909), build the constant_liar sampler inline when parallel (resume uses `_rebuild_study` unchanged — a resumed parallel run uses the standard sampler for its remaining asks, which is acceptable: constant_liar only marginally reduces within-batch clustering):

```python
            _fresh_workers = effective_workers(opt_workers) if method == "bayesian" else 1
            _sampler = (optuna.samplers.TPESampler(seed=42, n_startup_trials=10, constant_liar=True)
                        if _fresh_workers > 1 else _make_sampler(method))
            study = optuna.create_study(
                direction="maximize", sampler=_sampler,
                study_name=f"alphaforge_{job_id}",
            )
```

(`_make_sampler(method)` is NOT modified — no `workers` param, no `study.set_sampler`. The constant_liar sampler is built inline here only.)

- [ ] **Step 2: Replace the bayesian `else` branch with a sequential/parallel split**

The current bayesian branch is the `else:` at line 947 (the `objective_fn` + the `for i in range(completed, n_trials)` loop through line 984). Wrap it:

```python
        elif _workers <= 1:
            # SEQUENTIAL (workers==1) — UNCHANGED. Do NOT refactor to ask/tell. (spec §4 byte-identical)
            def objective_fn(trial: optuna.Trial) -> float:
                params = _suggest(trial, space)
                metrics, merged = evaluate(params)
                val = obj(metrics)
                trial_history.append({"params": params, "metrics": metrics, "objective_value": round(val, 4)})
                return val
            for i in range(completed, n_trials):
                # ... existing loop body VERBATIM (lines 956-984) ...
        else:
            # PARALLEL (workers>1) — batched ask/tell; opt-in, non-deterministic.
            pool = start_pool(raw_df, _workers)   # None -> a concurrent parallel job is active; we run sequential-in-process
            try:
                prior = completed
                while completed < n_trials:
                    cf, pf = await _job_control(job_id)
                    if cf:
                        log.info(f"Job {job_id} cancelled by user at trial {completed}/{n_trials}")
                        break
                    if pf and await _maybe_pause():
                        return
                    B = min(_workers, n_trials - completed)
                    trials = [study.ask() for _ in range(B)]
                    param_list = [_suggest(t, space) for t in trials]
                    param_sets = [(strategy.id, strategy.merged_params(p), None) for p in param_list]
                    results = await asyncio.to_thread(
                        parallel_backtest, pool, param_sets,
                        instrument=instrument, costs=costs, pretrade=pretrade)
                    # Atomic flush: tell+append ALL in ask-order, THEN best, THEN checkpoint.
                    for trial, params, (metrics, _m) in zip(trials, param_list, results):
                        if metrics is None:
                            study.tell(trial, None, state=optuna.trial.TrialState.FAIL)
                        else:
                            val = obj(metrics)
                            study.tell(trial, val)
                            trial_history.append({"params": params, "metrics": metrics, "objective_value": round(val, 4)})
                    completed += B
                    try:
                        study_best_val = study.best_value
                        study_best_params = dict(study.best_params)
                    except Exception:
                        study_best_val = None
                        study_best_params = {}
                    if study_best_val is not None and study_best_val > best_so_far["value"]:
                        best_metrics = next((t["metrics"] for t in reversed(trial_history)
                                             if t["params"] == study_best_params), best_so_far["metrics"])
                        best_so_far = {"value": study_best_val, "params": study_best_params,
                                       "metrics": best_metrics, "trial_num": completed - 1}
                    # Boundary-crossing checkpoint (completed jumps by B).
                    if (completed // 5) > (prior // 5) or completed >= n_trials:
                        await _update_job(job_id, {
                            "n_trials_completed": completed,
                            "best_so_far": {"value": round(best_so_far["value"], 4), "params": best_so_far["params"], "metrics": best_so_far["metrics"], "trial_num": best_so_far["trial_num"]},
                        })
                    if (completed // 50) > (prior // 50):
                        await _flush_trial_log(job_id, trial_history, best_so_far, completed)
                    prior = completed
            finally:
                shutdown_pool()
```

(Keep the existing `if method == "grid":` branch first, then `elif _workers <= 1:` for the unchanged sequential bayesian, then `else:` for parallel. `strategy` is already in scope; `raw_df` is the per-job frame at ~line 808.)

- [ ] **Step 3: Guarantee pool teardown on ALL exits**

Wrap the whole worker body's existing outer `try` (the one at the top of `run_optimization`) so that `shutdown_pool()` also runs if the parallel branch is bypassed — simplest: the `finally: shutdown_pool()` above covers the parallel branch; additionally call `shutdown_pool()` once in the function's outermost `except`/finalize (the `except Exception` at ~line 1206) as a belt-and-suspenders no-op when no pool exists.

- [ ] **Step 4: py_compile**

Run: `python -m py_compile backend/app/optimizer.py` → no output.

- [ ] **Step 5: Commit**

```bash
git add backend/app/optimizer.py
git commit -m "feat(optimizer): opt-in parallel bayesian trials (batched ask/tell, inline constant_liar, boundary checkpoint)"
```

### Task 4: Container gate (the real verification)

**Files:** Create `tests/container/test_multicore_gate.py` (run via `docker exec`, NOT host pytest — it imports nothing forbidden but needs `fork` + a candle DB).

- [ ] **Step 1: byte-identity@1** — run an optimization with `opt_workers=1` and one with the pre-change code (or a captured golden) on a fixed seed/window; assert identical `trial_log`/`best_params`/`best_value`. (Drive via the running stack `POST /api/optimize/start`, both `opt_workers` absent and `=1`, compare jobs.)
- [ ] **Step 2: speedup@1/4/6** — `AF_OPT_TIMING=1`, run the same optimize at `opt_workers` 1/4/6 on a representative window; record wall-clock + `timing` + peak RSS. Confirm the speedup and that results stay within the disclosed divergence.
- [ ] **Step 3: resume@>1** — start an `opt_workers=4` run, pause mid-run, resume; confirm it completes with a coherent `trial_log` (ask-order preserved, no dup/orphan trials).
- [ ] **Step 4: cancel-mid-batch** — start `opt_workers=4`, cancel; confirm the job finalizes and no worker processes leak (`docker exec … ps`).
- [ ] **Step 5: Commit the gate script + record results in the commit message.**

```bash
git add tests/container/test_multicore_gate.py
git commit -m "test(optimizer): container multi-core gate (byte-identity@1, speedup@1/4/6, resume, cancel)"
```

---

## PHASE 3 — WFO inner-loop reuse

### Task 5: Parallelize WFO trials within each window

**Files:** Modify `backend/app/wfo.py` (inner window trial loop) — REQUIRES reading wfo.py's current inner loop first (it mirrors the optimizer's sequential loop).

- [ ] **Step 1:** Read `wfo.py`'s per-window inner trial loop and confirm: it loads ONE full job frame and the per-window train/test are **row-slices** of it (the §5 invariant). If WFO instead loads separate per-window frames, STOP and report — the single-pool/slice_bounds design assumes a single full frame.
- [ ] **Step 2:** Apply the **identical** `workers<=1` (unchanged) vs `workers>1` (batched ask/tell) split from Task 3 to the inner loop, with two differences: (a) `param_sets = [(strategy.id, strategy.merged_params(p), (train_a, train_b)) for p in param_list]` — pass the window's **train-slice bounds**; (b) the worker slices `_RAW_DF.iloc[train_a:train_b]` and MUST match the current sequential WFO's enrich/slice order exactly (byte-identical per-window eval). Call `start_pool(full_raw_df, _workers)` ONCE at the start of the WFO job (full frame), reuse across windows, `shutdown_pool()` in the job's `finally`.
- [ ] **Step 3:** Container-verify: a `opt_workers=4` WFO run yields the same window results (within disclosed divergence) and a measurable speedup vs `opt_workers=1`; window-granular resume still works.
- [ ] **Step 4:** Commit.

```bash
git add backend/app/wfo.py
git commit -m "feat(wfo): reuse parallel_eval for inner-window trials (opt-in, slice_bounds, single job pool)"
```

---

## Completion

After P1-P3 (or the chosen subset) pass + the container gate is green: **Use superpowers:finishing-a-development-branch** — verify host tests (`python -m pytest tests/...`), present merge/PR/keep options. Do NOT push without explicit instruction. The `opt_workers` UI control is Phase 4 (only if asked) — until then it's a payload + `AF_OPT_WORKERS` env power-user flag.
