import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

import pandas as pd
import pytest
from app.strategies.builtin.opening_range_adaptive import OpeningRangeAdaptive


def _session_hist(n=26, sess="2025-01-02"):
    """n 1-min bars; first 15 form an OR of [100,105]; later bars break above to 109."""
    rows = []
    for k in range(n):
        mm = 15 + k
        ist = f"09:{mm:02d}" if mm < 60 else f"10:{mm - 60:02d}"
        cl = 102.0 if k < 16 else 109.0
        rows.append({"session_date": sess, "ist_time": ist,
                     "high": 105.0 if k < 16 else 109.5, "low": 100.0,
                     "close": cl, "atr": 3.0})
    return pd.DataFrame(rows)


def test_orf_registers():
    s = OpeningRangeAdaptive()
    assert s.id == "opening_range_adaptive"
    assert "or_minutes" in s.parameter_schema and "k_acc" in s.parameter_schema


def test_orf_breakout_up_on_trend_day_emits_CE():
    s = OpeningRangeAdaptive()
    p = s.default_params()
    hist = _session_hist()
    i = len(hist) - 1
    row = hist.iloc[i].copy()
    row["accel_z"] = 1.5
    row["tod_tradeable"] = True
    row["regime_score"] = 0.5
    row["day_type"] = "TREND"
    row["nr7"] = False
    ctx = {"i": i, "history_df": hist, "instrument": "NIFTY"}
    sig = s.evaluate(row, hist.iloc[i - 1], p, ctx)
    assert sig.direction == "CE"


def test_orf_outside_window_is_none():
    s = OpeningRangeAdaptive()
    p = s.default_params()
    hist = _session_hist()
    i = len(hist) - 1
    row = hist.iloc[i].copy()
    row["ist_time"] = "11:30"  # past the opening window
    row["accel_z"] = 1.5
    row["tod_tradeable"] = True
    row["regime_score"] = 0.5
    row["day_type"] = "TREND"
    ctx = {"i": i, "history_df": hist, "instrument": "NIFTY"}
    assert s.evaluate(row, hist.iloc[i - 1], p, ctx).direction == "NONE"


def test_orf_failed_break_fade_on_range_day_emits_PE():
    s = OpeningRangeAdaptive()
    p = s.default_params()
    # build a session that pokes above OR-high then closes back inside on a range day
    rows = []
    for k in range(20):
        mm = 15 + k
        ist = f"09:{mm:02d}" if mm < 60 else f"10:{mm - 60:02d}"
        rows.append({"session_date": "2025-01-03", "ist_time": ist,
                     "high": 105.0, "low": 100.0, "close": 102.0, "atr": 3.0})
    hist = pd.DataFrame(rows)
    i = len(hist) - 1
    prev = hist.iloc[i - 1].copy()
    prev["close"] = 106.0  # prior bar poked above OR-high (105)
    row = hist.iloc[i].copy()
    row["close"] = 104.0   # closed back inside -> failed breakout
    row["accel_z"] = -0.1  # reversion: turning, not strongly counter
    row["tod_tradeable"] = True
    row["regime_score"] = -0.5
    row["day_type"] = "RANGE"
    row["nr7"] = False
    ctx = {"i": i, "history_df": hist, "instrument": "NIFTY"}
    assert s.evaluate(row, prev, p, ctx).direction == "PE"
