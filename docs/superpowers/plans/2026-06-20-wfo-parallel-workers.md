# WFO Parallel Workers — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development.
> Steps use checkbox (`- [ ]`) syntax. Host tests run from repo root:
> `python -m pytest tests/...`. Host tests must NEVER import server.py / optimizer.py /
> runtime.py / paper_auto.py. `parallel_eval.py` and `wfo.py`'s pure/`_evaluate_slice`
> functions ARE host-importable (they import optuna/optimizer lazily inside runners).

**Goal:** Make opt-in `opt_workers` parallel trial workers available in walk-forward
(WFO) mode, default-off and byte-identical when off.

**Architecture:** New WFO-correct fork worker (enrich-full→slice→backtest) in
`parallel_eval.py` proven byte-identical to `wfo._evaluate_slice`; a parallel
batched ask/tell per-window branch in `run_wfo` behind `use_parallel` that leaves the
sequential default path untouched; `opt_workers` on `WfoStartReq`; UI control + extra
reproducibility warning in the walk-forward panel.

**Tech Stack:** Python (FastAPI, Optuna, pandas/numpy, multiprocessing fork pool),
React (Optimizer.jsx), pytest.

Reference spec: `docs/superpowers/specs/2026-06-20-wfo-parallel-workers-design.md`.

---

### Task 1: WFO fork worker + `parallel_backtest` worker param (host-TDD)

**Files:**
- Modify: `backend/app/parallel_eval.py`
- Test: `tests/test_parallel_eval_wfo.py` (new)

- [ ] **Step 1: Write the failing parity test.** New file
  `tests/test_parallel_eval_wfo.py`. Mirror `tests/test_parallel_eval.py`'s header
  (`sys.path.insert(0, str(ROOT / "backend"))`, `from app import parallel_eval as pe`,
  `from app.strategies.base import get_registry`, `from tests._adaptive_testutil import
  make_sessions`). Build a multi-session fixture frame (≥3 sessions so a window can
  start at row > 0). Tests:
  - `test_worker_evaluate_wfo_is_top_level_picklable`: `pickle.loads(pickle.dumps(
    pe._worker_evaluate_wfo)) is pe._worker_evaluate_wfo`.
  - `test_worker_evaluate_wfo_matches_evaluate_slice`: the HARD parity gate. Enrich
    the full fixture via `from app.indicator_groups import enrich_with_cache`;
    `from app.wfo import _evaluate_slice`. Pick `(a, b)` with `a > 0` (e.g. start of
    the 2nd session). `merged = strat.merged_params({})`.
    `ref_metrics, ref_trades = _evaluate_slice(enr_full, a, b, strat, merged, "NIFTY",
    True, {})`. `pe._RAW_DF = raw_df; pe._WORKER_CACHES = {}`;
    `got_metrics, got_merged = pe._worker_evaluate_wfo("confluence_scalper", merged,
    (a, b), "NIFTY", True, {})`. Assert `got_merged == merged`, `got_metrics ==
    ref_metrics` (full dict equality, incl. `ce_count`/`pe_count`).
  - `test_worker_evaluate_wfo_preserves_warmup_vs_slice_then_enrich`: documents WHY
    the new worker exists. Compute the WRONG way (slice raw THEN enrich) via the
    existing `pe._worker_evaluate("confluence_scalper", merged, (a, b), ...)` on the
    same `_RAW_DF`, and assert it produces DIFFERENT metrics from `_worker_evaluate_wfo`
    for the `a > 0` window (warmup is preserved only by the new worker). If they happen
    to match for the chosen strategy/window, pick a window/strategy where an indicator
    with a longer lookback makes warmup matter; assert difference.
  - `test_worker_evaluate_wfo_never_raises`: nonsense param → `(None, merged)`.
  - `test_parallel_backtest_uses_worker_param`: `pe.parallel_backtest(None, param_sets,
    worker=pe._worker_evaluate_wfo, raw_df=df, ...)` where `param_sets` carry `(a, b)`
    bounds → real backtests in submission order; cross-check element 0 equals a direct
    `_worker_evaluate_wfo` call.

- [ ] **Step 2: Run tests — expect FAIL** (`_worker_evaluate_wfo` undefined / no
  `worker` param). Run: `python -m pytest tests/test_parallel_eval_wfo.py -v`.

