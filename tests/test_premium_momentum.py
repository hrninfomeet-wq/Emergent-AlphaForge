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


# ---------------------------------------------------------------------------
# Adversarial-review regression tests (red-team confirmed findings)
# ---------------------------------------------------------------------------
def test_trail_no_intrabar_lookahead_on_reversal_bar():
    # HIGH finding: a wide-range REVERSAL bar (drops through the base stop, then
    # rallies to a big close) must exit at the BASE stop — bar j's own close must
    # NEVER ratchet the stop that governs bar j's low. Old (buggy) code booked 210
    # here (close 260 ratcheted the stop to 220 before testing low 150).
    import functools
    trail = functools.partial(stepped_trail_stop, x=20.0, y=20.0)
    ts    = [1, 2, 3]
    close = [170, 200, 260]
    low   = [170, 200, 150]
    open_ = [170, 200, 210]
    high  = [170, 200, 260]
    # ref 170, +15% => trigger 195.5 -> entry at close 200 (idx1). stop_pct 20 -> base 160.
    r = walk_premium_momentum(ts=ts, premium=close, low=low, open_=open_, high=high,
                              ref_premium=170.0, entry_pct=15.0, stop_pct=20.0,
                              target_pct=100.0, trail=trail)
    assert r["entered"] and r["exit_reason"] == "STOP"
    assert r["exit_premium"] == 160.0   # base stop — NOT 210 (look-ahead ratchet)


def test_target_intrabar_touch_on_high():
    # MEDIUM finding: a bar whose HIGH pierces the target but whose close falls
    # back must still book the win (symmetric with the intra-bar stop touch).
    ts    = [1, 2, 3]
    close = [200, 235, 255]
    low   = [200, 235, 250]
    open_ = [200, 235, 250]
    high  = [200, 235, 285]   # target 235*1.2 = 282 -> high 285 pierces it
    r = walk_premium_momentum(ts=ts, premium=close, low=low, open_=open_, high=high,
                              ref_premium=200.0, entry_pct=15.0, stop_pct=20.0,
                              target_pct=20.0)
    assert r["entered"] and r["exit_reason"] == "TARGET"
    assert r["exit_premium"] == 282.0   # fill at target level (opened below it)


def test_target_gap_up_fills_at_open():
    # Gap-UP through the target fills at the open (the honest better fill).
    ts    = [1, 2, 3]
    close = [200, 235, 300]
    low   = [200, 235, 290]
    open_ = [200, 235, 295]   # opens ABOVE the 282 target
    high  = [200, 235, 305]
    r = walk_premium_momentum(ts=ts, premium=close, low=low, open_=open_, high=high,
                              ref_premium=200.0, entry_pct=15.0, stop_pct=20.0,
                              target_pct=20.0)
    assert r["entered"] and r["exit_reason"] == "TARGET"
    assert r["exit_premium"] == 295.0   # max(282 target, 295 open)


def test_same_bar_stop_and_target_resolves_stop_first():
    # Wide-range bar touches BOTH levels -> pessimistic stop-first.
    ts    = [1, 2, 3]
    close = [200, 235, 240]
    low   = [200, 235, 180]   # touches 188 stop
    open_ = [200, 235, 240]
    high  = [200, 235, 290]   # also pierces 282 target
    r = walk_premium_momentum(ts=ts, premium=close, low=low, open_=open_, high=high,
                              ref_premium=200.0, entry_pct=15.0, stop_pct=20.0,
                              target_pct=20.0)
    assert r["entered"] and r["exit_reason"] == "STOP"


def test_entry_on_last_bar_is_rejected():
    # LOW finding: a trigger on the final bar has no bar left to manage — a
    # zero-bar phantom "trade" must not be booked.
    r = walk_premium_momentum(ts=[1, 2], premium=[200, 235], ref_premium=200.0,
                              entry_pct=15.0, stop_pct=20.0, target_pct=20.0)
    assert r["entered"] is False


def test_stepped_trail_capped_at_traded_high():
    # Hardening: an aggressive Y >> X must not place the stop above prices that
    # ever traded (stop capped at the high-water mark).
    got = stepped_trail_stop(entry_premium=200.0, running_high=260.0,
                             base_stop=160.0, x=20.0, y=100.0)
    assert got == 260.0   # min(160 + 3*100, 260) — capped, not 460


def test_premium_lookup_canonicalizes_dated_metadata_keys():
    # Expired-contract metadata keys are DATED 3-part (NSE_FO|42390|10-02-2026)
    # while options_1m stores plain 2-part keys. The lookup must canonicalize or
    # every past-expiry session silently reads "no premium series" (this exactly
    # reproduced as 92/127 sessions excluded in the first real backtest run).
    candles = pd.DataFrame([
        {"instrument_key": "NSE_FO|42390", "ts": 1, "close": 100.0},
        {"instrument_key": "NSE_FO|42390", "ts": 2, "close": 110.0},
    ])
    ts, prem = premium_series_for_key(candles, "NSE_FO|42390|10-02-2026")
    assert list(prem) == [100.0, 110.0]
    oh = premium_ohlc_for_key(candles, "NSE_FO|42390|10-02-2026")
    assert list(oh["close"]) == [100.0, 110.0]


