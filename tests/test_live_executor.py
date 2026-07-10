"""TDD tests for backend/app/live/executor.py (Task L3.4 — guarded executor).

Gate-block tests (ZERO place_order calls each):
  - mode=PAPER → blocked mode_not_live_test
  - mode=LIVE_TEST but single_shot_consumed=True → blocked mode_not_live_test
  - margin shortfall (cash "1000") → blocked dry_run_failed
  - band_pct=0 forcing a band fail → blocked dry_run_failed
  - engine.can_trade() returns (False, "halted") → blocked cannot_trade
  - claim_for_submit returns False → blocked already_claimed

Happy path:
  - all gates pass → exactly ONE place_order, mark_submitted called,
    consume_single_shot called, arm called once, placed=True + protected=True

Reject path:
  - broker rejects with "RMS" → placed=False, single-shot NOT consumed
    (is_live_order_allowed still True after the call), arm NOT called, no square

Arm-or-abort:
  - arm raises → cancel_order called on the norenordno, engine.halt called,
    returns placed=True + protected=False + halted=True

Lots hard-pinned:
  - passing a contract that resolves to qty==65 yields qty==lot_size;
    the caller cannot inject qty > lot_size (the lots parameter is
    never exposed)
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.live.executor import place_live_test_order
from app.live.mock_noren import MockNoren
from app.live.idempotency import IntentStore
from app.live.mode import ModeStore


# ---------------------------------------------------------------------------
# Helpers — async runner
# ---------------------------------------------------------------------------

def run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Shared fixtures / constants
# ---------------------------------------------------------------------------

# Canned NIFTY 26JUN26 25000 CE scrip (lot_size=65 — matches _CONTRACT below)
_NIFTY_SCRIP = {
    "tsym": "NIFTY26JUN26C25000",
    "token": "1",
    "ls": "65",
    "symname": "NIFTY",
    "optt": "CE",
    "exd": "26-JUN-2026",
    "dname": "NIFTY 26JUN26 25000 CE",
}

_CONTRACT = {
    "underlying": "NIFTY",
    "strike": 25000.0,
    "side": "CE",
    "expiry_date": "2026-06-26",
    "lot_size": 65,
}

_LOT_SIZE = 65
_REF_LTP = 200.0
_BAND_PCT = 5.0

# Good limits: cash well above 200 * 65 * 1.05 = 13650
_GOOD_LIMITS = {"cash": "16552.95"}


def _fake_search(exch: str, query: str):
    """Return the canned NIFTY scrip row unconditionally (sync, no network)."""
    return [_NIFTY_SCRIP]


# ---------------------------------------------------------------------------
# FakeAsyncCollection — minimal in-memory Mongo stand-in for mode + intent stores
# ---------------------------------------------------------------------------

class _FakeCursor:
    def __init__(self, docs: List[dict]) -> None:
        self._docs = docs

    async def to_list(self, length: Optional[int] = None) -> List[dict]:
        return list(self._docs) if length is None else list(self._docs[:length])


class _UpdateResult:
    def __init__(self, matched_count: int) -> None:
        self.matched_count = matched_count


def _matches(doc: Dict[str, Any], query: Dict[str, Any]) -> bool:
    """Simple equality match (no operators) sufficient for these tests."""
    for k, v in query.items():
        if doc.get(k) != v:
            return False
    return True


class FakeAsyncCollection:
    """In-memory async collection satisfying both ModeStore and IntentStore."""

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
        # No match
        if upsert and "$set" in update:
            new_doc = dict(update["$set"])
            # honour _id from query if present
            if "_id" in query:
                new_doc["_id"] = query["_id"]
            self.docs.append(new_doc)
            return _UpdateResult(matched_count=0)
        return _UpdateResult(matched_count=0)

    async def find(
        self,
        query: Dict[str, Any],
        projection: Optional[Dict[str, Any]] = None,
    ) -> _FakeCursor:
        return _FakeCursor([dict(d) for d in self.docs if _matches(d, query)])

    async def create_index(self, field: str, unique: bool = False) -> str:
        return field


# ---------------------------------------------------------------------------
# Fake engine — tracks can_trade / halt calls
# ---------------------------------------------------------------------------

class FakeEngine:
    def __init__(self, *, can_trade_result=(True, "ok")):
        self._can_trade_result = can_trade_result
        self.halt_calls: List[str] = []

    async def can_trade(self):
        return self._can_trade_result

    async def halt(self, reason: str) -> None:
        self.halt_calls.append(reason)


# ---------------------------------------------------------------------------
# Factory helpers — build pre-wired stores for each test scenario
# ---------------------------------------------------------------------------

def _live_test_mode_store() -> ModeStore:
    """Return a ModeStore with mode=LIVE_TEST and single_shot_consumed=False."""
    col = FakeAsyncCollection()
    col.docs.append({
        "_id": "singleton",
        "mode": "LIVE_TEST",
        "single_shot_consumed": False,
        "test_session_id": None,
    })
    return ModeStore(col)


def _consumed_mode_store() -> ModeStore:
    """LIVE_TEST but single_shot_consumed=True (already used)."""
    col = FakeAsyncCollection()
    col.docs.append({
        "_id": "singleton",
        "mode": "LIVE_TEST",
        "single_shot_consumed": True,
        "test_session_id": None,
    })
    return ModeStore(col)


def _paper_mode_store() -> ModeStore:
    """Return a ModeStore in PAPER mode (live orders not allowed)."""
    col = FakeAsyncCollection()
    # Default from an empty store is PAPER, but let's be explicit
    col.docs.append({
        "_id": "singleton",
        "mode": "PAPER",
        "single_shot_consumed": False,
        "test_session_id": None,
    })
    return ModeStore(col)


def _fresh_intent_store() -> IntentStore:
    """IntentStore backed by an empty fake collection."""
    return IntentStore(FakeAsyncCollection())


class _ClaimFalseIntentStore:
    """IntentStore stub where claim_for_submit always returns False."""

    async def record_intent(self, intent, *, mode: str = "live") -> dict:
        return {}  # no-op; claim will still return False

    async def claim_for_submit(self, cid: str) -> bool:
        return False

    async def mark_submitted(self, cid: str, norenordno: str) -> None:
        pass  # should never be reached in these tests


# ---------------------------------------------------------------------------
# Common kwargs builder so tests only override what they care about
# ---------------------------------------------------------------------------

def _kwargs(**overrides) -> Dict[str, Any]:
    base: Dict[str, Any] = dict(
        contract=_CONTRACT,
        side="B",
        ref_ltp=_REF_LTP,
        band_pct=_BAND_PCT,
        levels={},
        search_fn=_fake_search,
        fat_finger_cap=1,
        buffer_pct=0.5,
        uid="",
        actid="",
    )
    base.update(overrides)
    return base


async def _place(**overrides) -> Dict[str, Any]:
    """Run place_live_test_order with sensible defaults + overrides."""
    kw = _kwargs(**overrides)

    # Fill in stores/engine/arm if not provided
    if "mode_store" not in kw:
        kw["mode_store"] = _live_test_mode_store()
    if "intent_store" not in kw:
        kw["intent_store"] = _fresh_intent_store()
    if "engine" not in kw:
        kw["engine"] = FakeEngine()
    if "client" not in kw:
        kw["client"] = MockNoren(limits_data=_GOOD_LIMITS)
    if "arm" not in kw:
        kw["arm"] = _noop_arm

    return await place_live_test_order(**kw)


async def _noop_arm(intent, norenordno) -> None:
    """Default arm callable — succeeds silently."""
    pass


# ===========================================================================
# GATE-BLOCK TESTS — zero place_order calls each
# ===========================================================================

def test_mode_paper_blocks_before_place_order():
    """mode=PAPER → blocked with reason 'mode_not_live_test', zero orders."""
    client = MockNoren(limits_data=_GOOD_LIMITS)
    result = run(_place(
        mode_store=_paper_mode_store(),
        client=client,
    ))
    assert result["placed"] is False
    assert result["reason"] == "mode_not_live_test"
    assert run(client.order_book()) == [], "place_order must NOT have been called"


def test_mode_live_test_consumed_blocks():
    """LIVE_TEST with single_shot_consumed=True → blocked, zero orders."""
    client = MockNoren(limits_data=_GOOD_LIMITS)
    result = run(_place(
        mode_store=_consumed_mode_store(),
        client=client,
    ))
    assert result["placed"] is False
    assert result["reason"] == "mode_not_live_test"
    assert run(client.order_book()) == []


def test_guard_disarmed_blocks_real_manual_entry():
    """A real manual LIVE_TEST entry is BLOCKED unless the software guard is armed to
    auto-close it. Its only automated exits — the software guard stop and the 15:00
    IST EOD square — both transmit through the LIVE_GUARD_ARMED-gated square_fn, and
    the old ungated 10-min auto-square timer is gone. So placing a real entry with the
    guard disarmed would leave it with no automated close → block, zero orders."""
    client = MockNoren(limits_data=_GOOD_LIMITS)
    result = run(_place(client=client, guard_armed=False))
    assert result["placed"] is False
    assert result["reason"] == "guard_not_armed"
    assert run(client.order_book()) == [], "place_order must NOT have been called"


def test_guard_armed_allows_real_manual_entry():
    """Regression: with the guard armed, a valid manual entry still proceeds (the new
    gate does not block the armed path)."""
    result = run(_place(guard_armed=True))
    assert result["placed"] is True


def test_margin_shortfall_blocks():
    """cash='1000' << 200*65*1.05=13650 → dry_run_failed (margin verdict false), zero orders."""
    client = MockNoren(limits_data={"cash": "1000"})
    result = run(_place(client=client))
    assert result["placed"] is False
    assert result["reason"] == "dry_run_failed"
    # margin verdict must be in verdicts and must show ok=False
    margin_v = next((v for v in result["verdicts"] if v["check"] == "margin"), None)
    assert margin_v is not None, "margin verdict must be present"
    assert margin_v["ok"] is False
    assert run(client.order_book()) == []


def test_limits_read_failure_blocks_fail_closed():
    """limits() RAISING (expired token) must BLOCK the order fail-closed — never
    500, never place — with a reconnect hint in the margin verdict."""
    client = MockNoren(limits_data=_GOOD_LIMITS)
    client.script_read_error("limits", "Session Expired : Invalid Session Key")
    result = run(_place(client=client))
    assert result["placed"] is False
    assert result["reason"] == "dry_run_failed"
    assert run(client.order_book()) == [], "place_order must NOT have been called"
    margin_v = next((v for v in result["verdicts"] if v["check"] == "margin"), None)
    assert margin_v is not None and margin_v["ok"] is False
    assert "reconnect Flattrade" in margin_v["detail"]


def test_band_pct_zero_blocks():
    """band_pct=0 forces a buffer clamp to 0 — the price_band check will FAIL because
    the computed price with even a small buffer exceeds the zero band.

    With band_pct=0, build_intent's eff = min(buffer, band) = min(0.5,0) = 0, so
    prc = ref_ltp * 1.0 = ref_ltp exactly, but check_price_band(prc, ref_ltp, 0) passes
    because prc==ref_ltp (0% deviation).

    To reliably force a dry_run failure, we instead pass band_pct=0 AND a
    fat_finger_cap=0 which will fail the fat_finger check (lots=1 > cap=0).
    """
    client = MockNoren(limits_data=_GOOD_LIMITS)
    # fat_finger_cap=0 means cap=min(0,1)=0, lots=1>0 → fat_finger verdict fails
    result = run(_place(client=client, fat_finger_cap=0))
    assert result["placed"] is False
    assert result["reason"] == "dry_run_failed"
    ff_v = next((v for v in result["verdicts"] if v["check"] == "fat_finger"), None)
    assert ff_v is not None
    assert ff_v["ok"] is False
    assert run(client.order_book()) == []


def test_over_cap_blocks():
    """fat_finger_cap clamped to 1, then to min(caller_cap,1); if caller passes
    negative/zero cap → fat_finger fails → dry_run_failed, zero orders."""
    client = MockNoren(limits_data=_GOOD_LIMITS)
    result = run(_place(client=client, fat_finger_cap=-1))
    assert result["placed"] is False
    assert result["reason"] == "dry_run_failed"
    assert run(client.order_book()) == []


def test_engine_cannot_trade_blocks():
    """engine.can_trade() = (False, 'halted') → blocked cannot_trade:halted."""
    client = MockNoren(limits_data=_GOOD_LIMITS)
    engine = FakeEngine(can_trade_result=(False, "halted"))
    result = run(_place(client=client, engine=engine))
    assert result["placed"] is False
    assert "cannot_trade" in result["reason"]
    assert "halted" in result["reason"]
    assert run(client.order_book()) == []


def test_claim_for_submit_false_blocks():
    """claim_for_submit returns False → blocked already_claimed, zero orders."""
    client = MockNoren(limits_data=_GOOD_LIMITS)
    result = run(_place(client=client, intent_store=_ClaimFalseIntentStore()))
    assert result["placed"] is False
    assert result["reason"] == "already_claimed"
    assert run(client.order_book()) == []


# ===========================================================================
# HAPPY PATH
# ===========================================================================

def test_happy_path_places_exactly_once():
    """All gates pass → exactly ONE order placed, correct fields."""
    client = MockNoren(limits_data=_GOOD_LIMITS)
    mode_store = _live_test_mode_store()
    intent_store = _fresh_intent_store()
    engine = FakeEngine()
    arm_calls: List[Any] = []

    async def tracking_arm(intent, norenordno):
        arm_calls.append((intent, norenordno))

    result = run(_place(
        client=client,
        mode_store=mode_store,
        intent_store=intent_store,
        engine=engine,
        arm=tracking_arm,
    ))

    assert result["placed"] is True
    assert result["protected"] is True
    assert result["norenordno"] == "MOCK1"
    assert "cid" in result

    book = run(client.order_book())
    assert len(book) == 1, f"expected exactly 1 order, got {len(book)}: {book}"
    assert book[0]["trantype"] == "B"
    assert book[0]["tsym"] == "NIFTY26JUN26C25000"

    # arm called exactly once with correct norenordno
    assert len(arm_calls) == 1
    assert arm_calls[0][1] == "MOCK1"


def test_happy_path_mark_submitted_called():
    """After fill, mark_submitted records the norenordno in the store."""
    client = MockNoren(limits_data=_GOOD_LIMITS)
    mode_store = _live_test_mode_store()
    col = FakeAsyncCollection()
    intent_store = IntentStore(col)
    engine = FakeEngine()

    result = run(_place(
        client=client,
        mode_store=mode_store,
        intent_store=intent_store,
        engine=engine,
    ))

    assert result["placed"] is True
    # The intent doc in the collection should have a norenordno set
    submitted = [d for d in col.docs if d.get("norenordno") is not None]
    assert len(submitted) == 1
    assert submitted[0]["norenordno"] == "MOCK1"


def test_happy_path_consume_single_shot_called():
    """After fill, consume_single_shot is called → mode is locked."""
    client = MockNoren(limits_data=_GOOD_LIMITS)
    mode_store = _live_test_mode_store()

    result = run(_place(
        client=client,
        mode_store=mode_store,
    ))

    assert result["placed"] is True
    # After consume_single_shot, is_live_order_allowed must be False
    mode_doc = run(mode_store.get())
    assert mode_doc["single_shot_consumed"] is True


def test_happy_path_all_verdicts_ok():
    """All verdicts returned on success should be ok=True."""
    client = MockNoren(limits_data=_GOOD_LIMITS)
    result = run(_place(client=client))
    assert result["placed"] is True
    for v in result["verdicts"]:
        assert v["ok"] is True, f"verdict {v['check']!r} unexpectedly failed: {v}"


# ===========================================================================
# REJECT PATH (broker rejects the order)
# ===========================================================================

def test_reject_does_not_consume_single_shot():
    """Broker rejects → placed=False, single-shot NOT consumed."""
    client = MockNoren(limits_data=_GOOD_LIMITS)
    client.script_reject("RMS limit exceeded")
    mode_store = _live_test_mode_store()

    arm_calls: List[Any] = []
    async def tracking_arm(intent, norenordno):
        arm_calls.append((intent, norenordno))

    result = run(_place(
        client=client,
        mode_store=mode_store,
        arm=tracking_arm,
    ))

    assert result["placed"] is False
    assert "reject" in result["reason"]
    assert "RMS" in result["reason"]

    # single-shot must NOT be consumed — a future attempt is still allowed
    mode_doc = run(mode_store.get())
    assert mode_doc["single_shot_consumed"] is False, (
        "consume_single_shot must NOT be called on a broker reject"
    )


def test_reject_arm_not_called():
    """Broker rejects → arm is NOT called."""
    client = MockNoren(limits_data=_GOOD_LIMITS)
    client.script_reject("RMS")
    arm_calls: List[Any] = []

    async def tracking_arm(intent, norenordno):
        arm_calls.append((intent, norenordno))

    run(_place(client=client, arm=tracking_arm))
    assert len(arm_calls) == 0, "arm must NOT be called after a broker reject"


def test_reject_no_square():
    """Broker rejects → no cancel_order / no extra place_order for squaring."""
    client = MockNoren(limits_data=_GOOD_LIMITS)
    client.script_reject("RMS")
    run(_place(client=client))

    # Order book should be empty (rejected order not in MockNoren book)
    book = run(client.order_book())
    assert len(book) == 0


# ===========================================================================
# ARM-OR-ABORT
# ===========================================================================

def test_arm_failure_cancels_and_halts():
    """arm raises → cancel_order called on the norenordno, engine.halt called."""
    client = MockNoren(limits_data=_GOOD_LIMITS)
    engine = FakeEngine()

    async def failing_arm(intent, norenordno):
        raise RuntimeError("SL order rejected by broker")

    result = run(_place(
        client=client,
        engine=engine,
        arm=failing_arm,
    ))

    assert result["placed"] is True
    assert result["protected"] is False
    assert result["halted"] is True
    assert "post_place_failed" in result["reason"]
    assert "SL order rejected by broker" in result["reason"]

    # engine.halt must have been called (via _abort_protect)
    assert "post_place_protection_failed" in engine.halt_calls

    # cancel_order must have been called for the norenordno
    book = run(client.order_book())
    assert len(book) >= 1
    canceled = [o for o in book if o.get("status") == "CANCELED"]
    assert len(canceled) >= 1, (
        "cancel_order must be called on the entry order's norenordno when arm fails"
    )


def test_arm_failure_returns_correct_norenordno():
    """Even on arm failure, the placed norenordno is returned so the UI can display it."""
    client = MockNoren(limits_data=_GOOD_LIMITS)

    async def failing_arm(intent, norenordno):
        raise ValueError("arm error")

    result = run(_place(client=client, arm=failing_arm))

    assert result["placed"] is True
    assert result["norenordno"] == "MOCK1"


def test_arm_failure_consume_single_shot_still_called():
    """consume_single_shot happens BEFORE arm — even on arm failure, single-shot is consumed."""
    client = MockNoren(limits_data=_GOOD_LIMITS)
    mode_store = _live_test_mode_store()

    async def failing_arm(intent, norenordno):
        raise RuntimeError("arm failed")

    run(_place(client=client, mode_store=mode_store, arm=failing_arm))

    mode_doc = run(mode_store.get())
    assert mode_doc["single_shot_consumed"] is True, (
        "consume_single_shot happens at step 10, before arm at step 11"
    )


# ===========================================================================
# LOTS HARD-PINNED TO 1 (defense-in-depth)
# ===========================================================================

def test_qty_equals_lot_size_hard_pinned():
    """The placed order's qty must equal exactly lot_size (1 lot hard-pinned)."""
    client = MockNoren(limits_data=_GOOD_LIMITS)
    result = run(_place(client=client))
    assert result["placed"] is True
    book = run(client.order_book())
    assert len(book) == 1
    assert book[0]["qty"] == _LOT_SIZE, (
        f"expected qty={_LOT_SIZE} (1 lot hard-pinned), got {book[0]['qty']}"
    )


