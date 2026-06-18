"""Scenario -> exit plan dispatcher. Single source of per-scenario exit semantics.
Defaults encode the discovered edge; all magnitudes are OPTIMIZABLE via `params`.
Returns {spot_target_pts, spot_stop_pts, spot_target_level, trail, exit_mode} or
None (no trade for this scenario)."""
from __future__ import annotations
from typing import Any, Dict, Optional


def exit_plan(scenario: str, ctx: Dict[str, Any], *, params: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    atr = float(ctx.get("atr") or 0.0)
    open_px = ctx.get("open")
    if scenario == "TREND_CONTINUATION":
        return {"spot_target_pts": float(params.get("trend_target_atr", 4.0)) * atr,
                "spot_stop_pts": float(params.get("trend_stop_atr", 1.2)) * atr,
                "spot_target_level": None, "trail": True, "exit_mode": "spot_exit"}
    if scenario == "VOLATILE_FADE":
        return {"spot_target_pts": None, "spot_target_level": float(open_px) if open_px is not None else None,
                "spot_stop_pts": float(params.get("fade_stop_atr", 1.5)) * atr,
                "trail": False, "exit_mode": "spot_exit"}
    if scenario == "CHOP":
        return {"spot_target_pts": float(params.get("chop_target_atr", 1.0)) * atr,
                "spot_stop_pts": float(params.get("chop_stop_atr", 0.8)) * atr,
                "spot_target_level": None, "trail": False, "exit_mode": "spot_exit"}
    return None
