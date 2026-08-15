"""Mid-session candle recovery — the nine scenarios, driven through the real code.

Covers, one test group each: normal startup, mid-session startup, repeated recovery,
concurrent WebSocket traffic, partial backfill, broker failure and fallback, token
expiry, restart during recovery, and unresolved gaps.

Everything drives the real `candle_recovery` functions against a fake Mongo and fake
broker fetchers. No network, no real broker, and no order of any kind is ever placed.
"""
from __future__ import annotations

import asyncio
import os
import sys

import pandas as pd
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from app.candle_gap import MINUTE_MS, assess_completeness  # noqa: E402
from app.candle_recovery import recover_instrument, recover_today  # noqa: E402

DAY = "2026-08-14"
OPEN_MS = 1786679100000                      # 09:15 IST, verified against the warehouse
FULL = 375


def _ist(hh, mm):
    return OPEN_MS + ((hh * 60 + mm) - (9 * 60 + 15)) * MINUTE_MS


def _bars(ts_list):
    """A broker-shaped DataFrame (same columns fetch_intraday_1m returns)."""
    return pd.DataFrame([{
        "instrument": "NIFTY", "ts": t, "datetime": "", "open": 100.0,
        "high": 101.0, "low": 99.0, "close": 100.5, "volume": 0,
    } for t in ts_list])


class FakeDB:
    """Mongo stand-in with the real unique-key semantics: one row per (inst, ts)."""

    def __init__(self, seed=None):
        self.rows = {}                       # (inst, ts) -> doc
        for inst, ts_list in (seed or {}).items():
            for t in ts_list:
                self.rows[(inst, t)] = {"instrument": inst, "ts": t}
        self.candles_1m = self._Coll(self)

    class _Coll:
        def __init__(self, db):
            self.db = db

        def find(self, q, proj=None):
            inst = q["instrument"]
            lo = q["ts"]["$gte"]
            hi = q["ts"]["$lt"]
            got = [{"ts": t} for (i, t) in self.db.rows if i == inst and lo <= t < hi]

            class _Cur:
                async def to_list(self_inner, length=None):
                    return got
            return _Cur()

    async def persist(self, instrument, df):
        added = updated = 0
        for t in df["ts"].tolist():
            key = (instrument, int(t))
            if key in self.rows:
                updated += 1
            else:
                added += 1
            self.rows[key] = {"instrument": instrument, "ts": int(t)}
        return {"candles_added": added, "candles_updated": updated}

    def count(self, inst):
        return sum(1 for (i, _) in self.rows if i == inst)


def _run(coro):
    return asyncio.run(coro)


# --------------------------------------------------------------------------
# 1. Normal startup — nothing missing, nothing fetched
# --------------------------------------------------------------------------

def test_normal_startup_is_a_noop_and_spends_no_broker_call():
    db = FakeDB({"NIFTY": [_ist(9, 15) + i * MINUTE_MS for i in range(FULL)]})
    calls = []

    async def fetch(inst):
        calls.append(inst)
        return _bars([])

    r = _run(recover_instrument(db, "NIFTY", iso_date=DAY, now_ms=_ist(16, 0),
                                primary_fetch=fetch, persist=db.persist))
    assert r["status"] == "already_complete"
    assert calls == [], "a complete day must not spend a broker call"
    assert r["after"]["complete"] is True


def test_a_healthy_mid_session_state_is_also_a_noop():
    """Every CLOSED minute present at 10:31:30 — must not read as a gap."""
    now = _ist(10, 31) + 30_000
    have = [_ist(9, 15) + i * MINUTE_MS for i in range(76)]      # 09:15..10:30
    db = FakeDB({"NIFTY": have})
    calls = []

    async def fetch(inst):
        calls.append(inst)
        return _bars([])

    r = _run(recover_instrument(db, "NIFTY", iso_date=DAY, now_ms=now,
                                primary_fetch=fetch, persist=db.persist))
    assert r["status"] == "already_complete" and calls == []


# --------------------------------------------------------------------------
# 2. Mid-session startup — THE scenario (the real 2026-08-14 shape)
# --------------------------------------------------------------------------

