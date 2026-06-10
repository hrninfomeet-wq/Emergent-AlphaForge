"""Tests for the explosive-reversal confluence primitives (causal, no look-ahead)."""
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.context_signals import (  # noqa: E402
    round_level_proximity,
    recent_sr_levels,
    nearest_sr_proximity,
    rsi_divergence,
    macd_divergence,
    reversal_candle,
)


def test_round_level_proximity_nifty():
    # NIFTY step 100/50; price 24502 nearest round = 24500.
    r = round_level_proximity(24502.0, "NIFTY", atr=10.0)
    assert r["nearest_level"] == 24500.0
    assert r["distance_pts"] == 2.0
    assert r["is_near"] is True  # 2/10 = 0.2 ATR <= 0.5


def test_round_level_proximity_banknifty_500():
    # BANKNIFTY step 500; price 51230 nearest = 51000 (closer than 51500).
    r = round_level_proximity(51230.0, "BANKNIFTY", atr=50.0)
    assert r["nearest_level"] == 51000.0
    assert r["is_near"] is False  # 230/50 = 4.6 ATR


def test_round_level_proximity_far():
    r = round_level_proximity(24550.0, "NIFTY", atr=5.0)
    # 24550 is 0 from a 50-grid level -> near; use 24563 to be off-grid.
    r2 = round_level_proximity(24563.0, "NIFTY", atr=5.0)
    assert r2["is_near"] is False  # 13 pts / 5 = 2.6 ATR


def test_reversal_candle_bullish_and_bearish():
    bull = pd.Series({"open": 100, "high": 101, "low": 90, "close": 100.5})  # long lower wick, close high
    assert reversal_candle(bull) == "BULLISH"
    bear = pd.Series({"open": 100, "high": 110, "low": 99, "close": 99.5})   # long upper wick, close low
    assert reversal_candle(bear) == "BEARISH"
    doji = pd.Series({"open": 100, "high": 101, "low": 99, "close": 100})    # balanced
    assert reversal_candle(doji) is None


def _frame_with_swings():
    # Build a small frame with explicit swing flags + rsi/macd for divergence.
    rows = []
    n = 50
    for k in range(n):
        rows.append({
            "high": 100 + k, "low": 90 + k, "close": 95 + k, "open": 95 + k,
            "atr": 5.0, "rsi": 50.0, "macd_hist": 0.0,
            "is_swing_high": False, "is_swing_low": False,
        })
    return pd.DataFrame(rows)


def test_rsi_bearish_divergence():
    df = _frame_with_swings()
    # Two swing highs: first at idx 10 (price 120, rsi 75), second at idx 30 (price 140, rsi 65).
    df.loc[10, ["high", "rsi", "is_swing_high"]] = [120.0, 75.0, True]
    df.loc[30, ["high", "rsi", "is_swing_high"]] = [140.0, 65.0, True]  # higher high, lower rsi
    assert rsi_divergence(df, 35, lookback=40) == "BEARISH"


def test_rsi_bullish_divergence():
    df = _frame_with_swings()
    df.loc[10, ["low", "rsi", "is_swing_low"]] = [80.0, 25.0, True]
    df.loc[30, ["low", "rsi", "is_swing_low"]] = [70.0, 35.0, True]  # lower low, higher rsi
    assert rsi_divergence(df, 35, lookback=40) == "BULLISH"


def test_rsi_no_divergence_returns_none():
    df = _frame_with_swings()
    df.loc[10, ["high", "rsi", "is_swing_high"]] = [120.0, 65.0, True]
    df.loc[30, ["high", "rsi", "is_swing_high"]] = [140.0, 75.0, True]  # higher high + higher rsi = no div
    assert rsi_divergence(df, 35, lookback=40) is None


def test_macd_bearish_divergence():
    df = _frame_with_swings()
    df.loc[10, ["high", "macd_hist", "is_swing_high"]] = [120.0, 5.0, True]
    df.loc[30, ["high", "macd_hist", "is_swing_high"]] = [140.0, 2.0, True]
    assert macd_divergence(df, 35, lookback=40) == "BEARISH"


def test_sr_levels_and_proximity():
    df = _frame_with_swings()
    df.loc[15, ["high", "is_swing_high"]] = [135.0, True]
    df.loc[25, ["low", "is_swing_low"]] = [88.0, True]
    levels = recent_sr_levels(df, 40, lookback=60)
    assert any(abs(r - 135.0) < 2 for r in levels["resistance"])
    assert any(abs(s - 88.0) < 2 for s in levels["support"])
    prox = nearest_sr_proximity(135.5, levels, atr=5.0)
    assert prox["kind"] == "resistance"
    assert prox["is_near"] is True


def test_divergence_is_causal_no_lookahead():
    # A divergence formed by a FUTURE bar must not be visible at an earlier i.
    df = _frame_with_swings()
    df.loc[10, ["high", "rsi", "is_swing_high"]] = [120.0, 75.0, True]
    df.loc[45, ["high", "rsi", "is_swing_high"]] = [140.0, 65.0, True]  # second high is in the future
    # At i=20 the second swing (idx 45) hasn't happened -> no divergence yet.
    assert rsi_divergence(df, 20, lookback=40) is None
