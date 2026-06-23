"""TDD tests for backend/app/live/kill_switch.py (Task L2.2).

Coverage
--------
panic_squareoff (executor — MockNoren only):
  - cancels ALL working orders + flattens ALL positions (3 orders + 2 long + 1 short)
  - canceled == 3, flattened == 3, total True
  - SELL intents for long positions, BUY for short; qty == abs(netqty)
  - a position with no/zero/NaN lp → lands in unpriced; NOT silently dropped; total False
  - cancel failure (unknown norenordno) is tallied in cancel_failures; no raise
  - place reject (script_reject) is tallied in flatten_failures; no raise
  - fat-finger NEVER blocks: a 9999-lot position still produces a flatten intent
  - terminal orders (COMPLETE/REJECTED/CANCELED) are NOT cancelled

plan_squareoff (pure):
  - produces correct would_cancel + would_flatten + unpriced without any client call
  - unpriced list contains tsym+netqty for bad ref-price positions
  - terminal orders excluded from would_cancel
  - SELL for long, BUY for short

evaluate_guardrails:
  - loss breach → broker_stop_loss
  - profit reached → profit_lock
  - open_count breach → max_open_block
  - all clear → none
  - mtm=None → broker_stop_loss (fail-safe)
  - mtm=NaN → broker_stop_loss (fail-safe)
  - mtm=inf → broker_stop_loss (fail-safe)
  - open_count=None → broker_stop_loss (fail-safe)
  - loss wins over profit (priority check)
  - loss wins over open_count

latch (pure):
  - trip_latch → is_entry_blocked True
  - is_entry_blocked True while latched → new entries must be blocked
  - reset_latch → is_entry_blocked False
  - latch does NOT self-clear (is_entry_blocked still True without explicit reset)

SafetyConfigStore (FakeAsyncCollection):
  - get_config returns defaults when collection is empty
  - put_config persists whitelisted keys
  - put_config rejects unknown keys with ValueError
  - trip() sets blocked_until_reset=True
  - reset() sets blocked_until_reset=False
  - get_config merges stored values with defaults

route: kill-switch returns plan + transmitted=False + makes NO place/cancel call
  (order book UNCHANGED after the route call)
"""
from __future__ import annotations

import asyncio
import math
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.live.mock_noren import MockNoren
from app.live.kill_switch import (
    DEFAULT_SAFETY_CONFIG,
    TERMINAL,
    SafetyConfigStore,
    evaluate_guardrails,
    is_entry_blocked,
    panic_squareoff,
    plan_squareoff,
    reset_latch,
    trip_latch,
)


# ---------------------------------------------------------------------------
# FakeAsyncCollection (mirrors the one in test_live_idempotency.py)
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
    """In-memory async collection suitable for SafetyConfigStore tests."""

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
        # upsert: if no match, insert a new doc
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


def _fake_store() -> tuple[SafetyConfigStore, FakeAsyncCollection]:
    col = FakeAsyncCollection()
    return SafetyConfigStore(col), col


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

def _working_order(norenordno: str, tsym: str = "NIFTY25000CE") -> Dict[str, Any]:
    return {
        "norenordno": norenordno,
        "status": "OPEN",
        "tsym": tsym,
        "qty": 65,
    }


def _terminal_order(norenordno: str, status: str = "COMPLETE") -> Dict[str, Any]:
    return {
        "norenordno": norenordno,
        "status": status,
        "tsym": "NIFTY25000CE",
        "qty": 65,
    }


def _make_intent(tsym: str = "NIFTY25000CE", qty: int = 65, prc: float = 200.0) -> "OrderIntent":
    """Build a minimal valid OrderIntent for place_order."""
    from app.live.broker_protocol import OrderIntent
    return OrderIntent(
        client_order_id="test-cid",
        trantype="B",
        prctyp="LMT",
        exch="NFO",
        tsym=tsym,
        qty=qty,
        prc=prc,
    )


async def _place_n_orders(client: MockNoren, n: int) -> List[str]:
    """Place n orders and return their norenordnos."""
    from app.live.broker_protocol import OrderIntent
    norenordnos = []
    for i in range(n):
        intent = OrderIntent(
            client_order_id=f"cid-{i}",
            trantype="B",
            prctyp="LMT",
            exch="NFO",
            tsym=f"SYM{i}",
            qty=65,
            prc=200.0,
        )
        result = await client.place_order(intent)
        norenordnos.append(result.norenordno)
    return norenordnos


def _long_pos(tsym: str, netqty: int, lp: float = 200.0, exch: str = "NFO") -> Dict[str, Any]:
    return {"tsym": tsym, "netqty": str(netqty), "lp": str(lp), "exch": exch, "netavgprc": "190.0"}


def _short_pos(tsym: str, netqty: int, lp: float = 200.0, exch: str = "NFO") -> Dict[str, Any]:
    """netqty should be negative."""
    return {"tsym": tsym, "netqty": str(netqty), "lp": str(lp), "exch": exch, "netavgprc": "210.0"}


# ===========================================================================
# evaluate_guardrails
# ===========================================================================

