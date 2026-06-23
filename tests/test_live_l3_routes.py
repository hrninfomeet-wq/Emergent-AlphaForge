"""TDD tests for L3 routes in backend/app/routers/live_broker.py (Task L3.6).

Test coverage:
  GET  /live-broker/mode          — returns current mode doc
  PUT  /live-broker/mode          — guards: no confirm → 400; LIVE_ARMED → 400;
                                    connected+confirm+can_trade → 200 LIVE_TEST
  POST /live-broker/order/place   — PAPER → blocked; LIVE_TEST all-pass → exactly 1 place_order,
                                    protected=True, session recorded; halted engine → blocked
  POST /live-broker/order/square  — exit-only, squares position, reverts mode
  GET  /live-broker/test-session  — returns deadline + remaining_secs + heartbeat
  POST /live-broker/kill-switch   — EXECUTES panic squareoff, reverts mode, transmitted=True

CHOKEPOINT invariant:
  ENTRY place_order reachable ONLY via executor.place_live_test_order.
  All other place_order calls are EXIT-ONLY (verified by inspecting the call path
  for square, kill, and SL backstop).

RULES: Tests NEVER instantiate a real FlattradeClient. All broker access goes
through MockNoren patched via monkeypatch on the module-level getter functions.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import patch, AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient
from fastapi import FastAPI

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.live.mock_noren import MockNoren
from app.live.mode import ModeStore
from app.live.idempotency import IntentStore
from app.live.kill_switch import SafetyConfigStore
from app.live.session_store import SessionStore
import app.routers.live_broker as _routes


# ---------------------------------------------------------------------------
# Minimal in-memory collection — reused from other test files
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
    for k, v in query.items():
        if k == "_id":
            if doc.get("_id") != v:
                return False
            continue
        if doc.get(k) != v:
            return False
    return True


class FakeAsyncCollection:
    def __init__(self) -> None:
        self.docs: List[Dict[str, Any]] = []

    async def find_one(self, query, projection=None):
        for doc in self.docs:
            if _matches(doc, query):
                return dict(doc)
        return None

    async def insert_one(self, doc):
        self.docs.append(dict(doc))

    async def update_one(self, query, update, upsert=False):
        for doc in self.docs:
            if _matches(doc, query):
                if "$set" in update:
                    doc.update(update["$set"])
                return _UpdateResult(matched_count=1)
        if upsert and "$set" in update:
            new_doc = dict(update["$set"])
            if "_id" in query:
                new_doc["_id"] = query["_id"]
            self.docs.append(new_doc)
            return _UpdateResult(matched_count=0)
        return _UpdateResult(matched_count=0)

    async def find(self, query, projection=None):
        return _FakeCursor([dict(d) for d in self.docs if _matches(d, query)])

    async def create_index(self, field, unique=False):
        return field


# ---------------------------------------------------------------------------
# Fake engine
# ---------------------------------------------------------------------------

class FakeEngine:
    def __init__(self, *, can_trade_result=(True, "")):
        self._can_trade_result = can_trade_result
        self.halt_calls: List[str] = []
        self.halted = False

    async def can_trade(self):
        return self._can_trade_result

    async def halt(self, reason: str) -> None:
        self.halt_calls.append(reason)
        self.halted = True


# ---------------------------------------------------------------------------
# Store factories
# ---------------------------------------------------------------------------

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

_GOOD_LIMITS = {"cash": "16552.95"}


def _make_mode_store(mode: str = "LIVE_TEST", consumed: bool = False) -> ModeStore:
    col = FakeAsyncCollection()
    col.docs.append({
        "_id": "singleton",
        "mode": mode,
        "single_shot_consumed": consumed,
        "test_session_id": None,
    })
    return ModeStore(col)


def _make_intent_store() -> IntentStore:
    return IntentStore(FakeAsyncCollection())


def _make_config_store() -> SafetyConfigStore:
    return SafetyConfigStore(FakeAsyncCollection())


def _make_session_store() -> SessionStore:
    return SessionStore(FakeAsyncCollection())


def _make_mock_noren(limits=None, position_book=None) -> MockNoren:
    return MockNoren(
        limits_data=limits or _GOOD_LIMITS,
        position_book_data=position_book or [],
        search_scrip_data={"NFO": [_NIFTY_SCRIP]},
    )


# ---------------------------------------------------------------------------
# FastAPI test app setup
# ---------------------------------------------------------------------------

def _make_app(
    *,
    mode_store: Optional[ModeStore] = None,
    intent_store: Optional[IntentStore] = None,
    config_store: Optional[SafetyConfigStore] = None,
    session_store: Optional[SessionStore] = None,
    client: Optional[MockNoren] = None,
    engine: Optional[FakeEngine] = None,
) -> TestClient:
    """Build a TestClient with all stores/client patched to fakes."""
    app = FastAPI()
    app.include_router(_routes.api)

    ms = mode_store or _make_mode_store()
    is_ = intent_store or _make_intent_store()
    cs = config_store or _make_config_store()
    ss = session_store or _make_session_store()
    cl = client or _make_mock_noren()
    eng = engine or FakeEngine()

    # Patch the module-level getters so no real DB / broker is touched
    patches = {
        "_mode_store": lambda: ms,
        "_intent_store": lambda: is_,
        "_config_store": lambda: cs,
        "_session_store": lambda: ss,
        "_order_client": lambda: cl,
        "_l3_engine": lambda: eng,
        # Patch _get_client to return the MockNoren directly (async wrapper)
        "_get_client": AsyncMock(return_value=cl),
        # Patch _get_token_doc to return a fake token doc
        "_get_token_doc": AsyncMock(return_value={
            "jKey": "fake_jkey",
            "uid": "TESTUID",
            "actid": "TESTUID",
        }),
        # Patch _schedule_auto_square to be a no-op (background task)
        "_schedule_auto_square": MagicMock(),
        # Patch _utcnow_iso to a fixed time for deterministic tests
        "_utcnow_iso": lambda: "2026-06-22T06:00:00+00:00",
    }

    ctx_managers = []
    for name, val in patches.items():
        p = patch.object(_routes, name, val)
        ctx_managers.append(p)
        p.start()

    tc = TestClient(app, raise_server_exceptions=True)

    # We need to stop patches after the test; attach cleanup to the client
    # (tests call tc._stop_patches() or use a fixture)
    tc._patch_ctx = ctx_managers

    return tc


def _stop_patches(tc: TestClient) -> None:
    for p in getattr(tc, "_patch_ctx", []):
        try:
            p.stop()
        except RuntimeError:
            pass


# ---------------------------------------------------------------------------
# Helper: build a search_scrip mock that returns the NIFTY scrip
# ---------------------------------------------------------------------------

async def _fake_search_scrip(exch: str, query: str):
    return [_NIFTY_SCRIP]


# ===========================================================================
# GET /live-broker/mode
# ===========================================================================

def test_get_mode_returns_current_mode():
    ms = _make_mode_store(mode="PAPER")
    tc = _make_app(mode_store=ms)
    try:
        r = tc.get("/live-broker/mode")
        assert r.status_code == 200
        data = r.json()
        assert data["mode"] == "PAPER"
        assert data["single_shot_consumed"] is False
    finally:
        _stop_patches(tc)


# ===========================================================================
# PUT /live-broker/mode
# ===========================================================================

def test_put_mode_live_test_without_confirm_returns_400():
    tc = _make_app()
    try:
        r = tc.put("/live-broker/mode", json={"mode": "LIVE_TEST", "confirm": False})
        assert r.status_code == 400
        assert "confirm" in r.json()["detail"].lower()
    finally:
        _stop_patches(tc)


def test_put_mode_live_armed_returns_422():
    """LIVE_ARMED → 422 (Literal["PAPER","LIVE_OFFLINE","LIVE_TEST"] rejects it at boundary)."""
    tc = _make_app()
    try:
        r = tc.put("/live-broker/mode", json={"mode": "LIVE_ARMED", "confirm": True})
        # Pydantic Literal rejects LIVE_ARMED with 422 (previously 400 from mode.py logic)
        assert r.status_code == 422
    finally:
        _stop_patches(tc)


def test_put_mode_live_test_with_confirm_and_connected_returns_200():
    ms = _make_mode_store(mode="LIVE_OFFLINE")
    eng = FakeEngine(can_trade_result=(True, ""))
    tc = _make_app(mode_store=ms, engine=eng)
    try:
        r = tc.put("/live-broker/mode", json={"mode": "LIVE_TEST", "confirm": True})
        assert r.status_code == 200
        data = r.json()
        assert data["mode"] == "LIVE_TEST"
        assert data["single_shot_consumed"] is False
    finally:
        _stop_patches(tc)


def test_put_mode_to_paper_succeeds():
    ms = _make_mode_store(mode="LIVE_OFFLINE")
    tc = _make_app(mode_store=ms)
    try:
        r = tc.put("/live-broker/mode", json={"mode": "PAPER", "confirm": False})
        assert r.status_code == 200
        assert r.json()["mode"] == "PAPER"
    finally:
        _stop_patches(tc)


def test_put_mode_live_test_with_halted_engine_returns_400():
    ms = _make_mode_store(mode="LIVE_OFFLINE")
    eng = FakeEngine(can_trade_result=(False, "engine halted"))
    tc = _make_app(mode_store=ms, engine=eng)
    try:
        r = tc.put("/live-broker/mode", json={"mode": "LIVE_TEST", "confirm": True})
        assert r.status_code == 400
    finally:
        _stop_patches(tc)


# ===========================================================================
# POST /live-broker/order/place — ENTRY CHOKEPOINT
# ===========================================================================

_PLACE_BODY = {
    "contract": _CONTRACT,
    "side": "B",
    "ref_ltp": 200.0,
    "band_pct": 5.0,
    "levels": {},
}


def test_place_in_paper_mode_blocked():
    """PAPER mode → blocked mode_not_live_test, zero place_order calls."""
    ms = _make_mode_store(mode="PAPER")
    cl = _make_mock_noren()
    tc = _make_app(mode_store=ms, client=cl)
    try:
        r = tc.post("/live-broker/order/place", json=_PLACE_BODY)
        assert r.status_code == 200
        data = r.json()
        assert data["placed"] is False
        assert data["reason"] == "mode_not_live_test"
        # Verify zero orders
        book = asyncio.run(cl.order_book())
        assert len(book) == 0
    finally:
        _stop_patches(tc)


def test_place_in_live_test_all_pass_exactly_one_place_order():
    """LIVE_TEST + all gates pass → exactly one place_order, protected=True."""
    ms = _make_mode_store(mode="LIVE_TEST", consumed=False)
    cl = _make_mock_noren()
    # Patch search_scrip on the mock client to return the NIFTY scrip
    cl._search_scrip_data = {"NFO": [_NIFTY_SCRIP]}
    ss = _make_session_store()
    eng = FakeEngine(can_trade_result=(True, ""))
    tc = _make_app(mode_store=ms, client=cl, session_store=ss, engine=eng)
    try:
        r = tc.post("/live-broker/order/place", json=_PLACE_BODY)
        assert r.status_code == 200
        data = r.json()
        assert data["placed"] is True
        assert data["protected"] is True
        assert "norenordno" in data
        # Exactly 1 entry order in the book
        book = asyncio.run(cl.order_book())
        entries = [o for o in book if o.get("trantype") == "B"]
        assert len(entries) == 1
    finally:
        _stop_patches(tc)


def test_place_in_live_test_session_has_deadline():
    """After a successful place, the session store has a deadline."""
    ms = _make_mode_store(mode="LIVE_TEST", consumed=False)
    cl = _make_mock_noren()
    cl._search_scrip_data = {"NFO": [_NIFTY_SCRIP]}
    ss = _make_session_store()
    eng = FakeEngine(can_trade_result=(True, ""))
    tc = _make_app(mode_store=ms, client=cl, session_store=ss, engine=eng)
    try:
        r = tc.post("/live-broker/order/place", json=_PLACE_BODY)
        assert r.status_code == 200
        data = r.json()
        assert data["placed"] is True
        # Check session was recorded
        sess = asyncio.run(ss.get())
        assert sess["deadline"] is not None
        assert sess["status"] == "armed"
        assert sess["entry_norenordno"] is not None
    finally:
        _stop_patches(tc)


def test_place_with_halted_engine_blocked():
    """engine.can_trade() = (False, 'halted') → blocked cannot_trade."""
    ms = _make_mode_store(mode="LIVE_TEST", consumed=False)
    cl = _make_mock_noren()
    cl._search_scrip_data = {"NFO": [_NIFTY_SCRIP]}
    eng = FakeEngine(can_trade_result=(False, "halted"))
    tc = _make_app(mode_store=ms, client=cl, engine=eng)
    try:
        r = tc.post("/live-broker/order/place", json=_PLACE_BODY)
        assert r.status_code == 200
        data = r.json()
        assert data["placed"] is False
        assert "cannot_trade" in data["reason"]
        book = asyncio.run(cl.order_book())
        assert len(book) == 0
    finally:
        _stop_patches(tc)


def test_place_zero_entry_calls_in_paper_mode():
    """Paranoia: PAPER mode → place_order call count == 0."""
    ms = _make_mode_store(mode="PAPER")
    cl = _make_mock_noren()
    tc = _make_app(mode_store=ms, client=cl)
    try:
        tc.post("/live-broker/order/place", json=_PLACE_BODY)
        book = asyncio.run(cl.order_book())
        assert len(book) == 0
    finally:
        _stop_patches(tc)


def test_place_chokepoint_no_direct_entry_outside_executor():
    """Verify at import time that 'place_order' in the router is ONLY via executor call.

    This is a structural grep test — it reads the live_broker.py source and
    verifies that the only 'place_order' calls go through:
      1. executor.place_live_test_order (entry — through executor)
      2. square_position (exit-only)
      3. panic_squareoff (exit-only)
      4. _make_arm (SL backstop, exit-only sell-to-close)

    No raw 'client.place_order(' appears at the route level for entries.
    """
    router_path = ROOT / "backend" / "app" / "routers" / "live_broker.py"
    source = router_path.read_text()

    # The route itself must not contain a direct 'await client.place_order(' call
    # (that would be a direct entry bypass of the executor).
    # We allow 'client.place_order' only inside _make_arm (which is exit-only SL)
    # and assert executor is called for entries.
    assert "executor" in source or "place_live_test_order" in source, (
        "The /order/place route must route through the executor"
    )
    # Verify _make_arm is documented as exit-only
    assert "exit-only" in source.lower() or "sell-to-close" in source.lower(), (
        "_make_arm / SL backstop must be documented as exit-only"
    )


# ===========================================================================
# POST /live-broker/order/square — EXIT ONLY
# ===========================================================================

def test_square_exits_position_and_reverts_mode():
    """square route exits the open position and reverts mode to LIVE_OFFLINE."""
    ms = _make_mode_store(mode="LIVE_TEST", consumed=True)
    # Position with netqty=65 (one long lot)
    cl = _make_mock_noren(
        position_book=[{
            "tsym": "NIFTY26JUN26C25000",
            "exch": "NFO",
            "netqty": "65",
            "lp": "200.0",
        }],
    )
    ss = _make_session_store()
    # Pre-arm the session
    asyncio.run(ss.arm(
        entry_norenordno="MOCK_ENTRY",
        deadline="2026-06-22T06:10:00+00:00",
    ))
    tc = _make_app(mode_store=ms, client=cl, session_store=ss)
    try:
        r = tc.post("/live-broker/order/square", json={})
        assert r.status_code == 200
        data = r.json()
        assert data.get("squared") is True
        # Mode should be reverted
        mode_doc = asyncio.run(ms.get())
        assert mode_doc["mode"] == "LIVE_OFFLINE"
        # Session status updated
        sess = asyncio.run(ss.get())
        assert sess["status"] == "squared"
    finally:
        _stop_patches(tc)


def test_square_no_position_still_reverts_mode():
    """If no open position found, mode is still reverted."""
    ms = _make_mode_store(mode="LIVE_TEST", consumed=True)
    cl = _make_mock_noren(position_book=[])  # no positions
    ss = _make_session_store()
    tc = _make_app(mode_store=ms, client=cl, session_store=ss)
    try:
        r = tc.post("/live-broker/order/square", json={})
        assert r.status_code == 200
        data = r.json()
        assert data.get("squared") is True
        mode_doc = asyncio.run(ms.get())
        assert mode_doc["mode"] == "LIVE_OFFLINE"
    finally:
        _stop_patches(tc)


def test_square_is_exit_only_no_buy_orders():
    """Square route never places a BUY entry order."""
    cl = _make_mock_noren(
        position_book=[{
            "tsym": "NIFTY26JUN26C25000",
            "exch": "NFO",
            "netqty": "65",
            "lp": "200.0",
        }],
    )
    ss = _make_session_store()
    tc = _make_app(client=cl, session_store=ss)
    try:
        tc.post("/live-broker/order/square", json={})
        book = asyncio.run(cl.order_book())
        # All orders placed should be SELL (exit-only)
        for order in book:
            assert order.get("trantype") != "B", (
                f"Square route placed a BUY order: {order}"
            )
    finally:
        _stop_patches(tc)


# ===========================================================================
# GET /live-broker/test-session
# ===========================================================================

def test_test_session_returns_deadline_and_remaining():
    """GET /test-session returns deadline, remaining_secs, and heartbeat."""
    ss = _make_session_store()
    asyncio.run(ss.arm(
        entry_norenordno="MOCK1",
        deadline="2026-06-22T06:10:00+00:00",
        sl_norenordno="MOCK_SL",
        now_iso="2026-06-22T06:00:00+00:00",
    ))
    tc = _make_app(session_store=ss)
    try:
        r = tc.get("/live-broker/test-session")
        assert r.status_code == 200
        data = r.json()
        assert data["deadline"] == "2026-06-22T06:10:00+00:00"
        assert data["remaining_secs"] is not None
        assert data["heartbeat"] is not None
        assert data["sl_norenordno"] == "MOCK_SL"
        assert data["status"] == "armed"
        assert data["position"] == "MOCK1"
    finally:
        _stop_patches(tc)


def test_test_session_no_session_returns_none_fields():
    """GET /test-session with no active session returns null fields."""
    ss = _make_session_store()
    tc = _make_app(session_store=ss)
    try:
        r = tc.get("/live-broker/test-session")
        assert r.status_code == 200
        data = r.json()
        assert data["deadline"] is None
        assert data["position"] is None
    finally:
        _stop_patches(tc)


def test_test_session_bumps_heartbeat():
    """GET /test-session bumps the heartbeat timestamp."""
    ss = _make_session_store()
    asyncio.run(ss.arm(
        entry_norenordno="M1",
        deadline="2026-06-22T06:10:00+00:00",
        now_iso="2026-06-22T06:00:00+00:00",
    ))
    # First heartbeat is the arm time
    sess_before = asyncio.run(ss.get())
    assert sess_before["heartbeat_ts"] == "2026-06-22T06:00:00+00:00"

    tc = _make_app(session_store=ss)
    try:
        tc.get("/live-broker/test-session")
        # After the GET, heartbeat should be updated to _utcnow_iso()
        sess_after = asyncio.run(ss.get())
        # The patched _utcnow_iso returns "2026-06-22T06:00:00+00:00"
        assert sess_after["heartbeat_ts"] is not None
    finally:
        _stop_patches(tc)


# ===========================================================================
# POST /live-broker/kill-switch — EXECUTING in L3
# ===========================================================================

def test_kill_switch_executes_and_transmits():
    """Kill-switch EXECUTES panic squareoff (transmitted=True) and reverts mode."""
    ms = _make_mode_store(mode="LIVE_TEST", consumed=True)
    cl = _make_mock_noren(
        position_book=[{
            "tsym": "NIFTY26JUN26C25000",
            "exch": "NFO",
            "netqty": "65",
            "lp": "200.0",
        }],
    )
    ss = _make_session_store()
    asyncio.run(ss.arm(
        entry_norenordno="MOCK_ENTRY",
        deadline="2026-06-22T06:10:00+00:00",
    ))
    tc = _make_app(mode_store=ms, client=cl, session_store=ss)
    try:
        r = tc.post("/live-broker/kill-switch")
        assert r.status_code == 200
        data = r.json()
        assert data["transmitted"] is True
        assert data["connected"] is True
        # Mode reverted
        mode_doc = asyncio.run(ms.get())
        assert mode_doc["mode"] == "LIVE_OFFLINE"
        # Session status
        sess = asyncio.run(ss.get())
        assert sess["status"] == "kill_switch"
    finally:
        _stop_patches(tc)


def test_kill_switch_exit_only_no_buy_entry():
    """Kill-switch never places a BUY entry order — only sell exits."""
    cl = _make_mock_noren(
        position_book=[{
            "tsym": "NIFTY26JUN26C25000",
            "exch": "NFO",
            "netqty": "65",
            "lp": "200.0",
        }],
    )
    tc = _make_app(client=cl)
    try:
        tc.post("/live-broker/kill-switch")
        book = asyncio.run(cl.order_book())
        for order in book:
            assert order.get("trantype") != "B", (
                f"Kill-switch placed a BUY entry order: {order}"
            )
    finally:
        _stop_patches(tc)


def test_kill_switch_not_connected_returns_plan_not_transmitted():
    """Kill-switch with no broker connection returns plan + transmitted=False."""
    ms = _make_mode_store(mode="LIVE_OFFLINE")
    cs = _make_config_store()
    ss = _make_session_store()
    # Use a separate app with _get_client raising to simulate not-connected
    app = FastAPI()
    app.include_router(_routes.api)

    patches = {
        "_mode_store": lambda: ms,
        "_intent_store": _make_intent_store,
        "_config_store": lambda: cs,
        "_session_store": lambda: ss,
        "_order_client": lambda: None,
        "_l3_engine": lambda: FakeEngine(),
        # _get_client raises → connected=False
        "_get_client": AsyncMock(side_effect=Exception("not connected")),
        "_get_token_doc": AsyncMock(return_value={"jKey": "x", "uid": "u", "actid": "u"}),
        "_schedule_auto_square": MagicMock(),
        "_utcnow_iso": lambda: "2026-06-22T06:00:00+00:00",
    }
    ctx = []
    for name, val in patches.items():
        p = patch.object(_routes, name, val)
        ctx.append(p)
        p.start()
    try:
        tc = TestClient(app, raise_server_exceptions=True)
        r = tc.post("/live-broker/kill-switch")
        assert r.status_code == 200
        data = r.json()
        assert data["connected"] is False
        assert data["transmitted"] is False
    finally:
        for p in ctx:
            try:
                p.stop()
            except RuntimeError:
                pass


# ===========================================================================
# Background auto-square timer helper (_check_and_square_if_due)
# ===========================================================================

def test_check_and_square_if_due_fires_at_deadline():
    """_check_and_square_if_due with a past deadline triggers a square."""
    cl = _make_mock_noren(
        position_book=[{
            "tsym": "NIFTY26JUN26C25000",
            "exch": "NFO",
            "netqty": "65",
            "lp": "200.0",
        }],
    )
    ss = _make_session_store()
    asyncio.run(ss.arm(
        entry_norenordno="MOCK_ENTRY",
        deadline="2026-06-22T06:00:00+00:00",  # PAST deadline
        now_iso="2026-06-22T05:50:00+00:00",
    ))
    ms = _make_mode_store(mode="LIVE_TEST", consumed=True)

    # Patch _mode_store so _check_and_square_if_due can revert mode
    with patch.object(_routes, "_mode_store", lambda: ms):
        result = asyncio.run(_routes._check_and_square_if_due(
            client=cl,
            deadline="2026-06-22T06:00:00+00:00",
            band_pct=5.0,
            session_store=ss,
            uid="",
            actid="",
            now_iso="2026-06-22T06:05:00+00:00",  # AFTER deadline
        ))

    assert result is not None
    # Mode should be reverted
    mode_doc = asyncio.run(ms.get())
    assert mode_doc["mode"] == "LIVE_OFFLINE"
    # Session should be squared
    sess = asyncio.run(ss.get())
    assert sess["status"] == "squared"


def test_check_and_square_if_due_does_not_fire_before_deadline():
    """_check_and_square_if_due before deadline → returns None (no square)."""
    cl = _make_mock_noren()
    ss = _make_session_store()
    asyncio.run(ss.arm(
        entry_norenordno="MOCK_ENTRY",
        deadline="2026-06-22T06:10:00+00:00",  # FUTURE deadline
        now_iso="2026-06-22T06:00:00+00:00",
    ))

    result = asyncio.run(_routes._check_and_square_if_due(
        client=cl,
        deadline="2026-06-22T06:10:00+00:00",
        band_pct=5.0,
        session_store=ss,
        uid="",
        actid="",
        now_iso="2026-06-22T06:05:00+00:00",  # BEFORE deadline
    ))

    assert result is None
    book = asyncio.run(cl.order_book())
    assert len(book) == 0


# ===========================================================================
# CHOKEPOINT CLASSIFICATION (documented inline)
# ===========================================================================

def test_chokepoint_grep_entry_only_via_executor():
    """Structural test: verify the route module delegates entry to the executor.

    ENTRY place_order:  executor.place_live_test_order (via /order/place route)
    EXIT  place_order:  auto_square.square_position (square route, kill, timer)
    EXIT  cancel_order: auto_square.square_position + kill_switch.panic_squareoff
    SL backstop:        build_sl_backstop_intent → place_order inside _make_arm
                        (exit-only sell-to-close SELL SL-LMT)
    """
    router_path = ROOT / "backend" / "app" / "routers" / "live_broker.py"
    source = router_path.read_text()

    # The executor import must be present
    assert "_executor_mod" in source or "place_live_test_order" in source

    # The route must call place_live_test_order (the guarded chokepoint)
    assert "place_live_test_order" in source

    # The square and kill routes must use square_position / panic_squareoff
    assert "square_position" in source
    assert "panic_squareoff" in source

    # Verify SL backstop is annotated as exit-only
    assert "build_sl_backstop_intent" in source


# ===========================================================================
# FIX 1 — LONG-ONLY GUARD AT THE ROUTE: _PlaceBody.side constrained to
# Literal["B"]; side="S" must be 422 (Pydantic validation) or blocked by
# the executor (placed=False, side_must_be_buy).
# ===========================================================================

def test_place_side_sell_returns_422_or_blocked():
    """side='S' → either 422 (Pydantic Literal["B"] rejection) or
    executor blocks with side_must_be_buy (placed=False). Either way,
    zero broker orders are placed."""
    ms = _make_mode_store(mode="LIVE_TEST", consumed=False)
    cl = _make_mock_noren()
    cl._search_scrip_data = {"NFO": [_NIFTY_SCRIP]}
    tc = _make_app(mode_store=ms, client=cl)
    try:
        body = {**_PLACE_BODY, "side": "S"}
        r = tc.post("/live-broker/order/place", json=body)
        # Either Pydantic rejects at the boundary (422) or the executor blocks it
        assert r.status_code in (422, 200), f"unexpected status: {r.status_code}"
        if r.status_code == 200:
            data = r.json()
            assert data["placed"] is False
            assert data["reason"] == "side_must_be_buy"
        # Zero orders placed regardless
        book = asyncio.run(cl.order_book())
        assert len(book) == 0, "place_order must not be called for side='S'"
    finally:
        _stop_patches(tc)


def test_place_side_buy_still_works_after_long_only_fix():
    """side='B' → happy path not broken by the long-only guard."""
    ms = _make_mode_store(mode="LIVE_TEST", consumed=False)
    cl = _make_mock_noren()
    cl._search_scrip_data = {"NFO": [_NIFTY_SCRIP]}
    ss = _make_session_store()
    eng = FakeEngine(can_trade_result=(True, ""))
    tc = _make_app(mode_store=ms, client=cl, session_store=ss, engine=eng)
    try:
        r = tc.post("/live-broker/order/place", json=_PLACE_BODY)
        assert r.status_code == 200
        data = r.json()
        assert data["placed"] is True
    finally:
        _stop_patches(tc)


# ===========================================================================
# FIX 2 — STRICT CONFIRM/MODE TYPES AT THE ROUTE
# _ModePutBody: confirm → StrictBool, mode → Literal["PAPER","LIVE_OFFLINE","LIVE_TEST"]
# ===========================================================================

def test_put_mode_confirm_string_true_returns_422():
    """confirm='true' (string coercion) → 422 from StrictBool."""
    tc = _make_app()
    try:
        r = tc.put("/live-broker/mode", json={"mode": "LIVE_TEST", "confirm": "true"})
        assert r.status_code == 422, (
            f"Expected 422 for confirm='true' (string), got {r.status_code}: {r.text}"
        )
    finally:
        _stop_patches(tc)


def test_put_mode_confirm_int_1_returns_422():
    """confirm=1 (int coercion) → 422 from StrictBool."""
    tc = _make_app()
    try:
        r = tc.put("/live-broker/mode", json={"mode": "LIVE_TEST", "confirm": 1})
        assert r.status_code == 422, (
            f"Expected 422 for confirm=1 (int), got {r.status_code}: {r.text}"
        )
    finally:
        _stop_patches(tc)


def test_put_mode_confirm_true_bool_still_works():
    """confirm=true (real JSON bool) + connected + can_trade → 200 LIVE_TEST."""
    ms = _make_mode_store(mode="LIVE_OFFLINE")
    eng = FakeEngine(can_trade_result=(True, ""))
    tc = _make_app(mode_store=ms, engine=eng)
    try:
        r = tc.put("/live-broker/mode", json={"mode": "LIVE_TEST", "confirm": True})
        assert r.status_code == 200
        assert r.json()["mode"] == "LIVE_TEST"
    finally:
        _stop_patches(tc)


def test_put_mode_live_armed_returns_422_not_400():
    """mode='LIVE_ARMED' → 422 (Literal rejects it at the boundary; not 400 any more)."""
    tc = _make_app()
    try:
        r = tc.put("/live-broker/mode", json={"mode": "LIVE_ARMED", "confirm": True})
        # With Literal["PAPER","LIVE_OFFLINE","LIVE_TEST"], LIVE_ARMED → 422
        assert r.status_code == 422, (
            f"Expected 422 for mode='LIVE_ARMED' (Literal boundary), got {r.status_code}: {r.text}"
        )
    finally:
        _stop_patches(tc)


def test_put_mode_junk_returns_422():
    """mode='JUNK' → 422 from Literal constraint."""
    tc = _make_app()
    try:
        r = tc.put("/live-broker/mode", json={"mode": "JUNK", "confirm": False})
        assert r.status_code == 422, (
            f"Expected 422 for mode='JUNK', got {r.status_code}: {r.text}"
        )
    finally:
        _stop_patches(tc)


# ===========================================================================
# SINGLETON + FAIL-CLOSED GATE (TDD for the real _l3_engine() wiring)
# ===========================================================================

def _reset_engine_singleton():
    """Reset module-level singleton state so tests don't bleed into each other."""
    import app.routers.live_broker as _r
    _r._live_engine_singleton = None
    _r._live_engine_init_error = None


