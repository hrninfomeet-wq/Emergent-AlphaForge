"""The command-centre "today" block's OPEN-position accounting.

Two things the card used to get wrong (Task: misleading status indicators, 2026-09-29):

  * "Open trades" / "Open MTM" counted every OPEN row of the current-mode collection,
    whatever day it was entered — a paper trade stranded from an earlier day inflated
    both with a frozen number;
  * a live deployment demoted to paper (pause / kill switch) stopped reading
    `live_trades`, so a still-OPEN REAL-MONEY row vanished from the card.

`overview_open` decides the accounting purely; the route test runs the real
`deployments_overview` against in-memory collections with a frozen clock.
"""
from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from tests.test_signal_exit_lifecycle import FakeDB  # noqa: E402

import app.routers.deployments as dep  # noqa: E402
from app.overview_open import (  # noqa: E402
    entry_day_ist, is_carried, summarize_open_rows, summarize_other_book)

# Frozen "now": Tue 2026-09-29 11:30 IST (06:00Z).
NOW = datetime(2026, 9, 29, 6, 0, tzinfo=timezone.utc)
TODAY = "2026-09-29"
CUT = NOW - timedelta(seconds=120)
FRESH = (NOW - timedelta(seconds=10)).isoformat()
STALE = (NOW - timedelta(minutes=30)).isoformat()
TODAY_ENTRY = "2026-09-29T04:00:00+00:00"          # 09:30 IST today
OLD_ENTRY = "2026-09-25T04:00:00+00:00"            # Fri 25 Sep
OLDER_ENTRY = "2026-09-16T04:00:00+00:00"          # Wed 16 Sep


def _sum(rows, book):
    return summarize_open_rows(rows, book=book, today_ist=TODAY, fresh_cut=CUT)


# --------------------------------------------------------------------------- #
# the pure accounting
# --------------------------------------------------------------------------- #

def test_entry_day_is_the_IST_day_and_carried_means_an_earlier_one():
    assert entry_day_ist({"created_at": "2026-09-28T20:00:00+00:00"}) == "2026-09-29"   # 01:30 IST
    assert not is_carried({"created_at": "2026-09-28T20:00:00+00:00"}, TODAY)
    assert is_carried({"created_at": "2026-09-28T17:00:00+00:00"}, TODAY)               # 22:30 IST 28th
    assert entry_day_ist({"created_at": "junk"}) is None and entry_day_ist({}) is None


def test_an_unreadable_entry_day_is_never_called_carried_and_is_not_hidden():
    r = _sum([{"created_at": "junk", "unrealized_pnl": -25.0},
              {"unrealized_pnl": 5.0}], "paper")
    assert r["open_carried"] == 0 and r["open_carried_oldest"] is None
    assert r["open_trades"] == 2 and r["open_unrealized"] == -20.0


def test_a_future_entry_day_is_not_carried():
    assert not is_carried({"created_at": "2026-10-01T04:00:00+00:00"}, TODAY)


def test_live_needs_a_fresh_guard_mark_whatever_day_it_was_entered():
    """The frozen unrealized of a stuck live row is excluded and counted unverified."""
    rows = [
        {"created_at": TODAY_ENTRY, "unrealized_pnl": -100.0, "marked_at": FRESH},
        {"created_at": TODAY_ENTRY, "unrealized_pnl": -853.0, "marked_at": STALE},
        {"created_at": TODAY_ENTRY, "unrealized_pnl": -50.0},                      # never marked
        {"created_at": OLDER_ENTRY, "unrealized_pnl": -7000.0, "marked_at": STALE},   # stuck since 09-16
    ]
    r = _sum(rows, "live")
    assert r["open_trades"] == 4
    assert r["open_unrealized"] == -100.0
    assert r["open_unverified"] == 3
    assert r["open_carried"] == 1 and r["open_carried_oldest"] == "2026-09-16"


