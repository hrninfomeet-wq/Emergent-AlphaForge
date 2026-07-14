# tests/test_premium_momentum_contingency.py
"""Phase 5A — full contingency ("lazy legs") session state machine.

Covers the 11 items in
docs/superpowers/plans/2026-07-14-premium-momentum-phase5a-backtest-contingency.md
section 3. Fixture style mirrors tests/test_premium_momentum_backtest.py.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

import pandas as pd
import pytest

from app.premium_momentum import stepped_trail_stop_pct
from app.premium_momentum_backtest import run_premium_momentum_backtest


def _spot_bar(ts, ist, close, session="2026-07-10"):
    return {"ts": ts, "ist_time": ist, "close": close, "session_date": session}


def _opt(key, ts, close):
    return {"instrument_key": key, "ts": ts, "close": close}


# ---------------------------------------------------------------------------
# 1. Parity: default params -> byte-identical to the pre-5A engine.
# ---------------------------------------------------------------------------
def test_parity_default_params_byte_identical():
    # Exact fixture + params from test_one_session_ce_first_to_trigger (the
    # pre-5A test file) -- none of the new Phase 5A keys are present.
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
        _opt("CE|23950", 3, 120.0), _opt("CE|23950", 4, 150.0),
        _opt("PE|24050", 1, 100.0), _opt("PE|24050", 2, 101.0),
        _opt("PE|24050", 3, 102.0), _opt("PE|24050", 4, 103.0),
    ])
    out = run_premium_momentum_backtest(
        spot_df=spot, option_candles=opt, contracts=contracts, instrument="NIFTY",
        params={"reference_time": "09:31", "moneyness": "itm1", "side": "first_to_trigger",
                "momentum_pct": 15.0, "target_pct": 50.0, "stop_pct": 20.0},
    )
    assert out["coverage"]["sessions_traded"] == 1
    assert len(out["trades"]) == 1
    t = out["trades"][0]
    # Pinned numeric values, hand-traced against the pre-5A algorithm: entry
    # crosses 115 first at ts3 (120); target 180 and stop 96 never touched;
    # EOD exit at the last bar (ts4, 150).
    assert t["side"] == "CE" and t["strike"] == 23950
    assert t["entry_ts"] == 3 and t["entry_premium"] == 120.0
    assert t["exit_ts"] == 4 and t["exit_premium"] == 150.0
    assert t["exit_reason"] == "EOD"
    assert t["premium_pnl"] == 30.0
    assert t["ref_premium"] == 100.0
    # New fields are backward-compatible additions, not a behavior change.
    assert t["leg"] == "primary"
    assert t["net_pnl_pts"] == 30.0   # cost model disabled by default -> net == gross
    cov = out["coverage"]
    assert cov["lazy_armed"] == 0 and cov["lazy_entered"] == 0
    assert cov["lazy_blocked_cutoff"] == 0 and cov["lazy_excluded_no_data"] == 0
    by_leg = out["summary"]["by_leg"]
    assert by_leg["primary"]["trades"] == 1
    assert by_leg["lazy"]["trades"] == 0
    assert by_leg["primary"]["net_pnl_rupees"] == out["summary"]["net_pnl_rupees"]


# ---------------------------------------------------------------------------
# 2. both-mode: CE and PE both cross -> two primary trades.
# ---------------------------------------------------------------------------
def test_leg_mode_both_keeps_every_side_that_entered():
    spot = pd.DataFrame([
        _spot_bar(1, "09:31", 24000.0), _spot_bar(2, "09:32", 24000.0),
        _spot_bar(3, "09:33", 24000.0), _spot_bar(4, "09:34", 24000.0),
    ])
    contracts = [
        {"instrument_key": "CE|23950", "strike": 23950, "side": "CE", "expiry_date": "2026-07-14"},
        {"instrument_key": "PE|24050", "strike": 24050, "side": "PE", "expiry_date": "2026-07-14"},
    ]
    opt = pd.DataFrame([
        _opt("CE|23950", 1, 100.0), _opt("CE|23950", 2, 105.0),
        _opt("CE|23950", 3, 130.0), _opt("CE|23950", 4, 140.0),   # crosses 115 at ts3
        _opt("PE|24050", 1, 100.0), _opt("PE|24050", 2, 120.0),
        _opt("PE|24050", 3, 125.0), _opt("PE|24050", 4, 130.0),   # crosses 115 at ts2
    ])
    # first_to_trigger: same fixture yields exactly ONE trade (PE, earliest).
    single = run_premium_momentum_backtest(
        spot_df=spot, option_candles=opt, contracts=contracts, instrument="NIFTY",
        params={"reference_time": "09:31", "moneyness": "itm1",
                "momentum_pct": 15.0, "stop_pct": 90.0, "target_pct": 900.0},
    )
    assert len(single["trades"]) == 1

    both = run_premium_momentum_backtest(
        spot_df=spot, option_candles=opt, contracts=contracts, instrument="NIFTY",
        params={"reference_time": "09:31", "moneyness": "itm1", "leg_mode": "both",
                "momentum_pct": 15.0, "stop_pct": 90.0, "target_pct": 900.0},
    )
    assert len(both["trades"]) == 2
    sides = {t["side"] for t in both["trades"]}
    assert sides == {"CE", "PE"}
    assert all(t["leg"] == "primary" for t in both["trades"])
    assert both["coverage"]["sessions_traded"] == 1


# ---------------------------------------------------------------------------
# 3. Lazy arming: primary CE STOP -> lazy PE armed at the stop bar, fresh
#    strike from THAT bar's spot, ref = lazy strike's close at that bar,
#    enters on the lazy trigger after it.
# ---------------------------------------------------------------------------
def test_lazy_arms_on_primary_stop_and_enters():
    spot = pd.DataFrame([
        _spot_bar(1, "09:31", 24000.0), _spot_bar(2, "09:32", 24000.0),
        _spot_bar(3, "09:33", 24100.0),   # CE stop-out bar -> fresh spot 24100
        _spot_bar(4, "09:34", 24100.0), _spot_bar(5, "09:35", 24100.0),
        _spot_bar(6, "09:36", 24100.0),
    ])
    contracts = [
        {"instrument_key": "CE|23950", "strike": 23950, "side": "CE", "expiry_date": "2026-07-17"},
        {"instrument_key": "PE|24150", "strike": 24150, "side": "PE", "expiry_date": "2026-07-17"},
    ]
    opt = pd.DataFrame([
        # CE: ref 100 (ts1) -> +15% trigger 115; enters ts2 @120; stop 20% -> 96;
        # ts3 90 <= 96 -> STOP, fill = stop level 96.0 (no low/open given).
        _opt("CE|23950", 1, 100.0), _opt("CE|23950", 2, 120.0), _opt("CE|23950", 3, 90.0),
        # Fresh PE|24150 locked from spot at ts3 (24100). ref bar EXACT match at
        # ts3 (50.0). Walk starts strictly after it: ts4=54 (<55, no cross),
        # ts5=60 (>=55 -> entry), ts6=58 (EOD, stop 60*0.9=54 not touched).
        _opt("PE|24150", 3, 50.0), _opt("PE|24150", 4, 54.0),
        _opt("PE|24150", 5, 60.0), _opt("PE|24150", 6, 58.0),
    ])
    out = run_premium_momentum_backtest(
        spot_df=spot, option_candles=opt, contracts=contracts, instrument="NIFTY",
        params={"reference_time": "09:31", "moneyness": "itm1", "side": "CE",
                "momentum_pct": 15.0, "stop_pct": 20.0, "target_pct": 900.0,
                "lazy_enabled": True, "lazy_momentum_pct": 10.0, "lazy_stop_pct": 10.0,
                "lazy_moneyness": "itm1"},
    )
    trades = out["trades"]
    assert len(trades) == 2
    primary = next(t for t in trades if t["leg"] == "primary")
    lazy = next(t for t in trades if t["leg"] == "lazy")

    assert primary["side"] == "CE" and primary["exit_reason"] == "STOP"
    # premium_ohlc_for_key falls back open/low/high -> close (no OHLC columns
    # in these fixtures), so the gap-honest fill = min(stop, open[j]) collapses
    # to the bar's own close (90.0) whenever close <= stop, not the raw 96
    # stop level.
    assert primary["exit_ts"] == 3 and primary["exit_premium"] == 90.0

    assert lazy["side"] == "PE" and lazy["strike"] == 24150
    assert lazy["ref_premium"] == 50.0
    assert lazy["entry_ts"] == 5 and lazy["entry_premium"] == 60.0
    assert lazy["exit_ts"] == 6 and lazy["exit_premium"] == 58.0
    assert lazy["exit_reason"] == "EOD"
    assert lazy["lazy_parent_side"] == "CE"
    assert lazy["lazy_activated_ts"] == 3

    cov = out["coverage"]
    assert cov["lazy_armed"] == 1
    assert cov["lazy_entered"] == 1
    assert cov["lazy_blocked_cutoff"] == 0
    assert cov["lazy_excluded_no_data"] == 0

    by_leg = out["summary"]["by_leg"]
    assert by_leg["primary"]["trades"] == 1
    assert by_leg["lazy"]["trades"] == 1


# ---------------------------------------------------------------------------
# 4. Lazy NOT armed on TARGET or EOD exits.
# ---------------------------------------------------------------------------
def test_lazy_not_armed_on_target_or_eod_exit():
    contracts = [
        {"instrument_key": "CE|23950", "strike": 23950, "side": "CE", "expiry_date": "2026-07-17"},
        {"instrument_key": "PE|24050", "strike": 24050, "side": "PE", "expiry_date": "2026-07-17"},
    ]
    base_params = {"reference_time": "09:31", "moneyness": "itm1", "side": "CE",
                   "momentum_pct": 15.0,
                   "lazy_enabled": True, "lazy_momentum_pct": 10.0, "lazy_stop_pct": 10.0}

    # -- TARGET exit --
    spot = pd.DataFrame([_spot_bar(i, f"09:3{i}", 24000.0) for i in (1, 2, 3)])
    opt = pd.DataFrame([
        _opt("CE|23950", 1, 100.0), _opt("CE|23950", 2, 120.0),   # entry @120 (cross 115)
        _opt("CE|23950", 3, 150.0),                                # target 120*1.2=144 -> hit
        _opt("PE|24050", 1, 100.0), _opt("PE|24050", 2, 105.0), _opt("PE|24050", 3, 110.0),
    ])
    out = run_premium_momentum_backtest(
        spot_df=spot, option_candles=opt, contracts=contracts, instrument="NIFTY",
        params={**base_params, "target_pct": 20.0, "stop_pct": 90.0},
    )
    assert len(out["trades"]) == 1
    assert out["trades"][0]["exit_reason"] == "TARGET"
    assert out["coverage"]["lazy_armed"] == 0
    assert out["coverage"]["lazy_entered"] == 0

    # -- EOD exit --
    opt2 = pd.DataFrame([
        _opt("CE|23950", 1, 100.0), _opt("CE|23950", 2, 120.0),
        _opt("CE|23950", 3, 130.0),   # no stop (base 96), no target (huge) -> EOD
        _opt("PE|24050", 1, 100.0), _opt("PE|24050", 2, 105.0), _opt("PE|24050", 3, 110.0),
    ])
    out2 = run_premium_momentum_backtest(
        spot_df=spot, option_candles=opt2, contracts=contracts, instrument="NIFTY",
        params={**base_params, "target_pct": 900.0, "stop_pct": 90.0},
    )
    assert len(out2["trades"]) == 1
    assert out2["trades"][0]["exit_reason"] == "EOD"
    assert out2["coverage"]["lazy_armed"] == 0
    assert out2["coverage"]["lazy_entered"] == 0


# ---------------------------------------------------------------------------
# 5. One-shot: a lazy leg's own STOP arms nothing further.
# ---------------------------------------------------------------------------
def test_lazy_stop_does_not_arm_a_further_reversal():
    spot = pd.DataFrame([
        _spot_bar(1, "09:31", 24000.0), _spot_bar(2, "09:32", 24000.0),
        _spot_bar(3, "09:33", 24100.0),
        _spot_bar(4, "09:34", 24100.0), _spot_bar(5, "09:35", 24100.0),
        _spot_bar(6, "09:36", 24100.0),
    ])
    contracts = [
        {"instrument_key": "CE|23950", "strike": 23950, "side": "CE", "expiry_date": "2026-07-17"},
        {"instrument_key": "PE|24150", "strike": 24150, "side": "PE", "expiry_date": "2026-07-17"},
    ]
    opt = pd.DataFrame([
        _opt("CE|23950", 1, 100.0), _opt("CE|23950", 2, 120.0), _opt("CE|23950", 3, 90.0),
        # PE lazy: ref ts3=50, enters ts5 @60 (>=55), then ts6=50 <= stop(54) -> STOP.
        _opt("PE|24150", 3, 50.0), _opt("PE|24150", 4, 54.0),
        _opt("PE|24150", 5, 60.0), _opt("PE|24150", 6, 50.0),
    ])
    out = run_premium_momentum_backtest(
        spot_df=spot, option_candles=opt, contracts=contracts, instrument="NIFTY",
        params={"reference_time": "09:31", "moneyness": "itm1", "side": "CE",
                "momentum_pct": 15.0, "stop_pct": 20.0, "target_pct": 900.0,
                "lazy_enabled": True, "lazy_momentum_pct": 10.0, "lazy_stop_pct": 10.0},
    )
    trades = out["trades"]
    assert len(trades) == 2   # NOT 3+ -- the lazy PE's own STOP arms nothing
    lazy = next(t for t in trades if t["leg"] == "lazy")
    # Same close-only fallback fill convention as the primary STOP above.
    assert lazy["exit_reason"] == "STOP" and lazy["exit_premium"] == 50.0
    assert out["coverage"]["lazy_armed"] == 1     # only from the primary's STOP
    assert out["coverage"]["lazy_entered"] == 1
    assert sum(1 for t in trades if t.get("lazy_parent_side") == "PE") == 0


# ---------------------------------------------------------------------------
# 6. Cutoff: (a) primary cross at/after cutoff -> no entry; (b) stop-out
#    at/after cutoff -> no arming; (c) lazy cross at/after cutoff -> no lazy
#    entry; (d) an OPEN position still exits normally after the cutoff.
# ---------------------------------------------------------------------------
def test_cutoff_blocks_primary_entry_at_the_cutoff_bar():
    spot = pd.DataFrame([
        _spot_bar(1, "09:31", 24000.0), _spot_bar(2, "09:32", 24000.0),
        _spot_bar(3, "09:33", 24000.0), _spot_bar(4, "09:34", 24000.0),
    ])
    contracts = [{"instrument_key": "CE|23950", "strike": 23950, "side": "CE", "expiry_date": "2026-07-17"}]
    opt = pd.DataFrame([
        _opt("CE|23950", 1, 100.0), _opt("CE|23950", 2, 105.0),
        _opt("CE|23950", 3, 120.0),   # WOULD cross 115 here, but this bar IS the cutoff
        _opt("CE|23950", 4, 130.0),
    ])
    out = run_premium_momentum_backtest(
        spot_df=spot, option_candles=opt, contracts=contracts, instrument="NIFTY",
        params={"reference_time": "09:31", "moneyness": "itm1", "side": "CE",
                "momentum_pct": 15.0, "stop_pct": 90.0, "entry_cutoff": "09:33"},
    )
    assert out["trades"] == []
    assert out["coverage"]["sessions_no_signal"] == 1


def test_cutoff_blocks_lazy_arming_on_late_stop_out():
    spot = pd.DataFrame([
        _spot_bar(1, "09:31", 24000.0), _spot_bar(2, "09:32", 24000.0),
        _spot_bar(3, "09:33", 24100.0),
    ])
    contracts = [
        {"instrument_key": "CE|23950", "strike": 23950, "side": "CE", "expiry_date": "2026-07-17"},
        {"instrument_key": "PE|24150", "strike": 24150, "side": "PE", "expiry_date": "2026-07-17"},
    ]
    opt = pd.DataFrame([
        _opt("CE|23950", 1, 100.0), _opt("CE|23950", 2, 120.0),
        _opt("CE|23950", 3, 90.0),   # STOP at ts3 -- also the cutoff bar
        # A PE candle set that WOULD happily enter if arming weren't blocked.
        _opt("PE|24150", 3, 50.0), _opt("PE|24150", 4, 60.0), _opt("PE|24150", 5, 62.0),
    ])
    out = run_premium_momentum_backtest(
        spot_df=spot, option_candles=opt, contracts=contracts, instrument="NIFTY",
        params={"reference_time": "09:31", "moneyness": "itm1", "side": "CE",
                "momentum_pct": 15.0, "stop_pct": 20.0, "entry_cutoff": "09:33",
                "lazy_enabled": True, "lazy_momentum_pct": 10.0, "lazy_stop_pct": 10.0},
    )
    assert len(out["trades"]) == 1   # only the primary CE STOP
    assert out["coverage"]["lazy_blocked_cutoff"] == 1
    assert out["coverage"]["lazy_armed"] == 0
    assert out["coverage"]["lazy_entered"] == 0


def test_cutoff_blocks_lazy_entry_but_not_arming():
    spot = pd.DataFrame([
        _spot_bar(1, "09:31", 24000.0), _spot_bar(2, "09:32", 24000.0),
        _spot_bar(3, "09:33", 24100.0), _spot_bar(4, "09:34", 24100.0),
        _spot_bar(5, "09:35", 24100.0), _spot_bar(6, "09:36", 24100.0),
    ])
    contracts = [
        {"instrument_key": "CE|23950", "strike": 23950, "side": "CE", "expiry_date": "2026-07-17"},
        {"instrument_key": "PE|24150", "strike": 24150, "side": "PE", "expiry_date": "2026-07-17"},
    ]
    opt = pd.DataFrame([
        _opt("CE|23950", 1, 100.0), _opt("CE|23950", 2, 120.0), _opt("CE|23950", 3, 90.0),
        # Fresh PE ref @ts3=50; ts4=54 (no cross, <55); ts5=60 (WOULD cross,
        # but ts5 IS the cutoff bar); ts6=62.
        _opt("PE|24150", 3, 50.0), _opt("PE|24150", 4, 54.0),
        _opt("PE|24150", 5, 60.0), _opt("PE|24150", 6, 62.0),
    ])
    out = run_premium_momentum_backtest(
        spot_df=spot, option_candles=opt, contracts=contracts, instrument="NIFTY",
        params={"reference_time": "09:31", "moneyness": "itm1", "side": "CE",
                "momentum_pct": 15.0, "stop_pct": 20.0, "target_pct": 900.0,
                "entry_cutoff": "09:35",
                "lazy_enabled": True, "lazy_momentum_pct": 10.0, "lazy_stop_pct": 10.0},
    )
    assert len(out["trades"]) == 1   # only the primary; lazy never entered
    assert out["coverage"]["lazy_armed"] == 1        # stop-out (ts3) is before cutoff (ts5)
    assert out["coverage"]["lazy_entered"] == 0
    assert out["coverage"]["lazy_excluded_no_data"] == 0   # data was fine -- cutoff blocked it


def test_cutoff_open_position_still_exits_normally_after_cutoff():
    spot = pd.DataFrame([
        _spot_bar(1, "09:31", 24000.0), _spot_bar(2, "09:32", 24000.0),
        _spot_bar(3, "09:33", 24000.0), _spot_bar(4, "09:34", 24000.0),
    ])
    contracts = [{"instrument_key": "CE|23950", "strike": 23950, "side": "CE", "expiry_date": "2026-07-17"}]
    opt = pd.DataFrame([
        _opt("CE|23950", 1, 100.0), _opt("CE|23950", 2, 120.0),   # entry before cutoff
        _opt("CE|23950", 3, 125.0),   # cutoff bar -- no stop/target hit here
        _opt("CE|23950", 4, 130.0),   # after cutoff -- EOD exit still happens
    ])
    out = run_premium_momentum_backtest(
        spot_df=spot, option_candles=opt, contracts=contracts, instrument="NIFTY",
        params={"reference_time": "09:31", "moneyness": "itm1", "side": "CE",
                "momentum_pct": 15.0, "stop_pct": 90.0, "target_pct": 900.0,
                "entry_cutoff": "09:33"},
    )
    assert len(out["trades"]) == 1
    t = out["trades"][0]
    assert t["entry_ts"] == 2 and t["entry_premium"] == 120.0
    assert t["exit_reason"] == "EOD"
    assert t["exit_ts"] == 4 and t["exit_premium"] == 130.0


# ---------------------------------------------------------------------------
# 7. exit_time: open leg exits at the exit_time bar close, reason EOD; bars
#    after it are never touched.
# ---------------------------------------------------------------------------
def test_exit_time_bounds_the_session_hard_exit():
    spot = pd.DataFrame([
        _spot_bar(1, "09:31", 24000.0), _spot_bar(2, "09:32", 24000.0),
        _spot_bar(3, "09:33", 24000.0), _spot_bar(4, "09:34", 24000.0),
        _spot_bar(5, "09:35", 24000.0),
    ])
    contracts = [{"instrument_key": "CE|23950", "strike": 23950, "side": "CE", "expiry_date": "2026-07-17"}]
    opt = pd.DataFrame([
        _opt("CE|23950", 1, 100.0), _opt("CE|23950", 2, 120.0), _opt("CE|23950", 3, 125.0),
        _opt("CE|23950", 4, 999.0), _opt("CE|23950", 5, 999.0),   # NEVER touched
    ])
    out = run_premium_momentum_backtest(
        spot_df=spot, option_candles=opt, contracts=contracts, instrument="NIFTY",
        params={"reference_time": "09:31", "moneyness": "itm1", "side": "CE",
                "momentum_pct": 15.0, "stop_pct": 90.0, "target_pct": 900.0,
                "exit_time": "09:33"},
    )
    assert len(out["trades"]) == 1
    t = out["trades"][0]
    assert t["exit_reason"] == "EOD"
    assert t["exit_ts"] == 3 and t["exit_premium"] == 125.0


# ---------------------------------------------------------------------------
# 8. stepped_trail_stop_pct arithmetic.
# ---------------------------------------------------------------------------
def test_stepped_trail_stop_pct_pins_entry_100_five_five_high_112():
    got = stepped_trail_stop_pct(entry_premium=100.0, running_high=112.0,
                                 base_stop=80.0, x_pct=5.0, y_pct=5.0)
    # favorable 12 -> floor(12/5)=2 steps -> base + 2*5.0 = 90.0
    assert got == 90.0


def test_stepped_trail_stop_pct_capped_at_running_high():
    got = stepped_trail_stop_pct(entry_premium=100.0, running_high=112.0,
                                 base_stop=80.0, x_pct=5.0, y_pct=50.0)
    # 2 steps * 50.0 = 100 -> base+100=180, capped at running_high 112.
    assert got == 112.0


# ---------------------------------------------------------------------------
# 9. Look-ahead: lazy entry cannot trigger on its own ref bar.
# ---------------------------------------------------------------------------
def test_lazy_entry_cannot_trigger_on_its_own_ref_bar():
    spot = pd.DataFrame([
        _spot_bar(1, "09:31", 24000.0), _spot_bar(2, "09:32", 24000.0),
        _spot_bar(3, "09:33", 24100.0),
        _spot_bar(4, "09:34", 24100.0), _spot_bar(5, "09:35", 24100.0),
        _spot_bar(6, "09:36", 24100.0),
    ])
    contracts = [
        {"instrument_key": "CE|23950", "strike": 23950, "side": "CE", "expiry_date": "2026-07-17"},
        {"instrument_key": "PE|24150", "strike": 24150, "side": "PE", "expiry_date": "2026-07-17"},
    ]
    opt = pd.DataFrame([
        _opt("CE|23950", 1, 100.0), _opt("CE|23950", 2, 120.0), _opt("CE|23950", 3, 90.0),
        # Adversarial: lazy_momentum_pct=0.0 means "premium_now >= ref_premium"
        # is enough to trigger. The ref bar's OWN close (50.0) trivially equals
        # ref_premium -- if the walk mistakenly included the ref bar as a
        # candidate, entry would fire AT ts3 (== lazy_activated_ts). ts4=45 is
        # BELOW ref (must legitimately fail to trigger); ts5=55 is the first
        # bar that actually satisfies >= ref_premium.
        _opt("PE|24150", 3, 50.0), _opt("PE|24150", 4, 45.0),
        _opt("PE|24150", 5, 55.0), _opt("PE|24150", 6, 60.0),
    ])
    out = run_premium_momentum_backtest(
        spot_df=spot, option_candles=opt, contracts=contracts, instrument="NIFTY",
        params={"reference_time": "09:31", "moneyness": "itm1", "side": "CE",
                "momentum_pct": 15.0, "stop_pct": 20.0, "target_pct": 900.0,
                "lazy_enabled": True, "lazy_momentum_pct": 0.0, "lazy_stop_pct": 10.0},
    )
    lazy = next(t for t in out["trades"] if t["leg"] == "lazy")
    assert lazy["entry_ts"] == 5 and lazy["entry_premium"] == 55.0
    assert lazy["entry_ts"] != lazy["lazy_activated_ts"]
    assert lazy["lazy_activated_ts"] == 3


# ---------------------------------------------------------------------------
# 10. Missing lazy-strike candles -> lazy_excluded_no_data, no phantom trade.
# ---------------------------------------------------------------------------
def test_lazy_missing_candles_is_excluded_not_mis_filled():
    spot = pd.DataFrame([
        _spot_bar(1, "09:31", 24000.0), _spot_bar(2, "09:32", 24000.0),
        _spot_bar(3, "09:33", 24100.0),
    ])
    contracts = [
        {"instrument_key": "CE|23950", "strike": 23950, "side": "CE", "expiry_date": "2026-07-17"},
        {"instrument_key": "PE|24150", "strike": 24150, "side": "PE", "expiry_date": "2026-07-17"},
    ]
    # PE|24150 has NO candles at all in option_candles.
    opt = pd.DataFrame([
        _opt("CE|23950", 1, 100.0), _opt("CE|23950", 2, 120.0), _opt("CE|23950", 3, 90.0),
    ])
    out = run_premium_momentum_backtest(
        spot_df=spot, option_candles=opt, contracts=contracts, instrument="NIFTY",
        params={"reference_time": "09:31", "moneyness": "itm1", "side": "CE",
                "momentum_pct": 15.0, "stop_pct": 20.0,
                "lazy_enabled": True, "lazy_momentum_pct": 10.0, "lazy_stop_pct": 10.0},
    )
    assert len(out["trades"]) == 1   # only the primary -- no phantom lazy trade
    assert out["trades"][0]["leg"] == "primary"
    assert out["coverage"]["lazy_armed"] == 1
    assert out["coverage"]["lazy_excluded_no_data"] == 1
    assert out["coverage"]["lazy_entered"] == 0


def test_lazy_missing_contract_is_excluded_not_mis_filled():
    spot = pd.DataFrame([
        _spot_bar(1, "09:31", 24000.0), _spot_bar(2, "09:32", 24000.0),
        _spot_bar(3, "09:33", 24100.0),
    ])
    # No PE contract at ANY strike -> lock_reference_strike returns None.
    contracts = [{"instrument_key": "CE|23950", "strike": 23950, "side": "CE", "expiry_date": "2026-07-17"}]
    opt = pd.DataFrame([
        _opt("CE|23950", 1, 100.0), _opt("CE|23950", 2, 120.0), _opt("CE|23950", 3, 90.0),
    ])
    out = run_premium_momentum_backtest(
        spot_df=spot, option_candles=opt, contracts=contracts, instrument="NIFTY",
        params={"reference_time": "09:31", "moneyness": "itm1", "side": "CE",
                "momentum_pct": 15.0, "stop_pct": 20.0,
                "lazy_enabled": True, "lazy_momentum_pct": 10.0, "lazy_stop_pct": 10.0},
    )
    assert len(out["trades"]) == 1
    assert out["coverage"]["lazy_armed"] == 1
    assert out["coverage"]["lazy_excluded_no_data"] == 1
    assert out["coverage"]["lazy_entered"] == 0


# ---------------------------------------------------------------------------
# 11. Fail-loud ValueErrors.
# ---------------------------------------------------------------------------
def _minimal_args():
    spot = pd.DataFrame([_spot_bar(1, "09:31", 24000.0)])
    contracts = [{"instrument_key": "CE|23950", "strike": 23950, "side": "CE", "expiry_date": "2026-07-17"}]
    opt = pd.DataFrame(columns=["instrument_key", "ts", "close"])
    return dict(spot_df=spot, option_candles=opt, contracts=contracts, instrument="NIFTY")


def test_trail_pts_and_pct_both_set_raises():
    with pytest.raises(ValueError):
        run_premium_momentum_backtest(
            **_minimal_args(),
            params={"momentum_pct": 15.0, "stop_pct": 20.0,
                    "trail_x": 5.0, "trail_y": 5.0,
                    "trail_x_pct": 5.0, "trail_y_pct": 5.0},
        )


def test_lazy_enabled_with_no_lazy_trigger_raises():
    with pytest.raises(ValueError):
        run_premium_momentum_backtest(
            **_minimal_args(),
            params={"momentum_pct": 15.0, "stop_pct": 20.0, "lazy_enabled": True,
                    "lazy_stop_pct": 10.0},
        )


def test_lazy_momentum_pct_and_pts_both_set_raises():
    with pytest.raises(ValueError):
        run_premium_momentum_backtest(
            **_minimal_args(),
            params={"momentum_pct": 15.0, "stop_pct": 20.0, "lazy_enabled": True,
                    "lazy_momentum_pct": 10.0, "lazy_momentum_pts": 5.0,
                    "lazy_stop_pct": 10.0},
        )


# ---------------------------------------------------------------------------
# Route/tuner plumbing + frontend wiring (host string-pins over source, the
# repo's standard for the JSX layer -- see test_premium_momentum.py).
# ---------------------------------------------------------------------------
def test_route_tunable_keys_and_band_widening_wired():
    routes_path = (Path(__file__).resolve().parents[1] / "backend" / "app"
                   / "routers" / "premium_momentum_routes.py")
    src = routes_path.read_text(encoding="utf-8")
    for key in ["lazy_momentum_pct", "lazy_stop_pct", "lazy_target_pct",
                "trail_x_pct", "trail_y_pct", "lazy_trail_x_pct", "lazy_trail_y_pct"]:
        assert f'"{key}"' in src
    assert "lazy_enabled" in src
    # The widening decision lives in the pure preload_scope helper (behavior
    # tested below, not just string-pinned) — the route must actually call it.
    assert "preload_scope(moneynesses, sides, lazy_enabled)" in src


# ---------------------------------------------------------------------------
# preload_scope — REAL behavioral tests for the widening rule (replaces a
# string-pin the adversarial review flagged as weaker-than-it-looks: the pin
# passed even with review finding C1 present, where moneyness widened but a
# CE-only run still preloaded zero PE candles, so every lazy activation would
# silently count as lazy_excluded_no_data).
# ---------------------------------------------------------------------------
def test_preload_scope_off_is_passthrough():
    from app.premium_momentum_backtest import preload_scope
    m, s = preload_scope(["itm1"], ["CE"], lazy_enabled=False)
    assert m == ["itm1"]
    assert s == ["CE"]


def test_preload_scope_lazy_widens_moneyness_band_and_forces_both_sides():
    from app.premium_momentum_backtest import FULL_MONEYNESS_BAND, preload_scope
    m, s = preload_scope(["itm1"], ["CE"], lazy_enabled=True)
    # C1 regression pin: a single-side request MUST still preload BOTH sides —
    # the lazy reversal leg is always opposite-side.
    assert s == ["CE", "PE"]
    assert set(FULL_MONEYNESS_BAND) <= set(m)


def test_preload_scope_lazy_unions_requested_moneyness_beyond_band():
    from app.premium_momentum_backtest import preload_scope
    m, s = preload_scope(["otm3"], ["CE", "PE"], lazy_enabled=True)
    assert "otm3" in m  # a requested value outside the band survives the union
    assert s == ["CE", "PE"]


def test_frontend_premium_momentum_contingency_ui_wired():
    fe = Path(__file__).resolve().parents[1] / "frontend" / "src"
    page = (fe / "pages" / "PremiumMomentum.jsx").read_text(encoding="utf-8")
    for testid in ["pm-leg-mode", "pm-trail-x-pct", "pm-trail-y-pct",
                   "pm-entry-cutoff", "pm-exit-time", "pm-lazy-enabled",
                   "pm-lazy-moneyness", "pm-lazy-momentum", "pm-lazy-stop",
                   "pm-lazy-target", "pm-lazy-trail-x-pct", "pm-lazy-trail-y-pct",
                   "pm-lazy-coverage", "pm-by-leg"]:
        assert testid in page
    for field in ["leg_mode", "lazy_enabled", "lazy_moneyness", "lazy_momentum_pct",
                  "lazy_stop_pct", "lazy_target_pct", "lazy_trail_x_pct",
                  "lazy_trail_y_pct", "trail_x_pct", "trail_y_pct",
                  "entry_cutoff", "exit_time"]:
        assert field in page
    assert "lazy_armed" in page and "lazy_entered" in page
    assert "lazy_blocked_cutoff" in page and "lazy_excluded_no_data" in page
    assert "by_leg" in page