- [ ] **Step 3: Implement.** In `parallel_eval.py`:
  ```python
  def _worker_evaluate_wfo(strategy_id, merged, slice_bounds, instrument, costs,
                           pretrade, frame=None):
      """WFO worker: enrich the FULL frame, THEN slice to the window, preserving
      indicator warmup — mirrors wfo._evaluate_slice (enrich-once-then-slice). The
      single-run _worker_evaluate slices RAW then enriches, which strips warmup at
      window starts, so WFO needs this distinct path. slice_bounds is REQUIRED.
      Reads fork-inherited _RAW_DF (or `frame` in the sequential fallback). Returns
      (metrics|None, merged); never raises."""
      try:
          base = frame if frame is not None else _RAW_DF
          enr = enrich_with_cache(base, merged, _WORKER_CACHES)
          a, b = slice_bounds
          df_slice = enr.iloc[a:b].reset_index(drop=True)
          strategy = get_registry().get(strategy_id)
          res = run_backtest(df_slice, strategy, merged, instrument=instrument,
                             costs_enabled=costs, pretrade_filters=pretrade)
          metrics = dict(res["metrics"])
          trades = res.get("trades", []) or []
          ce = sum(1 for t in trades if str(t.get("direction", "")).upper() == "CE")
          metrics["ce_count"] = int(ce)
          metrics["pe_count"] = int(len(trades) - ce)
          return (metrics, merged)
      except Exception:
          return (None, merged)
  ```
  Add a `worker` param to `parallel_backtest` (default `_worker_evaluate`), used in
  both branches:
  ```python
  def parallel_backtest(pool, param_sets, *, raw_df, instrument, costs, pretrade,
                        worker=_worker_evaluate):
      if pool is None:
          return [worker(sid, m, sb, instrument, costs, pretrade, raw_df)
                  for (sid, m, sb) in param_sets]
      futs = [pool.submit(worker, sid, m, sb, instrument, costs, pretrade)
              for (sid, m, sb) in param_sets]
      return [f.result() for f in futs]
  ```
  (`worker=_worker_evaluate` as a default arg requires `_worker_evaluate` be defined
  ABOVE `parallel_backtest` — it already is; define `_worker_evaluate_wfo` near it.)

- [ ] **Step 4: Run tests — expect PASS.** Also run the existing
  `python -m pytest tests/test_parallel_eval.py -v` to prove the default-`worker`
  path is unchanged.

- [ ] **Step 5: Commit** (pathspec):
  `git add backend/app/parallel_eval.py tests/test_parallel_eval_wfo.py`
  `git commit -m "feat(parallel): WFO-correct fork worker (enrich-full→slice) + worker param"`

---

### Task 2: `opt_workers` on `WfoStartReq`

**Files:**
- Modify: `backend/app/schemas.py` (WfoStartReq, ~line 247)
- Test: `tests/test_wfo_schema.py` (new, or extend an existing schema test)

- [ ] **Step 1: Failing test.** Assert `WfoStartReq(strategy_id="x").opt_workers == 1`
  and that an explicit `opt_workers=4` round-trips through `.model_dump()`. Host-import
  pattern: `sys.path.insert(0, str(ROOT / "backend"))`; `from app.schemas import
  WfoStartReq`. (schemas.py is host-importable — it imports only pydantic/typing.)

- [ ] **Step 2: Run — expect FAIL** (`opt_workers` not a field; model_dump lacks it).

- [ ] **Step 3: Implement.** Add after `max_windows: int = 12` (line 247):
  ```python
  opt_workers: int = 1  # opt-in parallel trial workers (bayesian per-window); 1 = sequential
  ```

- [ ] **Step 4: Run — expect PASS.**

- [ ] **Step 5: Commit:**
  `git add backend/app/schemas.py tests/test_wfo_schema.py`
  `git commit -m "feat(schema): opt_workers on WfoStartReq"`

---

### Task 3: Parallel per-window branch in `run_wfo`

**Files:**
- Modify: `backend/app/wfo.py` (`run_wfo`, lazy-import block ~476-488; per-window loop
  ~615-690)

No new host test (run_wfo needs optuna + DB; covered by Task 5 stack verification and
the Task 1 worker parity gate). Keep the change tightly scoped.

- [ ] **Step 1: Extend the lazy import block** (inside `run_wfo`, ~line 476) to add:
  ```python
  from app.parallel_eval import (
      effective_workers, start_pool, shutdown_pool, parallel_backtest,
      _worker_evaluate_wfo,
  )
  ```

