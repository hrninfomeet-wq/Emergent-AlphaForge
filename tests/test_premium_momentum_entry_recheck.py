# tests/test_premium_momentum_entry_recheck.py
"""Track B Task 8 — last-line trigger re-check + lock transitions.

auto_live_trade_for_signal, for a premium-momentum signal (signal_doc carries
``premium_momentum.ref_premium``), re-verifies the momentum trigger against the
FRESH entry tick (ref_ltp) right before placement:

* fresh premium fell back below the trigger -> refuse with a DISTINCT journaled
  reason (``premium_trigger_not_met``), release the claim AND the session lock's
  trigger latch (triggered_side back to None) so a later bar may re-trigger;
* fresh premium still at/above the trigger -> proceed through the normal
  place/arm flow, and on success adopt the placed order into today's lock
  (mark_entered: entered_norenordno + entry_premium).

Also covers the guard's confirmed-flat close hook (_live_guard_on_close):
a premium-momentum deployment's confirmed close marks today's lock
done_for_day (reason="exited"); non-PM deployments and dry-run squares leave
locks untouched; the hook NEVER raises. Those tests import app.runtime (motor)
and self-skip outside the backend container.

DB fakes reuse tests/test_auto_live.py's FakeDB harness + _FakeLocks from
tests/test_premium_lock_store.py (re-sync both into the container).
"""
from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.auto_live import auto_live_trade_for_signal  # noqa: E402
from tests.test_auto_live import (  # noqa: E402
    KEY, NOW, _SUCCESS, FakeDB, _arm_for_factory, _fresh_tick,
    make_confirmed_signal, make_live_deployment, make_place_fn,
)
from tests.test_premium_lock_store import _FakeLocks  # noqa: E402


def run(c):
    return asyncio.run(c)


def _today_ist() -> str:
    return (datetime.now(timezone.utc)
            + timedelta(hours=5, minutes=30)).strftime("%Y-%m-%d")


def _latched_lock(dep_id: str = "dep-1", side: str = "CE") -> Dict[str, Any]:
    """A lock as the evaluator leaves it right after journal-then-latch: the
    trigger latched, no entry yet."""
    return {
        "deployment_id": dep_id, "session_date": _today_ist(),
        "done_for_day": False, "triggered_side": side,
        "entered_norenordno": None, "entry_premium": None,
        "ce": {"instrument_key": KEY, "trading_symbol": "NIFTYTEST23950CE"},
    }


def _pm_db() -> FakeDB:
    db = FakeDB()
    db.premium_locks = _FakeLocks()
    run(db.premium_locks.insert_one(_latched_lock()))
    return db


def _pm_signal(ref_premium: float = 100.0) -> Dict[str, Any]:
    sig = make_confirmed_signal()
    sig["premium_momentum"] = {"ref_premium": ref_premium, "premium_now": 116.0}
    return sig


def _pm_deployment(**params) -> Dict[str, Any]:
    dep = make_live_deployment(lots=2)
    dep["params"] = params or {"momentum_pct": 15}
    return dep


# ====================== last-line re-check =====================================

def test_recheck_refuses_when_fresh_premium_below_trigger():
    """ref 100, momentum_pct 15 -> trigger 115; fresh entry tick 114.0 must
    REFUSE: distinct journaled error, claim released, latch released, executor
    never called."""
    db = _pm_db()
    sig = _pm_signal()
    db.signals.rows.append(dict(sig))
    calls: List[Dict[str, Any]] = []
    out = run(auto_live_trade_for_signal(
        db, _pm_deployment(momentum_pct=15), sig,
        latest_tick_lookup={KEY: _fresh_tick(114.0)}.get, now_utc=NOW,
        place_fn=make_place_fn(_SUCCESS, calls),
        arm_for=_arm_for_factory([]), account_max=20))
    assert out["created"] is False
    assert out["reason"] == "premium_trigger_not_met"
    assert calls == []                                  # never reached the executor
    assert db.live_trades.rows == []
    stored = db.signals.rows[0]
    assert stored["state"] == "CONFIRMED"               # stays confirmed
    assert stored["live_trade_error"] == "premium_trigger_not_met"
    assert stored["live_intended"] == {"ref_premium": 100.0,
                                       "premium_at_entry": 114.0}
    assert "paper_trade_claim" not in stored            # claim released
    lock = run(db.premium_locks.find_one({"deployment_id": "dep-1",
                                          "session_date": _today_ist()}))
    assert lock["triggered_side"] is None               # latch released -> re-trigger OK


