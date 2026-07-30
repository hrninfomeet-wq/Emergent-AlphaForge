"""Three CONFIRMED HIGH defects from the Round-8 verification pass.

Each was checked against source by an independent verifier agent AND (for 10 and
24) by the orchestrator directly.

**Claim 24 — the profit_factor objective punishes a flawless strategy.**
`backtest.py:297` returns `None` when there are no losing trades:
    "profit_factor": round(gross_profit / abs(gross_loss), 3) if gross_loss < 0 else None
and `optimizer.py:164-166` maps that to the WORST possible score:
    if objective == "profit_factor":
        v = metrics.get("profit_factor")
        return float(v) if v is not None else 0.0
So a config with zero losses scores 0.0 while a mediocre one with PF 1.05 scores
1.05 — the objective actively steers away from the strictly-profitable configs.
The premium path already got this right (it uses a finite 999.0 sentinel for
wins-only), so the two families also disagreed.

**Claim 10 — `min_trades` never reaches Stage 2.**
`optimizer.py` `_option_rerank` ranks with
    ranked.sort(key=lambda r: (r["paired_trade_count"] > 0, r["option_pnl_value"]), reverse=True)
whose only sample guard is "more than zero". `min_trades` is enforced in
`_objective_value` against `metrics["trade_count"]`, which for an ORDINARY strategy
is the SPOT count — so a candidate with 120 spot trades and ONE paired option trade
passes the guard and can be promoted on that single trade's rupee P&L.

**Claim 6 — resume can skip trials it then counts as completed.**
`n_trials_completed` is written every 5 trials but `trial_log` only every 50, and
`server.py` flips a `running` job to `interrupted` on restart with no flush. Resume
then does `completed = int(rdoc.get("n_trials_completed") or len(trial_history))`
and the loops skip by that counter (`combos[completed:]`, `range(completed, ...)`),
so up to 49 combinations are never evaluated yet are reported as done.
Re-running a few trials is strictly better than silently skipping them.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from app.optimizer import (  # noqa: E402
    _objective_value,
    resolve_resume_completed,
    stage2_rank_key,
)


def _m(**kw):
    base = {"trade_count": 50, "win_rate": 50.0, "profit_factor": 1.2,
            "total_pnl_pts": 100.0, "max_dd_pts": -50.0, "sharpe": 1.0,
            "wins": 25, "losses": 25, "ce_count": 25, "pe_count": 25}
    base.update(kw)
    return base


# --------------------------------------------------------------------------- #
# claim 24 — profit_factor
# --------------------------------------------------------------------------- #

def test_a_zero_loss_config_beats_a_mediocre_one():
    """THE inversion: it used to score 0.0, below everything."""
    flawless = _objective_value(_m(profit_factor=None, wins=30, losses=0),
                                "profit_factor", lot_size=65, min_trades=10,
                                min_direction_share=0.0)
    mediocre = _objective_value(_m(profit_factor=1.05), "profit_factor",
                                lot_size=65, min_trades=10, min_direction_share=0.0)
    assert flawless > mediocre


def test_the_zero_loss_sentinel_is_finite():
    """inf breaks JSON serialisation and Optuna ranking alike."""
    import math
    v = _objective_value(_m(profit_factor=None, wins=30, losses=0),
                         "profit_factor", lot_size=65, min_trades=10,
                         min_direction_share=0.0)
    assert math.isfinite(v)


def test_no_wins_and_no_losses_is_still_the_worst_score():
    """Absence of a profit factor because nothing happened must NOT be rewarded."""
    v = _objective_value(_m(profit_factor=None, wins=0, losses=0, trade_count=50),
                         "profit_factor", lot_size=65, min_trades=10,
                         min_direction_share=0.0)
    assert v == 0.0


def test_a_real_profit_factor_is_unchanged():
    v = _objective_value(_m(profit_factor=1.87), "profit_factor", lot_size=65,
                         min_trades=10, min_direction_share=0.0)
    assert v == 1.87


def test_the_trade_count_guard_still_dominates():
    """A wins-only config with too few trades must still be disqualified."""
    v = _objective_value(_m(profit_factor=None, wins=2, losses=0, trade_count=2),
                         "profit_factor", lot_size=65, min_trades=10,
                         min_direction_share=0.0)
    assert v < 0


# --------------------------------------------------------------------------- #
# claim 10 — min_trades must reach Stage 2
# --------------------------------------------------------------------------- #

def _cand(paired, pnl):
    return {"paired_trade_count": paired, "option_pnl_value": pnl}


def test_a_single_paired_trade_cannot_outrank_a_real_sample():
    """The reported defect: one lucky paired trade won on rupees alone."""
    lucky = _cand(1, 99999.0)
    real = _cand(120, 50000.0)
    ranked = sorted([lucky, real], key=lambda r: stage2_rank_key(r, min_trades=30),
                    reverse=True)
    assert ranked[0] is real


def test_candidates_meeting_the_guard_still_rank_on_rupees():
    a, b = _cand(100, 10000.0), _cand(100, 20000.0)
    ranked = sorted([a, b], key=lambda r: stage2_rank_key(r, min_trades=30),
                    reverse=True)
    assert ranked[0] is b


def test_all_under_the_guard_still_produces_a_deterministic_order():
    """Never crash or lose candidates — the job must still finish and report."""
    a, b = _cand(2, 100.0), _cand(3, 500.0)
    ranked = sorted([a, b], key=lambda r: stage2_rank_key(r, min_trades=30),
                    reverse=True)
    assert ranked[0] is b and len(ranked) == 2


def test_zero_paired_still_ranks_last():
    """Pre-existing behaviour that must survive."""
    none_paired, one = _cand(0, 999999.0), _cand(1, 1.0)
    ranked = sorted([none_paired, one], key=lambda r: stage2_rank_key(r, min_trades=30),
                    reverse=True)
    assert ranked[0] is one


def test_min_trades_zero_disables_the_guard():
    lucky, real = _cand(1, 99999.0), _cand(120, 50000.0)
    ranked = sorted([lucky, real], key=lambda r: stage2_rank_key(r, min_trades=0),
                    reverse=True)
    assert ranked[0] is lucky


# --------------------------------------------------------------------------- #
# claim 6 — resume must not skip unlogged trials
# --------------------------------------------------------------------------- #

def test_resume_never_skips_past_the_evidence():
    """counter says 149, log holds 100 -> resume from 100. Re-running 49 trials is
    strictly better than never evaluating them."""
    assert resolve_resume_completed(n_trials_completed=149, trial_log_len=100) == 100


def test_resume_trusts_the_counter_when_the_log_agrees():
    assert resolve_resume_completed(n_trials_completed=100, trial_log_len=100) == 100


def test_a_longer_log_than_counter_is_respected():
    """The log is the evidence; never discard recorded trials."""
    assert resolve_resume_completed(n_trials_completed=40, trial_log_len=50) == 50


def test_an_absent_counter_falls_back_to_the_log():
    assert resolve_resume_completed(n_trials_completed=None, trial_log_len=37) == 37


def test_an_empty_log_with_a_counter_starts_over():
    """No evidence at all -> re-run rather than skip on an unverifiable count."""
    assert resolve_resume_completed(n_trials_completed=80, trial_log_len=0) == 0


def test_negative_or_garbage_is_clamped():
    assert resolve_resume_completed(n_trials_completed=-5, trial_log_len=0) == 0
    assert resolve_resume_completed(n_trials_completed="x", trial_log_len=3) == 3
