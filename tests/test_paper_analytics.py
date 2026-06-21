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
