"""Paper trades stranded by a missed square-off (Task B audit, 2026-09-29).

  (a) A boot-time square of a trade entered on an EARLIER IST day used to stamp
      `closed_at = boot time`, booking its P&L into the BOOT day — where the
      daily-loss kill switch could pause a deployment that never traded that day —
      and label a days-old mark a fresh, non-stale exit.
  (b) A backend that came up after the square-off time on the entry day left that
      day's trade OPEN overnight: the boot sweep is stale-only and the evaluator
      loop only swept inside 15:00-15:30.
  (c) `closed_at` is written in two timezone formats (IST "+05:30" by the sweep, UTC
      "+00:00" everywhere else) and two readers compared it as a STRING.

Everything here EXECUTES the real code. Time is injected (`now_ist`) or the runtime's
clock is patched exactly as tests/test_runtime_scheduled_squareoff.py does.
"""
from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from tests.test_signal_exit_lifecycle import (  # noqa: E402
    Coll, FakeDB, make_paper_trade, make_signal, run)

from app import runtime  # noqa: E402
from app.deployment_kill_switch import (  # noqa: E402
    check_deployment_kill_switches, check_soft_daily_governor, daily_realized_summary)
from app.paper_squareoff import (  # noqa: E402
    missed_squareoff_instant, square_off_open_paper_trades)
from app.trade_time import (  # noqa: E402
    in_window, instant_sort_key, parse_instant, realized_in_window)

IST = timezone(timedelta(hours=5, minutes=30))
UTC = timezone.utc

# "Boot" is the morning of Tue 2026-09-29; the stranded trade was entered Mon 09-28.
BOOT = datetime(2026, 9, 29, 10, 0, tzinfo=IST)
ENTERED = "2026-09-28T04:30:00+00:00"          # 10:00 IST on the 28th
MARKED = "2026-09-28T08:00:00+00:00"           # 13:30 IST — the last stored mark
MISSED_1500 = "2026-09-28T09:30:00+00:00"      # 15:00 IST on the 28th, in UTC


def _stranded(*, created=ENTERED, updated=MARKED, last=130.0, deployment_id=None,
              with_signal=False):
    db = FakeDB()
    sig = make_signal("ACTIVE")
    if with_signal:
        db.signals.rows.append(sig)
    trade = make_paper_trade(sig, last=last, deployment_id=deployment_id)
    trade["created_at"] = created
    trade["updated_at"] = updated
    db.paper_trades.rows.append(trade)
    return db, sig, trade


def _boot_sweep(db, **kw):
    return run(square_off_open_paper_trades(
        db, reason="boot_reconcile_missed_squareoff", now_ist=BOOT,
        entered_before_ist_date="2026-09-29", honour_allow_overnight=True, **kw))


# --------------------------------------------------------------------------- #
# (a) the boot sweep stamps the missed instant, not the boot time
# --------------------------------------------------------------------------- #

def test_a_boot_square_is_stamped_at_the_entry_days_square_off_not_at_boot():
    db, _, _ = _stranded()
    out = _boot_sweep(db)
    row = db.paper_trades.rows[0]
    assert row["status"] == "CLOSED"
    assert row["closed_at"] == MISSED_1500                    # 15:00 IST on the ENTRY day, UTC
    assert row["events"][-1]["at"] == MISSED_1500
    assert out[0]["closed_at"] == MISSED_1500


def test_the_stranded_pnl_lands_on_the_entry_day_not_the_boot_day():
    """THE incident: with closed_at = boot time, this loss was 'today's' on boot day."""
    db, _, _ = _stranded(last=60.0)                           # a loss
    _boot_sweep(db)
    closed = db.paper_trades.rows[0]
    assert closed["realized_pnl"] < 0
    assert daily_realized_summary([closed], "2026-09-29")["count"] == 0    # boot day: clean
    entry_day = daily_realized_summary([closed], "2026-09-28")
    assert entry_day["count"] == 1 and entry_day["net"] == closed["realized_pnl"]


def test_a_boot_square_is_never_labelled_a_fresh_exit():
    db, _, _ = _stranded()
    out = _boot_sweep(db)
    row = db.paper_trades.rows[0]
    assert row["exit_price_source"] == "last_mark"
    assert row["exit_price_stale"] is True                    # was False: a days-old mark read as fresh
    assert row["exit_mark_stale"] is True
    assert row["exit_mark_age_s"] == 5400                     # 13:30 mark -> 15:00 stamp = 90 min
    assert out[0]["exit_mark_stale"] is True and out[0]["exit_mark_age_s"] == 5400
    assert out[0]["exit_price_stale"] is True


