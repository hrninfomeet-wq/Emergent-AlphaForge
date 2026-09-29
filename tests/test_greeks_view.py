"""Behaviour of frontend/src/lib/greeksView.js, EXECUTED through node.

The Greeks card said "No open live positions." for an empty guard registry AND for
any failed read — and `Number(null)` rendered a missing figure as a calm ₹0. It may
claim "no positions" only when the BROKER's book was read and is empty.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess

import pytest

_LIB = os.path.join(os.path.dirname(__file__), "..", "frontend", "src", "lib", "greeksView.js")

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node required")


def _run_js(body: str):
    url = "file:///" + os.path.abspath(_LIB).replace("\\", "/")
    source = (
        f"import * as M from {url!r};\n"
        f"const out = await (async () => {{ {body} }})();\n"
        "process.stdout.write(JSON.stringify(out === undefined ? null : out));\n"
    )
    proc = subprocess.run(["node", "--input-type=module", "-e", source],
                          capture_output=True, text=True, timeout=60)
    if proc.returncode != 0:
        raise AssertionError(f"node failed:\n{proc.stderr}")
    return json.loads(proc.stdout)


def _view(greeks, poll_error="undefined"):
    return _run_js(f"return M.greeksView({greeks}, {poll_error});")


def _texts(view):
    return " | ".join(n["text"] for n in view["notes"])


FLAT = ("{net_delta_rupees_per_point: 0, net_theta_rupees_per_day: 0, n_computed: 0, n_skipped: 0,"
        " book: {state: 'flat', open_count: 0, guarded_count: 0, unguarded: [], error: null}}")


def test_only_a_read_and_empty_broker_book_may_say_no_positions():
    v = _view(FLAT)
    assert v["state"] == "flat"
    assert v["netDelta"] == 0 and v["netTheta"] == 0
    assert "No open positions" in _texts(v) and "broker book was read" in _texts(v)


def test_an_unreadable_book_never_says_no_positions_and_shows_dashes():
    """THE defect: expired session / registry empty -> 'No open live positions.'"""
    v = _view("{net_delta_rupees_per_point: null, net_theta_rupees_per_day: null,"
              " n_computed: 0, n_skipped: 0,"
              " book: {state: 'unknown', open_count: null, unguarded: [],"
              " error: 'token expired — reconnect Flattrade'}}")
    assert v["state"] == "unknown"
    assert v["netDelta"] is None and v["netTheta"] is None
    t = _texts(v)
    assert "No open positions" not in t and "empty" not in t
    assert "token expired" in t and "cannot be stated" in t
    assert v["notes"][0]["tone"] == "warn"


def test_a_null_figure_is_a_dash_never_zero():
    """Number(null) === 0 was the old card's calm '₹0'."""
    v = _view("{net_delta_rupees_per_point: null, net_theta_rupees_per_day: null,"
              " n_computed: 0, n_skipped: 2,"
              " book: {state: 'open', open_count: 2, unguarded: [], error: null}}")
    assert v["state"] == "open"
    assert v["netDelta"] is None and v["netTheta"] is None
    assert "unknown, not zero" in _texts(v)
    assert v["priced"] == "0 of 2 priced"


def test_open_positions_outside_the_guard_are_called_out_in_danger():
    v = _view("{net_delta_rupees_per_point: 1200.5, net_theta_rupees_per_day: -800, n_computed: 2,"
              " n_skipped: 0, book: {state: 'open', open_count: 2, guarded_count: 0,"
              " unguarded: ['NIFTY25000CE', 'SENSEX80000PE'], error: null}}")
    assert v["netDelta"] == 1200.5 and v["netTheta"] == -800
    note = v["notes"][0]
    assert note["tone"] == "danger"
    assert "2 open broker positions NOT under the software guard" in note["text"]
    assert "NIFTY25000CE, SENSEX80000PE" in note["text"]


def test_a_single_unguarded_position_is_singular_and_long_lists_are_truncated():
    one = _view("{net_delta_rupees_per_point: 1, net_theta_rupees_per_day: -1, n_computed: 1,"
                " n_skipped: 0, book: {state: 'open', unguarded: ['A'], error: null}}")
    assert "1 open broker position NOT" in one["notes"][0]["text"]
    many = _view("{net_delta_rupees_per_point: 1, net_theta_rupees_per_day: -1, n_computed: 6,"
                 " n_skipped: 0, book: {state: 'open', unguarded: ['A','B','C','D','E','F'], error: null}}")
    assert "(A, B, C, D…)" in many["notes"][0]["text"]


def test_a_fully_guarded_open_book_has_no_unguarded_note():
    v = _view("{net_delta_rupees_per_point: 10, net_theta_rupees_per_day: -5, n_computed: 1,"
              " n_skipped: 0, book: {state: 'open', unguarded: [], error: null}}")
    assert v["notes"] == [] and v["priced"] == "1 of 1 priced"


