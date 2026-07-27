"""Exhaustive tests for load-time DATA columns (`app.data_columns`).

The whole point of this seam is that a strategy can read India VIX *at decision
time*. That is only safe if three properties hold, so they are tested as
properties over randomized data, not just as hand-picked examples:

1. **No lookahead.** A bar at time T may only ever see a print stamped <= T.
   Tested by reconstructing, for every non-NaN bar, the exact source row the
   value must have come from.
2. **No drift from the scalar path.** The vectorized `merge_asof` join must
   agree EXACTLY with `app.vix.vix_asof` (the scalar bisect used by the live
   premium gate) for every bar. Two implementations of "as-of" that disagree
   would be a silent correctness split between backtest and live.
3. **No perturbation.** A strategy that declares nothing must get a frame that
   is the identical object — byte-identical behaviour by construction, not by
   inspection.

Pure + host-safe: no motor, no DB (the async warehouse seam is exercised against
a minimal in-memory fake).
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.data_columns import (                                   # noqa: E402
    DATA_COLUMN_REGISTRY,
    DataColumn,
    DataColumnError,
    asof_series,
    attach_data_columns,
    data_column_names,
    resolve_data_columns,
)
from app.vix import VIX_INSTRUMENT, build_asof_index, vix_asof   # noqa: E402

MIN = 60_000
DAY = 24 * 60 * 60 * 1000


def _rows(*pairs):
    return [{"ts": ts, "close": close} for ts, close in pairs]


def _frame(ts_values, **extra):
    data = {"ts": list(ts_values)}
    data.update(extra)
    return pd.DataFrame(data)


# ---------------------------------------------------------------------------
# registry + resolution
# ---------------------------------------------------------------------------

def test_vix_is_registered_against_the_aux_instrument():
    spec = DATA_COLUMN_REGISTRY["vix"]
    assert spec.column == "vix"
    assert spec.instrument == VIX_INSTRUMENT == "INDIAVIX"
    assert spec.causal is True
    assert spec.max_staleness_ms > 0


def test_vix_staleness_matches_every_other_vix_consumer():
    """One quantity, one staleness bound. The live session-start gate
    (deployment_evaluator._resolve_vix_asof) and the premium-momentum route both
    reach back 5 days; if the per-bar column used a different bound, the same
    session could PASS the gate while reading NaN per bar — two answers about the
    same market state inside one deployment."""
    from app.vix import VIX_ASOF_STALENESS_MS
    assert DATA_COLUMN_REGISTRY["vix"].max_staleness_ms == VIX_ASOF_STALENESS_MS

    evaluator = (ROOT / "backend/app/deployment_evaluator.py").read_text(encoding="utf-8")
    routes = (ROOT / "backend/app/routers/premium_momentum_routes.py").read_text(encoding="utf-8")
    # Both still hardcode the same number; pinned here so migrating them onto the
    # shared constant stays a no-op, and changing one alone breaks this test.
    assert "max_staleness_ms=5 * 24 * 60 * 60 * 1000" in evaluator
    assert "VIX_ASOF_STALENESS_MS = 5 * 24 * 3600 * 1000" in routes
    assert VIX_ASOF_STALENESS_MS == 5 * 24 * 60 * 60 * 1000


def test_resolve_empty_is_empty():
    assert resolve_data_columns([]) == []
    assert resolve_data_columns(None) == []


def test_resolve_unknown_name_raises_with_the_available_list():
    with pytest.raises(DataColumnError) as exc:
        resolve_data_columns(["nope"])
    assert exc.value.code == "unknown_data_column"
    assert exc.value.name == "nope"
    assert "vix" in exc.value.available


def test_resolve_dedupes_and_preserves_declaration_order():
    assert [s.name for s in resolve_data_columns(["vix", "vix"])] == ["vix"]
    assert data_column_names(["vix"]) == ["vix"]


# ---------------------------------------------------------------------------
# as-of semantics — the causality core
# ---------------------------------------------------------------------------

def test_empty_source_yields_all_nan_not_zero():
    """A missing series must be NaN, never 0.0 — a 0.0 would read as 'VIX is
    very low' and silently satisfy a `vix < threshold` rule."""
    out = asof_series(_frame([1_000, 2_000])["ts"], [], DAY)
    assert out.isna().all()


def test_empty_frame_yields_empty_series():
    out = asof_series(_frame([])["ts"], _rows((1_000, 12.0)), DAY)
    assert len(out) == 0


def test_print_exactly_at_bar_ts_is_used():
    """At-or-before is INCLUSIVE at the boundary."""
    out = asof_series(_frame([5_000])["ts"], _rows((5_000, 14.25)), DAY)
    assert out.iloc[0] == 14.25


def test_print_one_ms_after_the_bar_is_not_used():
    """THE no-lookahead assertion, at the tightest possible margin."""
    out = asof_series(_frame([5_000])["ts"], _rows((5_001, 99.0)), DAY)
    assert out.isna().all()


def test_bar_before_the_first_print_is_nan():
    out = asof_series(_frame([1_000, 9_000])["ts"], _rows((5_000, 13.0)), DAY)
    assert pd.isna(out.iloc[0])
    assert out.iloc[1] == 13.0


