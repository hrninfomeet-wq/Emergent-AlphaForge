"""Tests for app.db.ensure_indexes — the startup index creation.

Specifically guards the LIVE-execution indexes (added in the live-page upgrade):
  - live_orders.client_order_id UNIQUE — the dup-order race guard that
    idempotency.record_intent's DuplicateKeyError fallback depends on. Its
    absence in prod was a CRITICAL finding (concurrent/resumed same-cid submit
    could reach the broker twice).
  - live_trades per-deployment indexes — so the cap governor + /live/status
    counters do an index scan, not a full-collection scan every poll.

ensure_indexes() calls get_db() then issues create_index on each collection, so
we monkeypatch get_db with a fake recording-DB and assert the calls.
"""
from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import app.db as dbmod  # noqa: E402


class _FakeColl:
    def __init__(self):
        self.indexes = []

    async def create_index(self, keys, **opts):
        self.indexes.append((keys, opts))
        return "idx"


class _FakeDB:
    """Attribute access (db.<name>) lazily yields a recording collection."""

    def __init__(self):
        self.colls = {}

    def __getattr__(self, name):
        if name == "colls":  # guard before __init__ populates __dict__
            raise AttributeError(name)
        colls = self.__dict__["colls"]
        if name not in colls:
            colls[name] = _FakeColl()
        return colls[name]


def _run_ensure_indexes(monkeypatch):
    fake = _FakeDB()
    monkeypatch.setattr(dbmod, "get_db", lambda: fake)
    asyncio.run(dbmod.ensure_indexes())
    return fake


def test_live_orders_unique_index_on_client_order_id(monkeypatch):
    fake = _run_ensure_indexes(monkeypatch)
    lo = fake.colls["live_orders"].indexes
    assert any(keys == "client_order_id" and opts.get("unique") is True for keys, opts in lo), (
        f"live_orders must get a UNIQUE index on client_order_id (the dup-order guard); got {lo}"
    )


def test_live_trades_per_deployment_index(monkeypatch):
    fake = _run_ensure_indexes(monkeypatch)
    lt = fake.colls["live_trades"].indexes
    assert any(keys == [("deployment_id", 1), ("created_at", -1)] for keys, _ in lt), (
        f"live_trades must be indexed by (deployment_id, created_at) so the cap "
        f"governor + /live/status do an index scan, not a full-collection scan; got {lt}"
    )


def test_existing_indexes_unaffected(monkeypatch):
    """Smoke: the pre-existing indexes (e.g. paper_trades, options_1m) still created."""
    fake = _run_ensure_indexes(monkeypatch)
    assert fake.colls["paper_trades"].indexes, "paper_trades indexes must still be created"
    assert fake.colls["options_1m"].indexes, "options_1m indexes must still be created"