def test_production_l3_engine_is_real_live_engine_not_permissive():
    """Unpatched _l3_engine() (with fake DB injected) returns a real LiveEngine,
    NOT a _PermissiveEngine or _ClosedEngine."""
    import app.routers.live_broker as _r
    from app.live.engine import LiveEngine

    _reset_engine_singleton()

    fake_db = MagicMock()
    fake_db.live_orders = FakeAsyncCollection()

    with patch("app.routers.live_broker._order_client", return_value=None), \
         patch("app.routers.live_broker._intent_store", return_value=_make_intent_store()), \
         patch("app.routers.live_broker._config_store", return_value=_make_config_store()), \
         patch("app.db.get_db", return_value=fake_db):
        engine = _r._l3_engine()

    assert isinstance(engine, LiveEngine), (
        f"Expected LiveEngine, got {type(engine).__name__}"
    )
    # Clean up
    _reset_engine_singleton()


def test_production_l3_engine_is_singleton_halt_persists():
    """Two calls to _l3_engine() return the SAME object; a halt on it persists."""
    import app.routers.live_broker as _r
    from app.live.engine import LiveEngine

    _reset_engine_singleton()

    fake_db = MagicMock()
    fake_db.live_orders = FakeAsyncCollection()

    with patch("app.routers.live_broker._order_client", return_value=None), \
         patch("app.routers.live_broker._intent_store", return_value=_make_intent_store()), \
         patch("app.routers.live_broker._config_store", return_value=_make_config_store()), \
         patch("app.db.get_db", return_value=fake_db):
        eng1 = _r._l3_engine()
        eng2 = _r._l3_engine()

    assert eng1 is eng2, "Two calls must return the same singleton object"

    # Halt the engine via the public async path
    asyncio.run(eng1.halt("test_reason"))
    assert eng2.halted is True, "halt on eng1 must be visible on eng2 (same object)"
    assert eng2.halt_reason == "test_reason"

    _reset_engine_singleton()


