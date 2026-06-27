"""AST allowlist + subprocess smoke-test for AI-authored full-Python strategies.

static_check is a STRUCTURAL ALLOWLIST: module top level may contain only imports
+ exactly one StrategyBase subclass (so nothing executes at import time); the class
takes no decorators/metaclass; a whole-tree denylist closes the known method-body
escapes. Pure ast — no execution, host-safe. See the design spec for the rules.
"""
from __future__ import annotations

import ast
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
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
# Dangerous attribute/method names: pandas/numpy I/O + string-exec + process. Checked on ANY
# attribute access (so passing e.g. pd.read_csv as a callable is blocked too). The `read_`
# prefix covers all pandas/numpy readers. FORBIDDEN_MODULE_ATTRS (private-reexport submodule
# names) is folded in. Safe converters (to_numpy/to_list/to_dict/...) are deliberately NOT here.
FORBIDDEN_ATTR_NAMES = FORBIDDEN_MODULE_ATTRS | {
    "eval", "query",
    "load", "save", "savez", "savez_compressed", "fromfile", "tofile", "frombuffer", "memmap",
    "loadtxt", "genfromtxt", "savetxt", "fromregex", "fromstring",
    "ndfromtxt", "mafromtxt", "recfromtxt", "recfromcsv", "DataSource",
    "to_pickle", "to_csv", "to_parquet", "to_json", "to_hdf", "to_feather", "to_sql",
    "to_excel", "to_html", "to_xml", "to_stata", "to_gbq", "to_clipboard", "to_orc",
    "system", "popen", "fork", "spawn", "getoutput", "check_output", "check_call", "run", "Popen",
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
    if node.level:
        errors.append("relative imports are not allowed")
        return
    mod = node.module or ""
    root = mod.split(".")[0]
    if root not in ALLOWED_IMPORT_ROOTS:
        errors.append(f"import from '{mod}' is not allowed")
    elif root == "app" and mod != "app.strategies.base":
        errors.append(f"the only allowed app import is app.strategies.base (got '{mod}')")
    if mod == "app.strategies.base":
        for alias in node.names:
            if alias.name not in ("StrategyBase", "Signal"):
                errors.append(f"from app.strategies.base may only import StrategyBase or Signal (got '{alias.name}')")


def _is_literal(node) -> bool:
    """True if an expression is a compile-time literal (no calls/comprehensions/names
    that would execute at class-definition time)."""
    if node is None:
        return True  # bare annotation, no value to execute
    if isinstance(node, ast.Constant):
        return True
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        return all(_is_literal(e) for e in node.elts)
    if isinstance(node, ast.Dict):
        return all(_is_literal(k) for k in node.keys if k is not None) \
            and all(_is_literal(v) for v in node.values)
    if isinstance(node, ast.UnaryOp):
        return _is_literal(node.operand)
    return False


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
            continue
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if stmt.name in ("__init_subclass__", "__class_getitem__"):
                errors.append(f"defining {stmt.name} is not allowed")
            if stmt.decorator_list:
                errors.append(f"method decorators are not allowed (on '{stmt.name}')")
            a = stmt.args
            for d in list(a.defaults) + [k for k in a.kw_defaults if k is not None]:
                if not _is_literal(d):
                    errors.append("method default-argument values must be literals (no calls/comprehensions at definition time)")
                    break
            if stmt.name == "evaluate":
                has_evaluate = True
            continue
        if isinstance(stmt, (ast.Assign, ast.AnnAssign)):
            targets = stmt.targets if isinstance(stmt, ast.Assign) else [stmt.target]
            if any(isinstance(t, ast.Name) and t.id == "id" for t in targets):
                has_id = True
            value = stmt.value if isinstance(stmt, ast.Assign) else stmt.value
            if not _is_literal(value):
                errors.append("class variables must be literals (no calls/comprehensions at class-definition time)")
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
    has_future_annotations = False
    class_defs: List[ast.ClassDef] = []
    for i, node in enumerate(tree.body):
        if i == 0 and isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) \
                and isinstance(node.value.value, str):
            continue
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            top_imports.add(id(node))
            _check_imports(node, errors)
            if isinstance(node, ast.ImportFrom) and node.module == "__future__" \
                    and any(al.name == "annotations" for al in node.names):
                has_future_annotations = True
            continue
        if isinstance(node, ast.ClassDef):
            class_defs.append(node)
            continue
        errors.append(f"top-level {type(node).__name__} is not allowed (only imports + one StrategyBase class)")

    if not has_future_annotations:
        errors.append("the module must start with 'from __future__ import annotations'")

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
            if node.attr.startswith("_"):
                errors.append(f"access to a private/dunder attribute '{node.attr}' is not allowed")
            elif node.attr.startswith("read_") or node.attr in FORBIDDEN_ATTR_NAMES:
                errors.append(f"access to a forbidden attribute '{node.attr}' is not allowed")
        elif isinstance(node, ast.Call):
            f = node.func
            if isinstance(f, ast.Name) and f.id in FORBIDDEN_CALL_NAMES:
                errors.append(f"call to '{f.id}' is not allowed")
            elif isinstance(f, ast.Attribute) and f.attr in FORBIDDEN_CALL_NAMES:
                errors.append(f"call to '.{f.attr}()' is not allowed")
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            errors.append("global/nonlocal is not allowed")
        elif isinstance(node, (ast.Import, ast.ImportFrom)) and id(node) not in top_imports:
            errors.append("imports are only allowed at module top level")
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load) and node.id in FORBIDDEN_CALL_NAMES:
            errors.append(f"reference to '{node.id}' is not allowed")
        elif isinstance(node, ast.Constant) and isinstance(node.value, str) \
                and (node.value in FORBIDDEN_ATTR_NAMES
                     or node.value in ("read_csv", "read_pickle", "read_parquet", "read_json", "read_table")):
            errors.append(f"string literal '{node.value}' (a forbidden method name) is not allowed")

    seen, out = set(), []
    for e in errors:
        if e not in seen:
            seen.add(e); out.append(e)
    return out


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
