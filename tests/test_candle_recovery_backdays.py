"""Gaps on PRIOR days must be recoverable too — with the right endpoint.

Recovery originally only handled today, via Upstox's intraday endpoint. That left
a real, still-open hole: 2026-08-13 shows 368/375 bars on ALL THREE instruments
(09:15-09:18, 09:54, 10:01-10:02 — a feed-level dropout, not a boot gap), and
nothing would ever have closed it.

The two endpoints are not interchangeable, and picking the wrong one silently
returns nothing:

* **today** -> `/v3/historical-candle/intraday/{key}/minutes/1`. No date args; it
  returns today wholesale. The plain historical endpoint returns EMPTY for the
  in-progress day — the documented limitation that made the tick roller necessary.
* **any prior day** -> `/v3/historical-candle/{key}/minutes/1/{to}/{from}`.
  Verified live on 2026-08-15: `status=success`, **375 candles** for 2026-08-14,
  09:15 -> 15:29. The intraday endpoint cannot serve a prior day at all.

So the source is a function of WHICH DAY is being repaired, and that choice is the
thing these tests pin.
"""
from __future__ import annotations

import asyncio
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from app.candle_gap import MINUTE_MS  # noqa: E402
from app.candle_recovery import recover_day, recover_recent_days  # noqa: E402

FULL = 375
OPEN_0814 = 1786679100000                 # 2026-08-14 09:15 IST
OPEN_0813 = OPEN_0814 - 86_400_000        # 2026-08-13 09:15 IST


def _ist(base, hh, mm):
    return base + ((hh * 60 + mm) - (9 * 60 + 15)) * MINUTE_MS


def _bars(ts_list, inst="NIFTY"):
    return pd.DataFrame([{
        "instrument": inst, "ts": int(t), "datetime": "", "open": 100.0,
        "high": 101.0, "low": 99.0, "close": 100.5, "volume": 0,
    } for t in ts_list])


class FakeDB:
    def __init__(self, seed=None):
        self.rows = {}
        for inst, ts_list in (seed or {}).items():
            for t in ts_list:
                self.rows[(inst, t)] = {"instrument": inst, "ts": t}
        self.candles_1m = self._Coll(self)

    class _Coll:
        def __init__(self, db):
            self.db = db

        def find(self, q, proj=None):
            inst, lo, hi = q["instrument"], q["ts"]["$gte"], q["ts"]["$lt"]
            got = [{"ts": t} for (i, t) in self.db.rows if i == inst and lo <= t < hi]

            class _Cur:
                async def to_list(self_inner, length=None):
                    return got
            return _Cur()

    async def persist(self, instrument, df):
        added = updated = 0
        for t in df["ts"].tolist():
            key = (instrument, int(t))
            updated += key in self.rows
            added += key not in self.rows
            self.rows[key] = {"instrument": instrument, "ts": int(t)}
        return {"candles_added": added, "candles_updated": updated}


def _run(c):
    return asyncio.run(c)


# --------------------------------------------------------------------------
# Endpoint selection — the whole point
# --------------------------------------------------------------------------

def test_today_uses_the_INTRADAY_endpoint():
    db = FakeDB({"NIFTY": [_ist(OPEN_0814, 9, 50) + i * MINUTE_MS for i in range(340)]})
    used = []

    async def intraday(inst):
        used.append("intraday")
        return _bars([_ist(OPEN_0814, 9, 15) + i * MINUTE_MS for i in range(FULL)])

    async def historical(inst, day):
        used.append("historical")
        return _bars([])

    r = _run(recover_day(db, "NIFTY", iso_date="2026-08-14",
                         now_ms=_ist(OPEN_0814, 16, 0),
                         intraday_fetch=intraday, historical_fetch=historical,
                         persist=db.persist, today_iso="2026-08-14"))
    assert used == ["intraday"]
    assert r["status"] == "recovered" and r["source"] == "upstox_intraday"


def test_a_prior_day_uses_the_HISTORICAL_endpoint():
    """The intraday endpoint cannot serve a prior day; using it would silently
    return nothing and report the gap as unresolvable."""
    db = FakeDB({"NIFTY": [_ist(OPEN_0813, 9, 19) + i * MINUTE_MS for i in range(356)]})
    used = []

    async def intraday(inst):
        used.append("intraday")
        return _bars([])

    async def historical(inst, day):
        used.append(f"historical:{day}")
        return _bars([_ist(OPEN_0813, 9, 15) + i * MINUTE_MS for i in range(FULL)])

    r = _run(recover_day(db, "NIFTY", iso_date="2026-08-13",
                         now_ms=_ist(OPEN_0814, 16, 0),
                         intraday_fetch=intraday, historical_fetch=historical,
                         persist=db.persist, today_iso="2026-08-14"))
    assert used == ["historical:2026-08-13"]
    assert r["status"] == "recovered" and r["source"] == "upstox_historical"


