import sys
import types
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

motor_module = types.ModuleType("motor")
motor_asyncio_module = types.ModuleType("motor.motor_asyncio")


class DummyMotorClient:
    pass


motor_asyncio_module.AsyncIOMotorClient = DummyMotorClient
sys.modules.setdefault("motor", motor_module)
sys.modules.setdefault("motor.motor_asyncio", motor_asyncio_module)

from app import upstox_client  # noqa: E402


def test_normalize_market_quote_keeps_only_safe_snapshot_fields():
    payload = {
        "status": "success",
        "data": {
            "NSE_INDEX:Nifty 50": {
                "instrument_token": "NSE_INDEX|Nifty 50",
                "symbol": "Nifty 50",
                "last_price": 24985.35,
                "timestamp": "1779774123000",
                "last_trade_time": "1779774122000",
                "volume": 123456,
                "net_change": 42.5,
                "oi": 0,
                "ohlc": {"open": 24900, "high": 25010, "low": 24880, "close": 24942.85},
                "depth": {"buy": [{"price": 1, "quantity": 1}]},
            }
        },
    }

    quote = upstox_client.normalize_market_quote(payload, "NIFTY")

    assert quote == {
        "underlying": "NIFTY",
        "instrument_key": "NSE_INDEX|Nifty 50",
        "raw_key": "NSE_INDEX:Nifty 50",
        "symbol": "Nifty 50",
        "last_price": 24985.35,
        "timestamp": "1779774123000",
        "last_trade_time": "1779774122000",
        "volume": 123456,
        "oi": 0,
        "net_change": 42.5,
        "ohlc": {"open": 24900, "high": 25010, "low": 24880, "close": 24942.85},
        "source": "upstox_market_quote",
    }
    assert "depth" not in quote


def test_backend_exposes_upstox_market_quote_route():
    server = (ROOT / "backend" / "server.py").read_text(encoding="utf-8")

    assert "upstox_market_quote" in server
    assert '@api.get("/upstox/market-quote/{instrument}")' in server
