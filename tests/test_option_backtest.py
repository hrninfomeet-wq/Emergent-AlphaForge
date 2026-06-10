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


# ---- option_levels exit mode (item 9) ---------------------------------------


def _ce_trade(entry_ts=100_000, exit_ts=400_000):
    return [{
        "direction": "CE", "entry_ts": entry_ts, "exit_ts": exit_ts,
        "entry_price": 26012.0, "exit_price": 26020.0,
    }]


_CE_CONTRACT = [{"instrument_key": "ce-otm1", "side": "CE", "strike": 26050.0, "lot_size": 50}]


def test_option_levels_exits_on_premium_target_before_spot_exit():
    """Option should exit the moment its premium target is hit, even though the
    spot trade would have stayed open until exit_ts."""
    # Entry candle close 100; second bar spikes to high 145 (target 100+40=140 hit);
    # later bars don't matter because we exit early.
    option_rows = pd.DataFrame([
        {"instrument_key": "ce-otm1", "ts": 100_000, "close": 100.0, "high": 101.0, "low": 99.0},
        {"instrument_key": "ce-otm1", "ts": 200_000, "close": 142.0, "high": 145.0, "low": 100.0},
        {"instrument_key": "ce-otm1", "ts": 400_000, "close": 90.0, "high": 92.0, "low": 88.0},
    ])
    result = option_backtest.simulate_paired_option_trades(
        spot_trades=_ce_trade(),
        contracts=_CE_CONTRACT,
        option_candles=option_rows,
        underlying="NIFTY",
        moneyness="otm1",
        slippage_config={"otm1_pts": 0},  # isolate the level logic
        exit_mode="option_levels",
        option_target_pts=40.0,
        option_stop_pts=30.0,
    )
    trade = result["trades"][0]
    assert trade["status"] == "PAIRED"
    assert trade["option_exit_reason"] == "OPTION_TARGET"
    assert trade["option_target_level"] == 140.0
    assert trade["exit_option_price"] == 140.0      # filled at target level
    assert trade["option_exit_ts"] == 200_000        # exited on the spike bar
    assert trade["option_pnl_pts"] == 40.0


def test_option_levels_exits_on_premium_stop():
    option_rows = pd.DataFrame([
        {"instrument_key": "ce-otm1", "ts": 100_000, "close": 100.0, "high": 101.0, "low": 99.0},
        {"instrument_key": "ce-otm1", "ts": 200_000, "close": 75.0, "high": 100.0, "low": 68.0},
        {"instrument_key": "ce-otm1", "ts": 400_000, "close": 150.0, "high": 160.0, "low": 70.0},
    ])
    result = option_backtest.simulate_paired_option_trades(
        spot_trades=_ce_trade(),
        contracts=_CE_CONTRACT,
        option_candles=option_rows,
        underlying="NIFTY",
        moneyness="otm1",
        slippage_config={"otm1_pts": 0},
        exit_mode="option_levels",
        option_target_pts=40.0,
        option_stop_pts=30.0,  # stop level = 70
    )
    trade = result["trades"][0]
    assert trade["option_exit_reason"] == "OPTION_STOP"
    assert trade["option_stop_level"] == 70.0
    assert trade["exit_option_price"] == 70.0
    assert trade["option_pnl_pts"] == -30.0


def test_option_levels_stop_takes_priority_when_both_hit_in_same_bar():
    """Pessimistic assumption: if a single bar's range spans both stop and
    target, the stop is assumed to fill first."""
    option_rows = pd.DataFrame([
        {"instrument_key": "ce-otm1", "ts": 100_000, "close": 100.0, "high": 101.0, "low": 99.0},
        # This bar's range (low 65, high 150) contains both stop(70) and target(140).
        {"instrument_key": "ce-otm1", "ts": 200_000, "close": 120.0, "high": 150.0, "low": 65.0},
    ])
    result = option_backtest.simulate_paired_option_trades(
        spot_trades=_ce_trade(exit_ts=200_000),
        contracts=_CE_CONTRACT,
        option_candles=option_rows,
        underlying="NIFTY",
        moneyness="otm1",
        slippage_config={"otm1_pts": 0},
        exit_mode="option_levels",
        option_target_pts=40.0,
        option_stop_pts=30.0,
    )
    assert result["trades"][0]["option_exit_reason"] == "OPTION_STOP"


