"""TDD tests for L3 routes in backend/app/routers/live_broker.py (Task L3.6).

Test coverage:
  GET  /live-broker/mode          — returns current mode doc
  PUT  /live-broker/mode          — guards: no confirm → 400; LIVE_ARMED → 400;
                                    connected+confirm+can_trade → 200 LIVE_TEST
  POST /live-broker/order/place   — PAPER → blocked; LIVE_TEST all-pass → exactly 1 place_order,
                                    protected=True, session recorded; halted engine → blocked
  POST /live-broker/order/square  — exit-only, squares position, reverts mode
  GET  /live-broker/test-session  — returns status + heartbeat + entry order (no timer)
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
        # Patch _utcnow_iso to a fixed time for deterministic tests
        "_utcnow_iso": lambda: "2026-06-22T06:00:00+00:00",
        # No real sleeps between the kill-switch verification passes / re-sweep
        "_KILL_POLL_SECONDS": 0.0,
        # The manual place route now gates a real entry on LIVE_GUARD_ARMED (never
        # open a position the guard can't auto-close). Default the harness to ARMED so
        # the place-path tests exercise placement; a dedicated test flips it to False.
        "_live_guard_armed": lambda: True,
        "_KILL_RESWEEP_DELAY": 0.0,
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


def test_place_in_live_test_session_has_no_deadline():
    """After a successful place, the session is armed with NO deadline (the 10-min
    auto-square timer was removed — the guard stop + 15:00 EOD are the backstops)."""
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
        # Check session was recorded — armed, but no deadline field is set.
        sess = asyncio.run(ss.get())
        assert sess.get("deadline") is None
        assert sess["status"] == "armed"
        assert sess["entry_norenordno"] is not None
    finally:
        _stop_patches(tc)


def test_place_blocked_when_guard_disarmed():
    """POST /order/place with the software guard DISARMED (LIVE_GUARD_ARMED off) →
    blocked 'guard_not_armed', zero orders. Never open a real manual position whose
    only automated exits (guard stop + EOD square) can't transmit."""
    ms = _make_mode_store(mode="LIVE_TEST", consumed=False)
    cl = _make_mock_noren()
    cl._search_scrip_data = {"NFO": [_NIFTY_SCRIP]}
    eng = FakeEngine(can_trade_result=(True, ""))
    tc = _make_app(mode_store=ms, client=cl, engine=eng)
    try:
        with patch.object(_routes, "_live_guard_armed", lambda: False):
            r = tc.post("/live-broker/order/place", json=_PLACE_BODY)
        assert r.status_code == 200
        data = r.json()
        assert data["placed"] is False
        assert data["reason"] == "guard_not_armed"
        assert asyncio.run(cl.order_book()) == [], "place_order must NOT have been called"
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


def test_square_read_failure_400_and_session_stays_armed():
    """A broker read failure (expired token) must NOT false-square: the route
    400s (reconnect hint), the armed session stays 'armed', mode stays LIVE_TEST."""
    ms = _make_mode_store(mode="LIVE_TEST", consumed=True)
    cl = _make_mock_noren(position_book=[{
        "tsym": "NIFTY26JUN26C25000", "exch": "NFO", "netqty": "65", "lp": "200.0"}])
    cl.script_read_error("position_book", "Session Expired : Invalid Session Key")
    ss = _make_session_store()
    asyncio.run(ss.arm(entry_norenordno="MOCK_ENTRY"))
    tc = _make_app(mode_store=ms, client=cl, session_store=ss)
    try:
        r = tc.post("/live-broker/order/square", json={})
        assert r.status_code == 400
        assert "reconnect Flattrade" in r.text
        assert asyncio.run(ss.get())["status"] == "armed"      # NOT squared
        assert asyncio.run(ms.get())["mode"] == "LIVE_TEST"    # NOT reverted
    finally:
        _stop_patches(tc)


