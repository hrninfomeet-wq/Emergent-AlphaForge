# Full-Python Escape Hatch Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a "Full Python" authoring mode where the POWERFUL AI tier writes an arbitrary `StrategyBase` module, gated by a structural AST allowlist + a subprocess smoke-test before install.

**Architecture:** A new `py_sandbox.py` does pure-AST `static_check` (structural allowlist so nothing runs at import time) + `extract_strategy_id` + a subprocess `smoke_test` (result via a file channel, not stdout). `py_author.py` calls the POWERFUL tier. Three new router endpoints (generate / validate / install) with the server always re-validating. `base.py`'s `auto_discover` is fixed to fresh-import edited plugins. The wizard gets a Spec/Full-Python toggle + an editable code panel gated on a Validate pass.

**Tech Stack:** Python `ast`/`subprocess`/`resource`, FastAPI, Pydantic, React/CRA. Host tests are SDK-free (provider + subprocess seams mocked); the real subprocess runs only in the Linux backend container.

**Conventions:**
- Worktree root `C:\Users\haroo\af-wt-strategy-library` (branch `feat/strategy-full-python`, off merged main).
- Host tests: `PY="/c/Users/haroo/OneDrive/Documents/New project/Emergent-AlphaForge/.venv/Scripts/python.exe"`; run from the worktree root: `cd "/c/Users/haroo/af-wt-strategy-library" && "$PY" -m pytest tests/<file> -q`.
- The venv has pytest/pydantic/fastapi but NOT motor/anthropic/google-genai — keep SDK imports lazy.
- Spec: `docs/superpowers/specs/2026-06-27-strategy-authoring-full-python-escape-hatch-design.md` (authoritative for the exact rules).

---

### Task 1: `static_check` — the structural AST allowlist (the core safety gate)

**Files:**
- Create: `backend/app/ai/py_sandbox.py`
- Test: `tests/test_py_sandbox.py`

- [ ] **Step 1: Write the failing tests** — `tests/test_py_sandbox.py`:

```python
"""Pure-AST safety checks for AI-authored full-Python strategies (no execution)."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.ai.py_sandbox import static_check

VALID = '''
from __future__ import annotations
import pandas as pd
from app.strategies.base import StrategyBase, Signal


class MyStrat(StrategyBase):
    id = "my_strat"
    name = "My Strat"
    is_builtin = False
    parameter_schema = {"thr": {"type": "float", "default": 1.0}}

    def evaluate(self, row, prev, params, ctx) -> Signal:
        if float(row["close"]) > float(row["ema9"]):
            return Signal(direction="CE", spot_target_pts=30, spot_stop_pts=15)
        return Signal(direction="NONE")
'''


def test_valid_module_passes():
    assert static_check(VALID) == []


def test_syntax_error():
    assert static_check("def (:") and "syntax error" in static_check("def (:")[0]


def _has(errs, frag):
    return any(frag in e for e in errs)


def test_forbidden_import_os():
    code = VALID.replace("import pandas as pd", "import pandas as pd\nimport os")
    assert _has(static_check(code), "os")


def test_relative_import_rejected():
    code = VALID.replace("from app.strategies.base", "from .base")
    assert _has(static_check(code), "relative") or _has(static_check(code), "not allowed")


def test_app_import_must_be_base():
    code = VALID.replace("from app.strategies.base import StrategyBase, Signal",
                         "from app.db import get_db\nfrom app.strategies.base import StrategyBase, Signal")
    assert _has(static_check(code), "app.strategies.base")


def test_module_level_statement_rejected():
    code = VALID + "\nprint('hi')\n"
    assert _has(static_check(code), "top-level")


def test_module_level_assign_rejected():
    code = VALID.replace("import pandas as pd", "import pandas as pd\nLOADED = True")
    assert _has(static_check(code), "top-level")


def test_class_decorator_rejected():
    code = VALID.replace("class MyStrat(StrategyBase):", "@staticmethod\nclass MyStrat(StrategyBase):")
    # (syntactically a decorator on a class)
    assert static_check(code) != []


def test_metaclass_kw_rejected():
    code = VALID.replace("class MyStrat(StrategyBase):", "class MyStrat(StrategyBase, metaclass=type):")
    assert _has(static_check(code), "keyword") or _has(static_check(code), "metaclass")


def test_multiple_bases_rejected():
    code = VALID.replace("class MyStrat(StrategyBase):", "class MyStrat(StrategyBase, dict):")
    assert _has(static_check(code), "ONLY") or _has(static_check(code), "StrategyBase")


def test_dunder_attr_rejected():
    code = VALID.replace("return Signal(direction=\"NONE\")",
                         "return Signal(direction=type(self).__mro__[1].__name__)")
    assert _has(static_check(code), "dunder") or _has(static_check(code), "__mro__")


def test_reexport_os_walk_rejected():
    # the audit's named evasion: pandas re-exports a live os module
    code = VALID.replace("return Signal(direction=\"NONE\")",
                         "pd.io.common.os.system('echo x'); return Signal(direction=\"NONE\")")
    assert _has(static_check(code), "io") or _has(static_check(code), "os")


def test_numpy_f2py_walk_rejected():
    code = (VALID.replace("import pandas as pd", "import pandas as pd\nimport numpy as np")
            .replace("return Signal(direction=\"NONE\")",
                     "np.f2py.os.getpid(); return Signal(direction=\"NONE\")"))
    assert _has(static_check(code), "f2py") or _has(static_check(code), "os")


def test_forbidden_calls():
    for name in ("eval", "exec", "open", "getattr", "type", "__import__"):
        code = VALID.replace("return Signal(direction=\"NONE\")",
                             f"x = {name}; return Signal(direction=\"NONE\")")
        # bare reference is fine; a call is not
        codecall = VALID.replace("return Signal(direction=\"NONE\")",
                                 f"{name}('x'); return Signal(direction=\"NONE\")")
        assert _has(static_check(codecall), name), name


def test_method_level_import_rejected():
    code = VALID.replace("        if float(row",
                         "        import os\n        if float(row")
    assert _has(static_check(code), "import")


def test_zero_strategy_classes_rejected():
    code = VALID.replace("(StrategyBase)", "(object)").replace("class MyStrat(object)", "class MyStrat(object)")
    assert _has(static_check(code), "StrategyBase")


def test_two_strategy_classes_rejected():
    code = VALID + "\n\nclass Other(StrategyBase):\n    id = \"other\"\n    def evaluate(self, row, prev, params, ctx):\n        return Signal(direction=\"NONE\")\n"
    assert _has(static_check(code), "exactly one")


def test_missing_evaluate_rejected():
    code = VALID.replace("    def evaluate(self, row, prev, params, ctx) -> Signal:\n        if float(row[\"close\"]) > float(row[\"ema9\"]):\n            return Signal(direction=\"CE\", spot_target_pts=30, spot_stop_pts=15)\n        return Signal(direction=\"NONE\")\n", "    pass\n")
    assert _has(static_check(code), "evaluate")
```

