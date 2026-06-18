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


def _noop(_x: int) -> int:
    return 0


def _worker_evaluate(strategy_id: str, merged: Dict[str, Any], slice_bounds: Optional[Tuple[int, int]],
                     instrument: str, costs: bool, pretrade: Dict[str, Any]) -> Tuple[Optional[Dict[str, Any]], Dict[str, Any]]:
    """Top-level, picklable. Reads _RAW_DF from the (fork-inherited) module global;
    re-derives the strategy from the fork-inherited registry. Returns (metrics|None, merged).
    Never raises — a failure returns (None, merged), mirroring study.optimize(catch=Exception)."""
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
    parallel job is already active OR workers<=1 OR fork is unavailable (caller then
    runs sequentially). Forks workers eagerly (warmup) inside the lock so they
    snapshot _RAW_DF before it can be reassigned by a concurrent job."""
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
    """Tear down the active pool (no-op if none). Call in the optimizer job's finally."""
    global _POOL
    with _POOL_LOCK:
        if _POOL is not None:
            _POOL.shutdown(cancel_futures=True)
            _POOL = None


def parallel_backtest(pool: Optional[ProcessPoolExecutor],
                      param_sets: List[Tuple[str, Dict[str, Any], Optional[Tuple[int, int]]]],
                      *, instrument: str, costs: bool, pretrade: Dict[str, Any]) -> List[Tuple[Optional[Dict[str, Any]], Dict[str, Any]]]:
    """Run param_sets [(strategy_id, merged, slice_bounds), …]. Results are returned
    in SUBMISSION ORDER. When pool is None, runs sequentially in-process (identical
    to the per-trial path) — the fallback for opt_workers<=1 / fork-unavailable /
    concurrent-job."""
    if pool is None:
        return [_worker_evaluate(sid, m, sb, instrument, costs, pretrade) for (sid, m, sb) in param_sets]
    futs = [pool.submit(_worker_evaluate, sid, m, sb, instrument, costs, pretrade) for (sid, m, sb) in param_sets]
    return [f.result() for f in futs]  # iterated in submission order -> order preserved
