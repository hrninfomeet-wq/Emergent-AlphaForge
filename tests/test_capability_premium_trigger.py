"""Phase 4: teach the feasibility classifier that premium-native rules and
session-level gates are BUILDABLE — not a blanket R9 reject.

This test file locks in the user-facing contract for the "Configurable Contingency
Breakout (NF CE PE EXP2 Base)" AlgoTest blueprint the user pasted into the
Strategy Library's "Check feasibility" panel and got a blanket REJECT on every
rule. Root cause was `capability.py`'s R1-R9 model had zero concept of:

  * option-PREMIUM-native triggers (buy when the option PREMIUM rises N% from an
    entry-time snapshot on a locked strike) — the shipped `premium_momentum`
    strategy already implements exactly this, but the classifier didn't know
    it was buildable via config.
  * time-locked strikes / lazy-leg contingency
  * session-level gates the app already enforces at the DEPLOYMENT layer, not
    inside the strategy's evaluate() — entry-time gate, exit-time gate,
    EOD square-off, re-entry cutoff, max positions per day, global target/SL

There is also a real token collision to guard against: the existing R5 map
sends the bare word `premium` to the ICT `premium_discount` structural zone.
An OPTION-premium rule must NOT be silently misclassified into R5 — the new
premium-trigger detector must fire FIRST on the option-premium co-occurrence
tokens, and R5 must remain intact for real ICT premium/discount rules.

All tests are host-safe / pure (no motor, no LLM, no network).
"""
from __future__ import annotations

import os, sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import app.features.catalog  # noqa: F401  (populates FEATURE_REGISTRY)

from app.ai.capability import (
    FeasibilityClass as FC,
    RuleTokens,
    capability_report,
    capability_summary,
    classify_rule,
)


def _T(**kw):
    base = dict(cols=frozenset(), concepts=frozenset(), barspan=1, window=0,
                session_anchored=False, ohlcv_derivable=False)
    base.update(kw)
    base["cols"] = frozenset(base["cols"])
    base["concepts"] = frozenset(base["concepts"])
    return RuleTokens(**base)


# --------------------------------------------------------------------------
# 1. The core Phase 4 fix: option-premium momentum triggers are BUILDABLE.
# --------------------------------------------------------------------------
def test_option_premium_momentum_trigger_is_buildable():
    """The LLM emits `option_premium_trigger` for a rule like 'BUY when the CE
    premium rises 20% from the 09:31 snapshot on the ITM1 strike'. This maps
    to the shipped premium_momentum config, not a per-rule column model."""
    v = classify_rule(_T(concepts={"option_premium_trigger"}))
    assert v.feasibility in (FC.BUILDABLE_NOW, FC.BUILDABLE_WITH_FEATURE)
    assert v.feature == "premium_trigger_config"
    # Must be live-feasible — the shipped strategy already runs in live/paper.
    assert v.live_feasible is True
    # Message should point the user at the declarative config, not the reject.
    msg = v.message.lower()
    assert "premium" in msg and "config" in msg


def test_locked_strike_snapshot_is_buildable():
    v = classify_rule(_T(concepts={"locked_strike"}))
    assert v.feasibility in (FC.BUILDABLE_NOW, FC.BUILDABLE_WITH_FEATURE)
    assert v.feature == "premium_trigger_config"
    assert v.live_feasible is True


def test_moneyness_selection_is_buildable():
    """'ITM1' / 'OTM2' / 'ATM' strike-selection is a config knob, not a per-rule column."""
    v = classify_rule(_T(concepts={"moneyness_selection"}))
    assert v.feasibility in (FC.BUILDABLE_NOW, FC.BUILDABLE_WITH_FEATURE)
    assert v.feature == "premium_trigger_config"


def test_stepped_premium_trail_is_buildable():
    """The X-Y stepped trail (5%/5% blueprint default) is the shipped
    live_sl_monitor stepped_xy mode + backtest's walk_premium_momentum."""
    v = classify_rule(_T(concepts={"stepped_premium_trail"}))
    assert v.feasibility in (FC.BUILDABLE_NOW, FC.BUILDABLE_WITH_FEATURE)
    assert v.feature == "premium_trigger_config"


