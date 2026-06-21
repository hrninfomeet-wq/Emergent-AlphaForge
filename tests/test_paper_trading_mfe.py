import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.paper_trading import paper_trade_from_signal, mark_trade_to_market, close_trade  # noqa: E402


def _signal():
    return {"id": "s1", "instrument": "NIFTY", "direction": "CE", "strategy_id": "orr",
            "entry_price": 100.0, "option_contract": {"lot_size": 75, "instrument_key": "k"}}


def test_mark_tracks_running_mfe_mae():
    t = paper_trade_from_signal(_signal(), lots=1, entry_price=100.0)
    t = mark_trade_to_market(t, last_price=110.0)   # +750 (qty 75)
    t = mark_trade_to_market(t, last_price=96.0)    # -300
    t = mark_trade_to_market(t, last_price=107.0)   # +525
    assert t["mfe_value"] == 750.0
    assert t["mae_value"] == -300.0


def test_close_preserves_mfe_mae():
    t = paper_trade_from_signal(_signal(), lots=1, entry_price=100.0)
    t = mark_trade_to_market(t, last_price=120.0)   # +1500
    closed = close_trade(t, exit_price=108.0, reason="target")
    assert closed["mfe_value"] == 1500.0
    assert closed["status"] == "CLOSED"
