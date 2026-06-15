import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from app.paper_open_positions import build_open_positions


def test_open_positions_live_unrealized_from_tick():
    trades = [{
        "id": "t1", "status": "OPEN", "instrument_key": "NSE_FO|50614",
        "entry_price": 200.0, "quantity": 75, "lots": 1,
        "stop_price": 170.0, "target_price": 260.0,
    }]
    out = build_open_positions(trades, latest_tick_lookup=lambda k: {"last_price": 230.0, "ts": None})
    p = out["items"][0]
    assert p["live_premium"] == 230.0
    assert round(p["unrealized_pnl"], 2) == 2250.0          # (230-200)*75
    assert p["dist_to_stop"] == round(230.0 - 170.0, 2)
    assert p["dist_to_target"] == round(260.0 - 230.0, 2)
    assert out["open_mtm"] == round(2250.0, 2)


def test_open_positions_stale_falls_back_to_persisted_mark():
    trades = [{
        "id": "t2", "status": "OPEN", "instrument_key": "NSE_FO|99999",
        "entry_price": 100.0, "quantity": 75, "lots": 1, "unrealized_pnl": -750.0,
    }]
    out = build_open_positions(trades, latest_tick_lookup=lambda k: None)
    p = out["items"][0]
    assert p["live_stale"] is True
    assert p["unrealized_pnl"] == -750.0
