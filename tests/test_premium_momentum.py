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
