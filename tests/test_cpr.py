import numpy as np
import pandas as pd
import pytest
from app.cpr import cpr_levels
from tests._adaptive_testutil import make_sessions


def test_cpr_formula_and_tc_ge_bc():
    # session 1 H/L/C known -> session 2 CPR derived from it
    df = make_sessions([[100, 110, 90, 105], [106, 107, 104, 106]])
    out = cpr_levels(df)
    s2 = df["session_date"].iloc[-1]
    row = out[df["session_date"] == s2].iloc[0]
    H, L, C = 110.5, 89.5, 105.0   # high_pad/low_pad 0.5 from make_ohlc
    P = (H + L + C) / 3
    assert row["cpr_p"] == pytest.approx(P, rel=1e-6)
    assert row["cpr_tc"] >= row["cpr_bc"]
    assert row["cpr_width_pct"] == pytest.approx((row["cpr_tc"] - row["cpr_bc"]) / P * 100, rel=1e-6)


def test_cpr_day_type_narrow_is_trend():
    # 8 wide sessions then a very narrow CPR -> next session tagged TREND.
    # wide closes are asymmetric (C=120 != midpoint of H=130.5/L=69.5) so
    # CPR width ~12.5%; narrow_src close ~100.1 gives width ~0.07% -> clear TREND.
    wide = [[100, 130, 70, 120]] * 8
    narrow_src = [[100, 100.2, 99.8, 100.1]]   # tiny prior-day range -> narrow CPR
    after = [[100, 101, 99, 100.5]]
    df = make_sessions(wide + narrow_src + after)
    out = cpr_levels(df, narrow_pctile=40, wide_pctile=60, pctile_window=10)
    last = df["session_date"].iloc[-1]
    assert out[df["session_date"] == last]["day_type"].iloc[0] == "TREND"


def test_cpr_first_session_has_no_levels():
    df = make_sessions([[100, 110, 90, 105]])
    out = cpr_levels(df)
    assert out["cpr_p"].isna().all()  # no prior day -> NaN, never look-ahead
