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

def _matches_ref(doc, query):
    """Reference-count query matcher: $or + dotted keys (config.strategy_id)."""
    if "$or" in query:
        return any(_matches_ref(doc, sub) for sub in query["$or"])
    for k, v in query.items():
        cur = doc
        for part in k.split("."):
            cur = cur.get(part) if isinstance(cur, dict) else None
            if cur is None:
                break
        if cur != v:
            return False
    return True

class _Cursor:
    def __init__(self, docs): self._docs = docs
    async def to_list(self, length=None): return list(self._docs)

class FakeColl:
    def __init__(self): self.docs = []
    async def find_one(self, q, projection=None):
        return next((dict(d) for d in self.docs if _matches(d, q)), None)
    def find(self, q, projection=None):
        return _Cursor([dict(d) for d in self.docs if _matches(d, q)])
    async def count_documents(self, q):
        return len([d for d in self.docs if _matches_ref(d, q)])
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


def _delete_app(origin, *, retired=False, deployments=None):
    db = FakeDB()
    if retired:
        db.strategy_lifecycle.docs.append({"strategy_id": "foo", "retired": True})
    for dep in (deployments or []):
        db.strategy_deployments.docs.append(dep)
    tc = _make_app(db=db, registry_items=[{"id": "foo", "origin": origin}], origin_map={"foo": origin})
    return tc, db


def test_delete_unknown_404():
    tc = _make_app(registry_items=[], origin_map={})
    try:
        r = tc.delete("/strategies/foo")
        assert r.status_code == 404
    finally:
        _stop(tc)


def test_delete_builtin_403():
    tc, _ = _delete_app("builtin", retired=True)
    try:
        r = tc.delete("/strategies/foo")
        assert r.status_code == 403
    finally:
        _stop(tc)


def test_delete_not_retired_409():
    tc, _ = _delete_app("custom", retired=False)
    try:
        r = tc.delete("/strategies/foo")
        assert r.status_code == 409
        assert "retire" in r.json()["detail"].lower()
    finally:
        _stop(tc)


def test_delete_with_live_deployment_409():
    tc, _ = _delete_app("custom", retired=True,
                        deployments=[{"id": "d1", "strategy_id": "foo", "status": "ACTIVE"}])
    try:
        r = tc.delete("/strategies/foo")
        assert r.status_code == 409
        assert "deployment" in r.json()["detail"].lower()
    finally:
        _stop(tc)


def _seed_references(db):
    db.presets.docs.append({"name": "p1", "config": {"strategy_id": "foo"}})
    db.backtest_runs.docs.append({"id": "r1", "strategy_id": "foo"})
    db.backtest_runs.docs.append({"id": "r2", "strategy_id": "foo"})
    db.optimization_jobs.docs.append({"id": "o1", "strategy_id": "foo"})


def test_references_endpoint_counts_both_id_shapes():
    tc, db = _delete_app("custom", retired=True,
                         deployments=[{"id": "d1", "strategy_id": "foo", "status": "ARCHIVED"}])
    _seed_references(db)
    try:
        r = tc.get("/strategies/foo/references")
        assert r.status_code == 200
        body = r.json()
        assert body["references"] == {
            "presets": 1, "backtest_runs": 2, "optimization_jobs": 1,
            "deployments_total": 1, "deployments_blocking": 0,
        }
        assert body["orphaned_total"] == 4
    finally:
        _stop(tc)


def test_delete_with_references_requires_explicit_confirm():
    """Item D safety gap: origin flipped to custom must NOT make deletion a
    one-click orphaning of presets/runs/jobs — the counts come back in a 409
    until the caller confirms."""
    tc, db = _delete_app("custom", retired=True)
    _seed_references(db)
    try:
        with patch.object(sa, "_delete_plugin_file", Mock(return_value=True)) as mock_dpf:
            r = tc.delete("/strategies/foo")
            assert r.status_code == 409
            detail = r.json()["detail"]
            assert detail["code"] == "references_exist"
            assert detail["references"]["backtest_runs"] == 2
            assert "orphans 4" in detail["message"]
            mock_dpf.assert_not_called()

            r = tc.delete("/strategies/foo", params={"confirm": "true"})
            assert r.status_code == 200
            body = r.json()
            assert body["deleted"] is True
            assert body["orphaned_references"]["presets"] == 1
            mock_dpf.assert_called_once_with("foo")
    finally:
        _stop(tc)


def test_delete_without_references_needs_no_confirm():
    tc, _db_ = _delete_app("custom", retired=True)
    try:
        with patch.object(sa, "_delete_plugin_file", Mock(return_value=True)):
            r = tc.delete("/strategies/foo")
            assert r.status_code == 200 and r.json()["deleted"] is True
    finally:
        _stop(tc)


def test_delete_success_removes_file_and_lifecycle():
    tc, db = _delete_app("custom", retired=True,
                         deployments=[{"id": "d1", "strategy_id": "foo", "status": "ARCHIVED"}])
    try:
        with patch.object(sa, "_delete_plugin_file", Mock(return_value=True)) as mock_dpf:
            r = tc.delete("/strategies/foo")
            assert r.status_code == 200 and r.json()["deleted"] is True
            assert db.strategy_lifecycle.docs == []
            mock_dpf.assert_called_once_with("foo")
            sa.get_registry().unregister.assert_called_once_with("foo")
    finally:
        _stop(tc)


def test_reload_returns_count():
    tc = _make_app()
    try:
        with patch.object(sa, "get_registry") as gr:
            gr.return_value.reload.return_value = None
            gr.return_value.list_all.return_value = [{"id": "a"}, {"id": "b"}]
            r = tc.post("/strategies/reload")
            assert r.status_code == 200 and r.json()["count"] == 2
            gr.return_value.reload.assert_called_once()
    finally:
        _stop(tc)


def test_is_retired_helper():
    import asyncio
    db = FakeDB()
    db.strategy_lifecycle.docs.append({"strategy_id": "foo", "retired": True})
    with patch.object(sa, "_db", lambda: db):
        assert asyncio.run(sa.is_retired("foo")) is True
        assert asyncio.run(sa.is_retired("bar")) is False


def test_delete_plugin_file_removes_file_under_plugins(tmp_path):
    plug = tmp_path / "strategies" / "plugins"
    plug.mkdir(parents=True)
    f = plug / "my_plugin.py"
    f.write_text("x = 1")
    with patch.object(sa, "get_registry") as gr, \
         patch("app.strategy_source_hash.strategy_file_path", return_value=f):
        gr.return_value.get.return_value = object()
        assert sa._delete_plugin_file("my_plugin") is True
        assert not f.exists()


def test_delete_plugin_file_refuses_outside_plugins(tmp_path):
    f = tmp_path / "elsewhere" / "x.py"
    f.parent.mkdir(parents=True)
    f.write_text("x = 1")
    with patch.object(sa, "get_registry") as gr, \
         patch("app.strategy_source_hash.strategy_file_path", return_value=f):
        gr.return_value.get.return_value = object()
        assert sa._delete_plugin_file("x") is False
        assert f.exists()


def test_delete_plugin_file_none_when_unregistered():
    with patch.object(sa, "get_registry") as gr:
        gr.return_value.get.return_value = None
        assert sa._delete_plugin_file("nope") is False
