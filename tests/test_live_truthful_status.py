"""Live status indicators must say what is TRUE, not what was once configured.

2026-09-26: the operator read Flattrade as "connected" while every broker call
was 401ing on an expired daily token. The audit that followed found the
execution strip saying "LIVE — entries transmit real orders" / "auto-squares:
TRANSMIT" in exactly that state (a STORED token counted as connected), and every
guard indicator reading "ARMED" from a constant while the guard, cycling, could
not read a single price.
"""
from __future__ import annotations

import asyncio
import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from app.live.arm_state import compute_arm_state  # noqa: E402
from app.live.broker_protocol import TOKEN_EXPIRED_HINT  # noqa: E402
from app.live.live_position_guard import guard_health  # noqa: E402

NOW = datetime(2026, 9, 29, 5, 0, tzinfo=timezone.utc)


# --------------------------------------------------------------------------- #
# Execution-state verdict
# --------------------------------------------------------------------------- #

def test_an_expired_session_transmits_nothing_and_says_why():
    st = compute_arm_state(mode_doc={"mode": "LIVE_OFFLINE"}, connected=False,
                           autoplace_armed=True, armed_deployment_count=0,
                           session_expired=True)
    assert st["would_transmit_entry"] is False and st["would_transmit_exit"] is False
    assert st["session_expired"] is True
    assert "EXPIRED" in st["reasons"][0]


def test_session_expired_is_never_reported_alongside_connected():
    st = compute_arm_state(mode_doc=None, connected=True, autoplace_armed=False,
                           armed_deployment_count=0, session_expired=True)
    assert st["session_expired"] is False


def _no_real_db(monkeypatch):
    """get_arm_state scans deployments through app.db.get_db — never the real one."""
    import app.db

    class _Cur:
        async def to_list(self, length=None):
            return []

    class _Coll:
        def find(self, *a, **k):
            return _Cur()

    class _DB:
        strategy_deployments = _Coll()

    monkeypatch.setattr(app.db, "get_db", lambda: _DB())


def test_the_arm_state_route_does_not_count_an_expired_token_as_connected(monkeypatch):
    """The route used to set connected=True whenever a token doc EXISTED."""
    import app.live.flattrade_token as ft
    import app.routers.live_broker as lb

    async def _doc():
        return {"jKey": "k", "uid": "U"}

    async def _status(*a, **k):
        return {"connected": True, "expired": True}

    class _Mode:
        async def get(self):
            return {"mode": "LIVE_OFFLINE"}

    monkeypatch.setattr(lb, "_get_token_doc", _doc)
    monkeypatch.setattr(ft, "get_status", _status)
    monkeypatch.setattr(lb, "_mode_store", lambda: _Mode())
    _no_real_db(monkeypatch)
    st = asyncio.run(lb.get_arm_state())
    assert st["connected"] is False and st["session_expired"] is True
    assert st["would_transmit_exit"] is False


def test_a_valid_session_is_still_connected(monkeypatch):
    import app.live.flattrade_token as ft
    import app.routers.live_broker as lb

    async def _doc():
        return {"jKey": "k", "uid": "U"}

    async def _status(*a, **k):
        return {"connected": True, "expired": False}

    class _Mode:
        async def get(self):
            return {"mode": "LIVE_OFFLINE"}

    monkeypatch.setattr(lb, "_get_token_doc", _doc)
    monkeypatch.setattr(ft, "get_status", _status)
    monkeypatch.setattr(lb, "_mode_store", lambda: _Mode())
    _no_real_db(monkeypatch)
    st = asyncio.run(lb.get_arm_state())
    assert st["connected"] is True and st["would_transmit_exit"] is True


# --------------------------------------------------------------------------- #
# Guard health
# --------------------------------------------------------------------------- #

def _stats(**kw):
    s = {"running": True, "last_run_at": (NOW - timedelta(seconds=2)).isoformat(),
         "last_error": None, "guarded": 1}
    s.update(kw)
    return s


@pytest.mark.parametrize("stats,state", [
    (_stats(), "watching"),
    (_stats(guarded=0), "idle"),
    (_stats(last_error=TOKEN_EXPIRED_HINT), "blind"),
    (_stats(last_error="position read failed: Not_Ok"), "blind"),
    (_stats(last_error="no client"), "blind"),
    (_stats(running=False), "not_running"),
    (_stats(last_run_at=(NOW - timedelta(seconds=45)).isoformat()), "stalled"),
    (_stats(last_run_at=None), "stalled"),
    (_stats(last_error="NIFTY..: reprice unpriced (no quote) — retrying"), "watching"),
    # outside the session `_run` skips every cycle on purpose: never "stalled"
    (_stats(in_market_hours=False, last_run_at=None), "off_hours"),
    (_stats(in_market_hours=False, last_error=TOKEN_EXPIRED_HINT), "off_hours"),
    # ...but a real stall inside the session is still a stall, and an unknown
    # window keeps the old reading
    (_stats(in_market_hours=True, last_run_at=None), "stalled"),
    (_stats(in_market_hours=None, last_run_at=None), "stalled"),
    # a dead task outranks the clock
    (_stats(in_market_hours=False, running=False), "not_running"),
])
def test_guard_health(stats, state):
    assert guard_health(stats, now_utc=NOW)["state"] == state


def test_off_hours_names_the_positions_waiting_for_the_open():
    h = guard_health(_stats(in_market_hours=False, guarded=2), now_utc=NOW)
    assert h["label"] == "OFF HOURS" and "2 registered position(s)" in h["reason"]
    assert "registered" not in guard_health(_stats(in_market_hours=False, guarded=0),
                                            now_utc=NOW)["reason"]


def test_the_guard_reports_its_own_trading_window():
    from app.live.live_position_guard import LiveMonitorRegistry, LivePositionGuard

    async def _none():
        return None

    for inside in (True, False):
        g = LivePositionGuard(registry=LiveMonitorRegistry(), client_factory=_none,
                              square_fn=None, in_market_hours=lambda v=inside: v)
        assert g.status()["in_market_hours"] is inside

    def _boom():
        raise RuntimeError("clock")
    g = LivePositionGuard(registry=LiveMonitorRegistry(), client_factory=_none,
                          square_fn=None, in_market_hours=_boom)
    assert g.status()["in_market_hours"] is None


def test_a_blind_guard_says_stops_cannot_fire():
    h = guard_health(_stats(last_error=TOKEN_EXPIRED_HINT), now_utc=NOW)
    assert "cannot fire" in h["reason"] and h["label"] == "BLIND"


def test_the_guard_status_route_carries_health(monkeypatch):
    import app.routers.live_broker as lb
    import app.runtime as rt

    class _G:
        def status(self):
            # The ROUTE reads the real clock, so the last cycle must be recent in
            # real time. A fixed timestamp here made this pass only near 05:00Z.
            fresh = (datetime.now(timezone.utc) - timedelta(seconds=2)).isoformat()
            return _stats(last_error=TOKEN_EXPIRED_HINT, last_run_at=fresh)

    monkeypatch.setattr(rt, "live_position_guard", _G())
    out = asyncio.run(lb.guard_status())
    assert out["health"]["state"] == "blind"