def test_mid_session_startup_recovers_the_real_2026_08_14_hole():
    """340 bars from 09:50; the missing 09:15-09:49 must be closed."""
    db = FakeDB({"NIFTY": [_ist(9, 50) + i * MINUTE_MS for i in range(340)]})

    async def fetch(inst):                    # Upstox intraday returns the WHOLE day
        return _bars([_ist(9, 15) + i * MINUTE_MS for i in range(FULL)])

    r = _run(recover_instrument(db, "NIFTY", iso_date=DAY, now_ms=_ist(16, 0),
                                primary_fetch=fetch, persist=db.persist))
    assert r["before"]["missing"] == 35
    assert r["status"] == "recovered"
    assert r["after"]["complete"] is True and r["after"]["present"] == FULL
    assert r["source"] == "upstox_intraday"
    assert db.count("NIFTY") == FULL


def test_interior_feed_dropouts_are_recovered_too():
    """The real 2026-08-13 shape: 09:15-09:18, 09:54, 10:01-10:02 missing."""
    full = [_ist(9, 15) + i * MINUTE_MS for i in range(FULL)]
    holes = {_ist(9, 15), _ist(9, 16), _ist(9, 17), _ist(9, 18),
             _ist(9, 54), _ist(10, 1), _ist(10, 2)}
    db = FakeDB({"NIFTY": [t for t in full if t not in holes]})

    async def fetch(inst):
        return _bars(full)

    r = _run(recover_instrument(db, "NIFTY", iso_date=DAY, now_ms=_ist(16, 0),
                                primary_fetch=fetch, persist=db.persist))
    assert r["before"]["missing"] == 7 and len(r["before"]["ranges"]) == 3
    assert r["status"] == "recovered"


# --------------------------------------------------------------------------
# 3. Repeated recovery — idempotence
# --------------------------------------------------------------------------

def test_running_recovery_twice_changes_nothing_the_second_time():
    db = FakeDB({"NIFTY": [_ist(9, 50) + i * MINUTE_MS for i in range(340)]})
    full = [_ist(9, 15) + i * MINUTE_MS for i in range(FULL)]
    n_calls = []

    async def fetch(inst):
        n_calls.append(inst)
        return _bars(full)

    first = _run(recover_instrument(db, "NIFTY", iso_date=DAY, now_ms=_ist(16, 0),
                                    primary_fetch=fetch, persist=db.persist))
    after_first = db.count("NIFTY")
    second = _run(recover_instrument(db, "NIFTY", iso_date=DAY, now_ms=_ist(16, 0),
                                     primary_fetch=fetch, persist=db.persist))
    assert first["status"] == "recovered"
    assert second["status"] == "already_complete"
    assert db.count("NIFTY") == after_first == FULL
    assert len(n_calls) == 1, "the second pass must not re-fetch"


def test_recovery_is_idempotent_even_when_forced_to_refetch():
    """Same rows written twice must not duplicate — (instrument, ts) is unique."""
    full = [_ist(9, 15) + i * MINUTE_MS for i in range(FULL)]
    db = FakeDB({"NIFTY": full[:100]})

    async def fetch(inst):
        return _bars(full)

    _run(recover_instrument(db, "NIFTY", iso_date=DAY, now_ms=_ist(16, 0),
                            primary_fetch=fetch, persist=db.persist))
    _run(db.persist("NIFTY", _bars(full)))
    assert db.count("NIFTY") == FULL


# --------------------------------------------------------------------------
# 4. Concurrent WebSocket traffic — the reconciliation invariant
# --------------------------------------------------------------------------

