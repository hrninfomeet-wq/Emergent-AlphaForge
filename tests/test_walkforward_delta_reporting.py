"""Walk-forward must report the DECAY, not just a pass/fail boolean.

Twice in two days a real signal was hidden by a threshold:
  * Round 4 (holdout study): IS 53.00% -> OOS 43.56%, a 9.44-point decay.
  * 2026-07-30 audit:        IS 48.44% -> OOS 38.78%, a 9.65-point decay.
Both rendered `divergence_warning: False` because the premium cut is a hardcoded
10 points — and both strategies subsequently failed out of sample.

Also found: the two engines disagree on the rule itself.
  * spot    `walkforward.py`: `abs(avg_is_wr - avg_oos_wr) > 15`  (two-sided, 15)
  * premium: `(avg_is_wr - avg_oos_wr) >= 10.0`                   (one-sided, 10)
So the same decay is a warning in one family and a pass in the other.

This does NOT silently redefine either threshold — changing what "pass" means
would invalidate every saved run's interpretation. Instead both paths now expose
the SIGNED delta (positive = OOS worse = the overfitting direction) and a soft
caution band, so a 9.65-point decay can never again read as a clean pass.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import pandas as pd  # noqa: E402

from app.walkforward import premium_walk_forward  # noqa: E402

_PANEL = os.path.join(os.path.dirname(__file__), "..", "frontend", "src", "pages",
                      "BacktestLab.jsx")


def _trade(day, pnl):
    ts = int(pd.Timestamp(f"{day} 10:00", tz="Asia/Kolkata").timestamp() * 1000)
    return {"status": "PAIRED", "direction": "CE",
            "option_entry_ts": ts, "option_exit_ts": ts + 3600_000,
            "option_pnl_value": float(pnl), "option_pnl_pts": float(pnl) / 65.0}


def _series(n_days, pattern):
    return [_trade(f"2026-0{1 + i // 28}-{(i % 28) + 1:02d}", pattern(i))
            for i in range(n_days)]


def _wf(pattern, n=30):
    return premium_walk_forward(_series(n, pattern), n_folds=3, train_pct=0.6)


# --------------------------------------------------------------------------- #
# the delta itself
# --------------------------------------------------------------------------- #

def test_the_signed_delta_is_reported():
    wf = _wf(lambda i: 100.0 if i % 2 == 0 else -50.0)
    assert "avg_win_rate_delta" in wf["is_vs_oos"]


def test_a_decay_is_positive_and_an_improvement_is_negative():
    """Sign convention: positive = OOS worse = the direction that matters."""
    decay = _wf(lambda i: 100.0 if i < 15 else -100.0)["is_vs_oos"]
    assert decay["avg_win_rate_delta"] > 0
    improve = _wf(lambda i: -100.0 if i < 15 else 100.0)["is_vs_oos"]
    assert improve["avg_win_rate_delta"] < 0


def test_the_delta_equals_is_minus_oos():
    iv = _wf(lambda i: 100.0 if i < 15 else -100.0)["is_vs_oos"]
    assert iv["avg_win_rate_delta"] == round(
        iv["avg_is_win_rate"] - iv["avg_oos_win_rate"], 2)


def test_an_unmeasured_walkforward_reports_no_delta_rather_than_zero():
    """Zero would read as 'perfectly stable'."""
    wf = premium_walk_forward([], n_folds=3, train_pct=0.6)
    assert wf["is_vs_oos"]["avg_win_rate_delta"] is None


# --------------------------------------------------------------------------- #
# the soft band that would have caught both misses
# --------------------------------------------------------------------------- #

def test_a_soft_caution_flag_exists():
    wf = _wf(lambda i: 100.0 if i % 2 == 0 else -50.0)
    assert "divergence_soft" in wf["is_vs_oos"]


def test_a_stable_strategy_raises_neither_flag():
    iv = _wf(lambda i: 100.0 if i % 2 == 0 else -50.0)["is_vs_oos"]
    assert iv["divergence_warning"] is False
    assert iv["divergence_soft"] is False


def test_a_clear_decay_still_raises_the_hard_warning():
    iv = _wf(lambda i: 100.0 if i < 15 else -100.0)["is_vs_oos"]
    assert iv["divergence_warning"] is True


def test_the_hard_threshold_is_unchanged():
    """Deliberately NOT retuned — that would rewrite what every saved run meant."""
    import inspect
    from app import walkforward
    src = inspect.getsource(walkforward.premium_walk_forward)
    assert ">= 10.0" in src


def test_the_soft_band_is_lower_than_the_hard_one():
    from app.walkforward import SOFT_DIVERGENCE_PTS, HARD_DIVERGENCE_PTS
    assert 0 < SOFT_DIVERGENCE_PTS < HARD_DIVERGENCE_PTS


def test_the_two_real_missed_cases_now_raise_the_soft_flag():
    """9.44 and 9.65 points — both previously reported as a clean pass."""
    from app.walkforward import SOFT_DIVERGENCE_PTS
    for delta in (9.44, 9.65):
        assert delta >= SOFT_DIVERGENCE_PTS, (
            f"a {delta}-point decay still reads as clean")


# --------------------------------------------------------------------------- #
# spot path gets the same fields, so the panel needs no special case
# --------------------------------------------------------------------------- #

def test_both_engines_use_the_one_shared_divergence_helper():
    """Asserted as delegation, not as literal strings in each body: two copies of
    this rule are exactly how the 10-vs-15 discrepancy arose."""
    import inspect
    from app import walkforward
    for fn in (walkforward.walk_forward, walkforward.premium_walk_forward):
        assert "_divergence_fields(" in inspect.getsource(fn), fn.__name__


def test_the_shared_helper_emits_every_field_the_panel_reads():
    from app.walkforward import _divergence_fields
    out = _divergence_fields(50.0, 40.0, True)
    assert out["avg_win_rate_delta"] == 10.0
    assert out["divergence_soft"] is True
    assert out["divergence_warning"] is True


def test_the_shared_helper_is_none_safe():
    from app.walkforward import _divergence_fields
    out = _divergence_fields(None, None, False)
    assert out["avg_win_rate_delta"] is None
    assert out["divergence_soft"] is None


# --------------------------------------------------------------------------- #
# the UI shows it
# --------------------------------------------------------------------------- #

def test_the_panel_displays_the_delta():
    with open(_PANEL, encoding="utf-8") as fh:
        src = fh.read()
    i = src.index("walkforward-panel")
    body = src[i:i + 4000]
    assert "avg_win_rate_delta" in body, "the delta is computed but not shown"


def test_the_panel_surfaces_the_soft_caution():
    with open(_PANEL, encoding="utf-8") as fh:
        src = fh.read()
    assert "divergence_soft" in src
