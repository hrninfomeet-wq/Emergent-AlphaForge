import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.chart_positions import build_chart_positions
from app.chart_stream import ChartCandleAccumulator, chart_event_stream, stream_state
from app.upstox_stream import UpstoxMarketStreamManager

NOW = 1_800_000_030_000
INSTRUMENT = {"instrument_key": "NSE_FO|123", "symbol": "NIFTY NOV CONTRACT", "kind": "option",
              "segment": "NSE_FO", "exchange_token": "123", "expiry": "2026-11-03"}


def tick(price=110, stamp=NOW):
    return {"instrument_key": INSTRUMENT["instrument_key"], "last_price": price,
            "ts": stamp, "ingest_ts": NOW}


def test_stream_exposes_previous_close_from_same_quote_without_extra_fields():
    class Manager:
        def status(self):
            return {"connected": True}

        def latest_tick_map(self):
            return {INSTRUMENT["instrument_key"]: {
                **tick(), "close_price": 100, "received_ts": NOW, "private_field": "not-public"}}

    for timeframe in ("1m", "1d", "1w"):
        payload = stream_state(Manager(), INSTRUMENT["instrument_key"], timeframe, NOW)
        assert payload["tick"] == {"last_price": 110, "close_price": 100,
                                   "ts": NOW, "ingest_ts": NOW, "received_ts": NOW}
    assert stream_state(Manager(), "unknown", "1m", NOW)["tick"]["close_price"] is None


def book(stale=False):
    return {"rows": [{"exch": "NFO", "token": "123", "tsym": "NIFTY03NOV26C25000", "prd": "I",
                      "netqty": "50", "netavgprc": "100", "lp": "105", "urmtom": "250",
                      "prcftr": "1", "mult": "1"}], "age_ms": 1500, "stale": stale}


def test_broker_is_quantity_authority_and_paper_is_separate():
    paper = {"id": "p", "status": "OPEN", "instrument_key": INSTRUMENT["instrument_key"],
             "expiry": "2026-11-03", "quantity": 25, "entry_price": 90, "risk": {"stop_price": 80, "target_price": 130}}
    guard = {"id": "g", "seen_filled": True, "exch": "NFO", "tsym": "NIFTY03NOV26C25000", "prd": "I",
             "qty": 10, "state": {"stop_level": 95, "target_level": 140}}
    out = build_chart_positions(INSTRUMENT, broker_book=book(), paper_rows=[paper], guards=[guard],
                                ticks={INSTRUMENT["instrument_key"]: tick()}, now_ms=NOW)
    live, simulated = out["positions"]
    assert (live["origin"], live["quantity"], live["entry_price"], live["pnl"]) == ("broker", 50, 100, 500)
    assert live["stop_price"] == 95
    assert live["protection_levels"][0]["quantity"] == 10  # Guard does not replace broker quantity.
    assert (simulated["origin"], simulated["quantity"], simulated["pnl"]) == ("paper", 25, 500)
    assert simulated["stop_price"] == 80


def test_stale_book_is_never_remarked_even_with_new_ticks():
    out = build_chart_positions(INSTRUMENT, broker_book=book(stale=True),
                                ticks={INSTRUMENT["instrument_key"]: tick(1000)}, now_ms=NOW)
    pos = out["positions"][0]
    assert (pos["mark_price"], pos["pnl"], pos["fresh"]) == (105, 250, False)
    assert any("stale" in warning for warning in out["warnings"])


def test_received_now_is_not_a_fresh_trade_and_unknown_mtm_stays_unknown():
    old = tick(1000, NOW - 60000)
    out = build_chart_positions(INSTRUMENT, broker_book=book(), ticks={INSTRUMENT["instrument_key"]: old}, now_ms=NOW)
    assert out["positions"][0]["pnl"] == 250
    assert out["positions"][0]["fresh"] is False
    incomplete = book()
    incomplete["rows"][0].pop("urmtom")
    out = build_chart_positions(INSTRUMENT, broker_book=incomplete, ticks={INSTRUMENT["instrument_key"]: tick()}, now_ms=NOW)
    assert out["positions"][0]["pnl"] is None


