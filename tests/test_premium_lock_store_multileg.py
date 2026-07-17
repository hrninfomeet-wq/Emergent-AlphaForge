# tests/test_premium_lock_store_multileg.py
"""Phase 5B Task A1 — premium_locks per-leg primitives.

HOST-safe, mirrors tests/test_premium_lock_store.py's harness exactly: the
store takes ANY async collection, so this uses an in-memory fake that mimics
Mongo's filtered-update semantics (no motor/container dependency). Extended
here vs the original fake to also support $exists filters (already present
upstream) plus $unset updates (new — needed by unlatch_trigger_leg).

Also string-pins the EXISTING functions' exact source (get_or_create_lock,
get_lock, capture_ref, latch_trigger, unlatch_trigger, mark_entered,
mark_done, _now_iso) so any later drift on those fails loudly — Phase 5B
recon correction #1: those stay textually untouched. today_locked_keys is
NOT pinned: recon correction #2 requires it to change (scan lce/lpe too).
"""
import asyncio
import inspect
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app import premium_lock_store as store
from app.premium_lock_store import (
    get_or_create_lock, capture_ref, latch_trigger, mark_entered, mark_done,
    unlatch_trigger, today_locked_keys,
    latch_trigger_leg, unlatch_trigger_leg, mark_entered_leg, mark_leg_exited,
    set_lazy_armed, capture_ref_leg, legs_unresolved,
)


class _DupKey(Exception):
    def __str__(self):
        return "E11000 duplicate key error"


class _FakeLocks:
    """Minimal async collection mirroring test_premium_lock_store.py's fake,
    extended with $unset support (the original fake only ever needed $set)."""

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
                if (k in d) != v["$exists"]:
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
                for k in upd.get("$unset", {}):
                    d.pop(k, None)
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


# ---------------------------------------------------------------------------
# String-pin: existing functions byte-identical (recon correction #1)
# ---------------------------------------------------------------------------

