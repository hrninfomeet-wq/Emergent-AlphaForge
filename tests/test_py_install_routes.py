import sys
from pathlib import Path
from unittest.mock import patch
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
import app.routers.strategies_admin as sa

VALID = (
    "from __future__ import annotations\n"
    "from app.strategies.base import StrategyBase, Signal\n"
    "class Demo(StrategyBase):\n"
    "    id = \"py_demo\"\n"
    "    is_builtin = False\n"
    "    def evaluate(self, row, prev, params, ctx):\n"
    "        return Signal(direction=\"NONE\")\n"
)


def _app():
    a = FastAPI(); a.include_router(sa.api); return TestClient(a, raise_server_exceptions=True)


def test_python_from_source_forwards_provider():
    canned = {"code": VALID, "fidelity": {"captured": []}, "notes": "", "suggested_id": "py_demo"}
    with patch("app.ai.llm_client.any_configured", return_value=True), \
         patch("app.ai.llm_client.resolve_provider", return_value="gemini"), \
         patch("app.ai.py_author.author_python", return_value=canned) as m:
        r = _app().post("/strategies/author/python-from-source", json={"source": "x", "provider": "gemini"})
    assert r.status_code == 200, r.text
    assert r.json()["suggested_id"] == "py_demo"
    kwargs = m.call_args.kwargs
    args = m.call_args.args
    assert kwargs.get("provider") == "gemini" or (len(args) > 1 and args[1] == "gemini")


def test_validate_clean_runs_smoke():
    with patch("app.ai.py_sandbox.smoke_test", return_value={"ok": True, "error": None, "signal_repr": "S"}):
        r = _app().post("/strategies/author/python/validate", json={"code": VALID})
    assert r.status_code == 200 and r.json()["ok"] is True
    assert r.json()["violations"] == []


def test_validate_bad_code_skips_smoke():
    bad = VALID.replace("from app.strategies.base import StrategyBase, Signal",
                        "import os\nfrom app.strategies.base import StrategyBase, Signal")
    r = _app().post("/strategies/author/python/validate", json={"code": bad})
    assert r.status_code == 200 and r.json()["ok"] is False
    assert any("os" in v for v in r.json()["violations"])


def test_install_rejects_bad_static():
    bad = "import os\n" + VALID
    r = _app().post("/strategies/author/python/install", json={"code": bad, "strategy_id": "py_demo"})
    assert r.status_code == 400


def test_install_rejects_id_mismatch():
    r = _app().post("/strategies/author/python/install", json={"code": VALID, "strategy_id": "WRONG"})
    assert r.status_code == 400


class _FailReg:
    """Registry stand-in whose freshly-written plugin never loads."""
    def reload(self):
        pass

    def get(self, sid):
        return None


def test_rollback_removes_orphan_on_new_install_load_failure():
    """A generated plugin that writes but fails to load must be REMOVED, or the
    orphaned .py breaks every future reg.reload() and the next app boot."""
    import app.strategies.plugins as _pp
    from fastapi import HTTPException
    plugins_dir = Path(_pp.__file__).parent
    tid = "rollback_orphan_test"
    path = plugins_dir / f"{tid}.py"
    path.unlink(missing_ok=True)
    try:
        raised = False
        try:
            sa._write_plugin_with_rollback(tid, VALID, _FailReg())
        except HTTPException as exc:
            raised = True
            assert exc.status_code == 500
        assert raised, "expected HTTPException(500) on load failure"
        assert not path.exists(), "orphaned plugin file must be removed"
    finally:
        path.unlink(missing_ok=True)


def test_rollback_restores_previous_on_failed_overwrite():
    """A failed overwrite=True re-install must RESTORE the previously-working
    file, not clobber it — the user must never lose a working strategy."""
    import app.strategies.plugins as _pp
    from fastapi import HTTPException
    plugins_dir = Path(_pp.__file__).parent
    tid = "rollback_restore_test"
    path = plugins_dir / f"{tid}.py"
    prev_code = "# previous working version\n" + VALID
    try:
        path.write_text(prev_code, encoding="utf-8")
        try:
            sa._write_plugin_with_rollback(tid, "def broken(:\n", _FailReg())
        except HTTPException:
            pass
        assert path.exists(), "previous file must be restored"
        assert path.read_text(encoding="utf-8") == prev_code
    finally:
        path.unlink(missing_ok=True)


def test_install_happy_path():
    import app.strategies.plugins as _plugins_pkg
    plugins_dir = Path(_plugins_pkg.__file__).parent
    from app.strategies.base import get_registry
    async def _noop(*a, **k): return None
    try:
        with patch("app.ai.py_sandbox.smoke_test", return_value={"ok": True, "error": None, "signal_repr": "S"}), \
             patch.object(sa, "_db") as db:
            db.return_value.generated_strategies.update_one = _noop
            r = _app().post("/strategies/author/python/install", json={"code": VALID, "strategy_id": "py_demo"})
        assert r.status_code == 200, r.text
        assert (plugins_dir / "py_demo.py").exists()
        assert get_registry().get("py_demo") is not None
    finally:
        get_registry().unregister("py_demo")
        (plugins_dir / "py_demo.py").unlink(missing_ok=True)
        get_registry().reload()
