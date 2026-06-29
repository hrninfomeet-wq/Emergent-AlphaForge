"""SP-1: the opt-in feature registry (resolve + materialize), zero real features."""
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

import pandas as pd
import pytest
from app.features.registry import (
    FeatureGroup, FEATURE_REGISTRY, FeatureError, resolve_features, materialize_features,
)


def _register(monkeypatch, *groups):
    reg = {g.name: g for g in groups}
    monkeypatch.setattr("app.features.registry.FEATURE_REGISTRY", reg)
    return reg


def test_registry_contains_seed_features():
    import app.features.catalog  # noqa: F401  -> ensures structures registered
    from app.features.registry import FEATURE_REGISTRY
    assert {"swing_levels", "premium_discount", "displacement"} <= set(FEATURE_REGISTRY)
    g = FEATURE_REGISTRY["swing_levels"]
    assert "last_swing_high_level" in g.columns


def test_resolve_unknown_feature_raises_feature_error():
    with pytest.raises(FeatureError) as ei:
        resolve_features(["nope"])
    assert ei.value.name == "nope"
    assert "nope" in str(ei.value)


def test_resolve_closes_over_requires_and_topo_sorts(monkeypatch):
    base = FeatureGroup(name="base", columns=("b",), param_keys=(), requires=(),
                        cost_class="vectorized", session_anchored=False,
                        stateful_unbounded=False, min_history_bars=1,
                        compute=lambda df, p: {"b": df["close"]})
    dep = FeatureGroup(name="dep", columns=("d",), param_keys=(), requires=("base",),
                       cost_class="vectorized", session_anchored=False,
                       stateful_unbounded=False, min_history_bars=1,
                       compute=lambda df, p: {"d": df["b"] + 1})
    _register(monkeypatch, base, dep)
    order = [g.name for g in resolve_features(["dep"])]   # asked only for dep
    assert order == ["base", "dep"]                       # base pulled in + ordered first


def test_materialize_applies_columns_in_dependency_order(monkeypatch):
    base = FeatureGroup(name="base", columns=("b",), param_keys=(), requires=(),
                        cost_class="vectorized", session_anchored=False,
                        stateful_unbounded=False, min_history_bars=1,
                        compute=lambda df, p: {"b": df["close"] * 2})
    dep = FeatureGroup(name="dep", columns=("d",), param_keys=(), requires=("base",),
                       cost_class="vectorized", session_anchored=False,
                       stateful_unbounded=False, min_history_bars=1,
                       compute=lambda df, p: {"d": df["b"] + 1})
    _register(monkeypatch, base, dep)
    df = pd.DataFrame({"close": [10.0, 20.0]})
    out = materialize_features(df, {}, ["dep"], {})
    assert list(out["b"]) == [20.0, 40.0]
    assert list(out["d"]) == [21.0, 41.0]
    assert "b" not in df.columns and "d" not in df.columns   # caller's df not mutated


def test_materialize_caches_param_independent_group_once(monkeypatch):
    calls = {"n": 0}
    def _compute(df, p):
        calls["n"] += 1
        return {"b": df["close"]}
    g = FeatureGroup(name="base", columns=("b",), param_keys=(), requires=(),
                     cost_class="vectorized", session_anchored=False,
                     stateful_unbounded=False, min_history_bars=1, compute=_compute)
    _register(monkeypatch, g)
    df = pd.DataFrame({"close": [1.0, 2.0]})
    cache = {}
    materialize_features(df, {}, ["base"], cache)
    materialize_features(df, {}, ["base"], cache)
    assert calls["n"] == 1            # second call served from cache


def test_catalog_entries_shape():
    import app.features.catalog as c
    entries = c.feature_catalog_entries()
    assert len(entries) >= 3
    e = next(x for x in entries if x["feature"] == "swing_levels")
    for k in ("feature", "columns", "needs_declaration", "requires", "cost_class",
              "session_anchored", "stateful_unbounded", "min_history_bars",
              "data_requirements", "description", "live_feasible"):
        assert k in e, k
    assert e["live_feasible"] is True


def test_package_exports():
    import app.features as feats
    assert hasattr(feats, "materialize_features")
    assert hasattr(feats, "resolve_features")
    assert hasattr(feats, "FEATURE_REGISTRY")
    assert hasattr(feats, "FeatureError")


def test_grounding_catalog_has_feature_block():
    import app.features.catalog  # noqa: F401
    from app.ai.grounding import build_grounding_catalog
    cat = build_grounding_catalog()
    assert "feature_columns" in cat
    assert "last_swing_high_level" in cat["feature_columns"]
    assert "all_columns_including_features" in cat
    assert set(cat["indicator_columns"]) <= set(cat["all_columns_including_features"])
    assert "last_swing_high_level" in cat["all_columns_including_features"]
