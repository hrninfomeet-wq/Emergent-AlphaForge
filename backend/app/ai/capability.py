"""The capability surface + the deterministic feasibility classifier (SP-3).

Host-importable: no motor, no I/O, no LLM. Three things live here:
  * WAREHOUSE_MANIFEST  — static truth about what data the warehouse has / lacks.
  * capability_report() — composes the grounding catalog (columns + buildable
    features) with the manifest into one object fed to BOTH the LLM prompt and
    the pure checker.
  * classify_rule()     — the pure R1-R9 feasibility classifier (see below).
"""
from __future__ import annotations

import dataclasses
from enum import Enum
from typing import Any, Dict, FrozenSet, List, Optional, Tuple

# Mirrors app.data_hygiene.DEFAULT_START_DATE. Inlined (not imported) so this
# module stays host-importable — data_hygiene pulls motor.
_DATA_START = "2024-11-27"

# Static truth about the warehouse. Drives the NEEDS_NEW_DATA branch.
WAREHOUSE_MANIFEST: Dict[str, Any] = {
    "has_1m_ohlcv": True,                    # candles_1m, spot, all 3 indices
    "has_option_candles": True,              # options_1m, ATM +-1 band only
    "has_per_strike_greeks_history": False,
    "has_oi_history": False,
    "has_l2_depth": False,
    "has_tick_orderflow": False,
    "has_vix_history": False,
    "date_range": {"start": _DATA_START, "end": None},
    "instruments": ["NIFTY", "BANKNIFTY", "SENSEX"],
}

# Raw OHLCV always present (mirrors compiler._RAW_OHLCV).
_RAW_OHLCV = frozenset({"open", "high", "low", "close", "volume"})


def capability_report() -> Dict[str, Any]:
    """Compose the three capability sources into one object.

    columns  -> the MAPPED surface (indicator columns + raw OHLCV + always-on
                geometry; feature columns are NOT here — advertise != allow).
    features -> the buildable structural features (feature_entries: name,
                columns, requires, cost_class, live_feasible, ...).
    warehouse-> the static data-limits manifest.
    """
    from app.ai.grounding import build_grounding_catalog

    cat = build_grounding_catalog()
    columns = sorted(set(cat["indicator_columns"]) | _RAW_OHLCV)
    return {
        "columns": columns,
        "features": cat["feature_entries"],
        "warehouse": WAREHOUSE_MANIFEST,
    }


