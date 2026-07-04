# tests/test_gap_reset.py — intra-session gap detection + warm-up reset.
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

import numpy as np
import pandas as pd

from app.indicators import (
    MAX_CONTIGUOUS_GAP_MS, gap_before_mask, _reset_on_gap,
    atr, ema, velocity_accel, candle_geometry,
)
from tests._adaptive_testutil import make_ohlc, make_sessions


def _drop_mid_session(df, start_i, count):
    """Delete `count` contiguous rows starting at positional start_i, creating an
    intra-session hole; return a fresh 0..N-1 indexed frame."""
    keep = [i for i in range(len(df)) if not (start_i <= i < start_i + count)]
    return df.iloc[keep].reset_index(drop=True)


def test_gap_constant_is_one_minute():
    assert MAX_CONTIGUOUS_GAP_MS == 60_000


def test_gap_mask_all_false_on_contiguous_session():
    df = make_ohlc([100 + (i % 7) for i in range(60)])
    m = gap_before_mask(df)
    assert m.dtype == bool and len(m) == len(df)
    assert not m.any()


def test_gap_mask_flags_only_first_post_gap_bar():
    df = make_ohlc([100 + (i % 7) for i in range(60)])
    gapped = _drop_mid_session(df, 30, 5)          # remove positions 30..34
    m = gap_before_mask(gapped).to_numpy()
    assert m[30] == True                           # first bar after the hole
    assert m.sum() == 1                            # exactly one boundary


def test_gap_mask_ignores_overnight_boundary():
    df = make_sessions([[100 + (i % 5) for i in range(30)],
                        [200 + (i % 5) for i in range(30)]])
    m = gap_before_mask(df).to_numpy()
    assert not m.any()                             # cross-date boundary is NOT a gap


def test_reset_on_gap_fastpath_is_identity_series():
    df = make_ohlc([100 + (i % 7) for i in range(60)])
    df["gap_before"] = gap_before_mask(df)
    out = _reset_on_gap(df, lambda d: atr(d, 14))
    pd.testing.assert_series_equal(out, atr(df, 14))


def test_reset_on_gap_segments_reset_series():
    df = make_ohlc([100 + (i % 7) * 1.3 for i in range(60)])
    gapped = _drop_mid_session(df, 30, 5)          # segments [0:30] and [30:55]
    gapped["gap_before"] = gap_before_mask(gapped)
    out = _reset_on_gap(gapped, lambda d: atr(d, 14))
    pd.testing.assert_series_equal(out.iloc[0:30], atr(gapped.iloc[0:30], 14))
    pd.testing.assert_series_equal(out.iloc[30:55], atr(gapped.iloc[30:55], 14))
    assert out.iloc[30:30 + 13].isna().all()       # RESET: post-gap warm-up NaN again


def test_reset_on_gap_tuple_and_dict_shapes():
    df = make_ohlc([100 + (i % 7) * 1.1 for i in range(60)])
    gapped = _drop_mid_session(df, 30, 5)
    gapped["gap_before"] = gap_before_mask(gapped)
    vz, az = _reset_on_gap(gapped, lambda d: velocity_accel(d["close"], 2, 60))
    assert len(vz) == len(gapped) and len(az) == len(gapped)
    geo = _reset_on_gap(gapped, lambda d: candle_geometry(d))
    assert set(geo) >= {"body_frac", "inside_bar", "close_z"}
    assert all(len(s) == len(gapped) for s in geo.values())


def test_precompute_adds_gap_before_all_false_on_clean_frame():
    from app.indicators import precompute_all_indicators
    df = make_sessions([[100 + (i % 9) for i in range(80)],
                        [110 + (i % 9) for i in range(80)]])
    enr = precompute_all_indicators(df.copy(), {})
    assert "gap_before" in enr.columns
    assert enr["gap_before"].dtype == bool
    assert not enr["gap_before"].any()


def test_precompute_flags_intra_session_gap():
    from app.indicators import precompute_all_indicators
    df = make_ohlc([100 + (i % 9) * 0.7 for i in range(80)])   # single session
    gapped = _drop_mid_session(df, 40, 6)
    enr = precompute_all_indicators(gapped.copy(), {})
    assert enr["gap_before"].sum() == 1
    assert bool(enr["gap_before"].iloc[40]) is True
