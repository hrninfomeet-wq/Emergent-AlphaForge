"""Feature metadata for grounding + the capability surface. Importing this module
registers every built-in FeatureGroup into FEATURE_REGISTRY. In SP-1 there are
none; SP-2 adds them here.

Each catalog entry advertises a feature to the AI authoring layer:
  {feature, columns, needs_declaration, requires, cost_class, session_anchored,
   stateful_unbounded, min_history_bars, data_requirements, description}.
"""
from __future__ import annotations

from typing import Any, Dict, List

from app.features.registry import FEATURE_REGISTRY, FeatureGroup, feature_live_feasible

# name -> human metadata (kept beside the registry; SP-2 populates both).
FEATURE_CATALOG: Dict[str, Dict[str, Any]] = {}


def register_feature(group: FeatureGroup, *, description: str,
                     data_requirements: List[str]) -> None:
    """Register a FeatureGroup + its catalog metadata. Idempotent by name."""
    FEATURE_REGISTRY[group.name] = group
    FEATURE_CATALOG[group.name] = {
        "description": description,
        "data_requirements": data_requirements,
    }


def feature_catalog_entries() -> List[Dict[str, Any]]:
    """The advertised feature list for grounding/AI (empty in SP-1)."""
    out: List[Dict[str, Any]] = []
    for name, g in FEATURE_REGISTRY.items():
        meta = FEATURE_CATALOG.get(name, {})
        out.append({
            "feature": name,
            "columns": list(g.columns),
            "needs_declaration": True,
            "requires": list(g.requires),
            "cost_class": g.cost_class,
            "session_anchored": g.session_anchored,
            "stateful_unbounded": g.stateful_unbounded,
            "min_history_bars": g.min_history_bars,
            "data_requirements": meta.get("data_requirements", ["ohlcv_1m"]),
            "description": meta.get("description", ""),
            "live_feasible": feature_live_feasible(g),
        })
    return out


# Importing structures registers the seed FeatureGroups (side-effect import at
# the bottom to avoid a circular import: structures imports register_feature).
from app.features import structures as _structures  # noqa: E402,F401