- [ ] **Step 2: Run to verify it FAILS** — `cd "/c/Users/haroo/af-wt-strategy-library" && "$PY" -m pytest tests/test_py_sandbox.py -q` (ModuleNotFoundError).

- [ ] **Step 3: Implement `backend/app/ai/py_sandbox.py`** (static_check + helpers; the rest of the module is added in later tasks):

```python
"""AST allowlist + subprocess smoke-test for AI-authored full-Python strategies.

static_check is a STRUCTURAL ALLOWLIST: module top level may contain only imports
+ exactly one StrategyBase subclass (so nothing executes at import time); the class
takes no decorators/metaclass; a whole-tree denylist closes the known method-body
escapes. Pure ast — no execution, host-safe. See the design spec for the rules.
"""
from __future__ import annotations

import ast
import re
from typing import List, Optional

ALLOWED_IMPORT_ROOTS = {"pandas", "numpy", "math", "typing", "dataclasses", "app", "__future__"}
FORBIDDEN_MODULE_ATTRS = {
    "os", "sys", "subprocess", "socket", "shutil", "importlib", "ctypes", "builtins",
    "pathlib", "pickle", "marshal", "runpy", "pty", "signal", "io", "f2py",
    "ctypeslib", "testing", "compat",
}
FORBIDDEN_CALL_NAMES = {
    "eval", "exec", "compile", "__import__", "open", "input", "globals", "locals",
    "vars", "getattr", "setattr", "delattr", "type", "breakpoint", "memoryview",
}
_DUNDER_RE = re.compile(r"^__.*__$")
_SLUG_RE = re.compile(r"^[a-z][a-z0-9_]*$")


def _is_strategybase_class(node: ast.ClassDef) -> bool:
    return any(isinstance(b, ast.Name) and b.id == "StrategyBase" for b in node.bases)


def _check_imports(node, errors: List[str]) -> None:
    if isinstance(node, ast.Import):
        for alias in node.names:
            if alias.name.split(".")[0] not in ALLOWED_IMPORT_ROOTS:
                errors.append(f"import of '{alias.name}' is not allowed")
        return
    # ImportFrom
    if node.level:
        errors.append("relative imports are not allowed")
        return
    mod = node.module or ""
    root = mod.split(".")[0]
    if root not in ALLOWED_IMPORT_ROOTS:
        errors.append(f"import from '{mod}' is not allowed")
    elif root == "app" and mod != "app.strategies.base":
        errors.append(f"the only allowed app import is app.strategies.base (got '{mod}')")


def _check_class(cls: ast.ClassDef, errors: List[str]) -> None:
    if len(cls.bases) != 1 or not _is_strategybase_class(cls):
        errors.append("the strategy class must inherit ONLY from StrategyBase")
    if cls.decorator_list:
        errors.append("class decorators are not allowed")
    if cls.keywords:
        errors.append("class keywords (e.g. metaclass=) are not allowed")
    has_evaluate = has_id = False
    for i, stmt in enumerate(cls.body):
        if i == 0 and isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant) \
                and isinstance(stmt.value.value, str):
            continue  # docstring
        if isinstance(stmt, ast.FunctionDef):
            if stmt.name in ("__init_subclass__", "__class_getitem__"):
                errors.append(f"defining {stmt.name} is not allowed")
            if stmt.name == "evaluate":
                has_evaluate = True
            continue
        if isinstance(stmt, (ast.Assign, ast.AnnAssign)):
            targets = stmt.targets if isinstance(stmt, ast.Assign) else [stmt.target]
            if any(isinstance(t, ast.Name) and t.id == "id" for t in targets):
                has_id = True
            continue
        errors.append(f"class-level {type(stmt).__name__} is not allowed (only class vars + methods)")
    if not has_evaluate:
        errors.append("the strategy class must define an evaluate method")
    if not has_id:
        errors.append("the strategy class must assign an id")


def static_check(code: str) -> List[str]:
    errors: List[str] = []
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        return [f"syntax error: {e}"]

    top_imports = set()
    class_defs: List[ast.ClassDef] = []
    for i, node in enumerate(tree.body):
        if i == 0 and isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) \
                and isinstance(node.value.value, str):
            continue  # leading docstring
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            top_imports.add(id(node))
            _check_imports(node, errors)
            continue
        if isinstance(node, ast.ClassDef):
            class_defs.append(node)
            continue
        errors.append(f"top-level {type(node).__name__} is not allowed (only imports + one StrategyBase class)")

    strat_classes = [c for c in class_defs if _is_strategybase_class(c)]
    if len(strat_classes) != 1:
        errors.append(f"exactly one StrategyBase subclass is required (found {len(strat_classes)})")
    for c in class_defs:
        if not _is_strategybase_class(c):
            errors.append(f"top-level class {c.name!r} does not subclass StrategyBase")
    if strat_classes:
        _check_class(strat_classes[0], errors)

    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            if _DUNDER_RE.match(node.attr):
                errors.append(f"dunder attribute access '{node.attr}' is not allowed")
            elif node.attr in FORBIDDEN_MODULE_ATTRS:
                errors.append(f"access to a forbidden module attribute '{node.attr}' is not allowed")
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
                and node.func.id in FORBIDDEN_CALL_NAMES:
            errors.append(f"call to '{node.func.id}' is not allowed")
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            errors.append("global/nonlocal is not allowed")
        elif isinstance(node, (ast.Import, ast.ImportFrom)) and id(node) not in top_imports:
            errors.append("imports are only allowed at module top level")

    # de-dup preserving order
    seen, out = set(), []
    for e in errors:
        if e not in seen:
            seen.add(e); out.append(e)
    return out
```

- [ ] **Step 4: Run to verify PASS** — `cd "/c/Users/haroo/af-wt-strategy-library" && "$PY" -m pytest tests/test_py_sandbox.py -q` (all pass).

