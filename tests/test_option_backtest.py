import sys
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app import option_backtest  # noqa: E402


def test_simulate_paired_option_trades_selects_contract_and_uses_option_premium_pnl():
    spot_trades = [
        {
            "direction": "CE",
            "entry_ts": 100_000,
            "exit_ts": 220_000,
            "entry_price": 26012.0,
            "exit_price": 26045.0,
            "entry_datetime": "2026-05-22T09:30:00+05:30",
            "exit_datetime": "2026-05-22T09:32:00+05:30",
        }
    ]
    contracts = [
        {"instrument_key": "ce-otm1", "side": "CE", "strike": 26050.0, "lot_size": 65, "trading_symbol": "NIFTY 26050 CE"},
        {"instrument_key": "pe-otm1", "side": "PE", "strike": 25950.0, "lot_size": 65, "trading_symbol": "NIFTY 25950 PE"},
    ]
    option_rows = pd.DataFrame([
        {"instrument_key": "ce-otm1", "ts": 90_000, "close": 100.0, "high": 102.0, "low": 99.0},
        {"instrument_key": "ce-otm1", "ts": 160_000, "close": 111.0, "high": 116.0, "low": 97.0},
        {"instrument_key": "ce-otm1", "ts": 220_000, "close": 118.0, "high": 119.0, "low": 110.0},
    ])

    result = option_backtest.simulate_paired_option_trades(
        spot_trades=spot_trades,
        contracts=contracts,
        option_candles=option_rows,
        underlying="NIFTY",
        moneyness="otm1",
        lots=1,
    )

    trade = result["trades"][0]
    assert trade["status"] == "PAIRED"
    assert trade["instrument_key"] == "ce-otm1"
    # With default OTM1 slippage = 1.0 pt: BUY pays 100.0+1.0=101.0, SELL receives 118.0-1.0=117.0
    # Resulting pnl = 117.0 - 101.0 = 16.0 pts * 65 lot_size = 1040.0
    assert trade["raw_entry_option_price"] == 100.0
    assert trade["raw_exit_option_price"] == 118.0
    assert trade["entry_option_price"] == 101.0
    assert trade["exit_option_price"] == 117.0
    assert trade["entry_slippage_pts"] == 1.0
    assert trade["exit_slippage_pts"] == 1.0
    assert trade["slippage_bucket"] == "otm1"
    assert trade["expiry_tail_applied"] is False
    assert trade["option_pnl_pts"] == 16.0
    assert trade["option_pnl_value"] == 1040.0
    # MFE/MAE are computed off slippage-adjusted entry: 119.0 - 101.0 = 18.0 high; 101.0 - 97.0 = 4.0 low
    assert trade["option_mfe_pts"] == 18.0
    assert trade["option_mae_pts"] == 4.0
    assert result["metrics"]["paired_trade_count"] == 1
    assert result["metrics"]["total_option_pnl_value"] == 1040.0


def test_simulate_paired_option_trades_disables_slippage_when_pts_zero():
    """Setting slippage_config to zero pts disables it - useful for math-pure backtests."""
    spot_trades = [
        {
            "direction": "CE",
            "entry_ts": 100_000,
            "exit_ts": 220_000,
            "entry_price": 26012.0,
            "exit_price": 26045.0,
        }
    ]
    contracts = [
        {"instrument_key": "ce-otm1", "side": "CE", "strike": 26050.0, "lot_size": 65, "trading_symbol": "NIFTY 26050 CE"},
        {"instrument_key": "pe-otm1", "side": "PE", "strike": 25950.0, "lot_size": 65, "trading_symbol": "NIFTY 25950 PE"},
    ]
    option_rows = pd.DataFrame([
        {"instrument_key": "ce-otm1", "ts": 90_000, "close": 100.0, "high": 102.0, "low": 99.0},
        {"instrument_key": "ce-otm1", "ts": 220_000, "close": 118.0, "high": 119.0, "low": 110.0},
    ])
    result = option_backtest.simulate_paired_option_trades(
        spot_trades=spot_trades,
        contracts=contracts,
        option_candles=option_rows,
        underlying="NIFTY",
        moneyness="otm1",
        lots=1,
        slippage_config={"atm_pts": 0, "otm1_pts": 0, "itm1_pts": 0, "otm2_plus_pts": 0, "itm2_plus_pts": 0},
    )
    trade = result["trades"][0]
    assert trade["entry_option_price"] == 100.0
    assert trade["exit_option_price"] == 118.0
    assert trade["option_pnl_pts"] == 18.0
    assert trade["option_pnl_value"] == 1170.0


