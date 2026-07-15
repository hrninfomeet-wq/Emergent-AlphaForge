# tests/test_premium_momentum_overlays.py
"""Phase 5A.2 -- session day-stop + India VIX gate overlays.

Covers the eleven items (a)-(k) in
docs/superpowers/plans/2026-07-14-premium-momentum-phase5a2-overlays-edge-hunt.md
section 4. Fixture style mirrors tests/test_premium_momentum_contingency.py.

Instrument is always "NIFTY" (lot_size 65, see app.instruments.UNDERLYING_META)
so every rupee figure below is premium_pts * 65 (lots default 1, costs
disabled by default -> net_pnl_rupees == gross).
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

import pandas as pd
import pytest

from app.premium_momentum_backtest import run_premium_momentum_backtest
from app.vix import vix_by_session_map

LOT = 65  # NIFTY lot_size


def _spot_bar(ts, ist, close, session="2026-07-10"):
    return {"ts": ts, "ist_time": ist, "close": close, "session_date": session}


def _opt(key, ts, close):
    return {"instrument_key": key, "ts": ts, "close": close}


# ===========================================================================
# Session day-stop
# ===========================================================================

# ---------------------------------------------------------------------------
# (a) breach on max-loss blocks later entries (counter blocked_day_stop).
# ---------------------------------------------------------------------------
def test_day_stop_max_loss_blocks_later_entry():
    spot = pd.DataFrame([
        _spot_bar(1, "09:31", 24000.0), _spot_bar(2, "09:32", 24000.0),
        _spot_bar(3, "09:33", 24000.0), _spot_bar(4, "09:34", 24000.0),
        _spot_bar(5, "09:35", 24000.0), _spot_bar(6, "09:36", 24000.0),
    ])
    contracts = [
        {"instrument_key": "CE|23950", "strike": 23950, "side": "CE", "expiry_date": "2026-07-17"},
        {"instrument_key": "PE|24050", "strike": 24050, "side": "PE", "expiry_date": "2026-07-17"},
    ]
    opt = pd.DataFrame([
        # CE: ref 100 -> entry ts2 @130 (cross 115); stop 50% -> 65; ts3 close
        # 50 <= 65 -> STOP, fill = min(65,50) = 50 (close-only fallback fill).
        # pnl = 50-130 = -80pts = -5200 rupees -- ALONE breaches -5000.
        _opt("CE|23950", 1, 100.0), _opt("CE|23950", 2, 130.0), _opt("CE|23950", 3, 50.0),
        # PE: stays flat below trigger until ts5 (AFTER the breach at ts3),
        # then crosses and enters at ts5 -- must be dropped regardless of its
        # own subsequent outcome.
        _opt("PE|24050", 1, 100.0), _opt("PE|24050", 2, 100.0), _opt("PE|24050", 3, 100.0),
        _opt("PE|24050", 4, 100.0), _opt("PE|24050", 5, 130.0), _opt("PE|24050", 6, 140.0),
    ])
    out = run_premium_momentum_backtest(
        spot_df=spot, option_candles=opt, contracts=contracts, instrument="NIFTY",
        params={"reference_time": "09:31", "moneyness": "itm1", "leg_mode": "both",
                "momentum_pct": 15.0, "stop_pct": 50.0, "target_pct": 900.0,
                "session_max_loss_rupees": 5000.0},
    )
    trades = out["trades"]
    assert len(trades) == 1
    assert trades[0]["side"] == "CE" and trades[0]["exit_reason"] == "STOP"
    assert trades[0]["exit_ts"] == 3
    assert out["coverage"]["blocked_day_stop"] == 1
    assert out["coverage"]["forced_day_stop_exits"] == 0


# ---------------------------------------------------------------------------
# (b) open leg force-closed at first bar >= breach at close, reason DAY_STOP,
#     costs recomputed.
# ---------------------------------------------------------------------------
def test_day_stop_force_closes_open_leg_at_breach():
    spot = pd.DataFrame([
        _spot_bar(1, "09:31", 24000.0), _spot_bar(2, "09:32", 24000.0),
        _spot_bar(3, "09:33", 24000.0), _spot_bar(4, "09:34", 24000.0),
        _spot_bar(5, "09:35", 24000.0), _spot_bar(6, "09:36", 24000.0),
    ])
    contracts = [
        {"instrument_key": "CE|23950", "strike": 23950, "side": "CE", "expiry_date": "2026-07-17"},
        {"instrument_key": "PE|24050", "strike": 24050, "side": "PE", "expiry_date": "2026-07-17"},
    ]
    opt = pd.DataFrame([
        # CE: same as (a) -- alone breaches -5000 at ts3.
        _opt("CE|23950", 1, 100.0), _opt("CE|23950", 2, 130.0), _opt("CE|23950", 3, 50.0),
        # PE: enters EARLY (ts2, before the breach), then stays open with NO
        # stop/target hit through ts6 (its own natural exit would be EOD@6).
        # entry_ts(2) <= breach_ts(3) < exit_ts(6) -> OPEN AT BREACH.
        _opt("PE|24050", 1, 100.0), _opt("PE|24050", 2, 120.0), _opt("PE|24050", 3, 100.0),
        _opt("PE|24050", 4, 100.0), _opt("PE|24050", 5, 100.0), _opt("PE|24050", 6, 110.0),
    ])
    out = run_premium_momentum_backtest(
        spot_df=spot, option_candles=opt, contracts=contracts, instrument="NIFTY",
        params={"reference_time": "09:31", "moneyness": "itm1", "leg_mode": "both",
                "momentum_pct": 15.0, "stop_pct": 50.0, "target_pct": 900.0,
                "session_max_loss_rupees": 5000.0},
    )
    trades = out["trades"]
    assert len(trades) == 2
    ce = next(t for t in trades if t["side"] == "CE")
    pe = next(t for t in trades if t["side"] == "PE")
    assert ce["exit_reason"] == "STOP" and ce["exit_ts"] == 3   # unaffected -- IS the breach trade

    # PE force-closed at the first bar of ITS OWN series with ts >= breach_ts
    # (3) -- that's ts3 itself (close 100), not its natural ts6 EOD exit.
    assert pe["exit_reason"] == "DAY_STOP"
    assert pe["exit_ts"] == 3 and pe["exit_premium"] == 100.0
    assert pe["entry_ts"] == 2 and pe["entry_premium"] == 120.0
    assert pe["premium_pnl"] == -20.0
    assert pe["net_pnl_rupees"] == -20.0 * LOT   # costs recomputed off the new fill
    assert pe["bars_held"] == 1

    assert out["coverage"]["forced_day_stop_exits"] == 1
    assert out["coverage"]["blocked_day_stop"] == 0


# ---------------------------------------------------------------------------
# (c) same-bar tie: two exits sharing breach_ts both stay realized.
# ---------------------------------------------------------------------------
def test_day_stop_same_bar_tie_both_stay_realized():
    spot = pd.DataFrame([
        _spot_bar(1, "09:31", 24000.0), _spot_bar(2, "09:32", 24000.0),
        _spot_bar(3, "09:33", 24000.0),
    ])
    contracts = [
        {"instrument_key": "CE|23950", "strike": 23950, "side": "CE", "expiry_date": "2026-07-17"},
        {"instrument_key": "PE|24050", "strike": 24050, "side": "PE", "expiry_date": "2026-07-17"},
    ]
    opt = pd.DataFrame([
        # Both enter ts2 @130 (cross 115), both stop at ts3 -- SAME exit_ts.
        # stop_pct=30% shared -> stop level = 91 for both; the fill collapses
        # to the bar's own close (gap-honest, close<=stop).
        # CE: ts3 close 85 -> pnl 85-130=-45pts=-2925 (alone: no breach).
        _opt("CE|23950", 1, 100.0), _opt("CE|23950", 2, 130.0), _opt("CE|23950", 3, 85.0),
        # PE: ts3 close 20 -> pnl 20-130=-110pts=-7150. Combined with CE
        # (-2925-7150=-10075) breaches -5000 -- but the breach is detected
        # AT ts3, the exit_ts BOTH trades share.
        _opt("PE|24050", 1, 100.0), _opt("PE|24050", 2, 130.0), _opt("PE|24050", 3, 20.0),
    ])
    out = run_premium_momentum_backtest(
        spot_df=spot, option_candles=opt, contracts=contracts, instrument="NIFTY",
        params={"reference_time": "09:31", "moneyness": "itm1", "leg_mode": "both",
                "momentum_pct": 15.0, "stop_pct": 30.0, "target_pct": 900.0,
                "session_max_loss_rupees": 5000.0},
    )
    trades = out["trades"]
    assert len(trades) == 2
    ce = next(t for t in trades if t["side"] == "CE")
    pe = next(t for t in trades if t["side"] == "PE")
    # Both stay realized with their ORIGINAL STOP exit -- not DAY_STOP.
    assert ce["exit_reason"] == "STOP" and ce["exit_ts"] == 3 and ce["exit_premium"] == 85.0
    assert pe["exit_reason"] == "STOP" and pe["exit_ts"] == 3 and pe["exit_premium"] == 20.0
    assert out["coverage"]["blocked_day_stop"] == 0
    assert out["coverage"]["forced_day_stop_exits"] == 0


# ---------------------------------------------------------------------------
# (d) max-profit variant.
# ---------------------------------------------------------------------------
def test_day_stop_max_profit_blocks_later_entry():
    spot = pd.DataFrame([
        _spot_bar(1, "09:31", 24000.0), _spot_bar(2, "09:32", 24000.0),
        _spot_bar(3, "09:33", 24000.0), _spot_bar(4, "09:34", 24000.0),
        _spot_bar(5, "09:35", 24000.0), _spot_bar(6, "09:36", 24000.0),
    ])
    contracts = [
        {"instrument_key": "CE|23950", "strike": 23950, "side": "CE", "expiry_date": "2026-07-17"},
        {"instrument_key": "PE|24050", "strike": 24050, "side": "PE", "expiry_date": "2026-07-17"},
    ]
    opt = pd.DataFrame([
        # CE: entry ts2 @120 (cross 115); target 50% -> 180; ts3 close 200
        # >= 180 -> TARGET, fill = max(180,200) = 200. pnl = 200-120 =
        # +80pts = +5200 rupees -- ALONE breaches +5000.
        _opt("CE|23950", 1, 100.0), _opt("CE|23950", 2, 120.0), _opt("CE|23950", 3, 200.0),
        # PE: crosses AFTER the breach (ts5) -- must be dropped.
        _opt("PE|24050", 1, 100.0), _opt("PE|24050", 2, 100.0), _opt("PE|24050", 3, 100.0),
        _opt("PE|24050", 4, 100.0), _opt("PE|24050", 5, 130.0), _opt("PE|24050", 6, 140.0),
    ])
    out = run_premium_momentum_backtest(
        spot_df=spot, option_candles=opt, contracts=contracts, instrument="NIFTY",
        params={"reference_time": "09:31", "moneyness": "itm1", "leg_mode": "both",
                "momentum_pct": 15.0, "stop_pct": 90.0, "target_pct": 50.0,
                "session_max_profit_rupees": 5000.0},
    )
    trades = out["trades"]
    assert len(trades) == 1
    assert trades[0]["side"] == "CE" and trades[0]["exit_reason"] == "TARGET"
    assert trades[0]["exit_ts"] == 3
    assert out["coverage"]["blocked_day_stop"] == 1
    assert out["coverage"]["forced_day_stop_exits"] == 0


# ---------------------------------------------------------------------------
# (e) no caps = byte-identical (parity extension).
# ---------------------------------------------------------------------------
def test_day_stop_and_vix_off_is_byte_identical():
    # Exact fixture from test_parity_default_params_byte_identical (Phase 5A).
    spot = pd.DataFrame([
        _spot_bar(1, "09:31", 24000.0), _spot_bar(2, "09:32", 24010.0),
        _spot_bar(3, "09:33", 24020.0), _spot_bar(4, "09:34", 24020.0),
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
    assert len(out["trades"]) == 1
    t = out["trades"][0]
    assert t["entry_ts"] == 3 and t["entry_premium"] == 120.0
    assert t["exit_ts"] == 4 and t["exit_premium"] == 150.0
    assert t["exit_reason"] == "EOD"
    cov = out["coverage"]
    assert cov["blocked_day_stop"] == 0
    assert cov["forced_day_stop_exits"] == 0
    assert cov["sessions_excluded_vix_gate"] == 0
    assert cov["sessions_excluded_vix_missing"] == 0


# ---------------------------------------------------------------------------
# (f) a lazy arming whose PARENT's stop-out is itself after breach_ts is
#     likewise blocked (counted in blocked_day_stop, NOT decremented from
#     lazy_armed -- that counter reflects what happened during the walk,
#     which day-stop never touches).
# ---------------------------------------------------------------------------
def test_day_stop_blocks_lazy_child_of_a_post_breach_parent_stop():
    spot = pd.DataFrame([
        _spot_bar(1, "09:31", 24000.0), _spot_bar(2, "09:32", 24000.0),
        _spot_bar(3, "09:33", 24000.0), _spot_bar(4, "09:34", 24000.0),
        _spot_bar(5, "09:35", 24000.0), _spot_bar(6, "09:36", 24000.0),
        _spot_bar(7, "09:37", 24000.0),
    ])
    contracts = [
        {"instrument_key": "CE|23950", "strike": 23950, "side": "CE", "expiry_date": "2026-07-17"},
        {"instrument_key": "PE|24050", "strike": 24050, "side": "PE", "expiry_date": "2026-07-17"},
    ]
    opt = pd.DataFrame([
        # A (CE, primary): entry ts2 @130, stop 50%->65, ts3 close 50<=65 ->
        # STOP @50. pnl -80pts = -5200 -- ALONE breaches -5000 at ts3.
        # Candles continue past its own exit (ts4-7) ONLY to also serve as
        # the fresh strike B's lazy child later locks onto (same spot ->
        # same itm1 CE strike) -- A's own walk still terminates at ts3.
        _opt("CE|23950", 1, 100.0), _opt("CE|23950", 2, 130.0), _opt("CE|23950", 3, 50.0),
        _opt("CE|23950", 4, 55.0), _opt("CE|23950", 5, 60.0),
        _opt("CE|23950", 6, 75.0), _opt("CE|23950", 7, 80.0),
        # B (PE, primary): entry ts2 @120 (cross 115), stop 50%->60. Stays
        # open through ts3 (the breach bar: 100 > 60) and ts4 (90 > 60), then
        # STOPS at ts5 (50 <= 60 -> fill 50). entry_ts(2) <= breach_ts(3) <
        # exit_ts(5) -> B itself is OPEN AT BREACH -> force-closed.
        _opt("PE|24050", 1, 100.0), _opt("PE|24050", 2, 120.0), _opt("PE|24050", 3, 100.0),
        _opt("PE|24050", 4, 90.0), _opt("PE|24050", 5, 50.0),
    ])
    out = run_premium_momentum_backtest(
        spot_df=spot, option_candles=opt, contracts=contracts, instrument="NIFTY",
        params={"reference_time": "09:31", "moneyness": "itm1", "leg_mode": "both",
                "momentum_pct": 15.0, "stop_pct": 50.0, "target_pct": 900.0,
                "lazy_enabled": True, "lazy_momentum_pct": 10.0, "lazy_stop_pct": 10.0,
                "session_max_loss_rupees": 5000.0},
    )
    trades = out["trades"]
    # A (unaffected, IS the breach trade) + B (force-closed) survive; B's
    # lazy child (armed at B's TRUE stop-out ts5, entered ts6 > breach_ts)
    # is dropped -- never reaches the output.
    assert len(trades) == 2
    a = next(t for t in trades if t["side"] == "CE")
    b = next(t for t in trades if t["side"] == "PE")
    assert a["exit_reason"] == "STOP" and a["exit_ts"] == 3
    assert b["exit_reason"] == "DAY_STOP" and b["exit_ts"] == 3 and b["exit_premium"] == 100.0
    assert all(t["leg"] == "primary" for t in trades)   # the lazy child never survives

    cov = out["coverage"]
    # lazy_armed reflects the WALK (unaffected by day-stop): A's own STOP
    # also attempts an arming (opposite=PE, at ts3) that finds no crossing
    # data and never enters; B's STOP (at ts5) arms a lazy CE' that DOES
    # enter (ts6) -- so lazy_armed == 2, lazy_entered == 1, both untouched
    # by the day-stop drop that follows.
    assert cov["lazy_armed"] == 2
    assert cov["lazy_entered"] == 1
    assert cov["blocked_day_stop"] == 1
    assert cov["forced_day_stop_exits"] == 1
    assert out["summary"]["by_leg"]["lazy"]["trades"] == 0


# ===========================================================================
# India VIX gate
# ===========================================================================

def _two_session_ce_fixture():
    spot = pd.DataFrame([
        _spot_bar(1, "09:31", 24000.0, session="2026-07-10"),
        _spot_bar(2, "09:32", 24000.0, session="2026-07-10"),
        _spot_bar(3, "09:33", 24000.0, session="2026-07-10"),
        _spot_bar(4, "09:34", 24000.0, session="2026-07-10"),
        _spot_bar(11, "09:31", 24000.0, session="2026-07-11"),
        _spot_bar(12, "09:32", 24000.0, session="2026-07-11"),
        _spot_bar(13, "09:33", 24000.0, session="2026-07-11"),
        _spot_bar(14, "09:34", 24000.0, session="2026-07-11"),
    ])
    contracts = [
        {"instrument_key": "CE|23950", "strike": 23950, "side": "CE", "expiry_date": "2026-07-17"},
    ]
    opt = pd.DataFrame([
        _opt("CE|23950", 1, 100.0), _opt("CE|23950", 2, 120.0),
        _opt("CE|23950", 3, 125.0), _opt("CE|23950", 4, 130.0),
        _opt("CE|23950", 11, 100.0), _opt("CE|23950", 12, 120.0),
        _opt("CE|23950", 13, 125.0), _opt("CE|23950", 14, 130.0),
    ])
    return spot, contracts, opt


# ---------------------------------------------------------------------------
# (g) gate excludes an out-of-band session, with its own counter.
# ---------------------------------------------------------------------------
def test_vix_gate_excludes_out_of_band_session():
    spot, contracts, opt = _two_session_ce_fixture()
    vix_by_session = {"2026-07-10": 15.0, "2026-07-11": 25.0}
    out = run_premium_momentum_backtest(
        spot_df=spot, option_candles=opt, contracts=contracts, instrument="NIFTY",
        params={"reference_time": "09:31", "moneyness": "itm1", "side": "CE",
                "momentum_pct": 15.0, "stop_pct": 90.0, "target_pct": 900.0,
                "vix_min": 12.0, "vix_max": 18.0},
        vix_by_session=vix_by_session,
    )
    cov = out["coverage"]
    assert cov["sessions_total"] == 2
    assert cov["sessions_excluded_vix_gate"] == 1
    assert cov["sessions_excluded_vix_missing"] == 0
    assert cov["sessions_traded"] == 1
    assert len(out["trades"]) == 1
    assert out["trades"][0]["session_date"] == "2026-07-10"


# ---------------------------------------------------------------------------
# (h) gate + missing VIX = excluded with the MISSING counter, not a pass.
# ---------------------------------------------------------------------------
def test_vix_gate_missing_session_excluded_not_passed():
    spot, contracts, opt = _two_session_ce_fixture()
    vix_by_session = {"2026-07-11": 15.0}   # 2026-07-10 absent from the map
    out = run_premium_momentum_backtest(
        spot_df=spot, option_candles=opt, contracts=contracts, instrument="NIFTY",
        params={"reference_time": "09:31", "moneyness": "itm1", "side": "CE",
                "momentum_pct": 15.0, "stop_pct": 90.0, "target_pct": 900.0,
                "vix_min": 12.0, "vix_max": 18.0},
        vix_by_session=vix_by_session,
    )
    cov = out["coverage"]
    assert cov["sessions_excluded_vix_missing"] == 1
    assert cov["sessions_excluded_vix_gate"] == 0
    assert cov["sessions_traded"] == 1
    assert all(t["session_date"] != "2026-07-10" for t in out["trades"])


# ---------------------------------------------------------------------------
# (i) no gate + no map = byte-identical.
# ---------------------------------------------------------------------------
def test_vix_no_gate_no_map_is_byte_identical():
    spot, contracts, opt = _two_session_ce_fixture()
    baseline_params = {"reference_time": "09:31", "moneyness": "itm1", "side": "CE",
                       "momentum_pct": 15.0, "stop_pct": 90.0, "target_pct": 900.0}
    baseline = run_premium_momentum_backtest(
        spot_df=spot, option_candles=opt, contracts=contracts, instrument="NIFTY",
        params=baseline_params,
    )
    with_none_map = run_premium_momentum_backtest(
        spot_df=spot, option_candles=opt, contracts=contracts, instrument="NIFTY",
        params=baseline_params, vix_by_session=None,
    )
    assert baseline["trades"] == with_none_map["trades"]
    assert baseline["coverage"]["sessions_traded"] == with_none_map["coverage"]["sessions_traded"] == 2
    assert with_none_map["coverage"]["sessions_excluded_vix_gate"] == 0
    assert with_none_map["coverage"]["sessions_excluded_vix_missing"] == 0


# ---------------------------------------------------------------------------
# (j) map ignored when no gate is configured.
# ---------------------------------------------------------------------------
def test_vix_map_ignored_when_gate_not_configured():
    spot, contracts, opt = _two_session_ce_fixture()
    # This map would exclude BOTH sessions if a gate were active (out-of-band
    # + missing) -- but no vix_min/vix_max is set, so it must be inert.
    vix_by_session = {"2026-07-11": 999.0}
    out = run_premium_momentum_backtest(
        spot_df=spot, option_candles=opt, contracts=contracts, instrument="NIFTY",
        params={"reference_time": "09:31", "moneyness": "itm1", "side": "CE",
                "momentum_pct": 15.0, "stop_pct": 90.0, "target_pct": 900.0},
        vix_by_session=vix_by_session,
    )
    cov = out["coverage"]
    assert cov["sessions_traded"] == 2
    assert cov["sessions_excluded_vix_gate"] == 0
    assert cov["sessions_excluded_vix_missing"] == 0


# ===========================================================================
# (k) Fail-loud ValueErrors.
# ===========================================================================
def _minimal_args():
    spot = pd.DataFrame([_spot_bar(1, "09:31", 24000.0)])
    contracts = [{"instrument_key": "CE|23950", "strike": 23950, "side": "CE", "expiry_date": "2026-07-17"}]
    opt = pd.DataFrame(columns=["instrument_key", "ts", "close"])
    return dict(spot_df=spot, option_candles=opt, contracts=contracts, instrument="NIFTY")


def test_vix_min_greater_than_max_raises():
    with pytest.raises(ValueError):
        run_premium_momentum_backtest(
            **_minimal_args(),
            params={"momentum_pct": 15.0, "stop_pct": 20.0, "vix_min": 20.0, "vix_max": 10.0},
        )


def test_negative_session_max_loss_raises():
    with pytest.raises(ValueError):
        run_premium_momentum_backtest(
            **_minimal_args(),
            params={"momentum_pct": 15.0, "stop_pct": 20.0, "session_max_loss_rupees": -100.0},
        )


def test_negative_session_max_profit_raises():
    with pytest.raises(ValueError):
        run_premium_momentum_backtest(
            **_minimal_args(),
            params={"momentum_pct": 15.0, "stop_pct": 20.0, "session_max_profit_rupees": -100.0},
        )


# ===========================================================================
# Route wiring (string-pins, house convention -- see
# test_route_tunable_keys_and_band_widening_wired in the contingency file).
# ===========================================================================
def test_route_tunable_keys_include_overlay_params():
    routes_path = (Path(__file__).resolve().parents[1] / "backend" / "app"
                   / "routers" / "premium_momentum_routes.py")
    src = routes_path.read_text(encoding="utf-8")
    for key in ["session_max_loss_rupees", "session_max_profit_rupees",
                "vix_min", "vix_max", "entry_cutoff", "exit_time"]:
        assert f'"{key}"' in src


def test_route_builds_vix_map_only_when_gate_configured():
    routes_path = (Path(__file__).resolve().parents[1] / "backend" / "app"
                   / "routers" / "premium_momentum_routes.py")
    src = routes_path.read_text(encoding="utf-8")
    assert "vix_by_session_map" in src
    assert "vix_min" in src and "vix_max" in src


# ===========================================================================
# app.vix.vix_by_session_map -- pure, host-testable (the route's asof rule).
# ===========================================================================
def test_vix_by_session_map_asof_within_session():
    spot = pd.DataFrame([
        _spot_bar(1, "09:31", 24000.0, session="2026-07-10"),
        _spot_bar(2, "09:45", 24000.0, session="2026-07-10"),
    ])
    vix_candles = [{"ts": 1, "close": 14.5}, {"ts": 2, "close": 15.5}]
    m = vix_by_session_map(spot, vix_candles, ref_time="09:31")
    # ref bar is ts1 (first bar >= 09:31) -> asof <= ts1 -> 14.5.
    assert m == {"2026-07-10": 14.5}


def test_vix_by_session_map_falls_back_to_previous_session_within_5_days():
    spot = pd.DataFrame([
        _spot_bar(100, "09:31", 24000.0, session="2026-07-11"),
    ])
    # No VIX print on 2026-07-11 itself -- only a prior print, within 5 days.
    vix_candles = [{"ts": 50, "close": 13.0}]
    five_days_ms = 5 * 24 * 3600 * 1000
    m = vix_by_session_map(spot, vix_candles, ref_time="09:31", max_staleness_ms=five_days_ms)
    assert m == {"2026-07-11": 13.0}


def test_vix_by_session_map_absent_beyond_staleness():
    spot = pd.DataFrame([
        _spot_bar(10_000_000_000, "09:31", 24000.0, session="2026-07-11"),
    ])
    vix_candles = [{"ts": 1, "close": 13.0}]   # far more than 5 days stale
    five_days_ms = 5 * 24 * 3600 * 1000
    m = vix_by_session_map(spot, vix_candles, ref_time="09:31", max_staleness_ms=five_days_ms)
    assert m == {}


# ---------------------------------------------------------------------------
# Review-note closures (Fable honesty review, wf_2e2c9cbc): two behaviors that
# were verified by code-read only — pin them with real fixtures.
# ---------------------------------------------------------------------------
def test_day_stop_entry_exactly_at_breach_ts_is_kept_not_dropped():
    """Strict `>` boundary: a trade entered exactly AT breach_ts is NOT a
    blocked entry — it falls into the force-close branch (entry <= breach <
    exit) and is flattened at its own first bar >= breach at close."""
    spot = pd.DataFrame([
        _spot_bar(1, "09:31", 24000.0), _spot_bar(2, "09:32", 24000.0),
        _spot_bar(3, "09:33", 24000.0), _spot_bar(4, "09:34", 24000.0),
        _spot_bar(5, "09:35", 24000.0), _spot_bar(6, "09:36", 24000.0),
    ])
    contracts = [
        {"instrument_key": "CE|23950", "strike": 23950, "side": "CE", "expiry_date": "2026-07-17"},
        {"instrument_key": "PE|24050", "strike": 24050, "side": "PE", "expiry_date": "2026-07-17"},
    ]
    opt = pd.DataFrame([
        # CE: entry ts2 @130, STOP ts3 -> -80 pts = -5200 rupees, breach_ts = 3.
        _opt("CE|23950", 1, 100.0), _opt("CE|23950", 2, 130.0), _opt("CE|23950", 3, 50.0),
        # PE: crosses AT ts3 — entry_ts == breach_ts exactly. Must be KEPT and
        # force-closed (not silently dropped as a blocked entry).
        _opt("PE|24050", 1, 100.0), _opt("PE|24050", 2, 100.0), _opt("PE|24050", 3, 130.0),
        _opt("PE|24050", 4, 140.0), _opt("PE|24050", 5, 150.0), _opt("PE|24050", 6, 160.0),
    ])
    out = run_premium_momentum_backtest(
        spot_df=spot, option_candles=opt, contracts=contracts, instrument="NIFTY",
        params={"reference_time": "09:31", "moneyness": "itm1", "leg_mode": "both",
                "momentum_pct": 15.0, "stop_pct": 50.0, "target_pct": 900.0,
                "session_max_loss_rupees": 5000.0},
    )
    trades = out["trades"]
    assert len(trades) == 2, "the AT-breach entry must be kept, not dropped"
    pe = next(t for t in trades if t["side"] == "PE")
    assert pe["entry_ts"] == 3
    assert pe["exit_reason"] == "DAY_STOP"
    assert pe["exit_ts"] == 3  # first own-series bar >= breach is the entry bar itself
    assert pe["exit_premium"] == 130.0  # that bar's close == entry -> ~flat fill
    assert out["coverage"]["blocked_day_stop"] == 0
    assert out["coverage"]["forced_day_stop_exits"] == 1


def test_day_stop_breach_scan_uses_net_not_gross_rupees():
    """Discriminating fixture: gross loss 5200 < cap 5250 < net loss (~5317 via
    1%/side spread + charges). Costs ON -> the cap breaches (PE blocked);
    costs OFF -> no breach (PE trades). Only a NET-based scan produces this."""
    spot = pd.DataFrame([
        _spot_bar(1, "09:31", 24000.0), _spot_bar(2, "09:32", 24000.0),
        _spot_bar(3, "09:33", 24000.0), _spot_bar(4, "09:34", 24000.0),
        _spot_bar(5, "09:35", 24000.0), _spot_bar(6, "09:36", 24000.0),
    ])
    contracts = [
        {"instrument_key": "CE|23950", "strike": 23950, "side": "CE", "expiry_date": "2026-07-17"},
        {"instrument_key": "PE|24050", "strike": 24050, "side": "PE", "expiry_date": "2026-07-17"},
    ]
    opt = pd.DataFrame([
        _opt("CE|23950", 1, 100.0), _opt("CE|23950", 2, 130.0), _opt("CE|23950", 3, 50.0),
        _opt("PE|24050", 1, 100.0), _opt("PE|24050", 2, 100.0), _opt("PE|24050", 3, 100.0),
        _opt("PE|24050", 4, 100.0), _opt("PE|24050", 5, 130.0), _opt("PE|24050", 6, 140.0),
    ])
    # NOTE: gross_pnl_rupees is FILL-based (post-spread, pre-charges): entry
    # fill 130.65 / exit fill 49.75 -> gross -5258.5. Brokerage 50/leg makes
    # net ~ -5375. Cap 5300 sits between them with >=40 rupees margin each side.
    base = {"reference_time": "09:31", "moneyness": "itm1", "leg_mode": "both",
            "momentum_pct": 15.0, "stop_pct": 50.0, "target_pct": 900.0,
            "session_max_loss_rupees": 5300.0}

    costs_on = run_premium_momentum_backtest(
        spot_df=spot, option_candles=opt, contracts=contracts, instrument="NIFTY",
        params={**base, "cost_config": {"enabled": True, "spread_pct_of_premium": 1.0,
                                        "brokerage_per_order": 50.0}},
    )
    assert abs(costs_on["trades"][0]["gross_pnl_rupees"]) < 5300.0 < \
        abs(costs_on["trades"][0]["net_pnl_rupees"]), "fixture must straddle the cap"
    assert len(costs_on["trades"]) == 1, "net breached -> later PE entry blocked"
    assert costs_on["coverage"]["blocked_day_stop"] == 1

    costs_off = run_premium_momentum_backtest(
        spot_df=spot, option_candles=opt, contracts=contracts, instrument="NIFTY",
        params=dict(base),
    )
    assert len(costs_off["trades"]) == 2, "mark-based 5200 under the 5300 cap -> no breach"
    assert costs_off["coverage"]["blocked_day_stop"] == 0