- [ ] **Step 5: Commit**

```bash
cd "/c/Users/haroo/af-wt-strategy-library"
git add backend/app/ai/py_sandbox.py tests/test_py_sandbox.py
git commit -m "feat(ai): py_sandbox.static_check — structural AST allowlist for full-Python strategies"
```

---

### Task 2: `extract_strategy_id` (pure AST, never executes)

**Files:** Modify `backend/app/ai/py_sandbox.py`; Test `tests/test_py_sandbox.py`.

- [ ] **Step 1: Append failing tests** to `tests/test_py_sandbox.py`:

```python
from app.ai.py_sandbox import extract_strategy_id


def test_extract_id_literal():
    assert extract_strategy_id(VALID) == "my_strat"


def test_extract_id_nonliteral_is_none():
    code = VALID.replace('id = "my_strat"', "id = SLUG")
    assert extract_strategy_id(code) is None


def test_extract_id_missing_is_none():
    code = VALID.replace('    id = "my_strat"\n', "")
    assert extract_strategy_id(code) is None


def test_extract_id_bad_slug_is_none():
    code = VALID.replace('id = "my_strat"', 'id = "Bad-ID"')
    assert extract_strategy_id(code) is None
```

- [ ] **Step 2: Run to verify FAIL** — `"$PY" -m pytest tests/test_py_sandbox.py -k extract -q`.

- [ ] **Step 3: Add to `py_sandbox.py`:**

```python
def extract_strategy_id(code: str) -> Optional[str]:
    """Return the strategy class's literal `id` slug via pure AST (NEVER imports/execs
    the module — importing unverified AI code would defeat subprocess containment).
    Returns None if absent or non-literal or not a valid slug."""
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return None
    classes = [n for n in tree.body if isinstance(n, ast.ClassDef) and _is_strategybase_class(n)]
    if len(classes) != 1:
        return None
    for stmt in classes[0].body:
        targets = (stmt.targets if isinstance(stmt, ast.Assign)
                   else [stmt.target] if isinstance(stmt, ast.AnnAssign) else [])
        if any(isinstance(t, ast.Name) and t.id == "id" for t in targets):
            val = stmt.value
            if isinstance(val, ast.Constant) and isinstance(val.value, str) and _SLUG_RE.match(val.value):
                return val.value
            return None
    return None
```

- [ ] **Step 4: Run to verify PASS.** **Step 5: Commit**

```bash
git add backend/app/ai/py_sandbox.py tests/test_py_sandbox.py
git commit -m "feat(ai): py_sandbox.extract_strategy_id (pure-AST, literal-only)"
```

---

### Task 3: `_interpret_smoke_result` (pure outcome→dict helper) + matrix tests

**Files:** Modify `backend/app/ai/py_sandbox.py`; Test `tests/test_py_sandbox.py`.

- [ ] **Step 1: Append failing tests:**

```python
from app.ai.py_sandbox import _interpret_smoke_result


def test_smoke_result_timeout():
    r = _interpret_smoke_result(returncode=None, stdout="", stderr="", timed_out=True, result=None)
    assert r["ok"] is False and "timeout" in r["error"].lower()


def test_smoke_result_nonzero_exit():
    r = _interpret_smoke_result(returncode=1, stdout="", stderr="boom traceback", timed_out=False, result=None)
    assert r["ok"] is False and "boom" in r["error"]


def test_smoke_result_missing_result_file():
    r = _interpret_smoke_result(returncode=0, stdout="noise", stderr="", timed_out=False, result=None)
    assert r["ok"] is False and "no result" in r["error"].lower()


def test_smoke_result_driver_failed():
    r = _interpret_smoke_result(returncode=0, stdout="", stderr="", timed_out=False,
                                result={"ok": False, "error": "evaluate raised: KeyError"})
    assert r["ok"] is False and "evaluate raised" in r["error"]


def test_smoke_result_ok():
    r = _interpret_smoke_result(returncode=0, stdout="", stderr="", timed_out=False,
                                result={"ok": True, "signal_repr": "Signal(direction='NONE')"})
    assert r["ok"] is True and "NONE" in r["signal_repr"]
```

- [ ] **Step 2: Run to verify FAIL.**

- [ ] **Step 3: Add to `py_sandbox.py`:**

```python
def _interpret_smoke_result(*, returncode, stdout, stderr, timed_out, result) -> dict:
    """Pure parent-side mapping of a subprocess outcome to {ok, error, signal_repr}.
    `result` is the parsed result-file dict (or None if missing/unparseable)."""
    tail = (stderr or stdout or "").strip()[-1500:]
    if timed_out:
        return {"ok": False, "error": f"smoke-test timeout; {tail}", "signal_repr": None}
    if returncode not in (0, None):
        return {"ok": False, "error": f"smoke-test exited {returncode}: {tail}", "signal_repr": None}
    if result is None:
        return {"ok": False, "error": f"smoke-test produced no result: {tail}", "signal_repr": None}
    if not result.get("ok"):
        return {"ok": False, "error": result.get("error") or "smoke-test failed", "signal_repr": None}
    return {"ok": True, "error": None, "signal_repr": result.get("signal_repr")}
```

- [ ] **Step 4: Run to verify PASS. Step 5: Commit**

```bash
git add backend/app/ai/py_sandbox.py tests/test_py_sandbox.py
git commit -m "feat(ai): py_sandbox._interpret_smoke_result (pure, table-tested)"
```

---

### Task 4: `smoke_test` + the subprocess driver

**Files:**
- Modify: `backend/app/ai/py_sandbox.py` (add `smoke_test`)
- Create: `backend/app/ai/_py_smoke_driver.py`
- Test: `tests/test_py_sandbox.py` (a fake-subprocess test for `smoke_test` plumbing)

- [ ] **Step 1: Append a failing test** that drives `smoke_test` against a monkeypatched `subprocess` so it never spawns a real process:

```python
import types as _types


def test_smoke_test_reads_result_file(monkeypatch, tmp_path):
    from app.ai import py_sandbox

    captured = {}

    class _FakeProc:
        returncode = 0
        def communicate(self, timeout=None):
            # simulate the driver writing the result file
            import json
            (tmp_path / "result.json").write_text(json.dumps({"ok": True, "signal_repr": "Signal(NONE)"}))
            return ("", "")
        def kill(self): pass

    def fake_popen(cmd, **kw):
        captured["cmd"] = cmd
        # the driver's result path is argv[2]
        rp = Path(cmd[3]) if len(cmd) > 3 else (tmp_path / "result.json")
        import json
        rp.write_text(json.dumps({"ok": True, "signal_repr": "Signal(NONE)"}))
        return _FakeProc()

    monkeypatch.setattr(py_sandbox.subprocess, "Popen", fake_popen)
    # force the temp dir so we can predict paths
    monkeypatch.setattr(py_sandbox.tempfile, "mkdtemp", lambda prefix="": str(tmp_path))
    out = py_sandbox.smoke_test("ignored code", timeout=5)
    assert out["ok"] is True and "NONE" in (out["signal_repr"] or "")
```

(The exact fake shape may be adjusted by the implementer to match the final `smoke_test` plumbing — the assertion that matters: a result-file `ok:true` yields `ok:true`, and the code path never inspects stdout for the result.)

- [ ] **Step 2: Run to verify FAIL.**

