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


def test_momentum_triggered_rejects_both_pct_and_pts():
    # Ambiguous config must fail loudly, not silently prefer pct.
    import pytest
    with pytest.raises(ValueError):
        momentum_triggered(premium_now=230.0, ref_premium=200.0, pct=15.0, pts=10.0)


def test_walk_enters_on_first_cross_then_eod_when_target_not_reached():
    # ref 200; +15% => enter at >=230. target +20% (from entry) => 282, stop -20% => 188.
    ts   = [1, 2, 3, 4, 5, 6]
    prem = [200, 220, 235, 250, 280, 260]  # crosses 230 at idx2 (235); 280<282 so target never hit
    r = walk_premium_momentum(ts=ts, premium=prem, ref_premium=200.0,
                              entry_pct=15.0, target_pct=20.0, stop_pct=20.0)
    assert r["entered"] is True
    assert r["entry_ts"] == 3 and r["entry_premium"] == 235.0
    # stop = 235*0.8 = 188 (never hit); target = 235*1.2 = 282 (never hit) -> EOD at last bar
    assert r["exit_reason"] == "EOD"
    assert r["exit_premium"] == 260.0   # EOD fills at the bar premium, not a stop/target level


def test_walk_eod_when_stop_not_reached():
    ts   = [1, 2, 3, 4]
    prem = [200, 235, 200, 190]  # enter 235 at idx1; stop = 235*0.8=188; low 190 > 188 -> never hit
    r = walk_premium_momentum(ts=ts, premium=prem, ref_premium=200.0,
                              entry_pct=15.0, target_pct=50.0, stop_pct=20.0)
    assert r["entered"] is True and r["entry_premium"] == 235.0
    # lowest is 190, stop is 188 -> not hit -> EOD at 190 (the bar premium)
    assert r["exit_reason"] == "EOD"
    assert r["exit_premium"] == 190.0


def test_walk_stop_fills_at_stop_level():
    # ref 200; +15% => enter at >=230 (idx1 = 235). stop -20% from entry => 188.
    ts   = [1, 2, 3, 4]
    prem = [200, 235, 210, 185]  # idx3 185 <= 188 -> STOP; fill at the 188 LEVEL, not the 185 bar
    r = walk_premium_momentum(ts=ts, premium=prem, ref_premium=200.0,
                              entry_pct=15.0, target_pct=50.0, stop_pct=20.0)
    assert r["entered"] is True and r["entry_premium"] == 235.0
    assert r["exit_reason"] == "STOP"
    assert r["exit_ts"] == 4
    assert r["exit_premium"] == 188.0   # the stop LEVEL (fill convention), NOT the 185 bar premium


def test_walk_target_fills_at_target_level():
    # ref 200; +15% => enter at >=230 (idx1 = 235). target +20% from entry => 282.
    ts   = [1, 2, 3, 4]
    prem = [200, 235, 250, 300]  # idx3 300 >= 282 -> TARGET; fill at the 282 LEVEL, not the 300 bar
    r = walk_premium_momentum(ts=ts, premium=prem, ref_premium=200.0,
                              entry_pct=15.0, target_pct=20.0, stop_pct=20.0)
    assert r["entered"] is True and r["entry_premium"] == 235.0
    assert r["exit_reason"] == "TARGET"
    assert r["exit_ts"] == 4
    assert r["exit_premium"] == 282.0   # the target LEVEL (fill convention), NOT the 300 bar premium


def test_walk_no_entry_when_never_crosses():
    r = walk_premium_momentum(ts=[1, 2, 3], premium=[200, 205, 210], ref_premium=200.0,
                              entry_pct=15.0, target_pct=20.0, stop_pct=20.0)
    assert r["entered"] is False


from app.premium_momentum import stepped_trail_stop


def test_stepped_trail_ratchet_points():
    f = lambda high: stepped_trail_stop(entry_premium=200.0, running_high=high,
                                        base_stop=175.0, x=20.0, y=20.0)
    assert f(210.0) == 175.0   # < 1 full X step -> base stop
    assert f(220.0) == 195.0   # 1 step: 175 + 1*20
    assert f(239.0) == 195.0   # still 1 step
    assert f(240.0) == 215.0   # 2 steps: 175 + 2*20
    assert f(220.0) == 195.0   # monotonic within a call is by running_high, not path


