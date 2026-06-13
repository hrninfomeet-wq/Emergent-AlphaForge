"""select_rerank_candidates: the option re-rank shortlist (review Item 6b).

Default = top-K by the spot objective (historical behavior). Opt-in diversity
broadens the shortlist with an evenly-spaced tail sample so an option-profitable
but spot-mediocre config can surface, without increasing the option-eval count.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from app.rerank_select import select_rerank_candidates, DISQUALIFY as _DISQUALIFY  # noqa: E402


def _trials(n):
    # Already sorted by spot objective descending (as the optimizer passes them).
    return [{"params": {"p": i}, "objective_value": float(100 - i)} for i in range(n)]


def test_default_takes_top_k_by_spot_objective():
    out = select_rerank_candidates(_trials(100), top_k=10)
    assert [t["params"]["p"] for t in out] == list(range(10))


def test_dedups_by_params_and_skips_disqualified():
    trials = [
        {"params": {"p": 1}, "objective_value": 50.0},
        {"params": {"p": 1}, "objective_value": 40.0},        # duplicate params
        {"params": {"p": 2}, "objective_value": _DISQUALIFY},  # guard-failed
        {"params": {"p": 3}, "objective_value": 30.0},
    ]
    out = select_rerank_candidates(trials, top_k=10)
    assert [t["params"]["p"] for t in out] == [1, 3]


def test_diversity_keeps_top_and_adds_tail_configs():
    trials = _trials(100)
    plain = {t["params"]["p"] for t in select_rerank_candidates(trials, top_k=10)}
    diverse = {t["params"]["p"] for t in select_rerank_candidates(trials, top_k=10, diversity=True)}
    assert len(diverse) == 10
    # Strongest ~70% (top_n = round(10*0.7) = 7 -> spot ranks 0..6) are retained.
    assert {0, 1, 2, 3, 4, 5, 6} <= diverse
    # ...and tail configs beyond the plain top-10 are now eligible to be re-scored.
    assert any(p >= 10 for p in diverse)
    assert diverse != plain


def test_diversity_is_noop_when_few_qualified():
    out = select_rerank_candidates(_trials(5), top_k=10, diversity=True)
    assert len(out) == 5  # fewer than top_k -> all of them, unchanged