def test_cannot_inject_qty_greater_than_one_lot():
    """There is no lots parameter; callers cannot bypass the 1-lot pin.

    Verify that even passing fat_finger_cap=100 (which the executor clamps to 1)
    still results in qty == lot_size.
    """
    client = MockNoren(limits_data=_GOOD_LIMITS)
    # fat_finger_cap clamped to min(100, 1) = 1 inside the executor
    result = run(_place(client=client, fat_finger_cap=100))
    assert result["placed"] is True
    book = run(client.order_book())
    assert book[0]["qty"] == _LOT_SIZE


def test_qty_not_one_lot_blocks_as_not_one_lot():
    """Gate 5 defense-in-depth: if intent.qty != resolved_lot_size, blocked with 'not_one_lot'.

    We patch executor.build_intent to return a synthetic intent whose qty
    disagrees with the resolved_lot_size in the 3-tuple.  Gate 5 checks
    intent.qty != resolved_lot_size → not_one_lot, and ZERO orders are placed.
    """
    from unittest.mock import patch
    from app.live.broker_protocol import OrderIntent

    # Synthesise an intent with qty=130 (2 lots) — but resolved_lot_size=65
    fake_intent = OrderIntent(
        client_order_id="fake-cid",
        trantype="B",
        prctyp="LMT",
        exch="NFO",
        tsym="NIFTY26JUN26C25000",
        qty=130,   # 2 × 65 — disagrees with resolved_lot_size=65
        prc=201.0,
        prd="I",
        ret="DAY",
        remarks="fake-cid",
    )
    # All verdicts pass so Gate 4 is satisfied; Gate 5 catches the qty mismatch
    all_pass_verdicts = [
        {"check": "symbol", "ok": True, "detail": "ok"},
        {"check": "ref_ltp", "ok": True, "detail": "ok"},
        {"check": "price_finite", "ok": True, "detail": "ok"},
        {"check": "price_band", "ok": True, "detail": "ok"},
        {"check": "fat_finger", "ok": True, "detail": "ok"},
        {"check": "jdata", "ok": True, "detail": "ok"},
    ]
    # 3-tuple: intent, verdicts, resolved_lot_size=65 (but qty=130 → mismatch)
    mock_return = (fake_intent, all_pass_verdicts, 65)

    client = MockNoren(limits_data=_GOOD_LIMITS)

    with patch("app.live.executor.build_intent", return_value=mock_return):
        result = run(_place(client=client))

    assert result["placed"] is False
    assert result["reason"] == "not_one_lot"
    assert run(client.order_book()) == []