_PINNED_SOURCE = {
    "_now_iso": (
        "def _now_iso() -> str:\n"
        "    return datetime.now(timezone.utc).isoformat()\n"
    ),
    "get_or_create_lock": (
        "async def get_or_create_lock(col: Any, *, deployment_id: str, session_date: str,\n"
        "                             payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:\n"
        "    \"\"\"Create the session lock once; a concurrent/second caller ADOPTS the\n"
        "    existing doc (its payload is discarded). Never overwrites.\"\"\"\n"
        "    doc = {\n"
        "        \"deployment_id\": str(deployment_id),\n"
        "        \"session_date\": str(session_date),\n"
        "        \"locked_at\": _now_iso(),\n"
        "        \"triggered_side\": None,\n"
        "        \"entered_norenordno\": None,\n"
        "        \"entry_premium\": None,\n"
        "        \"done_for_day\": False,\n"
        "        \"done_reason\": None,\n"
        "        **(payload or {}),\n"
        "    }\n"
        "    try:\n"
        "        await col.insert_one(doc)\n"
        "        doc.pop(\"_id\", None)\n"
        "        return doc\n"
        "    except Exception as exc:  # duplicate key → adopt the existing winner\n"
        "        if \"duplicate\" not in str(exc).lower() and \"e11000\" not in str(exc).lower():\n"
        "            raise\n"
        "        existing = await col.find_one(\n"
        "            {\"deployment_id\": str(deployment_id), \"session_date\": str(session_date)},\n"
        "            {\"_id\": 0})\n"
        "        return existing or doc\n"
    ),
    "get_lock": (
        "async def get_lock(col: Any, *, deployment_id: str, session_date: str) -> Optional[Dict[str, Any]]:\n"
        "    return await col.find_one(\n"
        "        {\"deployment_id\": str(deployment_id), \"session_date\": str(session_date)}, {\"_id\": 0})\n"
    ),
    "capture_ref": (
        "async def capture_ref(col: Any, *, deployment_id: str, session_date: str,\n"
        "                      side: str, ref_premium: float, ref_ts: int) -> bool:\n"
        "    \"\"\"Persist one side's reference premium ONCE (filtered on the field being\n"
        "    absent — a second capture is a no-op, the first tick wins).\"\"\"\n"
        "    s = str(side).lower()\n"
        "    res = await col.update_one(\n"
        "        {\"deployment_id\": str(deployment_id), \"session_date\": str(session_date),\n"
        "         f\"{s}_ref_premium\": {\"$exists\": False}},\n"
        "        {\"$set\": {f\"{s}_ref_premium\": float(ref_premium),\n"
        "                  f\"{s}_ref_ts\": int(ref_ts),\n"
        "                  f\"{s}_ref_captured_at\": _now_iso()}},\n"
        "    )\n"
        "    return int(getattr(res, \"matched_count\", 0) or 0) == 1\n"
    ),
    "latch_trigger": (
        "async def latch_trigger(col: Any, *, deployment_id: str, session_date: str, side: str) -> bool:\n"
        "    \"\"\"Atomically latch the first side to trigger. Filter requires the latch to\n"
        "    still be None — Mongo's single-doc update makes first-wins race-safe.\"\"\"\n"
        "    res = await col.update_one(\n"
        "        {\"deployment_id\": str(deployment_id), \"session_date\": str(session_date),\n"
        "         \"triggered_side\": None, \"done_for_day\": False},\n"
        "        {\"$set\": {\"triggered_side\": str(side).upper(), \"triggered_at\": _now_iso()}},\n"
        "    )\n"
        "    return int(getattr(res, \"matched_count\", 0) or 0) == 1\n"
    ),
    "unlatch_trigger": (
        "async def unlatch_trigger(col: Any, *, deployment_id: str, session_date: str) -> None:\n"
        "    \"\"\"Release the latch after a journaled entry FAILURE (refusal/error) so a\n"
        "    later bar may re-trigger. Mirrors release_live_trade_claim's philosophy.\"\"\"\n"
        "    await col.update_one(\n"
        "        {\"deployment_id\": str(deployment_id), \"session_date\": str(session_date),\n"
        "         \"entered_norenordno\": None},\n"
        "        {\"$set\": {\"triggered_side\": None}},\n"
        "    )\n"
    ),
    "mark_entered": (
        "async def mark_entered(col: Any, *, deployment_id: str, session_date: str,\n"
        "                       norenordno: str, entry_premium: Optional[float]) -> None:\n"
        "    await col.update_one(\n"
        "        {\"deployment_id\": str(deployment_id), \"session_date\": str(session_date)},\n"
        "        {\"$set\": {\"entered_norenordno\": str(norenordno),\n"
        "                  \"entry_premium\": (float(entry_premium) if entry_premium is not None else None),\n"
        "                  \"entered_at\": _now_iso()}},\n"
        "    )\n"
    ),
    "mark_done": (
        "async def mark_done(col: Any, *, deployment_id: str, session_date: str, reason: str) -> None:\n"
        "    await col.update_one(\n"
        "        {\"deployment_id\": str(deployment_id), \"session_date\": str(session_date)},\n"
        "        {\"$set\": {\"done_for_day\": True, \"done_reason\": str(reason), \"done_at\": _now_iso()}},\n"
        "    )\n"
    ),
}


def test_existing_functions_are_textually_unchanged():
    for name, pinned in _PINNED_SOURCE.items():
        fn = getattr(store, name)
        actual = inspect.getsource(fn)
        assert actual == pinned, f"{name} source drifted from its Phase-5B pin:\n{actual!r}"


# ---------------------------------------------------------------------------
# New per-leg primitives
# ---------------------------------------------------------------------------

def test_latch_trigger_leg_is_atomic_first_wins_per_leg():
    col = _mk()
    run(get_or_create_lock(col, deployment_id="D1", session_date="2026-07-15", payload={}))
    assert run(latch_trigger_leg(col, deployment_id="D1", session_date="2026-07-15", leg="pce")) is True
    # second attempt on the SAME leg fails (already triggered)
    assert run(latch_trigger_leg(col, deployment_id="D1", session_date="2026-07-15", leg="pce")) is False
    # the OTHER primary leg is untouched by pce's latch
    assert run(latch_trigger_leg(col, deployment_id="D1", session_date="2026-07-15", leg="ppe")) is True
    doc = run(col.find_one({"deployment_id": "D1"}))
    assert doc["ce_triggered"] is True and doc["pe_triggered"] is True
    assert "triggered_side" not in doc or doc["triggered_side"] is None  # whole-doc field untouched