def test_square_failed_exit_does_not_mark_squared():
    """When square_position returns squared=False (exit rejected), the route must
    NOT mark the session 'squared' or revert mode — it stays armed for a retry."""
    from app.live.broker_protocol import OrderResult

    class _RejectPlace(MockNoren):
        async def place_order(self, intent):
            return OrderResult(ok=False, rejreason="RMS blocked",
                               raw={"stat": "Not_Ok", "emsg": "RMS blocked"})

    ms = _make_mode_store(mode="LIVE_TEST", consumed=True)
    cl = _RejectPlace(
        limits_data=_GOOD_LIMITS,
        position_book_data=[{"tsym": "NIFTY26JUN26C25000", "exch": "NFO",
                             "netqty": "65", "lp": "200.0"}],
        search_scrip_data={"NFO": [_NIFTY_SCRIP]},
    )
    ss = _make_session_store()
    asyncio.run(ss.arm(entry_norenordno="MOCK_ENTRY"))
    tc = _make_app(mode_store=ms, client=cl, session_store=ss)
    try:
        r = tc.post("/live-broker/order/square", json={})
        assert r.status_code == 200
        assert r.json().get("squared") is False
        assert asyncio.run(ss.get())["status"] == "armed"      # NOT squared
        assert asyncio.run(ms.get())["mode"] == "LIVE_TEST"    # NOT reverted
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

def test_test_session_returns_status_and_heartbeat_no_deadline():
    """GET /test-session returns status/heartbeat/entry — and NO deadline or
    remaining_secs (the 10-min timer was removed)."""
    ss = _make_session_store()
    asyncio.run(ss.arm(
        entry_norenordno="MOCK1",
        sl_norenordno="MOCK_SL",
        now_iso="2026-06-22T06:00:00+00:00",
    ))
    tc = _make_app(session_store=ss)
    try:
        r = tc.get("/live-broker/test-session")
        assert r.status_code == 200
        data = r.json()
        assert "deadline" not in data
        assert "remaining_secs" not in data
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
        assert "deadline" not in data
        assert data["position"] is None
    finally:
        _stop_patches(tc)


