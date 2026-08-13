import sys
import pickle
from pathlib import Path
from concurrent.futures.process import BrokenProcessPool

import pytest
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app import parallel_eval as pe
from app.indicator_groups import enrich_with_cache
from app.optimizer import _evaluate
from app.strategies.base import get_registry
from tests._adaptive_testutil import make_sessions


def _fixture_df():
    base = [100 + (i % 17) - (i % 5) * 0.7 for i in range(120)]
    return make_sessions([base, [x + 3 for x in base], [x - 2 for x in base]], start_date="2025-01-06")


def test_effective_workers_clamps_and_falls_back(monkeypatch):
    assert pe.effective_workers(1) == 1
    assert pe.effective_workers(0) == 1
    assert pe.effective_workers("x") == 1
    monkeypatch.setattr(pe.os, "cpu_count", lambda: 4)
    assert pe.effective_workers(8) == 3  # cpu-1
    monkeypatch.setenv("AF_OPT_WORKERS", "2")
    assert pe.effective_workers(8) == 2  # env cap


def test_pool_uses_spawn_not_fork():
    """The parent is a ~30-thread uvicorn server; forking it segfaults every child
    in glibc before a single trial runs. Spawn is the whole point of this module —
    guard it so nobody 'optimises' it back to fork for the cheaper startup."""
    src = (ROOT / "backend" / "app" / "parallel_eval.py").read_text(encoding="utf-8")
    assert 'get_context("spawn")' in src
    assert 'get_context("fork")' not in src


def test_worker_function_is_top_level_picklable():
    # ProcessPoolExecutor.submit requires the callable be importable by qualified name.
    assert pickle.loads(pickle.dumps(pe._worker_evaluate)) is pe._worker_evaluate


def test_worker_never_raises_returns_merged():
    get_registry().auto_discover()
    pe._RAW_DF = _fixture_df()
    pe._WORKER_CACHES = {}
    # Nonsense param must not crash the worker — contract is (metrics|None, merged), never an exception.
    metrics, merged = pe._worker_evaluate("confluence_scalper", {"ema_fast": -5}, None, "NIFTY", True, {})
    assert merged == {"ema_fast": -5}
    assert metrics is None or isinstance(metrics, dict)


def test_single_run_worker_matches_serial_optimizer_metrics():
    """Risk objectives require the exact same unitless drawdown basis in both paths."""
    get_registry().auto_discover()
    raw_df = _fixture_df()
    strategy = get_registry().get("confluence_scalper")
    merged = strategy.merged_params({})
    serial, serial_merged = _evaluate(
        lambda params: enrich_with_cache(raw_df, params, {}),
        strategy, merged, "NIFTY", True, {},
    )
    assert serial["trade_count"] > 0

    pe._RAW_DF = raw_df
    pe._WORKER_CACHES = {}
    worker, worker_merged = pe._worker_evaluate(
        "confluence_scalper", merged, None, "NIFTY", True, {},
    )

    assert worker_merged == serial_merged
    assert worker == serial
    assert worker["pnl_abs_sum"] > 0


def test_parallel_backtest_sequential_fallback_in_order():
    # pool=None path: frame passed explicitly via raw_df (no module global). Results
    # are real backtests in submission order — regression guard for the _RAW_DF bug
    # the container gate caught (sequential fallback returned all-None sentinels).
    get_registry().auto_discover()
    pe._RAW_DF = None  # prove the sequential path does NOT depend on the global
    pe._WORKER_CACHES = {}
    df = _fixture_df()
    strat = get_registry().get("confluence_scalper")
    param_sets = [("confluence_scalper", strat.merged_params({}), None),
                  ("confluence_scalper", strat.merged_params({"ema_fast": 5, "ema_slow": 13}), None)]
    out = pe.parallel_backtest(None, param_sets, raw_df=df, instrument="NIFTY", costs=True, pretrade={})
    assert len(out) == 2
    assert out[0][1] == param_sets[0][1] and out[1][1] == param_sets[1][1]  # order + merged preserved
    assert out[0][0] is not None and "trade_count" in out[0][0]  # REAL backtest, not a sentinel


def test_start_pool_sequential_when_not_parallel():
    assert pe.start_pool(_fixture_df(), 1) is None  # workers<=1 -> sequential
    assert pe.start_pool(_fixture_df(), 0) is None


def test_start_pool_degrades_instead_of_raising(monkeypatch):
    """A pool that cannot start must NOT kill the job. This is the regression that
    burned 800-trial runs: BrokenProcessPool propagated out of run_optimization and
    failed the whole optimization instead of falling back to the sequential path."""
    class _Exploding:
        def __init__(self, *a, **k):
            pass
        def map(self, *a, **k):
            raise BrokenProcessPool("worker died on warmup")
        def shutdown(self, *a, **k):
            pass

    monkeypatch.setattr(pe, "ProcessPoolExecutor", _Exploding)
    try:
        assert pe.start_pool(_fixture_df(), 4, "confluence_scalper") is None
        assert pe._POOL is None  # no half-built pool left to poison the next job
    finally:
        pe._POOL = None


def test_worker_selfcheck_rejects_a_worker_that_cannot_resolve_the_strategy():
    """_worker_evaluate swallows every exception and returns (None, merged). Without
    this warmup check, a spawn worker with an empty registry would score EVERY trial
    as a failed measurement and the job would 'succeed' with garbage."""
    prev_df = pe._RAW_DF
    try:
        pe._RAW_DF = None
        with pytest.raises(RuntimeError, match="never received raw_df"):
            pe._worker_selfcheck("confluence_scalper")

        pe._RAW_DF = _fixture_df()
        get_registry().auto_discover()
        assert pe._worker_selfcheck("confluence_scalper") == "ok"
        with pytest.raises(RuntimeError, match="cannot resolve strategy"):
            pe._worker_selfcheck("no_such_strategy_id")
    finally:
        pe._RAW_DF = prev_df


def test_init_worker_rebuilds_frame_and_registry():
    """A spawn worker inherits NOTHING — the initializer is the only thing that puts
    raw_df and the strategy registry into it."""
    prev_df = pe._RAW_DF
    try:
        pe._RAW_DF = None
        pe._WORKER_CACHES = {"stale": {}}
        df = _fixture_df()
        pe._init_worker(df)
        assert pe._RAW_DF is df
        assert pe._WORKER_CACHES == {}
        assert get_registry().get("confluence_scalper") is not None
    finally:
        pe._RAW_DF = prev_df