def test_partially_priced_open_book_says_how_many_could_not_be_priced():
    v = _view("{net_delta_rupees_per_point: 10, net_theta_rupees_per_day: -5, n_computed: 1,"
              " n_skipped: 1, book: {state: 'open', unguarded: [], error: null}}")
    assert "1 open position could not be priced" in _texts(v)


def test_a_route_error_on_an_open_book_is_shown_verbatim():
    v = _view("{net_delta_rupees_per_point: null, net_theta_rupees_per_day: null,"
              " n_computed: 0, n_skipped: 1, error: 'greeks computation failed',"
              " book: {state: 'open', unguarded: [], error: null}}")
    assert "greeks computation failed" in _texts(v)
    assert v["netDelta"] is None


def test_unknown_book_with_guard_visible_positions_shows_partial_figures_flagged():
    v = _view("{net_delta_rupees_per_point: 50, net_theta_rupees_per_day: -20, n_computed: 1,"
              " n_skipped: 0, book: {state: 'unknown', open_count: null, unguarded: [],"
              " error: 'broker unreachable'}}")
    assert v["state"] == "unknown" and v["netDelta"] == 50 and v["netTheta"] == -20
    assert "only the positions the software guard can see" in _texts(v)
    assert "broker unreachable" in _texts(v)


def test_unknown_book_with_nothing_priced_shows_dashes_even_if_the_route_sent_numbers():
    """Zeros with n_computed 0 are 'a sum over nothing', not exposure: unknown book +
    nothing priced -> '—'."""
    v = _view("{net_delta_rupees_per_point: 0, net_theta_rupees_per_day: 0, n_computed: 0,"
              " n_skipped: 0, book: {state: 'unknown', error: 'x'}}")
    assert v["state"] == "unknown" and v["netDelta"] is None and v["netTheta"] is None


def test_unknown_book_without_a_reason_still_says_unreadable():
    v = _view("{net_delta_rupees_per_point: null, net_theta_rupees_per_day: null, n_computed: 0,"
              " n_skipped: 0, book: {state: 'unknown', error: null}}")
    assert "broker book unreadable" in _texts(v)


def test_a_backend_that_does_not_report_the_book_cannot_support_no_positions():
    """An older route shape: zeros with no `book`. Must not read as flat."""
    v = _view("{net_delta_rupees_per_point: 0, net_theta_rupees_per_day: 0, n_computed: 0,"
              " n_skipped: 0, positions: []}")
    assert v["state"] == "unknown"
    assert v["netDelta"] is None and v["netTheta"] is None
    assert "not reported" in _texts(v) and "No open positions" not in _texts(v)


def test_an_unrecognised_book_state_is_unknown():
    v = _view("{net_delta_rupees_per_point: 0, net_theta_rupees_per_day: 0, n_computed: 0,"
              " n_skipped: 0, book: {state: 'weird'}}")
    assert v["state"] == "unknown" and v["netDelta"] is None
    # ...and it takes the "not reported" path, not the "book unreadable" one: the route
    # answered, it just said something this card does not understand.
    assert "not reported" in _texts(v) and "unreadable" not in _texts(v)


def test_loading_is_dashes_without_claims():
    v = _view("null")
    assert v["state"] == "loading" and v["netDelta"] is None and v["notes"] == []


def test_a_failing_poll_before_any_data_says_unavailable():
    v = _view("null", "new Error('x')")
    assert v["state"] == "loading" and "unavailable" in _texts(v)


def test_a_failing_poll_over_old_data_flags_it_as_the_last_reading_in_every_state():
    for greeks in (FLAT,
                   "{net_delta_rupees_per_point: 1, net_theta_rupees_per_day: -1, n_computed: 1,"
                   " n_skipped: 0, book: {state: 'open', unguarded: [], error: null}}",
                   "{net_delta_rupees_per_point: null, net_theta_rupees_per_day: null, n_computed: 0,"
                   " n_skipped: 0, book: {state: 'unknown', error: 'x'}}",
                   "{net_delta_rupees_per_point: 0, n_computed: 0}"):
        v = _view(greeks, "new Error('boom')")
        assert "last reading" in _texts(v), greeks


def test_a_healthy_poll_adds_no_stale_note():
    assert "last reading" not in _texts(_view(FLAT))


def test_finite_or_null_and_tone_classes():
    out = _run_js("""
      return {
        f: [M.finiteOrNull(null), M.finiteOrNull(undefined), M.finiteOrNull(""),
            M.finiteOrNull(NaN), M.finiteOrNull("abc"), M.finiteOrNull(0),
            M.finiteOrNull("12.5"), M.finiteOrNull(-3)],
        tones: [M.noteToneClass("danger"), M.noteToneClass("warn"), M.noteToneClass("dim")],
      };
    """)
    assert out["f"] == [None, None, None, None, None, 0, 12.5, -3]
    assert out["tones"] == ["text-danger", "text-warning", "text-dimmer/70"]
