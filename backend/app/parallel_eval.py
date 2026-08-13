"""Opt-in parallel backtest evaluation for the optimizer/WFO trial loop.

The PARENT keeps the Optuna study and does all ask/tell; this module only runs
backtests in worker processes.

Workers use the SPAWN start method. They are fresh interpreters that inherit
nothing from the parent, because the parent is a live uvicorn server with ~30
threads and forking such a process is not safe: every forked child segfaults
immediately inside glibc (SIGSEGV reading `struct pthread->tid`), which the
pool reports as an opaque BrokenProcessPool before a single trial runs. See
docs/superpowers/specs/2026-08-13-optimizer-parallel-pool-spawn-design.md.

Because nothing is inherited, raw_df is PICKLED to each worker once at pool
construction (via the initializer) — never per task; per-task payloads stay
small. The workers also rebuild the strategy registry themselves.

Only ONE parallel job runs at a time (module-global pool). start_pool returns
None — and the caller runs sequentially with correct results — when a second
parallel job is already active, when workers<=1, or when the pool cannot be
started and verified. It never raises: an unusable pool must not kill a job.
"""
from __future__ import annotations
import os
import time
import logging
import threading
import multiprocessing
from concurrent.futures import ProcessPoolExecutor
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

from app.backtest import run_backtest
from app.indicator_groups import enrich_with_cache
from app.strategies.base import get_registry

log = logging.getLogger(__name__)

# Worker-process globals. Under spawn these are populated by _init_worker in
# each worker; the PARENT leaves them untouched (its sequential fallback passes
# the frame and a fresh cache dict explicitly).
_RAW_DF: Optional[pd.DataFrame] = None
_WORKER_CACHES: Dict[str, Dict] = {}

# Parent-process pool state (single active parallel job at a time).
_POOL: Optional[ProcessPoolExecutor] = None
_POOL_LOCK = threading.Lock()


def effective_workers(requested: Any) -> int:
    """Clamp the requested worker count to a safe value. 1 (sequential) when
    requested<=1. Spawn is available on every supported platform, so there is no
    start-method gate here; the cap is cpu-1, further capped by AF_OPT_WORKERS."""
    try:
        req = int(requested or 1)
    except (TypeError, ValueError):
        req = 1
    if req <= 1:
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


def _init_worker(raw_df: Optional[pd.DataFrame] = None) -> None:
    """Runs ONCE per spawned worker, before any task.

    A spawn worker is a fresh interpreter: it inherits neither the frame nor the
    strategy registry. The registry does NOT self-populate (server.py calls
    auto_discover() at startup), and _worker_evaluate deliberately swallows every
    exception and returns (None, merged) — so a worker with an empty registry
    would score EVERY trial as a failed measurement and the job would finish
    "successfully" with garbage. Discover eagerly here; _worker_selfcheck then
    proves it before any trial is spent.
    """
    global _RAW_DF, _WORKER_CACHES
    _WORKER_CACHES = {}
    if raw_df is not None:
        _RAW_DF = raw_df
    get_registry().auto_discover()


def _worker_selfcheck(strategy_id: Optional[str]) -> str:
    """Warmup task: proves a worker booted AND can resolve the strategy under
    optimization. Raising here breaks the pool loudly, which start_pool converts
    into a sequential fallback that produces CORRECT results — infinitely better
    than a worker that silently returns None for every trial."""
    if _RAW_DF is None:
        raise RuntimeError("spawn worker never received raw_df")
    if strategy_id and get_registry().get(strategy_id) is None:
        raise RuntimeError(f"spawn worker registry cannot resolve strategy {strategy_id!r}")
    return "ok"