def test_recheck_passes_at_trigger_and_marks_entered():
    """Fresh premium 116.0 >= 115 trigger -> proceeds through the normal flow;
    the placed order is adopted into today's lock (mark_entered)."""
    db = _pm_db()
    sig = _pm_signal()
    db.signals.rows.append(dict(sig))
    calls: List[Dict[str, Any]] = []
    out = run(auto_live_trade_for_signal(
        db, _pm_deployment(momentum_pct=15), sig,
        latest_tick_lookup={KEY: _fresh_tick(116.0)}.get, now_utc=NOW,
        place_fn=make_place_fn(_SUCCESS, calls),
        arm_for=_arm_for_factory([]), account_max=20))
    assert out["created"] is True
    assert out["norenordno"] == "ABC"
    lock = run(db.premium_locks.find_one({"deployment_id": "dep-1",
                                          "session_date": _today_ist()}))
    assert lock["triggered_side"] == "CE"               # latch untouched
    assert lock["entered_norenordno"] == "ABC"          # adopted
    assert lock["entry_premium"] == 116.0


def test_recheck_pts_deployment_uses_pts_precedence_not_error():
    """The registration schema DEFAULTS momentum_pct=15.0, so a pts deployment
    carries BOTH knobs — the re-check must apply the same pts-wins precedence
    as evaluate_premium_momentum_bar (momentum_triggered raises on both-set,
    which would crash placement mid-claim). ref 100 + pts 10 -> trigger 110;
    fresh 114 proceeds (a pct-only read of 15% would have refused)."""
    db = _pm_db()
    sig = _pm_signal()
    db.signals.rows.append(dict(sig))
    out = run(auto_live_trade_for_signal(
        db, _pm_deployment(momentum_pct=15.0, momentum_pts=10.0), sig,
        latest_tick_lookup={KEY: _fresh_tick(114.0)}.get, now_utc=NOW,
        place_fn=make_place_fn(_SUCCESS, []),
        arm_for=_arm_for_factory([]), account_max=20))
    assert out["created"] is True


def test_non_pm_signal_skips_recheck_and_lock_writes():
    """A generic (non premium-momentum) signal must be COMPLETELY untouched by
    the re-check and the mark_entered adoption — no premium_locks writes."""
    db = FakeDB()
    db.premium_locks = _FakeLocks()                     # empty — must stay empty
    sig = make_confirmed_signal()                       # no premium_momentum field
    db.signals.rows.append(dict(sig))
    out = run(auto_live_trade_for_signal(
        db, make_live_deployment(lots=2), sig,
        latest_tick_lookup={KEY: _fresh_tick(114.0)}.get, now_utc=NOW,
        place_fn=make_place_fn(_SUCCESS, []),
        arm_for=_arm_for_factory([]), account_max=20))
    assert out["created"] is True                       # 114 is a fine generic entry
    assert db.premium_locks.docs == []


# ====================== confirmed-flat -> mark_done("exited") ==================
# These import app.runtime (motor) — container only; self-skip elsewhere.

class _Deployments:
    def __init__(self, docs):
        self.docs = list(docs)

    async def find_one(self, q, proj=None):
        for d in self.docs:
            if all(d.get(k) == v for k, v in q.items()):
                return dict(d)
        return None


class _RaisingDeployments:
    async def find_one(self, q, proj=None):
        raise RuntimeError("db down")


class _HookDB:
    def __init__(self, locks, deployments):
        self.premium_locks = locks
        self.strategy_deployments = deployments


def _wire_hook(monkeypatch, *, deployments):
    pytest.importorskip(
        "motor", reason="imports app.runtime (motor) — runs in the backend container")
    import app.runtime as rt
    import app.live.close_loop as cl
    closed: List[str] = []

    async def _fake_close(db, *, norenordno, exit_price, exit_reason, **kw):
        closed.append(str(norenordno))
        return True

    monkeypatch.setattr(cl, "close_live_trade", _fake_close)
    locks = _FakeLocks()
    lock = _latched_lock("D1")
    lock["entered_norenordno"] = "N1"
    lock["entry_premium"] = 115.0
    run(locks.insert_one(lock))
    db = _HookDB(locks, deployments)
    monkeypatch.setattr(rt, "get_db", lambda: db)
    return rt, db, closed


_ENTRY = {"id": "N1", "deployment_id": "D1", "source": "auto_live"}
_CONFIRMED_FLAT = {"squared": True, "via": "confirmed_flat"}


def test_guard_on_close_marks_premium_lock_done_exited(monkeypatch):
    rt, db, closed = _wire_hook(monkeypatch, deployments=_Deployments(
        [{"id": "D1", "strategy_id": "premium_momentum"}]))
    run(rt._live_guard_on_close(dict(_ENTRY), 118.0, "STOP", dict(_CONFIRMED_FLAT)))
    assert closed == ["N1"]                             # journaling unchanged
    doc = run(db.premium_locks.find_one({"deployment_id": "D1"}))
    assert doc["done_for_day"] is True
    assert doc["done_reason"] == "exited"


def test_guard_on_close_non_pm_deployment_leaves_lock(monkeypatch):
    rt, db, closed = _wire_hook(monkeypatch, deployments=_Deployments(
        [{"id": "D1", "strategy_id": "confluence"}]))
    run(rt._live_guard_on_close(dict(_ENTRY), 118.0, "STOP", dict(_CONFIRMED_FLAT)))
    assert closed == ["N1"]
    doc = run(db.premium_locks.find_one({"deployment_id": "D1"}))
    assert doc["done_for_day"] is False                 # untouched


