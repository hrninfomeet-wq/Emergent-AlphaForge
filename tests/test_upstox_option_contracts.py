import sys
import types
import asyncio
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


def test_normalize_current_option_contract():
    raw = {
        "name": "NIFTY",
        "exchange": "NSE",
        "segment": "NSE_FO",
        "expiry": "2026-05-28",
        "instrument_key": "NSE_FO|12345",
        "exchange_token": "12345",
        "trading_symbol": "NIFTY 28 MAY 26000 CE",
        "instrument_type": "CE",
        "strike_price": 26000,
        "lot_size": 65,
        "weekly": True,
    }

    contract = upstox_client.normalize_option_contract(raw, "NIFTY", source="current_option_contract")

    assert contract == {
        "underlying": "NIFTY",
        "underlying_key": "NSE_INDEX|Nifty 50",
        "instrument_key": "NSE_FO|12345",
        "exchange_token": "12345",
        "trading_symbol": "NIFTY 28 MAY 26000 CE",
        "expiry_date": "2026-05-28",
        "side": "CE",
        "strike": 26000.0,
        "lot_size": 65,
        "exchange": "NSE",
        "segment": "NSE_FO",
        "weekly": True,
        "source": "current_option_contract",
    }


def test_normalize_expired_option_contract_accepts_expired_key_alias():
    raw = {
        "underlying_key": "BSE_INDEX|SENSEX",
        "expired_instrument_key": "BSE_FO|999",
        "trading_symbol": "SENSEX 22 MAY 82000 PE",
        "expiry": "2026-05-22",
        "option_type": "PE",
        "strike_price": "82000",
        "minimum_lot": "20",
    }

    contract = upstox_client.normalize_option_contract(raw, "SENSEX", source="expired_option_contract")

    assert contract["underlying"] == "SENSEX"
    assert contract["underlying_key"] == "BSE_INDEX|SENSEX"
    assert contract["instrument_key"] == "BSE_FO|999"
    assert contract["side"] == "PE"
    assert contract["strike"] == 82000.0
    assert contract["lot_size"] == 20
    assert contract["source"] == "expired_option_contract"


def test_normalize_option_contracts_filters_unusable_rows():
    rows = [
        {"instrument_key": "NSE_FO|1", "instrument_type": "CE", "strike_price": 25000},
        {"instrument_type": "PE", "strike_price": 24900},
        {"instrument_key": "NSE_FO|2", "instrument_type": "XX", "strike_price": 25100},
    ]

    contracts = upstox_client.normalize_option_contracts(rows, "NIFTY", source="current_option_contract")

    assert len(contracts) == 1
    assert contracts[0]["instrument_key"] == "NSE_FO|1"


def test_fetch_expired_historical_1m_for_key_uses_expired_endpoint(monkeypatch):
    captured = {}

    async def fake_get(url, user_id="default"):
        captured["url"] = url
        captured["user_id"] = user_id
        return {
            "data": {
                "candles": [
                    ["2024-11-28T09:15:00+05:30", 10, 11, 9, 10.5, 1000, 5000],
                ]
            }
        }

    monkeypatch.setattr(upstox_client, "_authenticated_get", fake_get)

    df = asyncio.run(
        upstox_client.fetch_expired_historical_1m_for_key(
            "NSE_FO|42939|28-11-2024",
            "2024-11-28",
            "2024-11-28",
            contract={
                "underlying": "NIFTY",
                "expiry_date": "2024-11-28",
                "strike": 23900,
                "side": "CE",
                "trading_symbol": "NIFTY 23900 CE 28 NOV 24",
            },
        )
    )

    assert "/v2/expired-instruments/historical-candle/NSE_FO%7C42939%7C28-11-2024/1minute/2024-11-28/2024-11-28" in captured["url"]
    # ``instrument_key`` remains the live routing token while the immutable,
    # expiry-qualified identity is carried separately.  Keeping a dated URL
    # key in the routing field would recreate the token-identity split that the
    # provenance migration is designed to remove.
    assert df.iloc[0]["instrument_key"] == "NSE_FO|42939"
    assert df.iloc[0]["contract_key"] == "NSE_FO|42939|2024-11-28"
    assert df.iloc[0]["underlying"] == "NIFTY"


def test_fetch_historical_1m_for_key_routes_expired_contracts_to_expired_endpoint(monkeypatch):
    called = {}

    async def fake_expired(instrument_key, from_date, to_date, user_id="default", contract=None):
        called["instrument_key"] = instrument_key
        called["from_date"] = from_date
        called["to_date"] = to_date
        called["contract"] = contract
        return "expired-df"

    monkeypatch.setattr(upstox_client, "fetch_expired_historical_1m_for_key", fake_expired)

    result = asyncio.run(
        upstox_client.fetch_historical_1m_for_key(
            "NSE_FO|42939|28-11-2024",
            "2024-11-28",
            "2024-11-28",
            contract={"source": "expired_option_contract"},
        )
    )

    assert result == "expired-df"
    assert called["instrument_key"] == "NSE_FO|42939|28-11-2024"
    assert called["contract"]["source"] == "expired_option_contract"
