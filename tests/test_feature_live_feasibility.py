import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from app.features.registry import FeatureGroup, feature_live_feasible


def _g(**kw):
    base = dict(
        name="x", columns=("c",), param_keys=(), requires=(),
        cost_class="vectorized", session_anchored=False,
        stateful_unbounded=False, min_history_bars=10,
        compute=lambda df, p: {"c": df["close"]},
    )
    base.update(kw)
    return FeatureGroup(**base)


def test_vectorized_short_history_is_live_feasible():
    assert feature_live_feasible(_g()) is True


def test_session_anchored_is_not_live_feasible():
    assert feature_live_feasible(_g(session_anchored=True)) is False


def test_stateful_unbounded_is_not_live_feasible():
    assert feature_live_feasible(_g(stateful_unbounded=True)) is False


def test_long_history_exceeds_live_window():
    assert feature_live_feasible(_g(min_history_bars=200)) is False
