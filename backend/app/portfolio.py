"""Capital, position sizing, and rupee equity/risk metrics for option backtests.

The point-based spot backtest answers "does the signal have edge?" but a trader
lives in rupees. This module turns a sequence of paired option trades (each with
a rupee P&L after costs) into an account-level view: a rupee equity curve from a
starting capital, drawdown in rupees and %, and risk-adjusted metrics computed
on DAILY returns (a real Sharpe/Sortino, not the per-trade pseudo-Sharpe).

Position sizing (per user decision):
  - "premium_at_risk" (default): size lots so the rupee risk of the trade
    (premium-at-risk * lot_size * lots) is <= risk_per_trade_pct of capital.
    Premium-at-risk per unit = (entry_premium - stop_level) when an option stop
    exists (option_levels mode), else assumed_stop_pct_of_premium of the entry
    premium (a long option's realistic max-ish loss on a spot-based exit).
    Lot SIZE always comes from the contract; only the lot COUNT is sized.
  - "fixed_lots": use a fixed number of lots (the user's chosen `lots`).

Sizing never blocks a signal: if even one lot exceeds the risk budget, we still
take one lot but tag `risk_exceeded=True` so the discipline breach is visible
(consistent with the "tag, don't block, in research" philosophy).
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional

import numpy as np


IST_OFFSET = timedelta(hours=5, minutes=30)
DEFAULT_CAPITAL = 200_000.0
TRADING_DAYS_PER_YEAR = 252


@dataclass
class SizingConfig:
    mode: str = "premium_at_risk"          # "premium_at_risk" | "fixed_lots"
    capital: float = DEFAULT_CAPITAL
    risk_per_trade_pct: float = 1.0        # % of capital risked per trade
    fixed_lots: int = 1                    # used when mode == "fixed_lots"
    max_lots: int = 10                     # hard cap on sized lots
    assumed_stop_pct_of_premium: float = 50.0  # risk estimate when no option stop
    enabled: bool = False                  # opt-in; off keeps fixed-lots legacy behavior

    @classmethod
    def from_dict(cls, data: Optional[Dict[str, Any]]) -> "SizingConfig":
        if not data:
            return cls()
        cfg = cls()
        if "mode" in data and data["mode"]:
            cfg.mode = str(data["mode"])
        for key in ("capital", "risk_per_trade_pct", "assumed_stop_pct_of_premium"):
            if key in data and data[key] is not None:
                try:
                    setattr(cfg, key, float(data[key]))
                except (TypeError, ValueError):
                    pass
        for key in ("fixed_lots", "max_lots"):
            if key in data and data[key] is not None:
                try:
                    setattr(cfg, key, int(data[key]))
                except (TypeError, ValueError):
                    pass
        if "enabled" in data:
            cfg.enabled = bool(data["enabled"])
        return cfg

    def to_dict(self) -> Dict[str, Any]:
        return {
            "mode": self.mode,
            "capital": self.capital,
            "risk_per_trade_pct": self.risk_per_trade_pct,
            "fixed_lots": self.fixed_lots,
            "max_lots": self.max_lots,
            "assumed_stop_pct_of_premium": self.assumed_stop_pct_of_premium,
            "enabled": self.enabled,
        }


def size_position(
    *,
    entry_premium: float,
    lot_size: int,
    stop_level: Optional[float],
    cfg: SizingConfig,
) -> Dict[str, Any]:
    """Decide how many lots to trade. Returns {lots, risk_per_unit, risk_amount,
    risk_exceeded, sizing_mode}. lot_size always comes from the contract.
    """
    lot_size = max(1, int(lot_size or 1))
    if not cfg.enabled or cfg.mode == "fixed_lots":
        lots = max(1, int(cfg.fixed_lots or 1))
        lots = min(lots, max(1, cfg.max_lots))
        return {
            "lots": lots,
            "risk_per_unit": None,
            "risk_amount": None,
            "risk_exceeded": False,
            "sizing_mode": "fixed_lots",
        }

    # premium_at_risk
    if stop_level is not None and stop_level < entry_premium:
        risk_per_unit = float(entry_premium) - float(stop_level)
    else:
        risk_per_unit = float(entry_premium) * (cfg.assumed_stop_pct_of_premium / 100.0)
    risk_per_unit = max(0.01, risk_per_unit)  # avoid div-by-zero
    risk_budget = cfg.capital * (cfg.risk_per_trade_pct / 100.0)
    per_lot_risk = risk_per_unit * lot_size
    raw_lots = int(math.floor(risk_budget / per_lot_risk)) if per_lot_risk > 0 else 0
    risk_exceeded = raw_lots < 1
    lots = max(1, raw_lots)
    lots = min(lots, max(1, cfg.max_lots))
    return {
        "lots": lots,
        "risk_per_unit": round(risk_per_unit, 3),
        "risk_amount": round(per_lot_risk * lots, 2),
        "risk_exceeded": bool(risk_exceeded),
        "sizing_mode": "premium_at_risk",
    }


def _ist_date(ts_ms: Any) -> Optional[str]:
    try:
        dt = datetime.fromtimestamp(int(ts_ms) / 1000, tz=timezone.utc) + IST_OFFSET
        return dt.strftime("%Y-%m-%d")
    except Exception:
        return None


def build_rupee_equity_curve(
    trades: List[Dict[str, Any]],
    capital: float = DEFAULT_CAPITAL,
) -> Dict[str, Any]:
    """Build a rupee equity curve + risk metrics from paired option trades.

    Uses each PAIRED trade's `option_pnl_value` (rupees, net of costs) applied in
    chronological order from a starting capital. Returns the curve plus account
    metrics including a daily-returns Sharpe/Sortino and rupee/percent drawdown.
    """
    paired = [t for t in trades if t.get("status") == "PAIRED"]
    paired = sorted(paired, key=lambda t: int(t.get("option_exit_ts") or t.get("signal_exit_ts") or 0))

    equity = float(capital)
    peak = float(capital)
    max_dd_value = 0.0
    max_dd_pct = 0.0
    curve: List[Dict[str, Any]] = []
    daily_pnl: Dict[str, float] = {}

    for t in paired:
        pnl = float(t.get("option_pnl_value", 0.0))
        equity += pnl
        peak = max(peak, equity)
        dd_value = equity - peak
        dd_pct = (dd_value / peak * 100.0) if peak > 0 else 0.0
        max_dd_value = min(max_dd_value, dd_value)
        max_dd_pct = min(max_dd_pct, dd_pct)
        exit_ts = t.get("option_exit_ts") or t.get("signal_exit_ts")
        d = _ist_date(exit_ts)
        if d:
            daily_pnl[d] = daily_pnl.get(d, 0.0) + pnl
        curve.append({
            "ts": exit_ts,
            "equity_value": round(equity, 2),
            "drawdown_value": round(dd_value, 2),
            "drawdown_pct": round(dd_pct, 3),
            "pnl_value": round(pnl, 2),
        })

    net_pnl = round(equity - capital, 2)
    total_return_pct = round((net_pnl / capital * 100.0), 3) if capital > 0 else 0.0

    # Daily-return Sharpe/Sortino (the honest version).
    daily_returns = np.array([v / capital for v in daily_pnl.values()]) if daily_pnl else np.array([])
    sharpe = sortino = None
    if daily_returns.size >= 2:
        mean = float(daily_returns.mean())
        std = float(daily_returns.std(ddof=1))
        if std > 0:
            sharpe = round(mean / std * math.sqrt(TRADING_DAYS_PER_YEAR), 3)
        downside = daily_returns[daily_returns < 0]
        dstd = float(downside.std(ddof=1)) if downside.size >= 2 else 0.0
        if dstd > 0:
            sortino = round(mean / dstd * math.sqrt(TRADING_DAYS_PER_YEAR), 3)

    win_days = int(sum(1 for v in daily_pnl.values() if v > 0))
    loss_days = int(sum(1 for v in daily_pnl.values() if v < 0))

    return {
        "starting_capital": round(capital, 2),
        "ending_equity": round(equity, 2),
        "net_pnl_value": net_pnl,
        "total_return_pct": total_return_pct,
        "max_drawdown_value": round(max_dd_value, 2),
        "max_drawdown_pct": round(max_dd_pct, 3),
        "trading_days": len(daily_pnl),
        "win_days": win_days,
        "loss_days": loss_days,
        "sharpe_daily": sharpe,
        "sortino_daily": sortino,
        "curve": curve,
    }
