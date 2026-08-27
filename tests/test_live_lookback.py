"""Tests for the live rolling-window contract (register item #11).

Two defects, one root cause: the live window's size was decided by arithmetic
nobody could see the result of.

1. **Silent truncation.** `live_lookback = max(200, min(requested, 1_000))`. A
   strategy declaring more than 1,000 bars got 1,000 and was never told. Backtest
   sees the whole history; live sees a slice; both look healthy and compute
   different numbers. Candidate A's spec needs a 20-session baseline (~7,500
   bars) and would have hit exactly this.

2. **The insufficiency gate checked the wrong number.** It compared the loaded
   frame against a global `MIN_BARS_FOR_EVALUATION = 50`, not against what the
   STRATEGY declared it needs. Ten shipped strategies declare
   `live_lookback_bars = 400` — for the opening range, the session VWAP anchor
   and the prior session's close — and every one of them would evaluate happily
   on 50 bars, emitting a signal computed from anchors that are simply wrong.

   That is not a hypothetical: a measured session-VWAP anchor error of 2.12 ATR
   at 14:49 silently inverted nine shipped strategies (`fc424a1`), which is the
   whole reason those strategies raised their lookback in the first place. The
   gate meant to protect them was comparing against 50.

The two get DIFFERENT treatment, and the difference is the point:

* A declaration above the cap is a coding error in the strategy — no amount of
  data fixes it — so it is REFUSED outright.
* A short window is MISSING DATA, and missing data must degrade the app rather
  than disable it. Refusing was the first attempt and it broke 43 tests: any
  warehouse with less history than a strategy declares (a fresh install, a newly
  ingested instrument, an early backfill) would have stopped evaluating. The
  shortfall is now reported on the result as `window_note` and logged, while the
  evaluation still runs.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.deployment_evaluator import (  # noqa: E402
    LIVE_LOOKBACK_FLOOR,
    LIVE_LOOKBACK_MAX,
    MIN_BARS_FOR_EVALUATION,
    resolve_live_lookback,
)


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------

def test_a_normal_request_is_honoured_exactly():
    assert resolve_live_lookback(400) == (400, None)


def test_an_absent_or_unusable_request_falls_back_to_the_floor():
    for bad in (None, 0, "", "nonsense", [], {}, float("nan")):
        bars, reason = resolve_live_lookback(bad)
        assert bars == LIVE_LOOKBACK_FLOOR
        assert reason is None


def test_a_request_below_the_floor_is_raised_to_it():
    """More history than asked for is always safe — it can only add context."""
    bars, reason = resolve_live_lookback(50)
    assert bars == LIVE_LOOKBACK_FLOOR
    assert reason is None


def test_a_request_above_the_cap_is_REFUSED_not_silently_truncated():
    """The defect. Truncating to the cap and evaluating anyway is a confident
    wrong answer; refusing is an honest one."""
    bars, reason = resolve_live_lookback(LIVE_LOOKBACK_MAX + 1)
    assert reason is not None
    assert "live_window_exceeds_cap" in reason
    assert str(LIVE_LOOKBACK_MAX) in reason


def test_a_request_exactly_at_the_cap_is_allowed():
    assert resolve_live_lookback(LIVE_LOOKBACK_MAX) == (LIVE_LOOKBACK_MAX, None)


def test_the_refusal_names_both_numbers_so_the_fix_is_obvious():
    _, reason = resolve_live_lookback(7500)
    assert "7500" in reason and str(LIVE_LOOKBACK_MAX) in reason


def test_the_cap_is_above_every_shipped_strategys_declaration():
    """If a shipped strategy ever exceeds the cap it would stop evaluating, so
    this is the canary that says so at test time rather than in production."""
    from app.strategies.base import get_registry

    registry = get_registry()
    registry.auto_discover()
    for sid in registry.list_ids() if hasattr(registry, "list_ids") else []:
        strat = registry.get(sid)
        want = int(getattr(strat, "live_lookback_bars", 200) or 200)
        bars, reason = resolve_live_lookback(want)
        assert reason is None, f"{sid} declares {want} bars and cannot be evaluated"


# ---------------------------------------------------------------------------
# The insufficiency gate compares against what the STRATEGY needs
# ---------------------------------------------------------------------------

def test_the_floor_is_above_the_bare_minimum_bars_constant():
    """`MIN_BARS_FOR_EVALUATION` is a floor for indicator warm-up, not a
    substitute for the strategy's own declared need."""
    assert LIVE_LOOKBACK_FLOOR > MIN_BARS_FOR_EVALUATION


def test_required_bars_for_a_session_anchored_strategy_is_its_declaration():
    from app.deployment_evaluator import required_bars_for

    assert required_bars_for(400) == 400


def test_an_undeclared_strategy_is_held_only_to_the_indicator_warmup():
    """The LOAD floor is not a requirement. Over-fetching history is always safe,
    but holding a strategy to bars it never claimed to need would report a
    degraded window it has no opinion about."""
    from app.deployment_evaluator import required_bars_for

    assert required_bars_for(None) == MIN_BARS_FOR_EVALUATION
    assert required_bars_for(60) == 60


def test_required_bars_never_drops_below_the_indicator_warmup():
    from app.deployment_evaluator import required_bars_for

    assert required_bars_for(10) >= MIN_BARS_FOR_EVALUATION


@pytest.mark.parametrize("declared,available,ok", [
    (400, 400, True),
    (400, 399, False),     # one bar short of the anchors it needs
    (400, 50, False),      # the old gate compared against 50 and let this through
    (200, 200, True),
])
def test_a_short_window_is_DETECTED_for_the_strategy_that_declared_it(
        declared, available, ok):
    from app.deployment_evaluator import is_window_sufficient

    assert is_window_sufficient(available, declared) is ok


# The behaviour of the evaluator itself is pinned in
# `test_deployment_evaluator.py` — a source grep is not a test. Mutating the
# reporting away, and smuggling the note in as a blocker, both survived a
# grep-based version of this because the strings stayed in the file inside a
# dead branch.