def _worker_evaluate(strategy_id: str, merged: Dict[str, Any], slice_bounds: Optional[Tuple[int, int]],
                     instrument: str, costs: bool, pretrade: Dict[str, Any],
                     frame: Optional[pd.DataFrame] = None,
                     caches: Optional[Dict[str, Dict]] = None,
                     trade_window_start: Optional[str] = None,
                     trade_window_end: Optional[str] = None) -> Tuple[Optional[Dict[str, Any]], Dict[str, Any]]:
    """Top-level, picklable. In the POOL path, `frame`/`caches` are omitted and the
    worker reads the `_RAW_DF` set by `_init_worker` at spawn time + its own
    `_WORKER_CACHES` (the frame is shipped once per worker, never per task; the
    cache is created fresh per worker in `_init_worker`). In
    the SEQUENTIAL in-process path, the parent passes `frame` AND a fresh per-call
    `caches` dict explicitly — the module global must NEVER be used in the parent
    because it is keyed only on (group, params), not the frame, so a later job would
    index-align another frame's cached Series → NaN tails → wrong results (O12).
    trade_window_* (O6): entry window forwarded to run_backtest; None → its default.
    Returns (metrics|None, merged). Never raises — failure -> (None, merged)."""
    try:
        base = frame if frame is not None else _RAW_DF
        frame = base if slice_bounds is None else base.iloc[slice_bounds[0]:slice_bounds[1]]
        strategy = get_registry().get(strategy_id)
        enr = enrich_with_cache(frame, merged, _WORKER_CACHES if caches is None else caches)
        _tw = ({"trade_window_start": trade_window_start, "trade_window_end": trade_window_end}
               if trade_window_start and trade_window_end else {})
        res = run_backtest(enr, strategy, merged, instrument=instrument, costs_enabled=costs, pretrade_filters=pretrade, **_tw)
        metrics = dict(res["metrics"])
        trades = res.get("trades", []) or []
        metrics["pnl_abs_sum"] = float(sum(
            abs(float(t.get("pnl_pts", 0.0) or 0.0)) for t in trades
        ))
        ce = sum(1 for t in trades if str(t.get("direction", "")).upper() == "CE")
        metrics["ce_count"] = int(ce)
        metrics["pe_count"] = int(len(trades) - ce)
        return (metrics, merged)
    except Exception:
        return (None, merged)


def _worker_evaluate_wfo(strategy_id: str, merged: Dict[str, Any], slice_bounds: Tuple[int, int],
                         instrument: str, costs: bool, pretrade: Dict[str, Any],
                         frame: Optional[pd.DataFrame] = None,
                         caches: Optional[Dict[str, Dict]] = None,
                         trade_window_start: Optional[str] = None,
                         trade_window_end: Optional[str] = None) -> Tuple[Optional[Dict[str, Any]], Dict[str, Any]]:
    """WFO worker: enrich the FULL frame, THEN slice to the window, preserving
    indicator warmup — mirrors wfo._evaluate_slice (enrich-once-then-slice). The
    single-run _worker_evaluate slices RAW then enriches, which strips warmup at
    window starts, so WFO needs this distinct path. slice_bounds is REQUIRED.
    Reads the worker's _RAW_DF (or `frame` in the sequential fallback), and a fresh
    per-call `caches` in the fallback (never the frame-blind module global — O12).
    trade_window_* (O6): entry window forwarded to run_backtest; None → its default.
    Returns (metrics|None, merged); never raises."""
    try:
        base = frame if frame is not None else _RAW_DF
        enr = enrich_with_cache(base, merged, _WORKER_CACHES if caches is None else caches)
        a, b = slice_bounds
        df_slice = enr.iloc[a:b].reset_index(drop=True)
        strategy = get_registry().get(strategy_id)
        _tw = ({"trade_window_start": trade_window_start, "trade_window_end": trade_window_end}
               if trade_window_start and trade_window_end else {})
        res = run_backtest(df_slice, strategy, merged, instrument=instrument,
                           costs_enabled=costs, pretrade_filters=pretrade, **_tw)
        metrics = dict(res["metrics"])
        trades = res.get("trades", []) or []
        metrics["pnl_abs_sum"] = float(sum(
            abs(float(t.get("pnl_pts", 0.0) or 0.0)) for t in trades
        ))
        ce = sum(1 for t in trades if str(t.get("direction", "")).upper() == "CE")
        metrics["ce_count"] = int(ce)
        metrics["pe_count"] = int(len(trades) - ce)
        return (metrics, merged)
    except Exception:
        return (None, merged)


