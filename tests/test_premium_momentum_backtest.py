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


def test_first_to_trigger_picks_the_earlier_entry_side():
    # BOTH sides cross their momentum threshold, at DIFFERENT bars — the strategy
    # must take the side that crossed FIRST (earliest entry_ts). PE crosses at ts2,
    # CE only at ts3, so PE must win (headline first-to-trigger behaviour).
    spot = pd.DataFrame([
        _spot_bar(1, "09:31", 24000.0), _spot_bar(2, "09:32", 24000.0), _spot_bar(3, "09:33", 24000.0),
    ])
    contracts = [
        {"instrument_key": "CE|23950", "strike": 23950, "side": "CE", "expiry_date": "2026-07-14"},
        {"instrument_key": "PE|24050", "strike": 24050, "side": "PE", "expiry_date": "2026-07-14"},
    ]
    opt = pd.DataFrame([
        _opt("CE|23950", 1, 100.0), _opt("CE|23950", 2, 105.0), _opt("CE|23950", 3, 130.0),  # crosses 115 at ts3
        _opt("PE|24050", 1, 100.0), _opt("PE|24050", 2, 120.0), _opt("PE|24050", 3, 125.0),  # crosses 115 at ts2
    ])
    out = run_premium_momentum_backtest(
        spot_df=spot, option_candles=opt, contracts=contracts, instrument="NIFTY",
        params={"reference_time": "09:31", "moneyness": "itm1", "side": "first_to_trigger",
                "momentum_pct": 15.0, "target_pct": 50.0, "stop_pct": 20.0},
    )
    assert len(out["trades"]) == 1
    t = out["trades"][0]
    assert t["side"] == "PE" and t["entry_ts"] == 2 and t["entry_premium"] == 120.0


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


# ---------------------------------------------------------------------------
# Multi-session correctness (found preparing the first real backtest)
# ---------------------------------------------------------------------------
def test_each_session_resolves_its_own_weekly_expiry():
    # Two sessions in DIFFERENT weeks: each must trade its own week's expiry,
    # never the window's first week for every session.
    spot = pd.DataFrame([
        _spot_bar(1, "09:31", 24000.0, session="2026-07-06"),   # week 1 (expiry 07-07)
        _spot_bar(2, "09:32", 24000.0, session="2026-07-06"),
        _spot_bar(3, "09:33", 24000.0, session="2026-07-06"),
        _spot_bar(100, "09:31", 24000.0, session="2026-07-13"),  # week 2 (expiry 07-14)
        _spot_bar(101, "09:32", 24000.0, session="2026-07-13"),
        _spot_bar(102, "09:33", 24000.0, session="2026-07-13"),
    ])
    contracts = [
        {"instrument_key": "CE-W1", "strike": 23950, "side": "CE", "expiry_date": "2026-07-07"},
        {"instrument_key": "CE-W2", "strike": 23950, "side": "CE", "expiry_date": "2026-07-14"},
    ]
    opt = pd.DataFrame([
        _opt("CE-W1", 1, 100.0), _opt("CE-W1", 2, 120.0), _opt("CE-W1", 3, 122.0),        # W1 triggers (+15%)
        _opt("CE-W2", 100, 100.0), _opt("CE-W2", 101, 130.0), _opt("CE-W2", 102, 131.0),  # W2 triggers
    ])
    out = run_premium_momentum_backtest(
        spot_df=spot, option_candles=opt, contracts=contracts, instrument="NIFTY",
        params={"reference_time": "09:31", "moneyness": "itm1", "side": "CE",
                "momentum_pct": 15.0, "stop_pct": 20.0},
    )
    assert out["coverage"]["sessions_traded"] == 2
    by_sess = {t["session_date"]: t for t in out["trades"]}
    assert by_sess["2026-07-06"]["expiry_date"] == "2026-07-07"
    assert by_sess["2026-07-06"]["instrument_key"] == "CE-W1"
    assert by_sess["2026-07-13"]["expiry_date"] == "2026-07-14"
    assert by_sess["2026-07-13"]["instrument_key"] == "CE-W2"


def test_walk_never_leaks_into_the_next_session():
    # A locked key has candles on the NEXT day too (same weekly contract). The
    # session-1 walk must be bounded to session 1: EOD exit at session 1's last
    # bar, never at the next day's last candle.
    spot = pd.DataFrame([
        _spot_bar(1, "09:31", 24000.0, session="2026-07-06"),
        _spot_bar(2, "09:32", 24000.0, session="2026-07-06"),
        _spot_bar(3, "09:33", 24000.0, session="2026-07-06"),   # session 1 ends ts3
    ])
    contracts = [{"instrument_key": "CE-K", "strike": 23950, "side": "CE", "expiry_date": "2026-07-07"}]
    opt = pd.DataFrame([
        _opt("CE-K", 1, 100.0), _opt("CE-K", 2, 120.0), _opt("CE-K", 3, 125.0),
        _opt("CE-K", 50, 500.0),   # NEXT DAY's candle for the same contract
    ])
    out = run_premium_momentum_backtest(
        spot_df=spot, option_candles=opt, contracts=contracts, instrument="NIFTY",
        params={"reference_time": "09:31", "moneyness": "itm1", "side": "CE",
                "momentum_pct": 15.0, "stop_pct": 90.0, "target_pct": 900.0},
    )
    assert len(out["trades"]) == 1
    t = out["trades"][0]
    assert t["exit_reason"] == "EOD"
    assert t["exit_ts"] == 3 and t["exit_premium"] == 125.0   # NOT ts50 @ 500
