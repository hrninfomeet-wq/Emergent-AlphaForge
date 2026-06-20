import sys
import pickle
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app import parallel_eval as pe
from app.strategies.base import get_registry
from app.indicator_groups import enrich_with_cache
from app.wfo import _evaluate_slice
from tests._adaptive_testutil import make_sessions


def _fixture_df():
    # >=3 sessions so a window can start at row > 0 (deep enough that trailing-lookback
    # indicators carry warmup history across the window's start boundary).
    base = [100 + (i % 17) - (i % 5) * 0.7 for i in range(120)]
    return make_sessions([base, [x + 3 for x in base], [x - 2 for x in base]],
                         start_date="2025-01-06")


def test_worker_evaluate_wfo_is_top_level_picklable():
    # ProcessPoolExecutor.submit requires the callable be importable by qualified name.
    assert pickle.loads(pickle.dumps(pe._worker_evaluate_wfo)) is pe._worker_evaluate_wfo


def test_worker_evaluate_wfo_matches_evaluate_slice():
    # THE HARD PARITY GATE: the new WFO worker must be byte-identical to wfo._evaluate_slice.
    get_registry().auto_discover()
    raw_df = _fixture_df()
    strat = get_registry().get("confluence_scalper")
    merged = strat.merged_params({})
    enr_full = enrich_with_cache(raw_df, merged, {})
    a, b = len(enr_full) // 3, len(enr_full)  # a > 0 -> window starts mid-frame
    assert a > 0 and b > a

    ref_metrics, ref_trades = _evaluate_slice(enr_full, a, b, strat, merged, "NIFTY", True, {})

    pe._RAW_DF = raw_df
    pe._WORKER_CACHES = {}
    got_metrics, got_merged = pe._worker_evaluate_wfo("confluence_scalper", merged, (a, b), "NIFTY", True, {})

    assert got_merged == merged
    assert got_metrics == ref_metrics  # full dict equality, incl. ce_count/pe_count


def test_worker_evaluate_wfo_preserves_warmup_vs_slice_then_enrich():
    # Documents WHY the new worker exists: enrich-full-then-slice (correct) vs
    # slice-raw-then-enrich (the single-run worker = WRONG for WFO) must DIFFER,
    # because trailing-lookback indicators (EMA/RSI/MACD/ADX) lose warmup history
    # when the slice restarts the indicator computation at the window start.
    get_registry().auto_discover()
    raw_df = _fixture_df()
    strat = get_registry().get("confluence_scalper")
    merged = strat.merged_params({})
    enr_full = enrich_with_cache(raw_df, merged, {})
    # Start deep in the frame so the slice's first bars carry meaningful warmup that
    # the slice-then-enrich path cannot reproduce.
    a, b = len(enr_full) // 3, len(enr_full)
    assert a > 0 and b > a

    pe._RAW_DF = raw_df
    pe._WORKER_CACHES = {}
    m_correct, _ = pe._worker_evaluate_wfo("confluence_scalper", merged, (a, b), "NIFTY", True, {})

    # Existing single-run worker slices RAW then enriches = the WRONG way for WFO.
    pe._WORKER_CACHES = {}
    m_wrong, _ = pe._worker_evaluate("confluence_scalper", merged, (a, b), "NIFTY", True, {})

    assert m_correct is not None and m_wrong is not None
    assert m_correct != m_wrong  # warmup preserved only by the enrich-full-then-slice path


def test_worker_evaluate_wfo_never_raises():
    get_registry().auto_discover()
    raw_df = _fixture_df()
    pe._RAW_DF = raw_df
    pe._WORKER_CACHES = {}
    bounds = (0, len(raw_df))
    # Nonsense param must not crash the worker — contract is (metrics|None, merged), never raises.
    metrics, merged = pe._worker_evaluate_wfo("confluence_scalper", {"ema_fast": -5}, bounds, "NIFTY", True, {})
    assert merged == {"ema_fast": -5}
    assert metrics is None or isinstance(metrics, dict)


def test_parallel_backtest_uses_worker_param():
    # The `worker` kwarg routes parallel_backtest through the WFO worker; sequential
    # path passes the frame explicitly so it must NOT depend on _RAW_DF.
    get_registry().auto_discover()
    pe._RAW_DF = None  # prove the sequential path does NOT depend on the global
    pe._WORKER_CACHES = {}
    raw_df = _fixture_df()
    strat = get_registry().get("confluence_scalper")
    a, b = len(raw_df) // 3, len(raw_df)
    param_sets = [("confluence_scalper", strat.merged_params({}), (a, b)),
                  ("confluence_scalper", strat.merged_params({"ema_fast": 5, "ema_slow": 13}), (a, b))]
    out = pe.parallel_backtest(None, param_sets, raw_df=raw_df, instrument="NIFTY",
                               costs=True, pretrade={}, worker=pe._worker_evaluate_wfo)
    assert len(out) == 2
    assert out[0][1] == param_sets[0][1] and out[1][1] == param_sets[1][1]  # order + merged preserved
    assert out[0][0] is not None and "trade_count" in out[0][0]  # REAL backtest, not a sentinel

    # Element 0 equals a direct worker call with the frame passed explicitly.
    pe._WORKER_CACHES = {}
    direct_metrics, direct_merged = pe._worker_evaluate_wfo(
        param_sets[0][0], param_sets[0][1], param_sets[0][2], "NIFTY", True, {}, raw_df)
    assert out[0][1] == direct_merged
    assert out[0][0] == direct_metrics