def test_the_oldest_carried_entry_is_the_earliest_not_the_latest():
    r = _sum([
        {"created_at": OLD_ENTRY, "unrealized_pnl": 1.0, "marked_at": FRESH},
        {"created_at": OLDER_ENTRY, "unrealized_pnl": 1.0, "marked_at": FRESH},
        {"created_at": "2026-09-27T04:00:00+00:00", "unrealized_pnl": 1.0, "marked_at": FRESH},
    ], "live")
    assert r["open_carried"] == 3 and r["open_carried_oldest"] == "2026-09-16"


def test_a_carried_live_row_with_a_fresh_guard_mark_is_real_and_counted():
    """An overnight position the guard IS watching is real exposure: in MTM, and
    still labelled carried."""
    r = _sum([{"created_at": OLD_ENTRY, "unrealized_pnl": 300.0, "marked_at": FRESH}], "live")
    assert r["open_unrealized"] == 300.0 and r["open_unverified"] == 0
    assert r["open_carried"] == 1


def test_a_paper_row_entered_today_keeps_the_old_behaviour():
    r = _sum([{"created_at": TODAY_ENTRY, "unrealized_pnl": -25.0}], "paper")
    assert r == {"open_trades": 1, "open_unrealized": -25.0, "open_unverified": 0,
                 "open_carried": 0, "open_carried_oldest": None}


def test_a_stranded_paper_row_from_an_earlier_day_is_not_todays_mtm():
    """THE defect: a paper trade from Friday, frozen at its last mark, inflated
    "Open trades" and "Today MTM"."""
    rows = [
        {"created_at": TODAY_ENTRY, "unrealized_pnl": -25.0},
        {"created_at": OLD_ENTRY, "unrealized_pnl": -900.0, "updated_at": STALE},
    ]
    r = _sum(rows, "paper")
    assert r["open_trades"] == 2                       # still visible, still open
    assert r["open_unrealized"] == -25.0               # the frozen -900 is not MTM
    assert r["open_unverified"] == 1
    assert r["open_carried"] == 1 and r["open_carried_oldest"] == "2026-09-25"


def test_a_carried_paper_row_the_marker_is_still_stamping_is_counted():
    r = _sum([{"created_at": OLD_ENTRY, "unrealized_pnl": 40.0, "updated_at": FRESH}], "paper")
    assert r["open_unrealized"] == 40.0 and r["open_unverified"] == 0 and r["open_carried"] == 1


def test_a_carried_paper_row_with_no_stamp_is_unverified():
    r = _sum([{"created_at": OLD_ENTRY, "unrealized_pnl": 40.0}], "paper")
    assert r["open_unrealized"] == 0.0 and r["open_unverified"] == 1


def test_non_finite_and_missing_unrealized_count_as_zero_not_nan():
    r = _sum([{"created_at": TODAY_ENTRY, "unrealized_pnl": float("nan")},
              {"created_at": TODAY_ENTRY, "unrealized_pnl": None},
              {"created_at": TODAY_ENTRY, "unrealized_pnl": "x"},
              {"created_at": TODAY_ENTRY, "unrealized_pnl": 12.5}], "paper")
    assert r["open_unrealized"] == 12.5


def test_no_rows_is_all_zeros():
    assert _sum([], "live") == {"open_trades": 0, "open_unrealized": 0.0,
                                "open_unverified": 0, "open_carried": 0,
                                "open_carried_oldest": None}


def test_other_book_is_none_when_there_is_nothing_in_it():
    assert summarize_other_book([], book="live", today_ist=TODAY) is None
    assert summarize_other_book([], book="live", today_ist=TODAY, realized_today=0.0) is None


def test_other_book_reports_open_rows_oldest_entry_and_realized():
    ob = summarize_other_book(
        [{"created_at": OLDER_ENTRY}, {"created_at": TODAY_ENTRY}, {"created_at": "junk"}],
        book="live", today_ist=TODAY, realized_today=-300.0)
    assert ob == {"book": "live", "open_trades": 3, "open_carried": 1,
                  "oldest_open_entry_ist": "2026-09-16", "realized_today": -300.0}


