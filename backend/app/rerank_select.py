"""Option re-rank shortlist selection — pure, dependency-light.

Lives outside app.optimizer (which imports optuna) so it is unit-testable on a
host without the heavy optimizer dependencies, matching the project's
"tests never need optuna/motor" rule.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List

# Sentinel the optimizer assigns to guard-failing / zero-trade trials.
DISQUALIFY = -1e9


def select_rerank_candidates(
    sorted_trials: List[Dict[str, Any]],
    *,
    top_k: int,
    diversity: bool = False,
) -> List[Dict[str, Any]]:
    """Pick up to `top_k` unique-param candidates (from spot-ranked trials) to
    re-score on REAL paired-option net rupee.

    Default (diversity=False): the `top_k` strongest by the spot objective — the
    historical behavior. The risk the review flagged is that the spot objective
    and option rupee are correlated but NOT identical, so a config that is
    option-profitable yet only spot-mediocre never enters the shortlist and can
    never win.

    diversity=True: keep the strongest ~70% by spot objective, then fill the
    remaining budget with an EVENLY-SPACED sample across the rest of the qualified
    trials, so such configs get a chance to be re-scored — without increasing the
    (expensive) option-evaluation count. Pure + deterministic for testability.
    """
    seen = set()
    qualified: List[Dict[str, Any]] = []
    for t in sorted_trials or []:
        if t.get("objective_value", DISQUALIFY) <= DISQUALIFY:
            continue
        key = json.dumps(t.get("params", {}), sort_keys=True, default=str)
        if key in seen:
            continue
        seen.add(key)
        qualified.append(t)
    top_k = max(1, int(top_k or 1))
    if not diversity or len(qualified) <= top_k:
        return qualified[:top_k]
    top_n = max(1, int(round(top_k * 0.7)))
    head = list(qualified[:top_n])
    rest = qualified[top_n:]
    budget = top_k - len(head)
    if budget > 0 and rest:
        if budget >= len(rest):
            head.extend(rest)
        else:
            step = len(rest) / float(budget)
            head.extend(rest[min(len(rest) - 1, int(i * step))] for i in range(budget))
    return head[:top_k]
