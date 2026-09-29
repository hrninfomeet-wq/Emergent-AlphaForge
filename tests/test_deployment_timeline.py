"""GET /deployments/{id}/timeline — the read-only session timeline.

Merges signals (+ refusals, intended entries, lifecycle transitions), live_trades
(entry / exit), live_orders and the deployment's latest hold/disable/caps events
into one ascending list for ONE IST calendar day, and states in ``gaps`` what is
not recorded anywhere. Called the way the sibling route tests do: the async handler
directly, over the shared FakeDB.
"""
from __future__ import annotations

import asyncio
import copy
import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import app.routers.deployments as dep  # noqa: E402
from app.live_timeline import (  # noqa: E402
    GAP_HALT_HISTORY, GAP_SKIPPED_ENTRIES, _parse_ts,
)
from tests.test_deployment_live_routes import FakeDB, _Collection, _deployment, _install  # noqa: E402

DAY = "2026-06-25"  # _install pins "now" to 06:00 UTC == 11:30 IST on this date
IST = timezone(timedelta(hours=5, minutes=30))


def _ms(iso: str) -> int:
    return int(datetime.fromisoformat(iso).timestamp() * 1000)


def _db(**dep_extra):
    db = FakeDB()
    db.live_orders = _Collection()
    db.strategy_deployments.rows.append(_deployment(mode="live", **dep_extra))
    return db


def _call(monkeypatch, db, date=DAY, dep_id="dep-1", **install_kw):
    _install(monkeypatch, db, **install_kw)
    return asyncio.run(dep.deployment_session_timeline(dep_id, date))


def _sig(candle_iso, dep_id="dep-1", **kw):
    s = {"id": f"s-{candle_iso}", "deployment_id": dep_id, "candle_ts": _ms(candle_iso),
         "direction": "CE", "instrument": "NIFTY", "entry_price": 23950.0,
         "blocked": False, "blockers": [], "state": "CONFIRMED",
         "created_at": candle_iso, "updated_at": candle_iso, "events": []}
    s.update(kw)
    return s


def _kinds(out):
    return [e["kind"] for e in out["events"]]


# --------------------------------------------------------------------------- #
# Day bounds: the IST calendar day, half-open
# --------------------------------------------------------------------------- #

def test_signals_are_bounded_to_the_ist_day_half_open(monkeypatch):
    db = _db()
    db.signals.rows += [
        _sig("2026-06-24T18:29:00+00:00"),            # 23:59 IST the day before -> out
        _sig("2026-06-24T18:30:00+00:00"),            # 00:00 IST exactly -> IN
        _sig("2026-06-25T18:29:00+00:00"),            # 23:59 IST -> IN
        _sig("2026-06-25T18:30:00+00:00"),            # 00:00 IST next day, exactly -> out
        _sig("2026-06-25T04:00:00+00:00", dep_id="other"),  # someone else's -> out
    ]
    out = _call(monkeypatch, db)
    assert [e["ts"] for e in out["events"]] == ["2026-06-24T18:30:00+00:00",
                                                "2026-06-25T18:29:00+00:00"]
    assert out["date"] == DAY


def test_the_default_date_is_today_in_IST_not_UTC(monkeypatch):
    """20:00 UTC on 06-25 is 01:30 IST on 06-26 — 'today' is the IST calendar date."""
    db = _db()
    db.signals.rows.append(_sig("2026-06-26T04:00:00+00:00"))   # IST 06-26 -> in
    db.signals.rows.append(_sig("2026-06-25T04:00:00+00:00"))   # IST 06-25 -> out
    _install(monkeypatch, db)
    monkeypatch.setattr(dep, "_utcnow",
                        lambda: datetime(2026, 6, 25, 20, 0, tzinfo=timezone.utc))
    out = asyncio.run(dep.deployment_session_timeline("dep-1", None))
    assert out["date"] == "2026-06-26"
    assert [e["ts"] for e in out["events"]] == ["2026-06-26T04:00:00+00:00"]


