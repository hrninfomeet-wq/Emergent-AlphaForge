"""Phase 4 controls — tighten-only live caps, flatten-without-demote, and the pause
race they exposed.

Doctrine (routers/deployments._set_live_paused): the RESTRICTIVE direction must
always land; the PERMISSIVE one must not race. Tightening caps is restrictive, so
it needs no re-consent; loosening keeps the full Disable → re-Enable ceremony.
"""
from __future__ import annotations

import asyncio
import copy
import os
import sys

import pytest
from fastapi import HTTPException

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import app.routers.deployments as dep  # noqa: E402
from app.live.live_position_guard import LiveMonitorRegistry  # noqa: E402
from app.live.live_sl_monitor import build_monitor_state  # noqa: E402
from tests.test_deployment_live_routes import FakeDB, _deployment, _install  # noqa: E402

CAPS = {"lots": 3, "max_lots_per_day": 12, "max_concurrent": 2, "daily_loss_cap": 3000.0,
        "catastrophe_stop_pct": 60.0, "evidence_consent": {"accepted": True}}


def _live_db(**live_over):
    db = FakeDB()
    d = _deployment(mode="live")
    d["risk"]["live"] = {**copy.deepcopy(CAPS), **live_over}
    db.strategy_deployments.rows.append(d)
    return db


def _caps(**kw):
    return asyncio.run(dep.tighten_deployment_live_caps("dep-1", dep._LiveCapsBody(**kw)))


def _err(fn):
    with pytest.raises(HTTPException) as ei:
        fn()
    return ei.value


# --------------------------------------------------------------------------- #
# Tighten-only caps
# --------------------------------------------------------------------------- #

def test_each_cap_can_be_lowered_and_nothing_else_moves(monkeypatch):
    db = _live_db(paused=True)
    _install(monkeypatch, db)
    out = _caps(lots=2, max_lots_per_day=6, max_concurrent=1, daily_loss_cap=1500.0)
    row = db.strategy_deployments.rows[0]
    live = row["risk"]["live"]
    assert (live["lots"], live["max_lots_per_day"], live["max_concurrent"],
            live["daily_loss_cap"]) == (2, 6, 1, 1500.0)
    # what the tighten must NOT touch
    assert live["paused"] is True
    assert live["catastrophe_stop_pct"] == 60.0
    assert live["evidence_consent"] == {"accepted": True}
    assert row["mode"] == "live" and row["status"] == "ACTIVE"
    assert set(out["changed"]) == {"lots", "max_lots_per_day", "max_concurrent",
                                   "daily_loss_cap"}


@pytest.mark.parametrize("kw", [
    {"lots": 4}, {"max_lots_per_day": 13}, {"max_concurrent": 3},
    {"daily_loss_cap": 3000.5},
    {"lots": 1, "max_concurrent": 3},          # mixed: tightens one, loosens another
])
def test_any_loosening_is_refused_whole_and_nothing_is_written(monkeypatch, kw):
    db = _live_db()
    _install(monkeypatch, db)
    before = copy.deepcopy(db.strategy_deployments.rows[0])
    e = _err(lambda: _caps(**kw))
    assert e.status_code == 409 and e.detail["code"] == "caps_loosening_refused"
    assert db.strategy_deployments.rows[0] == before


def test_equal_values_write_nothing(monkeypatch):
    db = _live_db()
    _install(monkeypatch, db)
    before = copy.deepcopy(db.strategy_deployments.rows[0])
    out = _caps(lots=3, daily_loss_cap=3000.0)
    assert out["changed"] == []
    assert db.strategy_deployments.rows[0] == before


def test_a_missing_stored_lots_counts_as_one(monkeypatch):
    """resolve_capped_lots trades 1 lot when lots is unset — raising it is a loosen."""
    db = _live_db()
    db.strategy_deployments.rows[0]["risk"]["live"].pop("lots")
    _install(monkeypatch, db)
    assert _err(lambda: _caps(lots=3)).detail["code"] == "caps_loosening_refused"


