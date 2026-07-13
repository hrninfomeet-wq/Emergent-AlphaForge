"""Phase 4.1: close the remaining feasibility REJECT the user hit on the NF CE PE EXP2
blueprint. Session 1 (commit b444162) taught the classifier premium-trigger + session-
gate + Phase-5 concepts, but 3-5 rules per blueprint still fall through to R9 because
the LLM emits them as PURELY DESCRIPTIVE META/FILTER lines with empty concepts/cols:

  * "The instrument for the strategy is NIFTY 50 spot" (META/CORE) -> R9
  * "Options used are of the current weekly expiry" (FILTER/CORE) -> R9
  * "The strategy is intraday, requiring same-day exits only" (META/CORE) -> R9
  * "For the CE leg, the option type is CE" (FILTER/CORE) -> R9
  * "For the CE leg, the expiry type is weekly by default" (FILTER/CORE) -> R9

These rules describe the STRATEGY SHAPE (which is captured by the deployment's
`premium_trigger_config`), not per-bar trading logic. Two-pronged fix:

  A) The classifier learns three new concept groups (holding_period, option_kind,
     expiry_selection, instrument_selection) so if the LLM DOES emit them, they
     route correctly.
  B) The classifier grows a DESCRIPTIVE-META fallback: for kind in {META, FILTER,
     SESSION} with NO cols, NO concepts, NO ohlcv_derivable, NO barspan>2, NO
     window, NOT session_anchored — accept as declarative_config (mapped to the
     deployment / config-block layer) instead of blanket-R9-rejecting.

The fallback is CONSERVATIVE — it only fires for kinds where descriptive language
is expected (META/FILTER/SESSION). ENTRY/EXIT/GATE rules with empty tokens must
STILL reject; empty tokens on those kinds is a genuine LLM failure that should
surface, not be silently accepted.

All tests are host-safe / pure (no motor, no LLM, no network).
"""
from __future__ import annotations

import os, sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import app.features.catalog  # noqa: F401

from app.ai.capability import (
    FeasibilityClass as FC,
    RuleTokens,
    classify_rule,
    capability_summary,
)


def _T(**kw):
    base = dict(cols=frozenset(), concepts=frozenset(), barspan=1, window=0,
                session_anchored=False, ohlcv_derivable=False, kind=None)
    base.update(kw)
    base["cols"] = frozenset(base["cols"])
    base["concepts"] = frozenset(base["concepts"])
    return RuleTokens(**base)


# --------------------------------------------------------------------------
# 1. New concept groups: holding period / option kind / expiry / instrument.
# --------------------------------------------------------------------------
def test_holding_period_intraday_same_day_maps_to_deployment_layer():
    """'The strategy is intraday, same-day exit only' is a session-mode declaration.
    Belongs on the deployment (session windows + EOD square-off), not in evaluate()."""
    for c in ["intraday", "intraday_same_day", "same_day_squareoff",
              "holding_period", "session_scope", "strategy_scope"]:
        v = classify_rule(_T(concepts={c}))
        assert v.feasibility in (FC.BUILDABLE_NOW, FC.BUILDABLE_WITH_FEATURE), \
            f"'{c}' -> {v}"
        assert v.feature == "deployment_layer", f"'{c}' -> {v}"


def test_option_kind_leg_identity_maps_to_premium_trigger_config():
    """'For the CE leg, the option type is CE' / 'Lazy Leg 1 is a PE' — leg
    identity is a config knob on the premium-trigger block (side=CE|PE|BOTH),
    not a per-bar rule."""
    for c in ["option_kind", "option_type_selection", "ce_leg", "pe_leg",
              "leg_kind", "option_side", "call_side", "put_side"]:
        v = classify_rule(_T(concepts={c}))
        assert v.feasibility in (FC.BUILDABLE_NOW, FC.BUILDABLE_WITH_FEATURE), \
            f"'{c}' -> {v}"
        assert v.feature == "premium_trigger_config", f"'{c}' -> {v}"


def test_expiry_selection_maps_to_premium_trigger_config():
    """'weekly' / 'monthly' / 'expiry is weekly by default' — contract-selection
    config on the premium-trigger block, not a per-bar rule."""
    for c in ["expiry_selection", "expiry_type", "weekly_expiry",
              "monthly_expiry", "weekly", "monthly", "expiry"]:
        v = classify_rule(_T(concepts={c}))
        assert v.feasibility in (FC.BUILDABLE_NOW, FC.BUILDABLE_WITH_FEATURE), \
            f"'{c}' -> {v}"
        assert v.feature == "premium_trigger_config", f"'{c}' -> {v}"


def test_underlying_instrument_selection_maps_to_deployment_layer():
    """'The instrument is NIFTY 50 spot' / 'BANKNIFTY' — deployment-level choice
    of which underlying to watch. Not a per-bar rule."""
    for c in ["underlying_instrument", "instrument_selection", "underlying",
              "nifty", "banknifty", "sensex"]:
        v = classify_rule(_T(concepts={c}))
        assert v.feasibility in (FC.BUILDABLE_NOW, FC.BUILDABLE_WITH_FEATURE), \
            f"'{c}' -> {v}"
        assert v.feature == "deployment_layer", f"'{c}' -> {v}"


# --------------------------------------------------------------------------
# 2. Descriptive-META fallback: kind in {META, FILTER, SESSION} + empty tokens
#    accepts as declarative_config, NOT blanket-R9-rejects.
# --------------------------------------------------------------------------
def test_empty_meta_rule_accepts_as_declarative_config():
    """The exact user-reported rule: 'The strategy is intraday, same-day exits only'
    — META/CORE, empty concepts/cols. Historic R9 REJECT. Now: declarative_config."""
    v = classify_rule(_T(kind="META"))
    assert v.feasibility == FC.BUILDABLE_NOW
    assert v.feature == "declarative_config"
    assert "declarative" in v.message.lower() or "descriptive" in v.message.lower() \
        or "config" in v.message.lower()


