"""Show the operator, BEFORE arming, the exits that will actually be in force.

Audit finding [17] (HIGH). On 2026-08-14 deployment 314fb7e8 went live carrying
`stop = the 50% deep default`, `target = None`, and a `trail` the live path then
silently discarded — three facts, none of which appeared on any surface. The plan
is fully computable from the deployment doc plus the strategy's risk_hints BEFORE
a single rupee is committed, and nothing computed it.

The preview is DISPLAY-ONLY. It must never block arming: the standing project rule
is that live-arming gates are removed, not added. Every notice it emits is advisory.

The load-bearing property is that it is computed by the SAME `resolve_live_exit_plan`
the arm path calls, not a parallel reimplementation. A second copy of the precedence
rules would drift, and a preview that drifts is worse than no preview — it would have
shown a confident, wrong answer on exactly the trade that motivated it.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from app.live_exit_preview import describe_live_exits  # noqa: E402

# The REAL 2026-08-14 deployment risk block (314fb7e8 / New_Confluence_NIFTY).
REAL_RISK = {
    "exit_controls": {"enabled": True, "unit": "pts",
                      "breakeven": {"trigger": 10, "lock": 8}, "trailing": None},
    "daily_caps": None, "default_lots": 10, "auto_paper": True,
}
# The REAL risk_hints its signals carried all day.
REAL_HINTS = {"target_pct": None, "stop_pct": None,
              "spot_target_pts": 161.35554902405147,
              "spot_stop_pts": 96.29802628652973,
              "time_stop_minutes": None}


def _ids(preview):
    return {n["id"] for n in preview["notices"]}


# --------------------------------------------------------------------------
# The 2026-08-14 case — what the operator should have been shown
# --------------------------------------------------------------------------

def test_the_deep_default_stop_is_named_as_such():
    p = describe_live_exits({"risk": REAL_RISK}, REAL_HINTS)
    assert p["premium_stop"]["pct"] == 50.0
    assert p["premium_stop"]["is_deep_default"] is True
    assert p["premium_stop"]["source"] == "deep_default"
    assert "deep_default_stop" in _ids(p)


def test_the_absent_target_is_surfaced():
    p = describe_live_exits({"risk": REAL_RISK}, REAL_HINTS)
    assert p["premium_target"]["present"] is False
    assert p["premium_target"]["pct"] is None
    assert "no_premium_target" in _ids(p)


def test_the_trail_overlay_is_described_in_human_terms():
    p = describe_live_exits({"risk": REAL_RISK}, REAL_HINTS)
    t = p["trail"]
    assert t["present"] is True
    assert t["unit"] == "pts"
    assert t["breakeven"] == {"trigger": 10.0, "lock": 8.0}
    assert "+10" in t["summary"] and "+8" in t["summary"]


def test_the_spot_mirror_levels_are_surfaced():
    p = describe_live_exits({"risk": REAL_RISK}, REAL_HINTS)
    s = p["spot_exit"]
    assert s["present"] is True
    assert s["target_pts"] == 161.35554902405147
    assert s["stop_pts"] == 96.29802628652973


def test_the_eod_square_is_always_stated():
    p = describe_live_exits({"risk": REAL_RISK}, REAL_HINTS)
    assert p["eod_square_ist"] == "15:00"


# --------------------------------------------------------------------------
# Source attribution — where each level actually came from
# --------------------------------------------------------------------------

def test_a_strategy_hint_outranks_the_deployment_config():
    """auto_live.py:198-213 — hints win. The preview must say so, because an
    operator reading only the deployment doc would predict the wrong number."""
    risk = {"auto_paper_stop_pct": 18, "auto_paper_target_pct": 28}
    hints = {"stop_pct": 40, "target_pct": 80}
    p = describe_live_exits({"risk": risk}, hints)
    assert p["premium_stop"]["pct"] == 40 and p["premium_stop"]["source"] == "strategy_hint"
    assert p["premium_target"]["pct"] == 80 and p["premium_target"]["source"] == "strategy_hint"
    assert p["premium_stop"]["is_deep_default"] is False


def test_deployment_pct_is_used_when_no_hint():
    risk = {"auto_paper_stop_pct": 18, "auto_paper_target_pct": 28}
    p = describe_live_exits({"risk": risk}, {})
    assert p["premium_stop"]["pct"] == 18
    assert p["premium_stop"]["source"] == "deployment_pct"
    assert p["premium_target"]["pct"] == 28
    assert p["premium_target"]["present"] is True
    assert "no_premium_target" not in _ids(p)
    assert "deep_default_stop" not in _ids(p)


def test_deployment_pts_outranks_deployment_pct():
    """resolve_live_exit_plan checks _pts before _pct on each leg."""
    risk = {"auto_paper_stop_pts": 12, "auto_paper_stop_pct": 18,
            "auto_paper_target_pts": 30, "auto_paper_target_pct": 28}
    p = describe_live_exits({"risk": risk}, {})
    assert p["premium_stop"]["pts"] == 12 and p["premium_stop"]["pct"] is None
    assert p["premium_stop"]["source"] == "deployment_pts"
    assert p["premium_target"]["pts"] == 30


# --------------------------------------------------------------------------
# It must agree with the arm path, always
# --------------------------------------------------------------------------

def test_the_preview_matches_what_the_arm_path_resolves():
    """The anti-drift property. Same inputs -> same numbers as the real resolver."""
    from app.auto_live import resolve_live_exit_plan
    for risk, hints in (
        (REAL_RISK, REAL_HINTS),
        ({"auto_paper_stop_pct": 18, "auto_paper_target_pct": 28}, {}),
        ({"auto_paper_stop_pts": 12}, {"target_pct": 55}),
        ({}, {}),
    ):
        dep = {"risk": risk}
        lv = resolve_live_exit_plan({"risk_hints": hints}, dep)["levels"]
        p = describe_live_exits(dep, hints)
        assert p["premium_stop"]["pct"] == lv["stop_pct"], (risk, hints)
        assert p["premium_stop"]["pts"] == lv["stop_pts"], (risk, hints)
        assert p["premium_target"]["pct"] == lv["target_pct"], (risk, hints)
        assert p["premium_target"]["pts"] == lv["target_pts"], (risk, hints)


def test_an_empty_deployment_still_gets_the_protective_floor():
    """A deployment with nothing configured is exactly the dangerous case."""
    p = describe_live_exits({"risk": {}}, {})
    assert p["premium_stop"]["pct"] == 50.0
    assert p["premium_stop"]["is_deep_default"] is True
    assert {"deep_default_stop", "no_premium_target"} <= _ids(p)


# --------------------------------------------------------------------------
# Degenerate inputs must not raise — this renders inside an arm dialog
# --------------------------------------------------------------------------

def test_missing_and_junk_inputs_never_raise():
    for dep in ({}, {"risk": None}, {"risk": {}}, None):
        for hints in ({}, None, {"stop_pct": None}):
            p = describe_live_exits(dep, hints)
            assert p["premium_stop"]["pct"] == 50.0
            assert isinstance(p["notices"], list)


def test_an_inert_trail_is_reported_absent_not_present():
    """enabled:true with every overlay zeroed arms nothing — say so honestly."""
    risk = {"exit_controls": {"enabled": True, "unit": "pts",
                              "breakeven": {"trigger": 0, "lock": 0},
                              "trailing": {"activation": 0, "distance": 0}}}
    p = describe_live_exits({"risk": risk}, {})
    assert p["trail"]["present"] is False
    assert "no_trail" in _ids(p)


def test_a_disabled_trail_is_reported_absent():
    risk = {"exit_controls": {"enabled": False, "unit": "pts",
                              "breakeven": {"trigger": 10, "lock": 8}}}
    p = describe_live_exits({"risk": risk}, {})
    assert p["trail"]["present"] is False


def test_pct_trail_is_rendered_as_a_percentage_not_a_fraction():
    """exit_controls unit 'pct' is a FRACTION (e * (1 + t)). 0.2 must read as 20%,
    never as 0.2%. The SENSEX deployments ship exactly this shape."""
    risk = {"exit_controls": {"enabled": True, "unit": "pct",
                              "breakeven": {"trigger": 0.2, "lock": 0.05},
                              "trailing": None}}
    p = describe_live_exits({"risk": risk}, {})
    assert p["trail"]["present"] is True
    assert "20%" in p["trail"]["summary"]
    assert "5%" in p["trail"]["summary"]
    assert "0.2%" not in p["trail"]["summary"]


def test_trailing_only_config_is_described():
    risk = {"exit_controls": {"enabled": True, "unit": "pts", "breakeven": None,
                              "trailing": {"activation": 20, "distance": 5}}}
    p = describe_live_exits({"risk": risk}, {})
    assert p["trail"]["present"] is True
    assert p["trail"]["trailing"] == {"activation": 20.0, "distance": 5.0}
    assert "trail" in p["trail"]["summary"].lower()


# --------------------------------------------------------------------------
# Absolute levels, when a reference premium is known
# --------------------------------------------------------------------------

def test_absolute_levels_are_computed_when_a_reference_premium_is_given():
    p = describe_live_exits({"risk": REAL_RISK}, REAL_HINTS, ref_premium=61.70)
    assert p["premium_stop"]["level"] == 30.85          # 61.70 * 0.50
    assert p["premium_target"]["level"] is None
    assert p["reference_premium"] == 61.70


def test_levels_are_none_without_a_reference_premium():
    """At dialog time there is no live premium yet — say nothing rather than guess."""
    p = describe_live_exits({"risk": REAL_RISK}, REAL_HINTS)
    assert p["premium_stop"]["level"] is None
    assert p["reference_premium"] is None


def test_a_junk_reference_premium_degrades_to_no_levels():
    for bad in (0, -5, "x", float("nan"), float("inf")):
        p = describe_live_exits({"risk": REAL_RISK}, REAL_HINTS, ref_premium=bad)
        assert p["premium_stop"]["level"] is None, bad


# --------------------------------------------------------------------------
# It is advisory, never a gate
# --------------------------------------------------------------------------

def test_every_notice_is_advisory_severity():
    """No notice may carry a blocking severity — arming is the operator's call."""
    for risk in ({}, REAL_RISK, {"auto_paper_stop_pct": 18}):
        p = describe_live_exits({"risk": risk}, {})
        for n in p["notices"]:
            assert n["severity"] in ("info", "warn"), n
            assert set(n) == {"id", "severity", "message"}