@pytest.mark.parametrize("kw", [{"lots": 0}, {"max_concurrent": -1},
                                {"daily_loss_cap": float("nan")},
                                {"daily_loss_cap": -5.0}])
def test_invalid_values_get_enables_own_400(monkeypatch, kw):
    db = _live_db()
    _install(monkeypatch, db)
    assert _err(lambda: _caps(**kw)).status_code == 400


def test_both_routes_share_one_validator(monkeypatch):
    calls = []
    real = dep._validate_live_cap_values

    def spy(*a):
        calls.append(a)
        return real(*a)
    monkeypatch.setattr(dep, "_validate_live_cap_values", spy)
    db = _live_db()
    _install(monkeypatch, db)
    _caps(lots=2)
    assert calls, "the caps route did not run the shared validator"


def test_an_account_ceiling_below_the_new_value_is_refused(monkeypatch):
    db = _live_db(max_concurrent=5)
    _install(monkeypatch, db, account_max_open=2)
    e = _err(lambda: _caps(max_concurrent=4))
    assert e.detail["code"] == "account_position_ceiling_exceeded"


def test_a_concurrent_stop_beats_a_late_caps_write(monkeypatch):
    """Stop lands between the read and the write: the CAS misses, Stop's state
    stands, and the caps are NOT written."""
    db = _live_db()
    _install(monkeypatch, db)
    real = db.strategy_deployments.find_one
    fired = {"n": 0}

    async def racing(q, proj=None, **kw):
        row = await real(q, proj, **kw)
        if fired["n"] == 0:
            fired["n"] += 1
            r = db.strategy_deployments.rows[0]
            r.update({"status": "PAUSED", "mode": "paper",
                      "updated_at": "2026-06-25T09:59:59+00:00"})
        return row
    db.strategy_deployments.find_one = racing
    e = _err(lambda: _caps(lots=2))
    assert e.detail["code"] == "deployment_changed_during_caps_update"
    row = db.strategy_deployments.rows[0]
    assert row["status"] == "PAUSED" and row["mode"] == "paper"
    assert row["risk"]["live"]["lots"] == 3


def test_a_concurrent_tighten_is_not_loosened_by_a_stale_one(monkeypatch):
    """Another tab tightens lots to 1 between our read and our write, leaving
    mode/status alone. Our stale request for 2 would LOOSEN it — only the
    updated_at compare-and-swap can see that."""
    db = _live_db()
    _install(monkeypatch, db)
    real = db.strategy_deployments.find_one
    fired = {"n": 0}

    async def racing(q, proj=None, **kw):
        row = copy.deepcopy(await real(q, proj, **kw))
        if fired["n"] == 0:
            fired["n"] += 1
            r = db.strategy_deployments.rows[0]
            r["risk"]["live"]["lots"] = 1
            r["updated_at"] = "2026-06-25T09:59:59+00:00"
        return row
    db.strategy_deployments.find_one = racing
    e = _err(lambda: _caps(lots=2))
    assert e.detail["code"] == "deployment_changed_during_caps_update"
    assert db.strategy_deployments.rows[0]["risk"]["live"]["lots"] == 1


def test_a_mode_flip_without_a_timestamp_bump_still_misses(monkeypatch):
    """Some writers change mode without touching updated_at — mode/status are in
    the filter too."""
    db = _live_db()
    _install(monkeypatch, db)
    real = db.strategy_deployments.find_one

    async def racing(q, proj=None, **kw):
        row = await real(q, proj, **kw)
        db.strategy_deployments.rows[0]["mode"] = "paper"
        return row
    db.strategy_deployments.find_one = racing
    assert _err(lambda: _caps(lots=2)).detail["code"] == "deployment_changed_during_caps_update"


