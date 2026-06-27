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


def test_registry_is_empty_by_default():
    assert FEATURE_REGISTRY == {}


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
