"""Opt-in structural-feature registry (FVG zones, swing levels, order blocks, ...).

Sibling to `app.indicator_groups`: features are computed AFTER the indicator
frame is assembled, ONLY for strategies that declare them via
`StrategyBase.required_features`. Empty declaration => this module is never
invoked => existing strategies are byte-identical.

Host-importable: pandas only, no motor/IO.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

import pandas as pd


@dataclass(frozen=True)
class FeatureGroup:
    name: str
    columns: Tuple[str, ...]
    param_keys: Tuple[str, ...]
    requires: Tuple[str, ...]
    cost_class: str                       # "vectorized" | "session_loop"
    session_anchored: bool                # needs a full/prior session (live caveat)
    stateful_unbounded: bool              # carry-forward depends on pre-window history
    min_history_bars: int
    compute: Callable[[pd.DataFrame, dict], Dict[str, pd.Series]]
    causal: bool = True


# Populated by app.features.catalog at import time (empty in SP-1).
FEATURE_REGISTRY: Dict[str, FeatureGroup] = {}


class FeatureError(Exception):
    """Raised when a strategy declares a feature the engine cannot provide.
    The authoring/feasibility layer converts this into a REJECT-with-explanation."""

    def __init__(self, code: str, *, name: str = "", available: Optional[List[str]] = None):
        self.code = code
        self.name = name
        self.available = available or []
        super().__init__(
            f"{code}: feature {name!r} is not registered. "
            f"available={sorted(self.available)}"
        )


def resolve_features(required: List[str]) -> List[FeatureGroup]:
    """Expand `required` to the dependency-closed, topologically-ordered group
    list to materialize. Raises FeatureError('unknown_feature') on any name (or
    transitive `requires` name) not in FEATURE_REGISTRY."""
    ordered: List[FeatureGroup] = []
    seen: set = set()
    visiting: set = set()

    def visit(name: str) -> None:
        if name in seen:
            return
        if name not in FEATURE_REGISTRY:
            raise FeatureError("unknown_feature", name=name,
                               available=list(FEATURE_REGISTRY))
        if name in visiting:
            raise FeatureError("cyclic_feature", name=name,
                               available=list(FEATURE_REGISTRY))
        visiting.add(name)
        g = FEATURE_REGISTRY[name]
        for dep in g.requires:
            visit(dep)
        visiting.discard(name)
        seen.add(name)
        ordered.append(g)

    for n in required:
        visit(n)
    return ordered


def feature_live_feasible(group: "FeatureGroup", *, live_window: int = 200,
                          max_history: int = 150) -> bool:
    """Pure derivation of live-deployability on the rolling live window.

    A feature is live-correct on the ~200-bar deployment window only when it is
    NOT session-anchored (needs this session's range), NOT stateful-unbounded
    (carry-forward selection may depend on history older than the window), and
    its warm-up fits inside the window with headroom. Otherwise it is
    backtest-only in v1 (SP-4's agent refuses live deploy + explains).
    """
    if group.session_anchored:
        return False
    if group.stateful_unbounded:
        return False
    if group.min_history_bars > max_history:
        return False
    return True


def materialize_features(df: pd.DataFrame, params: dict, required: List[str],
                         feature_caches: Dict[str, Dict], *,
                         max_per_group: int = 4) -> pd.DataFrame:
    """Append the declared features' columns to a COPY of `df` (never mutates the
    caller's frame, which may be an indicator-cache frame). Mirrors
    `indicator_groups.enrich_with_cache`'s cache contract:
    feature_caches[name][param_key_tuple] -> {col: Series}."""
    if not required:
        return df
    out = df.copy()
    for g in resolve_features(required):
        key = tuple(params.get(k) for k in g.param_keys)
        cache = feature_caches.setdefault(g.name, {})
        cols = cache.get(key)
        if cols is None:
            cols = g.compute(out, params)
            if len(cache) < max_per_group:
                cache[key] = cols
        for c, s in cols.items():
            out[c] = s
    return out
