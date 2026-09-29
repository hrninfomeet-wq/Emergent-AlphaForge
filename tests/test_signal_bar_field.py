"""A signal's bar minute is `candle_ts` — never `bar_ts`.

Measured on the real database 2026-09-29: 0 of 4020 signals carried `bar_ts`; 4010
carried `candle_ts` (and `context.candle.ts`). `bar_ts` is the evaluation AUDIT
record's name for the same minute. Everything that read the bar off a signal as
`bar_ts` was therefore silently blind:

  * the Signal Journal's date filter matched NOTHING (every date came back empty) and
    its default "newest bar first" sort sorted on a missing field;
  * the overview's "Signals today" counted ZERO for every deployment, every day;
  * the new EXPIRED chip and the unactioned-signal sweep saw no bar at all.

These tests build signals in the REAL shape, taken from the database.
"""
from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.signal_lifecycle import signal_bar_ms  # noqa: E402

IST = timezone(timedelta(hours=5, minutes=30))
# 2026-09-29 13:59 IST — the minute of a real CONFIRMED signal sampled from the DB.
BAR = 1790670540000


def _real_signal(sig_id: str, bar_ms: int = BAR, **extra) -> Dict[str, Any]:
    """The shape the evaluator writes (keys sampled from the real database)."""
    return {"id": sig_id, "state": "CONFIRMED", "deployment_id": "D1",
            "strategy_id": "s", "instrument": "SENSEX", "direction": "CE",
            "blocked": False, "candle_ts": bar_ms, "confidence": 80,
            "context": {"candle": {"ts": bar_ms, "ist_time": "13:59"}},
            "created_at": "2026-09-29T08:30:09+00:00",
            "updated_at": "2026-09-29T08:30:10+00:00", **extra}


# --------------------------------------------------------------------------- #
# the resolver
# --------------------------------------------------------------------------- #

def test_the_real_shape_resolves_from_candle_ts():
    sig = _real_signal("a")
    assert "bar_ts" not in sig
    assert signal_bar_ms(sig) == BAR


def test_the_nested_candle_time_and_a_legacy_bar_ts_are_fallbacks():
    nested = _real_signal("b")
    del nested["candle_ts"]
    assert signal_bar_ms(nested) == BAR
    legacy = {"bar_ts": BAR}
    assert signal_bar_ms(legacy) == BAR


@pytest.mark.parametrize("bad", [None, "", "abc", 0, -5])
def test_an_unreadable_bar_is_none_never_zero(bad):
    assert signal_bar_ms({"candle_ts": bad, "context": {"candle": {"ts": bad}}}) is None


def test_a_non_dict_context_does_not_raise():
    assert signal_bar_ms({"context": "junk"}) is None


# --------------------------------------------------------------------------- #
# the Signal Journal route
# --------------------------------------------------------------------------- #

def _get(row, key):
    cur = row
    for part in key.split("."):
        cur = cur.get(part) if isinstance(cur, dict) else None
    return cur


def _match(row: Dict[str, Any], q: Dict[str, Any]) -> bool:
    for k, v in (q or {}).items():
        rv = _get(row, k)
        if isinstance(v, dict):
            if "$in" in v and rv not in v["$in"]:
                return False
            if "$exists" in v and (rv is not None) != bool(v["$exists"]):
                return False
            if v.get("$type") == "string" and not isinstance(rv, str):
                return False
            if "$gte" in v and (rv is None or not rv >= v["$gte"]):
                return False
            if "$lt" in v and (rv is None or not rv < v["$lt"]):
                return False
            if "$ne" in v and rv == v["$ne"]:
                return False
        elif rv != v:
            return False
    return True