def capability_summary() -> Dict[str, Any]:
    """UI-friendly view of what the strategy engine can build, grouped into HONEST
    tiers that mirror the deterministic classifier (R1-R9), so the wizard sets
    expectations up-front. Pure + host-safe.

    Tiers (each is genuinely different in practice):
      build_now       — indicator columns + live-feasible structural features:
                        work in BOTH backtest and live/paper, full fidelity.
      backtest_only   — stateful structural features (choch/fvg_zones/order_block):
                        build + backtest fine, but the ~200-bar live window may not
                        contain the zone's origin, so LIVE fidelity isn't guaranteed
                        yet (an engineering limit, not a permanent one).
      addable_data    — OI / PCR / greeks / IV: not stored today, but the broker
                        feed carries them — buildable once ingested + plumbed.
      needs_engine    — cross-instrument / relative-strength / pairs: needs a
                        second instrument's aligned bars in the eval context.
      infeasible      — order flow / depth / tape / news / sentiment: 1m retail
                        bars can't reconstruct it (or there's no data source).
    """
    rpt = capability_report()
    wh = rpt["warehouse"]
    all_feats = [
        {"name": f.get("feature") or f.get("name"),
         "live_feasible": f.get("live_feasible"),
         "columns": f.get("columns", [])}
        for f in rpt["features"]
    ]
    live_features = [f for f in all_feats if f["live_feasible"] is not False]
    backtest_only_features = [f for f in all_feats if f["live_feasible"] is False]

    build_now = {
        "columns": rpt["columns"],
        "features": live_features,
        "note": "Work in both backtest and live/paper at full fidelity.",
    }
    backtest_only = {
        "features": backtest_only_features,
        "note": "Build and backtest fine. These carry a zone forward from whenever "
                "it formed (SMC/ICT-style), and the live evaluator only sees the last "
                "~200 candles — so if the zone formed earlier, the live value can "
                "differ from the backtest. Deployable, but treat live behaviour as "
                "not-yet-guaranteed until longer live warm-up / state persistence lands.",
    }
    addable_data = {
        "items": [
            "Open interest / PCR / max-pain",
            "Option greeks / IV / IV-rank / vol structure",
        ],
        "note": "Not stored today, so they can't be backtested yet — but the Upstox/"
                "Flattrade option feed does carry OI and greeks are derivable. Buildable "
                "once we ingest + store their history and plumb them into the eval "
                "context (backtest-first, so an edge can be validated before real money).",
    }
    needs_engine = {
        "items": ["Cross-instrument / relative-strength / pairs / ratio-spreads"],
        "note": "Needs a second instrument's time-aligned bars inside the rule "
                "context — an engine change (planned Phase A), not a data gap.",
    }
    infeasible = {
        "items": [
            "Order flow / market depth (L2) / tape",
            "News / sentiment signals",
        ],
        "note": "Out of reach on this infrastructure: 1-minute retail bars can't "
                "reconstruct true tape/depth, and there's no news/sentiment source.",
    }
    data_limits = [
        "1-minute OHLCV candles for NIFTY, BANKNIFTY, SENSEX (spot).",
        "Option candles are stored for the ATM ±1 strike band only.",
        f"Data starts {wh.get('date_range', {}).get('start') or '(see warehouse)'}.",
        "Live/paper evaluation runs on the last ~200 candles per bar.",
    ]
    return {
        "build_now": build_now,
        "backtest_only": backtest_only,
        "addable_data": addable_data,
        "needs_engine": needs_engine,
        "infeasible": infeasible,
        "data_limits": data_limits,
        # NEW (Phase 4): advertise the premium-trigger tier so the AI authoring
        # prompt teaches the LLM the correct concept names, and the Strategy
        # Library UI can render a discoverable "Premium-trigger strategies"
        # section. What's shipped vs. Phase-5 future work is called out here.
        "premium_trigger": {
            "shipped": [
                "Locked-strike + premium-momentum entry "
                "(BUY when option premium rises N%/pts from a reference-time snapshot)",
                "Configurable moneyness selection (ITM/ATM/OTM band)",
                "Premium-based stop / target (%)",
                "Stepped X-Y premium trail (backtest + live parity via stepped_xy)",
                "Single-leg first-to-trigger CE+PE lock (either side may fire)",
            ],
            "future": [
                "Phase 5: simultaneous two-leg entry (both CE and PE may enter)",
                "Phase 5: lazy-leg contingency — on primary SL, arm the dormant "
                "opposite side with a fresh premium snapshot",
                "Phase 5: session-level max positions / re-entry cutoff / "
                "global target-SL as declared strategy config (today they're "
                "deployment-layer configuration)",
            ],
            "concepts": sorted(PREMIUM_TRIGGER_CONCEPTS),
            "session_gates": sorted(SESSION_GATE_CONCEPTS),
            "note": (
                "These rules do NOT map to per-bar OHLCV columns — they're driven "
                "by locked strikes + premium snapshots + momentum thresholds, and "
                "run through the shipped app/premium_momentum*.py family plus "
                "app/live/live_sl_monitor.py (stepped_xy trail). Configure on the "
                "deployment's premium_trigger block, not inside the strategy."
            ),
        },
        # Back-compat flat keys (older UI + tests): columns + live features.
        "columns": rpt["columns"],
        "features": all_feats,
    }


class FeasibilityClass(str, Enum):
    BUILDABLE_NOW = "BUILDABLE_NOW"
    BUILDABLE_WITH_FEATURE = "BUILDABLE_WITH_FEATURE"
    NEEDS_NEW_DATA = "NEEDS_NEW_DATA"
    INFEASIBLE = "INFEASIBLE"


@dataclasses.dataclass(frozen=True)
class RuleTokens:
    cols: FrozenSet[str] = dataclasses.field(default_factory=frozenset)
    concepts: FrozenSet[str] = dataclasses.field(default_factory=frozenset)
    barspan: int = 1
    window: int = 0
    session_anchored: bool = False
    ohlcv_derivable: bool = False


@dataclasses.dataclass(frozen=True)
class Verdict:
    feasibility: FeasibilityClass
    message: str
    feature: Optional[str] = None
    live_feasible: Optional[bool] = None


