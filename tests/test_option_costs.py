"""Tests for the rupee option cost model + its integration into the backtest."""
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
