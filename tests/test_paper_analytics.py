"""Tests for pure paper-trade analytics."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.paper_analytics import downsample, pnl_series, per_trade_analytics  # noqa: E402


def _trade(events, **kw):
    base = {
        "id": "t1", "created_at": "2026-06-20T04:48:00+00:00",
        "quantity": 75, "entry_price": 100.0,
        "risk": {"stop_price": 80.0, "target_price": 140.0},
        "status": "OPEN", "events": events,
    }
    base.update(kw)
    return base


def test_pnl_series_from_events():
    t = _trade([
        {"type": "OPEN", "at": "2026-06-20T04:48:00+00:00", "price": 100.0},
        {"type": "MARK", "at": "2026-06-20T04:49:00+00:00", "unrealized_pnl": 750.0},
        {"type": "MARK", "at": "2026-06-20T04:50:00+00:00", "unrealized_pnl": -300.0},
    ])
    s = pnl_series(t)
    assert [round(p["pnl"], 1) for p in s] == [0.0, 750.0, -300.0]


def test_per_trade_analytics_mfe_mae_running():
    t = _trade([
        {"type": "OPEN", "at": "2026-06-20T04:48:00+00:00", "price": 100.0},
        {"type": "MARK", "at": "2026-06-20T04:49:00+00:00", "unrealized_pnl": 750.0},
        {"type": "MARK", "at": "2026-06-20T04:50:00+00:00", "unrealized_pnl": -300.0},
        {"type": "MARK", "at": "2026-06-20T04:51:00+00:00", "unrealized_pnl": 525.0},
    ])
    a = per_trade_analytics(t)
    assert a["mfe_value"] == 750.0
    assert a["mae_value"] == -300.0
    assert a["running_pnl"] == 525.0
    assert a["sl"] == 80.0 and a["tp"] == 140.0
    assert a["duration_s"] >= 180
    assert len(a["spark"]) == 4


def test_per_trade_analytics_prefers_stored_mfe_value():
    t = _trade([], mfe_value=900.0, mae_value=-150.0, unrealized_pnl=400.0)
    a = per_trade_analytics(t)
    assert a["mfe_value"] == 900.0 and a["mae_value"] == -150.0


def test_closed_trade_uses_realized_for_running_and_endpoint():
    t = _trade(
        [
            {"type": "OPEN", "at": "2026-06-20T04:48:00+00:00", "price": 100.0},
            {"type": "MARK", "at": "2026-06-20T04:49:00+00:00", "unrealized_pnl": 600.0},
            {"type": "CLOSE", "at": "2026-06-20T05:00:00+00:00", "realized_pnl": 450.0},
        ],
        status="CLOSED", realized_pnl=450.0, closed_at="2026-06-20T05:00:00+00:00",
    )
    a = per_trade_analytics(t)
    assert a["running_pnl"] == 450.0
    assert a["spark"][-1]["pnl"] == 450.0


def test_downsample_keeps_endpoints_and_extremes():
    pts = [{"t": i, "pnl": v} for i, v in enumerate([0, 5, 9, 3, -7, 2, 8, 1, 4, 6, 0])]
    out = downsample(pts, n=5)
    assert len(out) == 5
    assert out[0] == pts[0] and out[-1] == pts[-1]
    vals = [p["pnl"] for p in out]
    assert 9 in vals and -7 in vals  # global max & min preserved


def test_downsample_passthrough_when_small():
    pts = [{"t": 0, "pnl": 1}, {"t": 1, "pnl": 2}]
    assert downsample(pts, n=30) == pts


# ---------------------------------------------------------------------------
# Task 2: period P&L, equity curve, exposure, account roll-up
# ---------------------------------------------------------------------------
from app.paper_analytics import (  # noqa: E402
    period_pnl, build_equity_curve, exposure, build_account_analytics,
)
from datetime import datetime, timezone  # noqa: E402

_DAY = 86_400_000
_BASE = 1_750_000_000_000  # fixed ms; tests are deterministic


def _closed(pnl, closed_ms, instrument="NIFTY", entry=100.0, qty=75):
    return {"status": "CLOSED", "realized_pnl": pnl,
            "closed_at": datetime.fromtimestamp(closed_ms / 1000, tz=timezone.utc).isoformat(),
            "instrument": instrument, "entry_price": entry, "quantity": qty}


def test_period_pnl_buckets_today_and_lifetime():
    now = _BASE
    rows = [_closed(1000, now - 1000), _closed(-200, now - _DAY * 3), _closed(500, now - _DAY * 40)]
    p = period_pnl(rows, now_ms=now)
    assert p["today"] == 1000.0
    assert p["lifetime"] == 1300.0
    assert p["win_rate"] == round(2 / 3 * 100, 1)
    assert p["profit_factor"] == round(1500 / 200, 2)


def test_build_equity_curve_from_capital():
    rows = [_closed(1000, _BASE - _DAY * 2), _closed(-500, _BASE - _DAY), _closed(2000, _BASE)]
    c = build_equity_curve(rows, starting_capital=200_000)
    assert c["starting_capital"] == 200_000
    assert c["account_value_realized"] == 202_500.0
    assert c["max_drawdown_value"] <= 0
    assert c["curve"][-1]["equity_value"] == 202_500.0


def test_exposure_pct_and_by_instrument():
    open_trades = [
        {"entry_price": 100.0, "quantity": 75, "instrument": "NIFTY"},
        {"entry_price": 50.0, "quantity": 30, "instrument": "BANKNIFTY"},
    ]
    e = exposure(open_trades, starting_capital=200_000)
    assert e["deployed_capital"] == 9000.0
    assert e["deployed_pct"] == round(9000 / 200_000 * 100, 2)
    assert e["by_instrument"]["NIFTY"] == 7500.0


def test_build_account_analytics_combines_realized_and_mtm():
    rows = [_closed(1000, _BASE)]
    open_trades = [{"entry_price": 100.0, "quantity": 75, "instrument": "NIFTY",
                    "unrealized_pnl": 300.0}]
    a = build_account_analytics(rows, open_trades, starting_capital=200_000, now_ms=_BASE)
    assert a["account_value_realized"] == 201_000.0
    assert a["open_pnl"] == 300.0
    assert a["account_value_mtm"] == 201_300.0
    assert a["deployed_capital"] == 7500.0
    assert "equity_curve" in a and "period_pnl" in a and "exposure" in a


# ---------------------------------------------------------------------------
# Task 3: per-strategy attribution + contribution
# ---------------------------------------------------------------------------
from app.paper_analytics import per_strategy_stats  # noqa: E402


def test_per_strategy_stats_attribution_and_contribution():
    rows = [
        {"strategy_id": "orr", "deployment_id": "d1", "status": "CLOSED",
         "realized_pnl": 1000.0, "created_at": "2026-06-20T04:00:00+00:00",
         "closed_at": "2026-06-20T04:30:00+00:00"},
        {"strategy_id": "orr", "deployment_id": "d1", "status": "CLOSED",
         "realized_pnl": -200.0, "created_at": "2026-06-20T05:00:00+00:00",
         "closed_at": "2026-06-20T05:20:00+00:00"},
        {"strategy_id": "scalp", "deployment_id": "d2", "status": "OPEN",
         "unrealized_pnl": 300.0},
    ]
    stats = per_strategy_stats(rows)
    by = {s["strategy_id"]: s for s in stats}
    assert by["orr"]["net_pnl"] == 800.0
    assert by["orr"]["closed_trades"] == 2
    assert by["orr"]["win_rate"] == 50.0
    assert by["orr"]["profit_factor"] == 5.0
    assert by["scalp"]["open_count"] == 1
    assert by["scalp"]["open_mtm"] == 300.0
    assert by["orr"]["contribution_pct"] == 100.0


# ---------------------------------------------------------------------------
# Phase 2 Task 1: per-trade R-multiple
# ---------------------------------------------------------------------------

def test_r_multiple_present_when_risk_amount():
    t = _trade([], status="CLOSED", realized_pnl=1800.0, risk_amount=1000.0,
               closed_at="2026-06-20T05:00:00+00:00")
    assert per_trade_analytics(t)["r_multiple"] == 1.8


def test_r_multiple_none_without_risk_amount():
    t = _trade([], status="CLOSED", realized_pnl=1800.0,
               closed_at="2026-06-20T05:00:00+00:00")
    assert per_trade_analytics(t)["r_multiple"] is None


def test_r_multiple_none_when_zero_risk():
    t = _trade([], status="CLOSED", realized_pnl=500.0, risk_amount=0.0,
               closed_at="2026-06-20T05:00:00+00:00")
    assert per_trade_analytics(t)["r_multiple"] is None


# ---------------------------------------------------------------------------
# Phase 2 Task 2: per-strategy avg_r + exit_mix
# ---------------------------------------------------------------------------
from app.paper_analytics import normalize_exit_reason  # noqa: E402


def test_normalize_exit_reason_buckets():
    assert normalize_exit_reason("target_hit") == "target"
    assert normalize_exit_reason("premium_stop") == "stop"
    assert normalize_exit_reason("eod_square_off") == "eod"
    assert normalize_exit_reason("manual_close_at_market") == "manual"
    assert normalize_exit_reason("") == "other"


def test_per_strategy_stats_avg_r_and_exit_mix():
    rows = [
        {"strategy_id": "orr", "deployment_id": "d1", "status": "CLOSED",
         "realized_pnl": 2000.0, "risk_amount": 1000.0, "exit_reason": "target_hit",
         "created_at": "2026-06-20T04:00:00+00:00", "closed_at": "2026-06-20T04:30:00+00:00"},
        {"strategy_id": "orr", "deployment_id": "d1", "status": "CLOSED",
         "realized_pnl": -500.0, "risk_amount": 1000.0, "exit_reason": "premium_stop",
         "created_at": "2026-06-20T05:00:00+00:00", "closed_at": "2026-06-20T05:20:00+00:00"},
    ]
    s = per_strategy_stats(rows)[0]
    assert s["avg_r"] == 0.75            # mean of (2.0, -0.5)
    assert s["exit_mix"]["target"] == 50
    assert s["exit_mix"]["stop"] == 50


def test_per_strategy_avg_r_none_without_risk():
    rows = [{"strategy_id": "x", "status": "CLOSED", "realized_pnl": 100.0,
             "exit_reason": "target", "created_at": "2026-06-20T04:00:00+00:00",
             "closed_at": "2026-06-20T04:10:00+00:00"}]
    assert per_strategy_stats(rows)[0]["avg_r"] is None


# ---------------------------------------------------------------------------
# Phase 2 Task 3: drift_compare pure combiner
# ---------------------------------------------------------------------------
from app.paper_analytics import drift_compare  # noqa: E402


def test_drift_no_baseline_when_params_mismatch():
    out = drift_compare({"win_rate": 55, "avg": 100, "visible": True},
                        {"win_rate": 60, "avg": 120, "params_match": False})
    assert out["state"] == "no_baseline"


def test_drift_insufficient_sample_when_not_visible():
    out = drift_compare({"win_rate": 55, "avg": 100, "visible": False},
                        {"win_rate": 60, "avg": 120, "params_match": True})
    assert out["state"] == "insufficient_sample"
    assert out["base_win_rate"] == 60


def test_drift_ok_with_deltas():
    out = drift_compare({"win_rate": 54, "avg": 90, "visible": True},
                        {"win_rate": 60, "avg": 120, "params_match": True})
    assert out["state"] == "ok"
    assert out["win_rate_delta"] == -6.0
    assert out["avg_delta"] == -30.0


# ---------------------------------------------------------------------------
# Fix: no-loss profit_factor must be JSON-safe (was float("inf") -> 500)
# ---------------------------------------------------------------------------
import json as _json  # noqa: E402


def test_per_strategy_stats_no_losses_profit_factor_none_and_json_safe():
    rows = [
        {"strategy_id": "allwin", "deployment_id": "d9", "status": "CLOSED",
         "realized_pnl": 500.0, "created_at": "2026-06-20T04:00:00+00:00",
         "closed_at": "2026-06-20T04:10:00+00:00"},
        {"strategy_id": "allwin", "deployment_id": "d9", "status": "CLOSED",
         "realized_pnl": 300.0, "created_at": "2026-06-20T05:00:00+00:00",
         "closed_at": "2026-06-20T05:10:00+00:00"},
    ]
    by = {s["strategy_id"]: s for s in per_strategy_stats(rows)}
    assert by["allwin"]["profit_factor"] is None    # no losses -> None, not float('inf')
    assert by["allwin"]["win_rate"] == 100.0
    _json.dumps(per_strategy_stats(rows), allow_nan=False)   # FastAPI uses allow_nan=False


def test_period_pnl_no_losses_profit_factor_none_and_json_safe():
    rows = [_closed(1000, _BASE - 1000), _closed(500, _BASE - _DAY)]
    p = period_pnl(rows, now_ms=_BASE)
    assert p["profit_factor"] is None
    assert p["win_rate"] == 100.0
    _json.dumps(p, allow_nan=False)


def test_json_safe_floats_replaces_nan_inf():
    from app.paper_analytics import json_safe_floats
    src = {"a": float("inf"), "b": float("nan"), "c": [1.0, float("-inf"), 2],
           "d": {"e": float("inf")}, "f": "x", "g": 3, "h": True}
    out = json_safe_floats(src)
    assert out == {"a": None, "b": None, "c": [1.0, None, 2],
                   "d": {"e": None}, "f": "x", "g": 3, "h": True}
    _json.dumps(out, allow_nan=False)


def test_f_rejects_nan_inf():
    from app.paper_analytics import _f
    assert _f(float("inf")) == 0.0
    assert _f(float("nan")) == 0.0
    assert _f(float("-inf")) == 0.0
    assert _f("inf") == 0.0
    assert _f(3.5) == 3.5
    assert _f(None) == 0.0