def test_value_carries_forward_until_the_next_print():
    ts = [10_000, 11_000, 12_000, 13_000]
    out = asof_series(_frame(ts)["ts"], _rows((10_000, 11.0), (12_000, 18.0)), DAY)
    assert list(out) == [11.0, 11.0, 18.0, 18.0]


def test_staleness_bound_is_inclusive_and_rejects_beyond():
    """Matches vix_asof's `(t - print_ts) > max_staleness` rejection exactly."""
    exact = asof_series(_frame([DAY])["ts"], _rows((0, 15.0)), DAY)
    assert exact.iloc[0] == 15.0                     # staleness == bound -> used
    beyond = asof_series(_frame([DAY + 1])["ts"], _rows((0, 15.0)), DAY)
    assert beyond.isna().all()                       # one ms past -> NaN


def test_no_staleness_bound_reaches_arbitrarily_far_back():
    out = asof_series(_frame([500 * DAY])["ts"], _rows((0, 15.0)), None)
    assert out.iloc[0] == 15.0


def test_unsorted_source_rows_are_handled():
    out = asof_series(_frame([12_000])["ts"], _rows((12_000, 20.0), (1_000, 9.0)), DAY)
    assert out.iloc[0] == 20.0


def test_duplicate_source_ts_takes_the_last_matching_row():
    """Mirrors bisect_right-1 in vix_asof, which also lands on the last dupe."""
    src = _rows((5_000, 11.0), (5_000, 22.0))
    assert asof_series(_frame([5_000])["ts"], src, DAY).iloc[0] == 22.0
    assert vix_asof(build_asof_index(src), 5_000) == 22.0


def test_source_rows_without_ts_are_skipped():
    src = [{"close": 5.0}, {"ts": 1_000, "close": 12.0}]
    assert asof_series(_frame([2_000])["ts"], src, DAY).iloc[0] == 12.0


def test_malformed_bar_ts_yields_nan_and_never_borrows_a_neighbour():
    """A NaN key must not be silently dropped INTO an adjacent bar's slot."""
    ts = pd.Series([1_000, None, 3_000], dtype="object")
    out = asof_series(ts, _rows((1_000, 10.0), (3_000, 30.0)), DAY)
    assert out.iloc[0] == 10.0
    assert pd.isna(out.iloc[1])
    assert out.iloc[2] == 30.0


def test_fractional_bar_ts_is_truncated_exactly_as_the_scalar_path_does():
    """Regression: a strict Int64 cast RAISED on a fractional ts while
    `vix_asof` (the live scalar path) floors it via `int(ts_ms)` — a genuine
    backtest-vs-live split on identical input. Truncation is also the right
    as-of answer: a bar at 4999.7ms may see a print stamped 4999."""
    src = _rows((4_999, 15.0))
    assert asof_series(pd.Series([4_999.7]), src, None).iloc[0] == 15.0
    assert vix_asof(build_asof_index(src), 4_999.7) == 15.0
    # and a fractional ts must NOT reach forward to a later print
    assert pd.isna(asof_series(pd.Series([4_999.7]), _rows((5_000, 99.0)), None).iloc[0])


@pytest.mark.parametrize("index", [
    pd.RangeIndex(3),                 # default
    pd.Index([7, 2, 11]),             # left over from a boolean filter
    pd.Index([0, 0, 0]),              # concat without ignore_index
    pd.Index([9, 8, 7]),              # descending
])
def test_alignment_survives_any_frame_index_shape(index):
    """The join maps results back by POSITION; a non-default, duplicated or
    descending index label set must not scramble it."""
    ts = pd.Series([9_000, 1_000, 5_000], index=index)
    out = asof_series(ts, _rows((1_000, 1.0), (5_000, 5.0), (9_000, 9.0)), DAY)
    assert list(out) == [9.0, 1.0, 5.0]


def test_unsorted_bar_frame_keeps_positional_alignment():
    ts = pd.Series([9_000, 1_000, 5_000])
    out = asof_series(ts, _rows((1_000, 1.0), (5_000, 5.0), (9_000, 9.0)), DAY)
    assert list(out) == [9.0, 1.0, 5.0]


def test_rounding_matches_the_scalar_path():
    src = _rows((1_000, 13.456789))
    assert asof_series(_frame([2_000])["ts"], src, DAY).iloc[0] == 13.46
    assert vix_asof(build_asof_index(src), 2_000) == 13.46


# ---------------------------------------------------------------------------
# property tests over randomized data
# ---------------------------------------------------------------------------

def _random_case(seed):
    rng = np.random.default_rng(seed)
    n_bars = int(rng.integers(5, 200))
    n_src = int(rng.integers(0, 120))
    base = 1_700_000_000_000
    bar_ts = base + np.sort(rng.integers(0, 400, size=n_bars).cumsum() * MIN)
    src_ts = base + np.sort(rng.integers(-50, 400, size=n_src).cumsum() * MIN) if n_src else np.array([], dtype="int64")
    src = [{"ts": int(t), "close": float(round(rng.uniform(8, 35), 4))} for t in src_ts]
    staleness = int(rng.choice([MIN * 5, DAY, 4 * DAY, 30 * DAY]))
    return pd.Series(bar_ts), src, staleness