def test_recycled_token_and_premium_lines_do_not_leak_to_other_contracts():
    paper = {"id": "old", "status": "OPEN", "instrument_key": INSTRUMENT["instrument_key"],
             "expiry": "2025-11-03", "quantity": 50, "entry_price": 100}
    old_book = book()
    old_book["rows"][0]["tsym"] = "NIFTY03NOV25C25000"
    out = build_chart_positions(INSTRUMENT, broker_book=old_book, paper_rows=[paper], now_ms=NOW)
    assert out["positions"] == []
    assert out["warnings"]
    index = {"instrument_key": "NSE_INDEX|Nifty 50", "kind": "index", "symbol": "NIFTY", "segment": "NSE_INDEX"}
    out = build_chart_positions(index, broker_book=book(), paper_rows=[paper], now_ms=NOW)
    assert out["positions"] == []


def test_missing_broker_is_not_confirmed_flat_and_short_sign_is_preserved():
    out = build_chart_positions(INSTRUMENT, now_ms=NOW)
    assert "does not confirm a flat account" in out["warnings"][0]
    short = book()
    short["rows"][0].update(netqty="-50", urmtom="-250")
    out = build_chart_positions(INSTRUMENT, broker_book=short, ticks={INSTRUMENT["instrument_key"]: tick()}, now_ms=NOW)
    assert out["positions"][0]["pnl"] == -500


def test_recycled_futures_token_cannot_mark_an_expired_broker_position():
    future = {**INSTRUMENT, "kind": "future", "expiry": "2026-11-24"}
    old = book()
    old["rows"][0]["tsym"] = "NIFTY27OCT26F"
    out = build_chart_positions(future, broker_book=old, ticks={future["instrument_key"]: tick()}, now_ms=NOW)
    assert out["positions"] == []
    assert any("contract identity" in warning for warning in out["warnings"])
    old["rows"][0]["tsym"] = "NIFTY24NOV26F"
    assert len(build_chart_positions(future, broker_book=old, now_ms=NOW)["positions"]) == 1


def test_contradictory_strike_or_option_side_blocks_broker_token_join():
    selected = {**INSTRUMENT, "strike": 26000, "option_type": "CE"}
    assert build_chart_positions(selected, broker_book=book(), now_ms=NOW)["positions"] == []
    selected.update(strike=25000, option_type="PE")
    assert build_chart_positions(selected, broker_book=book(), now_ms=NOW)["positions"] == []


def test_coalescing_retains_extrema_and_does_not_invent_volume():
    bucket = lambda ts, tf: ts // 60 * 60
    candle = ChartCandleAccumulator(INSTRUMENT["instrument_key"], "1m", bucket=bucket)
    for offset, price in enumerate([100, 130, 80, 110]):
        bar = candle.update(tick(price, NOW - 3000 + offset * 1000), NOW)
    assert (bar["open"], bar["high"], bar["low"], bar["close"]) == (100, 130, 80, 110)
    assert bar["volume"] is None and bar["provisional"]
    assert candle.update(tick(999, NOW - 4000), NOW) is None
    assert candle.update(tick(999, NOW - 20000), NOW) is None
    assert candle.bars[bar["time"]]["close"] == 110