# R2 — concepts that need data the warehouse does not store.
DATA_BLOCKED_CONCEPTS: FrozenSet[str] = frozenset({
    "oi", "open_interest", "pcr", "max_pain", "iv_rank", "iv", "implied_vol",
    "theta", "vega", "gamma", "delta", "historical_greeks", "greeks",
    "vol_structure", "term_structure", "news", "sentiment",
})

# NEW (Phase 4) — premium-native trigger concepts: rules the shipped
# `premium_momentum` strategy family + its declarative deployment config already
# handle. These do NOT map to a per-rule column model (they're driven by locked
# strikes + premium snapshots + momentum thresholds), but they ARE buildable —
# they were coming back as blanket-R9 rejects only because the classifier had no
# vocabulary for them. Backed by the shipped modules:
#   * `app/premium_momentum.py`      -> lock_reference_strike, momentum_triggered
#   * `app/premium_momentum_backtest.py` -> walk_premium_momentum, stepped_trail_stop
#   * `app/premium_momentum_live.py`     -> live evaluation
#   * `app/live/live_sl_monitor.py`      -> stepped_xy trail mode
#   * `app/premium_lock_store.py`        -> persisted locks
# Phase-5 concepts (lazy-leg contingency) are handled separately below with an
# honestly-scoped "future work" verdict — NOT a blanket accept.
PREMIUM_TRIGGER_CONCEPTS: FrozenSet[str] = frozenset({
    "option_premium_trigger",   # BUY when option premium crosses N%/pts from snapshot
    "option_premium_momentum",  # alias
    "premium_momentum",         # legacy name for the shipped strategy family
    "locked_strike",            # time-locked reference strike
    "strike_lock",              # alias
    "premium_snapshot",         # entry-time premium capture
    "moneyness_selection",      # ITM1 / OTM1 / ATM strike-selection knob
    "stepped_premium_trail",    # X-Y ratchet trail on premium (5%/5% blueprint default)
    "premium_stop_pct",         # premium-based SL
    "premium_target_pct",       # premium-based TP
})

# NEW (Phase 4) — session-level gates handled at the DEPLOYMENT layer, not
# inside a strategy's evaluate(). Historically these came back INFEASIBLE (R9)
# because the classifier only knew per-bar rules. They're accepted here with a
# message pointing at the existing mechanism, so users understand WHY they don't
# appear as strategy code:
#   * entry_time_gate / exit_time_gate -> deployment time windows + auto square-off
#     (`app/auto_live.py`, `app/deployment_evaluator.py`)
#   * eod_squareoff -> `app/live/auto_square.py`
#   * re_entry_cutoff -> `app/live_deploy_governor.py` (day caps + time windows)
#   * max_positions_per_day / max_lots_per_day -> `app/live_deploy_governor.py`,
#     `app/deployment_kill_switch.py`
#   * global_target_sl -> deployment-level P&L kill via kill_switch
SESSION_GATE_CONCEPTS: FrozenSet[str] = frozenset({
    "entry_time_gate", "entry_time",
    "exit_time_gate", "exit_time",
    "eod_squareoff", "eod_close", "session_close",
    "re_entry_cutoff", "no_entry_after",
    "max_positions_per_day", "max_lots_per_day", "day_caps",
    "global_target_sl", "session_target", "session_stop",
    "session_pnl_kill",
    # Position sizing is deployment-layer config too — lots per leg is set on
    # the deployment (paper capital / live per-order cap), not baked into the
    # strategy rule. Without this, "Size 2 lots" comes back INFEASIBLE (R9)
    # because the LLM emits it as a bare SIZING rule with no columns/concepts.
    "position_size", "lot_size", "lots_per_leg", "sizing",
})

# Phase-5 (future work) concepts: honestly-scoped as BUILDABLE_WITH_FEATURE +
# live_feasible=False, so the UI can render "buildable, not shipped yet" rather
# than promising an accept the code can't back or blanket-rejecting a legitimate
# shape. Track in docs/superpowers/specs/2026-07-13-premium-momentum-phase4-5*.md.
PHASE5_FUTURE_CONCEPTS: Dict[str, str] = {
    "lazy_leg_contingency": "lazy_leg_contingency",
    "lazy_leg": "lazy_leg_contingency",
    "contingent_leg": "lazy_leg_contingency",
    "two_leg_contingency": "lazy_leg_contingency",
    "opposite_side_activation": "lazy_leg_contingency",
}