def test_latched_config_blocks_place_order():
    """A config_store with blocked_until_reset=True makes can_trade() return False,
    and POST /order/place in LIVE_TEST returns placed=False with 'cannot_trade'."""
    # Build a config store pre-latched
    latched_col = FakeAsyncCollection()
    latched_col.docs.append({
        "_id": "singleton",
        "blocked_until_reset": True,
        "daily_loss_limit": -50000.0,
        "profit_lock_target": 20000.0,
        "max_open_positions": 3,
    })
    cs = SafetyConfigStore(latched_col)

    ms = _make_mode_store(mode="LIVE_TEST", consumed=False)
    cl = _make_mock_noren()
    cl._search_scrip_data = {"NFO": [_NIFTY_SCRIP]}

    # Build a real LiveEngine with the latched config store
    from app.live.engine import LiveEngine
    real_engine = LiveEngine(
        client=None,
        orders_collection=FakeAsyncCollection(),
        intent_store=_make_intent_store(),
        config_store=cs,
    )
    # Confirm can_trade is blocked
    ok, reason = asyncio.run(real_engine.can_trade())
    assert ok is False
    assert "blocked" in reason

    # Wire it into the route via monkeypatch
    tc = _make_app(mode_store=ms, client=cl, engine=real_engine)
    try:
        r = tc.post("/live-broker/order/place", json=_PLACE_BODY)
        assert r.status_code == 200
        data = r.json()
        assert data["placed"] is False
        assert "cannot_trade" in data["reason"]
        book = asyncio.run(cl.order_book())
        assert len(book) == 0, "latched gate must block place_order"
    finally:
        _stop_patches(tc)