def test_history_seed_is_preserved_and_previous_bucket_cannot_overwrite_it():
    start = NOW // 60000 * 60
    seed = {"time": start, "open": 90, "high": 150, "low": 70, "close": 100, "volume": 1000}
    candle = ChartCandleAccumulator(INSTRUMENT["instrument_key"], "1m", [seed], bucket=lambda ts, tf: ts // 60 * 60)
    bar = candle.update(tick(115), NOW)
    assert (bar["open"], bar["high"], bar["low"], bar["close"]) == (90, 150, 70, 115)
    assert seed["close"] == 100  # Never mutate cached history.


def test_history_watermark_rejects_ticks_queued_before_rest_snapshot():
    start = NOW // 60000 * 60
    seed = {"time": start, "open": 90, "high": 150, "low": 70, "close": 120, "volume": 1000}
    candle = ChartCandleAccumulator(INSTRUMENT["instrument_key"], "1m", [seed],
                                    bucket=lambda ts, tf: ts // 60 * 60, as_of_ms=NOW)
    assert candle.update(tick(105, NOW - 4000), NOW) is None
    assert candle.bars[start]["close"] == 120
    newer = {**tick(125, NOW + 1000), "ingest_ts": NOW + 1000}
    updated = candle.update(newer, NOW + 1000)
    assert (updated["high"], updated["low"], updated["close"]) == (150, 70, 125)


def test_conditional_intraday_levels_do_not_protect_carry_forward_product():
    order = {"id": "conditional", "mode": "live", "status": "OPEN", "contract": {
        "instrument_key": INSTRUMENT["instrument_key"], "expiry": INSTRUMENT["expiry"]},
        "filled_quantity": 50, "closed_quantity": 0, "exit_state": {"stop": 95, "target": 130}}
    holding = book()
    holding["rows"][0]["prd"] = "M"
    out = build_chart_positions(INSTRUMENT, broker_book=holding, conditional_rows=[order], now_ms=NOW)
    assert out["positions"][0]["protection_levels"] == []
    holding["rows"][0]["prd"] = "I"
    out = build_chart_positions(INSTRUMENT, broker_book=holding, conditional_rows=[order], now_ms=NOW)
    assert out["positions"][0]["stop_price"] == 95


def test_stream_generator_cleanup_releases_lease_on_disconnect():
    async def run():
        manager = UpstoxMarketStreamManager()
        manager.configure_session(session_id="test", instrument_keys=["NSE_INDEX|Nifty 50"])
        async def history(*args, **kwargs):
            return {"bars": [], "warnings": []}
        async def disconnected():
            return False
        stream = chart_event_stream(manager=manager, instrument=INSTRUMENT, timeframe="1m",
                                    history_loader=history, is_disconnected=disconnected, now_ms=lambda: NOW)
        first = await anext(stream)
        assert "event: snapshot" in first
        assert len(manager._chart_leases) == 1
        await stream.aclose()
        assert not manager._chart_leases
        assert not manager._tick_subscribers
        assert manager._session["instrument_keys"] == ["NSE_INDEX|Nifty 50"]
    asyncio.run(run())


def test_stream_adopts_rest_corrections_before_next_tick_and_skips_older_queued_trade():
    async def run():
        from app.chart_data import chart_bucket_time
        manager = UpstoxMarketStreamManager()
        manager.configure_session(session_id="test", instrument_keys=["NSE_INDEX|Nifty 50"])
        at = chart_bucket_time(NOW / 1000, "15m")
        initial = {"bars": [{"time": at, "open": 100, "high": 120, "low": 90, "close": 120, "volume": 100}],
                   "as_of_ms": NOW - 3000}
        cached = dict(initial)
        async def history(*args, **kwargs):
            # A trade queued during REST retrieval predates its snapshot.
            manager._broadcast([tick(105, NOW - 4000)])
            return initial
        async def disconnected():
            return False
        def payload(event):
            return json.loads(event.split("data: ", 1)[1])
        stream = chart_event_stream(manager=manager, instrument=INSTRUMENT, timeframe="15m",
                                    history_loader=history, history_peek=lambda *a, **kw: cached,
                                    is_disconnected=disconnected, now_ms=lambda: NOW, coalesce_s=0)
        await anext(stream)
        assert "bar" not in payload(await anext(stream))
        manager._broadcast([tick(112, NOW - 2000)])
        assert payload(await anext(stream))["bar"]["high"] == 120
        cached = {"bars": [{"time": at, "open": 100, "high": 150, "low": 80, "close": 115, "volume": 150}],
                  "as_of_ms": NOW - 1000}
        manager._broadcast([tick(116)])
        next_payload = payload(await anext(stream))
        assert (next_payload["bar"]["high"], next_payload["bar"]["low"], next_payload["bar"]["close"]) == (150, 80, 116)
        assert next_payload["history_as_of_ms"] == NOW - 1000
        await stream.aclose()
    asyncio.run(run())


def test_chart_routes_only_mutate_watchlist_preferences_and_validate_timeframe(monkeypatch):
    from fastapi import FastAPI
    import httpx
    from app.routers import charts
    app = FastAPI()
    app.include_router(charts.api)
    writes = [(route.path, route.methods) for route in charts.api.routes if route.methods != {"GET"}]
    assert writes == [("/charts/watchlist", {"PUT"})]
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://chart.test") as client:
            result = await client.get("/charts/history", params={"instrument_key": INSTRUMENT["instrument_key"], "timeframe": "0m"})
            assert result.status_code == 422
            assert (await client.post("/charts/positions")).status_code == 405
    asyncio.run(run())