def test_other_book_with_only_a_realized_figure_is_still_reported():
    ob = summarize_other_book([], book="live", today_ist=TODAY, realized_today=-300.0)
    assert ob["open_trades"] == 0 and ob["realized_today"] == -300.0
    assert ob["oldest_open_entry_ist"] is None


# --------------------------------------------------------------------------- #
# the route, against in-memory collections, clock frozen
# --------------------------------------------------------------------------- #

def _freeze(monkeypatch):
    class _DT(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW if tz is None else NOW.astimezone(tz)

    monkeypatch.setattr(dep, "datetime", _DT)


def _dep(dep_id, mode, name=None, status="ACTIVE"):
    return {"id": dep_id, "name": name or dep_id, "mode": mode, "status": status,
            "created_at": "2026-09-01T00:00:00+00:00"}


def _overview(monkeypatch, deployments, *, paper=(), live=()):
    _freeze(monkeypatch)
    db = FakeDB()
    db.strategy_deployments.rows.extend(deployments)
    db.paper_trades.rows.extend(paper)
    db.live_trades.rows.extend(live)
    monkeypatch.setattr(dep, "get_db", lambda: db)
    out = asyncio.run(dep.deployments_overview())
    return out, {i["deployment"]["id"]: i["today"] for i in out["items"]}


def _open(dep_id, created, pnl, **kw):
    return {"deployment_id": dep_id, "status": "OPEN", "created_at": created,
            "unrealized_pnl": pnl, **kw}


def test_route_a_stranded_paper_row_no_longer_inflates_todays_open_mtm(monkeypatch):
    out, by = _overview(monkeypatch, [_dep("P1", "paper")], paper=[
        _open("P1", TODAY_ENTRY, -25.0),
        _open("P1", OLD_ENTRY, -900.0, updated_at=STALE),
    ])
    t = by["P1"]
    assert t["open_trades"] == 2 and t["open_unrealized"] == -25.0
    assert t["open_unverified"] == 1
    assert t["open_carried"] == 1 and t["open_carried_oldest"] == "2026-09-25"
    assert t["other_book"] is None


def test_route_live_rows_keep_the_verified_mtm_rule_and_report_carried(monkeypatch):
    """Same expectations as tests/test_overview_pipeline_mongo.py (which skips
    without Mongo) — now enforced by an executed test."""
    out, by = _overview(monkeypatch, [_dep("L1", "live")], live=[
        _open("L1", TODAY_ENTRY, -100.0, marked_at=FRESH),
        _open("L1", TODAY_ENTRY, -853.0, marked_at=STALE),
        _open("L1", TODAY_ENTRY, -50.0),
        _open("L1", OLDER_ENTRY, -7000.0, marked_at=STALE),
        {"deployment_id": "L1", "status": "CLOSED", "realized_pnl": 500.0,
         "closed_at": "2026-09-29T05:00:00+00:00"},
        {"deployment_id": "L1", "status": "CLOSED", "realized_pnl": -9999.0,
         "closed_at": "2026-09-29T05:00:00+00:00", "exit_day_unknown": True},
    ])
    t = by["L1"]
    assert t["open_trades"] == 4 and t["open_unrealized"] == -100.0
    assert t["open_unverified"] == 3
    assert t["open_carried"] == 1 and t["open_carried_oldest"] == "2026-09-16"
    assert t["realized_pnl"] == 500.0


def test_route_a_demoted_deployments_live_rows_no_longer_vanish(monkeypatch):
    """THE second defect: paused / killed -> mode paper -> only paper_trades was read.
    Its OPEN real-money row (and today's live loss) disappeared from the card."""
    out, by = _overview(monkeypatch, [_dep("D1", "paper", status="PAUSED")], live=[
        _open("D1", OLDER_ENTRY, -4200.0, marked_at=STALE),
        {"deployment_id": "D1", "status": "CLOSED", "realized_pnl": -300.0,
         "closed_at": "2026-09-29T05:00:00+00:00"},
    ])
    t = by["D1"]
    assert t["other_book"] == {"book": "live", "open_trades": 1, "open_carried": 1,
                               "oldest_open_entry_ist": "2026-09-16", "realized_today": -300.0}
    # Never added to the primary (paper) figures — paper and real money are not summed.
    assert t["open_trades"] == 0 and t["open_unrealized"] == 0.0 and t["realized_pnl"] == 0.0
    assert out["totals"]["other_book_live_open"] == 1
    assert out["totals"]["other_book_live_realized_today"] == -300.0
    assert out["totals"]["open_trades"] == 0 and out["totals"]["realized_today"] == 0.0


def test_route_no_double_counting_across_deployments_and_books(monkeypatch):
    out, by = _overview(
        monkeypatch,
        [_dep("P1", "paper"), _dep("L1", "live"), _dep("D1", "paper", status="PAUSED")],
        paper=[_open("P1", TODAY_ENTRY, -25.0)],
        live=[_open("L1", TODAY_ENTRY, -100.0, marked_at=FRESH),
              _open("D1", OLDER_ENTRY, -4200.0, marked_at=STALE)])
    assert by["P1"]["open_trades"] == 1 and by["L1"]["open_trades"] == 1
    assert by["D1"]["open_trades"] == 0
    tot = out["totals"]
    assert tot["open_trades"] == 2                       # P1 + L1 only
    assert tot["open_unrealized"] == -125.0
    assert tot["other_book_live_open"] == 1              # D1's live row, counted once, apart
    assert tot["open_carried"] == 0


def test_route_a_stray_paper_row_on_a_live_deployment_is_reported_but_not_its_pnl(monkeypatch):
    out, by = _overview(monkeypatch, [_dep("L1", "live")], paper=[
        _open("L1", OLD_ENTRY, -10.0),
        {"deployment_id": "L1", "status": "CLOSED", "realized_pnl": 999.0,
         "closed_at": "2026-09-29T05:00:00+00:00"},
    ], live=[
        {"deployment_id": "L1", "status": "CLOSED", "realized_pnl": 500.0,
         "closed_at": "2026-09-29T05:00:00+00:00"},
    ])
    ob = by["L1"]["other_book"]
    # the paper book beside a LIVE deployment reports the stray OPEN row only: neither
    # its own 999 nor the live book's 500 is "paper realized today".
    assert ob["book"] == "paper" and ob["open_trades"] == 1 and ob["realized_today"] == 0.0
    assert by["L1"]["open_trades"] == 0 and by["L1"]["realized_pnl"] == 500.0
    assert out["totals"]["other_book_live_open"] == 0    # paper is not the real-money total


def test_route_closed_only_paper_book_beside_a_live_deployment_says_nothing(monkeypatch):
    out, by = _overview(monkeypatch, [_dep("L1", "live")], paper=[
        {"deployment_id": "L1", "status": "CLOSED", "realized_pnl": 999.0,
         "closed_at": "2026-09-29T05:00:00+00:00"}])
    assert by["L1"]["other_book"] is None


def test_route_realized_today_is_the_primary_books_only(monkeypatch):
    out, by = _overview(
        monkeypatch, [_dep("P1", "paper")],
        paper=[{"deployment_id": "P1", "status": "CLOSED", "realized_pnl": 110.0,
                "closed_at": "2026-09-29T05:00:00+00:00"}],
        live=[{"deployment_id": "P1", "status": "CLOSED", "realized_pnl": -50.0,
               "closed_at": "2026-09-29T05:00:00+00:00"}])
    assert by["P1"]["realized_pnl"] == 110.0
    assert by["P1"]["other_book"]["realized_today"] == -50.0
    assert out["totals"]["realized_today"] == 110.0


def test_route_with_no_deployments_does_not_query_or_crash(monkeypatch):
    out, by = _overview(monkeypatch, [])
    assert out["items"] == [] and out["totals"]["open_trades"] == 0
    assert out["totals"]["other_book_live_open"] == 0