def test_frontend_premium_momentum_page_is_wired():
    # UI wiring pins (host string-pins over JSX, the repo's standard): the page
    # exists, the route is registered, and the nav entry points at it.
    from pathlib import Path
    fe = Path(__file__).resolve().parents[1] / "frontend" / "src"
    page = (fe / "pages" / "PremiumMomentum.jsx").read_text(encoding="utf-8")
    assert "/premium-momentum/backtest" in page          # posts to the API route
    assert "pm-coverage" in page and "pm-run-btn" in page
    app = (fe / "App.js").read_text(encoding="utf-8")
    assert '<Route path="/premium-momentum"' in app
    layout = (fe / "components" / "Layout.jsx").read_text(encoding="utf-8")
    assert '"/premium-momentum"' in layout


# ---------------------------------------------------------------------------
# Phase 1.2 — cost model (reuses app.option_costs; nets must match the engine)
# ---------------------------------------------------------------------------
from app.option_costs import CostConfig, round_trip_charges, spread_pts_for_premium
from app.premium_momentum import apply_costs_to_trade


def _trade(entry=100.0, exit_=120.0):
    return {"entered": True, "entry_premium": entry, "exit_premium": exit_,
            "premium_pnl": round(exit_ - entry, 4), "exit_reason": "TARGET"}


def test_apply_costs_spread_and_charges_match_engine_model():
    # spread 2% of premium per SIDE convention = half per fill (mirrors
    # live_friction.fill_premium): BUY 100 -> +1.0, SELL 120 -> -1.2
    cfg = CostConfig.from_dict({"enabled": True, "spread_pct_of_premium": 2.0,
                                "brokerage_per_order": 20.0})
    out = apply_costs_to_trade(_trade(), cost_cfg=cfg, lot_size=65, lots=2)
    assert out["entry_fill"] == 101.0     # 100 + spread(100)*2%/2
    assert out["exit_fill"] == 118.8      # 120 - spread(120)*2%/2
    qty = 65 * 2
    charges = round_trip_charges(entry_premium=101.0, exit_premium=118.8,
                                 quantity=qty, cfg=cfg)["total_charges"]
    assert out["charges_rupees"] == charges                       # engine-identical
    assert out["gross_pnl_rupees"] == round((118.8 - 101.0) * qty, 2)
    assert out["net_pnl_rupees"] == round((118.8 - 101.0) * qty - charges, 2)
    assert out["net_pnl_pts"] == round(out["net_pnl_rupees"] / qty, 4)
    assert out["premium_pnl"] == 20.0     # gross mark P&L untouched


def test_apply_costs_disabled_is_identity_plus_qty_fields():
    cfg = CostConfig()                    # enabled=False default
    t = _trade()
    out = apply_costs_to_trade(t, cost_cfg=cfg, lot_size=65, lots=1)
    assert out["entry_fill"] == 100.0 and out["exit_fill"] == 120.0
    assert out["charges_rupees"] == 0.0
    assert out["net_pnl_pts"] == 20.0     # equals gross when costs off


def test_sim_applies_costs_when_configured():
    spot = pd.DataFrame([
        {"ts": 1, "ist_time": "09:31", "close": 24000.0, "session_date": "2026-07-10"},
        {"ts": 2, "ist_time": "09:32", "close": 24000.0, "session_date": "2026-07-10"},
        {"ts": 3, "ist_time": "09:33", "close": 24000.0, "session_date": "2026-07-10"},
    ])
    contracts = [{"instrument_key": "CE|23950", "strike": 23950, "side": "CE",
                  "expiry_date": "2026-07-14"}]
    opt = pd.DataFrame([
        {"instrument_key": "CE|23950", "ts": 1, "close": 100.0},
        {"instrument_key": "CE|23950", "ts": 2, "close": 120.0},
        {"instrument_key": "CE|23950", "ts": 3, "close": 125.0},
    ])
    from app.premium_momentum_backtest import run_premium_momentum_backtest
    out = run_premium_momentum_backtest(
        spot_df=spot, option_candles=opt, contracts=contracts, instrument="NIFTY",
        params={"reference_time": "09:31", "moneyness": "itm1", "side": "CE",
                "momentum_pct": 15.0, "stop_pct": 50.0,
                "lots": 2,
                "cost_config": {"enabled": True, "spread_pct_of_premium": 2.0}})
    assert len(out["trades"]) == 1
    t = out["trades"][0]
    assert t["entry_fill"] > t["entry_premium"]          # paid the ask
    assert t["exit_fill"] < t["exit_premium"]            # received the bid
    assert "net_pnl_rupees" in t and "charges_rupees" in t
    s = out["summary"]
    assert s["lot_size"] == 65 and s["lots"] == 2
    assert s["net_pnl_rupees"] == t["net_pnl_rupees"]
    assert s["costs_enabled"] is True
