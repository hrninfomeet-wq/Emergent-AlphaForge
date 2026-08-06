"""Realistic rupee cost model for Indian index option trades.

A backtest that ignores charges and the bid-ask spread will look far better than
reality. For an options-buying strategy on NIFTY/BANKNIFTY/SENSEX the real drag
per round trip is: brokerage + STT (sell side, on premium) + exchange txn +
SEBI + GST + stamp duty, PLUS the bid-ask spread you cross on entry and exit.

This module computes the total round-trip charges in rupees for ONE option
trade (buy then sell), given the entry/exit premium, quantity (lot_size * lots),
and a configurable charge schedule. It also models the bid-ask spread as a
PERCENTAGE OF PREMIUM (per user decision) with a small points floor, because a
fixed-point spread badly understates the pain on cheap far-OTM / 0DTE options
where a ₹2 spread on a ₹8 premium is 25%.

Everything is configurable and OPT-IN. The default schedule reflects common
Indian discount-broker rates as of 2026 (Flattrade = ₹0 brokerage; others ~₹20).
These are operator-tunable, not hard truths — exchanges revise charges.

Scope / honesty:
  - This is a deterministic, per-trade rupee charge. It does NOT replace the
    point-based slippage model (app.slippage); the bid-ask spread here is a
    SEPARATE, premium-relative cost layered on the fill, and the caller chooses
    which to apply. To avoid double-counting, the option backtest uses EITHER
    point-slippage OR this spread model, governed by config.
  - STT/charges are statutory and apply on every broker. Brokerage is the only
    broker-specific knob (₹0 for Flattrade).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional


# --- Statutory + brokerage rates (F&O options, 2026 reference) ---------------
# All rates are fractions unless noted. Sourced from public broker charge lists;
# operator-tunable via CostConfig.
DEFAULT_BROKERAGE_PER_ORDER = 0.0        # Flattrade = 0; set 20 for Zerodha/Upstox-style
# STT on options: 0.1% of SELL-side premium since 2024-10-01 (was 0.0625%).
# Saved presets/runs that serialized a full cost_config keep their stored rate
# (reproducibility); only configs without an explicit stt_sell_rate get this.
DEFAULT_STT_SELL_RATE = 0.001
DEFAULT_EXCHANGE_TXN_RATE = 0.0003503   # == EXCHANGE_TXN_RATE_NFO below.
# One NSE rate for paper, backtest AND live: a separate, marginally different
# default here would silently make live charge differently from the paper run
# that validated it — the exact divergence this consolidation removes.
DEFAULT_SEBI_RATE = 0.000001             # ₹10 per crore = 0.0001%
DEFAULT_GST_RATE = 0.18                  # 18% on (brokerage + exchange txn + SEBI)
DEFAULT_STAMP_BUY_RATE = 0.00003         # 0.003% on BUY-side premium turnover

# Exchange transaction charges differ by SEGMENT. DEFAULT_EXCHANGE_TXN_RATE above
# is the NSE (NFO) figure; BSE (BFO — SENSEX) is materially lower, so charging the
# NSE rate on a SENSEX trade over-states that component by ~7.7%.
#
# Ported from the former app/live/live_friction_profile.py, which carried the only
# segment-aware schedule in the codebase and had ZERO callers. Keeping a second,
# unwired charge implementation is a trap — a future caller wires the wrong one and
# live silently diverges from paper — so the knowledge moved here and the duplicate
# module was deleted.
#
# RATES ARE STATUTORY AND CHANGE. Verify against current circulars before relying
# on these for anything beyond estimation:
#   NSE circular CML/2024/57 and BSE notice 20240930-1 (exchange txn charges,
#   effective 2024-10-01); Finance Act 2024 (STT, options sell-side);
#   SEBI/HO/MRD/MRD-PoD-1/P/CIR/2022/0044 (SEBI fee); Stamp Act 2019 (stamp duty,
#   buy-side). Flattrade charges ZERO F&O brokerage, hence the 0.0 default above.
EXCHANGE_TXN_RATE_NFO = DEFAULT_EXCHANGE_TXN_RATE   # NSE F&O (~0.03503%)
EXCHANGE_TXN_RATE_BFO = 0.000325    # BSE F&O options (~0.0325% of turnover)


def cost_config_for_exchange(exch: Optional[str]) -> "CostConfig":
    """An enabled CostConfig carrying the exchange-transaction rate for *exch*.

    "BFO" (BSE / SENSEX) gets the BSE rate; everything else — including an
    unknown, empty or missing value — falls back to NSE, which is the busier
    segment and the safer over-estimate of charges.
    """
    cfg = CostConfig(enabled=True)
    cfg.exchange_txn_rate = (
        EXCHANGE_TXN_RATE_BFO if str(exch or "").strip().upper() == "BFO"
        else EXCHANGE_TXN_RATE_NFO
    )
    return cfg


@dataclass
class CostConfig:
    """Configurable rupee cost schedule for one option round trip."""
    brokerage_per_order: float = DEFAULT_BROKERAGE_PER_ORDER   # charged per leg (buy, sell)
    stt_sell_rate: float = DEFAULT_STT_SELL_RATE
    exchange_txn_rate: float = DEFAULT_EXCHANGE_TXN_RATE
    sebi_rate: float = DEFAULT_SEBI_RATE
    gst_rate: float = DEFAULT_GST_RATE
    stamp_buy_rate: float = DEFAULT_STAMP_BUY_RATE
    # Bid-ask spread modeled as % of premium, per side, with a points floor.
    # 0 disables the spread model (e.g. when using app.slippage points instead).
    spread_pct_of_premium: float = 0.0      # e.g. 1.0 = 1% of premium per side
    spread_min_pts: float = 0.0             # floor in option points per side
    enabled: bool = False                   # opt-in; off keeps legacy behavior

    @classmethod
    def from_dict(cls, data: Optional[Dict[str, Any]]) -> "CostConfig":
        if not data:
            return cls()
        cfg = cls()
        for key in (
            "brokerage_per_order", "stt_sell_rate", "exchange_txn_rate",
            "sebi_rate", "gst_rate", "stamp_buy_rate",
            "spread_pct_of_premium", "spread_min_pts",
        ):
            if key in data and data[key] is not None:
                try:
                    setattr(cfg, key, float(data[key]))
                except (TypeError, ValueError):
                    pass
        if "enabled" in data:
            cfg.enabled = bool(data["enabled"])
        return cfg

    def to_dict(self) -> Dict[str, Any]:
        return {
            "brokerage_per_order": self.brokerage_per_order,
            "stt_sell_rate": self.stt_sell_rate,
            "exchange_txn_rate": self.exchange_txn_rate,
            "sebi_rate": self.sebi_rate,
            "gst_rate": self.gst_rate,
            "stamp_buy_rate": self.stamp_buy_rate,
            "spread_pct_of_premium": self.spread_pct_of_premium,
            "spread_min_pts": self.spread_min_pts,
            "enabled": self.enabled,
        }


def spread_pts_for_premium(premium: float, cfg: CostConfig) -> float:
    """Per-side bid-ask spread in option POINTS for a given premium.

    Percentage-of-premium with a points floor. Returns 0 when the spread model
    is disabled. The caller applies this on the fill: BUY pays +half-spread,
    SELL receives -half-spread (a full spread is crossed over the round trip).
    """
    if not cfg.enabled or cfg.spread_pct_of_premium <= 0 and cfg.spread_min_pts <= 0:
        return 0.0
    pct_pts = max(0.0, float(premium)) * (cfg.spread_pct_of_premium / 100.0)
    return round(max(pct_pts, cfg.spread_min_pts), 4)


def round_trip_charges(
    *,
    entry_premium: float,
    exit_premium: float,
    quantity: int,
    cfg: CostConfig,
) -> Dict[str, Any]:
    """Compute total statutory + brokerage charges (₹) for one buy->sell round trip.

    quantity = lot_size * lots (total contracts). Premiums are per-unit option
    prices. Returns a breakdown plus the total. Does NOT include the bid-ask
    spread (that is applied to the fill price separately via spread_pts_for_premium).
    """
    qty = max(0, int(quantity))
    buy_turnover = max(0.0, float(entry_premium)) * qty
    sell_turnover = max(0.0, float(exit_premium)) * qty

    brokerage = cfg.brokerage_per_order * 2  # two legs
    stt = sell_turnover * cfg.stt_sell_rate
    exchange = (buy_turnover + sell_turnover) * cfg.exchange_txn_rate
    sebi = (buy_turnover + sell_turnover) * cfg.sebi_rate
    stamp = buy_turnover * cfg.stamp_buy_rate
    gst = (brokerage + exchange + sebi) * cfg.gst_rate
    total = brokerage + stt + exchange + sebi + stamp + gst
    return {
        "brokerage": round(brokerage, 2),
        "stt": round(stt, 2),
        "exchange_txn": round(exchange, 2),
        "sebi": round(sebi, 4),
        "stamp_duty": round(stamp, 2),
        "gst": round(gst, 2),
        "total_charges": round(total, 2),
    }