def test_the_mark_age_is_none_when_the_mark_time_is_unknown_never_invented():
    db, _, _ = _stranded(updated=ENTERED, last=100.0)         # never marked: last == entry
    _boot_sweep(db)
    row = db.paper_trades.rows[0]
    assert row["exit_price_source"] == "entry_fallback"
    assert row["exit_mark_stale"] is True
    assert row["exit_mark_age_s"] is None


def test_a_mark_taken_after_the_stamped_close_has_age_zero_not_negative():
    db, _, _ = _stranded(created="2026-09-28T09:40:00+00:00",       # 15:10 IST
                         updated="2026-09-28T09:41:00+00:00")
    _boot_sweep(db)
    assert db.paper_trades.rows[0]["exit_mark_age_s"] == 0


def test_a_trade_entered_after_the_days_square_off_is_never_closed_before_it_opened():
    db, _, _ = _stranded(created="2026-09-28T09:40:00+00:00",       # 15:10 IST, after 15:00
                         updated="2026-09-28T09:41:00+00:00")
    _boot_sweep(db)
    assert db.paper_trades.rows[0]["closed_at"] == "2026-09-28T09:40:00+00:00"


def test_the_entry_day_is_the_ist_day_not_the_utc_day():
    """20:00 UTC on the 27th is 01:30 IST on the 28th: the entry day is the 28th."""
    db, _, _ = _stranded(created="2026-09-27T20:00:00+00:00",
                         updated="2026-09-28T04:00:00+00:00")
    _boot_sweep(db)
    assert db.paper_trades.rows[0]["closed_at"] == MISSED_1500      # the 28th, not the 27th


def test_the_signal_exit_carries_the_missed_instant_too():
    db, sig, _ = _stranded(with_signal=True)
    _boot_sweep(db)
    assert db.signals.rows[0]["state"] == "EXITED"
    assert db.signals.rows[0]["exited_at"] == MISSED_1500


def test_a_same_day_square_is_unchanged():
    db, _, _ = _stranded(created="2026-09-29T04:30:00+00:00", updated="2026-09-29T08:00:00+00:00")
    out = run(square_off_open_paper_trades(
        db, reason="auto_square_off_15_00_IST",
        now_ist=datetime(2026, 9, 29, 15, 0, tzinfo=IST)))
    row = db.paper_trades.rows[0]
    assert row["closed_at"] == "2026-09-29T15:00:00+05:30"          # the sweep's own stamp
    assert row["exit_price_stale"] is False
    assert "exit_mark_stale" not in row and "exit_mark_age_s" not in row
    assert "exit_mark_stale" not in out[0] and "closed_at" not in out[0]


def test_a_fresh_live_tick_is_a_real_fill_now_so_it_keeps_now():
    """A tick is priced at this instant; back-dating it to yesterday would be a lie."""
    import time
    db, _, _ = _stranded()
    tick = {"NSE_FO|1|CE": {"last_price": 141.0, "received_ts": int(time.time() * 1000)}}
    out = run(square_off_open_paper_trades(
        db, reason="manual_stop", now_ist=BOOT, latest_tick_lookup=tick.get))
    row = db.paper_trades.rows[0]
    assert row["exit_price_source"] == "live_tick" and row["exit_price"] == 141.0
    assert row["closed_at"] == "2026-09-29T10:00:00+05:30"
    assert row["exit_price_stale"] is False and "exit_mark_stale" not in row
    assert "exit_mark_stale" not in out[0]


def test_an_allow_overnight_position_closed_by_hand_is_flagged_but_keeps_now():
    """Held overnight ON PURPOSE, then squared by Stop: the exit really happens now,
    so closed_at is now — but the stored mark is a day old and must say so."""
    db, _, _ = _stranded(deployment_id="dep-O")
    db.strategy_deployments.rows.append({"id": "dep-O", "risk": {"allow_overnight": True}})
    run(square_off_open_paper_trades(db, reason="manual_stop", now_ist=BOOT))   # honour=False
    row = db.paper_trades.rows[0]
    assert row["closed_at"] == "2026-09-29T10:00:00+05:30"
    assert row["exit_mark_stale"] is True and row["exit_price_stale"] is True
    assert row["exit_mark_age_s"] == 73800                    # 13:30 IST yesterday -> 10:00 IST today


