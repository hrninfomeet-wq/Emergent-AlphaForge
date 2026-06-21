"""Live-friction parity: the live paper path must book fills and net P&L with
the SAME model the option backtest uses (app.live_friction).

Trading-critical invariants:
- fill_premium slips BUY up / SELL down by EITHER the half %-spread (when the
  cost model configures one) OR the point-slippage proxy — never both, since both
  model the same bid-ask cost;
- a disabled friction config leaves the live close GROSS (legacy behavior);
- an enabled config makes the live NET P&L equal the backtest's net P&L for
  identical inputs (slippage + bid-ask spread + statutory charges) — so a
  deployed strategy's forward result mirrors the backtest that justified it.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from app.live_friction import (  # noqa: E402
    FrictionConfig,
    apply_entry_friction,
    close_economics,
    fill_premium,
)
from app.option_costs import round_trip_charges  # noqa: E402
from app.paper_trading import close_trade, paper_trade_from_signal  # noqa: E402


# A plain weekday minute, well before any expiry-tail window, with no expiry set
# so the expiry-tail multiplier never applies (keeps the arithmetic deterministic).
TS = 1748332200000
NO_TAIL_EXPIRY = None

# A ts INSIDE the expiry-day tail window (16:50 IST on 2025-05-27), used to verify
# the %-spread inherits the slippage model's expiry-tail widening multiplier.
TAIL_TS = 1748344800000
TAIL_EXPIRY = "2025-05-27"


def _friction(enabled=True, **over):
    data = {
        "enabled": enabled,
        "moneyness": "atm",
        "expiry_iso": None,
        "slippage": {"atm_pts": 0.5},
        "costs": {"enabled": True, "spread_pct_of_premium": 1.0},
    }
    data.update(over)
    return FrictionConfig.from_dict(data)


# --------------------------------------------------------------------------- #
# fill_premium — the single source of the fill price
# --------------------------------------------------------------------------- #

def test_fill_premium_buy_pays_more_sell_receives_less():
    cfg = FrictionConfig()  # default slippage (atm 0.5), costs disabled (no spread)
    buy = fill_premium(raw_premium=100.0, side="BUY", moneyness="atm", ts_ms=TS,
                       expiry_iso=NO_TAIL_EXPIRY, slippage_cfg=cfg.slippage, cost_cfg=cfg.costs)
    sell = fill_premium(raw_premium=100.0, side="SELL", moneyness="atm", ts_ms=TS,
                        expiry_iso=NO_TAIL_EXPIRY, slippage_cfg=cfg.slippage, cost_cfg=cfg.costs)
    assert buy["price"] == 100.5 and sell["price"] == 99.5
    assert buy["bucket"] == "atm" and buy["spread_pts"] == 0.0  # costs disabled → no spread


def test_fill_premium_spread_replaces_slippage_when_costs_enabled():
    # EITHER/OR: when the %-spread cost model is active it REPLACES the
    # point-slippage proxy (both model the same bid-ask cost — no double-count).
    f = _friction()  # costs enabled, 1% spread, atm 0.5 slippage
    buy = fill_premium(raw_premium=100.0, side="BUY", moneyness="atm", ts_ms=TS,
                       expiry_iso=NO_TAIL_EXPIRY, slippage_cfg=f.slippage, cost_cfg=f.costs)
    # 1% of 100 = 1.0 full spread → 0.5 half-spread per side; point-slippage dropped.
    assert buy["spread_pts"] == 0.5
    assert buy["slippage_pts"] == 0.0
    assert buy["price"] == 100.5  # 100 + 0.5 half-spread only (no 0.5 slippage on top)


def test_fill_premium_spread_inherits_expiry_tail_multiplier():
    # On expiry day in the tail window the %-spread inherits the slippage model's
    # 2x expiry-tail multiplier, so the expiry-day spread blow-out is not lost
    # when the spread model replaces point-slippage.
    f = _friction(expiry_iso=TAIL_EXPIRY)
    buy = fill_premium(raw_premium=100.0, side="BUY", moneyness="atm", ts_ms=TAIL_TS,
                       expiry_iso=TAIL_EXPIRY, slippage_cfg=f.slippage, cost_cfg=f.costs)
    assert buy["tail"] is True
    assert buy["slippage_pts"] == 0.0
    assert buy["spread_pts"] == 1.0   # 0.5 half-spread x 2 expiry-tail multiplier
    assert buy["price"] == 101.0      # 100 + 1.0 widened half-spread


# --------------------------------------------------------------------------- #
# FrictionConfig
# --------------------------------------------------------------------------- #

def test_friction_config_roundtrip_and_defaults():
    assert FrictionConfig.from_dict(None).enabled is False
    f = _friction()
    d = f.to_dict()
    assert d["enabled"] is True and d["moneyness"] == "atm"
    assert d["slippage"]["atm_pts"] == 0.5
    assert d["costs"]["enabled"] is True and d["costs"]["spread_pct_of_premium"] == 1.0
    # Round-trips without loss.
    assert FrictionConfig.from_dict(d).to_dict() == d


# --------------------------------------------------------------------------- #
# close_economics — gross vs net
# --------------------------------------------------------------------------- #

def test_close_economics_disabled_is_pure_gross():
    econ = close_economics(raw_exit_premium=120.0, entry_price=100.0, raw_entry_price=100.0,
                           quantity=75, friction=FrictionConfig(), ts_ms=TS)
    assert econ["realized_pnl"] == econ["gross_realized_pnl"] == round((120.0 - 100.0) * 75, 2)
    assert econ["total_charges"] == 0.0 and econ["friction_cost"] == 0.0 and econ["charges"] is None


def test_close_economics_enabled_nets_below_gross_with_charges():
    f = _friction()
    entry_fill = apply_entry_friction(100.0, f, ts_ms=TS)["price"]  # slipped BUY entry
    econ = close_economics(raw_exit_premium=120.0, entry_price=entry_fill, raw_entry_price=100.0,
                           quantity=75, friction=f, ts_ms=TS)
    assert econ["gross_realized_pnl"] == round((120.0 - 100.0) * 75, 2)  # raw move, no friction
    assert econ["realized_pnl"] < econ["gross_realized_pnl"]             # friction eats into it
    assert econ["friction_cost"] == round(econ["gross_realized_pnl"] - econ["realized_pnl"], 2)
    assert econ["total_charges"] > 0.0 and econ["charges"] is not None
    # Spread model is active here, so it replaces point-slippage: the friction is
    # carried by the half-spread, not the slippage bucket. The half-spread is
    # premium-relative — 1% of the 120 exit premium / 2 = 0.6.
    assert econ["exit_slippage_pts"] == 0.0
    assert econ["exit_spread_pts"] == 0.6


# --------------------------------------------------------------------------- #
# Sim ↔ Live net-P&L parity — the core guarantee
# --------------------------------------------------------------------------- #

def test_sim_live_net_pnl_parity():
    """For identical inputs, the live close path must produce the same NET P&L
    and exit fill as the backtest's inline computation (both go through
    fill_premium + round_trip_charges)."""
    f = _friction()
    raw_entry, raw_exit, qty = 100.0, 130.0, 75

    # --- what the backtest does (option_backtest.simulate_paired_option_trades) ---
    entry_fill = fill_premium(raw_premium=raw_entry, side="BUY", moneyness=f.moneyness, ts_ms=TS,
                              expiry_iso=f.expiry_iso, slippage_cfg=f.slippage, cost_cfg=f.costs)["price"]
    sim_exit_fill = fill_premium(raw_premium=raw_exit, side="SELL", moneyness=f.moneyness, ts_ms=TS,
                                 expiry_iso=f.expiry_iso, slippage_cfg=f.slippage, cost_cfg=f.costs)["price"]
    sim_charges = round_trip_charges(entry_premium=entry_fill, exit_premium=sim_exit_fill,
                                     quantity=qty, cfg=f.costs)["total_charges"]
    sim_net = round((sim_exit_fill - entry_fill) * qty - sim_charges, 2)

    # --- what the live path does ---
    live = close_economics(raw_exit_premium=raw_exit, entry_price=entry_fill,
                           raw_entry_price=raw_entry, quantity=qty, friction=f, ts_ms=TS)

    assert live["realized_pnl"] == sim_net
    assert live["exit_fill_price"] == round(sim_exit_fill, 3)


# --------------------------------------------------------------------------- #
# close_trade end-to-end (the live booking path)
# --------------------------------------------------------------------------- #

def _signal(lot_size=75):
    return {
        "id": "sig-1", "instrument": "NIFTY", "direction": "CE", "strategy_id": "x",
        "option_contract": {"instrument_key": "NSE_FO|TEST|CE", "trading_symbol": "T",
                            "lot_size": lot_size, "strike": 23950.0, "side": "CE"},
    }


def test_close_trade_without_friction_is_unchanged_gross():
    trade = paper_trade_from_signal(_signal(), lots=1, entry_price=100.0)
    closed = close_trade(trade, exit_price=120.0, reason="manual")
    assert closed["realized_pnl"] == round((120.0 - 100.0) * 75, 2)
    assert closed["exit_price"] == 120.0
    assert "charges" not in closed and closed["total_charges"] == 0.0


def test_close_trade_with_friction_books_net_and_records_gross():
    f = _friction()
    entry_fill = apply_entry_friction(100.0, f, ts_ms=TS)["price"]
    trade = paper_trade_from_signal(_signal(), lots=1, entry_price=entry_fill,
                                    raw_entry_price=100.0, friction=f.to_dict())
    closed = close_trade(trade, exit_price=130.0, reason="OPTION_TARGET")
    assert closed["gross_realized_pnl"] == round((130.0 - 100.0) * 75, 2)
    assert closed["realized_pnl"] < closed["gross_realized_pnl"]
    assert closed["friction_cost"] > 0.0 and closed["total_charges"] > 0.0
    assert "charges" in closed
    # SELL fill is below the raw mark, so the recorded exit price is too.
    assert closed["exit_price"] < 130.0 and closed["raw_exit_price"] == 130.0
