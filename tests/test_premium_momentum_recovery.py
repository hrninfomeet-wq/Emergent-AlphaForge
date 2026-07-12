# tests/test_premium_momentum_recovery.py
"""Track B Task 7 — recovery rehydrates entered premium-momentum positions
with the PERSISTED lock state (entry premium, deployment exit plan) instead of
the generic 50%-catastrophe default, and closes locks whose position is gone
from the broker book (done_for_day='exited_while_down').

CONTAINER test (imports app.runtime -> motor). Fakes follow the repo's
in-memory async-collection pattern; premium_locks reuses _FakeLocks from
tests/test_premium_lock_store.py (+ the `$ne` operator the recovery scan uses).
"""
from __future__ import annotations

import asyncio
import importlib.util
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

pytestmark = pytest.mark.skipif(
    importlib.util.find_spec("motor") is None,
    reason="imports app.runtime (motor) — runs in the backend container",
)

from app.runtime import rehydrate_premium_momentum  # noqa: E402
from tests.test_premium_lock_store import _FakeLocks  # noqa: E402


def run(c):
    return asyncio.run(c)


def _today_ist() -> str:
    return (datetime.now(timezone.utc)
            + timedelta(hours=5, minutes=30)).strftime("%Y-%m-%d")


class _Locks(_FakeLocks):
    """_FakeLocks + the `$ne` operator the recovery scan query uses."""

    def _matches(self, d, q):
        rest = {}
        for k, v in q.items():
            if isinstance(v, dict) and "$ne" in v:
                if d.get(k) == v["$ne"]:
                    return False
            else:
                rest[k] = v
        return super()._matches(d, rest)


class _Deployments:
    def __init__(self, docs):
        self.docs = list(docs)

    async def find_one(self, q, proj=None):
        for d in self.docs:
            if all(d.get(k) == v for k, v in q.items()):
                return dict(d)
        return None


class _DB:
    def __init__(self, locks, deployments):
        self.premium_locks = locks
        self.strategy_deployments = _Deployments(deployments)


class _Reg:
    def __init__(self):
        self.calls = []

    def register(self, **kw):
        self.calls.append(kw)
        return kw


CE = {"trading_symbol": "NIFTY10JUL26C24000", "exch": "NFO",
      "instrument_key": "NSE_FO|1001"}
PE = {"trading_symbol": "NIFTY10JUL26P24000", "exch": "NFO",
      "instrument_key": "NSE_FO|1002"}


def _entered_lock(dep_id, side, contract, ordno, entry):
    return {
        "deployment_id": dep_id, "session_date": _today_ist(),
        "done_for_day": False, "triggered_side": side, side: dict(contract),
        "entered_norenordno": ordno, "entry_premium": entry,
    }


def test_reattaches_with_persisted_entry_and_stepped_xy_trail():
    locks = _Locks()
    run(locks.insert_one(_entered_lock("D1", "ce", CE, "N1", 115.0)))
    db = _DB(locks, [{
        "id": "D1",
        "params": {"stop_pct": 30.0, "target_pct": 60.0},
        "risk": {"exit_controls": {"mode": "stepped_xy", "x": 20.0, "y": 10.0}},
    }])
    reg = _Reg()
    book = {CE["trading_symbol"]: {"tsym": CE["trading_symbol"], "netqty": "65",
                                   "exch": "NFO", "lp": "118.0"}}
    out = run(rehydrate_premium_momentum(db, reg, book))
    assert out == {"reattached": 1, "closed": 0, "errors": 0}
    assert len(reg.calls) == 1
    kw = reg.calls[0]
    assert kw["key"] == "N1"                      # keyed by the entry norenordno
    assert kw["tsym"] == CE["trading_symbol"]
    assert kw["qty"] == 65
    assert kw["entry_price"] == 115.0             # PERSISTED entry, NOT a 50% default
    assert kw["source"] == "auto_live"
    assert kw["deployment_id"] == "D1"
    state = kw["state"]
    assert state["entry"] == 115.0
    assert state["stop_level"] == pytest.approx(80.5)     # 115 − 30%
    assert state["target_level"] == pytest.approx(184.0)  # 115 + 60%
    assert state["mode"] == "stepped_xy"          # trail from risk.exit_controls
    assert state["trail"]["x"] == 20.0
    assert state["trail"]["y"] == 10.0


def test_dead_lock_marked_done_and_not_registered():
    locks = _Locks()
    run(locks.insert_one(_entered_lock("D2", "pe", PE, "N2", 90.0)))
    db = _DB(locks, [{"id": "D2", "params": {}, "risk": {}}])
    reg = _Reg()
    out = run(rehydrate_premium_momentum(db, reg, {}))   # position GONE from book
    assert out == {"reattached": 0, "closed": 1, "errors": 0}
    assert reg.calls == []
    doc = run(locks.find_one({"deployment_id": "D2",
                              "session_date": _today_ist()}))
    assert doc["done_for_day"] is True
    assert doc["done_reason"] == "exited_while_down"


def test_missing_entry_premium_left_to_generic_rehydrate():
    """No persisted entry premium -> neither register nor mark_done (the
    generic 50%-default rehydrate owns that position)."""
    locks = _Locks()
    run(locks.insert_one(_entered_lock("D3", "ce", CE, "N3", None)))
    db = _DB(locks, [{"id": "D3", "params": {}, "risk": {}}])
    reg = _Reg()
    book = {CE["trading_symbol"]: {"tsym": CE["trading_symbol"], "netqty": 65}}
    out = run(rehydrate_premium_momentum(db, reg, book))
    assert out == {"reattached": 0, "closed": 0, "errors": 0}
    assert reg.calls == []
    doc = run(locks.find_one({"deployment_id": "D3",
                              "session_date": _today_ist()}))
    assert doc["done_for_day"] is False
