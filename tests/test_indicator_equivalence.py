# tests/test_indicator_equivalence.py
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

import pandas as pd
from app.indicators import precompute_all_indicators
from app.regime import classify_regime_series
from app.indicator_groups import enrich_with_cache, run_all_groups
from tests._adaptive_testutil import make_sessions

# The comparison seam. `_enrich_ref` is the UNCHANGED golden reference
# (monolithic precompute + classify_regime_series). `_enrich_new` is now the
# memoized per-group assembler (cache-miss path) — proven byte-identical below.
def _enrich_ref(df, params):
    enr = precompute_all_indicators(df, params)
    enr["regime"] = classify_regime_series(enr)
    return enr

def _enrich_new(df, params):
    return run_all_groups(df, params)

def _fixture_df():
    # 3 sessions x 120 bars of a varied path so every indicator has signal.
    base = [100 + (i % 17) - (i % 5) * 0.7 for i in range(120)]
    return make_sessions([base, [x + 3 for x in base], [x - 2 for x in base]],
                         start_date="2025-01-06")

# Param sweep: defaults + single-variable variations over indicator-period keys,
# INCLUDING an atr_length variation (exercises the regime/tod hidden atr edges
# in a later phase). Every dict is a full param set the strategies accept.
_PARAM_SWEEP = [
    {},  # defaults
    {"ema_fast": 5, "ema_slow": 13},
    {"rsi_length": 9},
    {"atr_length": 7},                 # hidden-edge probe (regime, atr_avg, tod read atr)
    {"atr_length": 28},
    {"adx_length": 20},
    {"st_period": 7, "st_mult": 2.0},
    {"swing_lookback": 3},
    {"tod_lookback_sessions": 10, "tod_min_atr_frac": 0.4},
    {"or_minutes": 20},                # keyed orb_width group: vary the key
    {"or_minutes": 45},
]

def test_enrichment_is_deterministic():
    df = _fixture_df()
    for params in _PARAM_SWEEP:
        a = _enrich_new(df.copy(), params)
        b = _enrich_new(df.copy(), params)
        pd.testing.assert_frame_equal(a, b)

def test_new_matches_reference_across_param_sweep():
    df = _fixture_df()
    for params in _PARAM_SWEEP:
        ref = _enrich_ref(df.copy(), params)
        new = _enrich_new(df.copy(), params)
        pd.testing.assert_frame_equal(new, ref, check_dtype=True)

def test_memoized_matches_reference_across_sweep_with_cache_reuse():
    df = _fixture_df()
    caches = {}
    # Run the sweep TWICE through the SAME caches to exercise cache HITS, not just misses.
    for _round in range(2):
        for params in _PARAM_SWEEP:
            ref = _enrich_ref(df.copy(), params)
            new = enrich_with_cache(df.copy(), params, caches)
            # check_like=True: column ORDER may differ but values+dtype must match exactly.
            # Justified: strategies read columns by NAME (df.to_dict('records')), never by position.
            pd.testing.assert_frame_equal(new, ref, check_like=True, check_dtype=True)

def test_expected_columns_present():
    df = _fixture_df()
    enr = _enrich_new(df.copy(), {})
    for col in ("gap_before", "ema9", "ema21", "rsi", "macd_hist", "atr",
                "atr_avg", "adx", "chop", "vwap", "session_date", "ist_time",
                "regime", "squeeze_on", "supertrend", "st_dir", "tod_tradeable",
                "cpr_tc", "cpr_bc", "day_type", "nr7", "fvg",
                "orb_width_pct_partial", "orb_width_pct_prior"):
        assert col in enr.columns, f"missing {col}"


