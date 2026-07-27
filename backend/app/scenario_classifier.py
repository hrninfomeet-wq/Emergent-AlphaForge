"""Pure market-scenario classifier. Re-combines ALREADY-computed columns only ->
a scenario string. NEVER re-derives adx/atr/chop/regime.

The shipped rule keys off exactly TWO inputs: today's opening-range width
(`orb_width_pct`) and `regime`. `day_type` and `nr7` are RESERVED — the router
passes them because both are already free columns on the frame, but no rule
reads them yet. That is pinned by tests/test_scenario_classifier.py so reserved
surface stays a visible choice instead of becoming a silent capability claim.

Two former inputs were removed for exactly that reason: `atr_ratio` (which cost
the router a per-bar `_atr_ratio()` helper call to supply a value no rule read)
and `vix_bucket` (never passed by any caller, and there is no per-bar VIX column
to bucket — see app/strategies/plugins/explosive_reversal.py's docstring).

Discovered edge (NIFTY 2025-26): narrow opening range -> the drive CONTINUES
(trend-follow); wide opening range -> the drive FADES (toward the open)."""
from __future__ import annotations
from typing import Any, Optional

SCENARIOS = ("TREND_CONTINUATION", "VOLATILE_FADE", "CHOP", "NONE")
_CHOP_REGIMES = ("CHOP", "MIXED", "VOLATILE_CHOP")


def classify_scenario(*, regime: Any, orb_width_pct: Optional[float], day_type: Any,
                      nr7: Any, narrow_thr: float = 0.30, wide_thr: float = 0.60) -> str:
    """`orb_width_pct` = TODAY's opening-range width as % of pivot (the causal
    decision input). Thresholds are optimizable. Returns one of SCENARIOS."""
    try:
        w = None if orb_width_pct is None else float(orb_width_pct)
    except (TypeError, ValueError):
        w = None
    if w is None or w != w:  # None or NaN -> no decision
        return "NONE"
    if w >= wide_thr:
        return "VOLATILE_FADE"
    if w <= narrow_thr:
        return "TREND_CONTINUATION"
    if str(regime) in _CHOP_REGIMES:
        return "CHOP"
    return "NONE"