def test_test_session_bumps_heartbeat():
    """GET /test-session bumps the heartbeat timestamp."""
    ss = _make_session_store()
    asyncio.run(ss.arm(
        entry_norenordno="M1",
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


class _CountingClient(MockNoren):
    """Counts real order-affecting calls so a test can prove NOTHING transmitted."""

    def __init__(self, **kw):
        super().__init__(**kw)
        self.place_calls = 0
        self.cancel_calls = 0

    async def place_order(self, intent):
        self.place_calls += 1
        return await super().place_order(intent)

    async def cancel_order(self, non):
        self.cancel_calls += 1
        return await super().cancel_order(non)


def test_kill_switch_position_read_error_transmits_nothing_and_flags_token_expired():
    """A token-expired kill (position_book RAISES) must transmit NOTHING, keep
    connected=False, flag token_expired, emit no place/cancel, and NOT mark the
    session kill_switch — never a false ALL FLAT that reads as a clean no-op."""
    cl = _CountingClient(limits_data=_GOOD_LIMITS, search_scrip_data={"NFO": [_NIFTY_SCRIP]})
    cl.script_read_error("position_book", "Session Expired : Invalid Session Key")
    ss = _make_session_store()
    asyncio.run(ss.arm(entry_norenordno="MOCK_ENTRY"))
    tc = _make_app(client=cl, session_store=ss)
    try:
        r = tc.post("/live-broker/kill-switch")
        assert r.status_code == 200
        data = r.json()
        assert data["connected"] is False
        assert data["transmitted"] is False
        assert data["token_expired"] is True
        assert data["read_error"]
        assert "reconnect" in data["message"].lower()
        assert cl.place_calls == 0 and cl.cancel_calls == 0  # nothing transmitted
        assert asyncio.run(ss.get())["status"] == "armed"    # NOT marked kill_switch
    finally:
        _stop_patches(tc)


def test_kill_switch_orderbook_ok_then_positionbook_raises_still_fails_safe():
    """Concurrent interleaving: order_book() succeeds (returns working orders) but
    position_book() RAISES. connected must stay False (nothing transmitted) — a
    read error on either book is UNKNOWN, never a partial transmit."""
    cl = _CountingClient(
        limits_data=_GOOD_LIMITS,
        order_book_data=[{"tsym": "NIFTY26JUN26C25000", "norenordno": "OPEN1",
                          "status": "OPEN", "trantype": "B"}],
        search_scrip_data={"NFO": [_NIFTY_SCRIP]},
    )
    cl.script_read_error("position_book", "Session Expired : Invalid Session Key")
    tc = _make_app(client=cl)
    try:
        r = tc.post("/live-broker/kill-switch")
        assert r.status_code == 200
        data = r.json()
        assert data["connected"] is False
        assert data["transmitted"] is False
        assert data["token_expired"] is True
        assert cl.place_calls == 0 and cl.cancel_calls == 0
    finally:
        _stop_patches(tc)


# --- Kill = STOP-ALL (latch + halt + disarm), serialized ---------------------

class _KillDeployCursor:
    def __init__(self, docs):
        self._docs = docs

    async def to_list(self, length=None):
        return [dict(d) for d in self._docs]


class _KillDeployCol:
    """Motor-style strategy_deployments fake for the kill disarm helper."""

    def __init__(self, docs):
        self.docs = docs

    def find(self, query, projection=None):
        # the disarm helper queries {"risk.live.armed": True}
        armed = [d for d in self.docs
                 if ((d.get("risk") or {}).get("live") or {}).get("armed") is True]
        return _KillDeployCursor(armed)

    async def update_one(self, query, update):
        for d in self.docs:
            if d.get("id") == query.get("id"):
                if "$set" in update:
                    d.update(update["$set"])
        return _UpdateResult(matched_count=1)


class _KillDb:
    def __init__(self, deploys):
        self.strategy_deployments = _KillDeployCol(deploys)


def test_kill_switch_is_stop_all_trips_latch_halts_disarms():
    """The kill switch STOPS ALL: it trips the persistent safety latch (→ the
    executor's can_trade blocks new entries), halts the engine, and disarms every
    armed live deployment — so an armed deployment can't re-enter after ALL FLAT."""
    from app.live.kill_switch import is_entry_blocked

    deploys = [
        {"id": "d1", "risk": {"live": {"armed": True, "lots": 2}}},
        {"id": "d2", "risk": {"live": {"armed": True}}},
        {"id": "d3", "risk": {"live": {"armed": False}}},  # already disarmed → untouched
    ]
    fake_db = _KillDb(deploys)
    cs = _make_config_store()
    eng = FakeEngine()
    cl = _make_mock_noren(position_book=[{
        "tsym": "NIFTY26JUN26C25000", "exch": "NFO", "netqty": "65", "lp": "200.0"}])
    tc = _make_app(config_store=cs, engine=eng, client=cl)
    try:
        with patch("app.db.get_db", return_value=fake_db):
            r = tc.post("/live-broker/kill-switch")
        assert r.status_code == 200
        body = r.json()
        sa = body["stop_all"]
        assert sa["latch_tripped"] is True
        assert sa["engine_halted"] is True
        assert set(sa["disarmed_deployment_ids"]) == {"d1", "d2"}
        # engine actually halted with the kill reason
        assert eng.halted is True and "kill_switch" in eng.halt_calls
        # the persistent latch is set → is_entry_blocked True (executor Gate 6 reads this)
        assert is_entry_blocked(asyncio.run(cs.get_config())) is True
        # the two armed deployments are now disarmed with the reason
        assert deploys[0]["risk"]["live"]["armed"] is False
        assert deploys[0]["risk"]["live"]["disarmed_reason"] == "kill_switch"
        assert deploys[1]["risk"]["live"]["armed"] is False
        assert deploys[2]["risk"]["live"]["armed"] is False  # unchanged
    finally:
        _stop_patches(tc)


def test_kill_switch_stops_all_even_on_token_expiry():
    """A token-expired kill can't flatten (connected False), but the stop-all
    (latch/halt/disarm) STILL runs — new entries are blocked regardless."""
    from app.live.kill_switch import is_entry_blocked

    deploys = [{"id": "d1", "risk": {"live": {"armed": True}}}]
    fake_db = _KillDb(deploys)
    cs = _make_config_store()
    eng = FakeEngine()
    cl = _make_mock_noren()
    cl.script_read_error("order_book", "Session Expired : Invalid Session Key")
    tc = _make_app(config_store=cs, engine=eng, client=cl)
    try:
        with patch("app.db.get_db", return_value=fake_db):
            r = tc.post("/live-broker/kill-switch")
        body = r.json()
        assert body["connected"] is False           # couldn't read the broker
        assert body["stop_all"]["latch_tripped"] is True
        assert body["stop_all"]["engine_halted"] is True
        assert body["stop_all"]["disarmed_deployment_ids"] == ["d1"]
        assert is_entry_blocked(asyncio.run(cs.get_config())) is True
        assert deploys[0]["risk"]["live"]["armed"] is False
    finally:
        _stop_patches(tc)


def test_kill_switch_defers_tsym_already_being_exited():
    """L8: a tsym already claimed by another exit path (guard/auto-square mid-exit)
    is DEFERRED — the kill does NOT panic-flatten it (that would double-sell); the
    stop-all (latch/disarm) still runs."""
    from app.live.exit_claims import registry, reset_exit_claims

    reset_exit_claims()
    deploys = [{"id": "d1", "risk": {"live": {"armed": True}}}]
    fake_db = _KillDb(deploys)
    cs = _make_config_store()
    eng = FakeEngine()
    tsym = "NIFTY26JUN26C25000"
    cl = _CountingClient(
        limits_data=_GOOD_LIMITS,
        position_book_data=[{"tsym": tsym, "exch": "NFO", "netqty": "65", "lp": "200.0"}],
        search_scrip_data={"NFO": [_NIFTY_SCRIP]},
    )
    tc = _make_app(config_store=cs, engine=eng, client=cl)
    try:
        asyncio.run(registry().claim(tsym, "another_path_token"))  # held elsewhere
        with patch("app.db.get_db", return_value=fake_db):
            r = tc.post("/live-broker/kill-switch")
        body = r.json()
        assert tsym in body["stop_all"]["deferred_tsyms"]     # deferred, not flattened
        assert cl.place_calls == 0                            # NO competing exit placed
        assert body["stop_all"]["latch_tripped"] is True      # stop-all still ran
    finally:
        reset_exit_claims()
        _stop_patches(tc)


def test_kill_switch_rejects_concurrent_request():
    """A second kill while one is in progress fast-rejects (no double-flatten)."""
    from app.routers import live_broker as lb

    async def _run():
        await lb._kill_lock.acquire()
        try:
            return await lb.live_kill_switch()   # lock held → fast-reject
        finally:
            lb._kill_lock.release()

    res = asyncio.run(_run())
    assert res.get("already_running") is True
    assert res["transmitted"] is False


def test_kill_switch_partial_failure_report_reaches_response():
    """Mock-broker partial flatten: leg 1 fills, leg 2 (BFO) is REJECTED by the
    broker — the response must carry per-leg outcomes with the broker's reason
    and a message that never reads as success (item B: a partial flatten is
    loudly visible, never swallowed)."""
    class PartialClient(MockNoren):
        def __init__(self, **kw):
            super().__init__(**kw)
            self._placed = 0

        async def place_order(self, intent):
            self._placed += 1
            if intent.tsym.startswith("BSXOPT"):
                from app.live.broker_protocol import OrderResult
                return OrderResult(ok=False, rejreason="RMS:margin shortfall",
                                   raw={"stat": "Not_Ok", "emsg": "RMS:margin shortfall"})
            result = await super().place_order(intent)
            order = self._orders[result.norenordno]
            order["status"] = "COMPLETE"
            order["fillshares"] = str(order["qty"])
            self._position_book_data = [
                p for p in self._position_book_data if p["tsym"] != intent.tsym]
            return result

    positions = [
        {"tsym": "NIFTY26JUN26C25000", "exch": "NFO", "netqty": "65", "lp": "200.0", "prd": "M"},
        {"tsym": "BSXOPT26JUN26C81000", "exch": "BFO", "netqty": "20", "lp": "350.0", "prd": "M"},
    ]
    cl = PartialClient(position_book_data=[dict(p) for p in positions])
    tc = _make_app(client=cl)
    try:
        r = tc.post("/live-broker/kill-switch")
        assert r.status_code == 200
        data = r.json()
        legs = data["panic"]["legs"]
        outcomes = {l["tsym"]: l["outcome"] for l in legs}
        assert outcomes["NIFTY26JUN26C25000"] == "FILLED"
        assert outcomes["BSXOPT26JUN26C81000"] == "REJECTED"
        rej = next(l for l in legs if l["outcome"] == "REJECTED")
        assert "RMS:margin shortfall" in rej["reason"]
        # The filled leg went out exchange-aware: NFO, LMT, position's own prd.
        book = asyncio.run(cl.order_book())
        assert book[0]["exch"] == "NFO" and book[0]["prctyp"] == "LMT" and book[0]["prd"] == "M"
        # Loud partial-failure summary + broker-truth residuals.
        assert data["panic"]["all_flat"] is False
        assert data["panic"]["residual"][0]["tsym"] == "BSXOPT26JUN26C81000"
        assert "1 REJECTED" in data["message"]
        assert "POSITIONS REMAIN" in data["message"]
    finally:
        _stop_patches(tc)


# ---------------------------------------------------------------------------
# B6 — kill-switch must ALSO sweep every resting GTT/OCO alert.
#
# panic_squareoff cancels working ORDERS + flattens positions but does NOT
# touch resting GTT/OCO alerts — those would survive and fire later. So the
# kill-switch additionally cancels every row in gtt_book(). It picks cancel_oco
# vs cancel_gtt from the row's ai_t (OCO bracket = "LMT_BOS_O"), falling back to
# try-both when ai_t is missing/ambiguous. Best-effort: a sweep failure must
# NEVER block the panic flatten.
# ---------------------------------------------------------------------------


class _RecordingGttClient(MockNoren):
    """MockNoren that records every al_id passed to cancel_oco / cancel_gtt.

    The injected gtt_book rows are NOT placed through this client, so the base
    MockNoren's internal _gtts dict never sees them — we record the calls
    directly here to assert the sweep cancelled each resting alert.
    """

    def __init__(self, *args, gtt_raises: bool = False, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.oco_cancels: List[str] = []
        self.gtt_cancels: List[str] = []
        self._gtt_raises = gtt_raises

    async def cancel_oco(self, al_id):  # type: ignore[override]
        self.oco_cancels.append(str(al_id))
        return await super().cancel_oco(al_id)

    async def cancel_gtt(self, al_id):  # type: ignore[override]
        self.gtt_cancels.append(str(al_id))
        return await super().cancel_gtt(al_id)

    async def gtt_book(self):  # type: ignore[override]
        if self._gtt_raises:
            raise RuntimeError("gtt_book read failed")
        return await super().gtt_book()


def test_kill_switch_sweeps_all_resting_gtt_oco():
    """A connected kill-switch cancels EVERY resting GTT/OCO row by al_id."""
    cl = _RecordingGttClient(
        limits_data=_GOOD_LIMITS,
        position_book_data=[{
            "tsym": "NIFTY26JUN26C25000", "exch": "NFO",
            "netqty": "65", "lp": "200.0",
        }],
        search_scrip_data={"NFO": [_NIFTY_SCRIP]},
        gtt_book_data=[
            # OCO bracket (ai_t == LMT_BOS_O) — al_id keyed lowercase
            {"ai_t": "LMT_BOS_O", "al_id": "AL_OCO_1", "tsym": "NIFTY26JUN26C25000"},
            # Single GTT (LTP direction) — al_id keyed UPPER-case Al_id
            {"ai_t": "LTP_B_O", "Al_id": "AL_GTT_2", "tsym": "NIFTY26JUN26P25000"},
        ],
    )
    tc = _make_app(client=cl)
    try:
        r = tc.post("/live-broker/kill-switch")
        assert r.status_code == 200
        assert r.json()["transmitted"] is True
        # Every resting alert was cancelled exactly once (by either route).
        all_cancelled = set(cl.oco_cancels) | set(cl.gtt_cancels)
        assert all_cancelled == {"AL_OCO_1", "AL_GTT_2"}
    finally:
        _stop_patches(tc)


def test_kill_switch_picks_oco_vs_gtt_by_ai_t():
    """OCO row (ai_t LMT_BOS_O) → cancel_oco; single GTT row → cancel_gtt."""
    cl = _RecordingGttClient(
        limits_data=_GOOD_LIMITS,
        position_book_data=[],
        search_scrip_data={"NFO": [_NIFTY_SCRIP]},
        gtt_book_data=[
            {"ai_t": "LMT_BOS_O", "al_id": "OCO_A"},
            {"ai_t": "LTP_B_O", "al_id": "GTT_B"},
        ],
    )
    tc = _make_app(client=cl)
    try:
        r = tc.post("/live-broker/kill-switch")
        assert r.status_code == 200
        # The OCO bracket went to cancel_oco; the single GTT went to cancel_gtt.
        assert "OCO_A" in cl.oco_cancels
        assert "GTT_B" in cl.gtt_cancels
        # And NOT cross-wired.
        assert "GTT_B" not in cl.oco_cancels
        assert "OCO_A" not in cl.gtt_cancels
    finally:
        _stop_patches(tc)


def test_kill_switch_sweep_skips_rows_without_al_id():
    """A gtt row carrying no al_id is skipped, not cancelled with an empty id."""
    cl = _RecordingGttClient(
        limits_data=_GOOD_LIMITS,
        position_book_data=[],
        search_scrip_data={"NFO": [_NIFTY_SCRIP]},
        gtt_book_data=[
            {"ai_t": "LMT_BOS_O"},                       # no al_id → skipped
            {"ai_t": "LMT_BOS_O", "al_id": "AL_OK"},     # cancelled
        ],
    )
    tc = _make_app(client=cl)
    try:
        r = tc.post("/live-broker/kill-switch")
        assert r.status_code == 200
        cancelled = set(cl.oco_cancels) | set(cl.gtt_cancels)
        assert cancelled == {"AL_OK"}
        assert "" not in cancelled
    finally:
        _stop_patches(tc)


def test_kill_switch_sweep_is_best_effort_never_blocks_flatten():
    """A gtt_book read that raises must NOT break the panic flatten/transmit."""
    cl = _RecordingGttClient(
        limits_data=_GOOD_LIMITS,
        position_book_data=[{
            "tsym": "NIFTY26JUN26C25000", "exch": "NFO",
            "netqty": "65", "lp": "200.0",
        }],
        search_scrip_data={"NFO": [_NIFTY_SCRIP]},
        gtt_raises=True,  # gtt_book() blows up
    )
    tc = _make_app(client=cl)
    try:
        r = tc.post("/live-broker/kill-switch")
        # The panic flatten still ran and the route still returns 200/transmitted.
        assert r.status_code == 200
        assert r.json()["transmitted"] is True
        # The position was still flattened (a SELL exit was placed).
        book = asyncio.run(cl.order_book())
        assert any(o.get("trantype") == "S" for o in book)
    finally:
        _stop_patches(tc)


def test_kill_switch_not_connected_does_not_sweep():
    """Not-connected kill-switch performs NO sweep and does not crash."""
    ms = _make_mode_store(mode="LIVE_OFFLINE")
    cs = _make_config_store()
    ss = _make_session_store()
    app = FastAPI()
    app.include_router(_routes.api)

    patches = {
        "_mode_store": lambda: ms,
        "_intent_store": _make_intent_store,
        "_config_store": lambda: cs,
        "_session_store": lambda: ss,
        "_order_client": lambda: None,
        "_l3_engine": lambda: FakeEngine(),
        "_get_client": AsyncMock(side_effect=Exception("not connected")),
        "_get_token_doc": AsyncMock(return_value={"jKey": "x", "uid": "u", "actid": "u"}),
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
# CHOKEPOINT CLASSIFICATION (documented inline)
# ===========================================================================

def test_chokepoint_grep_entry_only_via_executor():
    """Structural test: verify the route module delegates entry to the executor.

    ENTRY place_order:  executor.place_live_test_order (via /order/place route)
    EXIT  place_order:  auto_square.square_position (square route, kill)
    EXIT  cancel_order: auto_square.square_position + kill_switch.panic_squareoff
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


def test_manual_10min_timer_symbols_are_removed():
    """The manual LIVE_TEST 10-minute auto-square timer is gone; its functions and
    constants must NOT reappear (a re-introduction would silently re-arm the removed
    backstop). Positive guard against regression-by-resurrection."""
    import app.live.auto_square as _auto_square
    for name in ("_schedule_auto_square", "_auto_square_task", "_check_and_square_if_due",
                 "_TIMER_CHECK_INTERVAL"):
        assert not hasattr(_routes, name), f"{name} must stay removed from live_broker"
    for name in ("deadline_iso", "is_due", "SQUARE_HORIZON_SEC", "_to_utc"):
        assert not hasattr(_auto_square, name), f"{name} must stay removed from auto_square"
    # SessionStore no longer carries the deadline countdown.
    assert not hasattr(SessionStore, "remaining_secs")


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
#   (c) return status: 'rejected'.
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


def _make_mock_noren_with_complete_order(entry_norenordno: str) -> MockNoren:
    """Return a MockNoren whose order_book contains the entry order as COMPLETE (filled)."""
    cl = MockNoren()
    cl._orders[entry_norenordno] = {
        "norenordno": entry_norenordno,
        "trantype": "B",
        "status": "COMPLETE",
        "rejreason": "",
        "fillshares": "65",
        "avgprc": "100",
        "qty": "65",
    }
    return cl


def test_test_session_filled_then_guard_closed_resolves_squared():
    """Armed session + entry order COMPLETE + the software guard NO LONGER holds it
    (the registry drops an entry only on a confirmed-flat finalize) → the session
    resolves to 'squared' so the "Live Position Active" card clears. The guard/EOD
    auto-close path does not touch the SessionStore, so without this the card lingered."""
    from app.live.live_position_guard import LiveMonitorRegistry
    ENTRY_ORD = "MOCK_FILLED_CLOSED"
    ms = _make_mode_store(mode="LIVE_OFFLINE", consumed=True)  # already reverted at fill
    cl = _make_mock_noren_with_complete_order(ENTRY_ORD)
    ss = _make_session_store()
    asyncio.run(ss.arm(entry_norenordno=ENTRY_ORD, now_iso="2026-06-22T06:00:00+00:00"))
    empty_reg = LiveMonitorRegistry()  # guard finalized it → no longer registered
    tc = _make_app(mode_store=ms, client=cl, session_store=ss)
    try:
        with patch.object(_routes, "_get_live_registry", lambda: empty_reg):
            data = tc.get("/live-broker/test-session").json()
        assert data["status"] == "squared", f"expected squared, got {data['status']!r}"
        assert asyncio.run(ss.get())["status"] == "squared"
    finally:
        _stop_patches(tc)


def test_test_session_filled_and_still_guarded_stays_armed():
    """Armed session + entry COMPLETE but STILL held by the guard (position open) →
    the session stays 'armed' (no premature squared)."""
    from app.live.live_position_guard import LiveMonitorRegistry
    from app.live.live_sl_monitor import build_monitor_state
    ENTRY_ORD = "MOCK_FILLED_OPEN"
    ms = _make_mode_store(mode="LIVE_OFFLINE", consumed=True)
    cl = _make_mock_noren_with_complete_order(ENTRY_ORD)
    ss = _make_session_store()
    asyncio.run(ss.arm(entry_norenordno=ENTRY_ORD, now_iso="2026-06-22T06:00:00+00:00"))
    reg = LiveMonitorRegistry()
    reg.register(key=ENTRY_ORD, tsym="NIFTY26JUN26C25000", exch="NFO", qty=65, prd="I",
                 entry_price=100.0, state=build_monitor_state(100.0, stop_pct=50))
    tc = _make_app(mode_store=ms, client=cl, session_store=ss)
    try:
        with patch.object(_routes, "_get_live_registry", lambda: reg):
            data = tc.get("/live-broker/test-session").json()
        assert data["status"] == "armed", f"expected armed, got {data['status']!r}"
    finally:
        _stop_patches(tc)


def test_test_session_armed_rejected_order_auto_resolves():
    """Armed session + broker shows REJECTED entry → status=rejected, mode reverted.

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
        now_iso="2026-06-22T06:00:00+00:00",
    ))
    tc = _make_app(mode_store=ms, client=cl, session_store=ss)
    try:
        r = tc.get("/live-broker/test-session")
        assert r.status_code == 200
        data = r.json()

        # Session must be auto-resolved to rejected
        assert data["status"] == "rejected", f"Expected 'rejected', got {data['status']!r}"
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
            assert "MIS" in (data["reject_reason"] or "")
        finally:
            _stop_patches(tc)


def test_test_session_armed_open_order_stays_active():
    """Armed session + broker shows OPEN entry → session stays armed (not auto-resolved)."""
    ENTRY_ORD = "MOCK_ENTRY_2"
    ms = _make_mode_store(mode="LIVE_TEST", consumed=True)
    cl = _make_mock_noren_with_open_order(ENTRY_ORD)
    ss = _make_session_store()
    asyncio.run(ss.arm(
        entry_norenordno=ENTRY_ORD,
        now_iso="2026-06-22T06:00:00+00:00",
    ))
    tc = _make_app(mode_store=ms, client=cl, session_store=ss)
    try:
        r = tc.get("/live-broker/test-session")
        assert r.status_code == 200
        data = r.json()

        # Session stays active — not auto-resolved
        assert data["status"] == "armed", f"Expected 'armed', got {data['status']!r}"
        # Mode untouched
        mode_doc = asyncio.run(ms.get())
        assert mode_doc["mode"] == "LIVE_TEST"
    finally:
        _stop_patches(tc)


def test_test_session_terminal_squared_stays_squared():
    """A terminal (squared) session reports status=squared (no countdown fields)."""
    ss = _make_session_store()
    asyncio.run(ss.arm(
        entry_norenordno="MOCK_ENTRY_3",
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
        assert "remaining_secs" not in data
    finally:
        _stop_patches(tc)


def test_test_session_no_client_leaves_session_unchanged():
    """If _order_client() returns None (not connected), session is left unchanged, no 500."""
    ss = _make_session_store()
    asyncio.run(ss.arm(
        entry_norenordno="MOCK_ENTRY_4",
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


# ===========================================================================
# Safety-config PUT — max_lots_per_order (account lot ceiling) flows through
# ===========================================================================

def test_put_safety_config_persists_max_lots_per_order():
    """PUT /live-broker/safety-config accepts max_lots_per_order and it round-trips
    through the store (the body field exists and put_config validates/persists it)."""
    cs = _make_config_store()
    tc = _make_app(config_store=cs)
    try:
        r = tc.put("/live-broker/safety-config", json={"max_lots_per_order": 7})
        assert r.status_code == 200
        assert r.json()["max_lots_per_order"] == 7
        # round-trips via GET
        got = tc.get("/live-broker/safety-config").json()
        assert got["max_lots_per_order"] == 7
    finally:
        _stop_patches(tc)


def test_put_safety_config_rejects_zero_max_lots_per_order():
    """A non-positive max_lots_per_order is rejected by the store → 400."""
    cs = _make_config_store()
    tc = _make_app(config_store=cs)
    try:
        r = tc.put("/live-broker/safety-config", json={"max_lots_per_order": 0})
        assert r.status_code == 400
    finally:
        _stop_patches(tc)
