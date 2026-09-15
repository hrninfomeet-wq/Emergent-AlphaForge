"""The tick-replay harness injects fake prices into a process that can place real
orders. These tests pin the gates that make that safe."""
import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from app import tick_replay
from app.tick_replay import ENV_FLAG, HARNESS_PREFIX, harness_key, make_tick, purge, replay


class _Manager:
    def __init__(self):
        self._latest_ticks = {}
        self.broadcasts = []

    def _broadcast(self, ticks):
        self.broadcasts.append(list(ticks))


@pytest.fixture
def enabled(monkeypatch):
    monkeypatch.setenv(ENV_FLAG, "1")
    yield
    monkeypatch.delenv(ENV_FLAG, raising=False)


def test_disabled_by_default(monkeypatch):
    monkeypatch.delenv(ENV_FLAG, raising=False)
    with pytest.raises(PermissionError):
        asyncio.run(replay(_Manager(), duration_s=0.1))


@pytest.mark.parametrize("value", ["", "0", "false", "no", "off", "maybe"])
def test_only_an_explicit_optin_enables_it(monkeypatch, value):
    monkeypatch.setenv(ENV_FLAG, value)
    with pytest.raises(PermissionError):
        asyncio.run(replay(_Manager(), duration_s=0.1))


def test_every_injected_key_is_in_the_reserved_namespace(enabled):
    """The load-bearing safety gate: nothing in the system references HARNESS|,
    so no position can be marked, exited or squared against an injected price."""
    mgr = _Manager()
    out = asyncio.run(replay(mgr, rate_hz=50, duration_s=0.3, keys=4))
    assert out["injected"] > 0
    assert all(k.startswith(HARNESS_PREFIX) for k in mgr._latest_ticks)
    assert all(t["instrument_key"].startswith(HARNESS_PREFIX)
               for batch in mgr.broadcasts for t in batch)


def test_a_real_instrument_key_is_never_touched(enabled):
    mgr = _Manager()
    mgr._latest_ticks["NSE_FO|47291"] = {"last_price": 161.9, "instrument_key": "NSE_FO|47291"}
    asyncio.run(replay(mgr, rate_hz=50, duration_s=0.2, keys=2))
    assert mgr._latest_ticks["NSE_FO|47291"]["last_price"] == 161.9


def test_it_both_stores_and_broadcasts_like_the_real_receive_loop(enabled):
    """If it only broadcast, it would measure the stream but not the store; if it
    only stored, it would never wake a stream. The real loop does both."""
    mgr = _Manager()
    asyncio.run(replay(mgr, rate_hz=40, duration_s=0.2, keys=1))
    assert mgr._latest_ticks, "nothing landed in the tick map"
    assert mgr.broadcasts, "nothing was broadcast to subscribers"


def test_ticks_carry_ingest_ts_so_latency_is_measurable(enabled):
    mgr = _Manager()
    asyncio.run(replay(mgr, rate_hz=40, duration_s=0.2, keys=1))
    tick = next(iter(mgr._latest_ticks.values()))
    assert isinstance(tick["ingest_ts"], int)
    assert tick["source"] == "tick_replay_harness"


def test_rate_is_honoured_within_tolerance(enabled):
    mgr = _Manager()
    out = asyncio.run(replay(mgr, rate_hz=40, duration_s=0.5, keys=4))
    assert 20 <= out["actual_rate_hz"] <= 80, out


def test_nothing_is_persisted(enabled):
    out = asyncio.run(replay(_Manager(), rate_hz=20, duration_s=0.15, keys=1))
    assert out["persisted"] is False


def test_purge_removes_only_harness_keys():
    mgr = _Manager()
    mgr._latest_ticks["NSE_FO|47291"] = {"last_price": 1}
    mgr._latest_ticks[harness_key(0)] = make_tick(harness_key(0), price=1, now_ms=1)
    mgr._latest_ticks[harness_key(1)] = make_tick(harness_key(1), price=1, now_ms=1)
    assert purge(mgr) == 2
    assert list(mgr._latest_ticks) == ["NSE_FO|47291"]


def test_duration_and_rate_are_clamped():
    """A harness must not be able to run away inside a live process. Tested on the
    pure clamp — asserting it by actually running would take the clamped 120s."""
    from app.tick_replay import MAX_DURATION_S, MAX_KEYS, MAX_RATE_HZ, clamp_params
    assert clamp_params(1e9, 1e9, 10_000) == (MAX_RATE_HZ, MAX_DURATION_S, MAX_KEYS)
    assert clamp_params(-5, -5, -5) == (0.1, 0.1, 1)
    assert clamp_params(40, 10, 4) == (40.0, 10.0, 4)