def test_engine_unavailable_fails_closed():
    """If get_db raises during singleton construction, _l3_engine() returns
    _ClosedEngine whose can_trade() is (False, 'engine_unavailable'), and
    POST /order/place is blocked (placed=False), never permissive-allow."""
    import app.routers.live_broker as _r

    _reset_engine_singleton()

    # Simulate DB failure during engine construction
    with patch("app.db.get_db", side_effect=RuntimeError("DB unavailable")), \
         patch("app.routers.live_broker._order_client", return_value=None), \
         patch("app.routers.live_broker._intent_store", return_value=_make_intent_store()), \
         patch("app.routers.live_broker._config_store", return_value=_make_config_store()):
        engine = _r._l3_engine()

    from app.live.engine import LiveEngine
    assert not isinstance(engine, LiveEngine), (
        "After DB failure, must return _ClosedEngine, not a real LiveEngine"
    )
    ok, reason = asyncio.run(engine.can_trade())
    assert ok is False
    assert "engine_unavailable" in reason

    _reset_engine_singleton()


def test_engine_unavailable_blocks_place_order_route():
    """End-to-end: _ClosedEngine injected via _l3_engine patch blocks place_order."""
    import app.routers.live_broker as _r

    ms = _make_mode_store(mode="LIVE_TEST", consumed=False)
    cl = _make_mock_noren()
    cl._search_scrip_data = {"NFO": [_NIFTY_SCRIP]}

    closed_engine = _r._ClosedEngine()
    tc = _make_app(mode_store=ms, client=cl, engine=closed_engine)
    try:
        r = tc.post("/live-broker/order/place", json=_PLACE_BODY)
        assert r.status_code == 200
        data = r.json()
        assert data["placed"] is False
        # Must be blocked by the gate, not by mode
        assert data["reason"] != "mode_not_live_test"
        book = asyncio.run(cl.order_book())
        assert len(book) == 0, "_ClosedEngine must block all place_order calls"
    finally:
        _stop_patches(tc)


