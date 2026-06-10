"""Tests for the honest walk-forward optimization module (app/wfo.py).

These cover the trading-critical pure math: window splitting (rolling and
anchored, trading-day based), stitched OOS metrics (must mirror
backtest.compute_metrics formulas), walk-forward efficiency, OOS consistency,
and parameter stability. The async job runner reuses the proven optimizer
worker patterns and is exercised by the API smoke test.
"""
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.wfo import (  # noqa: E402
    oos_consistency,
    param_stability,
    split_windows,
    stitch_equity_curve,
    stitch_oos_metrics,
    walk_forward_efficiency,
)


def _dates(n, start_day=1):
    """n synthetic ISO session dates (lexicographically sorted)."""
    return [f"2025-01-{d:02d}" if d <= 31 else f"2025-02-{d-31:02d}" for d in range(start_day, start_day + n)]


# ---------------------------------------------------------------------------
# split_windows
# ---------------------------------------------------------------------------

def test_rolling_windows_are_contiguous_and_non_overlapping_oos():
    dates = _dates(50)
    res = split_windows(dates, train_days=20, test_days=10, wf_mode="rolling")
    ws = res["windows"]
    assert len(ws) == 3  # starts at 0, 10, 20 → last needs 20+10 <= 50-start
    for w in ws:
        assert w["train_day_count"] == 20
        assert w["test_day_count"] == 10
        # test window begins the session right after train ends
        assert w["train_end"] < w["test_start"]
    # OOS segments are contiguous: window k+1 test starts right after window k test ends
    assert ws[0]["test_end"] < ws[1]["test_start"]
    assert ws[1]["test_end"] < ws[2]["test_start"]
    # first window trains on the first date, last window tests on the last date
    assert ws[0]["train_start"] == dates[0]
    assert ws[-1]["test_end"] == dates[49]


def test_rolling_window_boundaries_are_exact():
    dates = _dates(30)
    res = split_windows(dates, train_days=20, test_days=10, wf_mode="rolling")
    ws = res["windows"]
    assert len(ws) == 1
    w = ws[0]
    assert w["train_start"] == dates[0]
    assert w["train_end"] == dates[19]
    assert w["test_start"] == dates[20]
    assert w["test_end"] == dates[29]


def test_anchored_windows_grow_train():
    dates = _dates(50)
    res = split_windows(dates, train_days=20, test_days=10, wf_mode="anchored")
    ws = res["windows"]
    assert len(ws) == 3
    assert [w["train_day_count"] for w in ws] == [20, 30, 40]
    for w in ws:
        assert w["train_start"] == dates[0]  # anchored at the first session
    assert ws[-1]["test_end"] == dates[49]


def test_insufficient_data_returns_empty():
    assert split_windows(_dates(25), 20, 10)["windows"] == []
    assert split_windows([], 20, 10)["windows"] == []


def test_step_defaults_to_test_days_and_custom_step_overlaps():
    dates = _dates(60)
    default = split_windows(dates, 20, 10)["windows"]
    stepped = split_windows(dates, 20, 10, step_days=5)["windows"]
    assert len(stepped) > len(default)  # smaller step → more (overlapping-train) windows


def test_max_windows_drops_oldest_keeps_newest():
    dates = _dates(60)
    # uncapped: starts at 0,10,20,30 → 4 windows
    assert len(split_windows(dates, 20, 10, max_windows=12)["windows"]) == 4
    res = split_windows(dates, 20, 10, max_windows=2)
    ws = res["windows"]
    assert len(ws) == 2
    assert res["dropped_oldest"] == 2
    assert ws[-1]["test_end"] == dates[59]  # newest window survives the cap
    assert [w["index"] for w in ws] == [0, 1]  # re-indexed after the drop


def test_invalid_inputs_return_empty():
    dates = _dates(50)
    assert split_windows(dates, 0, 10)["windows"] == []
    assert split_windows(dates, 20, 0)["windows"] == []
    assert split_windows(dates, 20, 10, step_days=0)["windows"] == []


# ---------------------------------------------------------------------------
# stitched OOS metrics + equity
# ---------------------------------------------------------------------------

def _trade(pnl, ts=0):
    return {"pnl_pts": pnl, "exit_ts": ts, "exit_datetime": ""}


def test_stitch_metrics_basic():
    trades = [_trade(10), _trade(-5), _trade(20), _trade(-5)]
    m = stitch_oos_metrics(trades)
    assert m["trade_count"] == 4
    assert m["wins"] == 2
    assert m["losses"] == 2
    assert m["win_rate"] == 50.0
    assert m["total_pnl_pts"] == 20.0
    assert m["profit_factor"] == 3.0  # 30 gross profit / 10 gross loss
    assert m["sharpe"] is not None


