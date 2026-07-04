"""Pure convergence early-stop decision for the optimizer. No I/O — the optimizer
loop supplies the running counters. `n_trials` is treated as a CEILING: the search
stops once the best objective has not SIGNIFICANTLY improved for `patience` trials,
after a `warmup`. Off (early_stop=False) -> the optimizer never calls should_early_stop."""
from __future__ import annotations
import math


def is_significant_improvement(new_value: float, anchor_value: float, min_delta: float) -> bool:
    """True when new_value beats anchor_value by at least a relative min_delta of
    |anchor|. First improvement (anchor == -inf) is always significant. NaN -> False."""
    try:
        nv = float(new_value)
    except (TypeError, ValueError):
        return False
    if math.isnan(nv):
        return False
    if anchor_value == float("-inf"):
        return True
    return nv > anchor_value + abs(anchor_value) * float(min_delta)


def should_early_stop(*, completed: int, last_improve_trial: int, warmup: int, patience: int) -> bool:
    """Stop once at least `warmup` trials have run AND `patience`+ trials have passed
    since the last significant improvement. patience<1 disables (returns False)."""
    if patience < 1 or completed < warmup:
        return False
    return (completed - last_improve_trial) >= patience


def effective_warmup_patience(*, n_trials: int, warmup: int, patience: int) -> tuple[int, int]:
    """Scale the CEILING warmup/patience to this run's trial budget.

    The schema defaults warmup=patience=200, but the UI sends neither and uses
    default budgets (bayesian 150, grid/genetic 200) — so a literal 200-trial
    warmup means the advertised default-ON "Auto-stop when converged" can NEVER
    fire (a no-op the UI still promises). This derives per-run effective values:
    enough exploration before stopping, yet strictly below the budget so a real
    plateau can always trigger a stop. A user who deliberately raises warmup on a
    large run still gets up to their ceiling."""
    n = max(1, int(n_trials))
    eff_warmup = min(int(warmup), max(30, n // 3))
    eff_patience = min(int(patience), max(20, n // 5))
    eff_warmup = min(eff_warmup, max(1, n - 5))  # never swallow the whole budget
    return eff_warmup, eff_patience