def test_guard_on_close_dry_run_square_marks_nothing(monkeypatch):
    """A dry-run square left the position OPEN — neither journal nor lock-done."""
    rt, db, closed = _wire_hook(monkeypatch, deployments=_Deployments(
        [{"id": "D1", "strategy_id": "premium_momentum"}]))
    run(rt._live_guard_on_close(dict(_ENTRY), 118.0, "STOP",
                                {"squared": False, "dry_run": True}))
    assert closed == []
    doc = run(db.premium_locks.find_one({"deployment_id": "D1"}))
    assert doc["done_for_day"] is False


def test_guard_on_close_lock_hook_never_raises(monkeypatch):
    """A deployment-lookup failure must not break the close journaling path."""
    rt, db, closed = _wire_hook(monkeypatch, deployments=_RaisingDeployments())
    run(rt._live_guard_on_close(dict(_ENTRY), 118.0, "STOP", dict(_CONFIRMED_FLAT)))
    assert closed == ["N1"]                             # journal still happened
    doc = run(db.premium_locks.find_one({"deployment_id": "D1"}))
    assert doc["done_for_day"] is False                 # and nothing exploded


# ====================== 5B A4: both-mode per-leg lock writes ====================

def _both_latched_lock(dep_id: str = "dep-1") -> Dict[str, Any]:
    """A both-mode lock right after the evaluator leg-latched pce (ce_triggered
    set, session-global triggered_side untouched/None)."""
    return {
        "deployment_id": dep_id, "session_date": _today_ist(),
        "done_for_day": False, "triggered_side": None,
        "entered_norenordno": None, "entry_premium": None,
        "ce_triggered": True,
        "pe_triggered": True,   # the OTHER leg's latch — must never be touched
        "ce": {"instrument_key": KEY, "trading_symbol": "NIFTYTEST23950CE"},
    }


def _both_db() -> FakeDB:
    db = FakeDB()
    db.premium_locks = _FakeLocks()
    run(db.premium_locks.insert_one(_both_latched_lock()))
    return db


def _both_signal(ref_premium: float = 100.0, leg: str = "pce") -> Dict[str, Any]:
    sig = make_confirmed_signal()
    sig["premium_momentum"] = {"ref_premium": ref_premium, "premium_now": 116.0,
                               "leg": leg}
    return sig


def test_both_mode_recheck_refusal_unlatches_only_that_leg():
    """Refusal must release ONLY the failing leg's latch (ce_triggered gone via
    $unset) — the other leg's latch AND the legacy session-global field stay
    exactly as they were (recon anchor #2's two-signal seam)."""
    db = _both_db()
    sig = _both_signal()
    db.signals.rows.append(dict(sig))
    calls: List[Dict[str, Any]] = []
    dep = _pm_deployment(momentum_pct=15, leg_mode="both")
    out = run(auto_live_trade_for_signal(
        db, dep, sig,
        latest_tick_lookup={KEY: _fresh_tick(114.0)}.get, now_utc=NOW,
        place_fn=make_place_fn(_SUCCESS, calls),
        arm_for=_arm_for_factory([]), account_max=20))
    assert out["created"] is False and out["reason"] == "premium_trigger_not_met"
    lock = run(db.premium_locks.find_one({"deployment_id": "dep-1",
                                          "session_date": _today_ist()}))
    assert "ce_triggered" not in lock, "the failing leg's latch must be $unset"
    assert lock["pe_triggered"] is True, "the OTHER leg's latch must be untouched"
    assert lock["triggered_side"] is None, \
        "both-mode refusal must never touch the legacy session-global latch"


def test_both_mode_entry_adopts_via_leg_fields_not_legacy():
    """Success must adopt the order into the LEG's fields (ce_entered_norenordno
    / ce_entry_premium via the pce->ce prefix) and leave the legacy
    session-global entered_norenordno/entry_premium untouched (ambiguous with
    two concurrent entries)."""
    db = _both_db()
    sig = _both_signal()
    db.signals.rows.append(dict(sig))
    calls: List[Dict[str, Any]] = []
    dep = _pm_deployment(momentum_pct=15, leg_mode="both")
    out = run(auto_live_trade_for_signal(
        db, dep, sig,
        latest_tick_lookup={KEY: _fresh_tick(116.0)}.get, now_utc=NOW,
        place_fn=make_place_fn(_SUCCESS, calls),
        arm_for=_arm_for_factory([]), account_max=20))
    assert out["created"] is True
    lock = run(db.premium_locks.find_one({"deployment_id": "dep-1",
                                          "session_date": _today_ist()}))
    assert lock["ce_entered_norenordno"] == "ABC"
    assert lock["ce_entry_premium"] == 116.0
    assert lock["entered_norenordno"] is None, \
        "legacy session-global entry fields must stay untouched in both-mode"
    assert lock["entry_premium"] is None
