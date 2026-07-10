# tests/test_premium_momentum_backtest.py
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

import pandas as pd
from app.premium_momentum_backtest import run_premium_momentum_backtest


def _spot_bar(ts, ist, close, session="2026-07-10"):
    return {"ts": ts, "ist_time": ist, "close": close, "session_date": session}


def _opt(key, ts, close):
    return {"instrument_key": key, "ts": ts, "close": close}


def test_one_session_ce_first_to_trigger():
    # reference 09:31 spot 24000 -> CE ITM1 = 23950. CE premium 100 -> +15% => enter at >=115.
    spot = pd.DataFrame([
        _spot_bar(1, "09:31", 24000.0),
        _spot_bar(2, "09:32", 24010.0),
        _spot_bar(3, "09:33", 24020.0),
        _spot_bar(4, "09:34", 24020.0),
    ])
    contracts = [
        {"instrument_key": "CE|23950", "strike": 23950, "side": "CE", "expiry_date": "2026-07-14"},
        {"instrument_key": "PE|24050", "strike": 24050, "side": "PE", "expiry_date": "2026-07-14"},
    ]
    opt = pd.DataFrame([
        _opt("CE|23950", 1, 100.0), _opt("CE|23950", 2, 110.0),
        _opt("CE|23950", 3, 120.0), _opt("CE|23950", 4, 150.0),   # crosses 115 at ts3
        _opt("PE|24050", 1, 100.0), _opt("PE|24050", 2, 101.0),
        _opt("PE|24050", 3, 102.0), _opt("PE|24050", 4, 103.0),   # never crosses
    ])
    out = run_premium_momentum_backtest(
        spot_df=spot, option_candles=opt, contracts=contracts, instrument="NIFTY",
        params={"reference_time": "09:31", "moneyness": "itm1", "side": "first_to_trigger",
                "momentum_pct": 15.0, "target_pct": 50.0, "stop_pct": 20.0},
    )
    assert out["coverage"]["sessions_traded"] == 1
    assert len(out["trades"]) == 1
    t = out["trades"][0]
    assert t["side"] == "CE" and t["strike"] == 23950
    assert t["entry_premium"] == 120.0   # first bar >= 115


def test_session_excluded_when_locked_strike_has_no_candles():
    spot = pd.DataFrame([_spot_bar(1, "09:31", 24000.0), _spot_bar(2, "09:32", 24000.0)])
    contracts = [{"instrument_key": "CE|23950", "strike": 23950, "side": "CE", "expiry_date": "2026-07-14"}]
    opt = pd.DataFrame(columns=["instrument_key", "ts", "close"])  # NO candles
    out = run_premium_momentum_backtest(
        spot_df=spot, option_candles=opt, contracts=contracts, instrument="NIFTY",
        params={"reference_time": "09:31", "moneyness": "itm1", "side": "CE",
                "momentum_pct": 15.0, "stop_pct": 20.0},
    )
    assert out["trades"] == []
    assert out["coverage"]["sessions_excluded"] == 1
    assert out["coverage"]["exclude_reasons"].get("no_premium_series") == 1
