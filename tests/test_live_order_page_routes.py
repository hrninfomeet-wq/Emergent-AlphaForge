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


class _FakeCol:
    """Minimal async Mongo-ish collection (find_one / update_one upsert) for the
    OverallSettingsStore, mirroring the FakeAsyncCollection used in the L3 tests."""

    def __init__(self):
        self._docs = {}

    async def find_one(self, query):
        return self._docs.get(query.get("_id"))

    async def update_one(self, query, update, upsert=False):
        _id = query.get("_id")
        doc = self._docs.get(_id, {"_id": _id})
        doc.update(update.get("$set", {}))
        self._docs[_id] = doc
        return type("R", (), {"matched_count": 1})()


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


def _make_app(*, approval_store=None, place_mock=None, limits_data=None):
    app = FastAPI()
    app.include_router(_routes.api)
    # The preview / approval-queue paths now run a margin pre-check via
    # client.limits(); give the mock enough cash by default so the existing
    # happy-path tests still pass (NIFTY 1-lot @ ref_ltp 200 needs
    # 200*65*1.05 = ₹13,650).  Tests that exercise the margin gate pass their
    # own limits_data (low cash / empty).
    cl = MockNoren(
        search_scrip_data={"NFO": [_NIFTY_SCRIP]},
        limits_data=limits_data if limits_data is not None else {"cash": "50000"},
    )
    store = approval_store if approval_store is not None else ApprovalStore()

    from app.live.overall_settings_store import OverallSettingsStore
    overall_stores = {
        "overall": OverallSettingsStore(_FakeCol(), scope="overall"),
        "broker_level": OverallSettingsStore(_FakeCol(), scope="broker_level"),
    }
    patches = {
        "_get_client": AsyncMock(return_value=cl),
        "_get_token_doc": AsyncMock(return_value={"jKey": "k", "uid": "TESTUID", "actid": "TESTUID"}),
        "_approval_store": lambda: store,
        "_overall_store": lambda scope="overall": overall_stores.get(scope, overall_stores["overall"]),
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
            # the margin pre-check ran and passed (cash 50000 >= 13,650 required)
            assert any(v["check"] == "margin" and v["ok"] for v in d["verdicts"])
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
# margin pre-check (preview + approval-queue) — runs after the ticket validates,
# reads client.limits(), and blocks fail-closed when cash can't cover 1 lot.
# ---------------------------------------------------------------------------
class TestMarginPreCheck:
    def test_sufficient_cash_passes(self):
        tc = _make_app(limits_data={"cash": "50000"})
        try:
            d = tc.post("/live-broker/order/preview", json=_TICKET).json()
            assert d["ok"] is True
            mv = [v for v in d["verdicts"] if v["check"] == "margin"]
            assert mv and mv[0]["ok"] is True
        finally:
            _stop(tc)

    def test_insufficient_cash_blocks_preview(self):
        # NIFTY 1-lot @ ref_ltp 200 needs 200*65*1.05 = ₹13,650; cash 5,000 < that.
        tc = _make_app(limits_data={"cash": "5000"})
        try:
            d = tc.post("/live-broker/order/preview", json=_TICKET).json()
            assert d["ok"] is False
            mv = [v for v in d["verdicts"] if v["check"] == "margin"]
            assert mv and mv[0]["ok"] is False
            assert "insufficient funds" in mv[0]["detail"]
        finally:
            _stop(tc)

    def test_insufficient_cash_not_queued(self):
        tc = _make_app(limits_data={"cash": "5000"})
        try:
            d = tc.post("/live-broker/order/approvals", json=_TICKET).json()
            assert d["ok"] is False
            assert any(v["check"] == "margin" and not v["ok"] for v in d["verdicts"])
            # a ticket that fails the margin gate is NOT queued for approval
            assert tc.get("/live-broker/order/approvals").json()["pending"] == []
        finally:
            _stop(tc)

    def test_unreadable_limits_fails_closed(self):
        # empty limits → no cash field → fail closed (block), never let it through
        tc = _make_app(limits_data={})
        try:
            d = tc.post("/live-broker/order/preview", json=_TICKET).json()
            assert d["ok"] is False
            assert any(v["check"] == "margin" and not v["ok"] for v in d["verdicts"])
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

    def test_sell_ticket_not_auto_placed_but_reverts_to_pending(self):
        place = AsyncMock(return_value={"placed": True})
        tc = _make_app(place_mock=place)
        try:
            created = tc.post("/live-broker/order/approvals", json={**_TICKET, "side": "S"}).json()
            aid, tok = created["approval_id"], created["token"]
            r = tc.post(f"/live-broker/order/approvals/{aid}/approve", json={"token": tok}).json()
            assert r["placed"] is False
            assert "BUY" in r["reason"]
            assert r["retryable"] is True
            assert place.await_count == 0
            # NOT stranded: the approval is reverted to pending and stays in the queue
            # so the operator can Reject it.
            pend = tc.get("/live-broker/order/approvals").json()["pending"]
            assert any(p["approval_id"] == aid for p in pend)
            rj = tc.post(f"/live-broker/order/approvals/{aid}/reject").json()
            assert rj["ok"] is True
        finally:
            _stop(tc)

    def test_blocked_placement_reverts_and_is_retryable(self):
        """If the executor blocks (HTTPException), the redeemed approval reverts to
        pending so the operator can arm LIVE_TEST and retry with the SAME token —
        it must NOT vanish stranded."""
        from fastapi import HTTPException as _HTTPExc
        place = AsyncMock(side_effect=_HTTPExc(400, "mode_not_live_test"))
        tc = _make_app(place_mock=place)
        try:
            created = tc.post("/live-broker/order/approvals", json=_TICKET).json()
            aid, tok = created["approval_id"], created["token"]
            r = tc.post(f"/live-broker/order/approvals/{aid}/approve", json={"token": tok}).json()
            assert r["placed"] is False
            assert "placement blocked" in r["reason"]
            assert r["retryable"] is True
            # still pending → retry with the same token now succeeds
            pend = tc.get("/live-broker/order/approvals").json()["pending"]
            assert any(p["approval_id"] == aid for p in pend)
            place.side_effect = None
            place.return_value = {"placed": True, "norenordno": "MOCK9"}
            retry = tc.post(f"/live-broker/order/approvals/{aid}/approve", json={"token": tok}).json()
            assert retry["placed"] is True
            # now consumed → gone from pending, cannot be re-placed
            assert tc.get("/live-broker/order/approvals").json()["pending"] == []
            again = tc.post(f"/live-broker/order/approvals/{aid}/approve", json={"token": tok}).json()
            assert again["placed"] is False
        finally:
            _stop(tc)


# ---------------------------------------------------------------------------
# Overall-controls settings (Phase 2)
# ---------------------------------------------------------------------------
class TestOverallSettings:
    def test_get_returns_disabled_default(self):
        tc = _make_app()
        try:
            d = tc.get("/live-broker/overall-settings").json()
            assert d["sl"]["enabled"] is False
            assert d["trailing"]["mode"] == "none"
            assert d["reentry"]["enabled"] is False
        finally:
            _stop(tc)

    def test_put_then_get_roundtrip(self):
        tc = _make_app()
        try:
            cfg = {
                "sl": {"enabled": True, "mode": "mtm", "value": 5000},
                "trailing": {"mode": "lock_trail", "unit": "mtm", "lock_at": 2000,
                             "lock_floor": 1000, "trail_per": 500, "trail_by": 300, "base_sl": 0},
            }
            put = tc.put("/live-broker/overall-settings", json={"config": cfg}).json()
            assert put["sl"]["enabled"] is True
            got = tc.get("/live-broker/overall-settings").json()
            assert got["sl"]["value"] == 5000
            assert got["trailing"]["mode"] == "lock_trail"
        finally:
            _stop(tc)

    def test_put_invalid_config_400(self):
        tc = _make_app()
        try:
            bad = {"trailing": {"mode": "rocket"}}
            r = tc.put("/live-broker/overall-settings", json={"config": bad})
            assert r.status_code == 400
        finally:
            _stop(tc)

    def test_scopes_are_independent(self):
        tc = _make_app()
        try:
            tc.put("/live-broker/overall-settings?scope=overall",
                   json={"config": {"sl": {"enabled": True, "mode": "mtm", "value": 111}}})
            tc.put("/live-broker/overall-settings?scope=broker_level",
                   json={"config": {"sl": {"enabled": True, "mode": "mtm", "value": 222}}})
            o = tc.get("/live-broker/overall-settings?scope=overall").json()
            b = tc.get("/live-broker/overall-settings?scope=broker_level").json()
            assert o["sl"]["value"] == 111
            assert b["sl"]["value"] == 222
        finally:
            _stop(tc)


# ---------------------------------------------------------------------------
# GTT / OCO backstop routes (Phase 3, wired to the confirmed PiConnect schema)
# ---------------------------------------------------------------------------
class TestGtt:
    def test_list_gtt_reads_book(self):
        tc = _make_app()
        try:
            # MockNoren.gtt_book() defaults to [] → empty book + null note
            d = tc.get("/live-broker/gtt").json()
            assert d["gtt"] == []
            assert "note" in d
        finally:
            _stop(tc)

    def test_place_oco_preview_does_not_transmit(self):
        tc = _make_app()
        try:
            body = {"kind": "oco", "exch": "NFO", "tsym": "NIFTY26JUN26C25000",
                    "qty": 65, "prd": "M", "sl_trigger": 98.02, "sl_limit": 97.93,
                    "tp_trigger": 150.02, "tp_limit": 149.97}
            d = tc.post("/live-broker/gtt", json=body).json()
            assert d["placed"] is False
            assert d["preview"] is True
            assert d["kind"] == "oco"
            # documented OCO alert type + two-leg structure, tick-rounded
            assert d["intent"]["ai_t"] == "LMT_BOS_O"
            assert float(d["intent"]["place_order_params"]["prc"]) == 97.95
            assert float(d["intent"]["place_order_params_leg2"]["prc"]) == 149.95
        finally:
            _stop(tc)

    def test_place_oco_transmit_returns_alert_id(self):
        tc = _make_app()
        try:
            body = {"kind": "oco", "exch": "NFO", "tsym": "NIFTY26JUN26C25000",
                    "qty": 65, "prd": "M", "sl_trigger": 98.0, "sl_limit": 97.9,
                    "tp_trigger": 150.0, "tp_limit": 149.9, "transmit": True}
            d = tc.post("/live-broker/gtt", json=body).json()
            assert d["placed"] is True
            assert d["result"]["al_id"].startswith("MOCKAL")
        finally:
            _stop(tc)

    def test_place_single_gtt_requires_ai_t(self):
        tc = _make_app()
        try:
            # single GTT with no ai_t → 400 (direction must be explicit)
            body = {"kind": "gtt", "exch": "NFO", "tsym": "X", "qty": 65,
                    "trantype": "S", "d_trigger": 98.0, "prc_limit": 97.9, "prd": "M"}
            r = tc.post("/live-broker/gtt", json=body)
            assert r.status_code == 400
            # with ai_t it previews fine (LTP_B_O = confirmed below-trigger type)
            body["ai_t"] = "LTP_B_O"
            d = tc.post("/live-broker/gtt", json=body).json()
            assert d["intent"]["ai_t"] == "LTP_B_O"
            assert d["intent"]["validity"] == "GTT"
            assert d["intent"]["prc"]                # flat documented form (catalog #16)
        finally:
            _stop(tc)

    def test_place_gtt_mis_rejected(self):
        tc = _make_app()
        try:
            body = {"kind": "oco", "exch": "NFO", "tsym": "X", "qty": 65, "prd": "I",
                    "sl_trigger": 98.0, "sl_limit": 97.9, "tp_trigger": 150.0,
                    "tp_limit": 149.9}  # MIS → rejected
            r = tc.post("/live-broker/gtt", json=body)
            assert r.status_code == 400
        finally:
            _stop(tc)

    def test_cancel_gtt_transmits(self):
        tc = _make_app()
        try:
            d = tc.delete("/live-broker/gtt/AL123").json()
            assert d["canceled"] is True
            assert d["result"]["al_id"] == "AL123"
        finally:
            _stop(tc)

    def test_cancel_oco_routes_to_oco(self):
        tc = _make_app()
        try:
            d = tc.delete("/live-broker/gtt/AL999?kind=oco").json()
            assert d["canceled"] is True
            assert d["kind"] == "oco"
        finally:
            _stop(tc)

    def test_cancel_gtt_rejects_blank_id(self):
        tc = _make_app()
        try:
            r = tc.delete("/live-broker/gtt/%20")  # blank → 400
            assert r.status_code == 400
        finally:
            _stop(tc)