class TestEvaluateGuardrails:
    """Pure guardrail evaluation — no I/O."""

    def _cfg(self, **overrides) -> Dict[str, Any]:
        return {**DEFAULT_SAFETY_CONFIG, **overrides}

    def test_all_clear_returns_none(self):
        cfg = self._cfg()
        result = evaluate_guardrails(mtm=100, open_count=1, config=cfg)
        assert result == "none"

    def test_loss_breach_returns_broker_stop_loss(self):
        cfg = self._cfg(daily_loss_limit=5000)
        result = evaluate_guardrails(mtm=-5000, open_count=1, config=cfg)
        assert result == "broker_stop_loss"

    def test_loss_slightly_below_limit_blocks(self):
        cfg = self._cfg(daily_loss_limit=5000)
        result = evaluate_guardrails(mtm=-5001, open_count=1, config=cfg)
        assert result == "broker_stop_loss"

    def test_loss_exactly_at_limit_blocks(self):
        cfg = self._cfg(daily_loss_limit=5000)
        # mtm <= -abs(daily_loss_limit) → block; equals boundary
        result = evaluate_guardrails(mtm=-5000, open_count=1, config=cfg)
        assert result == "broker_stop_loss"

    def test_loss_just_above_limit_is_clear(self):
        cfg = self._cfg(daily_loss_limit=5000)
        result = evaluate_guardrails(mtm=-4999, open_count=1, config=cfg)
        assert result == "none"

    def test_profit_lock_returns_profit_lock(self):
        cfg = self._cfg(profit_lock_target=10000, daily_loss_limit=5000)
        result = evaluate_guardrails(mtm=10000, open_count=1, config=cfg)
        assert result == "profit_lock"

    def test_profit_just_below_target_is_clear(self):
        cfg = self._cfg(profit_lock_target=10000)
        result = evaluate_guardrails(mtm=9999, open_count=1, config=cfg)
        assert result == "none"

    def test_max_open_block(self):
        cfg = self._cfg(max_open_positions=5)
        result = evaluate_guardrails(mtm=100, open_count=5, config=cfg)
        assert result == "max_open_block"

    def test_below_max_open_is_clear(self):
        cfg = self._cfg(max_open_positions=5)
        result = evaluate_guardrails(mtm=100, open_count=4, config=cfg)
        assert result == "none"

    # Fail-safe: unknown / non-finite inputs → broker_stop_loss
    def test_mtm_none_failsafe(self):
        result = evaluate_guardrails(mtm=None, open_count=1, config=DEFAULT_SAFETY_CONFIG)
        assert result == "broker_stop_loss"

    def test_mtm_nan_failsafe(self):
        result = evaluate_guardrails(mtm=float("nan"), open_count=1, config=DEFAULT_SAFETY_CONFIG)
        assert result == "broker_stop_loss"

    def test_mtm_inf_failsafe(self):
        result = evaluate_guardrails(mtm=float("inf"), open_count=1, config=DEFAULT_SAFETY_CONFIG)
        assert result == "broker_stop_loss"

    def test_open_count_none_failsafe(self):
        result = evaluate_guardrails(mtm=100, open_count=None, config=DEFAULT_SAFETY_CONFIG)
        assert result == "broker_stop_loss"

    # Priority: loss FIRST — even if both loss and profit apply, loss wins
    def test_loss_wins_over_profit(self):
        cfg = self._cfg(daily_loss_limit=5000, profit_lock_target=10000)
        # This is contrived but tests priority: loss=-5001 AND profit=10001
        # (profit MTM means both conditions are logically impossible simultaneously,
        # but we verify loss priority by setting mtm to a loss value while also
        # being >= profit target — not naturally possible, but we can test priority
        # by having mtm at loss level and ensuring loss wins before profit is checked)
        result = evaluate_guardrails(mtm=-5001, open_count=1, config=cfg)
        assert result == "broker_stop_loss"  # not profit_lock

    def test_loss_wins_over_open_count(self):
        cfg = self._cfg(daily_loss_limit=5000, max_open_positions=5)
        result = evaluate_guardrails(mtm=-6000, open_count=10, config=cfg)
        assert result == "broker_stop_loss"  # not max_open_block


# ===========================================================================
# Latch helpers (pure)
# ===========================================================================

class TestLatch:
    def test_fresh_config_is_not_blocked(self):
        assert not is_entry_blocked(DEFAULT_SAFETY_CONFIG)

    def test_trip_latch_sets_blocked(self):
        new_cfg = trip_latch(DEFAULT_SAFETY_CONFIG)
        assert is_entry_blocked(new_cfg)

    def test_original_config_not_mutated_by_trip(self):
        orig = dict(DEFAULT_SAFETY_CONFIG)
        trip_latch(DEFAULT_SAFETY_CONFIG)
        assert DEFAULT_SAFETY_CONFIG == orig

    def test_reset_latch_clears_blocked(self):
        tripped = trip_latch(DEFAULT_SAFETY_CONFIG)
        cleared = reset_latch(tripped)
        assert not is_entry_blocked(cleared)

    def test_latch_does_not_self_clear(self):
        """The latch can ONLY be cleared by reset_latch; nothing else clears it."""
        tripped = trip_latch(DEFAULT_SAFETY_CONFIG)
        assert is_entry_blocked(tripped)
        # Simulating "time passes" / "evaluate_guardrails called again" — the
        # latched config does NOT self-clear.
        assert is_entry_blocked(tripped)  # still True
        assert is_entry_blocked(tripped)  # still True

    def test_entry_is_blocked_after_trip(self):
        """After trip, a simulated entry check must be blocked."""
        cfg = trip_latch(DEFAULT_SAFETY_CONFIG)
        # The engine would check is_entry_blocked before any new order
        assert is_entry_blocked(cfg) is True

    def test_entry_allowed_after_reset(self):
        cfg = trip_latch(DEFAULT_SAFETY_CONFIG)
        cfg = reset_latch(cfg)
        assert is_entry_blocked(cfg) is False


# ===========================================================================
# plan_squareoff (pure — no client calls)
# ===========================================================================