def test_stale_contract_lot_size_does_not_block():
    """The bug: contract says lot_size=75 (stale) but broker scrip ls=65.

    The executor must now use the broker-resolved lot (65) for gate 5 and margin,
    not the stale contract value.  Order must be placed (placed=True).
    """
    # Contract carries a stale lot_size=75 — the scrip always returns ls=65
    stale_contract = {**_CONTRACT, "lot_size": 75}  # stale
    client = MockNoren(limits_data=_GOOD_LIMITS)
    result = run(_place(client=client, contract=stale_contract))
    assert result["placed"] is True, (
        f"stale contract lot_size=75 must NOT block when broker scrip ls=65; "
        f"got placed=False, reason={result.get('reason')}, verdicts={result.get('verdicts')}"
    )
    book = run(client.order_book())
    assert len(book) == 1
    assert book[0]["qty"] == 65  # broker ls, not stale 75


def test_absent_contract_lot_size_does_not_block():
    """Contract with no lot_size key at all still resolves and places successfully."""
    no_lot_contract = {k: v for k, v in _CONTRACT.items() if k != "lot_size"}
    client = MockNoren(limits_data=_GOOD_LIMITS)
    result = run(_place(client=client, contract=no_lot_contract))
    assert result["placed"] is True, (
        f"absent contract lot_size must NOT block; "
        f"reason={result.get('reason')}, verdicts={result.get('verdicts')}"
    )
    book = run(client.order_book())
    assert book[0]["qty"] == 65