def test_stitch_metrics_max_drawdown():
    # equity path: 10, 0, -10, 10 → peak 10, trough -10 → max dd -20
    trades = [_trade(10), _trade(-10), _trade(-10), _trade(20)]
    m = stitch_oos_metrics(trades)
    assert m["max_dd_pts"] == -20.0


def test_stitch_metrics_empty():
    m = stitch_oos_metrics([])
    assert m["trade_count"] == 0
    assert m["profit_factor"] is None
    assert m["total_pnl_pts"] == 0.0


def test_stitch_metrics_all_wins_has_no_profit_factor():
    m = stitch_oos_metrics([_trade(5), _trade(5)])
    assert m["profit_factor"] is None  # no losses → undefined, not inf


def test_stitch_equity_curve_running_drawdown():
    curve = stitch_equity_curve([_trade(10, 1), _trade(-15, 2), _trade(5, 3)])
    assert [p["equity_pts"] for p in curve] == [10.0, -5.0, 0.0]
    assert [p["drawdown_pts"] for p in curve] == [0.0, -15.0, -10.0]


# ---------------------------------------------------------------------------
# efficiency + consistency
# ---------------------------------------------------------------------------

def _window(is_pnl, oos_pnl, train_days=20, test_days=10):
    return {
        "is_metrics": {"total_pnl_pts": is_pnl},
        "oos_metrics": {"total_pnl_pts": oos_pnl},
        "train_day_count": train_days,
        "test_day_count": test_days,
    }


def test_efficiency_full_survival_is_one():
    # IS: 100 pts over 20 days = 5/day. OOS: 50 pts over 10 days = 5/day.
    ws = [_window(100, 50)]
    assert walk_forward_efficiency(ws) == 1.0


def test_efficiency_half_survival():
    ws = [_window(100, 25)]  # IS 5/day, OOS 2.5/day
    assert walk_forward_efficiency(ws) == 0.5


def test_efficiency_negative_oos_is_negative():
    ws = [_window(100, -25)]
    assert walk_forward_efficiency(ws) == -0.5


def test_efficiency_none_when_is_pnl_not_positive():
    assert walk_forward_efficiency([_window(0, 10)]) is None
    assert walk_forward_efficiency([_window(-50, 10)]) is None
    assert walk_forward_efficiency([]) is None


def test_consistency_counts_positive_oos_windows():
    ws = [_window(10, 5), _window(10, -5), _window(10, 1)]
    c = oos_consistency(ws)
    assert c["windows"] == 3
    assert c["positive_windows"] == 2
    assert c["consistency_pct"] == 66.7


# ---------------------------------------------------------------------------
# param stability
# ---------------------------------------------------------------------------

SPACE = {
    "rsi_buy": {"type": "int", "min": 10, "max": 50},
    "atr_mult": {"type": "float", "min": 0.5, "max": 4.5},
    "use_vwap": {"type": "bool"},
    "locked": {"type": "int", "min": 1, "max": 9, "fixed": 5},
}


def test_stability_stable_param_has_low_spread():
    params = [{"rsi_buy": 30}, {"rsi_buy": 30}, {"rsi_buy": 32}]
    rows = param_stability(params, SPACE)
    row = next(r for r in rows if r["param"] == "rsi_buy")
    assert row["rel_spread"] == 0.05  # (32-30)/(50-10)
    assert row["median"] == 30


def test_stability_wandering_param_has_high_spread():
    params = [{"atr_mult": 0.5}, {"atr_mult": 4.5}]
    rows = param_stability(params, SPACE)
    row = next(r for r in rows if r["param"] == "atr_mult")
    assert row["rel_spread"] == 1.0


def test_stability_bool_agreement():
    params = [{"use_vwap": True}, {"use_vwap": True}, {"use_vwap": False}]
    rows = param_stability(params, SPACE)
    row = next(r for r in rows if r["param"] == "use_vwap")
    assert row["agreement_pct"] == 66.7


def test_stability_skips_fixed_params_and_sorts_by_spread():
    params = [
        {"rsi_buy": 30, "atr_mult": 0.5, "locked": 5},
        {"rsi_buy": 31, "atr_mult": 4.5, "locked": 5},
    ]
    rows = param_stability(params, SPACE)
    assert all(r["param"] != "locked" for r in rows)
    assert rows[0]["param"] == "atr_mult"  # widest spread first


def test_stability_empty():
    assert param_stability([], SPACE) == []
