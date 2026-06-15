"""Execution policy carried on presets.

A preset used to be "strategy + parameters", which silently dropped the option
execution context the result was validated under (moneyness, DTE filter, exit
mode, premium levels, lots, costs). This module derives a compact `execution`
block from an optimizer/WFO `option_config` so the preset becomes the full
deployable artifact: Backtest Lab re-applies it on load, and the deployment
form prefills option policy + auto-paper fallbacks from it.

Pure functions only — no DB access — so they stay unit-testable.
"""
from __future__ import annotations

from typing import Any, Dict, Optional


def _num(value: Any) -> Optional[float]:
    """Positive float or None (treat 0/''/garbage as unset)."""
    try:
        if value in (None, ""):
            return None
        f = float(value)
        return f if f > 0 else None
    except (TypeError, ValueError):
        return None


def execution_from_option_config(option_cfg: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Derive the preset `execution` block from an option_config dict.

    Returns None when there is no option config (spot-only result — nothing
    meaningful to carry). Premium level fields are kept only when set, so the
    block stays compact and "unset" stays distinguishable from 0.
    """
    if not option_cfg:
        return None
    execution: Dict[str, Any] = {
        "moneyness": str(option_cfg.get("moneyness") or "atm"),
        "dte_filter": option_cfg.get("dte_filter"),
        "exit_mode": str(option_cfg.get("exit_mode") or "spot_exit"),
        "lots": int(option_cfg.get("lots") or 1),
    }
    for key in ("option_target_pts", "option_stop_pts", "option_target_pct", "option_stop_pct"):
        val = _num(option_cfg.get(key))
        if val is not None:
            execution[key] = val
    cost_cfg = option_cfg.get("cost_config") or {}
    if cost_cfg.get("enabled"):
        execution["cost_config"] = {
            "enabled": True,
            "brokerage_per_order": float(cost_cfg.get("brokerage_per_order") or 0.0),
            "spread_pct_of_premium": float(cost_cfg.get("spread_pct_of_premium") or 0.0),
        }
    exit_controls = (option_cfg or {}).get("exit_controls")
    if exit_controls is not None:
        execution["exit_controls"] = exit_controls
    daily_caps = (option_cfg or {}).get("daily_caps")
    if daily_caps is not None:
        execution["daily_caps"] = daily_caps
    return execution