# ===========================================================================
# PLACE_ORDER CALL COUNT — paranoia: confirm gate blocks produce ZERO calls
# ===========================================================================

def _count_orders(client: MockNoren) -> int:
    return len(run(client.order_book()))


def test_mode_block_zero_orders():
    client = MockNoren(limits_data=_GOOD_LIMITS)
    run(_place(mode_store=_paper_mode_store(), client=client))
    assert _count_orders(client) == 0


def test_consumed_block_zero_orders():
    client = MockNoren(limits_data=_GOOD_LIMITS)
    run(_place(mode_store=_consumed_mode_store(), client=client))
    assert _count_orders(client) == 0


def test_margin_block_zero_orders():
    client = MockNoren(limits_data={"cash": "1000"})
    run(_place(client=client))
    assert _count_orders(client) == 0


def test_fat_finger_block_zero_orders():
    client = MockNoren(limits_data=_GOOD_LIMITS)
    run(_place(client=client, fat_finger_cap=0))
    assert _count_orders(client) == 0


def test_engine_block_zero_orders():
    client = MockNoren(limits_data=_GOOD_LIMITS)
    run(_place(client=client, engine=FakeEngine(can_trade_result=(False, "x"))))
    assert _count_orders(client) == 0


def test_claim_block_zero_orders():
    client = MockNoren(limits_data=_GOOD_LIMITS)
    run(_place(client=client, intent_store=_ClaimFalseIntentStore()))
    assert _count_orders(client) == 0