- [ ] **Step 2: Read opt_workers + decide parallelism** (after `method` is resolved,
  ~line 500, AFTER the `if method == "grid": method = "bayesian"` coercion):
  ```python
  opt_workers = int(payload.get("opt_workers", 1) or 1)
  _workers = effective_workers(opt_workers) if method == "bayesian" else 1
  ```

- [ ] **Step 3: Start the pool once + wrap the windows loop in try/finally.** Just
  before `cancelled = False` / the `for w in windows[...]` loop:
  ```python
  pool = start_pool(df, _workers) if _workers > 1 else None
  use_parallel = pool is not None
  if use_parallel:
      await _update_job(job_id, {"opt_workers_effective": _workers})
  try:
      cancelled = False
      for w in windows[len(completed_windows):]:
          ... (existing body, modified per Step 4/5) ...
      ... (existing final-analysis block stays INSIDE or AFTER try; keep it after) ...
  finally:
      shutdown_pool()
  ```
  Implementer note: scope the `try/finally` so `shutdown_pool()` always runs even on
  the early `return` paths (pause). The simplest correct shape: wrap from pool-start
  through the end of the windows `for` loop; the pause-path `return` inside still
  triggers `finally`. The final-analysis block can stay after the `finally` (pool no
  longer needed once windows are done).

- [ ] **Step 4: Sampler selection per window.** Replace
  `study = optuna.create_study(direction="maximize", sampler=_make_sampler(method))`
  (~line 619) with:
  ```python
  _wf_sampler = (optuna.samplers.TPESampler(seed=42, n_startup_trials=10,
                                            constant_liar=True)
                 if use_parallel else _make_sampler(method))
  study = optuna.create_study(direction="maximize", sampler=_wf_sampler)
  ```

- [ ] **Step 5: Branch the per-window trial loop.** Keep `window_best` as today.
  Replace the existing `paused = False` + `for i in range(n_trials_per_window): ...`
  block (~633-648) with a branch:
  ```python
  paused = False
  if not use_parallel:
      # SEQUENTIAL — UNCHANGED (byte-identical / deterministic default path).
      for i in range(n_trials_per_window):
          cf, pf = await _job_control(job_id)
          if cf:
              cancelled = True
              break
          if pf:
              paused = True
              break
          await asyncio.to_thread(study.optimize, objective_fn, n_trials=1,
                                  catch=(Exception,))
          if (i + 1) % 5 == 0 or i == n_trials_per_window - 1:
              await _update_job(job_id, {
                  "wfo_progress": {"window": w["index"] + 1, "window_count": len(windows),
                                   "trial": i + 1, "trials_per_window": n_trials_per_window},
                  "n_trials_completed": w["index"] * n_trials_per_window + i + 1,
              })
  else:
      # PARALLEL — opt-in batched ask/tell; non-deterministic. window_best updated
      # explicitly from results (objective_fn's closure-mutation is bypassed here).
      done = 0
      while done < n_trials_per_window:
          cf, pf = await _job_control(job_id)
          if cf:
              cancelled = True
              break
          if pf:
              paused = True
              break
          B = min(_workers, n_trials_per_window - done)
          trials = [study.ask() for _ in range(B)]
          param_list = [_suggest(t, space) for t in trials]
          param_sets = [(strategy.id, strategy.merged_params(p), (tr_a, tr_b))
                        for p in param_list]
          results = await asyncio.to_thread(
              parallel_backtest, pool, param_sets, raw_df=df, instrument=instrument,
              costs=costs, pretrade=pretrade, worker=_worker_evaluate_wfo)
          for trial, params, (metrics, _m) in zip(trials, param_list, results):
              if metrics is None:
                  study.tell(trial, None, state=optuna.trial.TrialState.FAIL)
              else:
                  val = obj(metrics)
                  study.tell(trial, val)
                  if val > window_best["value"]:
                      window_best.update({"value": val, "params": dict(params),
                                          "metrics": metrics})
          prev = done
          done += B
          if (done // 5) > (prev // 5) or done >= n_trials_per_window:
              await _update_job(job_id, {
                  "wfo_progress": {"window": w["index"] + 1, "window_count": len(windows),
                                   "trial": done, "trials_per_window": n_trials_per_window},
                  "n_trials_completed": w["index"] * n_trials_per_window + done,
              })
  ```
  Notes for the implementer:
  - `tr_a, tr_b` (train-window row bounds) are already computed at the top of the
    window body — reuse them; the worker slices to the TRAIN window (OOS evaluation
    after the loop still uses the unseen test window via `_evaluate_slice`, unchanged).
  - `strategy.id` / `strategy.merged_params` / `_suggest` / `space` / `obj` are all in
    scope already.
  - Do NOT alter `objective_fn` (still used by the sequential branch).
  - The post-loop `if paused: ... return` and `if cancelled: ... break` and the
    `window_best["value"] <= _DISQUALIFY` handling are UNCHANGED.

