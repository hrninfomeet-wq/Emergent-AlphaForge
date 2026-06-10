"""Tests for the market-context (regime/time-of-day/DTE/VIX) classification."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.market_context import (  # noqa: E402
    time_of_day_bucket,
    vix_bucket,
    build_trade_context,
    ist_time_from_ts,
)
from app.vix import build_asof_index, vix_asof, annotate_trades_with_vix  # noqa: E402


def test_time_of_day_buckets():
    assert time_of_day_bucket("09:14") == "PRE_OPEN"
    assert time_of_day_bucket("09:20") == "OPEN"
    assert time_of_day_bucket("09:25") == "MORNING"
    assert time_of_day_bucket("10:30") == "MORNING"
    assert time_of_day_bucket("11:00") == "MIDDAY"
    assert time_of_day_bucket("13:00") == "MIDDAY"
    assert time_of_day_bucket("13:30") == "AFTERNOON"
    assert time_of_day_bucket("15:00") == "CLOSE"
    assert time_of_day_bucket("15:35") == "POST_CLOSE"
    assert time_of_day_bucket("garbage") == "UNKNOWN"


def test_vix_buckets():
    assert vix_bucket(None) == "UNKNOWN"
    assert vix_bucket(10.5) == "LOW"
    assert vix_bucket(13.0) == "NORMAL"
    assert vix_bucket(17.0) == "ELEVATED"
    assert vix_bucket(25.0) == "HIGH"


def test_build_trade_context_assembles_fields():
    ctx = build_trade_context(regime="TREND", ist_time="10:30", dte=0, vix=16.4)
    assert ctx["regime"] == "TREND"
    assert ctx["time_of_day"] == "MORNING"
    assert ctx["dte"] == 0
    assert ctx["vix"] == 16.4
    assert ctx["vix_bucket"] == "ELEVATED"


def test_build_trade_context_derives_time_from_ts():
    # 2025-06-17 10:00 IST = 04:30 UTC.
    ts = 1750132800000  # 2025-06-17T04:30:00Z
    hm = ist_time_from_ts(ts)
    ctx = build_trade_context(regime="CHOP", ts_ms=ts)
    assert ctx["ist_time"] == hm
    assert ctx["time_of_day"] in ("MORNING", "OPEN", "MIDDAY")  # depends on exact ts


# ---- VIX as-of join ----------------------------------------------------------

def test_vix_asof_returns_most_recent_at_or_before():
    candles = [
        {"ts": 1000, "close": 12.0},
        {"ts": 2000, "close": 14.0},
        {"ts": 3000, "close": 16.0},
    ]
    idx = build_asof_index(candles)
    assert vix_asof(idx, 999) is None      # before first
    assert vix_asof(idx, 1000) == 12.0     # exact
    assert vix_asof(idx, 2500) == 14.0     # between -> earlier
    assert vix_asof(idx, 9999) == 16.0     # after last -> last


def test_vix_asof_respects_staleness():
    idx = build_asof_index([{"ts": 1000, "close": 12.0}])
    assert vix_asof(idx, 5000, max_staleness_ms=1000) is None  # 4000ms stale > 1000
    assert vix_asof(idx, 1500, max_staleness_ms=1000) == 12.0  # 500ms within limit


def test_annotate_trades_with_vix():
    trades = [
        {"entry_ts": 2500},
        {"entry_ts": 500},   # before first VIX -> stays None
    ]
    candles = [{"ts": 1000, "close": 12.0}, {"ts": 2000, "close": 14.0}]
    tagged = annotate_trades_with_vix(trades, candles)
    assert tagged == 1
    assert trades[0]["vix"] == 14.0
    assert "vix" not in trades[1]
