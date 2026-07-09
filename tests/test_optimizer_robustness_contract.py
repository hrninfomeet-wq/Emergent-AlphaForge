"""Item #8 O13/O14 — robustness guards that live deep inside run_optimization
(no lightweight driver exists), pinned at the source level. HOST test (reads the
backend source directly; complements the O12 behaviour test in
test_parallel_eval_cache.py).
"""
from __future__ import annotations

from pathlib import Path

_BACKEND = Path(__file__).resolve().parents[1] / "backend"


def _src(rel: str) -> str:
    return (_BACKEND / rel).read_text(encoding="utf-8")


def test_o14_grid_evaluate_is_wrapped_and_disqualifies():
    # A single raising grid combo must not crash the whole job (resume then
    # re-hits it forever). The grid branch catches, disqualifies, and continues.
    src = _src("app/optimizer.py")
    assert "disqualified, continuing" in src
    # the disqualified record carries None metrics + the error, then continues
    assert '"metrics": None' in src and '"error": str(exc)' in src


def test_o13_analyze_stage_threads_should_stop():
    src = _src("app/optimizer.py")
    # one should_stop signal (cancel ∥ pause ∥ budget) defined once and threaded
    # into the survival loop AND the previously-ungoverned exit-control grid.
    assert "async def _analyze_should_stop" in src
    assert src.count("_analyze_should_stop()") >= 2   # survival loop + exit grid
    # the heatmap/robustness tail refreshes the cancel flag (was read once, stale)
    assert "cancelled_flag = await _is_cancelled(job_id)" in src


def test_o12_parallel_fallback_uses_a_local_cache():
    src = _src("app/parallel_eval.py")
    # the pool-None fallback must build a fresh per-call cache, not the global
    assert "local_caches" in src
    assert "_WORKER_CACHES if caches is None else caches" in src