def test_the_route_is_registered_as_a_get():
    hits = [r for r in dep.api.routes
            if getattr(r, "path", "") == "/deployments/{deployment_id}/timeline"]
    assert hits and "GET" in hits[0].methods


# --------------------------------------------------------------------------- #
# Signals: blocked, refused, intended, lifecycle
# --------------------------------------------------------------------------- #

def test_a_blocked_signal_names_its_blockers(monkeypatch):
    db = _db()
    db.signals.rows.append(_sig("2026-06-25T04:00:00+00:00", blocked=True,
                                blockers=["entry_window (before 09:30)", "kill_switch"]))
    db.signals.rows.append(_sig("2026-06-25T04:05:00+00:00", direction="PE"))
    out = _call(monkeypatch, db)
    blocked, clean = out["events"]
    assert blocked["kind"] == "signal" and blocked["label"] == "CE signal — blocked"
    assert "entry_window (before 09:30)" in blocked["detail"]
    assert "kill_switch" in blocked["detail"]
    assert clean["label"] == "PE signal" and "blocked" not in clean["detail"]


def test_a_blocked_signal_with_no_recorded_blockers_says_so(monkeypatch):
    db = _db()
    db.signals.rows.append(_sig("2026-06-25T04:00:00+00:00", blocked=True, blockers=[]))
    out = _call(monkeypatch, db)
    assert out["events"][0]["detail"] == "blocked (no reason recorded)"


def test_a_refusal_intended_entry_and_transitions_each_get_an_event(monkeypatch):
    db = _db()
    db.signals.rows.append(_sig(
        "2026-06-25T04:00:00+00:00",
        live_trade_error="max_concurrent", updated_at="2026-06-25T04:00:02+00:00",
        live_intended={"would_send": False, "ref_ltp": 151.5, "lots": 2,
                       "at": "2026-06-25T04:00:01+00:00"},
        events=[
            {"from_state": None, "to_state": "WATCHING", "reason": "created",
             "at": "2026-06-25T04:00:00+00:00"},
            {"from_state": "FORMING", "to_state": "CONFIRMED", "reason": "clean",
             "at": "2026-06-25T04:00:00.500000+00:00"},
        ]))
    out = _call(monkeypatch, db)
    assert _kinds(out) == ["signal", "state", "intended", "refused"]
    state, intended, refused = out["events"][1:]
    assert state["label"] == "FORMING → CONFIRMED" and state["detail"] == "clean"
    assert "2 lots" in intended["detail"] and "ref 151.50" in intended["detail"]
    assert "dry-run" in intended["detail"]
    assert refused["label"] == "Entry refused" and refused["detail"] == "max_concurrent"
    assert refused["source"] == "signals.live_trade_error"
    # the creation transition is the signal event itself, not a duplicate
    assert "WATCHING" not in " ".join(e["label"] for e in out["events"])


def test_a_signal_with_no_refusal_or_intent_adds_neither(monkeypatch):
    db = _db()
    db.signals.rows.append(_sig("2026-06-25T04:00:00+00:00"))
    assert _kinds(_call(monkeypatch, db)) == ["signal"]


# --------------------------------------------------------------------------- #
# live_trades: entry and exit each on THEIR OWN IST day
# --------------------------------------------------------------------------- #

def _trade(**kw):
    t = {"id": "t1", "deployment_id": "dep-1", "noren_tsym": "NIFTY25JUN26C23950",
         "lots": 2, "entry_price": 150.0, "entry_fill_price": 151.25,
         "created_at": "2026-06-25T04:00:00+00:00", "status": "OPEN"}
    t.update(kw)
    return t