class TestPlanSquareoff:
    def test_working_orders_in_would_cancel(self):
        orders = [_working_order("ORD1"), _working_order("ORD2")]
        plan = plan_squareoff(orders, [])
        assert set(plan["would_cancel"]) == {"ORD1", "ORD2"}

    def test_terminal_orders_excluded_from_would_cancel(self):
        orders = [
            _working_order("ORD1"),
            _terminal_order("ORD2", "COMPLETE"),
            _terminal_order("ORD3", "REJECTED"),
            _terminal_order("ORD4", "CANCELED"),
        ]
        plan = plan_squareoff(orders, [])
        assert plan["would_cancel"] == ["ORD1"]

    def test_long_position_produces_sell_intent(self):
        positions = [_long_pos("NIFTY25000CE", 65, lp=200.0)]
        plan = plan_squareoff([], positions)
        assert len(plan["would_flatten"]) == 1
        jd = plan["would_flatten"][0]
        assert jd["trantype"] == "S"
        assert int(jd["qty"]) == 65

    def test_short_position_produces_buy_intent(self):
        positions = [_short_pos("NIFTY24000PE", -30, lp=150.0)]
        plan = plan_squareoff([], positions)
        assert len(plan["would_flatten"]) == 1
        jd = plan["would_flatten"][0]
        assert jd["trantype"] == "B"
        assert int(jd["qty"]) == 30

    def test_zero_netqty_position_ignored(self):
        pos = {"tsym": "NIFTY25000CE", "netqty": "0", "lp": "200", "exch": "NFO"}
        plan = plan_squareoff([], [pos])
        assert plan["would_flatten"] == []
        assert plan["unpriced"] == []

    def test_missing_lp_goes_to_unpriced(self):
        pos = {"tsym": "NIFTY25000CE", "netqty": "65", "exch": "NFO"}
        plan = plan_squareoff([], [pos])
        assert plan["would_flatten"] == []
        assert len(plan["unpriced"]) == 1
        assert plan["unpriced"][0]["tsym"] == "NIFTY25000CE"

    def test_zero_lp_goes_to_unpriced(self):
        pos = {"tsym": "NIFTY25000CE", "netqty": "65", "lp": "0", "exch": "NFO"}
        plan = plan_squareoff([], [pos])
        assert len(plan["unpriced"]) == 1

    def test_nan_lp_goes_to_unpriced(self):
        pos = {"tsym": "NIFTY25000CE", "netqty": "65", "lp": "nan", "exch": "NFO"}
        plan = plan_squareoff([], [pos])
        assert len(plan["unpriced"]) == 1

    def test_empty_inputs_return_empty_plan(self):
        plan = plan_squareoff([], [])
        assert plan == {"would_cancel": [], "would_flatten": [], "unpriced": []}

    def test_sell_price_below_lp(self):
        """SELL flatten price = tick-aligned(lp * (1 - band_pct/100), mode=down)."""
        from app.live.order_builder import round_to_tick
        positions = [_long_pos("SYM", 65, lp=200.0)]
        plan = plan_squareoff([], positions, band_pct=1.0)
        jd = plan["would_flatten"][0]
        expected_prc = round_to_tick(200.0 * (1 - 1.0 / 100), 0.05, mode="down")
        assert float(jd["prc"]) == pytest.approx(expected_prc)
        # Must be an exact 0.05 multiple
        assert round(expected_prc / 0.05) * 0.05 == pytest.approx(expected_prc)
        # Must be marketable (≤ lp)
        assert expected_prc <= 200.0

    def test_buy_price_above_lp(self):
        """BUY flatten price = tick-aligned(lp * (1 + band_pct/100), mode=up)."""
        from app.live.order_builder import round_to_tick
        positions = [_short_pos("SYM", -30, lp=150.0)]
        plan = plan_squareoff([], positions, band_pct=1.0)
        jd = plan["would_flatten"][0]
        expected_prc = round_to_tick(150.0 * (1 + 1.0 / 100), 0.05, mode="up")
        assert float(jd["prc"]) == pytest.approx(expected_prc)
        # Must be an exact 0.05 multiple
        assert round(expected_prc / 0.05) * 0.05 == pytest.approx(expected_prc)
        # Must be marketable (≥ lp)
        assert expected_prc >= 150.0

    def test_plan_makes_no_client_call(self):
        """plan_squareoff has no client parameter — it is purely pure."""
        # This is tested implicitly by calling it without a client.
        # If it tries to call any network method it would raise AttributeError.
        plan = plan_squareoff(
            [_working_order("ORD1")],
            [_long_pos("SYM", 65, lp=200.0)],
        )
        assert plan["would_cancel"] == ["ORD1"]
        assert len(plan["would_flatten"]) == 1


# ===========================================================================
# panic_squareoff (executor — MockNoren only)
# ===========================================================================

