"""The Live Trade Statistics routes say whether an OPEN journal row is vouched for.

`/live-broker/trade-history` returned raw `live_trades` docs and `/trade-stats` counted
`status == OPEN` per strategy — both the JOURNAL's word, nothing at the broker asked.
Both now carry the facts the app itself has: is the guard still marking the row
(`marked_at` within MARK_STALE_AFTER_SECONDS), how old is the mark, and was the row
entered on an earlier IST day. No broker call is added.
"""
from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

import app.db as app_db  # noqa: E402
import app.routers.live_broker as lb  # noqa: E402
from app.overview_open import annotate_live_open_row  # noqa: E402

NOW = datetime(2026, 9, 29, 6, 0, tzinfo=timezone.utc)           # 11:30 IST
TODAY = "2026-09-29"
CUT = NOW - timedelta(seconds=120)
TODAY_ENTRY = "2026-09-29T04:00:00+00:00"
OLD_ENTRY = "2026-09-16T04:00:00+00:00"
FRESH = (NOW - timedelta(seconds=10)).isoformat()
STALE = (NOW - timedelta(hours=3)).isoformat()


def _ann(row):
    return annotate_live_open_row(row, today_ist=TODAY, fresh_cut=CUT, now=NOW)


# --------------------------------------------------------------------------- #
# the pure annotation
# --------------------------------------------------------------------------- #

def test_a_freshly_marked_open_row_is_verified_with_its_age():
    a = _ann({"status": "OPEN", "created_at": TODAY_ENTRY, "marked_at": FRESH})
    assert a == {"open_state": "verified", "mark_age_s": 10, "carried": False,
                 "entry_day_ist": TODAY}


def test_a_stale_mark_is_unverified_and_its_age_is_reported():
    a = _ann({"status": "OPEN", "created_at": OLD_ENTRY, "marked_at": STALE})
    assert a["open_state"] == "unverified" and a["mark_age_s"] == 3 * 3600
    assert a["carried"] is True and a["entry_day_ist"] == "2026-09-16"


def test_a_never_marked_row_is_unverified_with_no_age_never_zero():
    a = _ann({"status": "OPEN", "created_at": TODAY_ENTRY})
    assert a["open_state"] == "unverified" and a["mark_age_s"] is None


def test_an_unparseable_mark_is_unverified_with_no_age():
    a = _ann({"status": "OPEN", "created_at": TODAY_ENTRY, "marked_at": "garbage"})
    assert a["open_state"] == "unverified" and a["mark_age_s"] is None


def test_the_freshness_bound_is_the_governors():
    from app.live_deploy_governor import MARK_STALE_AFTER_SECONDS
    at_bound = (NOW - timedelta(seconds=MARK_STALE_AFTER_SECONDS)).isoformat()
    past = (NOW - timedelta(seconds=MARK_STALE_AFTER_SECONDS + 1)).isoformat()
    assert _ann({"status": "OPEN", "marked_at": at_bound})["open_state"] == "verified"
    assert _ann({"status": "OPEN", "marked_at": past})["open_state"] == "unverified"


def test_a_mark_in_the_future_has_age_zero_not_negative():
    a = _ann({"status": "OPEN", "marked_at": (NOW + timedelta(seconds=30)).isoformat()})
    assert a["mark_age_s"] == 0


def test_closed_and_other_rows_get_nothing_added():
    for st in ("CLOSED", "closed", "REJECTED", None, ""):
        assert _ann({"status": st, "marked_at": FRESH}) == {}


# --------------------------------------------------------------------------- #
# the routes (in-memory live_trades, clock frozen)
# --------------------------------------------------------------------------- #

class _Cur:
    def __init__(self, rows):
        self._rows = list(rows)

    def sort(self, key, direction=1):
        self._rows.sort(key=lambda r: str(r.get(key) or ""), reverse=(direction == -1))
        return self

    def skip(self, n):
        self._rows = self._rows[int(n):]
        return self

    async def to_list(self, length=None):
        rows = self._rows if length is None else self._rows[: int(length)]
        return [dict(r) for r in rows]


class _Coll:
    def __init__(self, rows=()):
        self.rows = [dict(r) for r in rows]

    def _sel(self, q):
        return [r for r in self.rows if all(r.get(k) == v for k, v in (q or {}).items()
                                            if not isinstance(v, dict))]

    def find(self, q=None, projection=None):
        return _Cur(self._sel(q))

    async def count_documents(self, q):
        return len(self._sel(q))


class _Db:
    def __init__(self, live=()):
        self.live_trades = _Coll(live)
        self.strategy_deployments = _Coll([{"id": "D1", "name": "Dep One"}])


def _freeze(monkeypatch):
    class _DT(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW if tz is None else NOW.astimezone(tz)

    monkeypatch.setattr(lb, "datetime", _DT)


def _trade(tid, status, **kw):
    return {"id": tid, "deployment_id": "D1", "strategy_id": "S1", "status": status,
            "created_at": TODAY_ENTRY, **kw}


def _run(coro):
    return asyncio.run(coro)


def test_trade_history_annotates_open_rows_and_leaves_closed_rows_alone(monkeypatch):
    _freeze(monkeypatch)
    db = _Db([
        _trade("a", "OPEN", marked_at=FRESH),
        _trade("b", "OPEN", marked_at=STALE, created_at=OLD_ENTRY),
        _trade("c", "OPEN"),
        _trade("d", "CLOSED", realized_pnl=100.0, closed_at=TODAY_ENTRY),
    ])
    monkeypatch.setattr(app_db, "get_db", lambda: db)
    out = _run(lb.live_trade_history(limit=100, skip=0, status=None))
    by = {r["id"]: r for r in out["items"]}
    assert by["a"]["open_state"] == "verified" and by["a"]["carried"] is False
    assert by["b"]["open_state"] == "unverified" and by["b"]["carried"] is True
    assert by["b"]["entry_day_ist"] == "2026-09-16"
    assert by["c"]["open_state"] == "unverified" and by["c"]["mark_age_s"] is None
    assert "open_state" not in by["d"] and "mark_age_s" not in by["d"]
    assert by["a"]["deployment_name"] == "Dep One"


def test_trade_stats_counts_unverified_and_carried_open_rows_per_strategy(monkeypatch):
    _freeze(monkeypatch)
    db = _Db([
        _trade("a", "OPEN", marked_at=FRESH, unrealized_pnl=-10.0),
        _trade("b", "OPEN", marked_at=STALE, created_at=OLD_ENTRY, unrealized_pnl=-999.0),
        _trade("c", "OPEN", unrealized_pnl=5.0),
        _trade("d", "CLOSED", realized_pnl=100.0, closed_at=TODAY_ENTRY),
        {**_trade("e", "OPEN", marked_at=FRESH), "strategy_id": "S2"},
    ])
    monkeypatch.setattr(app_db, "get_db", lambda: db)
    out = _run(lb.live_trade_stats())
    by = {s["strategy_id"]: s for s in out["per_strategy"]}
    s1 = by["S1"]
    assert s1["open_count"] == 3                         # the journal's count, unchanged
    assert s1["open_unverified"] == 2                    # b (stale) and c (never marked)
    assert s1["open_carried"] == 1 and s1["open_carried_oldest"] == "2026-09-16"
    s2 = by["S2"]
    assert s2["open_count"] == 1 and s2["open_unverified"] == 0 and s2["open_carried"] == 0


def test_trade_stats_projection_includes_marked_at():
    """Without marked_at in the projection every OPEN row would read 'never marked'."""
    import inspect
    src = inspect.getsource(lb.live_trade_stats)
    assert '"marked_at": 1' in src
