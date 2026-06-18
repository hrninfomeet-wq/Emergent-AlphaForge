# tests/test_backtest_characterization.py
"""BYTE-IDENTICAL characterization gate for app.backtest.run_backtest.

This test pins the EXACT output (trades + metrics) that the current run_backtest
produces on a fixed, enriched multi-session fixture. Its sole purpose is to catch
ANY behavioral drift introduced by per-bar micro-optimizations to the core loop.

The golden values below were captured from the code as it stood BEFORE the
micro-opts (guarded reset_index, Trade override fields, ctx reuse). If a change
alters any trade or metric, this test must FAIL -- do NOT update the golden to
match; revert the offending change instead.

Coverage:
  - confluence_scalper      -> plain spot path (default target/stop, EOD close)
  - adaptive_regime_scalper -> ctx_local + ATR-derived per-trade target/stop
                               overrides + cooldown churn (47 trades)
  - opening_range_breakout  -> ORB ctx (orb_hi/orb_lo per session) path
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

import numpy as np
import pytest

from app.indicators import precompute_all_indicators
from app.regime import classify_regime_series
from app.strategies.base import get_registry
from app.backtest import run_backtest
from tests._adaptive_testutil import make_sessions


# --- Fixed fixture (deterministic seed). Five sessions of varied, trending
# price action so confluence / adaptive / ORB all actually fire trades. ---
def _build_df():
    rng = np.random.default_rng(42)
    sessions = []
    for s in range(5):
        base = 100.0 + rng.standard_normal(200).cumsum() * 0.8
        trend = np.linspace(0, (s % 2 * 2 - 1) * 8, 200)
        sessions.append(list(base + trend))
    return make_sessions(sessions, start_date="2025-01-06")


def _enrich(df):
    enr = precompute_all_indicators(df)
    enr["regime"] = classify_regime_series(enr)
    return enr


@pytest.fixture(scope="module")
def enriched_df():
    return _enrich(_build_df())


@pytest.fixture(scope="module")
def registry():
    reg = get_registry()
    reg.auto_discover()
    return reg


# --- GOLDEN: confluence_scalper (pinned EXACTLY -- metrics dict + every trade) ---
_CONFLUENCE_METRICS = {
    "trade_count": 3, "wins": 1, "losses": 2, "win_rate": 33.33,
    "profit_factor": 0.158, "avg_pnl_pts": -10.94, "expectancy_pts": -10.94,
    "max_dd_pts": -19.5, "sharpe": -14.347, "best_pts": 6.18, "worst_pts": -19.5,
    "target_exits": 0, "stop_exits": 2, "time_exits": 1, "total_pnl_pts": -32.82,
    "avg_bars_held": 151.5,
}

# (direction, entry_ts, exit_ts, entry_price, exit_price, exit_reason, pnl_pts, bars_held, score)
_CONFLUENCE_TRADES = [
    ("PE", 1736140920000, 1736231400000, 92.48349745676349, 110.48349745676349, "STOP", -19.5, 268, 70),
    ("CE", 1736232000000, 1736308500000, 114.79151047009444, 96.79151047009444, "STOP", -19.5, 35, 70),
    ("PE", 1736308560000, 1736492640000, 95.0025274312124, 87.32358778436486, "EOD", 6.179, 0, 70),
]


def test_confluence_scalper_byte_identical(enriched_df, registry):
    strat = registry.get("confluence_scalper")
    res = run_backtest(enriched_df.copy(), strat, strat.default_params())
    metrics = res["metrics"]
    trades = res["trades"]

    # Exact metrics equality (every key, every value).
    assert metrics == _CONFLUENCE_METRICS

    # Exact trade-by-trade equality on all load-bearing fields.
    assert len(trades) == len(_CONFLUENCE_TRADES)
    for got, exp in zip(trades, _CONFLUENCE_TRADES):
        d, ets, xts, ep, xp, xr, pnl, bh, sc = exp
        assert got["direction"] == d
        assert got["entry_ts"] == ets
        assert got["exit_ts"] == xts
        assert got["entry_price"] == ep
        assert got["exit_price"] == xp
        assert got["exit_reason"] == xr
        assert got["pnl_pts"] == pnl
        assert got["bars_held"] == bh
        assert got["score"] == sc

    # T9 serialization contract: scenario is emitted (empty for non-routed strategies),
    # and spot_target_level (internal bookkeeping) never leaks into the serialized dict.
    for got in trades:
        assert got["scenario"] == ""
        assert "spot_target_level" not in got


# --- GOLDEN: adaptive_regime_scalper (ctx_local + ATR override + cooldown) ---
_ADAPTIVE_METRICS = {
    "trade_count": 47, "wins": 11, "losses": 36, "win_rate": 23.4,
    "profit_factor": 0.055, "avg_pnl_pts": -1.838, "expectancy_pts": -1.838,
    "max_dd_pts": -83.8, "sharpe": -22.914, "best_pts": 0.62, "worst_pts": -2.96,
    "target_exits": 11, "stop_exits": 36, "time_exits": 0, "total_pnl_pts": -86.37,
    "avg_bars_held": 2.7,
}

# First three trades + the final trade, all fields. The ATR-derived per-trade
# stop overrides mean exit_price/pnl differ per trade -- pinning these catches
# any drift in the Trade-override-field path.
_ADAPTIVE_TRADES_SPOT = {
    0: ("CE", 1736223420000, 1736223660000, 100.396177, 99.326177, "STOP", -2.57, 4, 59),
    1: ("CE", 1736227020000, 1736227080000, 102.016986, 101.056986, "STOP", -2.46, 1, 100),
    2: ("PE", 1736228160000, 1736228220000, 103.084789, 104.124789, "STOP", -2.54, 1, 67),
    -1: ("PE", 1736491860000, 1736492160000, 82.174087, 83.154087, "STOP", -2.48, 5, 71),
}


def test_adaptive_regime_scalper_byte_identical(enriched_df, registry):
    strat = registry.get("adaptive_regime_scalper")
    res = run_backtest(enriched_df.copy(), strat, strat.default_params())
    metrics = res["metrics"]
    trades = res["trades"]

    assert metrics == _ADAPTIVE_METRICS
    assert len(trades) == 47

    for idx, exp in _ADAPTIVE_TRADES_SPOT.items():
        got = trades[idx]
        d, ets, xts, ep, xp, xr, pnl, bh, sc = exp
        assert got["direction"] == d, f"trade[{idx}] direction"
        assert got["entry_ts"] == ets, f"trade[{idx}] entry_ts"
        assert got["exit_ts"] == xts, f"trade[{idx}] exit_ts"
        assert round(got["entry_price"], 6) == ep, f"trade[{idx}] entry_price"
        assert round(got["exit_price"], 6) == xp, f"trade[{idx}] exit_price"
        assert got["exit_reason"] == xr, f"trade[{idx}] exit_reason"
        assert got["pnl_pts"] == pnl, f"trade[{idx}] pnl_pts"
        assert got["bars_held"] == bh, f"trade[{idx}] bars_held"
        assert got["score"] == sc, f"trade[{idx}] score"


# --- GOLDEN: opening_range_breakout (exercises orb_hi/orb_lo ctx path) ---
_ORB_METRICS = {
    "trade_count": 2, "wins": 1, "losses": 1, "win_rate": 50.0,
    "profit_factor": 0.154, "avg_pnl_pts": -8.245, "expectancy_pts": -8.245,
    "max_dd_pts": 0.0, "sharpe": -11.629, "best_pts": 3.01, "worst_pts": -19.5,
    "target_exits": 0, "stop_exits": 1, "time_exits": 1, "total_pnl_pts": -16.49,
    "avg_bars_held": 278.0,
}

_ORB_TRADES = [
    ("PE", 1736140800000, 1736231880000, 94.419857, 112.419857, "STOP", -19.5, 278, 70),
    ("PE", 1736310120000, 1736492640000, 91.834, 87.323588, "EOD", 3.01, 0, 70),
]


def test_opening_range_breakout_byte_identical(enriched_df, registry):
    strat = registry.get("opening_range_breakout")
    res = run_backtest(enriched_df.copy(), strat, strat.default_params())
    metrics = res["metrics"]
    trades = res["trades"]

    assert metrics == _ORB_METRICS
    assert len(trades) == len(_ORB_TRADES)
    for got, exp in zip(trades, _ORB_TRADES):
        d, ets, xts, ep, xp, xr, pnl, bh, sc = exp
        assert got["direction"] == d
        assert got["entry_ts"] == ets
        assert got["exit_ts"] == xts
        assert round(got["entry_price"], 6) == ep
        assert round(got["exit_price"], 6) == xp
        assert got["exit_reason"] == xr
        assert got["pnl_pts"] == pnl
        assert got["bars_held"] == bh
        assert got["score"] == sc

    # T9 serialization contract: scenario is emitted (empty for non-routed strategies),
    # and spot_target_level (internal bookkeeping) never leaks into the serialized dict.
    for got in trades:
        assert got["scenario"] == ""
        assert "spot_target_level" not in got