def test_latch_trigger_leg_respects_done_for_day():
    col = _mk()
    run(get_or_create_lock(col, deployment_id="D1", session_date="2026-07-15", payload={}))
    run(mark_done(col, deployment_id="D1", session_date="2026-07-15", reason="no_lock"))
    assert run(latch_trigger_leg(col, deployment_id="D1", session_date="2026-07-15", leg="pce")) is False


def test_latch_trigger_leg_unknown_leg_raises():
    col = _mk()
    try:
        run(latch_trigger_leg(col, deployment_id="D1", session_date="2026-07-15", leg="bogus"))
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_unlatch_trigger_leg_only_releases_that_leg_without_a_completed_entry():
    col = _mk()
    run(get_or_create_lock(col, deployment_id="D1", session_date="2026-07-15", payload={}))
    run(latch_trigger_leg(col, deployment_id="D1", session_date="2026-07-15", leg="pce"))
    run(latch_trigger_leg(col, deployment_id="D1", session_date="2026-07-15", leg="ppe"))
    run(mark_entered_leg(col, deployment_id="D1", session_date="2026-07-15", leg="ppe",
                         norenordno="N1", entry_premium=50.0))
    # pce has no completed entry -> release succeeds
    run(unlatch_trigger_leg(col, deployment_id="D1", session_date="2026-07-15", leg="pce"))
    # ppe DID complete an entry -> release is a no-op (never releases a completed entry)
    run(unlatch_trigger_leg(col, deployment_id="D1", session_date="2026-07-15", leg="ppe"))
    doc = run(col.find_one({"deployment_id": "D1"}))
    assert "ce_triggered" not in doc          # unset -> truly absent again
    assert doc["pe_triggered"] is True         # untouched (had a completed entry)
    # released leg can re-trigger a later bar
    assert run(latch_trigger_leg(col, deployment_id="D1", session_date="2026-07-15", leg="pce")) is True


def test_mark_entered_leg_and_mark_leg_exited():
    col = _mk()
    run(get_or_create_lock(col, deployment_id="D1", session_date="2026-07-15", payload={}))
    run(mark_entered_leg(col, deployment_id="D1", session_date="2026-07-15", leg="pce",
                         norenordno="N42", entry_premium=101.5))
    doc = run(col.find_one({"deployment_id": "D1"}))
    assert doc["ce_entered_norenordno"] == "N42" and doc["ce_entry_premium"] == 101.5
    assert "pe_entered_norenordno" not in doc      # other leg untouched

    run(mark_leg_exited(col, deployment_id="D1", session_date="2026-07-15", leg="pce"))
    doc = run(col.find_one({"deployment_id": "D1"}))
    assert doc["ce_exited"] is True
    assert "pe_exited" not in doc


def test_mark_entered_leg_handles_none_entry_premium():
    col = _mk()
    run(get_or_create_lock(col, deployment_id="D1", session_date="2026-07-15", payload={}))
    run(mark_entered_leg(col, deployment_id="D1", session_date="2026-07-15", leg="lce",
                         norenordno="N7", entry_premium=None))
    doc = run(col.find_one({"deployment_id": "D1"}))
    assert doc["lce_entered_norenordno"] == "N7" and doc["lce_entry_premium"] is None


def test_set_lazy_armed_is_idempotent_one_shot():
    col = _mk()
    run(get_or_create_lock(col, deployment_id="D1", session_date="2026-07-15", payload={}))
    assert run(set_lazy_armed(col, deployment_id="D1", session_date="2026-07-15",
                              side="ce", parent_reason="stop")) is True
    # second stop on the same side within the session never re-arms
    assert run(set_lazy_armed(col, deployment_id="D1", session_date="2026-07-15",
                              side="ce", parent_reason="trailing_stop")) is False
    doc = run(col.find_one({"deployment_id": "D1"}))
    assert doc["lazy_armed_ce"] is True and doc["lazy_armed_ce_reason"] == "stop"
    assert "lazy_armed_pe" not in doc


