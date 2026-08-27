"""Tests for the shared entry-time window (register item #6).

The defect: `deployment_evaluator` hardcoded 09:25-14:50 as module constants
while `backtest.run_backtest` defaulted to 09:25-15:00. Three consequences, all
recorded in the deliverable §7.2:

1. Every backtest run at defaults counted signals from 14:50-15:00 that live
   would refuse — the backtest was scoring trades that cannot exist.
2. A strategy needing the opening ten minutes could not be deployed at all,
   however it backtested. `dte_opening_shock_breakout`'s own docstring had to
   warn operators to override the window by hand.
3. The live blocker message hardcoded "09:25" into its text, so the moment the
   window became configurable the reason string would lie about why a bar was
   refused.

The fix is one resolver both sides call, so a saved run and its deployment
cannot disagree. These tests pin that they share it.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.entry_window import (  # noqa: E402
    DEFAULT_ENTRY_END,
    DEFAULT_ENTRY_START,
    HARD_EARLIEST,
    HARD_LATEST,
    resolve_entry_window,
)


# ---------------------------------------------------------------------------
# Defaults are the live-effective window
# ---------------------------------------------------------------------------

def test_no_config_yields_the_live_effective_window():
    assert resolve_entry_window(None) == (DEFAULT_ENTRY_START, DEFAULT_ENTRY_END)
    assert resolve_entry_window({}) == ("09:25", "14:50")


def test_the_backtest_default_is_the_same_object_as_the_live_default():
    """The anti-drift test — the entire point of register item #6.

    `backtest.TRADE_WINDOW_END` was "15:00" while live blocked from "14:50", so
    ten minutes of every default backtest counted signals live refuses. Both
    now read the same constants, and this fails if either is edited alone.
    """
    from app import backtest
    from app import deployment_evaluator as de

    assert backtest.TRADE_WINDOW_START == DEFAULT_ENTRY_START
    assert backtest.TRADE_WINDOW_END == DEFAULT_ENTRY_END
    assert de.BLOCK_OPEN_UNTIL.strftime("%H:%M") == DEFAULT_ENTRY_START
    assert de.BLOCK_CLOSE_FROM.strftime("%H:%M") == DEFAULT_ENTRY_END


def test_every_request_model_that_carries_a_window_shares_the_default():
    """Three request models carry the window, and the API-level one used by the
    UI was still "15:00" after the function default was fixed — so a fix that
    stopped at `run_backtest` would have changed nothing a user could see."""
    from app.schemas import BacktestReq, OptimizerStartReq, WfoStartReq

    for model in (BacktestReq, OptimizerStartReq, WfoStartReq):
        fields = model.model_fields
        assert fields["trade_window_start"].default == DEFAULT_ENTRY_START, model.__name__
        assert fields["trade_window_end"].default == DEFAULT_ENTRY_END, model.__name__


def test_the_backtest_ui_does_not_post_a_window_live_would_refuse():
    """The UI hardcoded "15:00" in three places and POSTS it explicitly, which
    overrides any Python default. Grepping JSX is not a test of behaviour, but it
    IS the right check for a literal the UI sends verbatim."""
    jsx = (ROOT / "frontend/src/pages/BacktestLab.jsx").read_text(encoding="utf-8")
    assert 'trade_window_end: "15:00"' not in jsx
    assert '"15:00"' not in jsx, "a 15:00 window literal is back in the Backtest Lab"
    assert jsx.count('"14:50"') >= 3


# ---------------------------------------------------------------------------
# A deployment may narrow, and may widen only within the hard bounds
# ---------------------------------------------------------------------------

def test_a_deployment_can_narrow_the_window():
    assert resolve_entry_window(
        {"trade_window_start": "10:00", "trade_window_end": "14:00"}
    ) == ("10:00", "14:00")


def test_a_deployment_can_open_at_the_bell_which_it_previously_could_not():
    """The inflexibility half of the defect: an opening-range strategy was
    undeployable because 09:15-09:25 was blocked by a module constant."""
    assert resolve_entry_window(
        {"trade_window_start": "09:15", "trade_window_end": "14:50"}
    ) == ("09:15", "14:50")


def test_nothing_can_be_configured_before_the_session_opens():
    for early in ("00:00", "08:00", "09:14"):
        start, _ = resolve_entry_window({"trade_window_start": early})
        assert start == HARD_EARLIEST


def test_nothing_can_be_configured_past_square_off():
    """`SQUARE_OFF_AT` is 15:00. An entry at or after it would be squared off on
    the same bar, so the clamp is not a preference — it is arithmetic."""
    for late in ("15:00", "15:20", "23:59"):
        _, end = resolve_entry_window({"trade_window_end": late})
        assert end == HARD_LATEST


def test_the_hard_bounds_bracket_the_defaults():
    assert HARD_EARLIEST <= DEFAULT_ENTRY_START < DEFAULT_ENTRY_END <= HARD_LATEST


# ---------------------------------------------------------------------------
# Anything unusable falls back to the safe default — never to a wider window
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("bad", [
    {"trade_window_start": "14:00", "trade_window_end": "10:00"},   # inverted
    {"trade_window_start": "11:00", "trade_window_end": "11:00"},   # empty
])
def test_a_window_that_admits_nothing_falls_back_to_the_default(bad):
    assert resolve_entry_window(bad) == (DEFAULT_ENTRY_START, DEFAULT_ENTRY_END)


@pytest.mark.parametrize("bad", ["", "9:25", "0925", "nonsense", None, 925,
                                 "25:00", "09:60", [], {}])
def test_a_malformed_time_falls_back_rather_than_being_guessed(bad):
    """Lexicographic comparison is how the window is applied everywhere, so
    "9:25" without its leading zero would silently compare GREATER than "14:50"
    and admit nothing. Fail to the default instead of guessing."""
    start, end = resolve_entry_window({"trade_window_start": bad,
                                       "trade_window_end": bad})
    assert (start, end) == (DEFAULT_ENTRY_START, DEFAULT_ENTRY_END)


def test_one_bad_bound_does_not_discard_the_other_good_one():
    assert resolve_entry_window(
        {"trade_window_start": "10:30", "trade_window_end": "junk"}
    ) == ("10:30", DEFAULT_ENTRY_END)


def test_a_non_mapping_config_is_ignored_safely():
    for junk in ("nope", 42, [], object()):
        assert resolve_entry_window(junk) == (DEFAULT_ENTRY_START, DEFAULT_ENTRY_END)


# One guard was masking another: the parametrized test above hands the SAME bad
# value to both bounds, so a validator that let it through produced an inverted
# window and the `start >= end` fallback rescued the result. Both mutants
# ("accept a malformed time", "accept an impossible minute") survived because of
# it. These pin each bound on its own, with the other bound valid, and use values
# chosen NOT to invert — so only the validator can produce the right answer.

@pytest.mark.parametrize("bad", ["0925", "09:60"])
def test_a_malformed_START_falls_back_with_no_other_guard_to_mask_it(bad):
    assert resolve_entry_window(
        {"trade_window_start": bad, "trade_window_end": "14:50"}
    ) == (DEFAULT_ENTRY_START, "14:50")


@pytest.mark.parametrize("bad", ["1450", "14:70"])
def test_a_malformed_END_falls_back_with_no_other_guard_to_mask_it(bad):
    assert resolve_entry_window(
        {"trade_window_start": "09:25", "trade_window_end": bad}
    ) == ("09:25", DEFAULT_ENTRY_END)
