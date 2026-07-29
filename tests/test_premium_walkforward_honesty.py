"""Walk-forward must never report "no divergence" when it measured nothing.

Found auditing the user's 2026-07-29 run. Their Walk-Forward Split Check showed:

    folds: 3, every fold trade_count 0 (IS and OOS)
    stitched_oos_trade_count: 0
    is_vs_oos: {avg_is_win_rate: 0, avg_oos_win_rate: 0, divergence_warning: FALSE}

`walk_forward` calls `run_backtest`, i.e. the SPOT path — and a premium-native
strategy's `evaluate()` is a deliberate inert stub, so every fold produced zero
trades. It then computed "no divergence" because 0 does not diverge from 0, and
rendered as a PASS next to a +41.87% headline. Silence would have been safer than
a green light.

The adjacent `significance` field degrades honestly in the same situation
(`badge: INSUFFICIENT, note: "0 trades"`), which is the standard to meet.

Two things are fixed here:

1. A walk-forward that measured NO trades must say so (`measured: False`) and
   must NOT assert `divergence_warning: False`.
2. A premium-native run gets a REAL walk-forward instead. The existing one
   re-runs the SAME params on time slices — it never re-optimizes — so slicing
   the produced option trades by session is semantically equivalent, and for this
   engine it is exact: each session independently locks its strikes at
   `reference_time`, so sessions are independent and no signal straddles a fold
   boundary.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from app.walkforward import premium_walk_forward  # noqa: E402


def _trade(day, pnl_value, pnl_pts=None):
    ts = int(__import__("pandas").Timestamp(f"{day} 10:00", tz="Asia/Kolkata").timestamp() * 1000)
    return {
        "status": "PAIRED", "direction": "CE",
        "option_entry_ts": ts, "option_exit_ts": ts + 3600_000,
        "option_pnl_value": float(pnl_value),
        "option_pnl_pts": float(pnl_pts if pnl_pts is not None else pnl_value / 65.0),
    }


def _trades(n_days=30, pattern=None):
    out = []
    for i in range(n_days):
        day = f"2026-0{1 + i // 28}-{(i % 28) + 1:02d}"
        v = pattern(i) if pattern else (100.0 if i % 2 == 0 else -50.0)
        out.append(_trade(day, v))
    return out


# --------------------------------------------------------------------------- #
# the false all-clear
# --------------------------------------------------------------------------- #

def test_no_trades_is_reported_as_unmeasured_not_as_agreement():
    """THE bug. 0 vs 0 must never render as 'no divergence'."""
    wf = premium_walk_forward([], n_folds=3, train_pct=0.6)
    assert wf["measured"] is False
    assert wf["is_vs_oos"].get("divergence_warning") is not False, (
        "a walk-forward that measured nothing must not claim agreement")


def test_no_trades_carries_a_human_reason():
    wf = premium_walk_forward([], n_folds=3, train_pct=0.6)
    assert wf.get("note"), "the UI needs something to show instead of a green tick"
    assert "0" in wf["note"] or "no" in wf["note"].lower()


def test_too_few_trades_to_split_is_also_unmeasured():
    """Three trades cannot populate three train/test folds honestly."""
    wf = premium_walk_forward(_trades(3), n_folds=3, train_pct=0.6)
    assert wf["measured"] is False


# --------------------------------------------------------------------------- #
# a real premium walk-forward
# --------------------------------------------------------------------------- #

def test_a_premium_run_actually_gets_measured():
    wf = premium_walk_forward(_trades(30), n_folds=3, train_pct=0.6)
    assert wf["measured"] is True
    assert wf["is_vs_oos"]["fold_count"] >= 1
    assert wf["stitched_oos_trade_count"] > 0, "OOS trades must actually be counted"


def test_every_fold_reports_nonzero_trades():
    wf = premium_walk_forward(_trades(30), n_folds=3, train_pct=0.6)
    for f in wf["folds"]:
        assert f["is_metrics"]["trade_count"] > 0
        assert f["oos_metrics"]["trade_count"] > 0


def test_folds_do_not_overlap_and_train_precedes_test():
    """A leak here would make OOS meaningless."""
    wf = premium_walk_forward(_trades(30), n_folds=3, train_pct=0.6)
    for f in wf["folds"]:
        assert f["train_range"][1] <= f["test_range"][0]


def test_sessions_are_never_split_across_train_and_test():
    """A session locks strikes once at reference_time; cutting one in half would
    put the same session's decision on both sides of the boundary."""
    wf = premium_walk_forward(_trades(30), n_folds=3, train_pct=0.6)
    for f in wf["folds"]:
        train_days = set(f["train_sessions"])
        test_days = set(f["test_sessions"])
        assert not (train_days & test_days)


def test_a_genuinely_degrading_strategy_raises_the_divergence_warning():
    """Good early, bad late — the signal the panel exists to give."""
    wf = premium_walk_forward(
        _trades(30, pattern=lambda i: 100.0 if i < 15 else -100.0),
        n_folds=3, train_pct=0.6)
    assert wf["measured"] is True
    assert wf["is_vs_oos"]["divergence_warning"] is True


def test_a_stable_strategy_does_not_raise_it():
    wf = premium_walk_forward(
        _trades(30, pattern=lambda i: 100.0 if i % 2 == 0 else -50.0),
        n_folds=3, train_pct=0.6)
    assert wf["is_vs_oos"]["divergence_warning"] is False


def test_win_rate_is_computed_on_rupee_pnl():
    """Premium runs are rupee-native; a points-based win rate would be a second
    units lie."""
    wf = premium_walk_forward(
        _trades(30, pattern=lambda i: 10.0 if i % 4 else -10.0), n_folds=3, train_pct=0.6)
    for f in wf["folds"]:
        assert 0.0 <= f["oos_metrics"]["win_rate"] <= 100.0


def test_unpaired_trades_are_ignored():
    rows = _trades(30) + [{"status": "UNPAIRED"}]
    wf = premium_walk_forward(rows, n_folds=3, train_pct=0.6)
    assert wf["measured"] is True


def test_the_shape_matches_the_spot_walk_forward_so_the_UI_needs_no_special_case():
    wf = premium_walk_forward(_trades(30), n_folds=3, train_pct=0.6)
    for key in ("folds", "is_vs_oos", "stitched_oos_equity", "stitched_oos_trade_count"):
        assert key in wf
    iv = wf["is_vs_oos"]
    for key in ("avg_is_win_rate", "avg_oos_win_rate", "avg_is_profit_factor",
                "avg_oos_profit_factor", "divergence_warning", "fold_count"):
        assert key in iv