def test_backfill_never_writes_the_in_progress_minute():
    """persist_candles_df is last-writer-wins with no merge, so a partial current
    bar could replace a complete roller-built one. It must be filtered out."""
    now = _ist(10, 31) + 30_000                # 10:31 is still forming
    db = FakeDB({"NIFTY": [_ist(9, 50) + i * MINUTE_MS for i in range(41)]})
    written = []

    async def fetch(inst):
        # A real broker returns today-so-far INCLUDING the partial current minute.
        return _bars([_ist(9, 15) + i * MINUTE_MS for i in range(77)])   # ..10:31

    async def persist(inst, df):
        written.extend(int(t) for t in df["ts"].tolist())
        return await db.persist(inst, df)

    _run(recover_instrument(db, "NIFTY", iso_date=DAY, now_ms=now,
                            primary_fetch=fetch, persist=persist))
    assert _ist(10, 31) not in written, "wrote the in-progress minute"
    assert _ist(10, 30) in written, "refused the last CLOSED minute"


def test_a_roller_bar_written_during_recovery_is_not_lost():
    """The roller keeps writing while recovery runs. Because both key on
    (instrument, ts), the union is what survives — no bar is dropped."""
    db = FakeDB({"NIFTY": [_ist(9, 50) + i * MINUTE_MS for i in range(340)]})
    full = [_ist(9, 15) + i * MINUTE_MS for i in range(FULL)]

    async def fetch(inst):
        # Simulate a concurrent roller flush landing mid-recovery.
        await db.persist("NIFTY", _bars([_ist(15, 29)]))
        return _bars(full[:200])              # broker returns only part of the day

    r = _run(recover_instrument(db, "NIFTY", iso_date=DAY, now_ms=_ist(16, 0),
                                primary_fetch=fetch, persist=db.persist))
    assert (("NIFTY", _ist(15, 29)) in db.rows), "the concurrent roller bar was lost"
    assert r["after"]["present"] > r["before"]["present"]


# --------------------------------------------------------------------------
# 5. Partial backfill — the broker returns less than the whole gap
# --------------------------------------------------------------------------

def test_a_partial_backfill_is_reported_as_improved_not_recovered():
    """Reporting a truncated fetch as success is the failure that matters here."""
    db = FakeDB({"NIFTY": [_ist(9, 50) + i * MINUTE_MS for i in range(340)]})

    async def fetch(inst):                    # only 20 of the 35 missing bars
        return _bars([_ist(9, 30) + i * MINUTE_MS for i in range(20)])

    r = _run(recover_instrument(db, "NIFTY", iso_date=DAY, now_ms=_ist(16, 0),
                                primary_fetch=fetch, persist=db.persist))
    assert r["status"] == "improved"
    assert r["after"]["complete"] is False
    assert r["after"]["missing"] == 15
    assert r["before"]["missing"] == 35


def test_the_verdict_comes_from_the_warehouse_not_from_what_the_broker_claimed():
    """A broker returning rows for the WRONG day must not read as recovered."""
    db = FakeDB({"NIFTY": [_ist(9, 50) + i * MINUTE_MS for i in range(340)]})

    async def fetch(inst):
        return _bars([_ist(9, 15) - 86_400_000 + i * MINUTE_MS for i in range(FULL)])

    r = _run(recover_instrument(db, "NIFTY", iso_date=DAY, now_ms=_ist(16, 0),
                                primary_fetch=fetch, persist=db.persist))
    assert r["status"] == "unresolved"
    assert r["after"]["missing"] == 35


# --------------------------------------------------------------------------
# 6. Broker failure and fallback
# --------------------------------------------------------------------------

def test_the_fallback_broker_is_used_when_the_primary_raises():
    db = FakeDB({"NIFTY": [_ist(9, 50) + i * MINUTE_MS for i in range(340)]})
    full = [_ist(9, 15) + i * MINUTE_MS for i in range(FULL)]

    async def primary(inst):
        raise RuntimeError("Upstox 503")

    async def fallback(inst, lo, hi):
        return _bars(full)

    r = _run(recover_instrument(db, "NIFTY", iso_date=DAY, now_ms=_ist(16, 0),
                                primary_fetch=primary, persist=db.persist,
                                fallback_fetch=fallback))
    assert r["source"] == "flattrade_tpseries"
    assert r["status"] == "recovered"
    assert any("Upstox 503" in e for e in r["errors"])


