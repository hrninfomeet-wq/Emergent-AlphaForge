# tests/test_premium_lock_store.py
"""Track B Task 1 — premium_locks store: create-once (duplicate-key adopt),
atomic trigger latch, entered/done transitions. CONTAINER test (motor import
via app.db is NOT needed — the store takes any async collection; these tests
use an in-memory fake that mimics Mongo's filtered-update semantics)."""
import asyncio
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.premium_lock_store import (
    get_or_create_lock, capture_ref, latch_trigger, mark_entered, mark_done,
    today_locked_keys,
)


class _DupKey(Exception):
    def __str__(self):
        return "E11000 duplicate key error"


class _FakeLocks:
    """Minimal async collection: insert_one raises duplicate on (deployment_id,
    session_date) collision; update_one applies $set iff the filter matches
    (top-level equality + $exists:False only — what the store uses); find
    supports the two queries the store issues."""

    def __init__(self):
        self.docs = []

    def _key(self, d):
        return (d.get("deployment_id"), d.get("session_date"))

    async def insert_one(self, doc):
        if any(self._key(x) == self._key(doc) for x in self.docs):
            raise _DupKey()
        self.docs.append(dict(doc))

    def _matches(self, d, q):
        for k, v in q.items():
            if isinstance(v, dict) and "$exists" in v:
                if (k in d and d[k] is not None) != v["$exists"]:
                    return False
            elif d.get(k) != v:
                return False
        return True

    async def find_one(self, q, proj=None):
        for d in self.docs:
            if self._matches(d, q):
                return dict(d)
        return None

    async def update_one(self, q, upd):
        for d in self.docs:
            if self._matches(d, q):
                d.update(upd.get("$set", {}))
                return type("R", (), {"matched_count": 1, "modified_count": 1})()
        return type("R", (), {"matched_count": 0, "modified_count": 0})()

    def find(self, q, proj=None):
        docs = [dict(d) for d in self.docs if self._matches(d, q)]

        class _Cur:
            async def to_list(self, length=None):
                return docs
        return _Cur()


def run(c):
    return asyncio.run(c)


def _mk():
    return _FakeLocks()


def test_get_or_create_is_create_once_and_adopts_existing():
    col = _mk()
    a = run(get_or_create_lock(col, deployment_id="D1", session_date="2026-07-10",
                               payload={"spot_at_ref": 24000.0}))
    b = run(get_or_create_lock(col, deployment_id="D1", session_date="2026-07-10",
                               payload={"spot_at_ref": 99999.0}))   # racer loses
    assert a["spot_at_ref"] == 24000.0
    assert b["spot_at_ref"] == 24000.0          # adopted, NOT overwritten
    assert len(col.docs) == 1


def test_capture_ref_sets_side_fields_once():
    col = _mk()
    run(get_or_create_lock(col, deployment_id="D1", session_date="2026-07-10",
                           payload={"ce": {"instrument_key": "K1"}}))
    ok = run(capture_ref(col, deployment_id="D1", session_date="2026-07-10",
                         side="ce", ref_premium=101.5, ref_ts=1720600000000))
    assert ok is True
    doc = run(col.find_one({"deployment_id": "D1"}))
    assert doc["ce_ref_premium"] == 101.5 and doc["ce_ref_ts"] == 1720600000000


def test_latch_trigger_is_atomic_first_wins():
    col = _mk()
    run(get_or_create_lock(col, deployment_id="D1", session_date="2026-07-10", payload={}))
    assert run(latch_trigger(col, deployment_id="D1", session_date="2026-07-10", side="CE")) is True
    assert run(latch_trigger(col, deployment_id="D1", session_date="2026-07-10", side="PE")) is False
    doc = run(col.find_one({"deployment_id": "D1"}))
    assert doc["triggered_side"] == "CE"


def test_mark_entered_and_done():
    col = _mk()
    run(get_or_create_lock(col, deployment_id="D1", session_date="2026-07-10", payload={}))
    run(mark_entered(col, deployment_id="D1", session_date="2026-07-10",
                     norenordno="N123", entry_premium=115.0))
    run(mark_done(col, deployment_id="D1", session_date="2026-07-10", reason="exited"))
    doc = run(col.find_one({"deployment_id": "D1"}))
    assert doc["entered_norenordno"] == "N123"
    assert doc["done_for_day"] is True and doc["done_reason"] == "exited"


def test_today_locked_keys_unions_both_sides():
    col = _mk()
    run(get_or_create_lock(col, deployment_id="D1", session_date="2026-07-10",
                           payload={"ce": {"instrument_key": "KC"}, "pe": {"instrument_key": "KP"}}))
    run(get_or_create_lock(col, deployment_id="D2", session_date="2026-07-10",
                           payload={"ce": {"instrument_key": "KC"}}))   # dup key unioned once
    run(get_or_create_lock(col, deployment_id="D3", session_date="2026-07-09",
                           payload={"ce": {"instrument_key": "OLD"}}))  # stale session excluded
    keys = run(today_locked_keys(col, session_date="2026-07-10"))
    assert sorted(keys) == ["KC", "KP"]