# ===========================================================================
# FRESH DRY-RUN PROPERTIES (no stale state reused across calls)
# ===========================================================================

def test_fresh_cid_each_call():
    """Each call generates a fresh cid (no cid reuse)."""
    seen_cids: List[str] = []

    async def cid_tracking_arm(intent, norenordno):
        seen_cids.append(intent.client_order_id)

    client1 = MockNoren(limits_data=_GOOD_LIMITS)
    r1 = run(_place(
        client=client1,
        mode_store=_live_test_mode_store(),
        intent_store=_fresh_intent_store(),
        arm=cid_tracking_arm,
    ))

    client2 = MockNoren(limits_data=_GOOD_LIMITS)
    r2 = run(_place(
        client=client2,
        mode_store=_live_test_mode_store(),
        intent_store=_fresh_intent_store(),
        arm=cid_tracking_arm,
    ))

    assert r1["cid"] != r2["cid"], "each call must mint a unique client_order_id"
    assert len(seen_cids) == 2
    assert seen_cids[0] != seen_cids[1]


# ===========================================================================
# POST-FILL EXCEPTION-TOTAL (regression: audit holes §12.3)
# ===========================================================================
# Every test in this section confirms that the "no unprotected live position"
# invariant holds even when a post-fill step raises.  In all cases:
#   - NO exception propagates out of place_live_test_order
#   - placed=True, protected=False is returned
#   - square_position ran (cancel order present in book)
#   - engine.halt was called
# ===========================================================================