# R3 — needs a second instrument's aligned bars (engine plumbing, Phase A).
RELATIVE_STRENGTH_CONCEPTS: FrozenSet[str] = frozenset({
    "relative_strength", "pairs", "cross_instrument", "ratio_spread", "spread",
})

# R4 — needs tick-level depth/tape 1m bars can't reconstruct.
ORDERFLOW_CONCEPTS: FrozenSet[str] = frozenset({
    "order_flow", "orderflow", "footprint", "depth", "l2", "tape",
    "bid_ask_imbalance", "delta_volume", "cvd",
})

# R5 — structure concepts -> the seed feature that materializes them (or None
# if detectable but no seed feature exists yet).
STRUCTURE_FEATURE_MAP: Dict[str, Optional[str]] = {
    "fvg": "fvg_zones", "fair_value_gap": "fvg_zones", "imbalance": "fvg_zones",
    "order_block": "order_block", "ob": "order_block",
    "bos": "displacement", "break_of_structure": "displacement",
    "displacement": "displacement",
    "choch": "choch", "change_of_character": "choch",
    "premium_discount": "premium_discount", "premium": "premium_discount",
    "discount": "premium_discount", "equilibrium": "premium_discount",
    "sweep": "swing_levels", "liquidity_sweep": "swing_levels",
    "swing": "swing_levels", "swing_level": "swing_levels",
    # detectable structure with no seed feature yet (-> a NEW feature is needed):
    "breaker": None, "ote": None, "equal_highs": None, "equal_lows": None,
    "divergence": None, "mtf": None,
}

_LIVE_WINDOW_MAX = 150


def _feature_live_feasible(name: str) -> Optional[bool]:
    from app.features.registry import FEATURE_REGISTRY, feature_live_feasible
    g = FEATURE_REGISTRY.get(name)
    return None if g is None else feature_live_feasible(g)


