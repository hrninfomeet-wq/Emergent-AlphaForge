import sys
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app import option_backtest  # noqa: E402


def test_option_lookup_never_uses_a_bar_that_ends_after_the_decision():
    rows = pd.DataFrame([
        {"ts": 100_000, "bar_end_ts": 170_001, "close": 101.0},
    ])
    # A 100_000-labelled one-minute spot bar is decided at 160_000.
    assert option_backtest._candle_at_or_before(rows, 100_000, 120_000) is None


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


def test_prebuilt_candles_by_key_is_byte_identical_to_internal_build():
    """The optimizer survival + exit-control search hot loops pre-build
    candles_by_key ONCE and pass it through (avoiding ~150s of per-sim
    copy+sort+groupby). This pins the byte-identical contract the perf fix
    relies on: passing candles_by_key == letting simulate build it."""
    spot_trades = [
        {"direction": "CE", "entry_ts": 100_000, "exit_ts": 220_000,
         "entry_price": 26012.0, "exit_price": 26045.0},
        {"direction": "PE", "entry_ts": 300_000, "exit_ts": 420_000,
         "entry_price": 26040.0, "exit_price": 25990.0},
    ]
    contracts = [
        {"instrument_key": "ce-otm1", "side": "CE", "strike": 26050.0, "lot_size": 65},
        {"instrument_key": "pe-otm1", "side": "PE", "strike": 25950.0, "lot_size": 65},
    ]
    option_rows = pd.DataFrame([
        {"instrument_key": "ce-otm1", "ts": 90_000, "close": 100.0, "high": 102.0, "low": 99.0},
        {"instrument_key": "ce-otm1", "ts": 160_000, "close": 111.0, "high": 116.0, "low": 97.0},
        {"instrument_key": "ce-otm1", "ts": 220_000, "close": 118.0, "high": 119.0, "low": 110.0},
        {"instrument_key": "pe-otm1", "ts": 290_000, "close": 90.0, "high": 92.0, "low": 88.0},
        {"instrument_key": "pe-otm1", "ts": 360_000, "close": 101.0, "high": 106.0, "low": 87.0},
        {"instrument_key": "pe-otm1", "ts": 420_000, "close": 108.0, "high": 109.0, "low": 100.0},
    ])
    kw = dict(spot_trades=spot_trades, contracts=contracts, option_candles=option_rows,
              underlying="NIFTY", moneyness="otm1", lots=1)
    internal = option_backtest.simulate_paired_option_trades(**kw)
    prebuilt = option_backtest.simulate_paired_option_trades(
        candles_by_key=option_backtest.build_candles_by_key(option_rows), **kw)
    assert internal["trades"] == prebuilt["trades"]
    assert internal["metrics"] == prebuilt["metrics"]


def test_reused_exchange_token_does_not_cross_pair_between_expiries():
    """Regression: token 52526 represented unrelated contracts in two years."""
    spot_trades = [{
        "direction": "CE", "entry_ts": 100_000, "exit_ts": 220_000,
        "entry_price": 57900.0, "exit_price": 57950.0,
    }]
    contracts = [
        {"instrument_key": "NSE_FO|52526", "underlying": "NIFTY", "side": "PE",
         "strike": 23850.0, "expiry_date": "2025-01-02", "lot_size": 65},
        {"instrument_key": "NSE_FO|52526", "underlying": "NIFTY", "side": "CE",
         "strike": 57900.0, "expiry_date": "2026-03-30", "lot_size": 65},
    ]
    option_rows = pd.DataFrame([
        # Correct expiry is deliberately first; canonical-token grouping would
        # let the unrelated later row win on a duplicate timestamp.
        {"instrument_key": "NSE_FO|52526", "expiry_date": "2026-03-30",
         "ts": 90_000, "close": 100.0, "high": 101.0, "low": 99.0},
        {"instrument_key": "NSE_FO|52526", "expiry_date": "2025-01-02",
         "ts": 90_000, "close": 999.0, "high": 1000.0, "low": 998.0},
        {"instrument_key": "NSE_FO|52526", "expiry_date": "2026-03-30",
         "ts": 220_000, "close": 110.0, "high": 111.0, "low": 109.0},
        {"instrument_key": "NSE_FO|52526", "expiry_date": "2025-01-02",
         "ts": 220_000, "close": 888.0, "high": 889.0, "low": 887.0},
    ])
    result = option_backtest.simulate_paired_option_trades(
        spot_trades=spot_trades, contracts=contracts,
        option_candles=option_rows, underlying="NIFTY", moneyness="atm",
        fixed_expiry_date="2026-03-30", lots=1,
        slippage_config={"atm_pts": 0},
    )
    trade = result["trades"][0]
    assert trade["status"] == "PAIRED"
    assert trade["raw_entry_option_price"] == 100.0
    assert trade["raw_exit_option_price"] == 110.0


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


