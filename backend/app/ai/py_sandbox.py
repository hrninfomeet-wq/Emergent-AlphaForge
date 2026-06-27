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
    class_defs: List[ast.ClassDef] = []
    for i, node in enumerate(tree.body):
        if i == 0 and isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) \
                and isinstance(node.value.value, str):
            continue
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

    seen, out = set(), []
    for e in errors:
        if e not in seen:
            seen.add(e); out.append(e)
    return out