def test_simulate_paired_option_trades_reports_missing_contracts():
    result = option_backtest.simulate_paired_option_trades(
        spot_trades=[{"direction": "PE", "entry_ts": 100_000, "exit_ts": 160_000, "entry_price": 26012.0}],
        contracts=[],
        option_candles=pd.DataFrame(),
        underlying="NIFTY",
    )

    assert result["trades"][0]["status"] == "MISSING_CONTRACT"
    assert result["coverage"]["missing_contract"] == 1
    assert result["metrics"]["paired_trade_count"] == 0


def test_simulate_paired_option_trades_resolves_next_expiry_per_trade():
    spot_trades = [
        {"direction": "CE", "entry_ts": 100_000, "exit_ts": 120_000, "entry_price": 26012.0},
        {"direction": "CE", "entry_ts": 900_000, "exit_ts": 920_000, "entry_price": 26012.0},
    ]
    contracts = [
        {"instrument_key": "week1-ce", "expiry_date": "2026-05-26", "side": "CE", "strike": 26050.0, "lot_size": 65},
        {"instrument_key": "week2-ce", "expiry_date": "2026-06-02", "side": "CE", "strike": 26050.0, "lot_size": 65},
    ]
    option_rows = pd.DataFrame([
        {"instrument_key": "week1-ce", "ts": 100_000, "close": 100.0, "high": 101.0, "low": 99.0},
        {"instrument_key": "week1-ce", "ts": 120_000, "close": 110.0, "high": 112.0, "low": 100.0},
        {"instrument_key": "week2-ce", "ts": 900_000, "close": 200.0, "high": 201.0, "low": 199.0},
        {"instrument_key": "week2-ce", "ts": 920_000, "close": 230.0, "high": 232.0, "low": 200.0},
    ])

    result = option_backtest.simulate_paired_option_trades(
        spot_trades=spot_trades,
        contracts=contracts,
        option_candles=option_rows,
        underlying="NIFTY",
        moneyness="otm1",
        expiry_by_trade={0: "2026-05-26", 1: "2026-06-02"},
    )

    assert [t["instrument_key"] for t in result["trades"]] == ["week1-ce", "week2-ce"]
    assert [t["expiry_date"] for t in result["trades"]] == ["2026-05-26", "2026-06-02"]
    assert result["metrics"]["paired_trade_count"] == 2


def test_simulate_paired_option_trades_respects_explicit_expiry_override():
    spot_trades = [{"direction": "CE", "entry_ts": 100_000, "exit_ts": 120_000, "entry_price": 26012.0}]
    contracts = [
        {"instrument_key": "week1-ce", "expiry_date": "2026-05-26", "side": "CE", "strike": 26050.0, "lot_size": 65},
        {"instrument_key": "week2-ce", "expiry_date": "2026-06-02", "side": "CE", "strike": 26050.0, "lot_size": 65},
    ]
    option_rows = pd.DataFrame([
        {"instrument_key": "week2-ce", "ts": 100_000, "close": 200.0, "high": 201.0, "low": 199.0},
        {"instrument_key": "week2-ce", "ts": 120_000, "close": 230.0, "high": 232.0, "low": 200.0},
    ])

    result = option_backtest.simulate_paired_option_trades(
        spot_trades=spot_trades,
        contracts=contracts,
        option_candles=option_rows,
        underlying="NIFTY",
        moneyness="otm1",
        expiry_by_trade={0: "2026-05-26"},
        fixed_expiry_date="2026-06-02",
    )

    assert result["trades"][0]["instrument_key"] == "week2-ce"
    assert result["trades"][0]["expiry_date"] == "2026-06-02"
