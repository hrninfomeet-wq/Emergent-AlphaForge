"""Tests for the rupee option cost model + its integration into the backtest."""
import pytest
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.option_costs import CostConfig, round_trip_charges, spread_pts_for_premium  # noqa: E402
from app import option_backtest  # noqa: E402


def test_disabled_config_has_zero_spread():
    cfg = CostConfig()  # enabled defaults False
    assert spread_pts_for_premium(100.0, cfg) == 0.0


def test_spread_is_percent_of_premium_with_floor():
    cfg = CostConfig(enabled=True, spread_pct_of_premium=2.0, spread_min_pts=0.5)
    # 2% of 100 = 2.0 pts (above the 0.5 floor)
    assert spread_pts_for_premium(100.0, cfg) == 2.0
    # 2% of 10 = 0.2 -> floored to 0.5
    assert spread_pts_for_premium(10.0, cfg) == 0.5


def test_flattrade_zero_brokerage_still_has_statutory_charges():
    cfg = CostConfig(enabled=True, brokerage_per_order=0.0)
    ch = round_trip_charges(entry_premium=100.0, exit_premium=120.0, quantity=75, cfg=cfg)
    assert ch["brokerage"] == 0.0
    # STT on sell turnover 120*75=9000 * 0.1% (post-2024-10-01 rate) = 9.0
    assert ch["stt"] == round(9000 * 0.001, 2)
    assert ch["total_charges"] > 0  # statutory charges always apply


def test_default_stt_rate_is_current_post_oct_2024():
    """0.1% on sell premium since 2024-10-01 — keep in sync with the statutory
    schedule; live_friction_profile.STT_OPTIONS_SELL carries the same rate."""
    from app.option_costs import DEFAULT_STT_SELL_RATE
    assert DEFAULT_STT_SELL_RATE == 0.001


def test_brokerage_two_legs_for_paid_broker():
    cfg = CostConfig(enabled=True, brokerage_per_order=20.0)
    ch = round_trip_charges(entry_premium=100.0, exit_premium=100.0, quantity=75, cfg=cfg)
    assert ch["brokerage"] == 40.0  # two legs * 20


def _ce_trade():
    return [{"direction": "CE", "entry_ts": 100_000, "exit_ts": 200_000,
             "entry_price": 24000.0, "exit_price": 24050.0}]


_CONTRACT = [{"instrument_key": "ce-atm", "side": "CE", "strike": 24000.0, "lot_size": 75}]
_CANDLES = pd.DataFrame([
    {"instrument_key": "ce-atm", "ts": 100_000, "close": 100.0, "high": 101.0, "low": 99.0},
    {"instrument_key": "ce-atm", "ts": 200_000, "close": 120.0, "high": 121.0, "low": 119.0},
])


def test_cost_model_reduces_net_pnl_vs_gross():
    """With the cost model enabled, net P&L must be gross minus charges, and the
    spread must widen the fills (entry up, exit down)."""
    result = option_backtest.simulate_paired_option_trades(
        spot_trades=_ce_trade(),
        contracts=_CONTRACT,
        option_candles=_CANDLES,
        underlying="NIFTY",
        moneyness="atm",
        slippage_config={"atm_pts": 0},  # isolate the cost model
        cost_config={"enabled": True, "brokerage_per_order": 0.0,
                     "spread_pct_of_premium": 2.0, "spread_min_pts": 0.0},
    )
    t = result["trades"][0]
    # Entry 100 + half of 2% (1.0) = 101; exit 120 - half of 2.4% (1.2) = 118.8
    assert t["entry_option_price"] == 101.0
    assert t["exit_option_price"] == 118.8
    assert t["gross_option_pnl_value"] > t["option_pnl_value"]  # charges deducted
    assert t["total_charges"] > 0
    assert result["metrics"]["total_charges"] > 0


def test_cost_model_disabled_matches_gross():
    """Default (no cost_config) keeps the legacy gross premium P&L."""
    result = option_backtest.simulate_paired_option_trades(
        spot_trades=_ce_trade(),
        contracts=_CONTRACT,
        option_candles=_CANDLES,
        underlying="NIFTY",
        moneyness="atm",
        slippage_config={"atm_pts": 0},
    )
    t = result["trades"][0]
    assert t["total_charges"] == 0.0
    assert t["option_pnl_value"] == t["gross_option_pnl_value"]
    # 20 pts * 75 = 1500 gross
    assert t["option_pnl_value"] == 1500.0


# ---------------------------------------------------------------------------
# Statutory properties — ported from tests/test_live_friction_profile.py when
# app/live/live_friction_profile.py was deleted (2026-08-07). That module was a
# SECOND charge implementation with zero production callers; its only unique
# value was the segment-aware exchange rate, now carried here. The properties it
# pinned are statutory facts about Indian index options and belong on whichever
# model survives, so they are re-asserted against round_trip_charges.
# ---------------------------------------------------------------------------

from app.option_costs import (  # noqa: E402
    EXCHANGE_TXN_RATE_BFO, EXCHANGE_TXN_RATE_NFO, cost_config_for_exchange,
)

_ON = CostConfig(enabled=True)


def _charges(entry=100.0, exit_=110.0, qty=50, cfg=None):
    return round_trip_charges(entry_premium=entry, exit_premium=exit_,
                              quantity=qty, cfg=cfg or _ON)


def test_stt_is_levied_on_the_sell_leg_only():
    """Finance Act: STT on options is charged on the SELL premium."""
    assert _charges(exit_=0.0)["stt"] == 0.0
    assert _charges(entry=0.0)["stt"] > 0.0


def test_stamp_duty_is_levied_on_the_buy_leg_only():
    """Stamp Act 2019: stamp duty is a BUY-side charge."""
    assert _charges(entry=0.0)["stamp_duty"] == 0.0
    assert _charges(exit_=0.0)["stamp_duty"] > 0.0


def test_exchange_and_sebi_apply_to_both_legs():
    one_leg = _charges(exit_=0.0)
    both = _charges()
    assert both["exchange_txn"] > one_leg["exchange_txn"]
    assert both["sebi"] > one_leg["sebi"]


def test_gst_applies_to_brokerage_exchange_and_sebi_only():
    """NOT to STT or stamp duty — those are taxes, not chargeable services."""
    c = _charges()
    base = c["brokerage"] + c["exchange_txn"] + c["sebi"]
    assert c["gst"] == pytest.approx(base * 0.18, abs=0.02)


def test_total_is_the_sum_of_its_components():
    c = _charges()
    parts = (c["brokerage"] + c["stt"] + c["exchange_txn"]
             + c["sebi"] + c["stamp_duty"] + c["gst"])
    assert c["total_charges"] == pytest.approx(parts, abs=0.02)


def test_segments_differ_only_on_the_exchange_transaction_charge():
    nfo = _charges(cfg=cost_config_for_exchange("NFO"))
    bfo = _charges(cfg=cost_config_for_exchange("BFO"))
    assert nfo["stt"] == bfo["stt"]
    assert nfo["stamp_duty"] == bfo["stamp_duty"]
    assert nfo["exchange_txn"] > bfo["exchange_txn"]
    assert EXCHANGE_TXN_RATE_NFO > EXCHANGE_TXN_RATE_BFO


def test_zero_turnover_costs_nothing():
    c = _charges(entry=0.0, exit_=0.0, qty=0)
    assert c["total_charges"] == 0.0


def test_negative_premiums_are_floored_not_credited():
    """A negative premium is nonsense input; it must never REFUND charges."""
    c = _charges(entry=-100.0, exit_=-110.0)
    assert c["total_charges"] >= 0.0