def test_hints_unknown_is_flagged_when_no_signal_was_observed():
    """Hints outrank config, so a preview built without them is provisional."""
    p = describe_live_exits({"risk": REAL_RISK}, None)
    assert "hints_unobserved" in _ids(p)
    p2 = describe_live_exits({"risk": REAL_RISK}, REAL_HINTS)
    assert "hints_unobserved" not in _ids(p2)


# --------------------------------------------------------------------------
# Anti-drift sweep — the load-bearing property, tested exhaustively
# --------------------------------------------------------------------------

def test_the_preview_never_disagrees_with_the_resolver_across_the_matrix():
    """Exhaustive: every combination of hint / deployment-pts / deployment-pct on
    both legs. If the precedence in resolve_live_exit_plan is ever changed, this
    fails loudly instead of the dialog quietly showing a number the guard will not
    use — which is the exact failure mode the preview exists to prevent.
    """
    from itertools import product

    from app.auto_live import resolve_live_exit_plan

    hint_vals = (None, 40)
    pts_vals = (None, 12)
    pct_vals = (None, 18)
    checked = 0
    for (sh, sp, sc, th, tp, tc) in product(hint_vals, pts_vals, pct_vals,
                                            hint_vals, pts_vals, pct_vals):
        risk = {}
        if sp is not None:
            risk["auto_paper_stop_pts"] = sp
        if sc is not None:
            risk["auto_paper_stop_pct"] = sc
        if tp is not None:
            risk["auto_paper_target_pts"] = tp
        if tc is not None:
            risk["auto_paper_target_pct"] = tc
        hints = {}
        if sh is not None:
            hints["stop_pct"] = sh
        if th is not None:
            hints["target_pct"] = th

        dep = {"risk": risk}
        lv = resolve_live_exit_plan({"risk_hints": hints}, dep)["levels"]
        p = describe_live_exits(dep, hints)
        ctx = (risk, hints)
        assert p["premium_stop"]["pct"] == lv["stop_pct"], ctx
        assert p["premium_stop"]["pts"] == lv["stop_pts"], ctx
        assert p["premium_target"]["pct"] == lv["target_pct"], ctx
        assert p["premium_target"]["pts"] == lv["target_pts"], ctx
        # The label must agree with the value it claims to explain.
        src = p["premium_stop"]["source"]
        if src == "strategy_hint":
            assert lv["stop_pct"] == sh, ctx
        elif src == "deployment_pts":
            assert lv["stop_pts"] == sp, ctx
        elif src == "deployment_pct":
            assert lv["stop_pct"] == sc, ctx
        elif src == "deep_default":
            assert p["premium_stop"]["is_deep_default"] is True, ctx
            assert (sh, sp, sc) == (None, None, None), ctx
        else:
            raise AssertionError(f"unexpected source {src!r} for {ctx}")
        checked += 1
    assert checked == 64


def test_a_changed_precedence_would_be_caught_not_papered_over():
    """If the resolver ever stopped honouring a hint, the label must degrade to
    'unknown' rather than assert a source that did not actually win."""
    import app.live_exit_preview as mod

    real = mod.resolve_live_exit_plan
    try:
        # Simulate drift: the resolver ignores everything and returns a fixed stop.
        mod.resolve_live_exit_plan = lambda sig, dep: {
            "levels": {"stop_pct": 99.0, "stop_pts": None,
                       "target_pct": None, "target_pts": None, "trail": None},
            "spot_exit": None, "time_stop_minutes": None}
        p = describe_live_exits({"risk": {}}, {})
        assert p["premium_stop"]["pct"] == 99.0
        assert p["premium_stop"]["source"] == "unknown"
        assert p["premium_stop"]["is_deep_default"] is False
        assert "deep_default_stop" not in {n["id"] for n in p["notices"]}
    finally:
        mod.resolve_live_exit_plan = real