- [ ] **Step 6: Sanity import.** From repo root, confirm the module still parses:
  `python -c "import ast; ast.parse(open('backend/app/wfo.py').read())"`. (Do not import
  it on the host — it pulls optuna; rely on container for runtime.)

- [ ] **Step 7: Commit:**
  `git add backend/app/wfo.py`
  `git commit -m "feat(wfo): opt-in parallel per-window trial workers (default byte-identical)"`

---

### Task 4: Frontend — control in WFO panel + payload + warning

**Files:**
- Modify: `frontend/src/pages/Optimizer.jsx` (WFO panel ~681-728; WFO payload ~363-392)

- [ ] **Step 1: Add the control to the walk-forward panel.** Inside the
  `config.run_kind === "walkforward"` box (the `grid grid-cols-2` at ~684, or a new row
  after Max windows), add:
  ```jsx
  <div>
    <Label className="text-[11px] text-dim">Parallel workers</Label>
    <Input
      type="number" min={1} max={15}
      value={config.opt_workers}
      onChange={(e) => setConfig({ ...config, opt_workers: e.target.value })}
      className="bg-bg-2 border-line h-8 text-xs font-mono mt-1"
      data-testid="opt-wf-parallel-workers"
    />
  </div>
  ```
  And below the grid (near the existing note at ~726), add the reproducibility warning
  shown only when workers > 1:
  ```jsx
  {Number(config.opt_workers) > 1 && (
    <div className="text-[10px] text-warning mt-1 leading-snug" data-testid="opt-wf-workers-warning">
      experimental · non-deterministic · more RAM. With parallel workers the OOS
      result and deployable params can vary run-to-run — walk-forward is your
      honest-OOS validation; use 1 worker for a reproducible deploy decision.
    </div>
  )}
  ```

- [ ] **Step 2: Send `opt_workers` in the WFO payload.** In the
  `if (config.run_kind === "walkforward")` payload object (~364), add:
  ```js
  opt_workers: Number(config.opt_workers) || 1,
  ```

- [ ] **Step 3: Verify the frontend builds.** `cd frontend && npm run build` (or rely
  on the Docker rebuild in Task 5). Expect no new eslint/build errors.

- [ ] **Step 4: Commit:**
  `git add frontend/src/pages/Optimizer.jsx`
  `git commit -m "feat(optimizer-ui): parallel workers in walk-forward panel + OOS reproducibility warning"`

---

### Task 5: Running-stack verification (controller-run)

**Files:** none (verification only).

- [ ] **Step 1: Rebuild** backend + frontend: `docker compose up -d --build backend
  frontend`; confirm `/api/health` `db:ok` and frontend HTTP 200.
- [ ] **Step 2: Baseline (opt_workers=1).** Start a small WFO run on NIFTY (solid
  window 2025-10→2026-06, small train/test/trials so it finishes fast) with
  `opt_workers: 1`. Capture the finished `wfo.stitched_oos`, `efficiency`,
  `consistency`, `best_params`, `wfo.final_params`.
- [ ] **Step 3: Parallel (opt_workers=4).** Start the SAME WFO config with
  `opt_workers: 4`. Confirm: job completes `done`; `opt_workers_effective` present;
  produces a valid `wfo` block; per-window progress advanced; wall-clock < baseline
  (or note if the run is too small to show a gain). Determinism caveat acknowledged
  (results need not match baseline — that is the documented tradeoff).
- [ ] **Step 4: Determinism invariant spot-check.** Run the baseline (opt_workers=1)
  config TWICE; confirm identical `stitched_oos.total_pnl_pts` + identical
  `final_params` (reproducible default). Optionally diff a `opt_workers=1` result
  against the pre-change code path is unnecessary — the sequential branch is unchanged.
- [ ] **Step 5: UI check.** In the Optimizer, Run type → Walk-forward: confirm the
  "Parallel workers" field renders, the warning appears when set > 1, and starting a
  run sends `opt_workers` (network payload / job `config.opt_workers`).
- [ ] **Step 6: Full host test suite** green from repo root: `python -m pytest tests/ -q`.
- [ ] **Step 7: finishing-a-development-branch** — present options; do NOT merge/push
  without explicit user instruction (standing per-changeset approval).
