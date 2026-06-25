"""Tests for GET /live-broker/reconcile wiring (slice 2, item 1).

The route used to call reconcile() with BOTH internal lists empty, so the
dashboard "Reconciled ✓" chip was structurally incapable of detecting anything
and falsely flagged every broker position the moment one existed.

The fix feeds REAL internal_positions from the SOFTWARE GUARD registry (the
watched set), while deliberately keeping internal_orders empty (the live_orders
store is an idempotency ledger with no running om-feed, so feeding SUBMITTED
docs would falsely flag every FILLED order). These tests lock that contract:

  - a guard-watched position that matches the broker reconciles CLEAN
  - a broker position NOT in the registry → unknown_broker_position (same signal
    as the UNGUARDED banner)
  - a flat broker with a still-watched registry entry is NOT falsely flagged
  - a qty divergence → position_qty_mismatch
  - a COMPLETE (terminal) broker order is NOT flagged — the exact false-positive
    we would have manufactured by feeding the SUBMITTED ledger as internal_orders

RULES: never instantiate a real FlattradeClient; patch the module getters.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import patch, AsyncMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

import app.routers.live_broker as _routes  # noqa: E402


class _FakeClient:
    """Minimal async broker client exposing only what the route reads."""

    def __init__(self, orders: List[Dict[str, Any]], positions: List[Dict[str, Any]]) -> None:
        self._orders = orders
        self._positions = positions

    async def order_book(self) -> List[Dict[str, Any]]:
        return list(self._orders)

    async def position_book(self) -> List[Dict[str, Any]]:
        return list(self._positions)


class _FakeRegistry:
    """Stand-in for the LiveMonitorRegistry — only snapshot() is read here."""

    def __init__(self, entries: List[Dict[str, Any]]) -> None:
        self._entries = entries

    def snapshot(self) -> List[Dict[str, Any]]:
        return list(self._entries)


def _reconcile(*, registry_entries, broker_orders, broker_positions) -> Dict[str, Any]:
    """Call the real route with a fake client + fake guard registry patched in."""
    app = FastAPI()
    app.include_router(_routes.api)
    client = _FakeClient(broker_orders, broker_positions)
    registry = _FakeRegistry(registry_entries)
    patches = {
        "_get_client": AsyncMock(return_value=client),
        "_get_live_registry": lambda: registry,
    }
    started = []
    for name, val in patches.items():
        p = patch.object(_routes, name, val)
        started.append(p)
        p.start()
    try:
        tc = TestClient(app, raise_server_exceptions=True)
        resp = tc.get("/live-broker/reconcile")
        assert resp.status_code == 200, resp.text
        return resp.json()
    finally:
        for p in started:
            p.stop()


def _types(report: Dict[str, Any]) -> List[str]:
    return [m["type"] for m in report.get("mismatches", [])]


def test_watched_position_matching_broker_is_clean():
    report = _reconcile(
        registry_entries=[{"tsym": "NIFTY24JUN24000CE", "qty": 75}],
        broker_orders=[],
        broker_positions=[{"tsym": "NIFTY24JUN24000CE", "netqty": "75"}],
    )
    assert report["ok"] is True
    assert report["mismatches"] == []


def test_unwatched_broker_position_is_flagged():
    # Registry empty (guard not watching) but broker holds a live position —
    # the same "exposed but unwatched" signal the UNGUARDED banner shows.
    report = _reconcile(
        registry_entries=[],
        broker_orders=[],
        broker_positions=[{"tsym": "NIFTY24JUN24000CE", "netqty": "75"}],
    )
    assert report["ok"] is False
    assert "unknown_broker_position" in _types(report)


def test_flat_broker_with_watched_registry_is_not_falsely_flagged():
    # Guard still watching an entry, broker shows no position (e.g. async-fill
    # window or just squared). The pure diff must NOT flag an internal position
    # absent from the broker book.
    report = _reconcile(
        registry_entries=[{"tsym": "NIFTY24JUN24000CE", "qty": 75}],
        broker_orders=[],
        broker_positions=[],
    )
    assert report["ok"] is True
    assert report["mismatches"] == []


def test_qty_divergence_is_flagged():
    report = _reconcile(
        registry_entries=[{"tsym": "NIFTY24JUN24000CE", "qty": 75}],
        broker_orders=[],
        broker_positions=[{"tsym": "NIFTY24JUN24000CE", "netqty": "150"}],
    )
    assert report["ok"] is False
    assert "position_qty_mismatch" in _types(report)


def test_completed_broker_order_is_not_flagged():
    # The crux of keeping internal_orders empty: a FILLED (COMPLETE) broker order
    # must reconcile clean. Feeding the SUBMITTED idempotency ledger would have
    # falsely flagged this as internal_order_not_at_broker.
    report = _reconcile(
        registry_entries=[],
        broker_orders=[{"norenordno": "25JUN0001", "status": "COMPLETE"}],
        broker_positions=[],
    )
    assert report["ok"] is True
    assert report["mismatches"] == []


def test_working_broker_order_with_no_internal_claim_is_surfaced():
    # With internal_orders=[], a NON-terminal (resting/working) broker order that
    # the app has no claim on is surfaced as unknown_broker_order — the documented
    # behavior (genuinely unexpected in the software-guard model).
    report = _reconcile(
        registry_entries=[],
        broker_orders=[{"norenordno": "25JUN0002", "status": "OPEN"}],
        broker_positions=[],
    )
    assert report["ok"] is False
    assert "unknown_broker_order" in _types(report)