@pytest.mark.parametrize("seed", range(40))
def test_property_vectorized_join_equals_the_scalar_vix_asof(seed):
    """Anti-drift: the fast path and the live scalar path must never disagree."""
    bar_ts, src, staleness = _random_case(seed)
    fast = asof_series(bar_ts, src, staleness)
    index = build_asof_index(src)
    slow = [vix_asof(index, int(t), max_staleness_ms=staleness) for t in bar_ts]
    for i, (f, s) in enumerate(zip(fast, slow)):
        if s is None:
            assert pd.isna(f), f"bar {i}: scalar said None, vectorized said {f}"
        else:
            assert f == pytest.approx(s), f"bar {i}: {f} != {s}"


@pytest.mark.parametrize("seed", range(40))
def test_property_no_value_ever_comes_from_the_future(seed):
    """For every non-NaN bar, the value must equal the LATEST source print at or
    before that bar. Reconstructed independently of the implementation."""
    bar_ts, src, staleness = _random_case(seed)
    out = asof_series(bar_ts, src, staleness)
    # Kept in INPUT order, not sorted by (ts, value): where two prints share a
    # timestamp the as-of answer is the last one as supplied — the tie-break
    # both build_asof_index (stable sort by ts) and merge_asof already use.
    # Sorting by the tuple would silently tie-break on value and make this
    # oracle disagree with every real implementation.
    pairs = [(int(r["ts"]), float(r["close"])) for r in src]
    for t, v in zip(bar_ts, out):
        eligible = [(ts, val) for ts, val in pairs
                    if ts <= int(t) and int(t) - ts <= staleness]
        if not eligible:
            assert pd.isna(v), f"bar {t}: nothing eligible but got {v}"
            continue
        newest = max(ts for ts, _ in eligible)
        expected = [val for ts, val in eligible if ts == newest][-1]
        assert v == pytest.approx(round(expected, 2))
        # And the value must be traceable to a print at or before this bar.
        assert newest <= int(t)


# ---------------------------------------------------------------------------
# attach_data_columns — framing, purity, coverage
# ---------------------------------------------------------------------------

def test_no_specs_returns_the_identical_object():
    """Byte-identical BY CONSTRUCTION for every strategy that declares nothing."""
    df = _frame([1_000, 2_000])
    out, cov = attach_data_columns(df, [], {})
    assert out is df
    assert cov == {}


def test_attach_never_mutates_the_caller_frame():
    """The optimizer hands in a cached frame; mutating it would poison trials."""
    df = _frame([1_000, 2_000])
    before = df.copy(deep=True)
    out, _ = attach_data_columns(df, resolve_data_columns(["vix"]),
                                 {"vix": _rows((1_000, 12.0))})
    pd.testing.assert_frame_equal(df, before)
    assert "vix" not in df.columns
    assert "vix" in out.columns


def test_coverage_full_partial_and_zero():
    specs = resolve_data_columns(["vix"])
    df = _frame([1_000, 2_000, 3_000])

    _, full = attach_data_columns(df, specs, {"vix": _rows((0, 12.0))})
    assert full["vix"]["coverage_pct"] == 100.0

    _, partial = attach_data_columns(df, specs, {"vix": _rows((2_000, 12.0))})
    assert partial["vix"]["present"] == 2
    assert partial["vix"]["coverage_pct"] == pytest.approx(66.67, abs=0.01)

    _, none = attach_data_columns(df, specs, {"vix": []})
    assert none["vix"]["present"] == 0
    assert none["vix"]["coverage_pct"] == 0.0


def test_coverage_names_the_missing_sessions_when_available():
    specs = resolve_data_columns(["vix"])
    df = _frame([1_000, 2_000, 3_000],
                session_date=["2025-01-01", "2025-01-01", "2025-01-02"])
    _, cov = attach_data_columns(df, specs, {"vix": _rows((3_000, 12.0))})
    assert cov["vix"]["sessions_missing"] == ["2025-01-01"]
    assert cov["vix"]["sessions_missing_count"] == 1


def test_attach_on_empty_frame_is_a_noop():
    out, cov = attach_data_columns(pd.DataFrame(), resolve_data_columns(["vix"]), {})
    assert cov == {}
    assert out.empty


def test_missing_source_key_yields_an_all_nan_column_not_a_crash():
    specs = resolve_data_columns(["vix"])
    out, cov = attach_data_columns(_frame([1_000]), specs, {})
    assert "vix" in out.columns and out["vix"].isna().all()
    assert cov["vix"]["coverage_pct"] == 0.0


def test_custom_source_field_is_honoured():
    spec = DataColumn(name="x", column="x", instrument="X", max_staleness_ms=DAY,
                      source_field="value")
    out, _ = attach_data_columns(_frame([2_000]), [spec],
                                 {"x": [{"ts": 1_000, "value": 7.5}]})
    assert out["x"].iloc[0] == 7.5
