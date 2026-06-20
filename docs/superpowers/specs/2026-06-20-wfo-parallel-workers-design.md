# WFO Parallel Workers — Design

**Date:** 2026-06-20
**Branch:** `feat/wfo-parallel-workers`
**Status:** Approved (determinism decision made — see §3)

## 1. Goal

Make the optimizer's opt-in parallel trial workers (`opt_workers`) available in
**walk-forward (WFO) mode**, not only single-run mode. Today the "Parallel
workers" UI control is gated to single-run Bayesian, and the WFO backend ignores
`opt_workers` entirely — its per-window trial search is strictly sequential.

## 2. Why this is a real backend change (not just un-hiding the UI)

`run_wfo` ([backend/app/wfo.py](../../../backend/app/wfo.py)) runs each window's
trials one at a time: `await asyncio.to_thread(study.optimize, objective_fn,
n_trials=1, ...)` in a Python `for` loop. It never reads `opt_workers`. Simply
showing the UI field would be a lie. The trial search must be parallelized,
mirroring the proven single-run branch in `run_optimization`
([backend/app/optimizer.py:1060-1116](../../../backend/app/optimizer.py)).

### The warmup constraint (the critical correctness risk)

WFO's correctness depends on **enrich the full frame ONCE, then slice to the
window**: `_evaluate_slice` does `enr_full.iloc[a:b].reset_index(drop=True)`
([wfo.py:354-369](../../../backend/app/wfo.py)). The module docstring is explicit
— indicators are causal, so slicing the *enriched* frame preserves realistic
warmup history and leaks no future data into a train window.

The existing single-run fork worker `_worker_evaluate` does the **opposite** —
it slices the *raw* frame then enriches (`base.iloc[slice_bounds]` →
`enrich_with_cache(frame)`, [parallel_eval.py:73-77](../../../backend/app/parallel_eval.py)).
For a WFO window starting at row > 0 this strips indicator warmup at the window
boundary and silently changes results. **Therefore the single-run worker cannot
be reused for WFO.** We add a dedicated WFO worker that does enrich-full →
slice → `reset_index` → backtest, and pin it byte-identical to `_evaluate_slice`
with a host test before any wiring.

## 3. Determinism decision (made)

Sequential WFO today is **fully reproducible**: `_make_sampler("bayesian")` is
`TPESampler(seed=42)` run serially. Parallel trial workers use batched ask/tell
with `constant_liar`, where tell-order depends on worker completion timing →
**non-deterministic**. Since WFO is the honest-OOS, deploy-decision validation,
turning `opt_workers > 1` on means the stitched OOS P&L and the deployable params
can vary run-to-run.

**Decision: opt-in, default deterministic, with an extra WFO-specific warning.**
- `opt_workers = 1` (default) → the existing sequential path, untouched,
  byte-identical and reproducible.
- `opt_workers > 1` → parallel + non-deterministic, with the standard
  "experimental · non-deterministic · more RAM" note **plus** an explicit
  walk-forward note that OOS results become non-reproducible when > 1.

## 4. Architecture

### 4.1 `parallel_eval.py` — new WFO worker

- `_worker_evaluate_wfo(strategy_id, merged, slice_bounds, instrument, costs,
  pretrade, frame=None)` — top-level, picklable. Reads the fork-inherited
  full-frame `_RAW_DF` (or `frame` in the sequential fallback), enriches the
  **full** frame via `enrich_with_cache(base, merged, _WORKER_CACHES)`, then
  `enr.iloc[a:b].reset_index(drop=True)`, then `run_backtest`. Folds in
  `ce_count`/`pe_count` exactly like `_worker_evaluate` and `_evaluate_slice`.
  Never raises → `(None, merged)` on exception. `slice_bounds` is required.
- `parallel_backtest` gains an optional `worker=_worker_evaluate` parameter used
  in BOTH the pool and the sequential-fallback branches. Existing callers pass
  nothing → default `_worker_evaluate` → byte-identical. WFO passes
  `worker=_worker_evaluate_wfo`.

### 4.2 `wfo.py` — parallel per-window branch

- Read `opt_workers = int(payload.get("opt_workers", 1) or 1)`.
- `_workers = effective_workers(opt_workers) if method == "bayesian" else 1`
  (grid is already coerced to bayesian; genetic stays sequential, mirroring
  single-run which gates parallel on bayesian).
- `pool = start_pool(df, _workers) if _workers > 1 else None`;
  `use_parallel = pool is not None`. The pool is started ONCE before the windows
  loop (the full frame `df` is COW-shared and valid for every window) and torn
  down in a `finally` via `shutdown_pool()`. If `start_pool` returns `None`
  (a concurrent parallel job already owns the pool, or no fork), `use_parallel`
  is `False` → full sequential fallback (still deterministic).
- Per window, the study sampler depends on `use_parallel`:
  `TPESampler(seed=42, n_startup_trials=10, constant_liar=True)` when parallel,
  else `_make_sampler(method)` (unchanged).
- Per window:
  - `use_parallel == False` → the **existing sequential loop, unchanged**
    (byte-identical invariant).
  - `use_parallel == True` → batched ask/tell, mirroring the single-run parallel
    branch: while `done < n_trials_per_window`, check cancel/pause per batch;
    `B = min(_workers, remaining)`; `study.ask()` × B → `_suggest` →
    `param_sets = [(strategy.id, merged, (tr_a, tr_b)) for ...]` →
    `parallel_backtest(pool, param_sets, worker=_worker_evaluate_wfo,
    raw_df=df, ...)`; then `study.tell` in ask-order (FAIL on `None`), update
    `window_best` from `(val, metrics)` directly, persist `wfo_progress` on the
    same 5-trial cadence.
- Everything after the per-window trial loop (OOS evaluation on `window_best`,
  `no_qualifying_params`/`_DISQUALIFY` handling, persistence, pause/cancel
  post-handling, final stitch/analysis) is unchanged — both branches feed the
  same `window_best` shape.

### 4.3 Schema + frontend

- `WfoStartReq` gains `opt_workers: int = 1` (flows via `req.model_dump()` into
  the WFO payload).
- `Optimizer.jsx`: render the "Parallel workers" control inside the walk-forward
  config panel (`data-testid="opt-wf-parallel-workers"`), bound to the existing
  shared `config.opt_workers`, with the extra WFO reproducibility note. Add
  `opt_workers: Number(config.opt_workers) || 1` to the WFO payload.

## 5. Invariants / acceptance

1. **Byte-identical default.** `opt_workers <= 1` → WFO behaviour (sampler, loop,
   results, persistence) is exactly as today. Proven by: the sequential branch is
   literally unchanged; host parity test on the worker; stack baseline run.
2. **Warmup parity.** `_worker_evaluate_wfo(..., (a,b))` == `_evaluate_slice(
   enrich_full, a, b)` for a window with `a > 0`. Host test (the hard gate).
3. **Pause/resume/cancel** still operate at window granularity; in-window
   parallel checks happen per batch.
4. **No pool leak** — `shutdown_pool()` in `finally`; single-active-pool guard
   (concurrent job → sequential fallback) preserved.
5. **RAM** — each worker enriches the full frame (own `_WORKER_CACHES`); peak ≈
   single-run's parallel profile. Covered by the "more RAM" note.

## 6. Out of scope

- Cross-window parallelism (windows stay sequential — resume/progress/deployable-
  params-from-last-window semantics depend on it).
- Parallelizing the final analysis / option-aware OOS pairing.
- Genetic-method parallelism (stays sequential, like single-run).
- Any change to the single-run parallel path.