def test_the_real_2026_08_13_dropout_shape_is_recovered():
    """368/375 with holes at 09:15-09:18, 09:54, 10:01-10:02."""
    full = [_ist(OPEN_0813, 9, 15) + i * MINUTE_MS for i in range(FULL)]
    holes = {_ist(OPEN_0813, 9, 15), _ist(OPEN_0813, 9, 16), _ist(OPEN_0813, 9, 17),
             _ist(OPEN_0813, 9, 18), _ist(OPEN_0813, 9, 54),
             _ist(OPEN_0813, 10, 1), _ist(OPEN_0813, 10, 2)}
    db = FakeDB({"NIFTY": [t for t in full if t not in holes]})

    async def historical(inst, day):
        return _bars(full)

    r = _run(recover_day(db, "NIFTY", iso_date="2026-08-13",
                         now_ms=_ist(OPEN_0814, 16, 0),
                         intraday_fetch=None, historical_fetch=historical,
                         persist=db.persist, today_iso="2026-08-14"))
    assert r["before"]["missing"] == 7 and len(r["before"]["ranges"]) == 3
    assert r["status"] == "recovered" and r["after"]["present"] == FULL


def test_a_prior_day_is_bounded_by_the_full_session_not_by_now():
    """`now_ms` must not truncate a COMPLETED day: a gap at 15:20 on a prior day
    is still a gap even though 'now' is the next morning."""
    db = FakeDB({"NIFTY": [_ist(OPEN_0813, 9, 15) + i * MINUTE_MS for i in range(300)]})
    seen = {}

    async def historical(inst, day):
        seen["day"] = day
        return _bars([])

    r = _run(recover_day(db, "NIFTY", iso_date="2026-08-13",
                         now_ms=_ist(OPEN_0814, 9, 30),      # next morning
                         intraday_fetch=None, historical_fetch=historical,
                         persist=db.persist, today_iso="2026-08-14"))
    assert r["before"]["expected"] == FULL, "a completed day must expect the full session"
    assert r["before"]["missing"] == 75


# --------------------------------------------------------------------------
# The multi-day sweep
# --------------------------------------------------------------------------

def test_the_sweep_skips_holidays_and_weekends():
    db = FakeDB({})
    days = []

    async def historical(inst, day):
        days.append(day)
        return _bars([])

    _run(recover_recent_days(db, ["NIFTY"], days=5, now_ms=_ist(OPEN_0814, 16, 0),
                             intraday_fetch=None, historical_fetch=historical,
                             persist=db.persist))
    # 2026-08-15 Sat/Independence Day and 08-16 Sun must never be requested.
    assert "2026-08-15" not in days and "2026-08-16" not in days
    assert all(d <= "2026-08-14" for d in days)


def test_the_sweep_covers_today_and_the_prior_trading_days():
    db = FakeDB({})
    seen = []

    async def intraday(inst):
        seen.append("today")
        return _bars([])

    async def historical(inst, day):
        seen.append(day)
        return _bars([])

    r = _run(recover_recent_days(db, ["NIFTY"], days=3, now_ms=_ist(OPEN_0814, 16, 0),
                                 intraday_fetch=intraday, historical_fetch=historical,
                                 persist=db.persist))
    assert seen[0] == "today", "today must be repaired first — it is the live one"
    assert len(r["days"]) == 3


def test_a_complete_prior_day_spends_no_call():
    full = [_ist(OPEN_0813, 9, 15) + i * MINUTE_MS for i in range(FULL)]
    db = FakeDB({"NIFTY": full})
    calls = []

    async def historical(inst, day):
        calls.append(day)
        return _bars([])

    r = _run(recover_day(db, "NIFTY", iso_date="2026-08-13",
                         now_ms=_ist(OPEN_0814, 16, 0),
                         intraday_fetch=None, historical_fetch=historical,
                         persist=db.persist, today_iso="2026-08-14"))
    assert r["status"] == "already_complete" and calls == []


def test_the_sweep_reports_every_unresolved_instrument_day():
    db = FakeDB({"NIFTY": [_ist(OPEN_0813, 9, 15) + i * MINUTE_MS for i in range(100)]})

    async def historical(inst, day):
        raise RuntimeError("broker down")

    async def intraday(inst):
        raise RuntimeError("broker down")

    r = _run(recover_recent_days(db, ["NIFTY"], days=2, now_ms=_ist(OPEN_0814, 16, 0),
                                 intraday_fetch=intraday, historical_fetch=historical,
                                 persist=db.persist))
    assert r["complete"] is False
    assert any("NIFTY" in u for u in r["unresolved"])


def test_the_sweep_never_raises_and_always_reports():
    db = FakeDB({})

    async def boom(*a, **k):
        raise RuntimeError("x")

    r = _run(recover_recent_days(db, ["NIFTY"], days=2, now_ms=_ist(OPEN_0814, 16, 0),
                                 intraday_fetch=boom, historical_fetch=boom,
                                 persist=db.persist))
    assert isinstance(r["days"], list) and r["complete"] is False
