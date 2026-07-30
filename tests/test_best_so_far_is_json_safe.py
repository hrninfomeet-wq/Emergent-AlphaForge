"""best_so_far must never persist a non-finite value.

Claim 21, CONFIRMED — and I initially REFUTED it, wrongly, by checking only two of
the five writers.

`_flush_trial_log` (optimizer.py:731) and the job finish payload (optimizer.py:2007)
both guard: `round(v, 4) if v > -1e8 else None`. But the three high-cadence progress
updates inside the trial loops — grid, sequential-bayesian and parallel — did not:

    "best_so_far": {"value": round(best_so_far["value"], 4), ...}

`best_so_far["value"]` is initialised to `-float("inf")` and is only replaced by
`if val > best_so_far["value"]`. In the GRID path a raising combo appends an error
record and `continue`s WITHOUT touching it — so if the first 5 combinations all
raise, the `completed % 5 == 0` update writes `-Infinity` straight into Mongo.
`round(-inf, 4)` is still `-inf`.

FastAPI serialises with `allow_nan=False`, so the value then 500s the whole
job-history endpoint — not just that job, the entire list. A single unlucky job
takes down the page.

Fixed with ONE helper used by every writer, so a sixth site cannot drift again.
"""
from __future__ import annotations

import inspect
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from app.optimizer import best_so_far_doc  # noqa: E402


def _bsf(value):
    return {"value": value, "params": {"a": 1}, "metrics": {"trade_count": 3},
            "trial_num": 7}


def test_negative_infinity_becomes_none():
    """THE bug: an all-raising grid job wrote -Infinity at the first %5 update."""
    assert best_so_far_doc(_bsf(-float("inf")))["value"] is None


def test_positive_infinity_becomes_none():
    assert best_so_far_doc(_bsf(float("inf")))["value"] is None


def test_nan_becomes_none():
    assert best_so_far_doc(_bsf(float("nan")))["value"] is None


def test_the_disqualify_sentinel_becomes_none():
    """A very negative sentinel is 'no usable result', not a score to display."""
    assert best_so_far_doc(_bsf(-1e9))["value"] is None


def test_a_real_score_survives_and_is_rounded():
    assert best_so_far_doc(_bsf(1.23456789))["value"] == 1.2346


def test_a_real_negative_score_survives():
    """neg_max_dd legitimately produces negative objectives."""
    assert best_so_far_doc(_bsf(-42.5))["value"] == -42.5


def test_the_other_fields_are_carried_through():
    out = best_so_far_doc(_bsf(1.0))
    assert out["params"] == {"a": 1}
    assert out["metrics"] == {"trade_count": 3}
    assert out["trial_num"] == 7


def test_a_missing_trial_num_is_safe():
    out = best_so_far_doc({"value": 1.0, "params": {}, "metrics": {}})
    assert out["trial_num"] is None


def test_the_output_is_json_serialisable_under_fastapi_rules():
    """FastAPI uses allow_nan=False; that is what turned this into a 500."""
    for v in (-float("inf"), float("inf"), float("nan"), -1e9, 1.5):
        json.dumps(best_so_far_doc(_bsf(v)), allow_nan=False)


# --------------------------------------------------------------------------- #
# every writer must use it
# --------------------------------------------------------------------------- #

def test_no_writer_builds_the_dict_by_hand():
    """Targets the WRITER shape, not the substring: every persisted best_so_far
    must come from the helper. The three trial-loop progress updates were the
    unguarded ones."""
    from app import optimizer
    src = inspect.getsource(optimizer)
    assert '"best_so_far": {' not in src, (
        "a hand-built best_so_far document remains — it can persist -Infinity")


def test_best_value_uses_the_same_sanitiser():
    """Its old guard (`> -1e8`) let +inf through, since inf > -1e8 is True."""
    from app import optimizer
    src = inspect.getsource(optimizer)
    assert '"best_value": best_so_far_doc(best_so_far)["value"],' in src


def test_the_helper_is_used_by_several_sites():
    from app import optimizer
    src = inspect.getsource(optimizer)
    assert src.count("best_so_far_doc(") >= 5, (
        f"expected every writer to use the helper, found "
        f"{src.count('best_so_far_doc(')} call(s)")