def test_pairing_bridges_dated_contract_key_to_canonical_candle_key():
    """Root cause #3 regression (2026-06-12): the same contract can be selected
    via an expired-sourced metadata doc (dated 3-part key) while its candles
    are stored under the canonical 2-part broker key. Pairing must bridge the
    two forms — before the fix this produced MISSING_ENTRY_CANDLE even though
    the candles existed."""
    spot_trades = [{
        "direction": "CE",
        "entry_ts": 100_000,
        "exit_ts": 220_000,
        "entry_price": 26012.0,
        "exit_price": 26045.0,
        "entry_datetime": "2026-05-22T09:30:00+05:30",
        "exit_datetime": "2026-05-22T09:32:00+05:30",
    }]
    # Contract metadata carries the DATED key (expired-backfill source)…
    contracts = [
        {"instrument_key": "NSE_FO|72171|26-05-2026", "side": "CE", "strike": 26050.0,
         "lot_size": 65, "trading_symbol": "NIFTY 26050 CE"},
    ]
    # …while the candles are stored under the canonical 2-part key.
    option_rows = pd.DataFrame([
        {"instrument_key": "NSE_FO|72171", "ts": 90_000, "close": 100.0, "high": 102.0, "low": 99.0},
        {"instrument_key": "NSE_FO|72171", "ts": 220_000, "close": 118.0, "high": 119.0, "low": 110.0},
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
    assert result["metrics"]["paired_trade_count"] == 1


def test_pairing_merges_candles_split_across_key_forms():
    """Candles for ONE contract split across both key forms (legacy + canonical)
    must merge into a single lookup group."""
    spot_trades = [{
        "direction": "CE",
        "entry_ts": 100_000,
        "exit_ts": 220_000,
        "entry_price": 26012.0,
        "exit_price": 26045.0,
        "entry_datetime": "2026-05-22T09:30:00+05:30",
        "exit_datetime": "2026-05-22T09:32:00+05:30",
    }]
    contracts = [
        {"instrument_key": "NSE_FO|72171", "side": "CE", "strike": 26050.0,
         "lot_size": 65, "trading_symbol": "NIFTY 26050 CE"},
    ]
    option_rows = pd.DataFrame([
        {"instrument_key": "NSE_FO|72171|26-05-2026", "ts": 90_000, "close": 100.0, "high": 102.0, "low": 99.0},
        {"instrument_key": "NSE_FO|72171", "ts": 220_000, "close": 118.0, "high": 119.0, "low": 110.0},
    ])
    result = option_backtest.simulate_paired_option_trades(
        spot_trades=spot_trades, contracts=contracts, option_candles=option_rows,
        underlying="NIFTY", moneyness="otm1", lots=1,
    )
    assert result["trades"][0]["status"] == "PAIRED"


from app.option_backtest import _walk_option_exit, simulate_paired_option_trades
from app.exit_controls import ExitControlsConfig


def _bars(rows):
    return pd.DataFrame(rows)


def test_walk_trailing_stop_exits_on_giveback_not_lookahead():
    # entry 100 at ts0. Bars rise to 200 then pull back. Trail distance 0.25.
    # The bar that prints the 200 high must NOT be stopped at 150 within itself.
    cfg = ExitControlsConfig.from_dict({"enabled": True, "unit": "pct",
                                        "trailing": {"activation": 0.10, "distance": 0.25}})
    rows = _bars([
        {"ts": 1, "open": 100, "high": 120, "low": 100, "close": 120},
        {"ts": 2, "open": 120, "high": 200, "low": 118, "close": 190},  # sets peak 200; low 118 must NOT stop
        {"ts": 3, "open": 150, "high": 152, "low": 140, "close": 145},  # trail=200*0.75=150; low 140 <= 150 -> STOP
    ])
    out = _walk_option_exit(rows, entry_ts=0, backstop_ts=3, entry_price=100.0,
                            target_level=None, stop_level=None, exit_cfg=cfg)
    assert out["exit_reason"] == "OPTION_TRAIL_STOP"
    assert out["exit_ts"] == 3
    assert out["exit_price"] == 150.0  # filled at the trail level (no gap)


def test_walk_trailing_gap_fills_at_open():
    cfg = ExitControlsConfig.from_dict({"enabled": True, "unit": "pct",
                                        "trailing": {"activation": 0.10, "distance": 0.25}})
    rows = _bars([
        {"ts": 1, "open": 100, "high": 200, "low": 100, "close": 200},  # peak 200
        {"ts": 2, "open": 130, "high": 131, "low": 120, "close": 125},  # opens 130 < trail 150 -> gap fill at 130
    ])
    out = _walk_option_exit(rows, entry_ts=0, backstop_ts=2, entry_price=100.0,
                            target_level=None, stop_level=None, exit_cfg=cfg)
    assert out["exit_reason"] == "OPTION_TRAIL_STOP"
    assert out["exit_price"] == 130.0


def test_walk_disabled_cfg_is_legacy_behavior():
    # No exit_cfg -> the old fixed-level walk; no premium levels -> signal exit at last bar.
    rows = _bars([{"ts": 1, "open": 100, "high": 110, "low": 95, "close": 108}])
    out = _walk_option_exit(rows, entry_ts=0, backstop_ts=1, entry_price=100.0,
                            target_level=None, stop_level=None)
    assert out["exit_reason"] == "OPTION_SIGNAL_EXIT"


def test_exit_controls_conduit_changes_rupee_curve():
    # CONDUIT: proves exit_controls flows simulate_paired_option_trades -> _walk_option_exit
    # and changes the realized ₹ vs disabled, on a crafted premium series.
    day_ms = 1_700_000_000_000
    spot = [{"direction": "CE", "entry_ts": day_ms, "exit_ts": day_ms + 240000,
             "entry_price": 100.0, "exit_price": 100.0, "entry_datetime": "", "exit_datetime": "",
             "regime": "", "ist_time": ""}]
    contracts = [{"instrument_key": "NSE_FO|OPT", "underlying": "NIFTY", "expiry_date": "2023-11-16",
                  "strike": 100, "side": "CE", "lot_size": 50, "trading_symbol": "NIFTY-CE-100", "atm": 100}]
    candles = _bars([
        {"instrument_key": "NSE_FO|OPT", "ts": day_ms, "open": 10, "high": 10, "low": 10, "close": 10},
        {"instrument_key": "NSE_FO|OPT", "ts": day_ms + 60000, "open": 16, "high": 16, "low": 16, "close": 16},
        {"instrument_key": "NSE_FO|OPT", "ts": day_ms + 120000, "open": 12, "high": 12, "low": 12, "close": 12},
        {"instrument_key": "NSE_FO|OPT", "ts": day_ms + 240000, "open": 14, "high": 14, "low": 14, "close": 14},
    ])
    common = dict(spot_trades=spot, contracts=contracts, option_candles=candles, underlying="NIFTY",
                  moneyness="atm", fixed_expiry_date="2023-11-16", exit_mode="option_levels",
                  option_stop_pct=50.0, option_target_pct=100.0)
    disabled = simulate_paired_option_trades(**common)
    enabled = simulate_paired_option_trades(**common, exit_controls={
        "enabled": True, "unit": "pct", "trailing": {"activation": 0.10, "distance": 0.25}})
    dv = disabled["metrics"]["total_option_pnl_value"]
    ev = enabled["metrics"]["total_option_pnl_value"]
    assert dv != ev   # the trailing overlay changed the realized ₹ -> the kwarg reached the walk


def test_walk_breakeven_stop_tagged():
    # Breakeven is tagged only when it ratchets ABOVE a base stop. entry 100, base stop 80,
    # breakeven trigger 0.20 lock 0.0 -> once running_max>=120, stop -> entry 100.
    cfg = ExitControlsConfig.from_dict({"enabled": True, "unit": "pct",
                                        "breakeven": {"trigger": 0.20, "lock": 0.0}})
    rows = _bars([
        {"ts": 1, "open": 100, "high": 130, "low": 110, "close": 125},  # peak->130 (check uses prior rm=100)
        {"ts": 2, "open": 105, "high": 106, "low": 95, "close": 100},    # rm=130>=120 -> BE stop=100; low 95<=100 STOP
    ])
    out = _walk_option_exit(rows, entry_ts=0, backstop_ts=2, entry_price=100.0,
                            target_level=None, stop_level=80.0, exit_cfg=cfg)
    assert out["exit_reason"] == "OPTION_BREAKEVEN_STOP"
    assert out["exit_price"] == 100.0


def test_walk_base_stop_and_trail_trail_wins():
    # Base stop 80 + trail; trail (150 from peak 200) ratchets above base -> tagged TRAIL.
    cfg = ExitControlsConfig.from_dict({"enabled": True, "unit": "pct",
                                        "trailing": {"activation": 0.10, "distance": 0.25}})
    rows = _bars([
        {"ts": 1, "open": 100, "high": 200, "low": 100, "close": 200},  # peak 200
        {"ts": 2, "open": 151, "high": 152, "low": 149, "close": 150},   # rm=200 trail=150; low 149<=150 STOP
    ])
    out = _walk_option_exit(rows, entry_ts=0, backstop_ts=2, entry_price=100.0,
                            target_level=None, stop_level=80.0, exit_cfg=cfg)
    assert out["exit_reason"] == "OPTION_TRAIL_STOP"
    assert out["exit_price"] == 150.0


# ---- daily cap governor (Task 5) -------------------------------------------


def _spot_trade(idx, entry_ts, exit_ts, entry_price=100.0, direction="CE"):
    return {"direction": direction, "entry_ts": entry_ts, "exit_ts": exit_ts,
            "entry_price": entry_price, "exit_price": entry_price, "entry_datetime": "",
            "exit_datetime": "", "regime": "", "ist_time": ""}


def test_daily_cap_skips_later_same_session_entries():
    # Trade 0 must PAIR (admitted=1); trade 1 same IST session with max_trades=1 -> SKIPPED.
    day_ms = 1_700_000_000_000
    spot = [_spot_trade(0, day_ms, day_ms + 60000),
            _spot_trade(1, day_ms + 120000, day_ms + 180000)]
    contracts = [{"instrument_key": "NSE_FO|OPT", "underlying": "NIFTY",
                  "expiry_date": "2023-11-16", "strike": 100, "side": "CE",
                  "lot_size": 50, "trading_symbol": "NIFTY-CE-100", "atm": 100}]
    candles = _bars([
        {"instrument_key": "NSE_FO|OPT", "ts": day_ms, "open": 10, "high": 11, "low": 9, "close": 10},
        {"instrument_key": "NSE_FO|OPT", "ts": day_ms + 60000, "open": 10, "high": 12, "low": 9, "close": 11},
        {"instrument_key": "NSE_FO|OPT", "ts": day_ms + 120000, "open": 11, "high": 13, "low": 10, "close": 12},
        {"instrument_key": "NSE_FO|OPT", "ts": day_ms + 180000, "open": 12, "high": 14, "low": 11, "close": 13},
    ])
    # control: WITHOUT the cap, both trades pair (so the cap is what suppresses trade 1, not missing data)
    ctrl = simulate_paired_option_trades(
        spot_trades=spot, contracts=contracts, option_candles=candles, underlying="NIFTY",
        moneyness="atm", fixed_expiry_date="2023-11-16")
    assert [t["status"] for t in ctrl["trades"]].count("PAIRED") == 2
    res = simulate_paired_option_trades(
        spot_trades=spot, contracts=contracts, option_candles=candles, underlying="NIFTY",
        moneyness="atm", fixed_expiry_date="2023-11-16", daily_caps={"max_trades": 1})
    statuses = [t["status"] for t in res["trades"]]
    assert statuses.count("PAIRED") == 1              # trade 0 admitted
    assert statuses.count("SKIPPED_DAILY_CAP") == 1   # trade 1 capped despite having candles
    assert res["coverage"].get("skipped_by_cap", 0) == 1


def test_metrics_report_exit_control_attribution():
    day_ms = 1_700_000_000_000
    spot = [{"direction": "CE", "entry_ts": day_ms, "exit_ts": day_ms + 240000,
             "entry_price": 100.0, "exit_price": 100.0, "entry_datetime": "", "exit_datetime": "",
             "regime": "", "ist_time": ""}]
    contracts = [{"instrument_key": "NSE_FO|OPT", "underlying": "NIFTY", "expiry_date": "2023-11-16",
                  "strike": 100, "side": "CE", "lot_size": 50, "trading_symbol": "X", "atm": 100}]
    candles = _bars([
        {"instrument_key": "NSE_FO|OPT", "ts": day_ms, "open": 10, "high": 10, "low": 10, "close": 10},
        {"instrument_key": "NSE_FO|OPT", "ts": day_ms + 60000, "open": 16, "high": 16, "low": 16, "close": 16},
        {"instrument_key": "NSE_FO|OPT", "ts": day_ms + 120000, "open": 11, "high": 11, "low": 11, "close": 11},
        {"instrument_key": "NSE_FO|OPT", "ts": day_ms + 240000, "open": 14, "high": 14, "low": 14, "close": 14},
    ])
    res = simulate_paired_option_trades(
        spot_trades=spot, contracts=contracts, option_candles=candles, underlying="NIFTY",
        moneyness="atm", fixed_expiry_date="2023-11-16", exit_mode="option_levels",
        option_stop_pct=50.0, option_target_pct=200.0,
        exit_controls={"enabled": True, "unit": "pct", "trailing": {"activation": 0.10, "distance": 0.25}})
    m = res["metrics"]
    assert "option_trail_exits" in m and "option_breakeven_exits" in m
    assert "skipped_by_cap" in m and "skipped_daily_loss" in m
    assert m["option_trail_exits"] >= 1     # the trailing stop fired


def test_empty_metrics_has_attribution_keys():
    res = simulate_paired_option_trades(
        spot_trades=[], contracts=[], option_candles=None, underlying="NIFTY")
    m = res["metrics"]
    for k in ("option_trail_exits", "option_breakeven_exits", "skipped_by_cap",
              "skipped_daily_loss", "skipped_daily_target", "skipped_max_trades"):
        assert m.get(k) == 0
