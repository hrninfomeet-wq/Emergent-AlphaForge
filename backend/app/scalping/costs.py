"""Statutory charges for the scalper, at the rates in force on 2026-10-07.

Reuses ``app.option_costs.round_trip_charges`` (one formula for the whole app) but supplies the
CURRENT schedule explicitly. The app-wide defaults in ``option_costs`` still carry the pre-2026
STT (0.10 %) and NSE transaction rate (0.03503 %); changing those reprices every saved backtest,
which HANDOFF §4.3 says needs operator sign-off, so that change is proposed, not made here
(docs/scalping/01-feasibility-and-capabilities.md §6).

Rates (fraction of premium turnover unless noted):

==================  ===========  ==================  ======================================================
component           NFO (NIFTY)  BFO (SENSEX)        evidence (accessed 2026-10-06/07)
==================  ===========  ==================  ======================================================
STT, sell side      0.0015       0.0015              Finance Act 2026, effective 2026-04-01; Zerodha charges
                                                     page, Upstox explainer 2026-02-13, Flattrade Kosh
                                                     2026-02-02 (Confirmed by 3 broker pages)
exchange txn        0.0003553    0.000325            NSE circular FA/73061 2026-02-27 eff. 2026-03-01
                                                     (Likely: broker page + circular summary; PDF fetch timed
                                                     out); BSE 0.0325 % unchanged (Zerodha page)
SEBI fee            0.000001     0.000001            Rs 10 / crore
stamp duty, buy     0.00003      0.00003             Stamp Act 2019 (0.003 %)
GST                 0.18         0.18                on (brokerage + exchange + SEBI)
brokerage           0            0                   Flattrade F&O = Rs 0 per order
==================  ===========  ==================  ======================================================

Ignored (immaterial): NSE IPFT Rs 0.01/crore; exercise STT on ITM expiry (a scalper never holds to
settlement — the engine force-exits before the close).
"""
from __future__ import annotations

from typing import Dict

from app.option_costs import CostConfig, round_trip_charges

STT_SELL_RATE_2026 = 0.0015
EXCHANGE_TXN_RATE = {"NFO": 0.0003553, "BFO": 0.000325}
SEBI_RATE = 0.000001
STAMP_BUY_RATE = 0.00003
GST_RATE = 0.18
BROKERAGE_PER_ORDER = 0.0

SCHEDULE_AS_OF = "2026-10-07"


def cost_config(exchange: str) -> CostConfig:
    """Enabled CostConfig at the current schedule for ``exchange`` ("NFO" | "BFO").

    An unknown exchange raises: silently charging the wrong segment is exactly the class of
    bug option_costs.cost_config_for_exchange documents (backtest/paper charge SENSEX at NSE)."""
    exch = str(exchange or "").strip().upper()
    if exch not in EXCHANGE_TXN_RATE:
        raise ValueError(f"unknown exchange {exchange!r}; expected NFO or BFO")
    cfg = CostConfig(enabled=True)
    cfg.brokerage_per_order = BROKERAGE_PER_ORDER
    cfg.stt_sell_rate = STT_SELL_RATE_2026
    cfg.exchange_txn_rate = EXCHANGE_TXN_RATE[exch]
    cfg.sebi_rate = SEBI_RATE
    cfg.stamp_buy_rate = STAMP_BUY_RATE
    cfg.gst_rate = GST_RATE
    cfg.spread_pct_of_premium = 0.0   # the scalper fills at real quotes; no modelled spread
    cfg.spread_min_pts = 0.0
    return cfg


def charges_inr(exchange: str, entry_price: float, exit_price: float, quantity: int) -> float:
    """Total statutory charges (Rs) for one buy->sell round trip."""
    return float(round_trip_charges(entry_premium=entry_price, exit_premium=exit_price,
                                    quantity=quantity, cfg=cost_config(exchange))["total_charges"])


def charges_breakdown(exchange: str, entry_price: float, exit_price: float, quantity: int) -> Dict[str, float]:
    return round_trip_charges(entry_premium=entry_price, exit_premium=exit_price,
                              quantity=quantity, cfg=cost_config(exchange))


def statutory_pct_of_premium(exchange: str, premium: float) -> float:
    """Round-trip statutory charges as % of premium for an unchanged price (buy == sell)."""
    if premium <= 0:
        raise ValueError("premium must be positive")
    q = 1000  # large quantity so 2-dp rounding inside round_trip_charges is negligible
    return 100.0 * charges_inr(exchange, premium, premium, q) / (premium * q)


def breakeven_exit_price(exchange: str, entry_price: float, quantity: int) -> float:
    """Smallest exit price (rounded UP to the 0.05 tick) whose net P&L after charges is >= 0."""
    tick = 0.05
    px = entry_price
    for _ in range(10_000):
        gross = (px - entry_price) * quantity
        if gross - charges_inr(exchange, entry_price, px, quantity) >= 0:
            return round(px, 2)
        px = round(px + tick, 2)
    raise RuntimeError("breakeven search did not converge")