class _Cur:
    def __init__(self, rows):
        self._rows = rows

    def sort(self, key, direction=1):
        # Mongo sorts a missing field as the lowest value; an unsortable key must
        # not silently pass as sorted, so rows WITHOUT the key keep insertion order
        # at the end — which is what made the bar_ts sort a no-op.
        have = [r for r in self._rows if _get(r, key) is not None]
        rest = [r for r in self._rows if _get(r, key) is None]
        have.sort(key=lambda r: _get(r, key), reverse=(direction == -1))
        self._rows = have + rest
        return self

    def skip(self, n):
        self._rows = self._rows[int(n):]
        return self

    def limit(self, n):
        self._rows = self._rows[: int(n)]
        return self

    async def to_list(self, length=None):
        return [dict(r) for r in (self._rows if length is None else self._rows[: int(length)])]


class _Coll:
    def __init__(self, rows=None):
        self.rows: List[Dict[str, Any]] = list(rows or [])
        self.pipelines: List[Any] = []

    def find(self, q=None, proj=None):
        return _Cur([r for r in self.rows if _match(r, q or {})])

    async def count_documents(self, q):
        return len([r for r in self.rows if _match(r, q or {})])

    def aggregate(self, pipeline):
        self.pipelines.append(pipeline)
        return _Cur([])


class _DB:
    def __init__(self, signals=()):
        self.signals = _Coll(signals)
        self.paper_trades = _Coll()
        self.live_trades = _Coll()
        self.strategy_deployments = _Coll()


def _enriched(monkeypatch, db, **kw):
    import app.routers.journals as j
    monkeypatch.setattr(j, "get_db", lambda: db)
    params = dict(deployment_id=None, strategy_id=None, instrument=None, state=None,
                  clean=None, date_from=None, date_to=None, sort="-bar_ts",
                  skip=0, limit=100, format=None)
    params.update(kw)
    return asyncio.run(j.list_signals_enriched(**params))


def test_a_date_filter_finds_the_days_signals(monkeypatch):
    day_before = BAR - 24 * 3600 * 1000
    db = _DB([_real_signal("today"), _real_signal("yesterday", bar_ms=day_before)])
    out = _enriched(monkeypatch, db, date_from="2026-09-29", date_to="2026-09-29")
    assert [i["id"] for i in out["items"]] == ["today"], "the date filter matched nothing"
    assert out["total"] == 1


def test_each_row_carries_its_bar_minute(monkeypatch):
    out = _enriched(monkeypatch, _DB([_real_signal("a")]))
    row = out["items"][0]
    assert row["bar_ts"] == BAR
    assert row["bar_ist"] == "13:59"


def test_the_default_sort_is_newest_bar_first(monkeypatch):
    rows = [_real_signal(f"s{i}", bar_ms=BAR + i * 60000) for i in (1, 3, 2)]
    out = _enriched(monkeypatch, _DB(rows))
    assert [i["id"] for i in out["items"]] == ["s3", "s2", "s1"]


# --------------------------------------------------------------------------- #
# the overview's "Signals today"
# --------------------------------------------------------------------------- #

def test_the_overview_counts_todays_signals_by_their_bar(monkeypatch):
    """The overview aggregates in Mongo; the fake records the pipeline and this test
    EXECUTES its $match against a real-shaped signal from today."""
    import app.routers.deployments as dep

    class _DT(datetime):
        @classmethod
        def now(cls, tz=None):
            now = datetime.fromtimestamp(BAR / 1000 + 600, timezone.utc)
            return now if tz is None else now.astimezone(tz)

    db = _DB()
    db.strategy_deployments.rows.append({"id": "D1", "name": "D1", "mode": "paper",
                                         "status": "ACTIVE",
                                         "created_at": "2026-09-01T00:00:00+00:00"})
    monkeypatch.setattr(dep, "datetime", _DT)
    monkeypatch.setattr(dep, "get_db", lambda: db)
    asyncio.run(dep.deployments_overview())
    stage = next(p[0]["$match"] for p in db.signals.pipelines
                 if p and "$match" in p[0] and "deployment_id" in p[0]["$match"])
    assert _match(_real_signal("today"), stage), \
        "today's signal does not match the overview's 'signals today' filter"
    assert not _match(_real_signal("old", bar_ms=BAR - 24 * 3600 * 1000), stage)
