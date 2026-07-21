import asyncio
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.upstox_stream import (  # noqa: E402
    DEFAULT_STREAM_MODE,
    UpstoxMarketStreamManager,
    build_subscription_message,
    decode_market_data_feed,
    normalize_feed_response,
    persist_ticks,
)
from tests.contract_corpus import backend_api_text


def _varint(value: int) -> bytes:
    out = bytearray()
    value = int(value)
    while value > 0x7F:
        out.append((value & 0x7F) | 0x80)
        value >>= 7
    out.append(value)
    return bytes(out)


def _key(field: int, wire_type: int) -> bytes:
    return _varint((field << 3) | wire_type)


def _double(field: int, value: float) -> bytes:
    import struct

    return _key(field, 1) + struct.pack("<d", float(value))


def _int(field: int, value: int) -> bytes:
    return _key(field, 0) + _varint(value)


def _string(field: int, value: str) -> bytes:
    raw = value.encode("utf-8")
    return _key(field, 2) + _varint(len(raw)) + raw


def _message(field: int, payload: bytes) -> bytes:
    return _key(field, 2) + _varint(len(payload)) + payload


def _ltpc(ltp: float, ltt: int, ltq: int, cp: float) -> bytes:
    return _double(1, ltp) + _int(2, ltt) + _int(3, ltq) + _double(4, cp)


def _quote(bid_q: int, bid_p: float, ask_q: int, ask_p: float) -> bytes:
    return _int(1, bid_q) + _double(2, bid_p) + _int(3, ask_q) + _double(4, ask_p)


def _feed_map_entry(instrument_key: str, feed_payload: bytes) -> bytes:
    return _string(1, instrument_key) + _message(2, feed_payload)


def test_build_subscription_message_is_binary_json_for_upstox_v3():
    payload = build_subscription_message(
        guid="abc-123",
        instrument_keys=["NSE_INDEX|Nifty 50", "BSE_INDEX|SENSEX"],
        mode="ltpc",
    )

    assert isinstance(payload, bytes)
    decoded = json.loads(payload.decode("utf-8"))
    assert decoded == {
        "guid": "abc-123",
        "method": "sub",
        "data": {
            "mode": "ltpc",
            "instrumentKeys": ["NSE_INDEX|Nifty 50", "BSE_INDEX|SENSEX"],
        },
    }


def test_decode_market_data_feed_extracts_ltpc_ticks_from_v3_protobuf_bytes():
    feed = _message(1, _ltpc(24985.35, 1779774122000, 50, 24942.85)) + _int(4, 0)
    response = _int(1, 1) + _message(2, _feed_map_entry("NSE_INDEX|Nifty 50", feed)) + _int(3, 1779774123000)

    decoded = decode_market_data_feed(response)
    normalized = normalize_feed_response(decoded)

    assert decoded["type"] == "live_feed"
    assert normalized == [
        {
            "instrument_key": "NSE_INDEX|Nifty 50",
            "ts": 1779774122000,
            "received_ts": 1779774123000,
            "last_price": 24985.35,
            "last_trade_quantity": 50,
            "close_price": 24942.85,
            "source": "upstox_ws_v3",
            "mode": "ltpc",
        }
    ]


def test_decode_market_data_feed_extracts_index_full_feed_ltpc():
    index_full_feed = _message(2, _message(1, _ltpc(55123.5, 1779774124000, 0, 55200.0)))
    feed = _message(2, index_full_feed) + _int(4, 1)
    response = _int(1, 1) + _message(2, _feed_map_entry("NSE_INDEX|Nifty Bank", feed)) + _int(3, 1779774125000)

    normalized = normalize_feed_response(decode_market_data_feed(response))

    assert normalized[0]["instrument_key"] == "NSE_INDEX|Nifty Bank"
    assert normalized[0]["last_price"] == 55123.5
    assert normalized[0]["close_price"] == 55200.0
    assert normalized[0]["mode"] == "full"


