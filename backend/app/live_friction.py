"""Shared execution-friction model for the option backtest AND the live paper path.

The backtest (`app.option_backtest`) has always priced fills net of friction —
per-side point slippage (`app.slippage`) plus an optional %-of-premium bid-ask
spread, and statutory rupee charges (`app.option_costs`). The live paper path
historically booked GROSS fills (raw tick, no slippage / spread / charges), so a
deployed strategy's forward P&L looked systematically better than the very
backtest that justified it (worst on cheap, wide-spread OTM / 0DTE premiums).

This module is the ONE place the entry/exit fill math lives, so sim and live can
never disagree about *at what price* a fill books. `app.option_backtest` calls
`fill_premium` for both legs; the live close path (`app.paper_trading.close_trade`)
calls it too, driven by a per-trade `friction` block that the deployment owner
configures (and can tune to zero). `tests/test_live_friction.py` pins sim↔live
NET-P&L parity for identical inputs.

Honest scope:
  - Friction is OPT-IN per deployment (`risk.friction.enabled`). When absent or
    disabled the live path keeps the legacy gross behavior, so nothing changes
    for existing deployments without the operator's explicit choice.
  - `fill_premium` is the single source of the fill-price computation; rupee
    charges remain in `app.option_costs.round_trip_charges` (already shared).
  - This does NOT model bid/ask depth or partial fills — it is the same
    deterministic, tunable model the backtest uses, applied consistently live.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from app.slippage import SlippageConfig, apply_slippage, estimate_slippage_per_side
from app.option_costs import CostConfig, round_trip_charges, spread_pts_for_premium


def fill_premium(
    *,
    raw_premium: float,
    side: str,                 # "BUY" (entry) or "SELL" (exit)
    moneyness: str,
    ts_ms: int,
    expiry_iso: Optional[str],
    slippage_cfg: SlippageConfig,
    cost_cfg: CostConfig,
) -> Dict[str, Any]:
    """Apply per-side execution friction to a raw fill: EITHER the half
    %-of-premium spread OR the point-slippage proxy — never both.

    The point-slippage model (`app.slippage`) is a points stand-in for the
    unmeasured bid-ask spread; the %-of-premium spread (`app.option_costs`) is the
    refined, premium-relative model of that SAME cost. Applying both double-counts
    the spread, so per the `app.option_costs` design the spread model REPLACES
    point-slippage when it is configured. The expiry-tail widening multiplier is
    carried over onto the spread, so the expiry-day blow-out the point model
    captured is not lost. When no spread is configured, point-slippage stands in
    (legacy behavior, unchanged). Returns the adjusted fill price plus components,
    so callers can store an auditable breakdown.

      BUY  -> raw + friction_pts   (you pay more than mid)
      SELL -> raw - friction_pts   (you receive less than mid)
    """
    slip = estimate_slippage_per_side(
        moneyness=moneyness,
        ts_ms=int(ts_ms),
        expiry_iso=expiry_iso,
        cfg=slippage_cfg,
    )
    tail = bool(slip.get("tail_multiplier_applied"))
    raw_half_spread = spread_pts_for_premium(float(raw_premium), cost_cfg) / 2.0
    if raw_half_spread > 0.0:
        # Spread model active: it replaces point-slippage (EITHER/OR). Preserve the
        # expiry-tail widening by applying the slippage model's multiplier to it.
        tail_mult = slippage_cfg.expiry_tail_multiplier if tail else 1.0
        half_spread = round(raw_half_spread * tail_mult, 4)
        slippage_pts = 0.0
        total_pts = half_spread
    else:
        # No spread configured -> fall back to the point-slippage proxy (legacy).
        half_spread = 0.0
        slippage_pts = slip["pts"]
        total_pts = slippage_pts
    price = apply_slippage(fill_price=float(raw_premium), side=side, pts=total_pts)
    return {
        "price": price,
        "slippage_pts": slippage_pts,
        "spread_pts": half_spread,
        "bucket": slip["bucket"],
        "tail": tail,
    }


@dataclass
class FrictionConfig:
    """Per-deployment execution-realism config for the LIVE paper path.

    Wraps the same `SlippageConfig` + `CostConfig` the backtest accepts, behind a
    single master switch so the operator chooses whether forward fills are priced
    with the same friction as the backtest (recommended) or left gross (legacy).
    `moneyness` / `expiry_iso` travel with the trade so the exit fill uses the
    same slippage bucket / expiry-tail rule as the entry.
    """
    enabled: bool = False
    moneyness: str = "atm"
    expiry_iso: Optional[str] = None
    slippage: SlippageConfig = field(default_factory=SlippageConfig)
    costs: CostConfig = field(default_factory=CostConfig)

    @classmethod
    def from_dict(cls, data: Optional[Dict[str, Any]]) -> "FrictionConfig":
        if not data:
            return cls()
        cfg = cls(
            slippage=SlippageConfig.from_dict(data.get("slippage")),
            costs=CostConfig.from_dict(data.get("costs")),
        )
        cfg.enabled = bool(data.get("enabled"))
        if data.get("moneyness"):
            cfg.moneyness = str(data["moneyness"]).lower()
        expiry = data.get("expiry_iso")
        cfg.expiry_iso = str(expiry) if expiry else None
        return cfg

    def to_dict(self) -> Dict[str, Any]:
        return {
            "enabled": self.enabled,
            "moneyness": self.moneyness,
            "expiry_iso": self.expiry_iso,
            "slippage": self.slippage.to_dict(),
            "costs": self.costs.to_dict(),
        }


def apply_entry_friction(
    raw_premium: float,
    friction: "FrictionConfig",
    *,
    ts_ms: int,
) -> Dict[str, Any]:
    """Adjust a resolved entry premium for the live BUY fill. When disabled the
    price is returned unchanged so levels/sizing behave exactly as before."""
    if not friction.enabled:
        return {"price": float(raw_premium), "slippage_pts": 0.0, "spread_pts": 0.0,
                "bucket": None, "tail": False}
    return fill_premium(
        raw_premium=raw_premium, side="BUY",
        moneyness=friction.moneyness, ts_ms=ts_ms, expiry_iso=friction.expiry_iso,
        slippage_cfg=friction.slippage, cost_cfg=friction.costs,
    )


def close_economics(
    *,
    raw_exit_premium: float,
    entry_price: float,
    raw_entry_price: float,
    quantity: int,
    friction: "FrictionConfig",
    ts_ms: int,
) -> Dict[str, Any]:
    """Compute the live SELL fill, round-trip charges, and net vs gross P&L.

    `entry_price` is the (already friction-adjusted at open) stored entry; the
    realized P&L is measured against it so entry and exit friction are both
    counted exactly once. `raw_entry_price` is the unslipped entry premium, used
    only to report the friction-free gross P&L for the operator to compare.
    """
    qty = max(0, int(quantity))
    if not friction.enabled:
        # Legacy gross close: realized = (raw_exit - entry) * qty, no charges.
        gross = round((float(raw_exit_premium) - float(entry_price)) * qty, 2)
        return {
            "exit_fill_price": float(raw_exit_premium),
            "realized_pnl": gross,
            "gross_realized_pnl": gross,
            "friction_cost": 0.0,
            "total_charges": 0.0,
            "charges": None,
            "exit_slippage_pts": 0.0,
            "exit_spread_pts": 0.0,
        }
    sell = fill_premium(
        raw_premium=raw_exit_premium, side="SELL",
        moneyness=friction.moneyness, ts_ms=ts_ms, expiry_iso=friction.expiry_iso,
        slippage_cfg=friction.slippage, cost_cfg=friction.costs,
    )
    exit_fill = sell["price"]
    charges = round_trip_charges(
        entry_premium=float(entry_price),
        exit_premium=exit_fill,
        quantity=qty,
        cfg=friction.costs,
    ) if friction.costs.enabled else None
    total_charges = float(charges["total_charges"]) if charges else 0.0
    net = round((exit_fill - float(entry_price)) * qty - total_charges, 2)
    # Gross is the pure premium move with NO friction on either leg, so the
    # operator can see exactly what slippage + spread + charges cost them.
    gross = round((float(raw_exit_premium) - float(raw_entry_price)) * qty, 2)
    return {
        "exit_fill_price": round(exit_fill, 3),
        "realized_pnl": net,
        "gross_realized_pnl": gross,
        "friction_cost": round(gross - net, 2),
        "total_charges": round(total_charges, 2),
        "charges": charges,
        "exit_slippage_pts": sell["slippage_pts"],
        "exit_spread_pts": round(sell["spread_pts"], 4),
    }
