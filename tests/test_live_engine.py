"""TDD tests for backend/app/live/engine.py (Task L2.3).

Coverage
--------
Happy path:
  - on_om(OPEN) → doc state OPEN persisted; engine not halted
  - on_om(COMPLETE fill 65 lots) → doc state COMPLETE, fillshares=65; not halted
  - reconcile_tick() with matching broker book → ok=True, engine not halted

Halt conditions:
  - reconcile_tick with broker-only position → report not ok, engine halted, alert set
  - om with reconcile_required (post-CANCEL fill) → engine halts
  - on_om for unknown norenordno → engine halts
  - halt is sticky: clean reconcile_tick after halt does NOT clear halted

resume_pending:
  - cid "C1" found in broker order_book by remarks → adopted; mark_submitted called
  - cid "C2" not in broker order_book → in needs_submit
  - NO re-submit (place_order never called)

guardrail_tick:
  - mtm below loss limit → action="broker_stop_loss", latch tripped, engine halted
  - can_trade() → (False, reason) when halted
  - clean mtm → action="none", engine not halted, can_trade() → (True, "")
  - can_trade() blocked by latch even when engine not halted
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.live.mock_noren import MockNoren, make_om
from app.live.engine import LiveEngine
from app.live.kill_switch import SafetyConfigStore, DEFAULT_SAFETY_CONFIG
from app.live.idempotency import IntentStore


# ---------------------------------------------------------------------------
# FakeAsyncCollection — in-memory Mongo stand-in (mirrors existing test pattern)
# ---------------------------------------------------------------------------

class _FakeCursor:
    def __init__(self, docs: List[dict]) -> None:
        self._docs = docs

    async def to_list(self, length: Optional[int] = None) -> List[dict]:
        if length is None:
            return list(self._docs)
        return list(self._docs[:length])


class _UpdateResult:
    def __init__(self, matched_count: int) -> None:
        self.matched_count = matched_count


class FakeAsyncCollection:
    """In-memory async collection for test injection."""

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
        if upsert:
            new_doc = dict(query)
            if "$set" in update:
                new_doc.update(update["$set"])
            self.docs.append(new_doc)
            return _UpdateResult(matched_count=1)
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
    for k, v in query.items():
        if doc.get(k) != v:
            return False
    return True


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

def _make_engine(
    *,
    order_book_data: Optional[List[Dict[str, Any]]] = None,
    position_book_data: Optional[List[Dict[str, Any]]] = None,
    initial_orders: Optional[List[Dict[str, Any]]] = None,
    safety_config_overrides: Optional[Dict[str, Any]] = None,
):
    """Build a LiveEngine wired to MockNoren + FakeAsyncCollections."""
    client = MockNoren(
        order_book_data=order_book_data,
        position_book_data=position_book_data,
    )
    orders_col = FakeAsyncCollection()
    if initial_orders:
        orders_col.docs.extend(initial_orders)

    intent_col = FakeAsyncCollection()
    intent_store = IntentStore(intent_col)

    config_col = FakeAsyncCollection()
    if safety_config_overrides:
        config_col.docs.append({"_id": "singleton", **safety_config_overrides})
    config_store = SafetyConfigStore(config_col)

    engine = LiveEngine(
        client=client,
        orders_collection=orders_col,
        intent_store=intent_store,
        config_store=config_store,
    )
    return engine, client, orders_col, intent_col, config_col


def _seed_order(orders_col: FakeAsyncCollection, norenordno: str, state: str, **extra) -> dict:
    """Insert an order doc into the fake collection and return it."""
    doc = {"norenordno": norenordno, "state": state, "qty": 65, **extra}
    orders_col.docs.append(dict(doc))
    return doc


# ---------------------------------------------------------------------------
# Happy path tests
# ---------------------------------------------------------------------------

def test_on_om_open_transitions_state():
    """on_om(OPEN) → doc in collection has state=OPEN; engine not halted."""
    engine, client, orders_col, _, _ = _make_engine()
    _seed_order(orders_col, "MOCK1", "SUBMITTED")

    asyncio.run(
        engine.on_om(make_om("MOCK1", "OPEN", "New"))
    )

    # Doc should be updated to OPEN
    doc = next(d for d in orders_col.docs if d["norenordno"] == "MOCK1")
    assert doc["state"] == "OPEN"
    assert not engine.halted
    assert engine.halt_reason is None


def test_on_om_complete_fill():
    """on_om(COMPLETE fill 65 shares @ 100) → state=COMPLETE, fillshares=65."""
    engine, client, orders_col, _, _ = _make_engine()
    _seed_order(orders_col, "MOCK1", "OPEN")

    asyncio.run(
        engine.on_om(make_om("MOCK1", "COMPLETE", "Fill", fillshares="65", avgprc="100"))
    )

    doc = next(d for d in orders_col.docs if d["norenordno"] == "MOCK1")
    assert doc["state"] == "COMPLETE"
    assert doc["fillshares"] == 65
    assert doc["avgprc"] == pytest.approx(100.0)
    assert not engine.halted


def test_reconcile_tick_clean():
    """reconcile_tick() with matching broker and internal state → ok=True, not halted.

    MockNoren.order_book() returns self._orders (the in-memory dict), NOT the
    order_book_data constructor arg (which is accepted but unused).  We seed the
    broker-side order directly into client._orders.
    """
    engine, client, orders_col, _, _ = _make_engine(position_book_data=[])
    # Seed the broker side: MOCK1 is OPEN at the broker
    client._orders["MOCK1"] = {"norenordno": "MOCK1", "status": "OPEN"}
    # Internal state matches: one OPEN order with norenordno set
    _seed_order(orders_col, "MOCK1", "OPEN")

    report = asyncio.run(engine.reconcile_tick())

    assert report["ok"] is True
    assert report["mismatches"] == []
    assert not engine.halted


# ---------------------------------------------------------------------------
# Halt condition: reconcile mismatch
# ---------------------------------------------------------------------------

def test_reconcile_tick_halts_on_broker_only_position():
    """Broker has a position we have no internal record for → halt."""
    broker_position = {"tsym": "NIFTY25JUN25C25000", "netqty": "65"}
    engine, client, orders_col, _, _ = _make_engine(
        order_book_data=[],
        position_book_data=[broker_position],
    )
    # Internal positions = [] (engine.internal_positions default)

    report = asyncio.run(engine.reconcile_tick())

    assert report["ok"] is False
    assert len(report["mismatches"]) >= 1
    assert engine.halted
    assert engine.halt_reason == "reconcile_mismatch"
    assert len(engine.alerts) >= 1
    assert engine.alerts[0]["reason"] == "reconcile_mismatch"


# ---------------------------------------------------------------------------
# Halt condition: reconcile_required flag from order_sm
# ---------------------------------------------------------------------------

def test_on_om_reconcile_required_halts():
    """Drive a post-CANCEL fill → apply_om sets reconcile_required → engine halts."""
    engine, client, orders_col, _, _ = _make_engine()
    # Doc is in CANCELED state with fillshares=0
    _seed_order(orders_col, "MOCK1", "CANCELED", fillshares=0)

    # A COMPLETE fill arriving on a CANCELED order → post_terminal_fillshares +
    # reconcile_required per order_sm.py invariants
    asyncio.run(
        engine.on_om(make_om("MOCK1", "COMPLETE", "Fill", fillshares="65", avgprc="100"))
    )

    assert engine.halted
    assert engine.halt_reason == "order_sm_flagged"
    alert = engine.alerts[0]
    assert alert["reason"] == "order_sm_flagged"
    assert alert["detail"]["reconcile_required"] is True


# ---------------------------------------------------------------------------
# Halt condition: om for unknown order
# ---------------------------------------------------------------------------

def test_on_om_unknown_order_halts():
    """on_om for a norenordno not in the collection → halt; no phantom doc created."""
    engine, client, orders_col, _, _ = _make_engine()
    # Collection is empty — "MOCK9" is unknown

    asyncio.run(
        engine.on_om(make_om("MOCK9", "OPEN", "New"))
    )

    assert engine.halted
    assert engine.halt_reason == "om_for_unknown_order"
    # No doc should have been created
    assert all(d.get("norenordno") != "MOCK9" for d in orders_col.docs)
    # Alert recorded
    assert engine.alerts[0]["reason"] == "om_for_unknown_order"
    assert engine.alerts[0]["detail"]["norenordno"] == "MOCK9"


# ---------------------------------------------------------------------------
# Halt is sticky
# ---------------------------------------------------------------------------

def test_halt_is_sticky_after_clean_reconcile():
    """After a halt, a subsequent clean reconcile_tick does NOT clear halted."""
    broker_position = {"tsym": "NIFTY25JUN25C25000", "netqty": "65"}
    engine, client, orders_col, _, _ = _make_engine(
        order_book_data=[],
        position_book_data=[broker_position],
    )

    # First tick: mismatched → halts
    asyncio.run(engine.reconcile_tick())
    assert engine.halted
    first_reason = engine.halt_reason

    # Swap broker state to clean (empty position book)
    client.set_position_book([])

    # Second tick: clean
    report2 = asyncio.run(engine.reconcile_tick())
    assert report2["ok"] is True

    # Halt MUST still be set — sticky
    assert engine.halted
    assert engine.halt_reason == first_reason


# ---------------------------------------------------------------------------
# resume_pending — duplicate-order gap closer
# ---------------------------------------------------------------------------

def test_resume_pending_adopts_existing_broker_order():
    """cid "C1" found in broker order_book (remarks match) → adopted via mark_submitted;
    cid "C2" not found → in needs_submit. No place_order called."""
    engine, client, orders_col, intent_col, _ = _make_engine()

    # Seed intent_col with two un-submitted intents
    intent_col.docs.append({
        "client_order_id": "C1",
        "norenordno": None,
        "state": "INTENT",
        "intent": {"remarks": "C1"},
    })
    intent_col.docs.append({
        "client_order_id": "C2",
        "norenordno": None,
        "state": "INTENT",
        "intent": {"remarks": "C2"},
    })

    # MockNoren order_book: only C1 is there (remarks="C1"), norenordno="MOCK9"
    client._orders["MOCK9"] = {
        "norenordno": "MOCK9",
        "remarks": "C1",
        "status": "OPEN",
    }

    result = asyncio.run(engine.resume_pending())

    assert "C1" in result["adopted"]
    assert "C2" in result["needs_submit"]
    assert "C2" not in result["adopted"]
    assert "C1" not in result["needs_submit"]

    # mark_submitted should have updated C1's intent doc
    c1_doc = next(d for d in intent_col.docs if d["client_order_id"] == "C1")
    assert c1_doc["norenordno"] == "MOCK9"

    # No place_order should have been called (MockNoren order counter should be 0)
    # (place_order increments _order_counter)
    assert client._order_counter == 0


def test_resume_pending_empty_broker_all_needs_submit():
    """When broker order_book is empty all pending cids go to needs_submit."""
    engine, client, orders_col, intent_col, _ = _make_engine(
        order_book_data=[],
    )

    intent_col.docs.append({
        "client_order_id": "CX",
        "norenordno": None,
        "state": "INTENT",
        "intent": {"remarks": "CX"},
    })

    result = asyncio.run(engine.resume_pending())

    assert result["needs_submit"] == ["CX"]
    assert result["adopted"] == []


# ---------------------------------------------------------------------------
# guardrail_tick
# ---------------------------------------------------------------------------

def test_guardrail_tick_loss_halts_and_latches():
    """mtm below loss limit → action=broker_stop_loss, latch tripped, engine halted."""
    engine, client, orders_col, _, config_col = _make_engine(
        safety_config_overrides={"daily_loss_limit": 5000, "blocked_until_reset": False}
    )

    # MTM of -6000 breaches the 5000 limit
    action = asyncio.run(
        engine.guardrail_tick(mtm=-6000, open_count=1)
    )

    assert action == "broker_stop_loss"
    assert engine.halted
    assert "broker_stop_loss" in engine.halt_reason

    # Latch should be persisted in config_col
    config_doc = next((d for d in config_col.docs if d.get("_id") == "singleton"), None)
    # SafetyConfigStore.trip() upserts the singleton doc
    assert config_doc is not None
    assert config_doc.get("blocked_until_reset") is True


def test_guardrail_tick_loss_can_trade_blocked():
    """After a guardrail halt, can_trade() returns (False, ...)."""
    engine, client, orders_col, _, _ = _make_engine(
        safety_config_overrides={"daily_loss_limit": 5000}
    )

    asyncio.run(
        engine.guardrail_tick(mtm=-6000, open_count=1)
    )

    ok, reason = asyncio.run(engine.can_trade())
    assert ok is False
    assert reason  # non-empty reason string


def test_guardrail_tick_clean_not_halted():
    """Clean MTM (no breach) → action=none, engine not halted."""
    engine, client, orders_col, _, _ = _make_engine(
        safety_config_overrides={"daily_loss_limit": 5000, "profit_lock_target": 10000}
    )

    action = asyncio.run(
        engine.guardrail_tick(mtm=500, open_count=1)
    )

    assert action == "none"
    assert not engine.halted


def test_guardrail_tick_clean_can_trade():
    """Not halted + latch not set → can_trade() returns (True, "")."""
    engine, client, orders_col, _, _ = _make_engine(
        safety_config_overrides={"daily_loss_limit": 5000, "blocked_until_reset": False}
    )

    asyncio.run(
        engine.guardrail_tick(mtm=500, open_count=1)
    )

    ok, reason = asyncio.run(engine.can_trade())
    assert ok is True
    assert reason == ""


def test_can_trade_blocked_by_latch_without_halt():
    """Latch is set in config but engine is not halted → can_trade returns False."""
    engine, client, orders_col, _, config_col = _make_engine(
        safety_config_overrides={"daily_loss_limit": 5000, "blocked_until_reset": True}
    )
    # Engine has not been halted by any event
    assert not engine.halted

    ok, reason = asyncio.run(engine.can_trade())
    assert ok is False
    assert "blocked" in reason.lower() or "latch" in reason.lower()


# ---------------------------------------------------------------------------
# halt idempotency — first reason preserved on multiple halts
# ---------------------------------------------------------------------------

def test_halt_keeps_first_reason():
    """Multiple _halt calls keep the FIRST reason; subsequent reasons go to alerts."""
    engine, _, _, _, _ = _make_engine()

    engine._halt("first_reason", {"x": 1})
    engine._halt("second_reason", {"x": 2})
    engine._halt("third_reason", {"x": 3})

    assert engine.halt_reason == "first_reason"
    assert engine.halted is True
    # All three alerts are preserved
    assert len(engine.alerts) == 3
    assert engine.alerts[0]["reason"] == "first_reason"
    assert engine.alerts[1]["reason"] == "second_reason"
    assert engine.alerts[2]["reason"] == "third_reason"


# ---------------------------------------------------------------------------
# Full happy-path integration: seed → on_om OPEN → on_om COMPLETE → reconcile
# ---------------------------------------------------------------------------

def test_full_happy_path():
    """Seed SUBMITTED → drive to COMPLETE via two oms → reconcile against broker.

    This is the integration capstone: all three main paths clean, engine not halted.

    MockNoren.order_book() reads self._orders — seed broker state there directly.
    COMPLETE is a terminal broker status so reconcile does not flag it as a
    working-order mismatch.
    """
    engine, client, orders_col, _, _ = _make_engine(position_book_data=[])
    # Seed broker side: MOCK1 COMPLETE (terminal — reconcile won't flag it)
    client._orders["MOCK1"] = {"norenordno": "MOCK1", "status": "COMPLETE"}
    _seed_order(orders_col, "MOCK1", "SUBMITTED")

    # Step 1: OPEN event
    asyncio.run(engine.on_om(make_om("MOCK1", "OPEN", "New")))
    assert not engine.halted

    # Step 2: COMPLETE fill
    asyncio.run(
        engine.on_om(make_om("MOCK1", "COMPLETE", "Fill", fillshares="65", avgprc="100"))
    )
    assert not engine.halted

    doc = next(d for d in orders_col.docs if d["norenordno"] == "MOCK1")
    assert doc["state"] == "COMPLETE"
    assert doc["fillshares"] == 65

    # Step 3: reconcile — COMPLETE terminal orders on both sides, flat positions
    report = asyncio.run(engine.reconcile_tick())
    assert report["ok"] is True
    assert not engine.halted
    assert engine.halt_reason is None
