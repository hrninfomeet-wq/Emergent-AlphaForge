"""The Upstox stream manager's status must say whether the socket is UP.

`status()["running"]` is "the task is alive" — and the task loops in backoff
forever after a dropped socket, so it stayed True while nothing was connected. The
market header turned that into a green "LIVE TICKS · Upstox WebSocket". These tests
drive the real `_run` loop with a fake websocket and pin the three facts a UI may
show: `connected`, `last_tick_age_s`, `last_error` — and that `running` keeps its
old meaning (the feed supervisor decides start/stop on it).
"""
import asyncio
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app import upstox_stream as us  # noqa: E402
from app.upstox_stream import UpstoxMarketStreamManager, _age_seconds  # noqa: E402


class _Socket:
    """An async-context-manager websocket: `frames` are returned by recv(); an
    Exception instance in `frames` is raised; running out of frames blocks forever."""

    def __init__(self, frames):
        self._frames = list(frames)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def send(self, _msg):
        return None

    async def recv(self):
        if not self._frames:
            await asyncio.Event().wait()
        item = self._frames.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class _FailingConnect:
    async def __aenter__(self):
        raise ConnectionError("connect refused (401)")

    async def __aexit__(self, *exc):
        return False


async def _url():
    return "wss://example.invalid/feed"


def _manager(connector):
    return UpstoxMarketStreamManager(
        authorize_url_fetcher=_url,
        websocket_connector=connector,
    )


async def _started_status(connector):
    """Start a manager on `connector`, let the loop run, read status, stop."""
    m = _manager(connector)
    await m.start(instrument_keys=["NSE_INDEX|Nifty 50"], persist=False)
    await asyncio.sleep(0.05)
    st = m.status()
    await m.stop()
    return st


# --------------------------------------------------------------------------- #
# the age helper
# --------------------------------------------------------------------------- #

def test_age_is_none_for_a_missing_or_garbage_timestamp_never_zero():
    assert _age_seconds(None) is None
    assert _age_seconds("") is None
    assert _age_seconds("not a time") is None


def test_age_is_measured_from_the_iso_stamp_and_never_negative():
    now = datetime(2026, 9, 29, 5, 0, 0, tzinfo=timezone.utc)
    assert _age_seconds((now - timedelta(seconds=42)).isoformat(), now) == 42.0
    # a stamp "in the future" (clock step) is clamped, not reported negative
    assert _age_seconds((now + timedelta(seconds=5)).isoformat(), now) == 0.0
    # naive stamps are read as UTC (what _now_iso writes is aware; be safe)
    assert _age_seconds("2026-09-29T04:59:00", now) == 60.0


# --------------------------------------------------------------------------- #
# the status the header reads
# --------------------------------------------------------------------------- #

def test_a_fresh_manager_reports_not_connected_and_no_tick_age():
    st = UpstoxMarketStreamManager().status()
    assert st["running"] is False
    assert st["connected"] is False
    assert st["last_tick_age_s"] is None


def test_a_failing_connection_is_running_but_not_connected_with_the_error():
    """THE defect: the task is alive (running True) while the socket never opens."""
    st = asyncio.run(_started_status(lambda url: _FailingConnect()))
    assert st["running"] is True          # the supervisor's contract is unchanged
    assert st["connected"] is False        # the health fact the header now reads
    assert "connect refused" in st["last_error"]
    assert st["reconnect_count"] >= 1
    assert st["last_tick_age_s"] is None


def test_an_open_socket_with_no_tick_yet_is_connected_with_no_age():
    st = asyncio.run(_started_status(lambda url: _Socket([])))
    assert st["running"] is True and st["connected"] is True
    assert st["last_tick_age_s"] is None    # never "0s" for "no tick received"


def test_a_tick_sets_a_small_age_and_a_drop_flips_connected_off(monkeypatch):
    tick = {"instrument_key": "NSE_INDEX|Nifty 50", "ltp": 24000.0, "ts": 1, "received_ts": 1}
    monkeypatch.setattr(us, "decode_market_data_feed", lambda frame: {})
    monkeypatch.setattr(us, "normalize_feed_response", lambda decoded: [dict(tick)])

    st = asyncio.run(_started_status(
        lambda url: _Socket([b"frame", ConnectionError("socket dropped")])))
    # the frame was ingested, then the socket dropped and the loop is backing off
    assert st["tick_count"] == 1
    assert st["last_tick_age_s"] is not None and 0.0 <= st["last_tick_age_s"] < 5.0
    assert st["running"] is True            # task alive in backoff
    assert st["connected"] is False         # ...but nothing is connected
    assert "socket dropped" in st["last_error"]
    assert st["reconnect_count"] == 1


def test_stop_clears_connected():
    async def go():
        m = _manager(lambda url: _Socket([]))
        await m.start(instrument_keys=["NSE_INDEX|Nifty 50"], persist=False)
        await asyncio.sleep(0.05)
        assert m.status()["connected"] is True
        return await m.stop()

    async def go_raw():
        m = _manager(lambda url: _Socket([]))
        await m.start(instrument_keys=["NSE_INDEX|Nifty 50"], persist=False)
        await asyncio.sleep(0.05)
        await m.stop()
        return m._session["connected"]

    st = asyncio.run(go())
    assert st["running"] is False and st["connected"] is False
    # the stored fact is cleared too, not merely masked by the finished task
    assert asyncio.run(go_raw()) is False
