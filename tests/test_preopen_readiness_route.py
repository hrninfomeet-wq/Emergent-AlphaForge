"""The persisted 08:45 IST readiness verdict, exposed read-only.

`_run_preopen_readiness` computed "flattrade_token_expired while live deployments are
armed" and wrote it to Mongo — and nothing ever read it. The route returns the latest
stored verdict wrapped with `is_today` / `days_ago`, so a previous day's verdict is
never mistaken for today's. No verdict is "no verdict", never "all clear".
"""
import asyncio
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

import app.db as app_db
import app.routers.live_broker as lb
from app.preopen_readiness import describe_stored_verdict


def _doc(session_date="2026-09-29", **kw):
    d = {
        "session_date": session_date,
        "evaluated_at": f"{session_date}T03:15:00+00:00",
        "trigger": "preopen_timer",
        "ready": False,
        "reason": "blocked",
        "blockers": [{"id": "flattrade_token_expired", "detail": "expired"}],
        "warnings": [{"id": "warehouse_actions_pending", "detail": "3 pending"}],
        "checks": {"trading_day": True},
        "live_deployment_count": 2,
    }
    d.update(kw)
    return d


# --------------------------------------------------------------------------- #
# the pure wrapper
# --------------------------------------------------------------------------- #

def test_a_todays_verdict_is_marked_today():
    out = describe_stored_verdict(_doc("2026-09-29"), "2026-09-29")
    assert out["is_today"] is True and out["days_ago"] == 0
    assert out["verdict"]["blockers"][0]["id"] == "flattrade_token_expired"
    assert out["verdict"]["ready"] is False and out["today"] == "2026-09-29"


def test_a_previous_days_verdict_is_not_today_and_says_how_old():
    out = describe_stored_verdict(_doc("2026-09-25"), "2026-09-29")
    assert out["is_today"] is False and out["days_ago"] == 4


def test_no_verdict_is_none_not_all_clear():
    out = describe_stored_verdict(None, "2026-09-29")
    assert out == {"verdict": None, "today": "2026-09-29", "is_today": None, "days_ago": None}


def test_an_unreadable_session_date_is_unknown_never_today():
    for bad in ("", None, "29/09/2026", "garbage"):
        out = describe_stored_verdict(_doc(session_date=bad), "2026-09-29")
        assert out["is_today"] is None and out["days_ago"] is None


def test_internal_fields_are_not_leaked_and_lists_are_copies():
    src = _doc()
    src["_id"] = "abc"
    out = describe_stored_verdict(src, "2026-09-29")
    assert "_id" not in out["verdict"] and "checks" not in out["verdict"]
    out["verdict"]["blockers"].append({"id": "x"})
    assert len(src["blockers"]) == 1


# --------------------------------------------------------------------------- #
# the route
# --------------------------------------------------------------------------- #

class _Coll:
    def __init__(self, docs=None, exc=None):
        self.docs, self.exc, self.calls = list(docs or []), exc, []

    async def find_one(self, flt, proj=None, sort=None):
        self.calls.append((flt, proj, sort))
        if self.exc:
            raise self.exc
        return dict(self.docs[0]) if self.docs else None


class _Db:
    def __init__(self, coll):
        self.preopen_readiness = coll


def _run(coro):
    return asyncio.run(coro)


def _freeze_ist_day(monkeypatch, y, m, d, hh=5, mm=0):
    """The route computes IST-today from datetime.now(timezone.utc)."""
    frozen = datetime(y, m, d, hh, mm, tzinfo=timezone.utc)

    class _DT(datetime):
        @classmethod
        def now(cls, tz=None):
            return frozen if tz is None else frozen.astimezone(tz)

    monkeypatch.setattr(lb, "datetime", _DT)


def test_route_returns_the_latest_verdict_newest_session_first(monkeypatch):
    coll = _Coll([_doc("2026-09-29")])
    monkeypatch.setattr(app_db, "get_db", lambda: _Db(coll))
    _freeze_ist_day(monkeypatch, 2026, 9, 29)
    out = _run(lb.live_preopen_readiness())
    assert out["is_today"] is True and out["error"] is None
    assert out["verdict"]["blockers"][0]["id"] == "flattrade_token_expired"
    flt, proj, sort = coll.calls[0]
    assert proj == {"_id": 0}
    assert sort == [("session_date", -1), ("evaluated_at", -1)]


def test_route_uses_the_IST_day_not_the_UTC_day(monkeypatch):
    """22:00 UTC on the 28th is 03:30 IST on the 29th."""
    monkeypatch.setattr(app_db, "get_db", lambda: _Db(_Coll([_doc("2026-09-29")])))
    _freeze_ist_day(monkeypatch, 2026, 9, 28, hh=22, mm=0)
    out = _run(lb.live_preopen_readiness())
    assert out["today"] == "2026-09-29" and out["is_today"] is True


def test_route_labels_an_old_verdict_as_not_today(monkeypatch):
    monkeypatch.setattr(app_db, "get_db", lambda: _Db(_Coll([_doc("2026-09-25")])))
    _freeze_ist_day(monkeypatch, 2026, 9, 29)
    out = _run(lb.live_preopen_readiness())
    assert out["is_today"] is False and out["days_ago"] == 4


def test_route_with_no_stored_verdict_says_none(monkeypatch):
    monkeypatch.setattr(app_db, "get_db", lambda: _Db(_Coll([])))
    _freeze_ist_day(monkeypatch, 2026, 9, 29)
    out = _run(lb.live_preopen_readiness())
    assert out["verdict"] is None and out["is_today"] is None and out["error"] is None


def test_route_never_raises_when_the_store_is_unreadable(monkeypatch):
    monkeypatch.setattr(app_db, "get_db", lambda: _Db(_Coll(exc=RuntimeError("mongo down"))))
    _freeze_ist_day(monkeypatch, 2026, 9, 29)
    out = _run(lb.live_preopen_readiness())
    assert out["verdict"] is None and out["error"] == "verdict store unreadable"
