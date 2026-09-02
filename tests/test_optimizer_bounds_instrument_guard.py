"""Point-denominated search bounds must not cross instruments unannounced.

The 2026-09-01 `explosive_reversal` jobs carried IDENTICAL `param_overrides`
(`spot_target_pts` max 200, `spot_stop_pts` max 80) on NIFTY and on SENSEX.
Both indices run at the same RELATIVE volatility (median 1m true range 0.0320%
vs 0.0328% of index) but SENSEX sits at ~3.2x the point scale, so the same box
is ~3.2x tighter there. NIFTY found a +Rs 514,052 optimum inside it; SENSEX
could not reach a profitable geometry at all and returned -Rs 932,976 with a
13.4% win rate, every one of its 50 re-ranked candidates negative.

Nothing on screen said so. Switching the instrument only regenerates the run
NAME (`Optimizer.jsx`); `param_overrides` carry over silently, and the existing
bounds audit only reports overrides belonging to a DIFFERENT STRATEGY.

These tests execute the real frontend helper in Node — source-string assertions
cannot prove the guard's behaviour.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess

import pytest

_LIB = os.path.join(
    os.path.dirname(__file__), "..", "frontend", "src", "lib", "instrumentBounds.js",
)

pytestmark = pytest.mark.skipif(
    shutil.which("node") is None,
    reason="node is required to execute the real frontend module",
)


def _run_js(body: str):
    url = "file:///" + os.path.abspath(_LIB).replace("\\", "/")
    src = (
        f"import * as M from {url!r};\n"
        f"const out = (() => {{ {body} }})();\n"
        "process.stdout.write(JSON.stringify(out));\n"
    )
    proc = subprocess.run(
        ["node", "--input-type=module", "-e", src],
        capture_output=True, text=True, timeout=60,
    )
    if proc.returncode != 0:
        raise AssertionError(f"node failed:\n{proc.stderr}")
    return json.loads(proc.stdout)


SCHEMA = {
    "sr_lookback": {"type": "int", "min": 20, "max": 120, "default": 60},
    "signal_threshold": {"type": "int", "min": 30, "max": 90, "default": 50},
    "spot_target_pts": {"type": "float", "min": 5, "max": 200, "default": 40},
    "spot_stop_pts": {"type": "float", "min": 3, "max": 100, "default": 18},
}
# The overrides the two real jobs actually ran under.
REAL_OVERRIDES = {"spot_target_pts": {"max": 200}, "spot_stop_pts": {"max": 80}}


def _audit(overrides, authored_on, instrument, schema=None):
    return _run_js(
        f"return M.auditInstrumentScale({{"
        f"  overrides: {json.dumps(overrides)},"
        f"  authoredOn: {json.dumps(authored_on)},"
        f"  instrument: {json.dumps(instrument)},"
        f"  parameterSchema: {json.dumps(schema if schema is not None else SCHEMA)},"
        f"}});"
    )


# --- the guard must fire on the exact case that caused the loss --------------

def test_nifty_bounds_carried_to_sensex_are_flagged():
    a = _audit(REAL_OVERRIDES, "NIFTY", "SENSEX")
    assert a["mismatch"] is True
    assert a["fromInstrument"] == "NIFTY"
    assert a["toInstrument"] == "SENSEX"
    # SENSEX ~80,200 / NIFTY ~24,500 -> ~3.2x
    assert 3.0 <= a["ratio"] <= 3.5
    assert sorted(p["name"] for p in a["params"]) == ["spot_stop_pts", "spot_target_pts"]


def test_flagged_params_carry_a_scaled_suggestion():
    """The operator needs the number, not just the warning."""
    a = _audit(REAL_OVERRIDES, "NIFTY", "SENSEX")
    by = {p["name"]: p for p in a["params"]}
    # 200 pts on NIFTY is 0.816% of index; the same fraction of SENSEX is ~655.
    assert 600 <= by["spot_target_pts"]["suggestedMax"] <= 700
    # 80 pts on NIFTY is 0.327%; on SENSEX that is ~262 — far outside the box,
    # which is precisely why the SENSEX search could not reach it.
    assert 240 <= by["spot_stop_pts"]["suggestedMax"] <= 290
    assert by["spot_stop_pts"]["max"] == 80


def test_direction_reverses_when_going_sensex_to_nifty():
    a = _audit({"spot_stop_pts": {"max": 260}}, "SENSEX", "NIFTY")
    assert a["mismatch"] is True
    assert 0.28 <= a["ratio"] <= 0.34
    assert a["params"][0]["suggestedMax"] == pytest.approx(79, abs=12)


# --- and must stay silent everywhere else -----------------------------------

def test_same_instrument_is_not_flagged():
    assert _audit(REAL_OVERRIDES, "NIFTY", "NIFTY")["mismatch"] is False


def test_no_overrides_is_not_flagged():
    assert _audit({}, "NIFTY", "SENSEX")["mismatch"] is False


def test_unauthored_overrides_are_not_flagged():
    """A config with no recorded authoring instrument (every job saved before
    this guard existed) must not spray warnings on load."""
    assert _audit(REAL_OVERRIDES, None, "SENSEX")["mismatch"] is False


def test_non_point_params_are_never_flagged():
    """Lookbacks, thresholds and counts are scale-free — flagging them would
    train the operator to dismiss the warning."""
    a = _audit({"sr_lookback": {"max": 90}, "signal_threshold": {"min": 40}},
               "NIFTY", "SENSEX")
    assert a["mismatch"] is False


def test_point_params_are_flagged_but_scale_free_neighbours_are_not():
    a = _audit({"sr_lookback": {"max": 90}, "spot_stop_pts": {"max": 80}},
               "NIFTY", "SENSEX")
    assert a["mismatch"] is True
    assert [p["name"] for p in a["params"]] == ["spot_stop_pts"]


def test_override_with_neither_min_nor_max_is_inert():
    """`{}` is what the UI leaves behind when a field is cleared."""
    assert _audit({"spot_stop_pts": {}}, "NIFTY", "SENSEX")["mismatch"] is False


def test_param_absent_from_the_schema_is_not_flagged():
    """Foreign overrides cannot bite the search, so they are the existing
    audit's business, not this one's."""
    a = _audit({"atr_stop_pts": {"max": 50}}, "NIFTY", "SENSEX",
               schema={"sr_lookback": SCHEMA["sr_lookback"]})
    assert a["mismatch"] is False


def test_unknown_instrument_warns_without_inventing_a_ratio():
    """Degrade loudly, not silently: the carry-over is still real."""
    a = _audit(REAL_OVERRIDES, "NIFTY", "MIDCPNIFTY")
    assert a["mismatch"] is True
    assert a["ratio"] is None
    assert all(p["suggestedMax"] is None for p in a["params"])


def test_equal_scale_instruments_do_not_warn():
    """Guard on SCALE, not on the name — an instrument at the same level needs
    no rescale even though the name changed."""
    a = _run_js(
        "return M.auditInstrumentScale({"
        f"  overrides: {json.dumps(REAL_OVERRIDES)},"
        "   authoredOn: 'NIFTY', instrument: 'CLONE_OF_NIFTY',"
        f"  parameterSchema: {json.dumps(SCHEMA)},"
        "   referenceLevels: { NIFTY: 24500, CLONE_OF_NIFTY: 24500 },"
        "});"
    )
    assert a["mismatch"] is False


# --- the point-denomination predicate itself --------------------------------

@pytest.mark.parametrize("name,expected", [
    ("spot_target_pts", True),
    ("spot_stop_pts", True),
    ("option_target_pts", True),
    ("sr_lookback", False),
    ("signal_threshold", False),
    ("displacement_atr_mult", False),   # ATR multiples already scale
    ("spot_stop_atr", False),
    ("round_step_pct", False),          # percentages already scale
    ("cooldown_bars", False),
])
def test_point_denominated_predicate(name, expected):
    assert _run_js(f"return M.isPointDenominatedParam({name!r});") is expected


# --- pct_of_index preview ---------------------------------------------------
# The Advanced panel shows the operator what a percentage will become in points
# BEFORE they launch. It is deliberately approximate (a static reference level);
# the job itself converts against the run window's real median close. The UI must
# therefore label it as approximate — but it must never be WILDLY wrong, or it
# stops being a check on a typo.

def _preview(pct, instrument):
    # json.dumps, not repr: Python's None/True are not valid JavaScript.
    return _run_js(
        f"return M.pctToPointsPreview({json.dumps(pct)}, {json.dumps(instrument)});")


def test_preview_converts_percent_to_points():
    # 0.317% of ~80,200 is ~254 points — the stop NIFTY's winner implies on SENSEX.
    assert _preview(0.317, "SENSEX") == pytest.approx(254, abs=3)
    assert _preview(0.317, "NIFTY") == pytest.approx(78, abs=2)


def test_preview_tracks_the_backend_conversion():
    """Must agree with app.bounds_unit.resolve_bounds_overrides to within the
    difference between the static level and the measured median (<10%)."""
    for inst, measured in (("NIFTY", 24468.0), ("SENSEX", 80176.0)):
        backend_pts = 0.317 / 100.0 * measured
        assert abs(_preview(0.317, inst) - backend_pts) / backend_pts < 0.10


def test_preview_is_none_for_unknown_instrument():
    assert _preview(0.317, "MIDCPNIFTY") is None


@pytest.mark.parametrize("bad", [None, "", "abc", -1])
def test_preview_is_none_for_unusable_input(bad):
    assert _preview(bad, "SENSEX") is None


def test_preview_of_zero_is_zero_not_none():
    assert _preview(0, "SENSEX") == 0


def test_reference_levels_track_the_warehouse():
    """Measured medians over the warehouse: NIFTY 24,468, BANKNIFTY 55,734,
    SENSEX 80,176. The table is approximate by design, but it must not drift
    far enough to change the guidance it gives."""
    levels = _run_js("return M.REFERENCE_INDEX_LEVEL;")
    for name, measured in (("NIFTY", 24468), ("BANKNIFTY", 55734), ("SENSEX", 80176)):
        assert abs(levels[name] - measured) / measured < 0.10, name
