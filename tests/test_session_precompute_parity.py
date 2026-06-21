"""Parity tests for the generalized per-session precompute (perf fix).

These prove the new precompute path is BYTE-IDENTICAL to the original per-bar
derivation it replaces -- the change is purely a performance fix (O(N) per bar
-> O(1) per bar), with no result change. Each test freezes the ORIGINAL per-bar
logic as a reference and asserts the precompute path matches it at EVERY bar,
including the edge cases:
  * the first session has no prior close (gap undefined),
  * a session shorter than or_minutes never forms its opening range,
  * the forming window before the OR is ready returns nothing.
"""
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

import pandas as pd

from app.strategies.builtin.gap_fade import GapFade
from app.strategies.builtin.opening_range_adaptive import OpeningRangeAdaptive
from app.strategies.session_features import gap_by_session, opening_range_by_session


def _multi_session_df():
    """3 sessions exercising the edges: a first session that is ALSO shorter than
    or_minutes (no prior close AND OR never forms), then normal sessions with a
    forming window. OHLC varies per bar so first/last/max/min are all non-trivial."""
    sessions = [("2025-01-02", 10), ("2025-01-03", 30), ("2025-01-06", 20)]
    rows = []
    base = 100.0
    for s_idx, (sess, n) in enumerate(sessions):
        for k in range(n):
            mm = 15 + k
            ist = f"09:{mm:02d}" if mm < 60 else f"10:{mm - 60:02d}"
            o = base + s_idx * 10 + k * 0.3
            c = o + ((-1) ** k) * 0.7
            hi = max(o, c) + 0.5 + (k % 3) * 0.2
            lo = min(o, c) - 0.5 - (k % 4) * 0.15
            rows.append({"session_date": sess, "ist_time": ist,
                         "open": o, "high": hi, "low": lo, "close": c})
    return pd.DataFrame(rows)


# ---- frozen reference: the ORIGINAL per-bar derivations, verbatim ----
def _ref_gap(row, hist, i):
    sess = row.get("session_date")
    upto = hist.iloc[: int(i) + 1]
    cur = upto[upto["session_date"] == sess]
    prior = upto[upto["session_date"] != sess]
    if len(cur) < 1 or len(prior) < 1:
        return None
    return float(cur["open"].iloc[0]), float(prior["close"].iloc[-1])


def _ref_opening_range(row, hist, i, or_minutes):
    sess = row.get("session_date")
    upto = hist.iloc[: int(i) + 1]
    sess_bars = upto[upto["session_date"] == sess]
    if len(sess_bars) <= or_minutes:
        return None
    or_bars = sess_bars.iloc[:or_minutes]
    return float(or_bars["high"].max()), float(or_bars["low"].min())


def test_gap_fast_path_matches_per_bar_reference():
    df = _multi_session_df()
    pre = gap_by_session(df)
    assert set(pre) >= {"day_open", "prev_close"}
    for i in range(len(df)):
        row = df.iloc[i]
        # fast ctx deliberately omits history_df: the ONLY way to answer is via
        # the precomputed maps, so this fails until the strategy reads them.
        fast_ctx = {"i": i, "day_open": pre["day_open"], "prev_close": pre["prev_close"]}
        assert GapFade._gap(row, fast_ctx) == _ref_gap(row, df, i), f"gap mismatch at bar {i}"


def test_opening_range_fast_path_matches_per_bar_reference():
    df = _multi_session_df()
    M = 15
    pre = opening_range_by_session(df, M)
    assert set(pre) >= {"or_hi", "or_lo", "or_ready_idx"}
    for i in range(len(df)):
        row = df.iloc[i]
        fast_ctx = {"i": i, "or_hi": pre["or_hi"], "or_lo": pre["or_lo"],
                    "or_ready_idx": pre["or_ready_idx"]}
        assert OpeningRangeAdaptive._opening_range(row, fast_ctx, M) == \
            _ref_opening_range(row, df, i, M), f"OR mismatch at bar {i}"


def test_opening_range_param_flows_through():
    """A different or_minutes must change the precomputed range (param-aware)."""
    df = _multi_session_df()
    a = opening_range_by_session(df, 15)
    b = opening_range_by_session(df, 20)
    # session 2025-01-03 has 30 bars; first-15 vs first-20 ranges should differ.
    assert a["or_hi"]["2025-01-03"] != b["or_hi"]["2025-01-03"] or \
        a["or_lo"]["2025-01-03"] != b["or_lo"]["2025-01-03"]


def test_session_precompute_helpers_no_session_date_column():
    """No session_date column -> empty maps (matches original 'return None')."""
    df = pd.DataFrame({"open": [1.0, 2.0], "high": [1.0, 2.0],
                       "low": [1.0, 2.0], "close": [1.0, 2.0]})
    assert gap_by_session(df) == {"day_open": {}, "prev_close": {}}
    assert opening_range_by_session(df, 15) == {"or_hi": {}, "or_lo": {}, "or_ready_idx": {}}