def test_orb_width_partial_is_causal():
    # Causality proof: orb_width_pct_partial must be NaN on the first bar of each
    # session and on every pre-cutoff bar (no look-ahead). or_minutes=30 -> the
    # 09:15..09:45 window; the fixture is 120 1-min bars/session so bars at
    # minute-offset >= 30 are at/after the cutoff. The width is normalized by the
    # prior-day pivot cpr_p, which is NaN for the FIRST session (no prior day) ->
    # that session's partial is legitimately all-NaN; only assert non-NaN
    # post-cutoff for sessions that have a valid pivot.
    df = _fixture_df()
    or_minutes = 30
    enr = _enrich_new(df.copy(), {"or_minutes": or_minutes})
    dt = pd.to_datetime(enr["ts"], unit="ms", utc=True).dt.tz_convert("Asia/Kolkata")
    saw_populated = False
    for _sdate, g in enr.groupby("session_date", sort=False):
        gdt = dt.loc[g.index]
        cutoff = gdt.iloc[0] + pd.Timedelta(minutes=or_minutes)
        partial = g["orb_width_pct_partial"]
        # First bar of the session: opening range not yet known -> NaN (no look-ahead).
        assert pd.isna(partial.iloc[0]), f"{_sdate}: partial leaks on first bar"
        # Causality: every pre-cutoff bar is NaN regardless of pivot availability.
        assert partial[gdt < cutoff].isna().all(), \
            f"{_sdate}: partial set before cutoff (look-ahead)"
        # When the prior-day pivot exists, post-cutoff bars are all populated.
        if pd.notna(g["cpr_p"].iloc[0]):
            assert partial[gdt >= cutoff].notna().all(), \
                f"{_sdate}: partial missing after cutoff"
            saw_populated = True
    assert saw_populated, "fixture never exercised a populated post-cutoff partial"

def test_orb_width_prior_first_session_is_nan():
    # Causality proof for the *prior* column: the FIRST session of a window has
    # NO prior session, so every bar of session 1 must have orb_width_pct_prior
    # NaN. A regression (Python negative indexing order[-1]) would leak the LAST
    # session's width back onto bar 0 -> this asserts that cannot happen.
    or_minutes = 30
    df = _fixture_df()  # 3 sessions; sessions 2 & 3 have a valid prior-day pivot.
    enr = _enrich_new(df.copy(), {"or_minutes": or_minutes})
    dt = pd.to_datetime(enr["ts"], unit="ms", utc=True).dt.tz_convert("Asia/Kolkata")
    groups = list(enr.groupby("session_date", sort=False))
    assert len(groups) >= 3, "fixture must have >=3 sessions for a teeth-having test"

    # The LAST session must have a defined partial width (needs a prior-day pivot,
    # i.e. it is NOT the first session) so the value that WOULD leak back onto
    # session 1 via order[-1] is genuinely non-NaN -> a regression fails loudly.
    _last_sdate, last_g = groups[-1]
    assert last_g["orb_width_pct_partial"].notna().any(), \
        "last session has no defined partial width -> leak test has no teeth"

    # First session: every bar's prior is NaN (no future-session value leaked back).
    # NB: session 1 also has NaN cpr_p (no prior-day pivot) so its own partial is
    # all-NaN; the leak-guard here is independent of that -- a regression would put
    # the LAST session's (non-NaN) width here, which this would catch.
    _first_sdate, first_g = groups[0]
    assert first_g["orb_width_pct_prior"].isna().all(), \
        "first session orb_width_pct_prior leaks a future-session width onto bar 0"

    # Pin the legitimate shift on the session 2 -> session 3 boundary, where BOTH
    # values are non-NaN (session 2 has a valid prior-day pivot, so a defined
    # partial). Session 3's prior must equal session 2's settled partial width.
    _s2_sdate, second_g = groups[1]
    _s3_sdate, third_g = groups[2]
    s2dt = dt.loc[second_g.index]
    cutoff2 = s2dt.iloc[0] + pd.Timedelta(minutes=or_minutes)
    s2_partial_post = second_g["orb_width_pct_partial"][s2dt >= cutoff2].dropna().unique()
    s3_prior = third_g["orb_width_pct_prior"].dropna().unique()
    assert len(s2_partial_post) == 1, "session 2 should have a single settled partial width"
    assert len(s3_prior) == 1, "session 3 prior should be a single constant width"
    assert s3_prior[0] == s2_partial_post[0], \
        "session 3 prior must equal session 2 partial (legitimate prior shift broken)"

def test_session_date_and_ist_time_match_strftime_reference():
    df = _fixture_df()
    enr = precompute_all_indicators(df.copy(), {})
    dt = pd.to_datetime(df["ts"], unit="ms", utc=True).dt.tz_convert("Asia/Kolkata")
    expected_date = dt.dt.strftime("%Y-%m-%d")
    expected_time = dt.dt.strftime("%H:%M")
    pd.testing.assert_series_equal(enr["session_date"], expected_date, check_names=False)
    pd.testing.assert_series_equal(enr["ist_time"], expected_time, check_names=False)