@pytest.mark.parametrize("setup,code", [
    (lambda d: d.update({"mode": "paper"}), "deployment_not_live"),
    (lambda d: d["risk"]["live"].update({"daily_loss_cap": float("nan")}), "stored_caps_invalid"),
])
def test_not_live_or_invalid_stored_caps_are_refused(monkeypatch, setup, code):
    db = _live_db()
    setup(db.strategy_deployments.rows[0])
    _install(monkeypatch, db)
    assert _err(lambda: _caps(lots=2)).detail["code"] == code


def test_tightening_works_with_the_broker_down(monkeypatch):
    db = _live_db()
    _install(monkeypatch, db, connected=False)
    assert _caps(lots=2)["changed"] == ["lots"]


def test_unknown_fields_are_rejected():
    with pytest.raises(Exception):
        dep._LiveCapsBody(lots=2, mode="paper")


# --------------------------------------------------------------------------- #
# The pause race the caps route exposed
# --------------------------------------------------------------------------- #

def test_a_pause_never_reverts_a_concurrent_tighten(monkeypatch):
    """Pause used to $set the whole `risk` it had read. A tighten landing between
    that read and write was silently undone on a deployment that stayed live."""
    db = _live_db()
    _install(monkeypatch, db)
    real = db.strategy_deployments.find_one
    fired = {"n": 0}

    async def racing(q, proj=None, **kw):
        # A real read is a SNAPSHOT: deep-copy it, or the "race" below would
        # mutate the very object pause already holds and the revert could not
        # happen (the first version of this test passed against the bug).
        row = copy.deepcopy(await real(q, proj, **kw))
        if fired["n"] == 0:          # the tighten lands inside pause's window
            fired["n"] += 1
            live = db.strategy_deployments.rows[0]["risk"]["live"]
            live["lots"] = 1
            live["daily_loss_cap"] = 1000.0
        return row
    db.strategy_deployments.find_one = racing
    asyncio.run(dep.pause_deployment_live("dep-1"))
    live = db.strategy_deployments.rows[0]["risk"]["live"]
    assert live["paused"] is True
    assert live["lots"] == 1 and live["daily_loss_cap"] == 1000.0


# --------------------------------------------------------------------------- #
# Flatten without demote
# --------------------------------------------------------------------------- #

def _open_market(monkeypatch, is_open=True):
    monkeypatch.setattr(dep, "market_status",
                        lambda now: {"is_open": is_open, "phase": "open" if is_open else "closed"})


def _reg(*entries):
    reg = LiveMonitorRegistry()
    for key, tsym, dep_id in entries:
        reg.register(key=key, tsym=tsym, exch="NFO", qty=65, prd="I", entry_price=100.0,
                     state=build_monitor_state(100.0, stop_pct=50), deployment_id=dep_id)
    return reg


def _flatten(hold=False):
    return asyncio.run(dep.flatten_deployment_live("dep-1", dep._LiveFlattenBody(hold=hold)))


def test_flatten_squares_only_this_deployment_and_never_demotes(monkeypatch):
    db = _live_db()
    reg = _reg(("o1", "NIFTY25000CE", "dep-1"), ("o2", "OTHER24000PE", "dep-2"))
    _reg_out, squared = _install(monkeypatch, db, registry=reg)
    _open_market(monkeypatch)
    before = copy.deepcopy(db.strategy_deployments.rows[0])
    out = _flatten()
    assert squared == ["NIFTY25000CE"]
    assert db.strategy_deployments.rows[0] == before, "flatten wrote to the deployment"
    assert out["mode"] == "live" and out["status"] == "ACTIVE"
    assert out["fill_confirmed"] is False and out["complete"] is True


def test_flatten_refuses_when_the_market_is_closed_and_sends_nothing(monkeypatch):
    db = _live_db()
    reg = _reg(("o1", "NIFTY25000CE", "dep-1"))
    _r, squared = _install(monkeypatch, db, registry=reg)
    _open_market(monkeypatch, is_open=False)
    e = _err(_flatten)
    assert e.status_code == 409 and e.detail["code"] == "market_closed"
    assert squared == []


