"""Do not fire an OCO the account provably cannot margin — but never drop a
backstop over a probe hiccup.

2026-08-14: the resting OCO was ACCEPTED (al_id 26081400000991) and then rejected
asynchronously with

    RED:Margin Shortfall:INR 101830.33 Available:INR 86932.68

A **resting** NRML sell is margined as a potential naked short — the broker cannot
know the long will still exist when the trigger fires — so protecting a Rs 4,010
option position demanded Rs 1,01,830 of span margin. On this account that is
structural, not a tuning problem: EVERY live entry places an OCO, has it accepted,
has it rejected seconds later, and gets cleaned up by the supervisor tick. The
entry path already asks GetOrderMargin first (executor.py); the arm path never did.

The DIRECTION of every fallback is the point, and it is the opposite of the entry
gate's. On the entry path an unreadable probe fails CLOSED — refuse to trade. Here
the position is already filled and guarded in software; "closed" would mean
discarding a catastrophe net because a probe was unreadable. So this skips ONLY on
an affirmative, readable shortfall, and attempts the OCO in every other case —
exactly today's behaviour, minus the one call that is provably doomed.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from app.live.margin import oco_margin_skip_reason  # noqa: E402


def test_the_real_2026_08_14_shortfall_is_skipped():
    r = oco_margin_skip_reason({"stat": "Ok", "cash": "86932.68", "marginused": "101830.33"})
    assert r is not None
    assert "101830.33" in r and "86932.68" in r


def test_sufficient_margin_proceeds():
    assert oco_margin_skip_reason({"stat": "Ok", "cash": "200000", "marginused": "101830.33"}) is None


def test_exactly_enough_proceeds():
    assert oco_margin_skip_reason({"stat": "Ok", "cash": "100", "marginused": "100"}) is None


def test_an_unavailable_probe_still_attempts_the_oco():
    """Opposite of the entry gate. The position is already filled and guarded in
    software; refusing to try a catastrophe net because a probe returned nothing
    would remove protection to punish a transport error."""
    for resp in ({}, None, "garbage", []):
        assert oco_margin_skip_reason(resp) is None, resp


def test_a_rejected_probe_still_attempts_the_oco():
    """A Not_Ok probe is not evidence of a shortfall — let the real call rule."""
    assert oco_margin_skip_reason({"stat": "Not_Ok", "emsg": "Invalid Input"}) is None


def test_unreadable_numbers_still_attempt_the_oco():
    for bad in ({"stat": "Ok", "cash": "x", "marginused": "1"},
                {"stat": "Ok", "cash": "1"},
                {"stat": "Ok", "cash": "nan", "marginused": "1"},
                {"stat": "Ok", "cash": "-5", "marginused": "1"}):
        assert oco_margin_skip_reason(bad) is None, bad


def test_the_reason_names_the_cause_for_the_journal():
    r = oco_margin_skip_reason({"stat": "Ok", "cash": "1", "marginused": "2"})
    assert "margin" in r.lower()
