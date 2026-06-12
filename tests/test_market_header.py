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
from tests.contract_corpus import backend_api_text


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
    server = backend_api_text()

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


def test_normalize_quote_exposes_day_high_low_from_ohlc():
    quote = normalize_quote(
        {
            "last_price": 23950,
            "net_change": -50,
            "ohlc": {"open": 24010, "high": 24080, "low": 23900, "close": 24000},
            "timestamp": "2026-05-26T15:19:00+05:30",
            "source": "upstox_market_quote",
        },
        label="NIFTY 50",
        key="nifty50",
        group="primary",
        source_label="Upstox",
    )
    assert quote["high"] == 24080
    assert quote["low"] == 23900
    assert quote["open"] == 24010
    assert quote["previous_close"] == 24000


def test_normalize_quote_reads_top_level_high_low_from_ws_tick():
    # WS full-mode tick carries day OHLC at top level (open/high/low).
    quote = normalize_quote(
        {
            "last_price": 51000,
            "close_price": 50800,
            "open": 50820,
            "high": 51200,
            "low": 50700,
            "received_ts": 1716712740000,
            "source": "upstox_ws_v3",
        },
        label="BANKNIFTY",
        key="banknifty",
        group="primary",
        source_label="Upstox WS",
    )
    assert quote["high"] == 51200
    assert quote["low"] == 50700


def test_market_header_backfills_day_range_from_cache_for_ltpc_tick():
    """A full-mode quote seeds the day range; a later ltpc tick (no high/low)
    should inherit it from the cache so the range bar stays stable."""
    from app.market_header import _DAY_RANGE_CACHE, _apply_cached_day_range

    _DAY_RANGE_CACHE.pop("nifty50", None)
    seeded = _apply_cached_day_range("nifty50", {
        "last_price": 24000, "open": 23980, "high": 24100, "low": 23900,
    })
    assert seeded["high"] == 24100
    # ltpc-only tick: no high/low present.
    ltpc_quote = _apply_cached_day_range("nifty50", {"last_price": 24050})
    assert ltpc_quote["high"] == 24100
    assert ltpc_quote["low"] == 23900
    _DAY_RANGE_CACHE.pop("nifty50", None)
