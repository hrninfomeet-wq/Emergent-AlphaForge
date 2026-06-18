# Optimizer Multi-Core (opt-in parallel trials) — Design Spec

**Date:** 2026-06-18
**Branch:** `feat/optimizer-multicore` (off `feat/backtest-exit-controls` tip 23ee545)
**Status:** Design — pending adversarial audit + user review

---

## 1. Goal & honest framing

Make optimizer/WFO trial runs **~2.5-5× faster** by running the GIL-bound backtest trials across CPU cores, **strictly opt-in** (default = today's exact sequential path, byte-identical). This is a **speed / faster-iteration** feature — it does NOT improve test results or profitability and is not a quality lever; the trust layer (survival/WFO OOS) is unaffected because it re-evaluates chosen params, not the search trajectory.

**Why process-based:** today's `asyncio.to_thread` gives no multi-core benefit — the per-bar backtest loop (`backtest.py` `run_backtest`, `for i in range(len(df))` calling `strategy.evaluate`) is scalar Python under the GIL, so every trial runs on one core. After T7 memoization the per-trial cost is ~95% this bar-loop (measured: `bar_loop_s 12.3s` vs `precompute_s 0.6s`), so trial-level **process** parallelism is the correct and only meaningful lever. Phase-0 "measure first" is therefore already satisfied by the T7 timing.

**Runtime fit:** the optimizer only ever runs inside the Linux `python:3.11-slim` Docker container (sole run path), so `fork` is available. The dev host is Windows (pytest only) and never runs a worker.

## 2. Locked decisions (brainstorming 2026-06-18)

1. **Opt-in, default-off.** A new `opt_workers` knob (default 1). `opt_workers<=1` bypasses all new code → byte-identical to today.
2. **Lean scope.** ONE new module + TWO wiring sites + ONE knob. Everything else is CUT (see §9).
3. **Reproducibility tradeoff accepted** for the opt-in path: parallel ask/tell diverges ~0.5-2% run-to-run (`seed=42` stays intra-run deterministic, not cross-run identical). Surfaced as experimental/non-deterministic.
4. **Box:** HaroonPC, Core Ultra 7 255H (16 cores), 32 GB RAM, Docker Desktop/WSL2. Cores are ample; memory (WSL2 cap + recorded OOM-recycle on heavy 12-month runs) + laptop thermals are the real ceiling → conservative default worker count, opt up for lighter runs.

## 3. Architecture (lean)

**One new module `backend/app/parallel_eval.py`** (~100-130 lines, pure/host-importable — imports only `app.indicator_groups`, `app.backtest`, `app.strategies.base`; NOT server/optimizer/runtime). It exposes:

```
def effective_workers(requested: int) -> int      # bound: max(1, min(requested, (os.cpu_count() or 1) - 1, env_cap)); 1 if fork unavailable
def parallel_backtest(param_sets, *, raw_df, instrument, costs, pretrade) -> list[tuple[dict|None, dict]]
#   param_sets: list of (strategy_id, merged, slice_bounds) tuples
```
- `parallel_backtest` returns `[(metrics, merged), …]` in the SAME ORDER as `param_sets` (the implementation MUST re-key results to submission order — `ProcessPoolExecutor.map` preserves order; if `submit`+`as_completed` is used, re-sort by submission index before returning). When effective workers == 1 it runs the list **sequentially in-process** (zero pool, identical to today's per-trial path) — the fallback for `opt_workers<=1` AND when `fork` is unavailable.
- For workers > 1: a lazily-created `ProcessPoolExecutor(mp_context=multiprocessing.get_context("fork"))`. The FULL job frame `raw_df` is set as a **module global in the parent before the pool forks** (and is immutable for the job's life), so workers COW-inherit the multi-MB candle frame — it is **never pickled**.
- **Concrete worker (pin this — picklability is load-bearing):** a MODULE-LEVEL function in `parallel_eval.py`, picklable by qualified name (NEVER a closure inside `run_optimization`/`run_wfo` — a local function fails `ProcessPoolExecutor.submit`):
  ```
  def _worker_evaluate(strategy_id: str, merged: dict, slice_bounds: tuple|None,
                       instrument: str, costs: bool, pretrade: dict) -> tuple[dict|None, dict]:
      frame = _RAW_DF if slice_bounds is None else _RAW_DF.iloc[slice_bounds[0]:slice_bounds[1]]
      strategy = get_registry().get(strategy_id)            # registry is fork-inherited; strategy NOT pickled
      enr = enrich_with_cache(frame, merged, _worker_group_caches)   # per-process bounded cache
      res = run_backtest(enr, strategy, merged, instrument=instrument, costs_enabled=costs, pretrade_filters=pretrade)
      ... fold ce/pe counts as _evaluate does ... return (metrics, merged)
  ```
  It reads `_RAW_DF` from the module global (not an arg), re-derives the strategy from the registry, and returns the compact `(metrics, merged)` tuple — **never an Optuna object**. `param_sets` carry `strategy_id`, not strategy objects. `slice_bounds` is `None` for the optimizer path and `(train_a, train_b)` for WFO (see §5).
- Per-task `try/except` in the worker returns a sentinel `(None, merged)` on failure (mirrors today's `study.optimize(catch=(Exception,))`); the parent maps `None` metrics to the `_DISQUALIFY` objective and logs the offending params. One bad param set can't poison the batch.
- The pool is created lazily and **torn down in a `finally`** so a crashed/cancelled job never leaks workers.

**`_MAX_ENRICHED_CACHE` must move to a pure module.** It currently lives in `optimizer.py:92`, which imports `app.db` (server layer) — so a pure worker module importing it from `optimizer` would pull the DB layer into every worker, breaking import-purity. Move `_MAX_ENRICHED_CACHE` to `indicator_groups.py` (next to `enrich_with_cache`, reconciling its `max_per_group` default so the bound is single-sourced); `optimizer.py`, `wfo.py` (which already imports it from `optimizer.py:476-486`), and `parallel_eval.py` all import it from there.

**The parent keeps the Optuna study and does ALL ask/tell.** Workers are pure backtest evaluators. No study sharing, no RDB, no pickling of samplers. **Optuna 4.8.0 (in `requirements.txt`) confirmed to support** `study.ask()`, `study.tell(trial, value)` / `study.tell(trial, None, state=FAIL)`, and `TPESampler(constant_liar=True)` — no version bump needed.

## 4. Wiring site 1 — optimizer bayesian loop (`backend/app/optimizer.py`)

Today (bayesian branch, ~lines 924-960): `for i in range(...): study.optimize(objective_fn, n_trials=1)`.

New, gated on `workers = effective_workers(opt_workers)`:
- **`workers == 1`:** unchanged — the existing `study.optimize(n_trials=1)` loop runs verbatim. **Byte-identical.** (Also the path when `fork` is unavailable.)
- **`workers > 1`:** batched ask/tell with batch size `B = workers`:
  1. `trials = [study.ask() for _ in range(B)]` (respecting remaining `n_trials`).
  2. Build each trial's params via the existing `_suggest`-style logic from the trial object + `space`.
  3. `results = parallel_backtest(param_sets, raw_df=raw_df, …)` → `[(metrics, merged), …]` in ask-order.
  4. For each `(trial, metrics)` in ask-order: compute `val = obj(metrics)`; `study.tell(trial, val)`; append `{params, metrics, objective_value}` to `trial_history` **in ask-order**.
  5. After the whole batch is told+appended: recompute `best_so_far` from the batch; run a **boundary-crossing** checkpoint (see §6 — `completed` jumps by B, so a plain `% 5`/`% 50` can skip a boundary).
- Sampler: build the parallel sampler **inline in the `workers > 1` branch** at study creation — `optuna.samplers.TPESampler(seed=42, n_startup_trials=10, constant_liar=True)` — `constant_liar` measurably reduces within-batch clustering. **Do NOT add a `workers` parameter to `_make_sampler(method)`** and do NOT call `study.set_sampler()` — the `workers==1` study and sampler must be created by the unchanged existing code.

`opt_workers` is read from `payload.get("opt_workers", 1)` near the other guard params (~line 776).

**Implementation-discipline checklist (protects the `opt_workers<=1` byte-identical path — §9 scope contract):** during implementation the `workers > 1` logic lives in a **separate `if workers > 1:` branch**, never a merged/unified ask-tell path. Specifically: (a) keep the `workers==1` `study.optimize(n_trials=1)` loop verbatim; (b) build the `constant_liar` sampler inline in the `workers>1` branch, not via a `_make_sampler` param; (c) never `study.set_sampler()`; (d) keep the sequential `trial_history.append` inside `objective_fn` and the inline `% 5`/`% 50` checks unchanged — do NOT extract a shared checkpoint/append handler; (e) a pre-merge container test asserts `opt_workers=1` `trial_history`/`best` is byte-identical to pre-change `main` at a fixed seed/window.

## 5. Wiring site 2 — WFO inner loop (`backend/app/wfo.py`)

The per-window inner trial loop (`for i in range(n_trials_per_window)`) gets the **same** batched ask/tell against the **same** `parallel_backtest` helper, gated on the same `opt_workers`. Windows are still processed **sequentially** — we do NOT parallelize across windows (avoids core over-subscription and keeps window-granular resume unchanged).

**Single-pool invariant (no stale-window hazard):** `_RAW_DF` is the FULL job frame, set **once per job** before the pool forks, and is immutable. WFO passes each window's **train/test row-slice bounds** (`slice_bounds`) as task args; the worker slices the COW-shared full frame locally (`_RAW_DF.iloc[a:b]`). Workers capture **nothing window-specific at fork time** — they are pure functions of `(strategy_id, merged, slice_bounds)` — so a **single job-lifetime pool**, reused across all windows, is safe (no per-window pool recreation, no frame pickling). The worker MUST replicate the current sequential WFO's enrich/slice order exactly (byte-identical per-window evaluation); the plan confirms whether current WFO enriches-then-slices or slices-then-enriches and matches it. The pool is created lazily on the first `workers>1` batch and torn down in a `finally` at job end.

## 6. Cancel / pause / resume preservation

The **batch becomes the atomic unit**; every checkpoint stays a consistent `(trial_history, best_so_far, completed)` triple:
- `_job_control(job_id)` is read **once per batch** (not per in-flight trial). Cancel/pause latency coarsens from ~1 backtest to ~B backtests; with `B = workers` (small) the worst-case Stop wait is a few seconds. Documented.
- **Atomic batch flush (mechanically pinned):** collect ALL B results first; THEN loop in ask-order doing `study.tell(trial, val)` + `trial_history.append(...)`; THEN recompute `best_so_far` (only from trials appended this batch); THEN checkpoint. Never interleave a flush between tells. **Every asked trial must be told before any checkpoint** — so there are no orphaned asked-but-untold trials to corrupt `_rebuild_study` on resume.
- **Boundary-crossing checkpoint (workers>1):** since `completed` jumps by B, a plain `% 5`/`% 50` can skip a boundary (e.g. 48→52 skips the 50-flush). Use `if (completed // 5) > (prior // 5)` and `if (completed // 50) > (prior // 50)` (track `prior_completed` across batches), evaluated after the full batch is told+appended. The sequential (`workers==1`) path keeps its inline `% 5`/`% 50` checks **literally unchanged** (it increments by 1, never skips).
- **Ask-order invariant (critical):** `trial_history` is appended and `study.tell` is called in `study.ask()` order, so `_rebuild_study` (which replays via `study.add_trial` in order) reconstructs the sampler correctly on resume. This depends on `parallel_backtest` returning results in submission order (§3).
- **Pause mid-batch:** let the in-flight batch finish (do NOT kill workers), then `_maybe_pause()` flushes the consistent triple — identical resume semantics to today.
- **Cancel mid-batch:** likewise let the in-flight batch finish (so all asked trials are told), THEN break and flush; the existing fast-finalize (skip heatmap/robustness/walkforward) path is untouched. Never break between `ask` and `tell`.
- WFO keeps window-granular resume unchanged; the same per-batch ask-order + atomic-flush discipline applies within a window.

## 7. Memory / fork / OOM plan

- **Fork only in Linux Docker.** `parallel_eval` checks `"fork" in multiprocessing.get_all_start_methods()`; if absent (backend run natively on Windows), `effective_workers` returns 1 → transparent sequential fallback. No spawn path (avoids the pickle-everything cost + Windows-only complexity).
- **`raw_df` shared COW** via the parent module-global-before-fork pattern — never pickled. Per-task transfer = small param dict + compact metrics tuple.
- **Worker bound (static, no `psutil` in v1):** `effective_workers(requested) = max(1, min(requested, (os.cpu_count() or 1) - 1, AF_OPT_WORKERS_env_cap))`. `AF_OPT_WORKERS` (env) is an optional hard cap for memory/thermal safety. **Documented guidance for this box:** keep `opt_workers <= 6` for heavy 12-month runs (memory), can go higher for 1-3 month runs. Each worker holds its own bounded enriched cache (`_MAX_ENRICHED_CACHE`, now sourced from `indicator_groups.py`); **peak memory ≈ COW-shared raw_df (counted once) + parent enriched cache + workers × (per-worker enriched working set ~16-50MB)**. For `opt_workers=6` on a 12-month frame that is roughly `raw_df + 7 × working-set` — must be checked against the WSL2 container cap (recorded OOM-recycle history) before raising the documented default; `AF_OPT_WORKERS` is the safety valve. No per-worker-cap tunable is added in v1.
- **Lazy pool + `finally` shutdown** so cancelled/crashed jobs don't leak processes. Per-worker exception → sentinel (see §3).
- **Deferred:** dynamic RSS throttling (`psutil`) — only if the static ceiling proves insufficient. `psutil` is not added in v1.

## 8. Reproducibility

Parallel ask/tell breaks bit-identical reruns even with `seed=42` (worker completion order varies → ~0.5-2% trajectory divergence). `seed=42` remains *intra-run, given-history* deterministic. Mitigations: opt-in/default-off; `constant_liar` only when workers>1; an explicit "experimental / non-deterministic" label wherever the knob is surfaced. A promotion-quality final run can always be re-validated single-threaded (`opt_workers=1`); validation (survival/WFO OOS) re-evaluates chosen params, not the search trajectory, so trust is unaffected.

## 9. CUT for simplicity (the scope contract — do not add without measured evidence)

- ❌ Parallelizing heatmap / robustness (spot-only, post-opt, ~4-5s, cancel-skipped).
- ❌ Parallelizing option-rerank candidate sims (already loads contracts+candles once; residual ~1-5s).
- ❌ Parallelizing survival folds / exit-control grid (conditional, ~0.4-3s).
- ❌ Cross-process / distributed / Redis / Optuna RDBStorage WFO dispatch.
- ❌ Parallelizing ACROSS WFO windows (keep windows sequential; parallelize trials within a window).
- ❌ `psutil` dependency + live RSS-monitor loop in v1.
- ❌ ANY change to the `opt_workers<=1` default path (no batching, no constant_liar, no fork) — the trusted sequential path stays literally the current code.

## 10. Testing

- **`tests/test_parallel_eval.py` (host pytest):** tests `effective_workers` bounding (requested, cpu cap, env cap, fork-unavailable → 1), the worker exception → sentinel `(None, merged)` mapping, and that the function reference is **top-level/picklable by name**. **Honest limitation:** on the Windows dev host `fork` is absent → `effective_workers` returns 1 → the "parallel == sequential" equality assertion exercises only the **sequential fallback** (a tautology for the real fork path). It validates bounding + sentinel + fallback byte-identity, nothing more.
- **Container gate (THE real gate — run inside the Linux Docker container where `fork` exists, e.g. `docker exec … python -m pytest`; host numbers are invalid):**
  - (a) **byte-identity@1:** `opt_workers=1` `trial_history`/`best` is byte-identical to a golden captured from pre-change `main` at a fixed seed/window;
  - (b) **parallel==sequential:** `parallel_backtest` over a fixed param list equals the sequential result, in order, with real fork workers;
  - (c) **resume@>1:** a real paused→resumed `opt_workers>1` run replays correctly — no orphaned asked-but-untold trials, no duplicates, ask-order preserved through `_rebuild_study`;
  - (d) **cancel mid-batch:** a deliberate cancel mid-batch leaves no zombie/leaked workers (pool torn down in `finally`) and finalizes cleanly;
  - (e) **speedup:** measured wall-clock at workers 1/4/6 on a representative 12-month run (this is the only valid source of the speedup number) + peak RSS vs the WSL2 cap.
- Full host suite stays green: `python -m pytest tests/...` (must not import server/optimizer/runtime/paper_auto).

## 11. Risks & mitigations

- **Sampler-history corruption on resume** if append/tell order diverges from ask order → silently degrades (not crashes) resumed TPE. → ask-order is the single enforced invariant + a resume test.
- **Reproducibility erosion** → opt-in/default-off + label + trust-layer re-validation.
- **OOM on heavy runs** (recorded behavior) → conservative static ceiling, COW raw_df, bounded per-worker cache, opt-up-only.
- **Cancel/pause latency regression** (one batch) → small B = workers; documented.
- **Cross-process exception debugging** harder → per-worker try/except + sentinel + parent logging of offending params; pool torn down in finally.
- **Scope creep back to the maximal design** → §9 is the contract; revisit only with measured evidence.

## 12. Phasing

- **P1:** `backend/app/parallel_eval.py` + `tests/test_parallel_eval.py` (helper + fork pool + bounding + fallback + sentinel). No optimizer wiring yet.
- **P2:** wire the optimizer bayesian loop (batched ask/tell behind `opt_workers`; workers==1 byte-identical; constant_liar only when workers>1). Running-stack verify (identical@1, resume@>1, speedup measured).
- **P3:** reuse the helper for the WFO inner loop.
- **P4 (only if asked):** a labeled experimental `opt_workers` UI control in the optimizer form. Until then payload + `AF_OPT_WORKERS` env only.

## Out of scope / future
The bar-loop itself is not made faster (it's inherently sequential — position state carries bar-to-bar); multi-core parallelizes ACROSS trials, which is the right lever. Profitability levers (objective-misalignment fix, adaptive Plan 4) are separate work.
