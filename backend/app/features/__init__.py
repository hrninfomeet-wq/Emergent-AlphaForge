"""Opt-in structural feature framework. See registry.py + catalog.py."""
from app.features.registry import (
    FeatureGroup,
    FEATURE_REGISTRY,
    FeatureError,
    resolve_features,
    materialize_features,
)
from app.features.catalog import (
    FEATURE_CATALOG,
    feature_catalog_entries,
    register_feature,
)

__all__ = [
    "FeatureGroup", "FEATURE_REGISTRY", "FeatureError",
    "resolve_features", "materialize_features",
    "FEATURE_CATALOG", "feature_catalog_entries", "register_feature",
]