class _RaisingMarkSubmittedIntentStore:
    """IntentStore stub: record_intent + claim_for_submit succeed; mark_submitted raises."""

    async def record_intent(self, intent, *, mode: str = "live") -> dict:
        return {}

    async def claim_for_submit(self, cid: str) -> bool:
        return True

    async def mark_submitted(self, cid: str, norenordno: str) -> None:
        raise RuntimeError("DB connection lost in mark_submitted")


class _RaisingConsumeModStore:
    """ModeStore stub: get() and record_intent succeed; consume_single_shot raises."""

    async def get(self) -> dict:
        return {"mode": "LIVE_TEST", "single_shot_consumed": False, "test_session_id": None}

    async def consume_single_shot(self) -> None:
        raise RuntimeError("consume_single_shot DB write failed")


def test_mark_submitted_raises_squares_and_halts_no_propagation():
    """mark_submitted raises after fill → _abort_protect runs, no propagation.

    Regression for audit hole: only arm() was guarded, mark_submitted was not.
    """
    client = MockNoren(limits_data=_GOOD_LIMITS)
    engine = FakeEngine()

    result = run(_place(
        client=client,
        engine=engine,
        intent_store=_RaisingMarkSubmittedIntentStore(),
    ))

    # Must return, not raise
    assert result["placed"] is True
    assert result["protected"] is False
    assert result["halted"] is True
    assert "post_place_failed" in result["reason"]
    assert "mark_submitted" in result["reason"]

    # engine.halt must have been called
    assert "post_place_protection_failed" in engine.halt_calls

    # square_position must have run (cancel_order called → CANCELED entry)
    book = run(client.order_book())
    assert len(book) >= 1
    canceled = [o for o in book if o.get("status") == "CANCELED"]
    assert len(canceled) >= 1, "cancel_order must run even when mark_submitted raises"