def test_the_fallback_is_given_only_the_missing_window():
    """TPSeries accepts st/et, so the fallback should not refetch the whole day."""
    db = FakeDB({"NIFTY": [_ist(9, 50) + i * MINUTE_MS for i in range(340)]})
    seen = {}

    async def primary(inst):
        return None

    async def fallback(inst, lo, hi):
        seen["lo"], seen["hi"] = lo, hi
        return _bars([_ist(9, 15) + i * MINUTE_MS for i in range(35)])

    _run(recover_instrument(db, "NIFTY", iso_date=DAY, now_ms=_ist(16, 0),
                            primary_fetch=primary, persist=db.persist,
                            fallback_fetch=fallback))
    assert seen["lo"] == _ist(9, 15) and seen["hi"] == _ist(9, 49)


def test_both_brokers_failing_is_reported_unresolved_and_never_raises():
    db = FakeDB({"NIFTY": [_ist(9, 50) + i * MINUTE_MS for i in range(340)]})

    async def primary(inst):
        raise RuntimeError("Upstox down")

    async def fallback(inst, lo, hi):
        raise RuntimeError("Flattrade down")

    r = _run(recover_instrument(db, "NIFTY", iso_date=DAY, now_ms=_ist(16, 0),
                                primary_fetch=primary, persist=db.persist,
                                fallback_fetch=fallback))
    assert r["status"] == "unresolved"
    assert len(r["errors"]) == 2
    assert r["after"]["missing"] == 35


def test_fetch_attempts_are_bounded():
    """An unbounded retry loop would hammer a rate budget shared with the account."""
    db = FakeDB({"NIFTY": [_ist(9, 50) + i * MINUTE_MS for i in range(340)]})
    n = []

    async def primary(inst):
        n.append(1)
        raise RuntimeError("boom")

    async def fallback(inst, lo, hi):
        n.append(1)
        raise RuntimeError("boom")

    _run(recover_instrument(db, "NIFTY", iso_date=DAY, now_ms=_ist(16, 0),
                            primary_fetch=primary, persist=db.persist,
                            fallback_fetch=fallback))
    assert len(n) <= 2


# --------------------------------------------------------------------------
# 7. Token expiry
# --------------------------------------------------------------------------

def test_token_expiry_surfaces_as_unresolved_with_the_reason_kept():
    db = FakeDB({"NIFTY": [_ist(9, 50) + i * MINUTE_MS for i in range(340)]})

    async def primary(inst):
        raise RuntimeError("Upstox token expired (401)")

    r = _run(recover_instrument(db, "NIFTY", iso_date=DAY, now_ms=_ist(16, 0),
                                primary_fetch=primary, persist=db.persist))
    assert r["status"] == "unresolved"
    assert any("expired" in e for e in r["errors"])


def test_a_later_pass_after_reauth_succeeds():
    """Recovery holds no state, so re-auth then re-run simply works."""
    db = FakeDB({"NIFTY": [_ist(9, 50) + i * MINUTE_MS for i in range(340)]})
    state = {"authed": False}
    full = [_ist(9, 15) + i * MINUTE_MS for i in range(FULL)]

    async def primary(inst):
        if not state["authed"]:
            raise RuntimeError("401")
        return _bars(full)

    first = _run(recover_instrument(db, "NIFTY", iso_date=DAY, now_ms=_ist(16, 0),
                                    primary_fetch=primary, persist=db.persist))
    state["authed"] = True
    second = _run(recover_instrument(db, "NIFTY", iso_date=DAY, now_ms=_ist(16, 0),
                                     primary_fetch=primary, persist=db.persist))
    assert first["status"] == "unresolved" and second["status"] == "recovered"


# --------------------------------------------------------------------------
# 8. Restart during recovery
# --------------------------------------------------------------------------

