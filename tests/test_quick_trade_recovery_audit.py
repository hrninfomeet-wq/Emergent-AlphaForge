"""Executable audit evidence; reproduces unsafe existing behavior, not a fix.

Synthetic identities and quantities are not exchange metadata. No real broker,
runtime startup, network connection, or database is used. A new registry models
loss of in-process state; this is not an actual process/database crash test.
"""
import asyncio
import json
import socket
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.live.broker_protocol import BrokerReadError
from app.live.live_position_guard import LiveMonitorRegistry, LivePositionGuard
from app.live.live_sl_monitor import build_monitor_state, evaluate_exit

NOW = datetime(2026, 10, 23, 5, 0, tzinfo=timezone.utc)
CASES = [("NIFTY_AUDIT_CE", "NFO"), ("BANKNIFTY_AUDIT_CE", "NFO"),
         ("SENSEX_AUDIT_CE", "BFO")]


@pytest.fixture
def isolated_loop(monkeypatch):
    # Windows needs a local socketpair when creating the event loop itself.
    loop = asyncio.new_event_loop()
    def forbidden(*args, **kwargs):
        raise AssertionError("Audit must not connect to network or production DB")
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket.socket, "connect_ex", forbidden)
    yield loop
    loop.close()


class FakeClient:
    def __init__(self, positions=None, expired=False):
        self.positions = positions or []
        self.expired = expired

    async def position_book(self):
        if self.expired:
            raise BrokerReadError("Session Expired : Invalid Session Key", route="PositionBook")
        return list(self.positions)


def make_guard(registry, client, exits):
    async def factory():
        return client
    async def square(client, position, *, reason):
        exits.append({"tsym": position["tsym"], "reason": reason})
        return {"squared": True}
    return LivePositionGuard(registry=registry, client_factory=factory,
                             square_fn=square, now_fn=lambda: NOW)


@pytest.mark.parametrize("symbol,exchange", CASES)
def test_recovery_discards_locked_stop_and_target(symbol, exchange, isolated_loop):
    async def scenario():
        original = build_monitor_state(100.0, stop_pct=10, target_pct=20,
                                       trail={"mode": "lock", "trigger": 110, "lock_profit": 5})
        original = evaluate_exit(original, 110)["state"]
        assert original["stop_level"] == 105
        # Independent baseline: the old protection exits at current premium 100.
        assert evaluate_exit(original, 100)["exit"] is True
        registry = LiveMonitorRegistry()
        client = FakeClient([{"tsym": symbol, "exch": exchange, "netqty": "1",
                              "lp": "100", "daybuyavgprc": "100", "urmtom": "0"}])
        exits = []
        guard = make_guard(registry, client, exits)
        assert await guard.rehydrate_from_broker(owned_tsyms={symbol}) == 1
        recovered = registry.snapshot()[0]["state"]
        assert recovered["stop_level"] == 50
        assert recovered["target_level"] is None
        assert recovered["mode"] == "none"
        await guard._cycle()
        assert exits == []
        print(json.dumps({"case": "restart", "symbol": symbol,
                          "original_stop": original["stop_level"],
                          "recovered_stop": recovered["stop_level"],
                          "original_target": original["target_level"],
                          "recovered_target": recovered["target_level"],
                          "original_mode": original["mode"], "recovered_mode": recovered["mode"],
                          "current_premium": 100, "original_would_exit": True,
                          "exit_calls": len(exits)}))
    isolated_loop.run_until_complete(scenario())


def test_expired_session_retains_position_but_cannot_square(isolated_loop):
    async def scenario():
        registry = LiveMonitorRegistry()
        registry.register(key="AUDIT", tsym="NIFTY_AUDIT_CE", exch="NFO", qty=1,
                          prd="I", entry_price=100,
                          state=build_monitor_state(100.0, stop_pct=10))
        exits = []
        guard = make_guard(registry, FakeClient(expired=True), exits)
        await guard._cycle()
        assert exits == [] and len(registry) == 1
        assert "reconnect Flattrade" in guard.status()["last_error"]
        print(json.dumps({"case": "expired_session", "exit_calls": len(exits),
                          "registered": len(registry), "last_error": guard.status()["last_error"]}))
    isolated_loop.run_until_complete(scenario())
