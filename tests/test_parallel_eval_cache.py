"""Item #8 O12 — the parallel-eval pool-None fallback must NOT reuse the frame-blind
module-global indicator cache across calls (cross-job/frame poisoning → NaN tails →
silently wrong best_params). CONTAINER test (imports app.backtest via parallel_eval).
"""
from __future__ import annotations

import sys
import types
from pathlib import Path
from unittest.mock import patch

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

import app.parallel_eval as pe  # noqa: E402

_FAKE_REG = types.SimpleNamespace(get=lambda _sid: object())


def _fake_run(*_a, **_k):
    return {"metrics": {"total_pnl": 1.0}, "trades": []}


def test_pool_none_uses_one_fresh_cache_per_call_not_the_global():
    pe._WORKER_CACHES.clear()
    seen = []

    def fake_enrich(frame, merged, caches):
        seen.append(caches)
        caches["written"] = True   # a real enrich would populate the cache
        return frame

    df = pd.DataFrame({"close": [1, 2, 3]})
    with patch.object(pe, "enrich_with_cache", fake_enrich), \
         patch.object(pe, "run_backtest", _fake_run), \
         patch.object(pe, "get_registry", lambda: _FAKE_REG):
        pe.parallel_backtest(
            None, [("s", {"a": 1}, None), ("s", {"a": 2}, None)],
            raw_df=df, instrument="NIFTY", costs=True, pretrade={})

    # both param_sets in ONE call share ONE fresh dict (perf preserved within a call)
    assert len(seen) == 2 and seen[0] is seen[1]
    # ...but it is NOT the module global → no cross-call / cross-frame poisoning
    assert seen[0] is not pe._WORKER_CACHES
    assert pe._WORKER_CACHES == {}      # the fallback never writes the global


def test_pool_none_second_call_gets_a_distinct_cache():
    pe._WORKER_CACHES.clear()
    seen = []

    def fake_enrich(frame, merged, caches):
        seen.append(caches)
        return frame

    df = pd.DataFrame({"close": [1]})
    with patch.object(pe, "enrich_with_cache", fake_enrich), \
         patch.object(pe, "run_backtest", _fake_run), \
         patch.object(pe, "get_registry", lambda: _FAKE_REG):
        pe.parallel_backtest(None, [("s", {}, None)], raw_df=df,
                             instrument="N", costs=True, pretrade={})
        pe.parallel_backtest(None, [("s", {}, None)], raw_df=df,
                             instrument="N", costs=True, pretrade={})

    assert seen[0] is not seen[1]       # a later job cannot reuse the earlier cache


def test_worker_evaluate_caches_none_still_uses_the_fork_global():
    # The FORK path (pool.submit) omits `caches` → defaults None → the per-worker
    # module global (cleared in _init_worker). That path stays byte-identical.
    pe._WORKER_CACHES.clear()
    seen = []

    def fake_enrich(frame, merged, caches):
        seen.append(caches)
        return frame

    with patch.object(pe, "enrich_with_cache", fake_enrich), \
         patch.object(pe, "run_backtest", _fake_run), \
         patch.object(pe, "get_registry", lambda: _FAKE_REG):
        pe._worker_evaluate("s", {}, None, "N", True, {}, pd.DataFrame({"close": [1]}))

    assert seen[0] is pe._WORKER_CACHES  # caches omitted → fork global (unchanged)