def test_entry_and_exit_are_placed_by_their_own_ist_day(monkeypatch):
    db = _db()
    db.live_trades.rows += [
        _trade(id="a", closed_at="2026-06-25T05:30:00+00:00", exit_reason="stop",
               realized_pnl=-1200.5, status="CLOSED"),
        # entered late yesterday IST (23:30), closed today: only the EXIT is today's
        _trade(id="b", created_at="2026-06-24T18:00:00+00:00",
               closed_at="2026-06-25T06:00:00+00:00", exit_reason="target",
               realized_pnl=800.0, status="CLOSED"),
        # entered today, closed tomorrow IST: only the ENTRY is today's
        _trade(id="c", created_at="2026-06-25T07:00:00+00:00",
               closed_at="2026-06-25T19:00:00+00:00", exit_reason="time_stop",
               realized_pnl=10.0, status="CLOSED"),
        _trade(id="d", deployment_id="other", created_at="2026-06-25T04:30:00+00:00"),
    ]
    out = _call(monkeypatch, db)
    got = [(e["kind"], e["ts"]) for e in out["events"]]
    assert got == [
        ("entry", "2026-06-25T04:00:00+00:00"),
        ("exit", "2026-06-25T05:30:00+00:00"),
        ("exit", "2026-06-25T06:00:00+00:00"),
        ("entry", "2026-06-25T07:00:00+00:00"),
    ]


def test_entry_detail_and_a_null_pnl_stays_a_dash(monkeypatch):
    db = _db()
    db.live_trades.rows.append(_trade(
        closed_at="2026-06-25T05:30:00+00:00", exit_reason="reconcile_closed",
        realized_pnl=None, realized_pnl_backfilled=True, exit_day_unknown=True,
        status="CLOSED"))
    entry, exit_ = _call(monkeypatch, db)["events"]
    assert entry["label"] == "Entry NIFTY25JUN26C23950"
    assert entry["detail"] == "2 lots · ref 150.00 · fill 151.25"
    assert exit_["kind"] == "exit"
    assert "P&L —" in exit_["detail"]                # null is never rendered as 0
    assert "+0.00" not in exit_["detail"]
    assert "backfilled" in exit_["detail"]
    assert "exit day unknown" in exit_["detail"]
    assert "reason reconcile_closed" in exit_["detail"]


def test_a_real_pnl_is_signed(monkeypatch):
    db = _db()
    db.live_trades.rows.append(_trade(closed_at="2026-06-25T05:30:00+00:00",
                                      exit_reason="stop", realized_pnl=-1200.5,
                                      status="CLOSED"))
    assert "P&L -1200.50" in _call(monkeypatch, db)["events"][1]["detail"]


def test_an_open_trade_has_no_exit_event(monkeypatch):
    db = _db()
    db.live_trades.rows.append(_trade())
    assert _kinds(_call(monkeypatch, db)) == ["entry"]


# --------------------------------------------------------------------------- #
# live_orders
# --------------------------------------------------------------------------- #

def test_orders_are_filtered_by_deployment_and_day(monkeypatch):
    db = _db()
    intent = {"trantype": "B", "tsym": "NIFTY25JUN26C23950", "qty": 150, "prc": 151.5}
    db.live_orders.rows += [
        {"client_order_id": "c1", "deployment_id": "dep-1", "intent": intent,
         "state": "SUBMITTED", "norenordno": "2606250001",
         "ts_intent": "2026-06-25T04:00:00+00:00",
         "ts_submitted": "2026-06-25T04:00:01+00:00"},
        {"client_order_id": "c2", "deployment_id": "other", "intent": intent,
         "state": "INTENT", "ts_intent": "2026-06-25T04:10:00+00:00"},
        {"client_order_id": "c3", "deployment_id": "dep-1",
         "intent": {**intent, "trantype": "S"}, "state": "INTENT",
         "ts_intent": "2026-06-24T04:10:00+00:00"},
    ]
    out = _call(monkeypatch, db)
    assert [(e["label"], e["ts"]) for e in out["events"]] == [
        ("BUY order intended", "2026-06-25T04:00:00+00:00"),
        ("BUY order sent to broker", "2026-06-25T04:00:01+00:00"),
    ]
    d = out["events"][0]["detail"]
    assert "qty 150" in d and "@ 151.50" in d and "now SUBMITTED" in d and "#2606250001" in d


# --------------------------------------------------------------------------- #
# Deployment point events
# --------------------------------------------------------------------------- #

