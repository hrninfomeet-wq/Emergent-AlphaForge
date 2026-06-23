"""TDD tests for the live order page routes (P1.7) in live_broker.py.

Covers: GET /order-rules/{underlying}, POST /order/preview (choke-point dry-run),
and the approval queue (create / list / approve / reject) with the one-shot token
gate sitting in FRONT of the single executor chokepoint (live_order_place).

RULES (mirrors test_live_l3_routes): never instantiate a real FlattradeClient;
all broker access goes through MockNoren patched on the module getters. The
real-order executor (live_order_place) is patched so NO order is ever placed.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.live.approval_store import ApprovalStore
from app.live.mock_noren import MockNoren
import app.routers.live_broker as _routes


_NIFTY_SCRIP = {
    "tsym": "NIFTY26JUN26C25000",
    "token": "1",
    "ls": "65",
    "symname": "NIFTY",
    "optt": "CE",
    "exd": "26-JUN-2026",
    "dname": "NIFTY 26JUN26 25000 CE",
    "ti": "0.05",
}

_TICKET = {
    "underlying": "NIFTY",
    "strike": 25000.0,
    "option_type": "CE",
    "side": "B",
    "expiry_date": "2026-06-26",
    "lots": 1,
    "order_type": "LIMIT",
    "product": "MIS",
    "ref_ltp": 200.0,
    "band_pct": 5.0,
    "fat_finger_cap": 5,
    "levels": {},
}


def _make_app(*, approval_store=None, place_mock=None):
    app = FastAPI()
    app.include_router(_routes.api)
    cl = MockNoren(search_scrip_data={"NFO": [_NIFTY_SCRIP]})
    store = approval_store if approval_store is not None else ApprovalStore()

    patches = {
        "_get_client": AsyncMock(return_value=cl),
        "_get_token_doc": AsyncMock(return_value={"jKey": "k", "uid": "TESTUID", "actid": "TESTUID"}),
        "_approval_store": lambda: store,
    }
    if place_mock is not None:
        patches["live_order_place"] = place_mock

    started = []
    for name, val in patches.items():
        p = patch.object(_routes, name, val)
        p.start()
        started.append(p)
    tc = TestClient(app, raise_server_exceptions=True)
    tc._patches = started
    tc._store = store
    return tc


def _stop(tc):
    for p in getattr(tc, "_patches", []):
        try:
            p.stop()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# order-rules
# ---------------------------------------------------------------------------
class TestOrderRules:
    def test_nifty_rules(self):
        tc = _make_app()
        try:
            r = tc.get("/live-broker/order-rules/NIFTY")
            assert r.status_code == 200
            d = r.json()
            assert d["exch"] == "NFO"
            assert d["lot_size"] == 65
            assert d["products"] == ["NRML", "MIS"]
            assert "MARKET" in d["price_types"]
        finally:
            _stop(tc)

    def test_unknown_underlying_404(self):
        tc = _make_app()
        try:
            assert tc.get("/live-broker/order-rules/GOLD").status_code == 404
        finally:
            _stop(tc)


# ---------------------------------------------------------------------------
# preview (choke-point dry-run)
# ---------------------------------------------------------------------------
class TestPreview:
    def test_valid_limit_buy(self):
        tc = _make_app()
        try:
            r = tc.post("/live-broker/order/preview", json=_TICKET)
            assert r.status_code == 200
            d = r.json()
            assert d["ok"] is True
            assert len(d["children"]) == 1
            assert all(v["ok"] for v in d["verdicts"])
            # tick-valid price in the would-send jdata
            prc = float(d["children"][0]["prc"])
            assert round(round(prc / 0.05) * 0.05, 2) == round(prc, 2)
        finally:
            _stop(tc)

    def test_co_product_blocked(self):
        tc = _make_app()
        try:
            body = {**_TICKET, "product": "CO"}
            d = tc.post("/live-broker/order/preview", json=body).json()
            assert d["ok"] is False
            assert any(v["check"] == "exchange_product" and not v["ok"] for v in d["verdicts"])
        finally:
            _stop(tc)

    def test_market_order_preview(self):
        tc = _make_app()
        try:
            body = {**_TICKET, "order_type": "MARKET"}
            d = tc.post("/live-broker/order/preview", json=body).json()
            assert d["ok"] is True
            assert d["children"][0]["prctyp"] == "MKT"
        finally:
            _stop(tc)


# ---------------------------------------------------------------------------
# approval queue: create / list / reject
# ---------------------------------------------------------------------------
class TestApprovalQueue:
    def test_create_returns_token_and_queues(self):
        tc = _make_app()
        try:
            d = tc.post("/live-broker/order/approvals", json=_TICKET).json()
            assert d["ok"] is True
            assert d["approval_id"]
            assert d["token"]
            assert d["summary"]["underlying"] == "NIFTY"
            # appears in the pending list, WITHOUT a token
            lst = tc.get("/live-broker/order/approvals").json()["pending"]
            assert len(lst) == 1
            assert lst[0]["approval_id"] == d["approval_id"]
            assert "token" not in lst[0]
        finally:
            _stop(tc)

    def test_invalid_ticket_not_queued(self):
        tc = _make_app()
        try:
            body = {**_TICKET, "product": "CO"}
            d = tc.post("/live-broker/order/approvals", json=body).json()
            assert d["ok"] is False
            assert tc.get("/live-broker/order/approvals").json()["pending"] == []
        finally:
            _stop(tc)

    def test_reject_then_not_listed(self):
        tc = _make_app()
        try:
            d = tc.post("/live-broker/order/approvals", json=_TICKET).json()
            rj = tc.post(f"/live-broker/order/approvals/{d['approval_id']}/reject").json()
            assert rj["ok"] is True
            assert tc.get("/live-broker/order/approvals").json()["pending"] == []
        finally:
            _stop(tc)


# ---------------------------------------------------------------------------
# approve → executor chokepoint (one-shot token gate)
# ---------------------------------------------------------------------------
class TestApproveGate:
    def test_valid_token_places_via_executor_once(self):
        place = AsyncMock(return_value={"placed": True, "norenordno": "MOCK1", "protected": True})
        tc = _make_app(place_mock=place)
        try:
            created = tc.post("/live-broker/order/approvals", json=_TICKET).json()
            aid, tok = created["approval_id"], created["token"]
            r = tc.post(f"/live-broker/order/approvals/{aid}/approve", json={"token": tok}).json()
            assert r["placed"] is True
            assert place.await_count == 1
            # approval is now consumed -> no longer pending
            assert tc.get("/live-broker/order/approvals").json()["pending"] == []
        finally:
            _stop(tc)

    def test_bad_token_does_not_reach_executor(self):
        place = AsyncMock(return_value={"placed": True})
        tc = _make_app(place_mock=place)
        try:
            created = tc.post("/live-broker/order/approvals", json=_TICKET).json()
            aid = created["approval_id"]
            r = tc.post(f"/live-broker/order/approvals/{aid}/approve", json={"token": "WRONG"}).json()
            assert r["placed"] is False
            assert r["reason"] == "bad_token"
            assert place.await_count == 0
            # still pending — legit operator can still approve
            assert len(tc.get("/live-broker/order/approvals").json()["pending"]) == 1
        finally:
            _stop(tc)

    def test_replay_token_places_only_once(self):
        place = AsyncMock(return_value={"placed": True, "norenordno": "MOCK1"})
        tc = _make_app(place_mock=place)
        try:
            created = tc.post("/live-broker/order/approvals", json=_TICKET).json()
            aid, tok = created["approval_id"], created["token"]
            first = tc.post(f"/live-broker/order/approvals/{aid}/approve", json={"token": tok}).json()
            second = tc.post(f"/live-broker/order/approvals/{aid}/approve", json={"token": tok}).json()
            assert first["placed"] is True
            assert second["placed"] is False
            assert "not pending" in second["reason"]
            assert place.await_count == 1   # placed exactly once despite the replay
        finally:
            _stop(tc)

    def test_sell_ticket_not_auto_placed(self):
        place = AsyncMock(return_value={"placed": True})
        tc = _make_app(place_mock=place)
        try:
            created = tc.post("/live-broker/order/approvals", json={**_TICKET, "side": "S"}).json()
            aid, tok = created["approval_id"], created["token"]
            r = tc.post(f"/live-broker/order/approvals/{aid}/approve", json={"token": tok}).json()
            assert r["placed"] is False
            assert "BUY" in r["reason"]
            assert place.await_count == 0
        finally:
            _stop(tc)
