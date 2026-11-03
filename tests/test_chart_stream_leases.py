"""Chart viewers may share prices, but never own execution subscriptions."""
import asyncio
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from app.upstox_stream import UpstoxMarketStreamManager


class Socket:
    def __init__(self):
        self.messages = []
        self.fail = False

    async def send(self, data):
        if self.fail:
            raise OSError("transport unavailable")
        self.messages.append(json.loads(data))


def manager():
    obj = UpstoxMarketStreamManager()
    obj.configure_session(session_id="test", instrument_keys=["NSE_INDEX|Nifty 50"])
    obj._active_websocket = Socket()
    return obj


def test_two_viewers_release_only_after_last_owner_and_keep_baseline():
    async def run():
        obj = manager()
        first = await obj.acquire_chart_instrument("NSE_EQ|equity")
        second = await obj.acquire_chart_instrument("NSE_EQ|equity")
        index = await obj.acquire_chart_instrument("NSE_INDEX|Nifty 50")
        assert len(obj._active_websocket.messages) == 1
        await obj.release_chart_instrument(first)
        await obj.release_chart_instrument(index)
        assert len(obj._active_websocket.messages) == 1
        await obj.release_chart_instrument(second)
        assert obj._active_websocket.messages[-1]["method"] == "unsub"
        assert obj._session["instrument_keys"] == ["NSE_INDEX|Nifty 50"]
        assert obj.status()["chart_viewers"] == 0
    asyncio.run(run())


def test_execution_promotion_wins_over_chart_cleanup_and_persistence_is_opt_in():
    async def run():
        obj = manager()
        lease = await obj.acquire_chart_instrument("NSE_FO|option")
        ticks = [{"instrument_key": "NSE_FO|option"}, {"instrument_key": "NSE_INDEX|Nifty 50"}]
        assert obj._persistent_ticks(ticks) == ticks[1:]
        await obj.ensure_instruments(["NSE_FO|option"])
        await obj.release_chart_instrument(lease)
        assert obj._persistent_ticks(ticks) == ticks
        assert "NSE_FO|option" in obj._session["instrument_keys"]
        assert [m["method"] for m in obj._active_websocket.messages] == ["sub"]
    asyncio.run(run())


def test_reconfiguration_retains_viewers_and_required_instruments():
    async def run():
        obj = manager()
        lease = await obj.acquire_chart_instrument("NSE_EQ|equity")
        await obj.ensure_instruments(["NSE_FO|position"])
        obj.configure_session(session_id="new", instrument_keys=["BSE_INDEX|SENSEX"])
        assert obj._owned_keys() == {"BSE_INDEX|SENSEX", "NSE_EQ|equity", "NSE_FO|position"}
        await obj.release_chart_instrument(lease)
        assert set(obj._session["instrument_keys"]) == {"BSE_INDEX|SENSEX", "NSE_FO|position"}
    asyncio.run(run())


def test_failed_send_keeps_uncertain_subscription_budgeted_then_retries():
    async def run():
        obj = manager()
        obj._active_websocket.fail = True
        with pytest.raises(OSError):
            await obj.acquire_chart_instrument("NSE_EQ|equity")
        assert not obj._chart_leases
        assert "NSE_EQ|equity" in obj._session["instrument_keys"]
        assert "NSE_EQ|equity" not in obj._owned_keys()
        obj._active_websocket.fail = False
        lease = await obj.acquire_chart_instrument("NSE_EQ|other")
        assert [m["method"] for m in obj._active_websocket.messages] == ["unsub", "sub"]
        obj._active_websocket.fail = True
        await obj.release_chart_instrument(lease)
        assert "NSE_EQ|other" in obj._chart_pending_removals
        assert "NSE_EQ|other" not in obj._owned_keys()
    asyncio.run(run())


def test_chart_cap_does_not_evict_an_execution_instrument():
    async def run():
        obj = manager()
        obj.configure_session(session_id="full", instrument_keys=[f"NSE_FO|{i}" for i in range(1500)])
        before = list(obj._session["instrument_keys"])
        with pytest.raises(ValueError, match="budget"):
            await obj.acquire_chart_instrument("NSE_EQ|new")
        assert obj._session["instrument_keys"] == before
        leases = [await obj.acquire_chart_instrument("NSE_FO|1") for _ in range(16)]
        with pytest.raises(ValueError, match="16"):
            await obj.acquire_chart_instrument("NSE_FO|1")
        for lease in leases:
            await obj.release_chart_instrument(lease)
        assert set(obj._session["instrument_keys"]) == set(before)
    asyncio.run(run())


def test_failed_chart_subscription_promoted_to_execution_retries_delivery():
    async def run():
        obj = manager()
        obj._active_websocket.fail = True
        with pytest.raises(OSError):
            await obj.acquire_chart_instrument("NSE_FO|held")
        with pytest.raises(OSError):
            await obj.ensure_instruments(["NSE_FO|held"])
        assert "NSE_FO|held" not in obj.status()["execution_instrument_keys"]
        obj._active_websocket.fail = False
        await obj.ensure_instruments(["NSE_FO|held"])
        assert obj._active_websocket.messages[-1]["data"]["instrumentKeys"] == ["NSE_FO|held"]
        assert "NSE_FO|held" in obj.status()["execution_instrument_keys"]
        assert "NSE_FO|held" not in obj._chart_pending_removals
    asyncio.run(run())


def test_unsubscribe_delivered_before_error_is_restored_for_execution_owner():
    async def run():
        obj = manager()
        socket = obj._active_websocket
        lease = await obj.acquire_chart_instrument("NSE_FO|held")
        original_send = socket.send
        async def delivered_then_failed(data):
            await original_send(data)
            raise OSError("uncertain acknowledgement")
        socket.send = delivered_then_failed
        await obj.release_chart_instrument(lease)
        socket.send = original_send
        await obj.ensure_instruments(["NSE_FO|held"])
        assert [m["method"] for m in socket.messages] == ["sub", "unsub", "sub"]
        assert "NSE_FO|held" in obj.status()["execution_instrument_keys"]
    asyncio.run(run())


def test_filtered_queue_does_not_drop_for_other_symbols_and_reports_real_overflow():
    obj = manager()
    chart = obj.subscribe(max_queue=2, instrument_keys=["A"])
    generic = obj.subscribe(max_queue=10)
    obj._broadcast([{"instrument_key": "B", "last_price": 20}] * 6)
    assert chart.qsize() == 0
    assert chart.dropped_count == 0
    obj._broadcast([{"instrument_key": "A", "last_price": p} for p in (1, 2, 3)])
    assert chart.dropped_count == 1
    assert [chart.get_nowait()["last_price"] for _ in range(2)] == [2, 3]
    assert generic.qsize() == 9
    obj.unsubscribe(chart)
    assert chart not in obj._subscriber_keys