def test_point_events_come_from_the_deployment_doc(monkeypatch):
    db = _db()
    db.strategy_deployments.rows[0]["risk"]["live"] = {
        "paused": True, "paused_at": "2026-06-25T06:00:00+00:00",
        "disabled_at": "2026-06-25T07:00:00+00:00", "last_block_reason": "daily_loss",
        "last_caps_change": {"at": "2026-06-25T05:00:00+00:00",
                             "from": {"lots": 3}, "to": {"lots": 2}},
    }
    out = _call(monkeypatch, db)
    assert [(e["kind"], e["ts"]) for e in out["events"]] == [
        ("caps", "2026-06-25T05:00:00+00:00"),
        ("hold", "2026-06-25T06:00:00+00:00"),
        ("disabled", "2026-06-25T07:00:00+00:00"),
    ]
    assert out["events"][0]["detail"] == "lots 3 → 2"
    assert out["events"][2]["detail"] == "reason daily_loss"


def test_point_events_from_another_day_or_a_resumed_hold_are_left_out(monkeypatch):
    db = _db()
    db.strategy_deployments.rows[0]["risk"]["live"] = {
        "paused": False, "paused_at": "2026-06-25T06:00:00+00:00",   # resumed: not held now
        "disabled_at": "2026-06-24T07:00:00+00:00",                  # yesterday
        "last_caps_change": {"at": "2026-06-26T05:00:00+00:00",      # tomorrow
                             "from": {"lots": 3}, "to": {"lots": 2}},
    }
    assert _call(monkeypatch, db)["events"] == []


# --------------------------------------------------------------------------- #
# Ordering across sources
# --------------------------------------------------------------------------- #

def test_events_from_every_source_merge_in_ascending_time(monkeypatch):
    db = _db()
    db.signals.rows.append(_sig("2026-06-25T04:00:00+00:00"))                       # 04:00
    db.live_orders.rows.append({"client_order_id": "c", "deployment_id": "dep-1",
                                "intent": {"trantype": "B"}, "state": "SUBMITTED",
                                "ts_intent": "2026-06-25T03:59:00+00:00"})           # 03:59
    db.live_trades.rows.append(_trade(created_at="2026-06-25T04:01:00+00:00"))       # 04:01
    db.strategy_deployments.rows[0]["risk"]["live"] = {
        "disabled_at": "2026-06-25T03:00:00+00:00"}                                   # 03:00
    out = _call(monkeypatch, db)
    assert [e["ts"] for e in out["events"]] == sorted(e["ts"] for e in out["events"])
    assert _kinds(out) == ["disabled", "order", "signal", "entry"]


def test_mixed_offsets_and_z_suffix_sort_by_the_instant(monkeypatch):
    db = _db()
    db.live_trades.rows += [
        _trade(id="a", created_at="2026-06-25T09:31:00+05:30"),   # 04:01Z
        _trade(id="b", created_at="2026-06-25T04:00:00Z"),         # 04:00Z
    ]
    out = _call(monkeypatch, db)
    assert [e["ts"] for e in out["events"]] == ["2026-06-25T04:00:00+00:00",
                                                "2026-06-25T04:01:00+00:00"]


def test_every_ts_is_iso_utc():
    assert _parse_ts("2026-06-25T09:30:00+05:30").isoformat() == "2026-06-25T04:00:00+00:00"
    assert _parse_ts("2026-06-25T04:00:00").isoformat() == "2026-06-25T04:00:00+00:00"   # naive = UTC
    assert _parse_ts(_ms("2026-06-25T04:00:00+00:00")).isoformat() == "2026-06-25T04:00:00+00:00"
    assert _parse_ts("garbage") is None and _parse_ts(None) is None and _parse_ts(True) is None


# --------------------------------------------------------------------------- #
# Gaps: what is not recorded anywhere
# --------------------------------------------------------------------------- #

