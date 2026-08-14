"""A 'breakeven' exit must break even on the price the account actually paid.

Every trailing mode in live_sl_monitor derives its level from `state["entry"]`,
and the arm site seeds that from `ref_ltp` — the pre-trade REFERENCE, because
OrderResult carries no avgprc and the fill is not knowable at registration.

On 2026-08-14 the reference was 61.35 and the fill 61.70. A `breakeven` exit would
have moved the stop to 61.35 and booked a GUARANTEED loss of 0.35 x 65 = Rs 22.75.
That is qualitatively worse than the 50% catastrophe stop being 0.5% off: a bounded
sizing error is a rounding issue, but a "breakeven" stop set to a price the account
never paid violates the exit's entire contract. Same for `lock` (entry + profit)
and `lock_trail`.

Re-basing is SAFE here precisely because `_raise_stop` is a monotonic ratchet:
  * fill ABOVE reference -> levels rise -> ratchet accepts -> strictly more protective
  * fill BELOW reference -> levels fall -> ratchet REJECTS -> stop stays put
so the correction can never loosen a live stop. (The already-resolved
`stop_level`/`initial_stop` are deliberately left alone — re-pricing those directly
is what could loosen protection, and the ratchet does not guard a direct write.)
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from app.live.live_sl_monitor import build_monitor_state, evaluate_exit  # noqa: E402

REF, FILL, QTY = 61.35, 61.70, 65


def _state(mode, **trail):
    t = {"mode": mode}
    t.update(trail)
    return build_monitor_state(REF, stop_pct=50, trail=t)


def test_breakeven_on_the_reference_books_a_real_loss():
    """Documents the defect's cost, so nobody 'simplifies' the fix away."""
    assert round((REF - FILL) * QTY, 2) == -22.75


def test_rebasing_entry_moves_breakeven_to_the_true_fill():
    st = _state("breakeven", trigger=63.0)
    st["entry"] = FILL                      # what the guard does once it sees the fill
    out = evaluate_exit(st, 63.5)           # trigger crossed -> breakeven engages
    assert out["state"]["stop_level"] == FILL, (
        "a 'breakeven' exit still anchors to a price the account never paid")


def test_lock_profit_is_measured_from_the_fill():
    st = _state("lock", trigger=63.0, lock_profit=1.0)
    st["entry"] = FILL
    out = evaluate_exit(st, 63.5)
    assert out["state"]["stop_level"] == round(FILL + 1.0, 2)


def test_the_ratchet_refuses_a_correction_that_would_LOWER_the_stop():
    """The safety property the whole fix rests on: a fill BELOW the reference
    must never drag a live stop down."""
    st = _state("breakeven", trigger=63.0)
    st["stop_level"] = 61.50                # already ratcheted up
    st["entry"] = 60.00                     # a lower 'fill'
    out = evaluate_exit(st, 63.5)
    assert out["state"]["stop_level"] == 61.50, "the ratchet let a stop be lowered"


def test_an_unchanged_entry_behaves_exactly_as_before():
    """No over-reach: without a correction nothing moves."""
    st = _state("breakeven", trigger=63.0)
    out = evaluate_exit(st, 63.5)
    assert out["state"]["stop_level"] == REF


# --- the guard must actually apply the correction ---------------------------

def test_the_guard_rebases_entry_when_it_observes_the_fill():
    """BEHAVIOURAL, not a source grep.

    Two earlier versions of this assertion searched the source text and failed
    against a CORRECT implementation that binds the state to a local first — the
    same false positive as grepping a docstring. Drive the real cycle instead.
    """
    import sys as _sys, os as _os
    _sys.path.insert(0, _os.path.dirname(__file__))
    from test_guard_marks_positions import _guard, _registered, _pos  # noqa: E402
    from app.live.live_position_guard import LiveMonitorRegistry  # noqa: E402
    from test_live_position_guard import run  # noqa: E402

    reg = LiveMonitorRegistry()
    _registered(reg)                               # registers with entry_price 250.0
    before = reg.snapshot()[0]["state"]["entry"]
    p = _pos(); p["daybuyavgprc"] = "251.5"        # the broker's true buy average
    run(_guard(reg, __import__("test_guard_marks_positions")._FakeClient([p]), [])._cycle())
    after = reg.snapshot()[0]["state"]["entry"]
    assert before == 250.0
    assert after == 251.5, (
        "the guard recorded the observed fill but never re-anchored the trailing "
        "basis to it, so 'breakeven' stays a promise it cannot keep")
