import sys
import pickle
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app import parallel_eval as pe
from app.strategies.base import get_registry
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


def test_parallel_backtest_sequential_fallback_in_order():
    # pool=None path: equals direct in-process evaluation, in submission order.
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


def test_start_pool_returns_none_without_fork(monkeypatch):
    monkeypatch.setattr(pe, "fork_available", lambda: False)
    assert pe.start_pool(_fixture_df(), 4) is None  # no fork -> sequential fallback
    assert pe.start_pool(_fixture_df(), 1) is None  # workers<=1 -> sequential