def test_gaps_always_state_what_is_not_recorded(monkeypatch):
    """Even on an EMPTY day: silence must never read as 'nothing was going on'."""
    out = _call(monkeypatch, _db())
    assert out["events"] == []
    assert GAP_SKIPPED_ENTRIES in out["gaps"] and GAP_HALT_HISTORY in out["gaps"]
    joined = " ".join(out["gaps"])
    assert "already held" in joined and "entry cutoff" in joined and "broker not connected" in joined
    assert "latch" in joined and "halt" in joined


def test_a_refusal_time_caveat_appears_only_when_there_is_a_refusal(monkeypatch):
    db = _db()
    db.signals.rows.append(_sig("2026-06-25T04:00:00+00:00"))
    assert not any("refusal's time" in g for g in _call(monkeypatch, db)["gaps"])
    db2 = _db()
    db2.signals.rows.append(_sig("2026-06-25T04:00:00+00:00", live_trade_error="no_option_contract"))
    assert any("refusal's time" in g for g in _call(monkeypatch, db2)["gaps"])


# --------------------------------------------------------------------------- #
# Never raises
# --------------------------------------------------------------------------- #

class _Boom:
    def find(self, *a, **k):
        raise RuntimeError("mongo is down")


def test_an_unreadable_source_degrades_into_a_gap_and_the_rest_still_renders(monkeypatch):
    db = _db()
    db.signals = _Boom()
    db.live_trades.rows.append(_trade())
    out = _call(monkeypatch, db)
    assert _kinds(out) == ["entry"]                      # trades survived the signals failure
    assert any("signals could not be read" in g for g in out["gaps"])


def test_a_db_with_no_live_orders_collection_is_a_gap_not_an_error(monkeypatch):
    db = FakeDB()                                        # no live_orders attribute at all
    db.strategy_deployments.rows.append(_deployment(mode="live"))
    db.live_trades.rows.append(_trade())
    out = _call(monkeypatch, db)
    assert _kinds(out) == ["entry"]
    assert any("live_orders could not be read" in g for g in out["gaps"])


def test_an_unknown_deployment_answers_200_with_a_gap(monkeypatch):
    out = _call(monkeypatch, _db(), dep_id="nope")
    assert out["events"] == []
    assert any("deployment document could not be read" in g for g in out["gaps"])


def test_a_bad_date_answers_with_an_explanation_not_an_exception(monkeypatch):
    out = _call(monkeypatch, _db(), date="25-06-2026")
    assert out["events"] == [] and out["date"] == "25-06-2026"
    assert "YYYY-MM-DD" in out["gaps"][0]
    assert GAP_SKIPPED_ENTRIES in out["gaps"]


def test_a_row_with_an_unreadable_timestamp_is_counted_not_guessed(monkeypatch):
    db = _db()
    db.live_trades.rows += [_trade(id="a", created_at="not-a-time"), _trade(id="b")]
    out = _call(monkeypatch, db)
    assert _kinds(out) == ["entry"]
    assert any("1 event(s) had no readable timestamp" in g for g in out["gaps"])


def test_a_failure_while_building_still_answers(monkeypatch):
    _install(monkeypatch, _db())

    def _boom(*a, **k):
        raise RuntimeError("no db")
    monkeypatch.setattr(dep, "get_db", _boom)
    out = asyncio.run(dep.deployment_session_timeline("dep-1", DAY))
    assert out["events"] == [] and "could not be built" in out["gaps"][0]


# --------------------------------------------------------------------------- #
# Read-only
# --------------------------------------------------------------------------- #

def test_the_timeline_writes_nothing(monkeypatch):
    db = _db()
    db.signals.rows.append(_sig("2026-06-25T04:00:00+00:00", live_trade_error="x"))
    db.live_trades.rows.append(_trade(closed_at="2026-06-25T05:30:00+00:00"))
    db.strategy_deployments.rows[0]["risk"]["live"] = {"paused": True,
                                                       "paused_at": "2026-06-25T06:00:00+00:00"}
    snap = {n: copy.deepcopy(getattr(db, n).rows)
            for n in ("signals", "live_trades", "live_orders", "strategy_deployments")}
    _call(monkeypatch, db)
    assert {n: getattr(db, n).rows for n in snap} == snap
