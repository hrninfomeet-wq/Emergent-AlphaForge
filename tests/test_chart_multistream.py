"""Synthetic only: shared chart SSE preserves identity, extrema and cleanup."""
import asyncio
import json
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from app import chart_stream
from app.upstox_stream import UpstoxMarketStreamManager


def test_channel_validation_rejects_bad_shapes_and_more_than_six_unique():
    parse = getattr(chart_stream, "parse_chart_channels", None)
    assert callable(parse), "multi-stream channel validator is missing"
    one = {"instrument_key": "NSE_INDEX|Nifty 50", "timeframe": "1m"}
    assert parse(json.dumps([one, one])) == [one]
    assert len(parse(json.dumps([dict(one, timeframe=tf) for tf in ("1m", "3m", "5m", "15m", "30m", "1h")]))) == 6
    for value in ("no JSON", "{}", "[]", '[null]', '[{"instrument_key": "x"}]',
                  '[{"instrument_key": "x", "timeframe": "0m"}]',
                  json.dumps([dict(one, instrument_key="x" * 201)]),
                  json.dumps([dict(one, instrument_key=str(i)) for i in range(7)])):
        with pytest.raises(ValueError):
            parse(value)


def test_multiple_generators_release_all_leases_when_closed_or_cancelled():
    async def run(cancel):
        manager = UpstoxMarketStreamManager()
        manager.configure_session(session_id="synthetic", instrument_keys=["NSE_INDEX|Nifty 50"])
        async def history(*a, **kw):
            return {"bars": [], "warnings": []}
        async def connected():
            return False
        multiplex = getattr(chart_stream, "chart_multi_event_stream", None)
        assert callable(multiplex), "shared chart SSE generator is missing"
        stream = multiplex(manager=manager, channels=[
            ({"instrument_key": "NSE_FO|123"}, "1m"),
            ({"instrument_key": "NSE_FO|456"}, "5m")],
            history_loader=history, is_disconnected=connected)
        first, second = await anext(stream), await anext(stream)
        assert {json.loads(row.split("data: ", 1)[1])["instrument_key"] for row in (first, second)} == {"NSE_FO|123", "NSE_FO|456"}
        assert len(manager._chart_leases) == 2
        if cancel:
            pending = asyncio.create_task(anext(stream))
            await asyncio.sleep(.01)
            pending.cancel()
            with pytest.raises(asyncio.CancelledError):
                await pending
        else:
            await stream.aclose()
        assert not manager._chart_leases
        assert not manager._tick_subscribers
        assert manager._session["instrument_keys"] == ["NSE_INDEX|Nifty 50"]
    asyncio.run(run(False))
    asyncio.run(run(True))


def test_slow_consumer_keeps_every_candle_and_channel_failure_is_isolated(monkeypatch):
    async def run():
        released = []
        async def source(*, instrument, timeframe, **kw):
            key = instrument["instrument_key"]
            try:
                if key == "broken":
                    raise RuntimeError("secret upstream URL")
                for high in (100, 130, 150, 170, 190):
                    yield chart_stream.sse_frame("snapshot", {"instrument_key": key, "timeframe": timeframe,
                                                               "bar": {"high": high}})
            finally:
                released.append(key)
        monkeypatch.setattr(chart_stream, "chart_event_stream", source)
        async def connected():
            return False
        multiplex = getattr(chart_stream, "chart_multi_event_stream", None)
        assert callable(multiplex), "shared chart SSE generator is missing"
        stream = multiplex(manager=None, channels=[({"instrument_key": "good"}, "1m"),
                                                   ({"instrument_key": "broken"}, "5m")],
                           history_loader=None, is_disconnected=connected, max_queue=1)
        events = []
        try:
            for _ in range(6):
                events.append(await asyncio.wait_for(anext(stream), timeout=1))
                await asyncio.sleep(.001)
        finally:
            await stream.aclose()
        candles = [json.loads(e.split("data: ", 1)[1])["bar"]["high"] for e in events if "event: snapshot" in e]
        assert candles == [100, 130, 150, 170, 190]
        errors = [e for e in events if "event: stream_error" in e]
        assert len(errors) == 1 and '"instrument_key": "broken"' in errors[0]
        assert "secret" not in errors[0]
        assert sorted(released) == ["broken", "good"]
    asyncio.run(run())


