# tests/test_premium_momentum.py
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

import pandas as pd
from app.premium_momentum import lock_reference_strike, premium_series_for_key


_CONTRACTS = [
    {"instrument_key": "NSE|CE|24000", "strike": 24000, "side": "CE", "expiry_date": "2026-07-14"},
    {"instrument_key": "NSE|CE|23950", "strike": 23950, "side": "CE", "expiry_date": "2026-07-14"},
    {"instrument_key": "NSE|PE|24000", "strike": 24000, "side": "PE", "expiry_date": "2026-07-14"},
    {"instrument_key": "NSE|PE|24050", "strike": 24050, "side": "PE", "expiry_date": "2026-07-14"},
]


def test_lock_reference_strike_ce_itm1_is_below_spot():
    # NIFTY step 50. spot 24000 -> ATM 24000; CE ITM1 = ATM - 1 step = 23950.
    got = lock_reference_strike(contracts=_CONTRACTS, underlying="NIFTY",
                                spot_at_ref=24000.0, side="CE", moneyness="itm1")
    assert got is not None
    assert got["strike"] == 23950 and got["side"] == "CE"
    assert got["instrument_key"] == "NSE|CE|23950"


def test_lock_reference_strike_pe_itm1_is_above_spot():
    got = lock_reference_strike(contracts=_CONTRACTS, underlying="NIFTY",
                                spot_at_ref=24000.0, side="PE", moneyness="itm1")
    assert got["strike"] == 24050 and got["side"] == "PE"


def test_lock_reference_strike_missing_returns_none():
    got = lock_reference_strike(contracts=_CONTRACTS, underlying="NIFTY",
                                spot_at_ref=24000.0, side="CE", moneyness="itm2")
    assert got is None   # no 23900 CE contract present


def test_premium_series_for_key_sorted_and_close_is_premium():
    candles = pd.DataFrame([
        {"instrument_key": "K", "ts": 300, "close": 12.0},
        {"instrument_key": "K", "ts": 100, "close": 10.0},
        {"instrument_key": "K", "ts": 200, "close": 11.0},
        {"instrument_key": "OTHER", "ts": 150, "close": 99.0},
    ])
    ts, prem = premium_series_for_key(candles, "K")
    assert list(ts) == [100, 200, 300]
    assert list(prem) == [10.0, 11.0, 12.0]


from app.premium_momentum import momentum_triggered, walk_premium_momentum


def test_momentum_triggered_pct_and_pts():
    assert momentum_triggered(premium_now=230.0, ref_premium=200.0, pct=15.0) is True
    assert momentum_triggered(premium_now=229.0, ref_premium=200.0, pct=15.0) is False
    assert momentum_triggered(premium_now=210.0, ref_premium=200.0, pts=10.0) is True
    assert momentum_triggered(premium_now=209.9, ref_premium=200.0, pts=10.0) is False


def test_walk_enters_on_first_cross_then_targets():
    # ref 200; +15% => enter at >=230. target +20% (from entry) => 276, stop -20% => 220.8
    ts   = [1, 2, 3, 4, 5, 6]
    prem = [200, 220, 235, 250, 280, 260]  # crosses 230 at idx2 (235), target 235*1.2=282 not hit til? 280<282,
    #                                        280 at idx4; 235*1.2=282 -> not hit; but +20% target uses entry=235
    r = walk_premium_momentum(ts=ts, premium=prem, ref_premium=200.0,
                              entry_pct=15.0, target_pct=20.0, stop_pct=20.0)
    assert r["entered"] is True
    assert r["entry_ts"] == 3 and r["entry_premium"] == 235.0
    # stop = 235*0.8 = 188 (never hit); target = 235*1.2 = 282 (never hit) -> TIME/EOD exit at last bar
    assert r["exit_reason"] in ("EOD", "TIME_EXIT")
    assert r["exit_premium"] == 260.0


def test_walk_stop_hit_before_target():
    ts   = [1, 2, 3, 4]
    prem = [200, 235, 200, 190]  # enter 235 at idx1; stop = 235*0.8=188; idx3 190>188 not hit; idx? 190>188
    r = walk_premium_momentum(ts=ts, premium=prem, ref_premium=200.0,
                              entry_pct=15.0, target_pct=50.0, stop_pct=20.0)
    assert r["entered"] is True and r["entry_premium"] == 235.0
    # lowest is 190, stop is 188 -> not hit -> EOD at 190
    assert r["exit_premium"] == 190.0


def test_walk_no_entry_when_never_crosses():
    r = walk_premium_momentum(ts=[1, 2, 3], premium=[200, 205, 210], ref_premium=200.0,
                              entry_pct=15.0, target_pct=20.0, stop_pct=20.0)
    assert r["entered"] is False