def test_a_non_overnight_position_closed_by_a_manual_stop_is_also_back_dated():
    db, _, _ = _stranded(deployment_id="dep-N")
    db.strategy_deployments.rows.append({"id": "dep-N", "risk": {"allow_overnight": False}})
    run(square_off_open_paper_trades(db, reason="manual_stop", now_ist=BOOT))
    assert db.paper_trades.rows[0]["closed_at"] == MISSED_1500


def test_an_unparseable_entry_time_is_not_treated_as_carried():
    db, _, _ = _stranded(created="garbage")
    run(square_off_open_paper_trades(db, reason="manual_stop", now_ist=BOOT))
    row = db.paper_trades.rows[0]
    assert row["closed_at"] == "2026-09-29T10:00:00+05:30"    # unknown date: never back-dated
    assert "exit_mark_stale" not in row


def test_the_boot_sweep_still_leaves_todays_entries_alone():
    db, _, _ = _stranded(created="2026-09-29T04:00:00+00:00", updated="2026-09-29T04:30:00+00:00")
    assert _boot_sweep(db) == []
    assert db.paper_trades.rows[0]["status"] == "OPEN"


def test_missed_squareoff_instant_is_1500_ist_in_utc_and_never_before_entry():
    assert missed_squareoff_instant("2026-09-28").isoformat() == MISSED_1500
    early = parse_instant("2026-09-28T03:45:00+00:00")
    assert missed_squareoff_instant("2026-09-28", early).isoformat() == MISSED_1500
    late = parse_instant("2026-09-28T09:40:00+00:00")
    assert missed_squareoff_instant("2026-09-28", late) == late


# --------------------------------------------------------------------------- #
# (b) the evaluator loop sweeps on "a trading day and now >= 15:00", not only in-window
# --------------------------------------------------------------------------- #

class _NoCandles:
    async def find_one(self, *a, **k):
        return None


class _LoopDB:
    candles_1m = _NoCandles()


def _drive_loop(monkeypatch, utc_now: datetime, cycles: int = 2):
    calls = []
    waits = 0

    class _Clock:
        @classmethod
        def now(cls, tz=None):
            return utc_now

    async def _wait(_timeout):
        nonlocal waits
        waits += 1
        if waits > cycles:
            raise asyncio.CancelledError

    async def _sweep(*args, **kwargs):
        calls.append((args, kwargs))
        return []

    monkeypatch.setattr(runtime, "datetime", _Clock)
    monkeypatch.setattr(runtime, "_evaluator_wait", _wait)
    monkeypatch.setattr(runtime, "get_db", lambda: _LoopDB())
    monkeypatch.setattr(runtime, "square_off_open_paper_trades", _sweep)
    monkeypatch.setattr(runtime.upstox_stream_manager, "latest_tick_map", lambda: {})
    # The REAL is_square_off_due is used on purpose: the holiday / weekend / cutoff
    # rules are part of what is being pinned.
    try:
        asyncio.run(runtime._deployment_evaluator_loop())
    except asyncio.CancelledError:
        pass
    return calls


@pytest.mark.parametrize("when, label", [
    (datetime(2026, 9, 29, 10, 15, tzinfo=UTC), "15:45 IST — after the 15:30 close"),
    (datetime(2026, 9, 29, 15, 0, tzinfo=UTC), "20:30 IST — evening"),
    (datetime(2026, 9, 29, 9, 30, tzinfo=UTC), "15:00 IST — the in-window minute"),
])
def test_a_late_backend_still_sweeps_the_days_open_trades_exactly_once(monkeypatch, when, label):
    calls = _drive_loop(monkeypatch, when, cycles=3)
    assert len(calls) == 1, f"{label}: expected one sweep across 3 cycles, got {len(calls)}"
    args, kwargs = calls[0]
    assert kwargs["reason"] == "auto_square_off_15_00_IST"
    assert kwargs["honour_allow_overnight"] is True
    assert (kwargs["now_ist"].hour, kwargs["now_ist"].minute) == (
        (when + timedelta(hours=5, minutes=30)).hour, (when + timedelta(hours=5, minutes=30)).minute)