def test_full_feed_retains_depth_oi_iv_and_greeks_for_forward_evidence():
    market_level = _message(1, _quote(130, 100.0, 195, 100.5))
    greeks = (_double(1, 0.51) + _double(2, -4.2) + _double(3, 0.001)
              + _double(4, 12.3) + _double(5, 2.1))
    market = (
        _message(1, _ltpc(100.25, 1779774124000, 65, 99.0))
        + _message(2, market_level)
        + _message(3, greeks)
        + _double(5, 100.1)
        + _int(6, 5000)
        + _double(7, 250000)
        + _double(8, 0.18)
        + _double(9, 10000)
        + _double(10, 9000)
    )
    feed = _message(2, _message(1, market)) + _int(4, 1)
    response = _int(1, 1) + _message(
        2, _feed_map_entry("NSE_FO|12345", feed)) + _int(3, 1779774125000)

    tick = normalize_feed_response(decode_market_data_feed(response))[0]

    assert tick["mode"] == "full"
    assert tick["best_bid_price"] == 100.0
    assert tick["best_ask_price"] == 100.5
    assert tick["best_ask_quantity"] == 195
    assert tick["open_interest"] == 250000.0
    assert tick["implied_volatility"] == 0.18
    assert tick["option_greeks"]["delta"] == 0.51
    assert tick["market_depth"][0]["bid_quantity"] == 130


class FakeTicksCollection:
    def __init__(self):
        self.operations = []

    async def bulk_write(self, operations, ordered=False):
        self.operations.extend(operations)

        class Result:
            upserted_count = 2
            modified_count = 0
            matched_count = 0

        return Result()


class FakeDb:
    def __init__(self):
        self.ticks = FakeTicksCollection()


def test_persist_ticks_upserts_without_raw_broker_payload():
    ticks = [
        {
            "instrument_key": "NSE_INDEX|Nifty 50",
            "ts": 1779774122000,
            "received_ts": 1779774123000,
            "last_price": 24985.35,
            "last_trade_quantity": 50,
            "close_price": 24942.85,
            "source": "upstox_ws_v3",
            "mode": "ltpc",
        }
    ]

    result = asyncio.run(persist_ticks(FakeDb(), ticks, session_id="session-1"))

    assert result["upserted"] == 2
    op = result["operations"][0]
    assert op._filter == {"instrument_key": "NSE_INDEX|Nifty 50", "ts": 1779774122000, "session_id": "session-1"}
    assert "raw" not in op._doc["$set"]
    assert op._doc["$set"]["last_price"] == 24985.35


def test_stream_manager_status_is_sanitized_and_tracks_subscription():
    manager = UpstoxMarketStreamManager()

    status = manager.status()
    assert status["running"] is False
    assert "access_token" not in json.dumps(status).lower()

    manager.configure_session(
        session_id="session-1",
        instrument_keys=["NSE_INDEX|Nifty 50"],
        mode=DEFAULT_STREAM_MODE,
        persist=True,
    )

    status = manager.status()
    assert status["session_id"] == "session-1"
    assert status["mode"] == "full"
    assert status["instrument_count"] == 1
    assert status["persist"] is True


def test_backend_and_frontend_expose_upstox_stream_controls():
    server = backend_api_text()
    api = (ROOT / "frontend" / "src" / "lib" / "api.js").read_text(encoding="utf-8")
    header = (ROOT / "frontend" / "src" / "components" / "MarketHeader.jsx").read_text(encoding="utf-8")

    assert '@api.post("/upstox/stream/start")' in server
    assert '@api.post("/upstox/stream/stop")' in server
    assert '@api.get("/upstox/stream/status")' in server
    assert '@api.get("/upstox/stream/ticks/latest")' in server
    assert "startUpstoxStream" in api
    assert "stopUpstoxStream" in api
    assert "upstoxStreamStatus" in api
    assert "live ticks" in header