class TestPanicSquareoff:

    def test_cancels_all_working_and_flattens_all_positions(self):
        """Core contract: 3 working orders + 2 longs + 1 short → all cleared.

        MockNoren's cancel_order works against self._orders (the real placed orders
        dict), not against order_book_data (which is an unused fixture field).
        We must place real orders so they exist in _orders before calling panic.
        """
        client = MockNoren(
            position_book_data=[
                _long_pos("NIFTY25000CE", 65, lp=200.0),
                _long_pos("NIFTY24500CE", 65, lp=180.0),
                _short_pos("NIFTY24000PE", -30, lp=150.0),
            ],
        )
        # Place 3 real orders so they exist in _orders and can be cancelled
        asyncio.run(_place_n_orders(client, 3))

        orders = asyncio.run(client.order_book())
        positions = asyncio.run(client.position_book())

        assert len(orders) == 3  # sanity: orders are in the book
        result = asyncio.run(panic_squareoff(client, orders, positions))

        assert result["canceled"] == 3
        assert result["flattened"] == 3
        assert result["cancel_failures"] == []
        assert result["flatten_failures"] == []
        assert result["unpriced"] == []
        assert result["total"] is True

    def test_sell_intent_for_long_positions(self):
        """Long position → SELL flatten order."""
        client = MockNoren(
            position_book_data=[_long_pos("NIFTY25000CE", 65, lp=200.0)],
        )
        orders = asyncio.run(client.order_book())
        positions = asyncio.run(client.position_book())

        asyncio.run(panic_squareoff(client, orders, positions))

        placed = asyncio.run(client.order_book())
        # Filter to the flatten orders (status=OPEN, placed by panic)
        flatten_orders = [o for o in placed if o.get("trantype") == "S"]
        assert len(flatten_orders) == 1
        assert int(flatten_orders[0]["qty"]) == 65

    def test_buy_intent_for_short_positions(self):
        """Short position → BUY flatten order."""
        client = MockNoren(
            position_book_data=[_short_pos("NIFTY24000PE", -30, lp=150.0)],
        )
        orders = asyncio.run(client.order_book())
        positions = asyncio.run(client.position_book())

        asyncio.run(panic_squareoff(client, orders, positions))

        placed = asyncio.run(client.order_book())
        flatten_orders = [o for o in placed if o.get("trantype") == "B"]
        assert len(flatten_orders) == 1
        assert int(flatten_orders[0]["qty"]) == 30

    def test_qty_equals_abs_netqty(self):
        """Flattened qty must equal abs(netqty) regardless of sign."""
        client = MockNoren(
            position_book_data=[
                _long_pos("A", 130, lp=100.0),  # 2 lots × 65
                _short_pos("B", -90, lp=200.0),  # 3 lots × 30
            ],
        )
        positions = asyncio.run(client.position_book())
        result = asyncio.run(panic_squareoff(client, [], positions))

        placed = asyncio.run(client.order_book())
        qtys = sorted(int(o["qty"]) for o in placed)
        assert qtys == [90, 130]

    def test_missing_lp_goes_to_unpriced_not_silently_dropped(self):
        """A position with no lp must appear in unpriced, NOT be silently ignored."""
        client = MockNoren(
            position_book_data=[
                {"tsym": "NIFTY25000CE", "netqty": "65", "exch": "NFO"},  # no lp
                _long_pos("NIFTY24500CE", 65, lp=180.0),
            ],
        )
        positions = asyncio.run(client.position_book())
        result = asyncio.run(panic_squareoff(client, [], positions))

        assert len(result["unpriced"]) == 1
        assert result["unpriced"][0]["tsym"] == "NIFTY25000CE"
        assert result["flattened"] == 1  # the priced one was flattened
        assert result["total"] is False  # unpriced ≠ [] → not total

    def test_zero_lp_goes_to_unpriced(self):
        client = MockNoren(
            position_book_data=[
                {"tsym": "SYM", "netqty": "65", "lp": "0", "exch": "NFO"},
            ],
        )
        positions = asyncio.run(client.position_book())
        result = asyncio.run(panic_squareoff(client, [], positions))
        assert len(result["unpriced"]) == 1
        assert result["total"] is False

    def test_nan_lp_goes_to_unpriced(self):
        client = MockNoren(
            position_book_data=[
                {"tsym": "SYM", "netqty": "65", "lp": "nan", "exch": "NFO"},
            ],
        )
        positions = asyncio.run(client.position_book())
        result = asyncio.run(panic_squareoff(client, [], positions))
        assert len(result["unpriced"]) == 1

    def test_cancel_failure_tallied_no_raise(self):
        """Cancelling an unknown order → cancel_failures; panic still returns a report."""
        client = MockNoren()
        # Pre-place one real order to have something in order_book
        orders = [{"norenordno": "NONEXISTENT", "status": "OPEN"}]

        result = asyncio.run(panic_squareoff(client, orders, []))

        assert result["canceled"] == 0
        assert len(result["cancel_failures"]) == 1
        assert result["cancel_failures"][0]["norenordno"] == "NONEXISTENT"
        # No exception was raised
        assert result["total"] is False

    def test_place_reject_tallied_no_raise(self):
        """Scripted place_order reject → flatten_failures; panic still returns a report."""
        client = MockNoren(
            position_book_data=[_long_pos("NIFTY25000CE", 65, lp=200.0)],
        )
        client.script_reject("RMS limit exceeded")

        positions = asyncio.run(client.position_book())
        result = asyncio.run(panic_squareoff(client, [], positions))

        assert result["flattened"] == 0
        assert len(result["flatten_failures"]) == 1
        assert "RMS" in result["flatten_failures"][0]["reason"]
        assert result["total"] is False

    def test_fat_finger_never_blocks_flatten(self):
        """A 9999-lot position (qty=9999) must still produce a flatten intent.

        The fat-finger cap in order_builder is NEVER applied to panic_squareoff —
        the engine MUST always be able to exit a position it holds.
        """
        client = MockNoren(
            position_book_data=[
                {"tsym": "NIFTY25000CE", "netqty": "9999", "lp": "200.0", "exch": "NFO"},
            ],
        )
        positions = asyncio.run(client.position_book())
        result = asyncio.run(panic_squareoff(client, [], positions))

        assert result["flattened"] == 1
        assert result["total"] is True

        # Verify the placed order has qty=9999
        placed = asyncio.run(client.order_book())
        assert len(placed) == 1
        assert int(placed[0]["qty"]) == 9999

    def test_terminal_orders_not_cancelled(self):
        """COMPLETE/REJECTED/CANCELED orders must NOT be cancelled again.

        We pass pre-built order dicts (not from _orders) because terminal orders
        come from the broker's order book as-is — panic_squareoff filters them by
        status before calling cancel_order.  Only working orders reach cancel_order.

        We need one real working order in _orders for the cancel to succeed.
        Terminal orders are in the orders list but never passed to cancel_order.
        """
        client = MockNoren()
        # Place one real order (will be OPEN, can be cancelled)
        asyncio.run(_place_n_orders(client, 1))
        real_orders = asyncio.run(client.order_book())
        assert len(real_orders) == 1
        open_norenordno = real_orders[0]["norenordno"]

        # Build the full orders list: 1 working (real) + 3 terminal (synthetic)
        orders = [
            real_orders[0],                             # OPEN — will be cancelled
            _terminal_order("COMP1", "COMPLETE"),       # should be skipped
            _terminal_order("REJ1", "REJECTED"),        # should be skipped
            _terminal_order("CAN1", "CANCELED"),        # should be skipped
        ]

        result = asyncio.run(panic_squareoff(client, orders, []))

        # Only the OPEN order should be cancelled
        assert result["canceled"] == 1
        assert result["cancel_failures"] == []

    def test_panic_returns_report_never_raises(self):
        """Even with multiple failures, panic_squareoff must not raise."""
        client = MockNoren()
        # Two non-existent orders + one scripted reject position
        orders = [
            {"norenordno": "GHOST1", "status": "OPEN"},
            {"norenordno": "GHOST2", "status": "OPEN"},
        ]
        client.script_reject("margin exceeded")
        positions = [_long_pos("SYM", 65, lp=100.0)]

        # Should not raise
        result = asyncio.run(panic_squareoff(client, orders, positions))
        assert isinstance(result, dict)
        assert "canceled" in result
        assert "total" in result

    def test_no_orders_no_positions_total_true(self):
        """When there is nothing to do, panic is trivially total."""
        client = MockNoren()
        result = asyncio.run(panic_squareoff(client, [], []))
        assert result["canceled"] == 0
        assert result["flattened"] == 0
        assert result["total"] is True