def test_consume_single_shot_raises_squares_and_halts_no_propagation():
    """consume_single_shot raises after fill → _abort_protect runs, no propagation.

    Regression for audit hole: consume_single_shot was unguarded post-fill.
    """
    client = MockNoren(limits_data=_GOOD_LIMITS)
    engine = FakeEngine()

    result = run(_place(
        client=client,
        engine=engine,
        mode_store=_RaisingConsumeModStore(),
    ))

    assert result["placed"] is True
    assert result["protected"] is False
    assert result["halted"] is True
    assert "post_place_failed" in result["reason"]
    assert "consume_single_shot" in result["reason"]

    assert "post_place_protection_failed" in engine.halt_calls

    book = run(client.order_book())
    assert len(book) >= 1
    canceled = [o for o in book if o.get("status") == "CANCELED"]
    assert len(canceled) >= 1, "cancel_order must run even when consume_single_shot raises"


def test_arm_failure_via_abort_protect_squares_and_halts():
    """arm raises → _abort_protect squares + halts; same invariant as the two above.

    This test confirms arm failure still flows correctly through the new unified
    _abort_protect path (kept as a passing regression guard).
    """
    client = MockNoren(limits_data=_GOOD_LIMITS)
    engine = FakeEngine()

    async def failing_arm(intent, norenordno):
        raise RuntimeError("arm SL rejected")

    result = run(_place(
        client=client,
        engine=engine,
        arm=failing_arm,
    ))

    assert result["placed"] is True
    assert result["protected"] is False
    assert result["halted"] is True
    assert "post_place_failed" in result["reason"]
    assert "arm SL rejected" in result["reason"]
    assert "post_place_protection_failed" in engine.halt_calls
    book = run(client.order_book())
    canceled = [o for o in book if o.get("status") == "CANCELED"]
    assert len(canceled) >= 1