- [ ] **Step 3: Add `smoke_test` to `py_sandbox.py`** (imports `subprocess`, `tempfile`, `os`, `sys`, `json`, `shutil` at module top — these are the SANDBOX's own tools, host-safe stdlib):

```python
import json
import os
import shutil
import subprocess
import sys
import tempfile

_DRIVER = os.path.join(os.path.dirname(__file__), "_py_smoke_driver.py")
_RLIMIT_AS_BYTES = 1024 * 1024 * 1024  # 1 GiB


def _preexec():  # pragma: no cover - POSIX only, exercised live in the container
    try:
        import resource
        resource.setrlimit(resource.RLIMIT_AS, (_RLIMIT_AS_BYTES, _RLIMIT_AS_BYTES))
    except Exception:
        pass


def smoke_test(code: str, *, timeout: int = 10) -> dict:
    """Run the candidate in a fresh subprocess; return {ok, error, signal_repr}.
    Patchable seam — host tests mock it; the real run happens in the Linux container."""
    workdir = tempfile.mkdtemp(prefix="smoke_")
    code_path = os.path.join(workdir, "candidate.py")
    result_path = os.path.join(workdir, "result.json")
    with open(code_path, "w", encoding="utf-8") as f:
        f.write(code)
    env = {"PATH": os.environ.get("PATH", ""), "PYTHONPATH": "/app",
           "LANG": os.environ.get("LANG", "C.UTF-8")}
    posix = os.name == "posix"
    kwargs = dict(cwd="/app", env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if posix:
        kwargs["start_new_session"] = True
        kwargs["preexec_fn"] = _preexec
    timed_out = False
    out = err = ""
    try:
        proc = subprocess.Popen([sys.executable, _DRIVER, code_path, result_path, str(timeout)], **kwargs)
        try:
            out, err = proc.communicate(timeout=timeout + 2)
        except subprocess.TimeoutExpired:
            timed_out = True
            if posix:
                try:
                    os.killpg(os.getpgid(proc.pid), 9)
                except Exception:
                    proc.kill()
            else:
                proc.kill()
            out, err = proc.communicate()
        result = None
        try:
            with open(result_path, encoding="utf-8") as f:
                result = json.load(f)
        except Exception:
            result = None
        return _interpret_smoke_result(returncode=proc.returncode, stdout=out, stderr=err,
                                       timed_out=timed_out, result=result)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
```

- [ ] **Step 4: Create `backend/app/ai/_py_smoke_driver.py`** (runs in the subprocess; imports the candidate under a unique name; builds a realistic frame; result → file, NEVER stdout):

```python
"""Subprocess driver: load an AI-authored strategy module, run evaluate() on a
synthetic ~2-session frame, write {ok, error, signal_repr} to argv[2]. Run via
py_sandbox.smoke_test with cwd=/app so `from app.strategies.base import ...` resolves."""
import json
import sys
import traceback
import uuid


def _result(path, payload):
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f)
    except Exception:
        pass


def main():
    code_path, result_path = sys.argv[1], sys.argv[2]
    try:
        import importlib.util
        from app.strategies.base import StrategyBase, Signal
        from app.ai.compiler import allowed_columns

        modname = f"_smoke_{uuid.uuid4().hex}"
        spec = importlib.util.spec_from_file_location(modname, code_path)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[modname] = mod
        spec.loader.exec_module(mod)  # module top-level (allowlist guarantees no side effects)

        strat_classes = [
            c for c in vars(mod).values()
            if isinstance(c, type) and issubclass(c, StrategyBase) and c is not StrategyBase
            and getattr(c, "__module__", None) == modname and getattr(c, "id", "")
        ]
        if len(strat_classes) != 1:
            return _result(result_path, {"ok": False, "error": f"expected exactly one strategy class, found {len(strat_classes)}"})
        inst = strat_classes[0]()

        import pandas as pd
        import numpy as np
        cols = sorted(allowed_columns())
        n = 120  # ~2 sessions of a few bars
        frame = {c: np.linspace(100, 110, n) for c in cols}
        frame["regime"] = ["TREND"] * n
        if "day_type" in cols:
            frame["day_type"] = ["TREND_DAY"] * n
        df = pd.DataFrame(frame)
        # time/session keys the engine feeds evaluate()
        base = pd.Timestamp("2026-06-01 09:15:00")
        df["ts"] = [(base + pd.Timedelta(minutes=i)).value // 10**6 for i in range(n)]
        df["datetime"] = [(base + pd.Timedelta(minutes=i)).isoformat() for i in range(n)]
        df["ist_time"] = [(base + pd.Timedelta(minutes=i)).strftime("%H:%M") for i in range(n)]
        df["session_date"] = ["2026-06-01" if i < n // 2 else "2026-06-02" for i in range(n)]

        params = inst.merged_params(None)
        ctx = {"instrument": "NIFTY", "mode": "INTRADAY", "session_date": "2026-06-01"}
        last_repr = None
        for i in range(2, min(n, 20)):
            row, prev = df.iloc[i], df.iloc[i - 1]
            sig = inst.evaluate(row, prev, params, ctx)
            if not isinstance(sig, Signal):
                return _result(result_path, {"ok": False, "error": f"evaluate returned {type(sig).__name__}, not Signal"})
            if sig.direction not in ("CE", "PE", "NONE"):
                return _result(result_path, {"ok": False, "error": f"invalid direction {sig.direction!r}"})
            last_repr = repr(sig)
        return _result(result_path, {"ok": True, "signal_repr": last_repr})
    except Exception:
        return _result(result_path, {"ok": False, "error": "evaluate/import raised:\n" + traceback.format_exc()[-1500:]})


if __name__ == "__main__":
    main()
```

- [ ] **Step 5: Run to verify PASS** — `"$PY" -m pytest tests/test_py_sandbox.py -q`. Host import: `"$PY" -c "import sys; sys.path.insert(0,'backend'); import app.ai.py_sandbox; print('import ok')"`.

- [ ] **Step 6: Commit**

```bash
git add backend/app/ai/py_sandbox.py backend/app/ai/_py_smoke_driver.py tests/test_py_sandbox.py
git commit -m "feat(ai): smoke_test subprocess + driver (result-file channel, synthetic 2-session frame)"
```

---

### Task 5: Registry reload fresh-import fix + hermetic install-loop test

**Files:**
- Modify: `backend/app/strategies/base.py` (`auto_discover`)
- Test: `tests/test_registry_reload.py` (new, real registry, no mocks)

- [ ] **Step 1: Write the failing test** — `tests/test_registry_reload.py`:

```python
"""Real registry reload: an EDITED plugin file must take effect after reload()."""
import sys, textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.strategies.base import get_registry
import app.strategies.plugins as _plugins_pkg

PLUGINS_DIR = Path(_plugins_pkg.__file__).parent


def _write(name, direction):
    (PLUGINS_DIR / f"{name}.py").write_text(textwrap.dedent(f'''
        from app.strategies.base import StrategyBase, Signal
        class ReloadProbe(StrategyBase):
            id = "{name}"
            is_builtin = False
            def evaluate(self, row, prev, params, ctx):
                return Signal(direction="{direction}")
    '''))


def test_edited_plugin_takes_effect_after_reload(tmp_path):
    name = "reload_probe_tmp"
    reg = get_registry()
    try:
        _write(name, "CE")
        reg.reload()
        s = reg.get(name)
        assert s is not None
        assert s.evaluate(None, None, {}, {}).direction == "CE"
        # EDIT the file -> reload -> must pick up new behavior (the bug being fixed)
        _write(name, "PE")
        reg.reload()
        assert reg.get(name).evaluate(None, None, {}, {}).direction == "PE"
        assert reg.get(name).meta()["origin"] == "custom"
    finally:
        (PLUGINS_DIR / f"{name}.py").unlink(missing_ok=True)
        reg.reload()
```

- [ ] **Step 2: Run to verify it FAILS** (the edited "PE" assertion fails because `import_module` no-ops the already-imported module): `"$PY" -m pytest tests/test_registry_reload.py -q`.

- [ ] **Step 3: Fix `auto_discover` in `backend/app/strategies/base.py`** — replace the import line so plugin modules are fresh-imported. Change:

```python
                full = f"{pkg_name}.{modname}"
                try:
                    mod = importlib.import_module(full)
```
to:
```python
                full = f"{pkg_name}.{modname}"
                try:
                    # Plugins can be edited/overwritten at runtime (authoring). A bare
                    # import_module is a no-op for an already-imported module, so drop it
                    # from sys.modules first to force a clean fresh import. NEVER do this
                    # for builtins. A failed fresh import is auto-removed by CPython.
                    if pkg_name == "app.strategies.plugins":
                        import sys as _sys
                        _sys.modules.pop(full, None)
                    mod = importlib.import_module(full)
```

- [ ] **Step 4: Run to verify PASS** — `"$PY" -m pytest tests/test_registry_reload.py -q`. Re-run the full builtin discovery is unaffected: `"$PY" -m pytest tests/test_strategy_admin_routes.py -q`.

- [ ] **Step 5: Commit**

```bash
git add backend/app/strategies/base.py tests/test_registry_reload.py
git commit -m "fix(strategies): auto_discover fresh-imports edited plugins (reload picks up overwrites)"
```

---

### Task 6: `py_author` — POWERFUL-tier full-Python generation

**Files:**
- Create: `backend/app/ai/py_author.py`
- Test: `tests/test_py_author.py`

- [ ] **Step 1: Write the failing test** — `tests/test_py_author.py`:

```python
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.ai import llm_client
from app.ai.py_author import author_python, AuthoredPython
from app.ai.strategy_author import Fidelity

_CANNED = AuthoredPython(
    code="from app.strategies.base import StrategyBase, Signal\n",
    fidelity=Fidelity(captured=["x"]), notes="n", suggested_id="demo",
)


def test_author_python_uses_powerful_tier_and_forwards_provider(monkeypatch):
    seen = {}
    def fake(*, tier, system, user, output_model, provider=None, max_tokens=4000):
        seen.update(tier=tier, provider=provider); return _CANNED
    monkeypatch.setattr(llm_client, "complete_structured", fake)
    out = author_python("write me a strategy", provider="gemini")
    assert seen["tier"] == llm_client.POWERFUL
    assert seen["provider"] == "gemini"
    assert out["code"].startswith("from app.strategies.base")
    assert out["suggested_id"] == "demo"


def test_system_prompt_grounded():
    from app.ai.py_author import _system_prompt
    p = _system_prompt({"indicator_columns": ["ema9", "rsi"]})
    assert "StrategyBase" in p and "evaluate" in p and "ema9" in p
    assert "is_builtin" in p
```

- [ ] **Step 2: Run to verify FAIL.**

- [ ] **Step 3: Implement `backend/app/ai/py_author.py`:**

```python
"""Map a free-text/transcript description to an arbitrary StrategyBase python module
via the POWERFUL tier. The output is gated by py_sandbox before install."""
from __future__ import annotations

from typing import Any, Dict

from pydantic import BaseModel

from app.ai.strategy_author import Fidelity


class AuthoredPython(BaseModel):
    code: str
    fidelity: Fidelity
    notes: str = ""
    suggested_id: str = ""


def _system_prompt(catalog: Dict[str, Any]) -> str:
    cols = ", ".join(sorted(catalog["indicator_columns"]) + ["open", "high", "low", "close", "volume"])
    return f"""You write ONE complete Python module defining EXACTLY ONE StrategyBase subclass for an \
Indian-index option-BUYING intraday backtester. Output only valid python in `code`.

# Hard structural rules (the module is statically validated; violations are rejected)
- Module top level may contain ONLY: `from __future__ import annotations`; imports of \
pandas/numpy/math/typing/dataclasses; `from app.strategies.base import StrategyBase, Signal`; \
and the single class. NO other top-level statements (no module-level assignments, prints, calls).
- The class inherits ONLY from StrategyBase, has NO decorators and NO metaclass.
- Do NOT use: os/sys/subprocess/eval/exec/open/getattr/type()/__import__, any dunder attribute \
(e.g. __class__, __mro__), or pandas/numpy SUBMODULES (pandas.io, numpy.f2py, numpy.ctypeslib).
- evaluate(self, row, prev, params, ctx) must be a PURE function returning a Signal.

# Required class attributes
id (lowercase slug ^[a-z][a-z0-9_]*$, a STRING LITERAL), name, version="1.0.0", description, \
is_builtin = False, supported_instruments, supported_modes (["SCALP","INTRADAY"]), \
supported_timeframes, parameter_schema (dict literal {{name: {{type,min,max,default}}}}).

# Data available on row/prev (reference ONLY these column names)
{cols}
row/prev are pandas Series; index with row["close"] etc. and wrap in float(...). `params` is a \
dict of your parameter_schema defaults; `ctx` is a dict (may be empty). A SCALP/INTRADAY strategy \
should set at least one exit on the Signal (spot_target_pts/spot_stop_pts/target_pct/stop_pct/time_stop_minutes).

# Signal(direction=..., ...) fields
direction ("CE" buy-call / "PE" buy-put / "NONE"), score, reasons, blockers, target_pct, stop_pct, \
time_stop_minutes, spot_target_pts, spot_stop_pts.

# fidelity (be honest): captured (what you encoded), couldnt_map (rules with no column/representation), \
ambiguous (needs clarification). suggested_id: the id slug you chose."""


def author_python(source_text: str, provider: str | None = None) -> Dict[str, Any]:
    from app.ai.grounding import build_grounding_catalog
    from app.ai import llm_client

    catalog = build_grounding_catalog()
    out: AuthoredPython = llm_client.complete_structured(
        tier=llm_client.POWERFUL,
        system=_system_prompt(catalog),
        user=source_text,
        output_model=AuthoredPython,
        provider=provider,
        max_tokens=8000,
    )
    return {"code": out.code, "fidelity": out.fidelity.model_dump(),
            "notes": out.notes, "suggested_id": out.suggested_id}
```

- [ ] **Step 4: Run to verify PASS.** **Step 5: Commit**

```bash
git add backend/app/ai/py_author.py tests/test_py_author.py
git commit -m "feat(ai): py_author — POWERFUL-tier full-Python strategy generation"
```

---

### Task 7: Schemas + the three router endpoints

**Files:**
- Modify: `backend/app/schemas.py`
- Modify: `backend/app/routers/strategies_admin.py`
- Test: `tests/test_py_install_routes.py` (new)

- [ ] **Step 1: Write failing tests** — `tests/test_py_install_routes.py`:

```python
import sys
from pathlib import Path
from unittest.mock import patch
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
import app.routers.strategies_admin as sa

VALID = (
    "from app.strategies.base import StrategyBase, Signal\n"
    "class Demo(StrategyBase):\n"
    "    id = \"py_demo\"\n"
    "    is_builtin = False\n"
    "    def evaluate(self, row, prev, params, ctx):\n"
    "        return Signal(direction=\"NONE\")\n"
)


def _app():
    a = FastAPI(); a.include_router(sa.api); return TestClient(a, raise_server_exceptions=True)


def test_python_from_source_forwards_provider(monkeypatch):
    canned = {"code": VALID, "fidelity": {"captured": []}, "notes": "", "suggested_id": "py_demo"}
    with patch("app.ai.llm_client.any_configured", return_value=True), \
         patch("app.ai.llm_client.resolve_provider", return_value="gemini"), \
         patch("app.ai.py_author.author_python", return_value=canned) as m:
        r = _app().post("/strategies/author/python-from-source", json={"source": "x", "provider": "gemini"})
    assert r.status_code == 200, r.text
    assert r.json()["suggested_id"] == "py_demo"
    assert m.call_args.kwargs.get("provider") == "gemini" or m.call_args[0][1] == "gemini"


def test_validate_clean_runs_smoke(monkeypatch):
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


def test_install_happy_path(monkeypatch, tmp_path):
    monkeypatch.setattr(sa, "_plugins_dir", lambda: str(tmp_path))
    with patch("app.ai.py_sandbox.smoke_test", return_value={"ok": True, "error": None, "signal_repr": "S"}), \
         patch.object(sa, "_db") as db:
        # provenance upsert is async; stub it
        async def _noop(*a, **k): return None
        db.return_value.generated_strategies.update_one = _noop
        r = _app().post("/strategies/author/python/install", json={"code": VALID, "strategy_id": "py_demo"})
    assert r.status_code == 200, r.text
    assert (tmp_path / "py_demo.py").exists()
    # cleanup the registered strategy
    from app.strategies.base import get_registry
    get_registry().unregister("py_demo")
```

- [ ] **Step 2: Run to verify FAIL.**

- [ ] **Step 3: Add schemas** to `backend/app/schemas.py` (ensure `Optional` imported):

```python
class PythonFromSourceReq(BaseModel):
    source: str
    provider: Optional[str] = None


class PythonValidateReq(BaseModel):
    code: str


class PythonInstallReq(BaseModel):
    code: str
    strategy_id: str
    overwrite: bool = False
```

- [ ] **Step 4: Add the endpoints** to `backend/app/routers/strategies_admin.py` (near the other author routes). Import the new schemas at the top with the existing ones.

```python
@api.post("/strategies/author/python-from-source")
async def author_python_from_source(req: PythonFromSourceReq):
    """Generate an arbitrary StrategyBase module via the POWERFUL tier. No install."""
    from app.ai import llm_client
    from app.ai.source_ingest import ingest_source
    from app.ai.py_author import author_python
    if not llm_client.any_configured():
        raise HTTPException(503, "AI authoring is not configured — set GEMINI_API_KEY or ANTHROPIC_API_KEY in backend/.env")
    if not (req.source or "").strip():
        raise HTTPException(400, "source is empty")
    if req.provider:
        try:
            llm_client.resolve_provider(req.provider)
        except RuntimeError as e:
            raise HTTPException(400, str(e))
    try:
        ing = ingest_source(req.source)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except RuntimeError as e:
        raise HTTPException(502, f"Transcript fetch failed: {e}")
    try:
        out = author_python(ing["text"], provider=req.provider)
    except RuntimeError as e:
        raise HTTPException(502, f"AI generation failed: {e}")
    out["source_kind"] = ing["kind"]
    return out


@api.post("/strategies/author/python/validate")
async def author_python_validate(req: PythonValidateReq):
    """Static-check; if clean, smoke-test. Never raises on bad code."""
    from app.ai.py_sandbox import static_check, smoke_test
    violations = static_check(req.code or "")
    if violations:
        return {"ok": False, "violations": violations, "smoke": None}
    smoke = smoke_test(req.code)
    return {"ok": bool(smoke.get("ok")), "violations": [], "smoke": smoke}


@api.post("/strategies/author/python/install")
async def author_python_install(req: PythonInstallReq):
    """Server re-validates (static + smoke), then writes + reloads + records provenance."""
    import hashlib
    from datetime import datetime, timezone
    from app.ai import llm_client
    from app.ai.py_sandbox import static_check, smoke_test, extract_strategy_id
    violations = static_check(req.code or "")
    if violations:
        raise HTTPException(400, "static check failed: " + "; ".join(violations))
    cid = extract_strategy_id(req.code)
    if cid is None or cid != req.strategy_id:
        raise HTTPException(400, f"the module's class id ({cid!r}) must be a literal slug equal to strategy_id ({req.strategy_id!r})")
    reg = get_registry()
    origin = reg.origin_of(req.strategy_id)
    if origin == "builtin":
        raise HTTPException(403, "cannot overwrite a built-in strategy id")
    if origin is not None and not req.overwrite:
        raise HTTPException(409, f"Strategy id '{req.strategy_id}' already exists — choose another id or set overwrite")
    smoke = smoke_test(req.code)
    if not smoke.get("ok"):
        raise HTTPException(422, f"smoke-test failed: {smoke.get('error')}")
    _write_plugin_file(req.strategy_id, req.code)
    reg.reload()
    if reg.get(req.strategy_id) is None:
        try:
            _delete_plugin_file(req.strategy_id)
        except Exception:
            pass
        raise HTTPException(500, f"Strategy '{req.strategy_id}' failed to load after install")
    now = datetime.now(timezone.utc).isoformat()
    code_sha = hashlib.sha256(req.code.encode("utf-8")).hexdigest()[:16]
    model = llm_client.model_for(llm_client.resolve_provider(req.provider if hasattr(req, "provider") else None), llm_client.POWERFUL) \
        if llm_client.any_configured() else None
    await _db().generated_strategies.update_one(
        {"strategy_id": req.strategy_id},
        {"$set": {"strategy_id": req.strategy_id, "source": "full_python", "code": req.code,
                  "code_sha": code_sha, "model": model, "created_at": now}},
        upsert=True,
    )
    return {"strategy_id": req.strategy_id, "installed": True, "code_sha": code_sha}
```

(Note: `_write_plugin_file`/`_delete_plugin_file`/`_plugins_dir`/`_db` already exist in this router. The `model` line is best-effort provenance — if it complicates the test, the implementer may simplify to `model=None`.)

- [ ] **Step 5: Run to verify PASS** — `"$PY" -m pytest tests/test_py_install_routes.py -q`. Host import: `"$PY" -c "import sys; sys.path.insert(0,'backend'); import app.routers.strategies_admin, app.schemas; print('import ok')"`.

- [ ] **Step 6: Commit**

```bash
git add backend/app/schemas.py backend/app/routers/strategies_admin.py tests/test_py_install_routes.py
git commit -m "feat(ai): /python-from-source + /python/validate + /python/install endpoints (server re-validates)"
```

---

### Task 8: Frontend — mode toggle + Full-Python panel

**Files:**
- Modify: `frontend/src/lib/api.js`
- Modify: `frontend/src/components/strategy/AuthoringWizard.jsx`

- [ ] **Step 1: Add API methods** to `frontend/src/lib/api.js` (alongside the Part 1 author methods):

```javascript
  authorPythonFromSource: (source, provider) =>
    apiClient.post("/strategies/author/python-from-source", { source, provider }, { timeout: LONG_TIMEOUT_MS }).then((r) => r.data),
  validatePython: (code) =>
    apiClient.post("/strategies/author/python/validate", { code }, { timeout: LONG_TIMEOUT_MS }).then((r) => r.data),
  installPython: (code, strategy_id, overwrite = false) =>
    apiClient.post("/strategies/author/python/install", { code, strategy_id, overwrite }).then((r) => r.data),
```

- [ ] **Step 2: Add the mode toggle + Full-Python panel** to `AuthoringWizard.jsx`. Add state near the AI state:

```javascript
  const [mode, setMode] = useState("spec"); // "spec" | "python"
  const [pyCode, setPyCode] = useState("");
  const [pyNotes, setPyNotes] = useState("");
  const [pyBusy, setPyBusy] = useState(false);
  const [pyValidation, setPyValidation] = useState(null); // { ok, violations, smoke }
  const validationTokenRef = useRef(0); // bumped on every edit; stale Validate responses ignored
```

Add a toggle row at the top of the dialog body (before the ✨ box):
```jsx
        <div className="flex gap-2">
          {[["spec", "Spec (fast)"], ["python", "Full Python (powerful)"]].map(([m, label]) => (
            <button key={m} onClick={() => setMode(m)} data-testid={`author-mode-${m}`}
              className={`text-xs px-3 py-1.5 rounded-md border ${mode === m ? "bg-info/15 border-info/50 text-foreground" : "bg-bg-2 border-line text-dim"}`}>
              {label}
            </button>
          ))}
        </div>
```

In the ✨ "Describe with AI" box, branch the Generate handler on `mode`:
```javascript
  async function onGenerate() {
    if (mode === "python") return onGeneratePython();
    return onGenerateWithAi(); // existing Spec-mode handler
  }

  async function onGeneratePython() {
    if (!aiSource.trim()) return;
    setPyBusy(true);
    try {
      const res = await api.authorPythonFromSource(aiSource, provider || undefined);
      setPyCode(res.code || "");
      setPyNotes(res.notes || "");
      setFidelity(res.fidelity || null);
      setPyValidation(null);
      validationTokenRef.current += 1;
      toast.success("AI wrote a strategy — review, validate, then install");
    } catch (e) {
      toast.error("Generation failed: " + (e?.response?.data?.detail || e?.message || "unknown error"));
    } finally { setPyBusy(false); }
  }

  function onPyCodeEdit(v) {
    setPyCode(v);
    validationTokenRef.current += 1;   // any edit invalidates the last validate pass
    setPyValidation(null);
  }

  async function onValidatePython() {
    const token = validationTokenRef.current;
    setPyBusy(true);
    try {
      const res = await api.validatePython(pyCode);
      if (token === validationTokenRef.current) setPyValidation(res); // ignore stale
    } catch (e) {
      if (token === validationTokenRef.current)
        setPyValidation({ ok: false, violations: [e?.response?.data?.detail || e?.message || "validate failed"], smoke: null });
    } finally { setPyBusy(false); }
  }

  async function onInstallPython() {
    setPyBusy(true);
    try {
      const id = extractIdFromCode(pyCode); // simple regex: /id\s*=\s*["']([a-z][a-z0-9_]*)["']/
      const res = await api.installPython(pyCode, id);
      toast.success("Installed " + res.strategy_id);
      onInstalled?.(); onOpenChange(false);
    } catch (e) {
      toast.error("Install failed: " + (e?.response?.data?.detail || e?.message || "unknown error"));
    } finally { setPyBusy(false); }
  }
```

Render the Full-Python body only when `mode === "python"` (instead of the spec form): the editable `<textarea data-testid="author-py-code" value={pyCode} onChange={e => onPyCodeEdit(e.target.value)}>` (monospace, ~16 rows), `pyNotes`, the fidelity readback, a **Validate** button (`data-testid="author-py-validate"`), a violations/smoke result block, and an **Install** button (`data-testid="author-py-install"`) `disabled={!pyValidation?.ok || pyBusy}`. Add `extractIdFromCode` helper at module scope:
```javascript
function extractIdFromCode(code) {
  const m = /id\s*=\s*["']([a-z][a-z0-9_]*)["']/.exec(code || "");
  return m ? m[1] : "";
}
```
Wire the Generate button's onClick to `onGenerate`, and disable it with `|| (mode === 'python' && pyBusy)`.

- [ ] **Step 3: Build to verify it compiles** — junction `node_modules` from the main repo, then `& "node_modules\.bin\craco.cmd" build` (per the Part 1 procedure), confirm "Compiled successfully", then `cmd /c rmdir` the junction.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/lib/api.js frontend/src/components/strategy/AuthoringWizard.jsx
git commit -m "feat(strategy-library): Spec/Full-Python mode toggle + editable code panel (validate-gated install)"
```

---

### Task 9: Full host-suite regression

- [ ] **Step 1: Run** `cd "/c/Users/haroo/af-wt-strategy-library" && "$PY" -m pytest tests -q` → all pass (prior + the new `test_py_sandbox.py` / `test_py_author.py` / `test_py_install_routes.py` / `test_registry_reload.py`).
- [ ] **Step 2:** commit any incidental fixups (`git commit -am "test(ai): full-python suite green" || echo none`).

---

### Task 10: Adversarial sandbox-evasion review (Workflow, controller-run)

This is run by the CONTROLLER (not a per-task subagent) after Task 9: a Workflow of parallel "try to evade `static_check`" agents seeded with the audit payloads (`pd.io.common.os.system`, `numpy.f2py.os.fork`, `type(...).__mro__`, decorator/metaclass/default-arg hooks, comprehension/lambda tricks). Each confirmed evasion → a new denylist rule in `py_sandbox.py` + a regression test in `test_py_sandbox.py`, then re-run Task 9. Commit: `fix(ai): close static_check evasions found by adversarial review`.

---

### Task 11: Live validation on Gemini Pro

- [ ] **Step 1:** rebuild the side-by-side wt Docker backend (`docker compose -p alphaforge_wt ... build --no-cache backend && up -d backend`) so it has the new code (`AI_PROVIDER=gemini` already set).
- [ ] **Step 2:** `POST /strategies/author/python-from-source` with a description Spec mode can't express (e.g. "score = 2*RSI-slope + MACD-hist z-score, buy calls when score>thr") → expect a code module.
- [ ] **Step 3:** `POST /strategies/author/python/validate` with that code → expect `ok:true` (or fix the prompt/denylist if Gemini emits something rejected).
- [ ] **Step 4:** `POST /strategies/author/python/install` → expect installed; confirm it appears in `GET /strategies` and is deployable.
- [ ] **Step 5:** Chrome (`:3001`): toggle to Full Python, Generate, edit a char (confirm Install re-disables), Validate, Install; verify no console errors.

---

### Task 12: Rebase onto main + open PR

- [ ] **Step 1:** `git fetch origin && git rebase origin/main` (resolve minimal conflicts).
- [ ] **Step 2:** `"$PY" -m pytest tests -q` → all pass.
- [ ] **Step 3:** `git push -u origin feat/strategy-full-python` + `gh pr create --base main --title "Full-Python escape hatch + mode toggle" --body "..."`.
- [ ] **Step 4:** hand the PR to the user to review/merge.

---

## Self-Review

**Spec coverage:** §3 contract → Task 6 prompt + Task 1 structure rules ✓; §4.1 py_author → Task 6 ✓; §4.2 static_check/extract_id/_interpret/smoke_test/driver → Tasks 1–4 ✓; §4.3 endpoints → Task 7 ✓; §4.4 reload fix → Task 5 ✓; §4.5 source-drift → noted (no new code; uses existing repin) ✓; §5 frontend toggle+panel+token-race → Task 8 ✓; §6 host-safety → seams in Tasks 4/7 ✓; §7 evasion review → Task 10 ✓; §8 tests → Tasks 1–9 (pure + seams + hermetic real install loop in Task 5) ✓; §9 rollout → Tasks 11–12 ✓.

**Placeholder scan:** Task 8 frontend renders are described prose + the key snippets/test-ids are given (the wizard JSX body is large; the implementer follows the existing render patterns). Task 10/11 are controller/live steps (no code placeholders). Task 7's `model` provenance line is flagged as best-effort-simplifiable. No "TBD".

**Type consistency:** `static_check`/`extract_strategy_id`/`_interpret_smoke_result`/`smoke_test` signatures match across Tasks 1–4, 7. `AuthoredPython{code,fidelity,notes,suggested_id}` consistent Task 6↔7. Endpoint paths + request schemas (`PythonFromSourceReq`/`PythonValidateReq`/`PythonInstallReq`) consistent Task 7↔8. `_plugins_dir`/`_write_plugin_file`/`_delete_plugin_file`/`_db`/`origin_of` are existing router symbols.