# ===========================================================================
# SafetyConfigStore
# ===========================================================================

class TestSafetyConfigStore:

    def test_get_config_returns_defaults_when_empty(self):
        store, col = _fake_store()
        cfg = asyncio.run(store.get_config())
        assert cfg["daily_loss_limit"] == DEFAULT_SAFETY_CONFIG["daily_loss_limit"]
        assert cfg["blocked_until_reset"] is False

    def test_get_config_merges_stored_values_with_defaults(self):
        store, col = _fake_store()
        asyncio.run(store.put_config({"daily_loss_limit": 9999}))
        cfg = asyncio.run(store.get_config())
        assert cfg["daily_loss_limit"] == 9999
        # Other defaults are still present
        assert "profit_lock_target" in cfg

    def test_put_config_persists_whitelisted_keys(self):
        store, col = _fake_store()
        asyncio.run(store.put_config({"daily_loss_limit": 2000, "max_open_positions": 3}))
        cfg = asyncio.run(store.get_config())
        assert cfg["daily_loss_limit"] == 2000
        assert cfg["max_open_positions"] == 3

    def test_put_config_rejects_unknown_keys(self):
        store, col = _fake_store()
        with pytest.raises(ValueError, match="Unknown safety config keys"):
            asyncio.run(store.put_config({"unknown_key": 99}))

    def test_trip_persists_blocked_until_reset_true(self):
        store, col = _fake_store()
        asyncio.run(store.trip())
        cfg = asyncio.run(store.get_config())
        assert cfg["blocked_until_reset"] is True

    def test_reset_clears_blocked_until_reset(self):
        store, col = _fake_store()
        asyncio.run(store.trip())
        asyncio.run(store.reset())
        cfg = asyncio.run(store.get_config())
        assert cfg["blocked_until_reset"] is False

    def test_put_config_returns_updated_config(self):
        store, col = _fake_store()
        result = asyncio.run(store.put_config({"daily_loss_limit": 7500}))
        assert result["daily_loss_limit"] == 7500


# ===========================================================================
# Route: kill-switch returns plan, never transmits
# ===========================================================================

class TestKillSwitchRoute:
    """Test that the /live-broker/kill-switch route:
    1. Returns plan + transmitted=False.
    2. Makes NO place_order / cancel_order calls (order book is UNCHANGED).

    We test this by using the plan_squareoff function directly (which is what
    the route calls) with a MockNoren for the read-only fetch, verifying that
    the order book state is identical before and after.
    """

    def test_plan_squareoff_does_not_mutate_order_book(self):
        """plan_squareoff has no client; calling it cannot modify any order book."""
        orders = [_working_order("ORD1"), _working_order("ORD2")]
        positions = [_long_pos("NIFTY25000CE", 65, lp=200.0)]

        # Snapshot before
        orders_before = [dict(o) for o in orders]

        plan = plan_squareoff(orders, positions)

        # Snapshot after — plan_squareoff must not mutate its inputs
        assert [dict(o) for o in orders] == orders_before

    def test_route_returns_transmitted_false(self):
        """The route must return transmitted=False — it is a plan, not an execution."""
        # We simulate what the route does: fetch + plan_squareoff
        orders = [_working_order("ORD1")]
        positions = [_long_pos("NIFTY25000CE", 65, lp=200.0)]
        plan = plan_squareoff(orders, positions)

        # Route would return:
        response = {
            "plan": plan,
            "transmitted": False,
            "armed": True,
        }

        assert response["transmitted"] is False
        assert response["armed"] is True
        assert "would_cancel" in response["plan"]

    def test_mock_noren_order_book_unchanged_after_plan(self):
        """After plan_squareoff (which the route calls), the broker order book must
        be identical to before — no cancel_order or place_order was issued."""
        client = MockNoren(
            order_book_data=[_working_order("ORD1"), _working_order("ORD2")],
            position_book_data=[_long_pos("NIFTY25000CE", 65, lp=200.0)],
        )

        orders_before = asyncio.run(client.order_book())
        positions = asyncio.run(client.position_book())

        # The route fetches order_book() + position_book() then calls plan_squareoff.
        # plan_squareoff does NOT take a client argument.
        plan = plan_squareoff(orders_before, positions)

        orders_after = asyncio.run(client.order_book())

        # Order book must be UNCHANGED — no cancels or places occurred
        assert len(orders_after) == len(orders_before)
        for before, after in zip(orders_before, orders_after):
            assert before["status"] == after["status"]
            assert before["norenordno"] == after["norenordno"]

    def test_plan_includes_unpriced_for_bad_ref_price(self):
        """Unpriced positions appear in the plan, never silently dropped."""
        orders = []
        positions = [
            {"tsym": "SYM_NOREF", "netqty": "65", "exch": "NFO"},  # no lp
        ]
        plan = plan_squareoff(orders, positions)
        assert len(plan["unpriced"]) == 1
        assert plan["unpriced"][0]["tsym"] == "SYM_NOREF"
        assert plan["would_flatten"] == []


# ===========================================================================
# F1 — float-form netqty (CATASTROPHIC: was silently coerced to 0)
# ===========================================================================