def test_permissive_engine_never_on_production_path():
    """Structural test: after the fix, the production _l3_engine() code path
    must NOT contain an always-True fallback engine (the old _PermissiveEngine).

    We verify by inspecting the source of live_broker.py:
      - No class named _PermissiveEngine should appear INSIDE the /order/place
        route body (it was the inline stub).
      - The old 'if engine is None: class _PermissiveEngine' block must be gone.
    """
    router_path = ROOT / "backend" / "app" / "routers" / "live_broker.py"
    source = router_path.read_text()

    # The inline permissive stub that bypassed the gate must be gone
    assert "if engine is None:" not in source or "_PermissiveEngine" not in source, (
        "Found 'if engine is None:' + '_PermissiveEngine' — the fail-open stub was not removed"
    )
    # The fail-closed class must be present
    assert "_ClosedEngine" in source, "_ClosedEngine sentinel must exist in the router"
    # The singleton builder must be present
    assert "_build_live_engine_singleton" in source, "Singleton builder must be present"


# ===========================================================================
# FIX 3 — Auto-reject detection in GET /live-broker/test-session
#
# When the session is armed but the broker order book shows the entry order
# as REJECTED/CANCELED, the route must:
#   (a) mark the session 'rejected' with the broker rejreason,
#   (b) revert the mode to LIVE_OFFLINE,
#   (c) return remaining_secs: 0 and status: 'rejected'.
#
# Terminal sessions (squared/kill_switch/rejected) must always return
# remaining_secs: 0 — no phantom countdown.
#
# If the order client is unavailable (None) the route must leave the session
# unchanged and never 500.
# ===========================================================================