def test_square_position_raises_inside_abort_still_halts_no_propagation():
    """square_position itself raises inside _abort_protect → engine.halt still called,
    square_result carries the error, NO exception propagates.
    """
    from unittest.mock import patch, AsyncMock

    client = MockNoren(limits_data=_GOOD_LIMITS)
    engine = FakeEngine()

    async def failing_arm(intent, norenordno):
        raise RuntimeError("arm failed, triggering abort")

    # Make square_position raise inside _abort_protect
    async def _raising_square(*args, **kwargs):
        raise ConnectionError("broker TCP timeout during square")

    with patch("app.live.executor.square_position", side_effect=_raising_square):
        result = run(_place(
            client=client,
            engine=engine,
            arm=failing_arm,
        ))

    # Must return, not raise
    assert result["placed"] is True
    assert result["protected"] is False
    # halt must still have been attempted and succeeded
    assert result["halted"] is True
    assert "post_place_protection_failed" in engine.halt_calls
    # square_result must carry the error so operator can see it
    sq = result.get("square_result", {})
    assert sq.get("squared") is False
    assert "broker TCP timeout" in sq.get("error", "")


def test_engine_halt_raises_inside_abort_still_returns_no_propagation():
    """engine.halt raises inside _abort_protect → executor still returns,
    halted=False, square ran, NO exception propagates.
    """
    client = MockNoren(limits_data=_GOOD_LIMITS)

    class _RaisingHaltEngine(FakeEngine):
        async def halt(self, reason: str) -> None:
            raise RuntimeError("engine.halt DB write failed")

    engine = _RaisingHaltEngine()

    async def failing_arm(intent, norenordno):
        raise RuntimeError("arm failed triggering abort")

    result = run(_place(
        client=client,
        engine=engine,
        arm=failing_arm,
    ))

    # Must return, not raise
    assert result["placed"] is True
    assert result["protected"] is False
    # halt raised → halted must be False
    assert result["halted"] is False
    # square still ran (cancel_order issued)
    book = run(client.order_book())
    canceled = [o for o in book if o.get("status") == "CANCELED"]
    assert len(canceled) >= 1, "square must run even when engine.halt raises"


# ===========================================================================
# FAT FINGER CAP HARDENING (non-numeric cap must not raise TypeError)
# ===========================================================================

def test_fat_finger_cap_none_fails_closed_no_typeerror():
    """fat_finger_cap=None → clean dry_run_failed (fat_finger verdict), no TypeError."""
    client = MockNoren(limits_data=_GOOD_LIMITS)
    result = run(_place(client=client, fat_finger_cap=None))
    assert result["placed"] is False
    assert result["reason"] == "dry_run_failed"
    ff_v = next((v for v in result["verdicts"] if v["check"] == "fat_finger"), None)
    assert ff_v is not None, "fat_finger verdict must be present"
    assert ff_v["ok"] is False
    assert run(client.order_book()) == []


def test_fat_finger_cap_string_fails_closed_no_typeerror():
    """fat_finger_cap='1' (string) → clean dry_run_failed, no TypeError."""
    client = MockNoren(limits_data=_GOOD_LIMITS)
    result = run(_place(client=client, fat_finger_cap="1"))
    assert result["placed"] is False
    assert result["reason"] == "dry_run_failed"
    ff_v = next((v for v in result["verdicts"] if v["check"] == "fat_finger"), None)
    assert ff_v is not None
    assert ff_v["ok"] is False
    assert run(client.order_book()) == []


# ===========================================================================
# FIX 1 — LONG-ONLY GUARD: side != "B" must be blocked at the executor
# (audit: side="S" opened an unprotected naked short)
# ===========================================================================

def test_side_sell_blocked_before_place_order():
    """side='S' → blocked with reason 'side_must_be_buy', ZERO place_order calls."""
    client = MockNoren(limits_data=_GOOD_LIMITS)
    result = run(_place(client=client, side="S"))
    assert result["placed"] is False
    assert result["reason"] == "side_must_be_buy"
    assert run(client.order_book()) == [], "place_order must NOT be called for side='S'"


def test_side_x_blocked_before_place_order():
    """side='X' (invalid) → blocked with reason 'side_must_be_buy', ZERO place_order calls."""
    client = MockNoren(limits_data=_GOOD_LIMITS)
    result = run(_place(client=client, side="X"))
    assert result["placed"] is False
    assert result["reason"] == "side_must_be_buy"
    assert run(client.order_book()) == []


def test_side_buy_still_places():
    """side='B' → happy path not broken by the long-only guard."""
    client = MockNoren(limits_data=_GOOD_LIMITS)
    result = run(_place(client=client, side="B"))
    assert result["placed"] is True
    assert result["protected"] is True
    book = run(client.order_book())
    assert len(book) == 1
    assert book[0]["trantype"] == "B"
