"""One raising trial must not destroy a completed optimization.

Confirmed 2026-07-30 (audit claims 4 / 9 / 12, verified by the orchestrator against
source rather than taken on trust).

The grid path deliberately survives a raising combo — `optimizer.py:1468-1477`:

    # bayesian study.optimize(catch=Exception): disqualify + continue.
    except Exception as exc:
        log.warning("grid trial %d raised (%s) — disqualified, continuing", ...)
        trial_history.append({"params": params, "metrics": None,
                              "objective_value": None, "error": str(exc)[:200]})
        completed += 1
        continue

…and then the analyze stage sorts on that key (`optimizer.py:1652`):

    sorted_trials = sorted(trial_history, key=lambda t: t["objective_value"], reverse=True)

In Python 3 comparing None with a float raises
`TypeError: '<' not supported between instances of 'NoneType' and 'float'`. So the
handler that exists specifically to keep the job alive is defeated by the sort —
and it fires AFTER every trial has finished, so the entire search is lost and the
job is persisted as `failed`.

Param importance (`optimizer.py:1667`) reads the same key into numeric work and has
the same exposure.

A failed trial has no score: it must be EXCLUDED from ranking and from importance,
not treated as the worst value (which would let it occupy a Top-N slot) and not
allowed to crash the stage.
"""
from __future__ import annotations

import inspect
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from app.optimizer import scored_trials  # noqa: E402

_HISTORY = [
    {"params": {"a": 1}, "metrics": {"trade_count": 10}, "objective_value": 1.5},
    {"params": {"a": 2}, "metrics": None, "objective_value": None, "error": "boom"},
    {"params": {"a": 3}, "metrics": {"trade_count": 20}, "objective_value": 2.5},
    {"params": {"a": 4}, "metrics": None, "objective_value": None, "error": "boom2"},
]


def test_failed_trials_are_excluded():
    out = scored_trials(_HISTORY)
    assert len(out) == 2
    assert all(t["objective_value"] is not None for t in out)


def test_the_sort_no_longer_raises_on_a_failed_trial():
    """THE bug: this sort is what killed the job after every trial had run."""
    ranked = sorted(scored_trials(_HISTORY),
                    key=lambda t: t["objective_value"], reverse=True)
    assert [t["params"]["a"] for t in ranked] == [3, 1]


def test_a_failed_trial_never_occupies_a_top_n_slot():
    """Treating None as -inf would keep it in the list and show a params blob with
    no metrics in the alternatives table."""
    out = scored_trials(_HISTORY)
    assert {t["params"]["a"] for t in out} == {1, 3}


def test_an_all_failed_history_is_empty_not_a_crash():
    only_bad = [t for t in _HISTORY if t["objective_value"] is None]
    assert scored_trials(only_bad) == []


def test_an_empty_history_is_safe():
    assert scored_trials([]) == []
    assert scored_trials(None) == []


def test_a_missing_key_is_treated_as_unscored():
    assert scored_trials([{"params": {"a": 9}}]) == []


def test_nan_is_treated_as_unscored():
    """A NaN would sort unpredictably AND break JSON on the way out."""
    assert scored_trials([{"params": {"a": 1}, "objective_value": float("nan")}]) == []


def test_disqualified_but_real_scores_are_kept():
    """_DISQUALIFY is a real (very negative) float, not None — it must still rank,
    because that is how the guard rails express 'valid run, unusable result'."""
    hist = [{"params": {"a": 1}, "objective_value": -1e9},
            {"params": {"a": 2}, "objective_value": 0.5}]
    assert len(scored_trials(hist)) == 2


# --------------------------------------------------------------------------- #
# both consumers must use it
# --------------------------------------------------------------------------- #

def test_the_top_n_sort_uses_the_helper():
    from app import optimizer
    src = inspect.getsource(optimizer.run_optimization)
    i = src.index("sorted_trials = sorted(")
    assert "scored_trials(" in src[max(0, i - 200):i + 200]


def test_param_importance_uses_the_helper():
    """It reads objective_value into numeric work — same exposure."""
    from app import optimizer
    src = inspect.getsource(optimizer.run_optimization)
    i = src.index('t["objective_value"]) for t in')
    assert "scored_trials(" in src[max(0, i - 400):i + 200]