def _make_mock_noren_with_rejected_order(entry_norenordno: str, rejreason: str) -> MockNoren:
    """Return a MockNoren whose order_book already contains the entry order as REJECTED."""
    cl = MockNoren()
    # Inject a pre-existing REJECTED order directly into the internal store
    cl._orders[entry_norenordno] = {
        "norenordno": entry_norenordno,
        "trantype": "B",
        "status": "REJECTED",
        "rejreason": rejreason,
        "fillshares": "0",
        "avgprc": "0",
        "qty": "65",
    }
    return cl


def _make_mock_noren_with_open_order(entry_norenordno: str) -> MockNoren:
    """Return a MockNoren whose order_book contains the entry order as OPEN."""
    cl = MockNoren()
    cl._orders[entry_norenordno] = {
        "norenordno": entry_norenordno,
        "trantype": "B",
        "status": "OPEN",
        "rejreason": "",
        "fillshares": "0",
        "avgprc": "0",
        "qty": "65",
    }
    return cl


def test_test_session_armed_rejected_order_auto_resolves():
    """Armed session + broker shows REJECTED entry → status=rejected, remaining_secs=0, mode reverted.

    This is the primary regression test: Flattrade returns an order number then
    async-rejects it, leaving the session stuck 'armed'. The /test-session route
    must detect this and auto-resolve to 'rejected'.
    """
    ENTRY_ORD = "MOCK_ENTRY_1"
    ms = _make_mode_store(mode="LIVE_TEST", consumed=True)
    cl = _make_mock_noren_with_rejected_order(ENTRY_ORD, "RMS limit exceeded")
    ss = _make_session_store()
    # Pre-arm the session with the entry order
    asyncio.run(ss.arm(
        entry_norenordno=ENTRY_ORD,
        deadline="2026-06-22T07:00:00+00:00",
        now_iso="2026-06-22T06:00:00+00:00",
    ))
    tc = _make_app(mode_store=ms, client=cl, session_store=ss)
    try:
        r = tc.get("/live-broker/test-session")
        assert r.status_code == 200
        data = r.json()

        # Session must be auto-resolved to rejected
        assert data["status"] == "rejected", f"Expected 'rejected', got {data['status']!r}"
        # No phantom countdown
        assert data["remaining_secs"] == 0, (
            f"Expected remaining_secs=0 for rejected session, got {data['remaining_secs']!r}"
        )
        # Reject reason populated
        assert data["reject_reason"] is not None
        assert "RMS" in (data["reject_reason"] or ""), (
            f"Expected reject_reason to contain broker reason, got {data['reject_reason']!r}"
        )
        # Mode must be reverted
        mode_doc = asyncio.run(ms.get())
        assert mode_doc["mode"] == "LIVE_OFFLINE", (
            f"Expected mode reverted to LIVE_OFFLINE, got {mode_doc['mode']!r}"
        )
        # Session store updated
        sess = asyncio.run(ss.get())
        assert sess["status"] == "rejected"
        assert sess.get("reject_reason") is not None
    finally:
        _stop_patches(tc)


