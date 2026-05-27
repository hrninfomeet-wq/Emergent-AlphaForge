import asyncio
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.market_header import (  # noqa: E402
    build_market_header_snapshot,
    normalize_quote,
    pct_change,
)


def test_pct_change_uses_previous_close():
    assert pct_change(105, 100) == 5.0
    assert pct_change(95, 100) == -5.0
    assert pct_change(95, 0) is None


def test_normalize_quote_derives_change_from_ohlc_close_when_needed():
    quote = normalize_quote(
        {
            "last_price": 23900,
            "net_change": None,
            "ohlc": {"close": 24000},
            "timestamp": "2026-05-26T15:19:00+05:30",
            "source": "upstox_market_quote",
        },
        label="NIFTY 50",
        key="nifty50",
        group="primary",
        source_label="Upstox",
    )

    assert quote["label"] == "NIFTY 50"
    assert quote["last_price"] == 23900
    assert quote["change"] == -100
    assert quote["change_pct"] == -0.42
    assert quote["source"] == "Upstox"
    assert quote["status"] == "ok"


def test_normalize_quote_derives_pct_from_upstox_net_change():
    quote = normalize_quote(
        {
            "last_price": 23913.7,
            "net_change": -118,
            "ohlc": {"close": 23913.7},
            "timestamp": "2026-05-26T15:48:26+05:30",
            "source": "upstox_market_quote",
        },
        label="NIFTY 50",
        key="nifty50",
        group="primary",
        source_label="Upstox",
    )

    assert quote["change"] == -118
    assert quote["change_pct"] == -0.49


def test_build_market_header_snapshot_keeps_failed_items_visible():
    async def fetch_upstox(item):
        if item["key"] == "nifty50":
            return {
                "last_price": 23900,
                "net_change": -100,
                "ohlc": {"close": 24000},
                "timestamp": "2026-05-26T15:19:00+05:30",
                "source": "upstox_market_quote",
            }
        raise RuntimeError("not subscribed")

    async def fetch_fallback(item):
        return {
            "last_price": 76500,
            "change": 250,
            "change_pct": 0.33,
            "timestamp": "2026-05-26T15:19:00+05:30",
            "source": "fallback",
        }

    snapshot = asyncio.run(build_market_header_snapshot(
        items=[
            {"key": "nifty50", "label": "NIFTY 50", "group": "primary", "source": "upstox", "instrument_key": "NSE_INDEX|Nifty 50"},
            {"key": "btcusd", "label": "BTCUSD", "group": "primary", "source": "fallback", "fallback_symbol": "BTC-USD"},
            {"key": "gold", "label": "Gold Fut", "group": "primary", "source": "upstox", "instrument_key": "MCX_FO|GOLD"},
        ],
        fetch_upstox=fetch_upstox,
        fetch_fallback=fetch_fallback,
    ))

    by_key = {item["key"]: item for item in snapshot["items"]}
    assert snapshot["source_mode"] == "api_fallback"
    assert by_key["nifty50"]["status"] == "ok"
    assert by_key["btcusd"]["status"] == "ok"
    assert by_key["gold"]["status"] == "error"
    assert by_key["gold"]["last_price"] is None


def test_backend_exposes_market_header_route():
    server = (ROOT / "backend" / "server.py").read_text(encoding="utf-8")

    assert '@api.get("/market/header")' in server
    assert "market_header_snapshot" in server


def test_frontend_exposes_market_header_component():
    api = (ROOT / "frontend" / "src" / "lib" / "api.js").read_text(encoding="utf-8")
    layout = (ROOT / "frontend" / "src" / "components" / "Layout.jsx").read_text(encoding="utf-8")
    component = ROOT / "frontend" / "src" / "components" / "MarketHeader.jsx"

    assert "marketHeader" in api
    assert component.exists()
    text = component.read_text(encoding="utf-8")
    for needle in (
        "market-header",
        "market-header-primary",
        "market-header-global-toggle",
        "market-header-global",
        "NIFTY 50",
        "Global Markets",
    ):
        assert needle in text + layout