def test_a_crash_midway_leaves_a_smaller_gap_and_the_next_run_finishes_it():
    """There is no progress marker to corrupt: the assessment IS the state, so a
    half-written day is indistinguishable from any other partial day."""
    db = FakeDB({"NIFTY": [_ist(9, 50) + i * MINUTE_MS for i in range(340)]})
    full = [_ist(9, 15) + i * MINUTE_MS for i in range(FULL)]

    async def dies_halfway(inst):
        await db.persist("NIFTY", _bars(full[:18]))      # wrote 18 of 35
        raise RuntimeError("process killed")

    first = _run(recover_instrument(db, "NIFTY", iso_date=DAY, now_ms=_ist(16, 0),
                                    primary_fetch=dies_halfway, persist=db.persist))
    assert first["status"] == "improved"
    assert first["after"]["missing"] == 17

    async def ok(inst):
        return _bars(full)

    second = _run(recover_instrument(db, "NIFTY", iso_date=DAY, now_ms=_ist(16, 0),
                                     primary_fetch=ok, persist=db.persist))
    assert second["before"]["missing"] == 17          # picked up where it stopped
    assert second["status"] == "recovered"


def test_a_half_recovered_day_never_reports_complete():
    db = FakeDB({"NIFTY": [_ist(9, 50) + i * MINUTE_MS for i in range(340)]})
    v = assess_completeness(DAY, [t for (i, t) in db.rows], now_ms=_ist(16, 0))
    assert v["complete"] is False


# --------------------------------------------------------------------------
# 9. Unresolved gaps — the multi-instrument report
# --------------------------------------------------------------------------

def test_recover_today_reports_every_affected_instrument():
    db = FakeDB({
        "NIFTY": [_ist(9, 50) + i * MINUTE_MS for i in range(340)],
        "BANKNIFTY": [_ist(9, 15) + i * MINUTE_MS for i in range(FULL)],
        "SENSEX": [_ist(9, 15) + i * MINUTE_MS for i in range(300)],
    })

    async def fetch(inst):
        if inst == "NIFTY":
            return _bars([_ist(9, 15) + i * MINUTE_MS for i in range(FULL)])
        raise RuntimeError("no data")

    r = _run(recover_today(db, ["NIFTY", "BANKNIFTY", "SENSEX"],
                           primary_fetch=fetch, persist=db.persist,
                           now_ms=_ist(16, 0)))
    by = {x["instrument"]: x for x in r["instruments"]}
    assert by["NIFTY"]["status"] == "recovered"
    assert by["BANKNIFTY"]["status"] == "already_complete"
    assert by["SENSEX"]["status"] == "unresolved"
    assert r["complete"] is False
    assert r["unresolved"] == ["SENSEX"]


def test_recover_today_is_complete_when_every_instrument_is():
    db = FakeDB({i: [_ist(9, 15) + n * MINUTE_MS for n in range(FULL)]
                 for i in ("NIFTY", "BANKNIFTY", "SENSEX")})

    async def fetch(inst):
        return _bars([])

    r = _run(recover_today(db, ["NIFTY", "BANKNIFTY", "SENSEX"],
                           primary_fetch=fetch, persist=db.persist,
                           now_ms=_ist(16, 0)))
    assert r["complete"] is True and r["unresolved"] == []


def test_one_instrument_exploding_does_not_stop_the_others():
    db = FakeDB({"NIFTY": [], "BANKNIFTY": [_ist(9, 15) + i * MINUTE_MS
                                            for i in range(FULL)]})

    async def fetch(inst):
        if inst == "NIFTY":
            raise ValueError("unsupported instrument")
        return _bars([])

    r = _run(recover_today(db, ["NIFTY", "BANKNIFTY"], primary_fetch=fetch,
                           persist=db.persist, now_ms=_ist(16, 0)))
    assert len(r["instruments"]) == 2
    assert r["instruments"][1]["status"] == "already_complete"


def test_a_holiday_run_is_complete_and_spends_nothing():
    db = FakeDB({"NIFTY": []})
    calls = []

    async def fetch(inst):
        calls.append(inst)
        return _bars([])

    # 2026-01-26 is Republic Day.
    r = _run(recover_today(db, ["NIFTY"], primary_fetch=fetch, persist=db.persist,
                           now_ms=int(pd.Timestamp("2026-01-26 12:00:00",
                                                   tz="Asia/Kolkata").timestamp() * 1000)))
    assert r["complete"] is True and calls == []