def test_all_metadata_is_checked_before_opening_and_invalid_instrument_is_channel_scoped(monkeypatch):
    from fastapi import FastAPI
    import httpx
    from app.routers import charts
    import types
    seen = []
    async def resolve(key):
        seen.append(key)
        if key == "invalid":
            raise ValueError("unsupported instrument")
        return {"instrument_key": key}
    class Service:
        pass
    service = Service()
    service.resolve = resolve
    monkeypatch.setattr(charts, "get_chart_data_service", lambda: service)
    service.history = None
    service.peek_history = None
    runtime = types.ModuleType("app.runtime")
    runtime.upstox_stream_manager = None
    monkeypatch.setitem(sys.modules, "app.runtime", runtime)
    async def source(*, instrument, timeframe, **kw):
        assert seen == ["valid", "invalid"]  # Preflight finishes before a channel acquires resources.
        yield chart_stream.sse_frame("snapshot", {"instrument_key": instrument["instrument_key"], "timeframe": timeframe})
    monkeypatch.setattr(chart_stream, "chart_event_stream", source)
    app = FastAPI()
    app.include_router(charts.api)
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://chart.test") as client:
            response = await client.get("/charts/streams", params={"channels": json.dumps([
                {"instrument_key": "valid", "timeframe": "1m"}, {"instrument_key": "invalid", "timeframe": "5m"}])})
            assert response.status_code == 200
            assert seen == ["valid", "invalid"]
            assert "event: stream_error" in response.text and '"instrument_key": "invalid"' in response.text
            assert "event: snapshot" in response.text and '"instrument_key": "valid"' in response.text
            response = await client.get("/charts/streams", params={"channels": "[]"})
            assert response.status_code == 400
    asyncio.run(run())


def test_tick_overflow_behind_slow_shared_transport_resyncs_before_more_candles():
    async def run():
        from app.chart_data import chart_bucket_time
        manager = UpstoxMarketStreamManager()
        manager.configure_session(session_id="synthetic", instrument_keys=[])
        key = "NSE_FO|123"
        calls = []
        async def history(*a, **kw):
            calls.append(True)
            return {"bars": [{"time": chart_bucket_time(time.time(), "1m"), "open": 100,
                              "high": 2000, "low": 50, "close": 100}], "warnings": []}
        async def connected():
            return False
        def tick(price):
            now = int(time.time() * 1000)
            return {"instrument_key": key, "last_price": price, "ts": now, "ingest_ts": now}
        stream = chart_stream.chart_multi_event_stream(
            manager=manager, channels=[({"instrument_key": key}, "1m")],
            history_loader=history, is_disconnected=connected, max_queue=1)
        await anext(stream)
        manager._broadcast([tick(110)])
        await asyncio.sleep(.15)  # First candle fills the shared transport queue.
        manager._broadcast([tick(120)])
        await asyncio.sleep(.15)  # Next candle blocks its pump, not the market feed.
        manager._broadcast([tick(130) for _ in range(1030)])
        assert next(iter(manager._tick_subscribers)).dropped_count > 0
        await anext(stream)
        await anext(stream)
        resync = await asyncio.wait_for(anext(stream), timeout=2)
        assert "event: resync" in resync and "tick_queue_overflow" in resync
        after = json.loads((await asyncio.wait_for(anext(stream), timeout=2)).split("data: ", 1)[1])
        assert len(calls) == 2
        assert (after["bar"]["high"], after["bar"]["low"]) == (2000, 50)
        await stream.aclose()
        assert not manager._chart_leases and not manager._tick_subscribers
    asyncio.run(run())