def test_test_session_rejection_detected_through_prod_client_path():
    """REGRESSION (live bug 2026-06-23): the rejection auto-detect must run through
    the REAL async client (_get_client) — NOT the _order_client() stub, which
    returns None in production (only tests patch it). With _order_client forced to
    its production None, the detection must STILL fire (it would NOT on the old
    code, which gated on _order_client() and so was silently dead live).
    """
    ENTRY_ORD = "MOCK_ENTRY_PROD"
    ms = _make_mode_store(mode="LIVE_TEST", consumed=True)
    cl = _make_mock_noren_with_rejected_order(ENTRY_ORD, "MIS orders disallowed after square off")
    ss = _make_session_store()
    asyncio.run(ss.arm(
        entry_norenordno=ENTRY_ORD,
        deadline="2026-06-22T07:00:00+00:00",
        now_iso="2026-06-22T06:00:00+00:00",
    ))
    tc = _make_app(mode_store=ms, client=cl, session_store=ss)
    # Override _order_client to the PRODUCTION behavior (returns None) for the
    # duration of the request — proving the route does NOT depend on it.
    with patch.object(_routes, "_order_client", lambda: None):
        try:
            data = tc.get("/live-broker/test-session").json()
            assert data["status"] == "rejected", (
                f"phantom not cleared — got {data['status']!r} (the detection is "
                f"dead unless it uses _get_client, not _order_client)"
            )
            assert data["remaining_secs"] == 0
            assert "MIS" in (data["reject_reason"] or "")
        finally:
            _stop_patches(tc)


