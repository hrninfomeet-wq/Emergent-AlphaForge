"""Pure market-scenario classifier. Re-combines ALREADY-computed columns only
(regime, today's opening-range width, day_type, nr7, atr/atr_avg ratio, optional
vix_bucket) -> a scenario string. NEVER re-derives adx/atr/chop/regime.
Discovered edge (NIFTY 2025-26): narrow opening range -> the drive CONTINUES
(trend-follow); wide opening range -> the drive FADES (toward the open)."""
from __future__ import annotations
from typing import Any, Optional

SCENARIOS = ("TREND_CONTINUATION", "VOLATILE_FADE", "CHOP", "NONE")
_CHOP_REGIMES = ("CHOP", "MIXED", "VOLATILE_CHOP")


def classify_scenario(*, regime: Any, orb_width_pct: Optional[float], day_type: Any,
                      nr7: Any, atr_ratio: Any, vix_bucket: str = "UNKNOWN",
                      narrow_thr: float = 0.30, wide_thr: float = 0.60) -> str:
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