def test_a_failed_square_is_reported_not_counted_as_done(monkeypatch):
    db = _live_db()
    reg = _reg(("o1", "NIFTY25000CE", "dep-1"))
    _install(monkeypatch, db, registry=reg)
    _open_market(monkeypatch)

    async def _fail(client, position, *, reason, **kw):
        return {"squared": False, "failures": ["rejected"]}
    monkeypatch.setattr(dep, "_live_square_position", _fail)
    out = _flatten()
    assert out["failed_tsyms"] == ["NIFTY25000CE"] and out["complete"] is False
    assert reg.get("o1") is not None and not reg.get("o1").get("squaring")


def test_a_shared_contract_is_skipped_not_squared(monkeypatch):
    """Squaring clamps to the ACCOUNT's netqty on the scrip — it would flatten the
    other deployment's position on the same contract too."""
    db = _live_db()
    reg = _reg(("o1", "NIFTY25000CE", "dep-1"), ("o2", "NIFTY25000CE", "dep-2"))
    _r, squared = _install(monkeypatch, db, registry=reg)
    _open_market(monkeypatch)
    out = _flatten()
    assert squared == []
    assert out["skipped_shared_tsyms"] == ["NIFTY25000CE"] and out["complete"] is False


def test_an_open_journal_row_the_guard_does_not_hold_is_named(monkeypatch):
    """After a restart a rehydrated entry has no deployment_id — the flatten would
    silently find nothing. The journal says otherwise; say so."""
    db = _live_db()
    db.live_trades.rows.append({"deployment_id": "dep-1", "status": "OPEN",
                                "noren_tsym": "NIFTY25100CE", "norenordno": "N9"})
    _install(monkeypatch, db, registry=_reg())
    _open_market(monkeypatch)
    out = _flatten()
    assert out["unguarded_open_tsyms"] == ["NIFTY25100CE"] and out["complete"] is False


def test_an_entry_already_squaring_is_not_resent(monkeypatch):
    db = _live_db()
    reg = _reg(("o1", "NIFTY25000CE", "dep-1"))
    reg.get("o1")["squaring"] = True
    _r, squared = _install(monkeypatch, db, registry=reg)
    _open_market(monkeypatch)
    out = _flatten()
    assert squared == [] and out["already_squaring_tsyms"] == ["NIFTY25000CE"]


def test_flatten_and_hold_holds_BEFORE_squaring_and_keeps_live(monkeypatch):
    """The next signal must not re-enter while the exit is still working."""
    db = _live_db()
    reg = _reg(("o1", "NIFTY25000CE", "dep-1"))
    _install(monkeypatch, db, registry=reg)
    _open_market(monkeypatch)
    seen = []

    async def _sq(client, position, *, reason, **kw):
        seen.append(db.strategy_deployments.rows[0]["risk"]["live"].get("paused"))
        return {"squared": True, "tsym": position.get("tsym")}
    monkeypatch.setattr(dep, "_live_square_position", _sq)
    out = _flatten(hold=True)
    assert seen == [True], "the hold landed after the square"
    row = db.strategy_deployments.rows[0]
    assert row["mode"] == "live" and row["status"] == "ACTIVE" and out["held"] is True


def test_nothing_to_flatten_is_complete_and_writes_nothing(monkeypatch):
    db = _live_db()
    _install(monkeypatch, db, registry=_reg())
    _open_market(monkeypatch)
    before = copy.deepcopy(db.strategy_deployments.rows[0])
    out = _flatten()
    assert out["complete"] is True and out["exit_submitted_tsyms"] == []
    assert db.strategy_deployments.rows[0] == before


def test_live_stop_is_unchanged_by_the_new_options(monkeypatch):
    """/live/stop still squares a shared contract (its semantics are 'stop it all')."""
    db = _live_db()
    reg = _reg(("o1", "NIFTY25000CE", "dep-1"), ("o2", "NIFTY25000CE", "dep-2"))
    _r, squared = _install(monkeypatch, db, registry=reg)
    asyncio.run(dep.stop_deployment_live("dep-1"))
    assert squared == ["NIFTY25000CE"]
