import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

import pandas as pd
import pytest
from app.strategies.plugins.gap_fade import GapFade


def _hist(gap_open, prev_close=100.0, cur_close=104.0, n_cur=35):
    """Prior session (5 bars @ prev_close) + current session (n_cur bars; bar 0 opens at gap_open)."""
    rows = []
    for k in range(5):  # prior session
        rows.append({"session_date": "2025-01-02", "ist_time": f"09:{15 + k:02d}",
                     "open": prev_close, "high": prev_close + 0.5, "low": prev_close - 0.5, "close": prev_close})
    for k in range(n_cur):  # current session
        mm = 15 + k
        ist = f"09:{mm:02d}" if mm < 60 else f"10:{mm - 60:02d}"
        op = gap_open if k == 0 else cur_close
        rows.append({"session_date": "2025-01-03", "ist_time": ist,
                     "open": op, "high": max(op, cur_close) + 0.5, "low": min(op, cur_close) - 0.5, "close": cur_close})
    return pd.DataFrame(rows)


def _row(hist, **kw):
    i = len(hist) - 1
    base = hist.iloc[i].to_dict()
    base.update({"atr": 2.5, "rsi": 75.0, "vwap": 104.0, "cpr_p": 100.0,
                 "regime_score": 0.0, "accel_z": -0.2, "tod_tradeable": True})
    base.update(kw)
    return pd.Series(base), i


def _ctx(hist, i):
    return {"i": i, "history_df": hist, "instrument": "NIFTY"}


def test_gap_registers_and_merges_params():
    s = GapFade()
    assert s.id == "gap_fade"
    assert "g_min_atr" in s.parameter_schema and "k_acc" in s.parameter_schema


def test_gap_up_overbought_fades_PE():
    s = GapFade(); p = s.default_params()
    hist = _hist(gap_open=105.0)  # +5 over a 2.5 ATR -> 2.0 ATR gap
    row, i = _row(hist)
    assert s.evaluate(row, hist.iloc[i - 1], p, _ctx(hist, i)).direction == "PE"


def test_gap_down_oversold_fades_CE():
    s = GapFade(); p = s.default_params()
    hist = _hist(gap_open=95.0, cur_close=96.0)  # -5 gap
    row, i = _row(hist, rsi=25.0, accel_z=0.2, close=96.0, vwap=96.0)
    assert s.evaluate(row, hist.iloc[i - 1], p, _ctx(hist, i)).direction == "CE"


def test_gap_small_is_none():
    s = GapFade(); p = s.default_params()
    hist = _hist(gap_open=101.0)  # +1 over 2.5 ATR = 0.4 ATR < 1.0
    row, i = _row(hist)
    assert s.evaluate(row, hist.iloc[i - 1], p, _ctx(hist, i)).direction == "NONE"


def test_gap_breakaway_is_skipped():
    s = GapFade(); p = s.default_params()
    hist = _hist(gap_open=105.0)
    row, i = _row(hist, regime_score=0.5, accel_z=1.0)  # gap WITH strong accel trend -> breakaway
    assert s.evaluate(row, hist.iloc[i - 1], p, _ctx(hist, i)).direction == "NONE"


def test_gap_pre_confirm_window_is_none():
    s = GapFade(); p = s.default_params()
    hist = _hist(gap_open=105.0)
    row, i = _row(hist, ist_time="09:20")  # before confirm_hhmm 09:45
    assert s.evaluate(row, hist.iloc[i - 1], p, _ctx(hist, i)).direction == "NONE"