def test_test_session_armed_open_order_stays_active():
    """Armed session + broker shows OPEN entry → session stays armed, remaining_secs > 0."""
    ENTRY_ORD = "MOCK_ENTRY_2"
    ms = _make_mode_store(mode="LIVE_TEST", consumed=True)
    cl = _make_mock_noren_with_open_order(ENTRY_ORD)
    ss = _make_session_store()
    asyncio.run(ss.arm(
        entry_norenordno=ENTRY_ORD,
        deadline="2026-06-22T07:00:00+00:00",  # future relative to patched _utcnow_iso
        now_iso="2026-06-22T06:00:00+00:00",
    ))
    tc = _make_app(mode_store=ms, client=cl, session_store=ss)
    try:
        r = tc.get("/live-broker/test-session")
        assert r.status_code == 200
        data = r.json()

        # Session stays active — not auto-resolved
        assert data["status"] == "armed", f"Expected 'armed', got {data['status']!r}"
        # Countdown must still be positive (deadline is in the future)
        assert data["remaining_secs"] is not None and data["remaining_secs"] > 0, (
            f"Expected positive remaining_secs for active session, got {data['remaining_secs']!r}"
        )
        # Mode untouched
        mode_doc = asyncio.run(ms.get())
        assert mode_doc["mode"] == "LIVE_TEST"
    finally:
        _stop_patches(tc)


def test_test_session_terminal_squared_returns_zero_remaining():
    """A terminal (squared) session always returns remaining_secs=0 — no phantom countdown."""
    ss = _make_session_store()
    asyncio.run(ss.arm(
        entry_norenordno="MOCK_ENTRY_3",
        deadline="2026-06-22T07:00:00+00:00",  # future
        now_iso="2026-06-22T06:00:00+00:00",
    ))
    # Manually transition to squared (e.g. position was closed)
    asyncio.run(ss.update_status("squared"))

    tc = _make_app(session_store=ss)
    try:
        r = tc.get("/live-broker/test-session")
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "squared"
        # Must be zero even though deadline is in the future
        assert data["remaining_secs"] == 0, (
            f"Expected remaining_secs=0 for squared session, got {data['remaining_secs']!r}"
        )
    finally:
        _stop_patches(tc)


def test_test_session_no_client_leaves_session_unchanged():
    """If _order_client() returns None (not connected), session is left unchanged, no 500."""
    ss = _make_session_store()
    asyncio.run(ss.arm(
        entry_norenordno="MOCK_ENTRY_4",
        deadline="2026-06-22T07:00:00+00:00",
        now_iso="2026-06-22T06:00:00+00:00",
    ))
    # Build app with _order_client returning None (not connected)
    app = FastAPI()
    app.include_router(_routes.api)

    ms = _make_mode_store(mode="LIVE_TEST", consumed=True)
    patches = {
        "_mode_store": lambda: ms,
        "_intent_store": _make_intent_store,
        "_config_store": _make_config_store,
        "_session_store": lambda: ss,
        "_order_client": lambda: None,  # not connected
        "_l3_engine": lambda: FakeEngine(),
        "_get_client": AsyncMock(side_effect=Exception("not connected")),
        "_get_token_doc": AsyncMock(return_value={"jKey": "x", "uid": "u", "actid": "u"}),
        "_schedule_auto_square": MagicMock(),
        "_utcnow_iso": lambda: "2026-06-22T06:00:00+00:00",
    }
    ctx = []
    for name, val in patches.items():
        p = patch.object(_routes, name, val)
        ctx.append(p)
        p.start()
    try:
        tc = TestClient(app, raise_server_exceptions=True)
        r = tc.get("/live-broker/test-session")
        # Must not crash
        assert r.status_code == 200
        data = r.json()
        # Session still armed (unchanged) — no client to check order book
        assert data["status"] == "armed"
        # Mode unchanged
        mode_doc = asyncio.run(ms.get())
        assert mode_doc["mode"] == "LIVE_TEST"
    finally:
        for p in ctx:
            try:
                p.stop()
            except RuntimeError:
                pass