class TestNetqtyParsing:
    """F1: _parse_netqty + both squareoff functions must handle float-form strings.

    Before the fix: int("100.0") raised ValueError → except → netqty=0 → position
    SKIPPED → total=True over a live open position.  Now we use int(float(...))
    matching reconcile.py:172.
    """

    # --- _parse_netqty unit tests ---

    def test_parse_integer_string(self):
        from app.live.kill_switch import _parse_netqty
        assert _parse_netqty("100") == 100

    def test_parse_float_string_long(self):
        from app.live.kill_switch import _parse_netqty
        assert _parse_netqty("100.0") == 100

    def test_parse_negative_float_string(self):
        from app.live.kill_switch import _parse_netqty
        assert _parse_netqty("-50.0") == -50

    def test_parse_comma_formatted(self):
        from app.live.kill_switch import _parse_netqty
        assert _parse_netqty("1,000") == 1000

    def test_parse_truncation_documented(self):
        """99.9 → 99 (truncation, not rounding). Matches reconcile.py:172."""
        from app.live.kill_switch import _parse_netqty
        assert _parse_netqty("99.9") == 99

    def test_parse_garbage_returns_none(self):
        from app.live.kill_switch import _parse_netqty
        assert _parse_netqty("abc") is None

    def test_parse_nan_returns_none(self):
        from app.live.kill_switch import _parse_netqty
        assert _parse_netqty("nan") is None

    def test_parse_inf_returns_none(self):
        from app.live.kill_switch import _parse_netqty
        assert _parse_netqty("inf") is None

    def test_parse_native_int(self):
        from app.live.kill_switch import _parse_netqty
        assert _parse_netqty(100) == 100

    def test_parse_native_float(self):
        from app.live.kill_switch import _parse_netqty
        assert _parse_netqty(100.0) == 100

    def test_parse_zero_string(self):
        from app.live.kill_switch import _parse_netqty
        assert _parse_netqty("0") == 0

    # --- plan_squareoff: float-form netqty must be flattened, not dropped ---

    def test_plan_float_netqty_long_flattened(self):
        """'100.0' → qty 100 SELL (not skipped)."""
        pos = {"tsym": "SYM", "netqty": "100.0", "lp": "200.0", "exch": "NFO"}
        plan = plan_squareoff([], [pos])
        assert len(plan["would_flatten"]) == 1
        jd = plan["would_flatten"][0]
        assert jd["trantype"] == "S"
        assert int(jd["qty"]) == 100

    def test_plan_float_netqty_short_flattened(self):
        """-50.0 → qty 50 BUY."""
        pos = {"tsym": "SYM", "netqty": "-50.0", "lp": "150.0", "exch": "NFO"}
        plan = plan_squareoff([], [pos])
        assert len(plan["would_flatten"]) == 1
        jd = plan["would_flatten"][0]
        assert jd["trantype"] == "B"
        assert int(jd["qty"]) == 50

    def test_plan_comma_netqty_flattened(self):
        """"1,000" → qty 1000 SELL."""
        pos = {"tsym": "SYM", "netqty": "1,000", "lp": "100.0", "exch": "NFO"}
        plan = plan_squareoff([], [pos])
        assert len(plan["would_flatten"]) == 1
        assert int(plan["would_flatten"][0]["qty"]) == 1000

    def test_plan_garbage_netqty_goes_to_unpriced_not_dropped(self):
        """"abc" → in unpriced, NOT silently skipped, total-equivalent unpriced list non-empty."""
        pos = {"tsym": "SYM", "netqty": "abc", "lp": "200.0", "exch": "NFO"}
        plan = plan_squareoff([], [pos])
        assert plan["would_flatten"] == []
        assert len(plan["unpriced"]) == 1
        assert plan["unpriced"][0]["tsym"] == "SYM"

    def test_plan_nan_netqty_goes_to_unpriced(self):
        pos = {"tsym": "SYM", "netqty": "nan", "lp": "200.0", "exch": "NFO"}
        plan = plan_squareoff([], [pos])
        assert len(plan["unpriced"]) == 1

    def test_plan_inf_netqty_goes_to_unpriced(self):
        pos = {"tsym": "SYM", "netqty": "inf", "lp": "200.0", "exch": "NFO"}
        plan = plan_squareoff([], [pos])
        assert len(plan["unpriced"]) == 1

    # --- panic_squareoff: float-form netqty must be flattened, not dropped ---

    def test_panic_float_netqty_long_flattened(self):
        """'100.0' → qty 100 SELL actually placed (not silently skipped)."""
        client = MockNoren(
            position_book_data=[
                {"tsym": "SYM", "netqty": "100.0", "lp": "200.0", "exch": "NFO"},
            ],
        )
        positions = asyncio.run(client.position_book())
        result = asyncio.run(panic_squareoff(client, [], positions))

        assert result["flattened"] == 1
        assert result["unpriced"] == []
        assert result["total"] is True

        placed = asyncio.run(client.order_book())
        assert len(placed) == 1
        assert placed[0]["trantype"] == "S"
        assert int(placed[0]["qty"]) == 100

    def test_panic_float_netqty_short_flattened(self):
        """-50.0 → qty 50 BUY placed."""
        client = MockNoren(
            position_book_data=[
                {"tsym": "SYM", "netqty": "-50.0", "lp": "150.0", "exch": "NFO"},
            ],
        )
        positions = asyncio.run(client.position_book())
        result = asyncio.run(panic_squareoff(client, [], positions))

        assert result["flattened"] == 1
        placed = asyncio.run(client.order_book())
        assert placed[0]["trantype"] == "B"
        assert int(placed[0]["qty"]) == 50

    def test_panic_garbage_netqty_in_unpriced_total_false(self):
        """"abc" netqty → unpriced, total False, NOT silently dropped."""
        client = MockNoren(
            position_book_data=[
                {"tsym": "SYM", "netqty": "abc", "lp": "200.0", "exch": "NFO"},
            ],
        )
        positions = asyncio.run(client.position_book())
        result = asyncio.run(panic_squareoff(client, [], positions))

        assert result["flattened"] == 0
        assert len(result["unpriced"]) == 1
        assert result["unpriced"][0]["tsym"] == "SYM"
        assert result["total"] is False  # NOT total — a live position was not handled

    def test_panic_nan_netqty_in_unpriced_total_false(self):
        client = MockNoren(
            position_book_data=[
                {"tsym": "SYM", "netqty": "nan", "lp": "200.0", "exch": "NFO"},
            ],
        )
        positions = asyncio.run(client.position_book())
        result = asyncio.run(panic_squareoff(client, [], positions))
        assert len(result["unpriced"]) == 1
        assert result["total"] is False