@pytest.mark.parametrize("when, label", [
    (datetime(2026, 9, 29, 9, 29, tzinfo=UTC), "14:59 IST — before the cutoff"),
    (datetime(2026, 9, 29, 2, 30, tzinfo=UTC), "08:00 IST — pre-market"),
    (datetime(2026, 9, 26, 10, 15, tzinfo=UTC), "a Saturday"),
    (datetime(2026, 10, 2, 10, 15, tzinfo=UTC), "a gazetted holiday (Gandhi Jayanti, Friday)"),
])
def test_no_sweep_before_the_cutoff_or_on_a_day_the_market_never_opened(monkeypatch, when, label):
    assert _drive_loop(monkeypatch, when) == [], label


# --------------------------------------------------------------------------- #
# (c) two closed_at formats, compared as instants
# --------------------------------------------------------------------------- #

IST_1500 = "2026-09-28T15:00:00+05:30"     # == 09:30Z
UTC_1000 = "2026-09-28T10:00:00+00:00"     # 30 minutes LATER, yet sorts EARLIER as text


def test_the_two_formats_name_instants_that_do_not_sort_alike_as_text():
    assert UTC_1000 < IST_1500                                   # the trap, as strings
    assert instant_sort_key(IST_1500) < instant_sort_key(UTC_1000)   # the truth, as instants


def test_parse_instant_reads_both_formats_z_and_naive_and_rejects_garbage():
    assert parse_instant(IST_1500) == parse_instant("2026-09-28T09:30:00Z")
    assert parse_instant("2026-09-28T09:30:00") == parse_instant("2026-09-28T09:30:00+00:00")
    assert parse_instant(" 2026-09-28T09:30:00+00:00 ") is not None
    for bad in (None, "", "garbage", "2026-13-40"):
        assert parse_instant(bad) is None
    assert instant_sort_key("garbage") == float("-inf")          # unknown sorts oldest, as "" did


def test_in_window_is_half_open_and_fails_closed():
    lo, hi = "2026-09-28T18:30:00+00:00", "2026-09-29T18:30:00+00:00"
    assert in_window("2026-09-29T00:00:00+05:30", lo, hi)        # 18:30Z on the 28th: the boundary
    assert not in_window("2026-09-29T18:30:00+00:00", lo, hi)    # end is exclusive
    assert not in_window("2026-09-28T23:59:00+05:30", lo, hi)    # 18:29Z: just before
    assert in_window("2026-09-29T20:00:00+00:00", lo)            # open-ended
    assert not in_window("garbage", lo, hi)                      # unknown is not "today"
    assert not in_window(IST_1500, "garbage")                    # a bad bound never matches


def test_realized_in_window_sums_per_deployment_over_instants():
    lo, hi = "2026-09-28T18:30:00+00:00", "2026-09-29T18:30:00+00:00"
    rows = [
        {"deployment_id": "A", "closed_at": "2026-09-29T09:30:00+05:30", "realized_pnl": 100.0},
        {"deployment_id": "A", "closed_at": "2026-09-28T19:00:00+00:00", "realized_pnl": 10.0},
        {"deployment_id": "A", "closed_at": "2026-09-28T23:59:00+05:30", "realized_pnl": 1000.0},  # yesterday
        {"deployment_id": "A", "closed_at": "2026-09-29T20:00:00+00:00", "realized_pnl": 5000.0},  # tomorrow
        {"deployment_id": "A", "closed_at": "2026-09-29T05:00:00+00:00", "realized_pnl": -30.0,
         "exit_day_unknown": True},
        {"deployment_id": "A", "closed_at": "garbage", "realized_pnl": 7777.0},
        {"deployment_id": "A", "closed_at": "2026-09-29T05:00:00+00:00", "realized_pnl": float("nan")},
        {"deployment_id": "A", "closed_at": "2026-09-29T05:00:00+00:00", "realized_pnl": None},
        {"deployment_id": "B", "closed_at": "2026-09-29T05:00:00+00:00", "realized_pnl": -5.0},
    ]
    assert realized_in_window(rows, lo, hi) == {"A": 110.0, "B": -5.0}


def _kill_db(*trades):
    db = FakeDB()
    db.paper_trades.rows.extend(trades)
    return db