def test_stepped_trail_never_below_base():
    assert stepped_trail_stop(entry_premium=200.0, running_high=205.0,
                              base_stop=175.0, x=20.0, y=20.0) == 175.0


def test_walk_with_stepped_trail_exits_at_ratcheted_stop():
    import functools
    trail = functools.partial(stepped_trail_stop, x=20.0, y=20.0)
    # entry 235 (crosses 15% of 200=230 at 235). base stop 235*.9=211.5.
    ts   = [1, 2, 3, 4, 5]
    prem = [200, 235, 275, 255, 205]   # high 275 -> favorable 40 -> 2 steps -> stop 211.5+40=251.5;
    #                                    255>251.5 holds, then 205<=251.5 -> STOP.
    r = walk_premium_momentum(ts=ts, premium=prem, ref_premium=200.0, entry_pct=15.0,
                              stop_pct=10.0, target_pct=100.0, trail=trail)
    assert r["entered"] and r["exit_reason"] == "STOP"
    # FILL CONVENTION: exit at the stop LEVEL (mirrors the spot engine's intrabar_exit),
    # not the gapped bar premium. 211.5 base + floor(40/20)*20 = 251.5.
    assert r["exit_premium"] == 251.5


# ---------------------------------------------------------------------------
# Phase 1.1 — GAP-HONEST stop fills (option-buyer tail risk must not be flattered)
# ---------------------------------------------------------------------------
from app.premium_momentum import premium_ohlc_for_key


def test_premium_ohlc_falls_back_to_close_when_ohlc_missing():
    candles = pd.DataFrame([
        {"instrument_key": "K", "ts": 2, "close": 11.0},
        {"instrument_key": "K", "ts": 1, "close": 10.0},
    ])
    oh = premium_ohlc_for_key(candles, "K")
    assert list(oh["ts"]) == [1, 2]
    assert list(oh["close"]) == [10.0, 11.0]
    assert list(oh["low"]) == [10.0, 11.0]    # fallback to close
    assert list(oh["open"]) == [10.0, 11.0]


def test_walk_gap_honest_stop_fills_at_open_on_gapdown():
    # entry 235; stop_pct 20 -> base stop 188. A bar gaps DOWN through the stop:
    # open 170 (below stop), low 165, close 180. Legacy (close-only) fills at the 188
    # stop LEVEL; gap-honest fills at min(stop, open)=170 — the real, worse fill.
    ts    = [1, 2, 3]
    close = [200, 235, 180]
    low   = [200, 235, 165]
    open_ = [200, 235, 170]
    r = walk_premium_momentum(ts=ts, premium=close, low=low, open_=open_, ref_premium=200.0,
                              entry_pct=15.0, stop_pct=20.0, target_pct=100.0)
    assert r["entered"] and r["exit_reason"] == "STOP"
    assert r["exit_premium"] == 170.0    # min(188 stop, 170 open) — NOT the 188 level


def test_walk_intrabar_stop_touch_on_low_even_if_close_recovers():
    # bar low 180 touches the 188 stop, but close 210 recovers above it. A close-only
    # model MISSES the stop-out; the intra-bar low model catches it (conservative).
    # open 205 is above the stop, so the fill is the stop level, not a gap.
    ts    = [1, 2, 3, 4]
    close = [200, 235, 210, 210]
    low   = [200, 235, 180, 205]
    open_ = [200, 235, 205, 208]
    r = walk_premium_momentum(ts=ts, premium=close, low=low, open_=open_, ref_premium=200.0,
                              entry_pct=15.0, stop_pct=20.0, target_pct=100.0)
    assert r["entered"] and r["exit_reason"] == "STOP" and r["exit_ts"] == 3
    assert r["exit_premium"] == 188.0    # min(188 stop, 205 open) = 188 (opened above)


def test_walk_close_only_stop_is_legacy_fill_at_level():
    # No low/open provided -> legacy close-touch, fill at the stop level (188).
    r = walk_premium_momentum(ts=[1, 2, 3], premium=[200, 235, 180], ref_premium=200.0,
                              entry_pct=15.0, stop_pct=20.0, target_pct=100.0)
    assert r["entered"] and r["exit_reason"] == "STOP" and r["exit_premium"] == 188.0
