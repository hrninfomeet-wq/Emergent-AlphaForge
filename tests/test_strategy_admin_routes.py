import sys
from pathlib import Path
from unittest.mock import patch, AsyncMock, Mock
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

import app.routers.strategies_admin as sa


def _matches(doc, query):
    return all(doc.get(k) == v for k, v in query.items())

class _Cursor:
    def __init__(self, docs): self._docs = docs
    async def to_list(self, length=None): return list(self._docs)

class FakeColl:
    def __init__(self): self.docs = []
    async def find_one(self, q, projection=None):
        return next((dict(d) for d in self.docs if _matches(d, q)), None)
    def find(self, q, projection=None):
        return _Cursor([dict(d) for d in self.docs if _matches(d, q)])
    async def update_one(self, q, update, upsert=False):
        for d in self.docs:
            if _matches(d, q):
                d.update(update.get("$set", {})); return Mock(matched_count=1)
        if upsert:
            nd = {k: v for k, v in q.items() if not k.startswith("$")}
            nd.update(update.get("$set", {})); self.docs.append(nd)
        return Mock(matched_count=0)
    async def delete_one(self, q):
        before = len(self.docs)
        self.docs = [d for d in self.docs if not _matches(d, q)]
        return Mock(deleted_count=before - len(self.docs))

class FakeDB:
    def __init__(self): self._c = {}
    def __getattr__(self, name):
        c = self.__dict__.setdefault("_c", {})
        return c.setdefault(name, FakeColl())

def _make_app(db=None, registry_items=None, origin_map=None):
    app = FastAPI()
    app.include_router(sa.api)
    db = db if db is not None else FakeDB()
    patches = [patch.object(sa, "_db", lambda: db)]
    if registry_items is not None or origin_map is not None:
        reg = Mock()
        reg.list_all.return_value = registry_items or []
        reg.origin_of.side_effect = lambda sid: (origin_map or {}).get(sid)
        reg.unregister.return_value = True
        patches.append(patch.object(sa, "get_registry", lambda: reg))
    for p in patches: p.start()
    tc = TestClient(app, raise_server_exceptions=True)
    tc._patches = patches; tc._db = db
    return tc

def _stop(tc):
    for p in tc._patches: p.stop()


def test_list_merges_retired_flag():
    db = FakeDB()
    db.strategy_lifecycle.docs.append({"strategy_id": "foo", "retired": True})
    tc = _make_app(db=db, registry_items=[
        {"id": "foo", "name": "Foo", "origin": "custom"},
        {"id": "bar", "name": "Bar", "origin": "builtin"},
    ])
    try:
        r = tc.get("/strategies")
        assert r.status_code == 200
        items = {s["id"]: s for s in r.json()["items"]}
        assert items["foo"]["is_retired"] is True
        assert items["bar"]["is_retired"] is False
    finally:
        _stop(tc)


def test_get_single_404():
    tc = _make_app(registry_items=[], origin_map={})
    try:
        with patch.object(sa, "get_registry") as gr:
            reg = gr.return_value
            reg.get.return_value = None
            r = tc.get("/strategies/missing")
            assert r.status_code == 404
    finally:
        _stop(tc)


def test_retire_sets_flag_and_squares_off():
    db = FakeDB()
    tc = _make_app(db=db, registry_items=[{"id": "foo", "origin": "custom"}], origin_map={"foo": "custom"})
    try:
        with patch.object(sa, "_square_off_strategy_deployments",
                          AsyncMock(return_value=[{"id": "t1"}, {"id": "t2"}])):
            r = tc.post("/strategies/foo/retire")
            assert r.status_code == 200
            body = r.json()
            assert body["retired"] is True and body["squared_off_count"] == 2
            life = db.strategy_lifecycle.docs[0]
            assert life["strategy_id"] == "foo" and life["retired"] is True
    finally:
        _stop(tc)


def test_retire_unknown_404():
    tc = _make_app(registry_items=[], origin_map={})
    try:
        with patch.object(sa, "get_registry") as gr:
            gr.return_value.get.return_value = None
            gr.return_value.origin_of.return_value = None
            r = tc.post("/strategies/nope/retire")
            assert r.status_code == 404
            assert "not found" in r.json()["detail"].lower()
    finally:
        _stop(tc)


def test_unretire_clears_flag():
    db = FakeDB()
    db.strategy_lifecycle.docs.append({"strategy_id": "foo", "retired": True})
    tc = _make_app(db=db, registry_items=[{"id": "foo", "origin": "custom"}], origin_map={"foo": "custom"})
    try:
        r = tc.post("/strategies/foo/un-retire")
        assert r.status_code == 200 and r.json()["retired"] is False
        assert db.strategy_lifecycle.docs[0]["retired"] is False
    finally:
        _stop(tc)


def test_retire_uses_origin_of_when_get_is_none():
    """A failed/origin-only plugin (reg.get is None but origin_of is set) is still retirable."""
    db = FakeDB()
    tc = _make_app(db=db)  # real registry; we patch it below
    try:
        with patch.object(sa, "get_registry") as gr, \
             patch.object(sa, "_square_off_strategy_deployments", AsyncMock(return_value=[])):
            gr.return_value.get.return_value = None
            gr.return_value.origin_of.return_value = "custom"
            r = tc.post("/strategies/variant/retire")
            assert r.status_code == 200 and r.json()["retired"] is True
            assert db.strategy_lifecycle.docs[0]["strategy_id"] == "variant"
    finally:
        _stop(tc)
