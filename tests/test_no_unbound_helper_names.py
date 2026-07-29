"""A module that USES a shared helper must actually BIND it.

User-reported (2026-07-29): "Optimization failed: name
'is_premium_trigger_strategy' is not defined". Six call sites in `optimizer.py`,
zero imports — every optimizer run for any strategy raised NameError.

Root cause of the bug: the patch that introduced those call sites guarded the new
module-level import on `if "from app.premium_trigger_dispatch import" not in src`.
That string was already present — as a LOCAL import inside a function — so the
guard skipped and the import was never added.

Root cause of it SHIPPING: the accompanying test asserted
`"is_premium_trigger_strategy" in src`. A grep cannot tell a USE from a
DEFINITION, so six unbound usages satisfied it perfectly. The suite was green and
the feature was broken at import time.

This file closes the class rather than the instance: any module referencing one
of the shared helpers must bind it (import, def, class, or assignment). It is a
static check, so it catches the failure without needing to execute the code path
— which is exactly what the previous test could not do.
"""
from __future__ import annotations

import ast
import os
from typing import List, Set

_ROOT = os.path.join(os.path.dirname(__file__), "..", "backend", "app")

#: Cross-module helpers introduced by the Phase 1 work. A name here that is used
#: without being bound is a NameError waiting for a user to find.
SHARED_HELPERS: Set[str] = {
    "is_premium_trigger_strategy",
    "extract_premium_trigger_config",
    "premium_trigger_allowed_keys",
    "resolve_deployment_premium_trigger",
    "effective_premium_params",
    "is_premium_native_deployment",
    "ENGINE_PARAM_KEYS",
    "StructuredOutputError",
    "resolve_lot_size",
    # Added 2026-07-30: the preflight rewire used `contract_identity_key` in
    # runtime.py while only `canonical_instrument_key` was imported. The
    # accompanying tests were source contracts, which do not execute the path —
    # the same blind spot that shipped the original NameError.
    "contract_identity_key",
    "canonical_instrument_key",
    "build_candles_by_key",
    "candle_contract_identity",
    "premium_walk_forward",
    "resolve_premium_lots",
    "build_engine_params",
    "premium_dropped_params",
    "restrict_space_to_engine_params",
    "premium_preload_needs_lazy",
}


def _bound_names(tree: ast.AST) -> Set[str]:
    """Every name the module binds anywhere (imports, defs, assignments)."""
    bound: Set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                bound.add(alias.asname or alias.name.split(".")[0])
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            bound.add(node.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    bound.add(target.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            bound.add(node.target.id)
    return bound


def _offenders() -> List[str]:
    out: List[str] = []
    for dirpath, _dirs, files in os.walk(_ROOT):
        for fn in files:
            if not fn.endswith(".py"):
                continue
            path = os.path.join(dirpath, fn)
            with open(path, encoding="utf-8") as fh:
                try:
                    tree = ast.parse(fh.read())
                except SyntaxError:
                    continue
            used = {n.id for n in ast.walk(tree)
                    if isinstance(n, ast.Name) and n.id in SHARED_HELPERS}
            if not used:
                continue
            missing = sorted(used - _bound_names(tree))
            if missing:
                out.append(f"{os.path.relpath(path, _ROOT)} uses {missing} without binding them")
    return out


def test_no_module_uses_a_shared_helper_it_never_bound():
    """THE regression: optimizer.py called it six times and imported it zero."""
    offenders = _offenders()
    assert not offenders, "NameError at runtime:\n  " + "\n  ".join(offenders)


def test_the_optimizer_can_actually_resolve_the_predicate():
    """Direct proof for the reported failure — asserting the ATTRIBUTE exists is
    what the old grep-based test could not do."""
    import sys
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
    from app import optimizer
    assert hasattr(optimizer, "is_premium_trigger_strategy"), (
        "optimizer references the predicate but does not bind it — every "
        "optimization run raises NameError")


def test_the_guard_would_catch_a_reintroduction():
    """Guard the guard: prove the detector actually fires on an unbound use."""
    tree = ast.parse("def f(strategy):\n    return is_premium_trigger_strategy(strategy)\n")
    used = {n.id for n in ast.walk(tree)
            if isinstance(n, ast.Name) and n.id in SHARED_HELPERS}
    assert used and (used - _bound_names(tree)), "the detector would miss this"
