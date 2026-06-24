"""Tests for the strategy-authoring compile-preview + install endpoints (Phase 2A).

Pattern mirrors test_strategy_admin_routes.py: build a FastAPI app, include the
router, patch sa._db with a FakeDB, use TestClient.  Heavy side effects
(_write_plugin_file, get_registry) are mocked so we never touch the real
plugins directory or the real strategy registry.
"""
import sys
from pathlib import Path
from unittest.mock import patch, AsyncMock, Mock, MagicMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

import app.routers.strategies_admin as sa

# ---------------------------------------------------------------------------
# Fake DB (copied from test_strategy_admin_routes.py)
# ---------------------------------------------------------------------------

def _matches(doc, query):
    return all(doc.get(k) == v for k, v in query.items())


class _Cursor:
    def __init__(self, docs):
        self._docs = docs

    async def to_list(self, length=None):
        return list(self._docs)


class FakeColl:
    def __init__(self):
        self.docs = []

    async def find_one(self, q, projection=None):
        return next((dict(d) for d in self.docs if _matches(d, q)), None)

    def find(self, q, projection=None):
        return _Cursor([dict(d) for d in self.docs if _matches(d, q)])

    async def update_one(self, q, update, upsert=False):
        for d in self.docs:
            if _matches(d, q):
                d.update(update.get("$set", {}))
                return Mock(matched_count=1)
        if upsert:
            nd = {k: v for k, v in q.items() if not k.startswith("$")}
            nd.update(update.get("$set", {}))
            self.docs.append(nd)
        return Mock(matched_count=0)

    async def delete_one(self, q):
        before = len(self.docs)
        self.docs = [d for d in self.docs if not _matches(d, q)]
        return Mock(deleted_count=before - len(self.docs))


class FakeDB:
    def __init__(self):
        self._c = {}

    def __getattr__(self, name):
        c = self.__dict__.setdefault("_c", {})
        return c.setdefault(name, FakeColl())


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------

def _make_app(db=None):
    app = FastAPI()
    app.include_router(sa.api)
    db = db if db is not None else FakeDB()
    p = patch.object(sa, "_db", lambda: db)
    p.start()
    tc = TestClient(app, raise_server_exceptions=True)
    tc._patch = p
    tc._db = db
    return tc


def _stop(tc):
    tc._patch.stop()


# ---------------------------------------------------------------------------
# A minimal valid StrategySpec payload
# (uses close > ema9 as the entry CE condition and a spot target exit)
# ---------------------------------------------------------------------------

VALID_SPEC = {
    "id": "test_ema_rsi",
    "name": "Test EMA RSI",
    "version": "1.0.0",
    "description": "A test strategy",
    "supported_instruments": ["NIFTY"],
    "supported_modes": ["SCALP"],
    "supported_timeframes": ["1m"],
    "params": [
        {"name": "threshold", "type": "float", "min": 0.0, "max": 100.0, "default": 0.5},
    ],
    "entry_ce": [
        {"left": "close", "op": ">", "right": "ema9", "label": "price above ema9"},
    ],
    "entry_pe": [],
    "gate_skip_regimes": [],
    "cooldown_bars": 0,
    "exits": {
        "spot_target_pts": 30.0,
        "spot_stop_pts": 15.0,
    },
}

# A spec referencing a column that does not exist in the grounding catalog.
INVALID_SPEC_UNKNOWN_COL = {
    "id": "test_bad",
    "name": "Bad Strategy",
    "version": "1.0.0",
    "description": "",
    "supported_instruments": ["NIFTY"],
    "supported_modes": ["SCALP"],
    "supported_timeframes": ["1m"],
    "params": [],
    "entry_ce": [
        {"left": "nonexistent_indicator_xyz", "op": ">", "right": 0.0},
    ],
    "entry_pe": [],
    "gate_skip_regimes": [],
    "cooldown_bars": 0,
    "exits": {
        "spot_target_pts": 30.0,
        "spot_stop_pts": 15.0,
    },
}


# ---------------------------------------------------------------------------
# 1. Compile happy path
# ---------------------------------------------------------------------------

def test_compile_ok():
    tc = _make_app()
    try:
        r = tc.post("/strategies/author/compile", json={"spec": VALID_SPEC})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["ok"] is True
        assert body["errors"] == []
        assert body["code"] is not None
        assert "class " in body["code"]
        assert "is_builtin = False" in body["code"]
        assert body["strategy_id"] == "test_ema_rsi"
    finally:
        _stop(tc)


# ---------------------------------------------------------------------------
# 2. Compile reports errors for an invalid spec — never raises
# ---------------------------------------------------------------------------

def test_compile_reports_errors():
    tc = _make_app()
    try:
        r = tc.post("/strategies/author/compile", json={"spec": INVALID_SPEC_UNKNOWN_COL})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["ok"] is False
        assert len(body["errors"]) > 0
        assert body["code"] is None
    finally:
        _stop(tc)


# ---------------------------------------------------------------------------
# 3. Install — writes file, reloads registry, stores provenance
# ---------------------------------------------------------------------------

def test_install_writes_reloads_and_records_provenance():
    db = FakeDB()
    tc = _make_app(db=db)
    try:
        # reg.get: None first call (collision check), then truthy after reload
        reg = Mock()
        reg.get.side_effect = [None, object()]
        reg.reload.return_value = None

        with patch.object(sa, "get_registry", return_value=reg), \
             patch.object(sa, "_write_plugin_file", return_value="/fake/path/test_ema_rsi.py") as mock_write:
            r = tc.post("/strategies/author/install", json={"spec": VALID_SPEC})
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["installed"] is True
            assert body["strategy_id"] == "test_ema_rsi"
            assert "code_sha" in body and len(body["code_sha"]) == 16

            # _write_plugin_file must have been called with (id, <generated code>)
            mock_write.assert_called_once()
            call_id, call_code = mock_write.call_args.args
            assert call_id == "test_ema_rsi"
            assert "class " in call_code

            # reg.reload must have been called
            reg.reload.assert_called_once()

            # Provenance doc must be in generated_strategies
            provenance_docs = db.generated_strategies.docs
            assert len(provenance_docs) == 1
            doc = provenance_docs[0]
            assert doc["strategy_id"] == "test_ema_rsi"
            assert doc["source"] == "spec"
            assert "code_sha" in doc
            assert "created_at" in doc
    finally:
        _stop(tc)


# ---------------------------------------------------------------------------
# 4. Install — 409 when id already exists and overwrite is False
# ---------------------------------------------------------------------------

def test_install_collision_409():
    tc = _make_app()
    try:
        reg = Mock()
        reg.get.return_value = object()  # strategy already registered
        with patch.object(sa, "get_registry", return_value=reg):
            r = tc.post("/strategies/author/install", json={"spec": VALID_SPEC, "overwrite": False})
            assert r.status_code == 409, r.text
            assert "already exists" in r.json()["detail"].lower()
    finally:
        _stop(tc)


# ---------------------------------------------------------------------------
# 5. Install — 400 for invalid spec (unknown column)
# ---------------------------------------------------------------------------

def test_install_invalid_spec_400():
    tc = _make_app()
    try:
        r = tc.post("/strategies/author/install", json={"spec": INVALID_SPEC_UNKNOWN_COL})
        assert r.status_code == 400, r.text
        assert r.json()["detail"]  # non-empty error message
    finally:
        _stop(tc)
