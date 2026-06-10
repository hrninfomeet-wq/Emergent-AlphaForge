"""Tests for capital, position sizing, and rupee equity metrics."""
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.portfolio import SizingConfig, size_position, build_rupee_equity_curve  # noqa: E402
from app import option_backtest  # noqa: E402


def test_fixed_lots_mode_uses_user_lots():
    cfg = SizingConfig(enabled=True, mode="fixed_lots", fixed_lots=3, max_lots=10)
    r = size_position(entry_premium=100.0, lot_size=75, stop_level=None, cfg=cfg)
    assert r["lots"] == 3
    assert r["sizing_mode"] == "fixed_lots"


def test_premium_at_risk_sizes_from_stop():
    # Capital 200000, risk 1% = 2000. Entry 100, stop 70 -> risk/unit 30.
    # lot_size 75 -> per-lot risk 2250 -> floor(2000/2250) = 0 -> min 1 lot, risk_exceeded.
    cfg = SizingConfig(enabled=True, mode="premium_at_risk", capital=200_000, risk_per_trade_pct=1.0)
    r = size_position(entry_premium=100.0, lot_size=75, stop_level=70.0, cfg=cfg)
    assert r["lots"] == 1
    assert r["risk_per_unit"] == 30.0
    assert r["risk_exceeded"] is True


def test_premium_at_risk_allows_multiple_lots_when_budget_permits():
    # Risk 5% of 200000 = 10000. Entry 50, stop 40 -> risk/unit 10, lot_size 50 ->
    # per-lot risk 500 -> floor(10000/500)=20 -> capped at max_lots.
    cfg = SizingConfig(enabled=True, mode="premium_at_risk", capital=200_000,
                       risk_per_trade_pct=5.0, max_lots=8)
    r = size_position(entry_premium=50.0, lot_size=50, stop_level=40.0, cfg=cfg)
    assert r["lots"] == 8  # capped
    assert r["risk_exceeded"] is False


def test_premium_at_risk_uses_assumed_stop_when_none():
    # No stop -> assumed 50% of premium = 50 risk/unit.
    cfg = SizingConfig(enabled=True, mode="premium_at_risk", capital=200_000,
                       risk_per_trade_pct=10.0, assumed_stop_pct_of_premium=50.0, max_lots=99)
    r = size_position(entry_premium=100.0, lot_size=25, stop_level=None, cfg=cfg)
    # risk budget 20000, per-unit 50, lot_size 25 -> per-lot 1250 -> floor(20000/1250)=16
    assert r["lots"] == 16
    assert r["risk_per_unit"] == 50.0


def test_lot_size_comes_from_contract_not_config():
    cfg = SizingConfig(enabled=True, mode="premium_at_risk", capital=200_000, risk_per_trade_pct=2.0)
    r1 = size_position(entry_premium=100.0, lot_size=75, stop_level=90.0, cfg=cfg)
    r2 = size_position(entry_premium=100.0, lot_size=15, stop_level=90.0, cfg=cfg)
    # Smaller lot_size => more lots fit in the same risk budget.
    assert r2["lots"] >= r1["lots"]


def test_build_rupee_equity_curve_tracks_capital_and_drawdown():
    trades = [
        {"status": "PAIRED", "option_pnl_value": 1000.0, "option_exit_ts": 1_700_000_000_000},
        {"status": "PAIRED", "option_pnl_value": -500.0, "option_exit_ts": 1_700_086_400_000},
        {"status": "PAIRED", "option_pnl_value": 2000.0, "option_exit_ts": 1_700_172_800_000},
    ]
    p = build_rupee_equity_curve(trades, capital=200_000)
    assert p["starting_capital"] == 200_000
    assert p["ending_equity"] == 202_500.0
    assert p["net_pnl_value"] == 2500.0
    assert p["max_drawdown_value"] <= 0  # had a -500 dip
    assert len(p["curve"]) == 3


def test_build_rupee_equity_ignores_unpaired():
    trades = [
        {"status": "MISSING_CONTRACT"},
        {"status": "PAIRED", "option_pnl_value": 100.0, "option_exit_ts": 1_700_000_000_000},
    ]
    p = build_rupee_equity_curve(trades, capital=100_000)
    assert p["ending_equity"] == 100_100.0
    assert len(p["curve"]) == 1


# ---- integration: sizing flows through the backtest --------------------------

_CONTRACT = [{"instrument_key": "ce-atm", "side": "CE", "strike": 24000.0, "lot_size": 75}]
_CANDLES = pd.DataFrame([
    {"instrument_key": "ce-atm", "ts": 100_000, "close": 100.0, "high": 101.0, "low": 99.0},
    {"instrument_key": "ce-atm", "ts": 200_000, "close": 120.0, "high": 121.0, "low": 119.0},
])


def test_sizing_disabled_keeps_fixed_lots():
    result = option_backtest.simulate_paired_option_trades(
        spot_trades=[{"direction": "CE", "entry_ts": 100_000, "exit_ts": 200_000,
                      "entry_price": 24000.0, "exit_price": 24050.0}],
        contracts=_CONTRACT,
        option_candles=_CANDLES,
        underlying="NIFTY",
        moneyness="atm",
        lots=2,
        slippage_config={"atm_pts": 0},
    )
    t = result["trades"][0]
    assert t["lots"] == 2
    assert result["portfolio"]["starting_capital"] == 200_000  # default capital


def test_sizing_enabled_overrides_lots_from_risk():
    result = option_backtest.simulate_paired_option_trades(
        spot_trades=[{"direction": "CE", "entry_ts": 100_000, "exit_ts": 200_000,
                      "entry_price": 24000.0, "exit_price": 24050.0}],
        contracts=_CONTRACT,
        option_candles=_CANDLES,
        underlying="NIFTY",
        moneyness="atm",
        lots=2,
        slippage_config={"atm_pts": 0},
        sizing_config={"enabled": True, "mode": "premium_at_risk", "capital": 200_000,
                       "risk_per_trade_pct": 5.0, "max_lots": 50},
    )
    t = result["trades"][0]
    # No option stop -> assumed 50% of 100 = 50 risk/unit; budget 10000; lot 75 ->
    # per-lot 3750 -> floor(10000/3750)=2 lots.
    assert t["sizing_mode"] == "premium_at_risk"
    assert t["lots"] == 2
    assert t["risk_per_unit"] == 50.0