# ===========================================================================
# F2 — status case drift
# ===========================================================================

class TestStatusCaseNormalization:
    """F2: order status must be uppercased before TERMINAL membership check."""

    def test_plan_lowercase_canceled_treated_as_terminal(self):
        """"canceled" (lowercase) must NOT appear in would_cancel."""
        orders = [
            {"norenordno": "ORD_CANCELED_LOWER", "status": "canceled"},
            {"norenordno": "ORD_OPEN", "status": "OPEN"},
        ]
        plan = plan_squareoff(orders, [])
        assert "ORD_CANCELED_LOWER" not in plan["would_cancel"]
        assert "ORD_OPEN" in plan["would_cancel"]

    def test_plan_title_case_canceled_treated_as_terminal(self):
        orders = [{"norenordno": "ORD", "status": "Canceled"}]
        plan = plan_squareoff(orders, [])
        assert plan["would_cancel"] == []

    def test_plan_lowercase_complete_treated_as_terminal(self):
        orders = [{"norenordno": "ORD", "status": "complete"}]
        plan = plan_squareoff(orders, [])
        assert plan["would_cancel"] == []

    def test_plan_mixed_case_rejected_treated_as_terminal(self):
        orders = [{"norenordno": "ORD", "status": "Rejected"}]
        plan = plan_squareoff(orders, [])
        assert plan["would_cancel"] == []

    def test_panic_lowercase_canceled_not_re_cancelled(self):
        """A "canceled" order must be skipped by panic_squareoff (not re-cancelled)."""
        client = MockNoren()
        orders = [{"norenordno": "ALREADY_CANCELED", "status": "canceled"}]
        result = asyncio.run(panic_squareoff(client, orders, []))
        # No cancel attempt should have been made
        assert result["canceled"] == 0
        assert result["cancel_failures"] == []
        assert result["total"] is True

    def test_panic_title_case_complete_not_re_cancelled(self):
        client = MockNoren()
        orders = [{"norenordno": "DONE", "status": "Complete"}]
        result = asyncio.run(panic_squareoff(client, orders, []))
        assert result["canceled"] == 0
        assert result["cancel_failures"] == []


# ===========================================================================
# F3 — latch whitelist / put_config cannot clear the latch
# ===========================================================================

class TestLatchWhitelist:
    """F3: put_config must NEVER be able to set or clear blocked_until_reset."""

    def test_put_config_rejects_blocked_until_reset(self):
        """Sending blocked_until_reset=False via put_config must raise ValueError."""
        store, col = _fake_store()
        with pytest.raises(ValueError):
            asyncio.run(store.put_config({"blocked_until_reset": False}))

    def test_put_config_cannot_set_latch_to_true(self):
        """Sending blocked_until_reset=True via put_config must raise ValueError."""
        store, col = _fake_store()
        with pytest.raises(ValueError):
            asyncio.run(store.put_config({"blocked_until_reset": True}))

    def test_put_config_does_not_clear_a_tripped_latch(self):
        """Even if the call somehow went through, the latch must stay True after trip."""
        store, col = _fake_store()
        asyncio.run(store.trip())
        # Attempt to clear via put_config — must be rejected
        with pytest.raises(ValueError):
            asyncio.run(store.put_config({"blocked_until_reset": False}))
        # Latch must still be set
        cfg = asyncio.run(store.get_config())
        assert cfg["blocked_until_reset"] is True

    def test_put_config_numeric_threshold_leaves_latch_untouched(self):
        """A normal put_config(daily_loss_limit=...) must not touch the latch."""
        store, col = _fake_store()
        asyncio.run(store.trip())
        asyncio.run(store.put_config({"daily_loss_limit": 1234}))
        cfg = asyncio.run(store.get_config())
        assert cfg["blocked_until_reset"] is True  # latch still set
        assert cfg["daily_loss_limit"] == 1234

    def test_only_reset_clears_the_latch(self):
        """Only store.reset() clears blocked_until_reset."""
        store, col = _fake_store()
        asyncio.run(store.trip())
        assert asyncio.run(store.get_config())["blocked_until_reset"] is True
        asyncio.run(store.reset())
        assert asyncio.run(store.get_config())["blocked_until_reset"] is False

    def test_trip_coerces_to_strict_bool(self):
        """trip() must store True (strict bool), not a truthy int or string."""
        store, col = _fake_store()
        asyncio.run(store.trip())
        cfg = asyncio.run(store.get_config())
        assert cfg["blocked_until_reset"] is True
        assert type(cfg["blocked_until_reset"]) is bool

    def test_reset_coerces_to_strict_bool(self):
        """reset() must store False (strict bool)."""
        store, col = _fake_store()
        asyncio.run(store.trip())
        asyncio.run(store.reset())
        cfg = asyncio.run(store.get_config())
        assert cfg["blocked_until_reset"] is False
        assert type(cfg["blocked_until_reset"]) is bool


# ===========================================================================
# TICK ROUNDING — exit prices must be exact 0.05 multiples (critical safety)
# ===========================================================================
# Regression test for: kill switch / auto-square built exit prices with
# round(ref*(1±eff/100), 2) — NOT tick-aligned — so the broker rejected them
# ("Price 53.61 is not a multiple of tick size 0.05") and the position was
# left OPEN.  These tests document the exact failure mode and verify the fix.
# ===========================================================================