def start_pool(raw_df: pd.DataFrame, workers: int,
               strategy_id: Optional[str] = None) -> Optional[ProcessPoolExecutor]:
    """Create the spawn pool, shipping raw_df to each worker once via the
    initializer. Returns the pool, or None when a parallel job is already active,
    workers<=1, or the pool could not be started AND verified.

    NEVER raises. Every None path means "caller runs sequentially" — correct
    results, just slower. That matters: this used to raise BrokenProcessPool
    straight out of run_optimization and kill an 800-trial job outright.

    Workers are started eagerly inside the lock and each must pass
    _worker_selfcheck, so a pool that cannot evaluate is rejected here rather
    than silently scoring every trial as a failure."""
    global _POOL
    if workers <= 1:
        return None
    with _POOL_LOCK:
        if _POOL is not None:
            return None  # another parallel job owns the pool -> caller falls back to sequential
        pool = None
        try:
            ctx = multiprocessing.get_context("spawn")
            pool = ProcessPoolExecutor(max_workers=workers, mp_context=ctx,
                                       initializer=_init_worker, initargs=(raw_df,))
            t0 = time.perf_counter()
            # Start every worker NOW and prove it can resolve the strategy, before
            # any trial is spent on it. Spawn workers re-import the app, so this is
            # also where that one-time cost is paid — log it rather than assume it.
            list(pool.map(_worker_selfcheck, [strategy_id] * workers))
            log.info("parallel pool: %d spawn workers ready in %.1fs (rows=%d)",
                     workers, time.perf_counter() - t0, len(raw_df))
        except Exception as e:
            log.warning("parallel pool unavailable (%s: %s) — falling back to sequential; "
                        "results are unaffected, the run is just slower",
                        type(e).__name__, e)
            if pool is not None:
                try:
                    pool.shutdown(wait=False, cancel_futures=True)
                except Exception:
                    pass
            return None
        _POOL = pool
    return pool


def shutdown_pool() -> None:
    """Tear down the active pool (no-op if none). Call in the optimizer job's finally."""
    global _POOL
    with _POOL_LOCK:
        if _POOL is not None:
            _POOL.shutdown(cancel_futures=True)
            _POOL = None


def parallel_backtest(pool: Optional[ProcessPoolExecutor],
                      param_sets: List[Tuple[str, Dict[str, Any], Optional[Tuple[int, int]]]],
                      *, raw_df: pd.DataFrame, instrument: str, costs: bool, pretrade: Dict[str, Any],
                      worker=_worker_evaluate,
                      trade_window_start: Optional[str] = None,
                      trade_window_end: Optional[str] = None) -> List[Tuple[Optional[Dict[str, Any]], Dict[str, Any]]]:
    """Run param_sets [(strategy_id, merged, slice_bounds), …]. Results are returned
    in SUBMISSION ORDER. `worker` selects the evaluation function — defaults to the
    single-run _worker_evaluate (slice-raw-then-enrich); pass _worker_evaluate_wfo for
    WFO (enrich-full-then-slice, warmup-preserving). When pool is None, runs sequentially
    in-process passing `raw_df` as the frame explicitly (no module global -> concurrent-job
    safe) — the fallback for opt_workers<=1 / concurrent-job / a pool that failed to start.
    When pool is set, workers read the _RAW_DF their initializer installed (raw_df is
    shipped once per worker at pool construction, NOT pickled per task)."""
    if pool is None:
        # Sequential fallback runs IN THE PARENT. Use a FRESH cache dict per call so
        # a later job / different frame can never reuse another frame's cached Series
        # (the module global _WORKER_CACHES is keyed only on (group, params) → silent
        # index-align poisoning). Sharing within this one call is correct (same frame).
        local_caches: Dict[str, Dict] = {}
        return [worker(sid, m, sb, instrument, costs, pretrade, raw_df, local_caches,
                       trade_window_start, trade_window_end)
                for (sid, m, sb) in param_sets]
    futs = [pool.submit(worker, sid, m, sb, instrument, costs, pretrade,
                        trade_window_start=trade_window_start, trade_window_end=trade_window_end)
            for (sid, m, sb) in param_sets]
    return [f.result() for f in futs]  # iterated in submission order -> order preserved