# --------------------------------------------------------------------------
# 2. Disambiguation: option-premium rules MUST NOT be misclassified as ICT
#    premium/discount zones (the historic R5 token collision).
# --------------------------------------------------------------------------
def test_ict_premium_discount_still_maps_to_r5_feature():
    """Guard the regression: a genuine ICT premium/discount rule (no option-
    premium co-occurrence tokens) still maps to the premium_discount feature."""
    v = classify_rule(_T(concepts={"premium_discount"}))
    assert v.feasibility == FC.BUILDABLE_WITH_FEATURE
    assert v.feature == "premium_discount"
    assert v.live_feasible is True


def test_option_premium_trigger_wins_over_bare_premium_alias():
    """If the LLM (uncertainly) emits BOTH bare 'premium' and the specific
    option_premium_trigger concept, the option-premium branch must fire — NOT
    the R5 ICT premium_discount branch. This is the token-collision guard the
    Phase 4 spec (2026-07-13 doc §3.3) called out as a required disambiguation."""
    v = classify_rule(_T(concepts={"premium", "option_premium_trigger"}))
    assert v.feature == "premium_trigger_config"
    # Must NOT be misclassified as the ICT premium_discount feature.
    assert v.feature != "premium_discount"


def test_bare_premium_alone_falls_through_to_ict_r5():
    """Bare `premium` (no option-side markers) still maps to ICT premium_discount
    via R5 — matches the existing STRUCTURE_FEATURE_MAP entry. This is the
    baseline behavior we must not break."""
    v = classify_rule(_T(concepts={"premium"}))
    assert v.feasibility == FC.BUILDABLE_WITH_FEATURE
    assert v.feature == "premium_discount"


# --------------------------------------------------------------------------
# 3. Session-level gates are BUILDABLE — mapped to the DEPLOYMENT layer.
# --------------------------------------------------------------------------
def test_entry_time_gate_maps_to_deployment_layer():
    """'Enter at 09:31:00' is not a strategy rule — it's a deployment-time
    trigger. The classifier must ACCEPT it with a message pointing at the
    existing time-window mechanism, not blanket REJECT it."""
    v = classify_rule(_T(concepts={"entry_time_gate"}))
    assert v.feasibility in (FC.BUILDABLE_NOW, FC.BUILDABLE_WITH_FEATURE)
    assert v.feature == "deployment_layer"
    msg = v.message.lower()
    assert "deployment" in msg or "time" in msg


def test_exit_time_gate_maps_to_deployment_layer():
    """EOD-close-at 15:13 is the deployment-layer square-off, not evaluate() logic."""
    v = classify_rule(_T(concepts={"exit_time_gate"}))
    assert v.feasibility in (FC.BUILDABLE_NOW, FC.BUILDABLE_WITH_FEATURE)
    assert v.feature == "deployment_layer"


def test_eod_squareoff_maps_to_deployment_layer():
    """The 15:00 auto-square-off is a kill-switch level concern."""
    v = classify_rule(_T(concepts={"eod_squareoff"}))
    assert v.feature == "deployment_layer"


def test_re_entry_cutoff_maps_to_deployment_layer():
    """'No new entries after 15:09' is a deployment cap, not a per-rule filter."""
    v = classify_rule(_T(concepts={"re_entry_cutoff"}))
    assert v.feature == "deployment_layer"


def test_max_positions_per_day_maps_to_deployment_layer():
    """Deployment governor / kill-switch / max_lots_per_day layer, not evaluate()."""
    v = classify_rule(_T(concepts={"max_positions_per_day"}))
    assert v.feature == "deployment_layer"


def test_global_target_sl_maps_to_deployment_layer():
    """A blueprint's 'close everything when global TP hit' is a deployment overlay."""
    v = classify_rule(_T(concepts={"global_target_sl"}))
    assert v.feature == "deployment_layer"


def test_position_size_maps_to_deployment_layer():
    """'Size 2 lots' is deployment configuration (paper capital / live per-order
    cap), not a strategy rule. Historic REJECT: the LLM emitted no concepts for
    'Size 2 lots' so it fell to R9 blanket INFEASIBLE. Now maps to deployment layer."""
    v = classify_rule(_T(concepts={"position_size"}))
    assert v.feature == "deployment_layer"
    v = classify_rule(_T(concepts={"lot_size"}))
    assert v.feature == "deployment_layer"
    v = classify_rule(_T(concepts={"sizing"}))
    assert v.feature == "deployment_layer"