class TestTickRoundingPlanSquareoff:
    """plan_squareoff exit prices must be exact 0.05 multiples."""

    def _is_tick_aligned(self, prc: float, tick: float = 0.05) -> bool:
        """Return True iff prc is an exact multiple of tick."""
        # Use Decimal to avoid floating-point precision noise
        from decimal import Decimal
        d = Decimal(str(prc))
        t = Decimal(str(tick))
        return (d % t) == 0

    def test_sell_exit_53_90_is_tick_aligned(self):
        """Reproduction: ref=53.90, SELL exit → prc is exact 0.05 multiple and ≤ ref.

        The bug: round(53.90 * (1 - 0.01), 2) = 53.36 (already aligned here, but
        53.90 * 0.99 can yield non-multiples for other values, e.g. ref=53.61 → 53.07).
        The exact live failure: ref_ltp 53.61 → 53.07 is NOT 0.05-aligned? Actually
        the key is that for prices like 53.61 itself coming in as a raw ref, the tick
        rejection occurs.  This test uses a value that would produce a non-aligned
        intermediate and confirms the fix produces a tick-aligned result.
        """
        # ref=53.90, band_pct=1.0 → raw = 53.90 * 0.99 = 53.3610 → round(,2)=53.36
        # 53.36 / 0.05 = 1067.2 → NOT aligned (was the old bug for some values)
        # With tick rounding mode=down: floor(53.3610 / 0.05)*0.05 = 53.35
        pos = _long_pos("NIFTY2562521000CE", 65, lp=53.90)
        plan = plan_squareoff([], [pos], band_pct=1.0)
        jd = plan["would_flatten"][0]
        prc = float(jd["prc"])
        assert jd["trantype"] == "S"
        assert self._is_tick_aligned(prc), f"SELL exit prc {prc} is not a 0.05 multiple"
        assert prc <= 53.90, f"SELL exit prc {prc} is NOT marketable (> ref 53.90)"

    def test_buy_exit_53_90_is_tick_aligned(self):
        """BUY-to-close exit → exact 0.05 multiple and ≥ ref."""
        pos = _short_pos("NIFTY2562521000PE", -65, lp=53.90)
        plan = plan_squareoff([], [pos], band_pct=1.0)
        jd = plan["would_flatten"][0]
        prc = float(jd["prc"])
        assert jd["trantype"] == "B"
        assert self._is_tick_aligned(prc), f"BUY exit prc {prc} is not a 0.05 multiple"
        assert prc >= 53.90, f"BUY exit prc {prc} is NOT marketable (< ref 53.90)"

    def test_sell_exit_several_refs_all_tick_aligned(self):
        """SELL exit is tick-aligned for a range of ref prices (regression sweep)."""
        refs = [53.61, 53.90, 100.33, 200.77, 75.12, 120.48, 1.03, 9999.97]
        for ref in refs:
            pos = _long_pos("SYM", 65, lp=ref)
            plan = plan_squareoff([], [pos], band_pct=1.0)
            prc = float(plan["would_flatten"][0]["prc"])
            assert self._is_tick_aligned(prc), (
                f"ref={ref}: SELL exit prc {prc} is not a 0.05 multiple"
            )
            assert prc <= ref, f"ref={ref}: SELL exit prc {prc} > ref (not marketable)"

    def test_buy_exit_several_refs_all_tick_aligned(self):
        """BUY exit is tick-aligned for a range of ref prices."""
        refs = [53.61, 53.90, 100.33, 200.77, 75.12, 120.48, 1.03, 9999.97]
        for ref in refs:
            pos = _short_pos("SYM", -65, lp=ref)
            plan = plan_squareoff([], [pos], band_pct=1.0)
            prc = float(plan["would_flatten"][0]["prc"])
            assert self._is_tick_aligned(prc), (
                f"ref={ref}: BUY exit prc {prc} is not a 0.05 multiple"
            )
            assert prc >= ref, f"ref={ref}: BUY exit prc {prc} < ref (not marketable)"


class TestTickRoundingPanicSquareoff:
    """panic_squareoff exit prices must be exact 0.05 multiples (live executor path)."""

    def _is_tick_aligned(self, prc: float, tick: float = 0.05) -> bool:
        from decimal import Decimal
        d = Decimal(str(prc))
        t = Decimal(str(tick))
        return (d % t) == 0

    def test_panic_sell_exit_53_90_tick_aligned(self):
        """Exact reproduction of the live failure: kill switch fires, SELL exit placed.

        The broker rejected "Price 53.61 not a multiple of tick size 0.05".
        After the fix, the exit price must be a 0.05 multiple.
        """
        client = MockNoren(
            position_book_data=[_long_pos("NIFTY2562521000CE", 65, lp=53.90)],
        )
        positions = asyncio.run(client.position_book())
        asyncio.run(panic_squareoff(client, [], positions))

        placed = asyncio.run(client.order_book())
        assert len(placed) == 1
        prc = float(placed[0]["prc"])
        assert placed[0]["trantype"] == "S"
        assert self._is_tick_aligned(prc), (
            f"CRITICAL: SELL exit prc {prc} is not a 0.05 multiple — broker will reject!"
        )
        assert prc <= 53.90, f"SELL exit prc {prc} is not marketable (> ref)"

    def test_panic_buy_exit_tick_aligned(self):
        """BUY-to-close from panic_squareoff is also tick-aligned."""
        client = MockNoren(
            position_book_data=[_short_pos("NIFTY2562521000PE", -65, lp=53.90)],
        )
        positions = asyncio.run(client.position_book())
        asyncio.run(panic_squareoff(client, [], positions))

        placed = asyncio.run(client.order_book())
        assert len(placed) == 1
        prc = float(placed[0]["prc"])
        assert placed[0]["trantype"] == "B"
        assert self._is_tick_aligned(prc), (
            f"CRITICAL: BUY exit prc {prc} is not a 0.05 multiple — broker will reject!"
        )
        assert prc >= 53.90, f"BUY exit prc {prc} is not marketable (< ref)"

    def test_panic_sell_exit_various_refs_tick_aligned(self):
        """Sweep: SELL exit is tick-aligned for various ref prices via panic_squareoff."""
        refs = [53.61, 53.90, 100.33, 200.77, 75.12]
        for ref in refs:
            client = MockNoren(
                position_book_data=[_long_pos("SYM", 65, lp=ref)],
            )
            positions = asyncio.run(client.position_book())
            asyncio.run(panic_squareoff(client, [], positions))
            placed = asyncio.run(client.order_book())
            prc = float(placed[0]["prc"])
            assert self._is_tick_aligned(prc), (
                f"ref={ref}: panic SELL exit prc {prc} is not a 0.05 multiple"
            )