def test_option_levels_falls_back_to_signal_exit_when_no_level_hit():
    """Neither target nor stop trigger -> close at the spot signal's exit bar."""
    option_rows = pd.DataFrame([
        {"instrument_key": "ce-otm1", "ts": 100_000, "close": 100.0, "high": 101.0, "low": 99.0},
        {"instrument_key": "ce-otm1", "ts": 200_000, "close": 110.0, "high": 115.0, "low": 95.0},
        {"instrument_key": "ce-otm1", "ts": 400_000, "close": 118.0, "high": 120.0, "low": 108.0},
    ])
    result = option_backtest.simulate_paired_option_trades(
        spot_trades=_ce_trade(),
        contracts=_CE_CONTRACT,
        option_candles=option_rows,
        underlying="NIFTY",
        moneyness="otm1",
        slippage_config={"otm1_pts": 0},
        exit_mode="option_levels",
        option_target_pts=40.0,   # target 140 never reached (max high 120)
        option_stop_pts=30.0,     # stop 70 never reached (min low 95)
    )
    trade = result["trades"][0]
    assert trade["option_exit_reason"] == "OPTION_SIGNAL_EXIT"
    assert trade["option_exit_ts"] == 400_000
    assert trade["exit_option_price"] == 118.0


def test_option_levels_percent_targets_resolve_against_entry():
    option_rows = pd.DataFrame([
        {"instrument_key": "ce-otm1", "ts": 100_000, "close": 100.0, "high": 101.0, "low": 99.0},
        {"instrument_key": "ce-otm1", "ts": 200_000, "close": 130.0, "high": 135.0, "low": 100.0},
    ])
    result = option_backtest.simulate_paired_option_trades(
        spot_trades=_ce_trade(exit_ts=200_000),
        contracts=_CE_CONTRACT,
        option_candles=option_rows,
        underlying="NIFTY",
        moneyness="otm1",
        slippage_config={"otm1_pts": 0},
        exit_mode="option_levels",
        option_target_pct=25.0,  # 100 * 1.25 = 125 target
        option_stop_pct=20.0,    # 100 * 0.80 = 80 stop
    )
    trade = result["trades"][0]
    assert trade["option_target_level"] == 125.0
    assert trade["option_stop_level"] == 80.0
    assert trade["option_exit_reason"] == "OPTION_TARGET"
    assert trade["exit_option_price"] == 125.0


def test_spot_exit_mode_is_default_and_unchanged():
    """Default behavior (no exit_mode) must remain the spot-mirroring exit."""
    option_rows = pd.DataFrame([
        {"instrument_key": "ce-otm1", "ts": 100_000, "close": 100.0, "high": 101.0, "low": 99.0},
        {"instrument_key": "ce-otm1", "ts": 200_000, "close": 200.0, "high": 250.0, "low": 100.0},
        {"instrument_key": "ce-otm1", "ts": 400_000, "close": 118.0, "high": 120.0, "low": 108.0},
    ])
    result = option_backtest.simulate_paired_option_trades(
        spot_trades=_ce_trade(),
        contracts=_CE_CONTRACT,
        option_candles=option_rows,
        underlying="NIFTY",
        moneyness="otm1",
        slippage_config={"otm1_pts": 0},
    )
    trade = result["trades"][0]
    # Exits at the spot exit candle (ts 400_000), ignoring the 250 spike.
    assert trade["option_exit_reason"] == "SPOT_EXIT"
    assert trade["exit_option_price"] == 118.0
    assert result["exit_mode"] == "spot_exit"


# ---- missing-candle diagnostics ---------------------------------------------


def test_missing_entry_candle_reports_no_candles_for_strike():
    """When the resolved strike has zero candles, miss_reason flags the strike
    was never fetched (the real cause behind the user's MISSING_ENTRY_CANDLE)."""
    spot_trades = [{"direction": "CE", "entry_ts": 100_000, "exit_ts": 160_000, "entry_price": 22800.0}]
    # Contract exists (ATM for 22800 is 22800) but NO option candles are provided.
    contracts = [{"instrument_key": "ce-atm", "side": "CE", "strike": 22800.0, "lot_size": 50}]
    result = option_backtest.simulate_paired_option_trades(
        spot_trades=spot_trades,
        contracts=contracts,
        option_candles=pd.DataFrame(),  # nothing stored for this strike
        underlying="NIFTY",
        moneyness="atm",
    )
    trade = result["trades"][0]
    assert trade["status"] == "MISSING_ENTRY_CANDLE"
    assert "no_candles_for_strike" in trade["miss_reason"]


def test_missing_entry_candle_reports_no_candle_near_entry():
    """Candles exist for the strike but none within the entry age window."""
    spot_trades = [{"direction": "CE", "entry_ts": 10_000_000, "exit_ts": 10_060_000, "entry_price": 22800.0}]
    contracts = [{"instrument_key": "ce-atm", "side": "CE", "strike": 22800.0, "lot_size": 50}]
    # Candle exists but far in the past (well outside the 120s entry window).
    option_rows = pd.DataFrame([
        {"instrument_key": "ce-atm", "ts": 100_000, "close": 100.0, "high": 101.0, "low": 99.0},
    ])
    result = option_backtest.simulate_paired_option_trades(
        spot_trades=spot_trades,
        contracts=contracts,
        option_candles=option_rows,
        underlying="NIFTY",
        moneyness="atm",
    )
    trade = result["trades"][0]
    assert trade["status"] == "MISSING_ENTRY_CANDLE"
    assert "no_candle_near_entry" in trade["miss_reason"]
