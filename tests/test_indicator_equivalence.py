# tests/test_indicator_equivalence.py
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

import pandas as pd
from app.indicators import precompute_all_indicators
from app.regime import classify_regime_series
from tests._adaptive_testutil import make_sessions

# The comparison seam. A later phase re-points _enrich_new at the memoized assembly;
# for now both are the current monolithic precompute, so equality is exact.
def _enrich_ref(df, params):
    enr = precompute_all_indicators(df, params)
    enr["regime"] = classify_regime_series(enr)
    return enr

def _enrich_new(df, params):
    enr = precompute_all_indicators(df, params)
    enr["regime"] = classify_regime_series(enr)
    return enr

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

def test_expected_columns_present():
    df = _fixture_df()
    enr = _enrich_new(df.copy(), {})
    for col in ("ema9", "ema21", "rsi", "macd_hist", "atr", "atr_avg", "adx",
                "chop", "vwap", "session_date", "ist_time", "regime",
                "squeeze_on", "supertrend", "st_dir", "tod_tradeable",
                "cpr_tc", "cpr_bc", "day_type", "nr7", "fvg"):
        assert col in enr.columns, f"missing {col}"

def test_session_date_and_ist_time_match_strftime_reference():
    df = _fixture_df()
    enr = precompute_all_indicators(df.copy(), {})
    dt = pd.to_datetime(df["ts"], unit="ms", utc=True).dt.tz_convert("Asia/Kolkata")
    expected_date = dt.dt.strftime("%Y-%m-%d")
    expected_time = dt.dt.strftime("%H:%M")
    pd.testing.assert_series_equal(enr["session_date"], expected_date, check_names=False)
    pd.testing.assert_series_equal(enr["ist_time"], expected_time, check_names=False)