def _closed(tid, pnl, closed_at, dep="D1", created="2026-09-28T04:00:00+00:00"):
    return {"id": tid, "deployment_id": dep, "status": "CLOSED", "realized_pnl": pnl,
            "closed_at": closed_at, "created_at": created, "entry_value": 1000.0}


def test_the_trailing_loss_run_orders_closes_by_instant_not_by_text():
    """Z loss (08:00Z), X WIN (09:30Z, stamped IST), Y loss (10:00Z). By instant the
    last close is Y, a loss: run = 1 -> max_consecutive_losses=1 pauses. As TEXT the
    order is Z, Y, X, ending on the win: run = 0 and the switch never fires."""
    db = _kill_db(_closed("Z", -10.0, "2026-09-28T08:00:00+00:00"),
                  _closed("X", 50.0, IST_1500),
                  _closed("Y", -20.0, UTC_1000))
    dep = {"id": "D1", "mode": "paper", "risk": {"max_consecutive_losses": 1}}
    out = run(check_deployment_kill_switches(db, dep, today_ist="2026-09-29"))
    assert out["inputs"]["consecutive_losses"] == 1
    assert out["pause"] is True and out["pause_switch"] == "max_consecutive_losses"


def test_the_soft_daily_governor_orders_closes_by_instant_too():
    """Loss cap 100: T1 -150 (09:30Z, IST-stamped) then T2 +200 (10:00Z). By instant
    the running P&L dips to -150 first -> halt. As text T2 sorts first, the dip never
    happens, and the governor stays open."""
    t1 = _closed("T1", -150.0, "2026-09-29T15:00:00+05:30", created="2026-09-29T04:00:00+00:00")
    t2 = _closed("T2", 200.0, "2026-09-29T10:00:00+00:00", created="2026-09-29T04:30:00+00:00")
    db = _kill_db(t1, t2)
    dep = {"id": "D1", "mode": "paper", "risk": {"daily_caps": {"loss": 100}}}
    out = run(check_soft_daily_governor(db, dep, today_ist="2026-09-29"))
    assert out["halt"] is True


def test_forward_metrics_closed_trades_come_back_in_instant_order():
    import app.forward_metrics as fm
    db = _kill_db(_closed("Z", -10.0, "2026-09-28T08:00:00+00:00"),
                  _closed("X", 50.0, IST_1500),
                  _closed("Y", -20.0, UTC_1000))
    rows = run(fm._closed_trades(db, "D1"))
    assert [r["id"] for r in rows] == ["Z", "X", "Y"]


def _overview_db(paper_rows):
    db = FakeDB()
    db.strategy_deployments.rows.append(
        {"id": "P1", "name": "paper one", "mode": "paper", "status": "ACTIVE",
         "created_at": "2026-09-01T00:00:00+00:00"})
    db.paper_trades.rows.extend(paper_rows)
    return db


def test_the_overview_realized_today_compares_close_instants_across_both_formats(monkeypatch):
    import app.routers.deployments as dep
    now_ist = datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)
    today = now_ist.date()
    yday = today - timedelta(days=1)

    def row(tid, pnl, closed_at, **kw):
        return {"id": tid, "deployment_id": "P1", "status": "CLOSED",
                "realized_pnl": pnl, "closed_at": closed_at, **kw}

    db = _overview_db([
        row("A", 100.0, f"{today}T09:30:00+05:30"),              # today, IST-stamped
        row("B", 1000.0, f"{yday}T23:59:00+05:30"),              # YESTERDAY 23:59 IST — the old string
        #                                                          compare vs the UTC day start counted it
        row("C", 10.0, f"{yday}T19:00:00+00:00"),                # 00:30 IST today, UTC-stamped
        row("D", 5000.0, f"{today}T20:00:00+00:00"),             # 01:30 IST TOMORROW
        row("E", -30.0, f"{today}T05:00:00+00:00", exit_day_unknown=True),
        row("F", 7777.0, "garbage"),
        {"id": "G", "deployment_id": "P1", "status": "OPEN", "realized_pnl": 9999.0,
         "closed_at": f"{today}T05:00:00+00:00"},
    ])
    monkeypatch.setattr(dep, "get_db", lambda: db)
    out = run(dep.deployments_overview())
    item = out["items"][0]
    assert item["today"]["realized_pnl"] == 110.0
    assert out["totals"]["realized_today"] == 110.0
