"""The trading clock — one server-side answer for every countdown.

Every clock on the live page was the browser's own: holiday-blind (the cockpit pill
read "MARKET OPEN" on NSE holidays), uncorrected for a skewed PC clock, and deriving
15:00 from yet another literal. describe_session derives each boundary from what
ENFORCES it, and hands the browser absolute instants to count down against.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, time as dtime, timezone

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from app.live.session_clock import describe_session  # noqa: E402

EOD = dtime(15, 0)


def _at(y, mo, d, hh, mm):
    """An IST wall-clock moment, expressed in UTC."""
    return datetime(y, mo, d, hh, mm, tzinfo=timezone.utc) - __import__("datetime").timedelta(
        hours=5, minutes=30)


@pytest.mark.parametrize("when,phase,next_kind", [
    (_at(2026, 9, 29, 8, 0), "pre_open", "market_open"),
    (_at(2026, 9, 29, 10, 30), "session", "entry_cutoff"),
    (_at(2026, 9, 29, 15, 5), "after_eod", "next_session_open"),
    (_at(2026, 9, 26, 11, 0), "closed_day", "next_session_open"),     # Saturday
    (_at(2026, 10, 2, 11, 0), "closed_day", "next_session_open"),     # Gandhi Jayanti
])
def test_phases(when, phase, next_kind):
    s = describe_session(when, eod_square_ist=EOD)
    assert s["phase"] == phase
    assert s["next_event"]["kind"] == next_kind


def test_a_weekday_holiday_is_named():
    """The cockpit pill said MARKET OPEN on exactly this day."""
    s = describe_session(_at(2026, 10, 2, 11, 0), eod_square_ist=EOD)
    assert s["day_kind"] == "holiday" and s["is_trading_day"] is False
    assert s["holiday_label"]


def test_the_next_session_skips_the_weekend_and_the_holiday():
    """Fri 02 Oct is a holiday: after Thursday's EOD the next open is Mon 05 Oct."""
    s = describe_session(_at(2026, 10, 1, 16, 0), eod_square_ist=EOD)
    assert s["next_event"]["date"] == "2026-10-05"
    assert s["next_event"]["at_ms"] == int(_at(2026, 10, 5, 9, 15).timestamp() * 1000)


def test_the_cutoff_is_the_gates_own_instant():
    from app.live.mode import entry_cutoff_today_ist
    now = _at(2026, 9, 29, 10, 30)
    s = describe_session(now, eod_square_ist=EOD)
    assert s["entry_cutoff_ms"] == int(
        datetime.fromisoformat(entry_cutoff_today_ist(now)).timestamp() * 1000)
    assert s["next_event"]["at_ms"] == s["entry_cutoff_ms"]


def test_the_eod_comes_from_the_guard_not_a_literal():
    """Cutoff and EOD are separate constants, equal only by coincidence. A guard
    configured for 15:10 must yield 15:10 — and an after_cutoff window."""
    s = describe_session(_at(2026, 9, 29, 15, 5), eod_square_ist=dtime(15, 10))
    assert s["eod_square_ist"] == "15:10"
    assert s["phase"] == "after_cutoff"
    assert s["next_event"] == {"kind": "eod_square",
                               "at_ms": int(_at(2026, 9, 29, 15, 10).timestamp() * 1000)}


def test_an_unknown_eod_is_null_not_guessed():
    s = describe_session(_at(2026, 9, 29, 15, 5), eod_square_ist=None)
    assert s["eod_square_ist"] is None and s["eod_square_ms"] is None
    assert s["phase"] == "after_cutoff" and s["next_event"] is None


def test_the_ist_day_boundary_is_used_not_utc():
    """18:40Z on the 28th is 00:10 IST on Tuesday the 29th — a trading day,
    pre-open. UTC would call it Monday evening."""
    s = describe_session(datetime(2026, 9, 28, 18, 40, tzinfo=timezone.utc),
                         eod_square_ist=EOD)
    assert s["ist_date"] == "2026-09-29" and s["phase"] == "pre_open"


def test_beyond_the_verified_calendar_the_next_session_is_unknown():
    s = describe_session(_at(2026, 12, 31, 16, 0), eod_square_ist=EOD)
    assert s["calendar_verified"] is True
    assert s["next_event"] is None      # 2027 is not a verified calendar


def test_the_server_anchor_is_now():
    now = _at(2026, 9, 29, 10, 30)
    assert describe_session(now, eod_square_ist=EOD)["server_now_ms"] == int(
        now.timestamp() * 1000)


# --------------------------------------------------------------------------- #
# The views that used it
# --------------------------------------------------------------------------- #

def test_the_governor_view_is_market_closed_on_a_holiday():
    import asyncio

    from app.live_deploy_governor import describe_live_caps
    from tests.test_live_deploy_governor import _DB
    dep = {"id": "d", "mode": "live",
           "risk": {"live": {"max_concurrent": 2, "daily_loss_cap": 3000.0}}}
    acct = {"max_lots_per_order": 20, "max_open_positions": 5, "daily_loss_limit": 5000}
    out = asyncio.run(describe_live_caps(_DB([]), dep, now_utc=_at(2026, 10, 2, 11, 0),
                                         account_config=acct, connected=True))
    assert out["binding"] == {"layer": "authorization", "reason": "market_closed_today",
                              "pause": False}


def test_arm_state_does_not_count_deployments_on_a_holiday(monkeypatch):
    """"LIVE — entries transmit real orders" on a holiday, with a token stored."""
    import asyncio

    import app.db
    import app.live.flattrade_token as ft
    import app.routers.live_broker as lb

    async def _doc():
        return {"jKey": "k"}

    async def _status(*a, **k):
        return {"connected": True, "expired": False}

    class _Mode:
        async def get(self):
            return {"mode": "LIVE_OFFLINE"}

    class _Cur:
        async def to_list(self, length=None):
            return [{"id": "d", "mode": "live", "status": "ACTIVE", "risk": {"live": {}}}]

    class _DB:
        class strategy_deployments:
            @staticmethod
            def find(*a, **k):
                return _Cur()

    class _FrozenDT(datetime):
        @classmethod
        def now(cls, tz=None):
            return _at(2026, 10, 2, 11, 0)

    monkeypatch.setattr(lb, "_get_token_doc", _doc)
    monkeypatch.setattr(ft, "get_status", _status)
    monkeypatch.setattr(lb, "_mode_store", lambda: _Mode())
    monkeypatch.setattr(app.db, "get_db", lambda: _DB())
    monkeypatch.setattr(lb, "datetime", _FrozenDT)
    st = asyncio.run(lb.get_arm_state())
    assert st["armed_deployments"] == 0 and st["would_transmit_entry"] is False
    assert st["session"]["phase"] == "closed_day"