def test_empty_filter_rule_accepts_as_declarative_config():
    """'For the CE leg, the option type is CE' — FILTER/CORE, empty tokens.
    Historic R9 REJECT. Now: declarative_config."""
    v = classify_rule(_T(kind="FILTER"))
    assert v.feasibility == FC.BUILDABLE_NOW
    assert v.feature == "declarative_config"


def test_empty_session_rule_accepts_as_declarative_config():
    """'The strategy is intraday' phrased as SESSION kind."""
    v = classify_rule(_T(kind="SESSION"))
    assert v.feasibility == FC.BUILDABLE_NOW
    assert v.feature == "declarative_config"


def test_empty_entry_rule_still_rejects_at_r9():
    """SAFETY: an ENTRY rule with empty tokens is a genuine LLM failure —
    the model couldn't decompose it. Must NOT be silently accepted as
    declarative_config. R9 REJECT is the correct answer."""
    v = classify_rule(_T(kind="ENTRY"))
    assert v.feasibility == FC.INFEASIBLE
    assert v.feature is None


def test_empty_exit_rule_still_rejects_at_r9():
    """Same as ENTRY — EXIT is a real trading rule, empty tokens = REJECT."""
    v = classify_rule(_T(kind="EXIT"))
    assert v.feasibility == FC.INFEASIBLE


def test_empty_gate_rule_still_rejects_at_r9():
    """GATE rules are trading rules (conditional flow), not descriptive. Empty
    tokens on a GATE means the LLM missed real logic; R9 REJECT is correct."""
    v = classify_rule(_T(kind="GATE"))
    assert v.feasibility == FC.INFEASIBLE


def test_empty_sizing_rule_still_rejects_at_r9():
    """SIZING rules with a concept (position_size etc.) already map to
    deployment_layer via the session-gate branch. A SIZING rule with EMPTY
    tokens is genuinely wrong (no clue how much to trade); R9 REJECT."""
    v = classify_rule(_T(kind="SIZING"))
    assert v.feasibility == FC.INFEASIBLE


def test_empty_meta_with_something_still_rejects():
    """SAFETY: a META rule that DOES have concepts must be classified by those
    concepts, not silently promoted by the fallback. If it has a data-blocked
    concept like `iv_rank`, it must still return NEEDS_NEW_DATA."""
    v = classify_rule(_T(kind="META", concepts={"iv_rank"}))
    assert v.feasibility == FC.NEEDS_NEW_DATA


def test_no_kind_still_falls_through_to_r9_when_no_tokens():
    """Back-compat: existing call sites that don't pass kind (all the pre-Phase-
    4.1 tests) must see the OLD R9 REJECT behavior on empty-tokens. The
    declarative fallback only fires when the caller signals kind."""
    v = classify_rule(_T())  # kind=None
    assert v.feasibility == FC.INFEASIBLE  # baseline behavior preserved


# --------------------------------------------------------------------------
# 3. Full blueprint smoke: the 3-5 rules that caused the user's REJECT now
#    accept (either via concept mapping OR the descriptive fallback).
# --------------------------------------------------------------------------
def test_user_reported_reject_rules_all_accept_via_concepts():
    """The user's 5 REJECT rules — when the LLM emits the right concept — accept."""
    cases = [
        # (concepts, expected_feature)
        ({"intraday"}, "deployment_layer"),
        ({"intraday_same_day"}, "deployment_layer"),
        ({"ce_leg"}, "premium_trigger_config"),
        ({"pe_leg"}, "premium_trigger_config"),
        ({"weekly_expiry"}, "premium_trigger_config"),
        ({"monthly_expiry"}, "premium_trigger_config"),
        ({"underlying_instrument"}, "deployment_layer"),
    ]
    for concepts, expected in cases:
        v = classify_rule(_T(concepts=concepts))
        assert v.feasibility != FC.INFEASIBLE, f"{concepts} -> {v}"
        assert v.feature == expected, f"{concepts} -> feature={v.feature}, expected {expected}"


def test_user_reported_reject_rules_all_accept_via_fallback():
    """Same rules — but when the LLM emits NO concepts (the failure mode) — the
    descriptive fallback must catch them since they're META/FILTER kind."""
    for kind in ("META", "FILTER", "SESSION"):
        v = classify_rule(_T(kind=kind))
        assert v.feasibility != FC.INFEASIBLE, f"kind={kind} -> {v}"
        assert v.feature == "declarative_config", f"kind={kind} -> feature={v.feature}"


# --------------------------------------------------------------------------
# 4. capability_summary advertises the widened tier.
# --------------------------------------------------------------------------
def test_capability_summary_advertises_new_concept_groups():
    """The LLM prompt reads the summary — it needs to know the new concepts exist."""
    summ = capability_summary()
    pt = summ["premium_trigger"]
    concepts_str = " ".join(pt.get("concepts", []) + pt.get("session_gates", []))
    # Must mention at least one of each new group so the LLM sees them.
    assert any(c in concepts_str for c in ("ce_leg", "pe_leg", "option_kind"))
    assert any(c in concepts_str for c in ("weekly_expiry", "monthly_expiry", "expiry_selection"))
    assert any(c in concepts_str for c in ("intraday", "intraday_same_day", "holding_period"))