@pytest.mark.parametrize("cancel", [False, True])
def test_failed_channel_recovers_without_restarting_healthy_sibling(monkeypatch, cancel):
    monkeypatch.setattr(chart_stream, "_CHANNEL_RETRY_INITIAL_S", .01, raising=False)
    monkeypatch.setattr(chart_stream, "_CHANNEL_RETRY_MAX_S", .02, raising=False)

    async def run():
        manager = UpstoxMarketStreamManager()
        manager.configure_session(session_id="synthetic-recovery", instrument_keys=[])
        calls = {}

        async def history(key, **kwargs):
            calls[key] = calls.get(key, 0) + 1
            if key == "recovering" and calls[key] == 1:
                raise RuntimeError("temporary history failure")
            return {"bars": [], "warnings": []}

        async def connected():
            return False

        stream = chart_stream.chart_multi_event_stream(
            manager=manager, channels=[({"instrument_key": key}, "1m") for key in ("recovering", "healthy")],
            history_loader=history, is_disconnected=connected)
        healthy_lease = None
        snapshots = set()
        resyncs = []
        try:
            while len(snapshots) < 2:
                event = await asyncio.wait_for(anext(stream), timeout=1)
                payload = json.loads(event.split("data: ", 1)[1])
                if "event: resync" in event:
                    resyncs.append(payload)
                if "event: snapshot" in event:
                    snapshots.add(payload["instrument_key"])
                    if payload["instrument_key"] == "healthy":
                        healthy_lease = next(lease for lease, key in manager._chart_leases.items() if key == "healthy")
            assert calls == {"recovering": 2, "healthy": 1}
            assert resyncs == [{"instrument_key": "recovering", "timeframe": "1m", "reason": "channel_recovered"}]
            assert healthy_lease in manager._chart_leases
            assert len(manager._chart_leases) == len(manager._tick_subscribers) == 2
            now = int(time.time() * 1000)
            manager._broadcast([{"instrument_key": key, "last_price": price, "ts": now, "ingest_ts": now}
                                for key, price in (("recovering", 110), ("healthy", 220))])
            changed = {}
            while len(changed) < 2:
                event = await asyncio.wait_for(anext(stream), timeout=1)
                payload = json.loads(event.split("data: ", 1)[1])
                if "bar" in payload:
                    changed[payload["instrument_key"]] = payload["bar"]["close"]
            assert changed == {"recovering": 110, "healthy": 220}
            if cancel:
                pending = asyncio.create_task(anext(stream))
                await asyncio.sleep(.01)
                pending.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await pending
        finally:
            await stream.aclose()
        assert not manager._chart_leases and not manager._tick_subscribers

    asyncio.run(run())


def test_persistent_channel_failure_backs_off_with_a_cap_and_releases_each_lease(monkeypatch):
    monkeypatch.setattr(chart_stream, "_CHANNEL_RETRY_INITIAL_S", 1)
    monkeypatch.setattr(chart_stream, "_CHANNEL_RETRY_MAX_S", 4)

    async def run():
        manager = UpstoxMarketStreamManager()
        manager.configure_session(session_id="synthetic-backoff", instrument_keys=[])
        delays = []
        sleep = asyncio.sleep

        async def fast_sleep(delay):
            assert not manager._chart_leases and not manager._tick_subscribers
            delays.append(delay)
            await sleep(.001)

        monkeypatch.setattr(chart_stream.asyncio, "sleep", fast_sleep)

        async def history(*args, **kwargs):
            raise RuntimeError("still unavailable")

        async def connected():
            return False

        stream = chart_stream.chart_multi_event_stream(
            manager=manager, channels=[({"instrument_key": "retrying"}, "1m")],
            history_loader=history, is_disconnected=connected, max_queue=1)
        try:
            for _ in range(5):
                assert "event: stream_error" in await asyncio.wait_for(anext(stream), timeout=1)
        finally:
            await stream.aclose()
        assert delays[:4] == [1, 2, 4, 4]
        assert not manager._chart_leases and not manager._tick_subscribers

    asyncio.run(run())


@pytest.mark.parametrize("cancel", [False, True])
def test_close_or_cancel_during_channel_backoff_leaves_no_tasks(monkeypatch, cancel):
    monkeypatch.setattr(chart_stream, "_CHANNEL_RETRY_INITIAL_S", 30)

    async def run():
        manager = UpstoxMarketStreamManager()
        manager.configure_session(session_id="synthetic-backoff-cleanup", instrument_keys=[])
        before = asyncio.all_tasks()

        async def history(*args, **kwargs):
            raise RuntimeError("temporary failure")

        async def connected():
            return False

        stream = chart_stream.chart_multi_event_stream(
            manager=manager, channels=[({"instrument_key": "retrying"}, "1m")],
            history_loader=history, is_disconnected=connected)
        assert "event: stream_error" in await anext(stream)
        await asyncio.sleep(.01)
        assert not manager._chart_leases and not manager._tick_subscribers
        if cancel:
            pending = asyncio.create_task(anext(stream))
            await asyncio.sleep(.01)
            pending.cancel()
            with pytest.raises(asyncio.CancelledError):
                await pending
        await stream.aclose()
        assert not asyncio.all_tasks().difference(before)

    asyncio.run(run())