def classify_rule(tokens: RuleTokens, *, required_features=()) -> Verdict:
    """Pure R1-R9 first-match-wins feasibility classification of one rule.

    The LLM (SP-4) fills `tokens`; this function makes the deterministic call.
    """
    from app.ai.compiler import allowed_columns

    # R1 — every referenced column is already available (incl. declared
    # features) AND the rule fits Spec's 2-bar window AND no extra concept.
    if tokens.cols and not tokens.concepts and tokens.barspan <= 2:
        if tokens.cols <= allowed_columns(required_features):
            return Verdict(FeasibilityClass.BUILDABLE_NOW,
                           "Buildable now from existing columns.")

    # R2 — needs data the warehouse does not store.
    blocked = tokens.concepts & DATA_BLOCKED_CONCEPTS
    if blocked:
        c = sorted(blocked)[0]
        return Verdict(FeasibilityClass.NEEDS_NEW_DATA,
                       f"'{c}' needs data the warehouse does not store "
                       f"(only 1m OHLCV + ATM-band option candles).")

    # ---------------------------------------------------------------
    # NEW (Phase 4): premium-native triggers. Fires BEFORE R5 so an
    # `option_premium_trigger` never collides with the ICT `premium` -> R5 path
    # (test_option_premium_trigger_wins_over_bare_premium_alias). The shipped
    # `premium_momentum` code family + a small declarative config block on the
    # deployment cover ALL these knobs today; the LLM/UI only needed a name
    # for what it was seeing.
    if tokens.concepts & PREMIUM_TRIGGER_CONCEPTS:
        return Verdict(
            FeasibilityClass.BUILDABLE_NOW,
            "Buildable via the shipped premium-trigger config "
            "(locked-strike + premium momentum + stepped trail). Configure on the "
            "deployment's premium_trigger block — no per-rule column mapping needed. "
            "Backed by app/premium_momentum*.py + app/live/live_sl_monitor.py "
            "(stepped_xy).",
            feature="premium_trigger_config", live_feasible=True,
        )

    # NEW (Phase 4): session-level gates handled at the deployment layer, not
    # inside strategy evaluate() code. The message names the actual mechanism
    # so a user reading the report knows WHERE to configure it.
    if tokens.concepts & SESSION_GATE_CONCEPTS:
        overlap = sorted(tokens.concepts & SESSION_GATE_CONCEPTS)[0]
        return Verdict(
            FeasibilityClass.BUILDABLE_NOW,
            f"'{overlap}' is handled at the deployment layer (time-of-day windows, "
            "day caps, and the auto square-off / kill-switch chain — see "
            "app/live_deploy_governor.py, app/deployment_kill_switch.py, "
            "app/live/auto_square.py). Configure it on the deployment, not inside "
            "the strategy.",
            feature="deployment_layer", live_feasible=True,
        )

    # NEW (Phase 4/5 gate): honest scoping for lazy-leg contingency — genuinely
    # NOT shipped yet (design lives in
    # docs/superpowers/specs/2026-07-13-premium-momentum-phase4-5-full-contingency-design.md),
    # but it IS a defined future-work item, so refusing to acknowledge it (R9
    # blanket reject) mis-frames it as impossible. Verdict: buildable-with-a-
    # future-feature, live-gated until Phase 5 ships.
    for c in sorted(tokens.concepts):
        if c in PHASE5_FUTURE_CONCEPTS:
            feat = PHASE5_FUTURE_CONCEPTS[c]
            return Verdict(
                FeasibilityClass.BUILDABLE_WITH_FEATURE,
                "Lazy-leg contingency (opposite-side activation on primary-leg "
                "SL) is Phase 5 future work — the design is committed "
                "(docs/superpowers/specs/2026-07-13-premium-momentum-phase4-5-"
                "full-contingency-design.md) but not yet shipped. Not-yet live-"
                "feasible; today the single-leg first-to-trigger shape ships.",
                feature=feat, live_feasible=False,
            )
    # ---------------------------------------------------------------

    # R3 — relative strength / pairs: needs a second instrument's aligned bars.
    if tokens.concepts & RELATIVE_STRENGTH_CONCEPTS:
        return Verdict(FeasibilityClass.BUILDABLE_WITH_FEATURE,
                       "Needs the other instrument's aligned bars in ctx — an "
                       "engine change (Phase A). Feasible-but-nontrivial.",
                       feature=None, live_feasible=False)

    # R4 — order flow / depth / tape: 1m bars can't reconstruct it.
    if tokens.concepts & ORDERFLOW_CONCEPTS:
        return Verdict(FeasibilityClass.INFEASIBLE,
                       "Requires tick-level depth/tape that 1m bars can't "
                       "reconstruct. Infeasible to backtest.")

    # R5 — ICT/SMC structure: detectable, but the tradeable level isn't a
    # column yet -> add (or reuse) a feature; carry its live caveat.
    struct = tokens.concepts & set(STRUCTURE_FEATURE_MAP)
    if struct:
        concept = sorted(struct)[0]
        feat = STRUCTURE_FEATURE_MAP[concept]
        lf = _feature_live_feasible(feat) if feat else None
        if feat and lf is False:
            msg = (f"Detectable via the '{feat}' feature, but it is stateful "
                   f"(carry-forward) -> backtest-only on the live window.")
        elif feat:
            msg = f"Detectable via the '{feat}' feature. Safe in backtest + live."
        else:
            msg = (f"'{concept}' is detectable from price but needs a new "
                   f"structural feature built first.")
        return Verdict(FeasibilityClass.BUILDABLE_WITH_FEATURE, msg,
                       feature=feat, live_feasible=lf)

    # R6/R7 — a vectorized quantity derivable from OHLCV but not yet a column.
    if tokens.ohlcv_derivable:
        live = (not tokens.session_anchored) and tokens.window <= _LIVE_WINDOW_MAX
        if live:
            return Verdict(FeasibilityClass.BUILDABLE_WITH_FEATURE,
                           "One vectorized feature from OHLCV. Safe in backtest + live.",
                           feature=None, live_feasible=True)
        return Verdict(FeasibilityClass.BUILDABLE_WITH_FEATURE,
                       "Buildable, but exceeds the live window / is session-anchored "
                       "-> backtest-correct, live-gated (declared backtest-only).",
                       feature=None, live_feasible=False)

    # R8 — needs more than 2 bars of history, only expressible via the history
    # frame (Full-Python), not the 2-bar Spec window.
    if tokens.barspan > 2:
        return Verdict(FeasibilityClass.BUILDABLE_WITH_FEATURE,
                       "Exceeds Spec's 2-bar window. Full-Python via the history "
                       "frame, or a small *_Nago column.",
                       feature=None, live_feasible=True)

    # R9 — default: nothing maps.
    return Verdict(FeasibilityClass.INFEASIBLE,
                   "Can't map this to anything derivable from 1m OHLCV. Give the "
                   "precise calculation or it's out of scope.")