# --------------------------------------------------------------------------
# 4. Lazy-leg contingency is future-work (Phase 5) — must be honestly-scoped,
#    NOT a blanket REJECT and NOT a false BUILD.
# --------------------------------------------------------------------------
def test_lazy_leg_contingency_is_honestly_scoped():
    """The blueprint's 'if primary CE hits SL, arm dormant PE with a fresh
    snapshot' is genuinely NEW behavior (Phase 5). It should NOT return
    INFEASIBLE (that mis-frames it as impossible) — it's buildable, it's just
    not shipped yet. Honest verdict: BUILDABLE_WITH_FEATURE, live-gated."""
    v = classify_rule(_T(concepts={"lazy_leg_contingency"}))
    assert v.feasibility == FC.BUILDABLE_WITH_FEATURE
    # Feature name points at the future Phase-5 spec, not a shipped feature.
    assert v.feature == "lazy_leg_contingency"
    # Not yet live-feasible — must not be silently promised.
    assert v.live_feasible is False
    msg = v.message.lower()
    assert "phase 5" in msg or "not yet" in msg or "future" in msg


# --------------------------------------------------------------------------
# 5. Full blueprint sanity: the AlgoTest blueprint rules from the handoff spec
#    must produce NO blanket rejects. Some may be ADVISE/backtest-only
#    (lazy_leg_contingency), but nothing that already-shipped code handles
#    should come back INFEASIBLE.
# --------------------------------------------------------------------------
def test_full_algotest_blueprint_produces_no_blanket_rejects_on_shipped_behavior():
    """The blueprint rules that map to ALREADY-SHIPPED behavior must accept.
    (Lazy-leg contingency is Phase-5 future work and is checked separately.)"""
    # Rules the shipped premium_momentum + deployment layer already covers.
    shipped_rule_concepts = [
        {"entry_time_gate"},           # global entry time 09:31
        {"exit_time_gate"},            # exit at 15:13
        {"re_entry_cutoff"},           # 15:09 no-new-entries
        {"max_positions_per_day"},     # 1 per leg
        {"global_target_sl"},          # session-wide TP/SL
        {"position_size"},             # 2 lots per leg — deployment sizing
        {"option_premium_trigger"},    # BUY when premium rises N% from snapshot
        {"locked_strike"},             # ITM1 lock at 09:31
        {"moneyness_selection"},       # ITM1 / OTM1 / ATM
        {"stepped_premium_trail"},     # 5%/5% ratchet
    ]
    for concepts in shipped_rule_concepts:
        v = classify_rule(_T(concepts=concepts))
        assert v.feasibility != FC.INFEASIBLE, (
            f"Concepts {concepts} came back INFEASIBLE; the shipped strategy + "
            f"deployment layer already handle this. Verdict: {v}"
        )
        assert v.feasibility != FC.NEEDS_NEW_DATA, (
            f"Concepts {concepts} came back NEEDS_NEW_DATA; but the warehouse "
            f"already stores what's needed (option candles + spot 1m). Verdict: {v}"
        )


# --------------------------------------------------------------------------
# 6. capability_report / capability_summary advertise the new tier so the LLM
#    prompt + the UI wizard know these rule shapes are supported.
# --------------------------------------------------------------------------
def test_capability_summary_advertises_premium_trigger_tier():
    """The Strategy Library UI + the LLM authoring prompt both read the summary.
    Add a top-level `premium_trigger` block so users/LLM know it's supported."""
    summ = capability_summary()
    assert "premium_trigger" in summ, (
        "capability_summary must advertise the premium_trigger tier so the LLM "
        "authoring prompt teaches the model the correct concept names, and the "
        "Strategy Library wizard can render a 'Premium-trigger strategies' hint."
    )
    pt = summ["premium_trigger"]
    # Must call out what's shipped vs. Phase-5 future work.
    assert "shipped" in pt or "supported" in pt or "concepts" in pt


def test_capability_report_unchanged_for_existing_consumers():
    """Regression: the existing `columns` / `features` / `warehouse` keys must
    still exist — existing tests + the compiler depend on them."""
    rpt = capability_report()
    assert "columns" in rpt
    assert "features" in rpt
    assert "warehouse" in rpt
    # And the ICT premium_discount feature is still discoverable via features.
    feats = {f.get("feature") or f.get("name"): f for f in rpt["features"]}
    assert "premium_discount" in feats
