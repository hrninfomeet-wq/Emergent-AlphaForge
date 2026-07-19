"""TDD tests for backend/app/live/mode.py (Task L3.1 — mode gate).

Covers
------
is_live_order_allowed:
  - True ONLY for {mode:"LIVE_TEST", single_shot_consumed:False}
  - False for PAPER
  - False for LIVE_OFFLINE
  - False for LIVE_ARMED
  - False when single_shot_consumed=True (even in LIVE_TEST)
  - False for None
  - False for {} (empty dict)
  - False for a non-dict (int, str, list)

ModeStore (via FakeAsyncCollection):
  - empty store -> get() returns mode PAPER + single_shot_consumed False
  - set_mode("LIVE_ARMED") raises ValueError
  - set_mode("LIVE_TEST") without confirm raises ValueError
  - set_mode("LIVE_TEST", confirm=True, connected=False) raises ValueError
  - set_mode("LIVE_TEST", confirm=True, connected=True, can_trade=False) raises ValueError
  - set_mode("LIVE_TEST", confirm=True, connected=True, can_trade=True) succeeds
    → get() shows mode LIVE_TEST + single_shot_consumed False
  - consume_single_shot() → is_live_order_allowed(get()) is False
  - second set_mode to PAPER then back to LIVE_TEST resets single_shot_consumed False
    (deliberate re-entry resets the latch)
  - revert_to_offline() sets mode LIVE_OFFLINE + clears single_shot_consumed + test_session_id
  - now_iso is stored as "since" on set_mode and revert_to_offline
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.live.mode import (
    ModeStore,
    is_live_order_allowed,
)


# ---------------------------------------------------------------------------
# FakeAsyncCollection — in-memory Mongo stand-in (reuse pattern from idempotency)
# ---------------------------------------------------------------------------

class _FakeCursor:
    def __init__(self, docs: List[dict]) -> None:
        self._docs = docs

    async def to_list(self, length: Optional[int] = None) -> List[dict]:
        return list(self._docs) if length is None else list(self._docs[:length])


class _UpdateResult:
    def __init__(self, matched_count: int) -> None:
        self.matched_count = matched_count


class FakeAsyncCollection:
    """In-memory async collection satisfying the ModeStore collection interface."""

    def __init__(self) -> None:
        self.docs: List[Dict[str, Any]] = []

    async def find_one(
        self,
        query: Dict[str, Any],
        projection: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        for doc in self.docs:
            if _matches(doc, query):
                return dict(doc)
        return None

    async def insert_one(self, doc: Dict[str, Any]) -> Any:
        self.docs.append(dict(doc))

    async def update_one(
        self,
        query: Dict[str, Any],
        update: Dict[str, Any],
        upsert: bool = False,
    ) -> _UpdateResult:
        for doc in self.docs:
            if _matches(doc, query):
                if "$set" in update:
                    doc.update(update["$set"])
                return _UpdateResult(matched_count=1)
        # No match — handle upsert
        if upsert:
            new_doc: Dict[str, Any] = {}
            # Seed with query fields (excluding Mongo operators)
            for k, v in query.items():
                if not k.startswith("$"):
                    new_doc[k] = v
            if "$set" in update:
                new_doc.update(update["$set"])
            self.docs.append(new_doc)
            return _UpdateResult(matched_count=0)
        return _UpdateResult(matched_count=0)

    def find(
        self,
        query: Dict[str, Any],
        projection: Optional[Dict[str, Any]] = None,
    ) -> _FakeCursor:
        results = [dict(d) for d in self.docs if _matches(d, query)]
        return _FakeCursor(results)

    async def create_index(self, field: str, unique: bool = False) -> str:
        return f"{field}_1"


def _matches(doc: dict, query: dict) -> bool:
    """Evaluate a flat Mongo query dict (equality; None matches None)."""
    for k, v in query.items():
        if doc.get(k) != v:
            return False
    return True


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _store() -> tuple[ModeStore, FakeAsyncCollection]:
    col = FakeAsyncCollection()
    return ModeStore(col), col


# ---------------------------------------------------------------------------
# is_live_order_allowed — pure predicate tests
# ---------------------------------------------------------------------------

class TestIsLiveOrderAllowed:
    """Pure predicate — no I/O, no store required."""

    def test_true_for_live_test_unconsumed(self):
        doc = {"mode": "LIVE_TEST", "single_shot_consumed": False}
        assert is_live_order_allowed(doc) is True

    def test_false_for_paper(self):
        assert is_live_order_allowed({"mode": "PAPER", "single_shot_consumed": False}) is False

    def test_false_for_live_offline(self):
        assert is_live_order_allowed({"mode": "LIVE_OFFLINE", "single_shot_consumed": False}) is False

    def test_false_for_live_armed(self):
        # LIVE_ARMED is an L4 mode; should not grant real orders via this predicate
        assert is_live_order_allowed({"mode": "LIVE_ARMED", "single_shot_consumed": False}) is False

    def test_false_when_single_shot_consumed_true(self):
        doc = {"mode": "LIVE_TEST", "single_shot_consumed": True}
        assert is_live_order_allowed(doc) is False

    def test_false_for_none(self):
        assert is_live_order_allowed(None) is False

    def test_false_for_empty_dict(self):
        assert is_live_order_allowed({}) is False

    def test_false_for_non_dict_int(self):
        assert is_live_order_allowed(42) is False  # type: ignore[arg-type]

    def test_false_for_non_dict_string(self):
        assert is_live_order_allowed("LIVE_TEST") is False  # type: ignore[arg-type]

    def test_false_for_non_dict_list(self):
        assert is_live_order_allowed(["LIVE_TEST"]) is False  # type: ignore[arg-type]

    def test_false_for_missing_mode_key(self):
        """Dict without 'mode' key → treated as unknown mode → False."""
        assert is_live_order_allowed({"single_shot_consumed": False}) is False

    def test_false_for_missing_single_shot_consumed_key(self):
        """Dict without 'single_shot_consumed' — get() defaults to None which is falsy
        BUT mode is not LIVE_TEST so this is doubly False."""
        assert is_live_order_allowed({"mode": "PAPER"}) is False

    def test_false_for_live_test_missing_consumed_key_defaults_falsy(self):
        """LIVE_TEST without single_shot_consumed key → .get() returns None.

        F3: require explicit False — a missing key is NOT the same as False.
        A partial/tampered doc with the key absent must be fail-closed (False).
        """
        doc = {"mode": "LIVE_TEST"}
        # None is not False → blocked (fail-closed)
        assert is_live_order_allowed(doc) is False

    # ------------------------------------------------------------------
    # F1 — strict confirm (truthy non-True values must be rejected)
    # ------------------------------------------------------------------

    def test_f1_confirm_int_1_raises(self):
        """confirm=1 is truthy but not literal True — must raise ValueError."""
        store, _ = _store()
        with pytest.raises(ValueError, match="confirm"):
            asyncio.run(store.set_mode("LIVE_TEST", confirm=1))  # type: ignore[arg-type]

    def test_f1_confirm_string_yes_raises(self):
        """confirm='yes' is truthy but not literal True — must raise ValueError."""
        store, _ = _store()
        with pytest.raises(ValueError, match="confirm"):
            asyncio.run(store.set_mode("LIVE_TEST", confirm="yes"))  # type: ignore[arg-type]

    def test_f1_confirm_list_raises(self):
        """confirm=[1] is truthy but not literal True — must raise ValueError."""
        store, _ = _store()
        with pytest.raises(ValueError, match="confirm"):
            asyncio.run(store.set_mode("LIVE_TEST", confirm=[1]))  # type: ignore[arg-type]

    def test_f1_confirm_literal_true_succeeds(self):
        """confirm=True (literal) must succeed."""
        store, _ = _store()
        result = asyncio.run(
            store.set_mode("LIVE_TEST", confirm=True, connected=True, can_trade=True)
        )
        assert result["mode"] == "LIVE_TEST"

    # ------------------------------------------------------------------
    # F2 — strict connected / can_trade (truthy non-True values must be rejected)
    # ------------------------------------------------------------------

    def test_f2_connected_int_1_raises(self):
        """connected=1 is truthy but not literal True — must raise ValueError."""
        store, _ = _store()
        with pytest.raises(ValueError, match="connected"):
            asyncio.run(store.set_mode("LIVE_TEST", confirm=True, connected=1))  # type: ignore[arg-type]

    def test_f2_can_trade_string_yes_raises(self):
        """can_trade='yes' is truthy but not literal True — must raise ValueError."""
        store, _ = _store()
        with pytest.raises(ValueError, match="can_trade"):
            asyncio.run(
                store.set_mode("LIVE_TEST", confirm=True, connected=True, can_trade="yes")  # type: ignore[arg-type]
            )

    def test_f2_all_literal_true_succeeds(self):
        """All three literal True must succeed (default call pattern)."""
        store, _ = _store()
        result = asyncio.run(
            store.set_mode("LIVE_TEST", confirm=True, connected=True, can_trade=True)
        )
        assert result["mode"] == "LIVE_TEST"
        assert result["single_shot_consumed"] is False

    # ------------------------------------------------------------------
    # F3 — explicit-False consumed in is_live_order_allowed
    # ------------------------------------------------------------------

    def test_f3_consumed_missing_key_blocked(self):
        """{mode:LIVE_TEST} with no consumed key → False (fail-closed)."""
        assert is_live_order_allowed({"mode": "LIVE_TEST"}) is False

    def test_f3_consumed_none_blocked(self):
        """{mode:LIVE_TEST, single_shot_consumed:None} → False."""
        assert is_live_order_allowed({"mode": "LIVE_TEST", "single_shot_consumed": None}) is False

    def test_f3_consumed_zero_blocked(self):
        """{mode:LIVE_TEST, single_shot_consumed:0} → False (0 is falsy but not False)."""
        assert is_live_order_allowed({"mode": "LIVE_TEST", "single_shot_consumed": 0}) is False

    def test_f3_consumed_empty_string_blocked(self):
        """{mode:LIVE_TEST, single_shot_consumed:''} → False."""
        assert is_live_order_allowed({"mode": "LIVE_TEST", "single_shot_consumed": ""}) is False

    def test_f3_consumed_explicit_false_allowed(self):
        """{mode:LIVE_TEST, single_shot_consumed:False} → True (literal False = unconsumed)."""
        assert is_live_order_allowed({"mode": "LIVE_TEST", "single_shot_consumed": False}) is True

    def test_f3_consumed_true_blocked(self):
        """{mode:LIVE_TEST, single_shot_consumed:True} → False (already consumed)."""
        assert is_live_order_allowed({"mode": "LIVE_TEST", "single_shot_consumed": True}) is False

    def test_f3_normal_path_store_doc_still_allowed(self):
        """set_mode writes literal False — normal-path doc must still allow orders."""
        store, _ = _store()
        asyncio.run(
            store.set_mode("LIVE_TEST", confirm=True, connected=True, can_trade=True)
        )
        doc = asyncio.run(store.get())
        # The store writes single_shot_consumed=False (literal) so this must be True
        assert is_live_order_allowed(doc) is True


# ---------------------------------------------------------------------------
# ModeStore — empty store defaults
# ---------------------------------------------------------------------------

class TestModeStoreDefaults:
    def test_empty_store_returns_paper_mode(self):
        store, _ = _store()
        doc = asyncio.run(store.get())
        assert doc["mode"] == "PAPER"

    def test_empty_store_single_shot_consumed_false(self):
        store, _ = _store()
        doc = asyncio.run(store.get())
        assert doc["single_shot_consumed"] is False

    def test_empty_store_test_session_id_none(self):
        store, _ = _store()
        doc = asyncio.run(store.get())
        assert doc.get("test_session_id") is None

    def test_empty_store_is_live_order_not_allowed(self):
        store, _ = _store()
        doc = asyncio.run(store.get())
        assert is_live_order_allowed(doc) is False


# ---------------------------------------------------------------------------
# ModeStore — set_mode guards
# ---------------------------------------------------------------------------

class TestSetModeGuards:
    def test_live_armed_raises_value_error(self):
        store, _ = _store()
        with pytest.raises(ValueError, match="LIVE_ARMED"):
            asyncio.run(store.set_mode("LIVE_ARMED"))

    def test_unknown_mode_raises_value_error(self):
        store, _ = _store()
        with pytest.raises(ValueError):
            asyncio.run(store.set_mode("TURBO_YOLO"))

    def test_live_test_without_confirm_raises(self):
        store, _ = _store()
        with pytest.raises(ValueError, match="confirm"):
            asyncio.run(store.set_mode("LIVE_TEST"))

    def test_live_test_confirm_false_raises(self):
        store, _ = _store()
        with pytest.raises(ValueError, match="confirm"):
            asyncio.run(store.set_mode("LIVE_TEST", confirm=False))

    def test_live_test_connected_false_raises(self):
        store, _ = _store()
        with pytest.raises(ValueError, match="connected"):
            asyncio.run(store.set_mode("LIVE_TEST", confirm=True, connected=False))

    def test_live_test_can_trade_false_raises(self):
        store, _ = _store()
        with pytest.raises(ValueError, match="can_trade"):
            asyncio.run(
                store.set_mode("LIVE_TEST", confirm=True, connected=True, can_trade=False)
            )

    def test_live_test_all_guards_passed_succeeds(self):
        store, _ = _store()
        result = asyncio.run(
            store.set_mode(
                "LIVE_TEST",
                confirm=True,
                connected=True,
                can_trade=True,
                now_iso="2026-06-22T09:00:00+00:00",
            )
        )
        assert result["mode"] == "LIVE_TEST"
        assert result["single_shot_consumed"] is False

    def test_set_paper_succeeds_without_confirm(self):
        store, _ = _store()
        result = asyncio.run(store.set_mode("PAPER"))
        assert result["mode"] == "PAPER"

    def test_set_live_offline_succeeds_without_confirm(self):
        store, _ = _store()
        result = asyncio.run(store.set_mode("LIVE_OFFLINE"))
        assert result["mode"] == "LIVE_OFFLINE"

    def test_since_timestamp_stored_on_set_mode(self):
        store, _ = _store()
        asyncio.run(
            store.set_mode(
                "LIVE_TEST",
                confirm=True,
                connected=True,
                can_trade=True,
                now_iso="2026-06-22T09:15:00+00:00",
            )
        )
        doc = asyncio.run(store.get())
        assert doc.get("since") == "2026-06-22T09:15:00+00:00"


# ---------------------------------------------------------------------------
# ModeStore — LIVE_TEST entry + single-shot lifecycle
# ---------------------------------------------------------------------------

class TestLiveTestLifecycle:
    def _enter_live_test(self, store: ModeStore) -> None:
        asyncio.run(
            store.set_mode("LIVE_TEST", confirm=True, connected=True, can_trade=True)
        )

    def test_after_set_live_test_get_shows_live_test(self):
        store, _ = _store()
        self._enter_live_test(store)
        doc = asyncio.run(store.get())
        assert doc["mode"] == "LIVE_TEST"
        assert doc["single_shot_consumed"] is False

    def test_after_set_live_test_order_is_allowed(self):
        store, _ = _store()
        self._enter_live_test(store)
        doc = asyncio.run(store.get())
        assert is_live_order_allowed(doc) is True

    def test_consume_single_shot_blocks_further_orders(self):
        store, _ = _store()
        self._enter_live_test(store)

        asyncio.run(store.consume_single_shot())

        doc = asyncio.run(store.get())
        assert doc["single_shot_consumed"] is True
        assert is_live_order_allowed(doc) is False

    def test_consume_single_shot_is_idempotent(self):
        """Calling consume a second time must not raise."""
        store, _ = _store()
        self._enter_live_test(store)
        asyncio.run(store.consume_single_shot())
        # Must not raise
        asyncio.run(store.consume_single_shot())
        doc = asyncio.run(store.get())
        assert doc["single_shot_consumed"] is True

    def test_re_entry_resets_consumed_flag(self):
        """Setting mode back to PAPER then re-entering LIVE_TEST resets the latch.

        This models the deliberate re-entry workflow: an operator explicitly
        enters PAPER (or LIVE_OFFLINE), then re-arms LIVE_TEST for a new order.
        Each set_mode("LIVE_TEST") writes single_shot_consumed=False.
        """
        store, _ = _store()

        # First LIVE_TEST entry + consume
        self._enter_live_test(store)
        asyncio.run(store.consume_single_shot())
        doc = asyncio.run(store.get())
        assert doc["single_shot_consumed"] is True

        # Revert to PAPER
        asyncio.run(store.set_mode("PAPER"))
        doc = asyncio.run(store.get())
        assert doc["mode"] == "PAPER"

        # Re-enter LIVE_TEST — consumed must reset to False
        self._enter_live_test(store)
        doc = asyncio.run(store.get())
        assert doc["mode"] == "LIVE_TEST"
        assert doc["single_shot_consumed"] is False
        assert is_live_order_allowed(doc) is True


# ---------------------------------------------------------------------------
# ModeStore — revert_to_offline
# ---------------------------------------------------------------------------

class TestRevertToOffline:
    def test_revert_sets_live_offline(self):
        store, _ = _store()
        asyncio.run(
            store.set_mode("LIVE_TEST", confirm=True, connected=True, can_trade=True)
        )
        asyncio.run(store.consume_single_shot())

        result = asyncio.run(store.revert_to_offline())
        assert result["mode"] == "LIVE_OFFLINE"

    def test_revert_clears_single_shot_consumed(self):
        store, _ = _store()
        asyncio.run(
            store.set_mode("LIVE_TEST", confirm=True, connected=True, can_trade=True)
        )
        asyncio.run(store.consume_single_shot())

        asyncio.run(store.revert_to_offline())
        doc = asyncio.run(store.get())
        assert doc["single_shot_consumed"] is False

    def test_revert_clears_test_session_id(self):
        store, col = _store()
        asyncio.run(
            store.set_mode("LIVE_TEST", confirm=True, connected=True, can_trade=True)
        )
        # Manually plant a test_session_id in the stored doc
        for doc in col.docs:
            doc["test_session_id"] = "sess-abc123"

        asyncio.run(store.revert_to_offline())
        doc = asyncio.run(store.get())
        assert doc.get("test_session_id") is None

    def test_revert_stores_since_timestamp(self):
        store, _ = _store()
        asyncio.run(
            store.set_mode("LIVE_TEST", confirm=True, connected=True, can_trade=True)
        )
        asyncio.run(
            store.revert_to_offline(now_iso="2026-06-22T10:00:00+00:00")
        )
        doc = asyncio.run(store.get())
        assert doc.get("since") == "2026-06-22T10:00:00+00:00"

    def test_revert_makes_order_not_allowed(self):
        store, _ = _store()
        asyncio.run(
            store.set_mode("LIVE_TEST", confirm=True, connected=True, can_trade=True)
        )
        asyncio.run(store.revert_to_offline())
        doc = asyncio.run(store.get())
        assert is_live_order_allowed(doc) is False

    def test_revert_from_empty_store_creates_offline_doc(self):
        """revert_to_offline on an empty store must not raise; creates the doc."""
        store, _ = _store()
        result = asyncio.run(store.revert_to_offline())
        assert result["mode"] == "LIVE_OFFLINE"
        assert result["single_shot_consumed"] is False


# ---------------------------------------------------------------------------
# ModeStore — persistence round-trip
# ---------------------------------------------------------------------------

class TestModeStorePersistence:
    """Verify that set_mode/consume writes are visible to a second store
    instance over the same collection — simulating the DB-backed singleton."""

    def test_set_mode_visible_on_second_instance(self):
        col = FakeAsyncCollection()
        store1 = ModeStore(col)
        asyncio.run(
            store1.set_mode("LIVE_TEST", confirm=True, connected=True, can_trade=True)
        )

        store2 = ModeStore(col)  # same underlying collection
        doc = asyncio.run(store2.get())
        assert doc["mode"] == "LIVE_TEST"

    def test_consume_visible_on_second_instance(self):
        col = FakeAsyncCollection()
        store1 = ModeStore(col)
        asyncio.run(
            store1.set_mode("LIVE_TEST", confirm=True, connected=True, can_trade=True)
        )
        asyncio.run(store1.consume_single_shot())

        store2 = ModeStore(col)
        doc = asyncio.run(store2.get())
        assert doc["single_shot_consumed"] is True
        assert is_live_order_allowed(doc) is False


# ---------------------------------------------------------------------------
# is_deployment_live_allowed + armed_until_today_ist — Part A new functions
# ---------------------------------------------------------------------------

from datetime import datetime, timezone, timedelta
from app.live.mode import is_deployment_live_allowed, armed_until_today_ist


def _dep(mode="live", **live):
    """A LIVE-mode deployment. Authorization is `mode`, not an arm record — the
    per-deployment arm ceremony was removed; risk.live is now pure config."""
    base = {"lots": 1, "max_concurrent": 5, "max_lots_per_day": 100}
    base.update(live)
    return {"mode": mode, "risk": {"live": base}}


def test_live_allowed_when_live_mode_connected_before_cutoff():
    now = datetime(2026, 6, 25, 6, 0, tzinfo=timezone.utc)   # 11:30 IST
    assert is_deployment_live_allowed(_dep(), now, connected=True) == (True, "ok")


def test_live_blocked_when_not_live_mode():
    """A paper/signal_only deployment never reaches the real-order path."""
    now = datetime(2026, 6, 25, 6, 0, tzinfo=timezone.utc)
    assert is_deployment_live_allowed(_dep(mode="paper"), now, connected=True) == (False, "not_live_mode")
    assert is_deployment_live_allowed(_dep(mode="signal_only"), now, connected=True) == (False, "not_live_mode")


def test_live_blocked_after_entry_cutoff():
    """15:00 IST new-entry cutoff. This is the explicit replacement for the old
    armed_until expiry and is the ONLY thing stopping a late-session real entry
    from opening minutes before the EOD square runs."""
    now = datetime(2026, 6, 25, 10, 0, tzinfo=timezone.utc)  # 15:30 IST
    assert is_deployment_live_allowed(_dep(), now, connected=True) == (False, "after_entry_cutoff")


def test_live_allowed_immediately_before_cutoff_and_blocked_at_it():
    """Boundary: the cutoff is inclusive-blocking at exactly 15:00 IST."""
    just_before = datetime(2026, 6, 25, 9, 29, tzinfo=timezone.utc)   # 14:59 IST
    exactly_at = datetime(2026, 6, 25, 9, 30, tzinfo=timezone.utc)    # 15:00 IST
    assert is_deployment_live_allowed(_dep(), just_before, connected=True)[0] is True
    assert is_deployment_live_allowed(_dep(), exactly_at, connected=True) == (False, "after_entry_cutoff")


def test_live_blocked_when_not_connected():
    now = datetime(2026, 6, 25, 6, 0, tzinfo=timezone.utc)
    assert is_deployment_live_allowed(_dep(), now, connected=False) == (False, "not_connected")


def test_live_fail_closed_on_malformed():
    now = datetime(2026, 6, 25, 6, 0, tzinfo=timezone.utc)
    assert is_deployment_live_allowed({}, now, connected=True)[0] is False
    assert is_deployment_live_allowed(None, now, connected=True)[0] is False
    assert is_deployment_live_allowed({"risk": {"live": "x"}}, now, connected=True)[0] is False


def test_legacy_armed_record_no_longer_authorizes():
    """MIGRATION SAFETY: a pre-existing doc carrying the old arm record but not
    mode=="live" must NOT trade. Fail-closed is the only safe direction here."""
    now = datetime(2026, 6, 25, 6, 0, tzinfo=timezone.utc)
    legacy = {"mode": "paper",
              "risk": {"live": {"armed": True, "armed_until": "2026-06-25T09:30:00+00:00"}}}
    assert is_deployment_live_allowed(legacy, now, connected=True) == (False, "not_live_mode")


def test_armed_until_today_ist_is_1500_ist_in_utc():
    now = datetime(2026, 6, 25, 4, 0, tzinfo=timezone.utc)  # 09:30 IST
    assert armed_until_today_ist(now) == "2026-06-25T09:30:00+00:00"