def test_capture_ref_leg_sets_once_for_lazy_leg():
    col = _mk()
    run(get_or_create_lock(col, deployment_id="D1", session_date="2026-07-15", payload={}))
    ok = run(capture_ref_leg(col, deployment_id="D1", session_date="2026-07-15",
                             leg="lce", ref_premium=42.0, ref_ts=1720600000000))
    assert ok is True
    ok2 = run(capture_ref_leg(col, deployment_id="D1", session_date="2026-07-15",
                              leg="lce", ref_premium=999.0, ref_ts=1))
    assert ok2 is False   # first capture wins
    doc = run(col.find_one({"deployment_id": "D1"}))
    assert doc["lce_ref_premium"] == 42.0 and doc["lce_ref_ts"] == 1720600000000


def test_capture_ref_leg_primary_alias_writes_same_field_as_capture_ref():
    """pce/ppe alias the EXISTING ce/pe ref storage -- no duplicate field."""
    col = _mk()
    run(get_or_create_lock(col, deployment_id="D1", session_date="2026-07-15", payload={}))
    run(capture_ref_leg(col, deployment_id="D1", session_date="2026-07-15",
                        leg="pce", ref_premium=101.5, ref_ts=1720600000000))
    doc = run(col.find_one({"deployment_id": "D1"}))
    assert doc["ce_ref_premium"] == 101.5   # same field capture_ref(side="ce") would set
    # and capture_ref itself now sees the field as already captured (no dup write)
    ok = run(capture_ref(col, deployment_id="D1", session_date="2026-07-15",
                         side="ce", ref_premium=555.0, ref_ts=2))
    assert ok is False


def test_today_locked_keys_scans_lazy_legs_too():
    col = _mk()
    run(get_or_create_lock(col, deployment_id="D1", session_date="2026-07-15",
                           payload={"ce": {"instrument_key": "KC"}}))
    run(get_or_create_lock(col, deployment_id="D2", session_date="2026-07-15", payload={}))
    run(capture_ref_leg(col, deployment_id="D2", session_date="2026-07-15",
                        leg="lpe", ref_premium=10.0, ref_ts=1))
    # capture_ref_leg alone doesn't set the instrument key; simulate the lazy
    # strike-lock write A3 will perform (flat naming, per the spec).
    col.docs[1]["lpe_instrument_key"] = "KLPE"
    keys = run(today_locked_keys(col, session_date="2026-07-15"))
    assert sorted(keys) == ["KC", "KLPE"]


# ---------------------------------------------------------------------------
# legs_unresolved — pure helper
# ---------------------------------------------------------------------------

def test_legs_unresolved_empty_when_nothing_active():
    assert legs_unresolved({}, {"leg_mode": "both"}) == []


def test_legs_unresolved_reports_triggered_not_yet_exited():
    lock = {"ce_triggered": True}
    assert legs_unresolved(lock, {"leg_mode": "both"}) == ["pce"]


def test_legs_unresolved_excludes_exited_legs():
    lock = {"ce_triggered": True, "ce_exited": True, "pe_entered_norenordno": "N1"}
    assert legs_unresolved(lock, {"leg_mode": "both"}) == ["ppe"]


def test_legs_unresolved_ignores_lazy_legs_when_lazy_disabled():
    lock = {"lce_triggered": True}   # shouldn't happen, but defend anyway
    assert legs_unresolved(lock, {"leg_mode": "both", "lazy_enabled": False}) == []


def test_legs_unresolved_includes_lazy_legs_when_enabled():
    lock = {"lce_triggered": True, "pe_entered_norenordno": "N1", "pe_exited": True}
    assert legs_unresolved(lock, {"leg_mode": "both", "lazy_enabled": True}) == ["lce"]


def test_legs_unresolved_all_resolved_returns_empty():
    lock = {"ce_triggered": True, "ce_exited": True,
            "pe_triggered": True, "pe_exited": True}
    assert legs_unresolved(lock, {"leg_mode": "both"}) == []
