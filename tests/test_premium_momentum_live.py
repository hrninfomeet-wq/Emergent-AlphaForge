# tests/test_premium_momentum_live.py
import asyncio
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.premium_momentum_live import evaluate_premium_momentum_bar, _ist_hhmm
from tests.test_premium_lock_store import _FakeLocks   # reuse the fake collection


def run(c):
    return asyncio.run(c)


_CONTRACTS = [
    {"instrument_key": "CE|23950", "strike": 23950, "side": "CE", "expiry_date": "2026-07-14", "lot_size": 65},
    {"instrument_key": "PE|24050", "strike": 24050, "side": "PE", "expiry_date": "2026-07-14", "lot_size": 65},
]


def _tickmap(d):
    # latest_tick_map shape: {instrument_key: {"last_price": x, "ts": epoch_ms}}
    def lookup():
        return d
    return lookup


def _dep(params=None):
    return {"id": "D1", "strategy_id": "premium_momentum",
            "params": {"reference_time": "09:31", "moneyness": "itm1",
                       "side": "first_to_trigger", "momentum_pct": 15.0,
                       "stop_pct": 20.0, "late_lock_cutoff": "10:15",
                       **(params or {})}}


# candle_ts constants: real epoch-ms for 2026-07-10 09:29 / 09:31 IST
# (09:31 IST == 04:01 UTC), computed via zoneinfo — no hand-baked literals.
_IST = ZoneInfo("Asia/Kolkata")
TS_0929 = int(datetime(2026, 7, 10, 9, 29, tzinfo=_IST).timestamp() * 1000)
TS_0931 = int(datetime(2026, 7, 10, 9, 31, tzinfo=_IST).timestamp() * 1000)
# self-check: a wrong constant fails loudly at import time
assert _ist_hhmm(TS_0929) == "09:29"
assert _ist_hhmm(TS_0931) == "09:31"


def test_holding_before_reference_time():
    locks = _FakeLocks()
    out = run(evaluate_premium_momentum_bar(
        locks_col=locks, deployment=_dep(), instrument="NIFTY",
        candle_ts=TS_0929, spot_close=24000.0, contracts=_CONTRACTS,
        latest_tick_map=_tickmap({}), now_ts=TS_0929 / 1000 + 60,
    ))
    assert out["outcome"] == "pre_reference"
    assert locks.docs == []                       # nothing persisted yet


def test_lock_and_ref_capture_at_reference_bar():
    locks = _FakeLocks()
    ticks = {"CE|23950": {"last_price": 100.0, "ts": TS_0931 + 55_000},
             "PE|24050": {"last_price": 110.0, "ts": TS_0931 + 55_000}}
    out = run(evaluate_premium_momentum_bar(
        locks_col=locks, deployment=_dep(), instrument="NIFTY",
        candle_ts=TS_0931, spot_close=24000.0, contracts=_CONTRACTS,
        latest_tick_map=_tickmap(ticks), now_ts=TS_0931 / 1000 + 60,
    ))
    assert out["outcome"] == "monitoring"
    doc = locks.docs[0]
    assert doc["ce"]["strike"] == 23950 and doc["pe"]["strike"] == 24050
    assert doc["ce_ref_premium"] == 100.0 and doc["pe_ref_premium"] == 110.0
    # ref_ts persists the TICK's timestamp in epoch-MS (audit/recovery source)
    assert doc["ce_ref_ts"] == TS_0931 + 55_000
    assert doc["pe_ref_ts"] == TS_0931 + 55_000


def test_stale_tick_holds_never_captures():
    locks = _FakeLocks()
    stale = {"CE|23950": {"last_price": 100.0, "ts": TS_0931 - 10 * 60_000},
             "PE|24050": {"last_price": 110.0, "ts": TS_0931 - 10 * 60_000}}
    out = run(evaluate_premium_momentum_bar(
        locks_col=locks, deployment=_dep(), instrument="NIFTY",
        candle_ts=TS_0931, spot_close=24000.0, contracts=_CONTRACTS,
        latest_tick_map=_tickmap(stale), now_ts=TS_0931 / 1000 + 60,
    ))
    assert out["outcome"] == "awaiting_ref"       # strikes locked, refs NOT captured
    doc = locks.docs[0]
    assert "ce_ref_premium" not in doc


def test_late_lock_cutoff_marks_done():
    locks = _FakeLocks()
    ts_1016 = TS_0931 + 45 * 60_000               # 10:16 IST
    out = run(evaluate_premium_momentum_bar(
        locks_col=locks, deployment=_dep(), instrument="NIFTY",
        candle_ts=ts_1016, spot_close=24000.0, contracts=_CONTRACTS,
        latest_tick_map=_tickmap({}), now_ts=ts_1016 / 1000 + 60,
    ))
    assert out["outcome"] == "done"
    assert locks.docs[0]["done_reason"] == "no_lock"


def test_trigger_first_to_cross_uses_locked_contract():
    locks = _FakeLocks()
    ticks = {"CE|23950": {"last_price": 100.0, "ts": TS_0931 + 55_000},
             "PE|24050": {"last_price": 110.0, "ts": TS_0931 + 55_000}}
    run(evaluate_premium_momentum_bar(          # bar 1: lock + refs
        locks_col=locks, deployment=_dep(), instrument="NIFTY",
        candle_ts=TS_0931, spot_close=24000.0, contracts=_CONTRACTS,
        latest_tick_map=_tickmap(ticks), now_ts=TS_0931 / 1000 + 60,
    ))
    ts2 = TS_0931 + 60_000
    ticks2 = {"CE|23950": {"last_price": 116.0, "ts": ts2 + 55_000},   # +16% > 15%
              "PE|24050": {"last_price": 111.0, "ts": ts2 + 55_000}}
    out = run(evaluate_premium_momentum_bar(
        locks_col=locks, deployment=_dep(), instrument="NIFTY",
        candle_ts=ts2, spot_close=26000.0,        # spot moved FAR — lock must hold
        contracts=_CONTRACTS, latest_tick_map=_tickmap(ticks2), now_ts=ts2 / 1000 + 60,
    ))
    assert out["outcome"] == "triggered"
    assert out["direction"] == "CE"
    assert out["contract"]["instrument_key"] == "CE|23950"   # from LOCK, not spot 26000
    assert out["ref_premium"] == 100.0 and out["premium_now"] == 116.0
    # latch is NOT set here — the evaluator sets it only after the signal journals
    assert locks.docs[0]["triggered_side"] is None


def test_no_refire_when_done_or_triggered():
    locks = _FakeLocks()
    run(locks.insert_one({"deployment_id": "D1", "session_date": "2026-07-10",
                          "triggered_side": "CE", "done_for_day": False,
                          "entered_norenordno": "N1"}))
    ts2 = TS_0931 + 120_000
    out = run(evaluate_premium_momentum_bar(
        locks_col=locks, deployment=_dep(), instrument="NIFTY",
        candle_ts=ts2, spot_close=24000.0, contracts=_CONTRACTS,
        latest_tick_map=_tickmap({}), now_ts=ts2 / 1000 + 60,
    ))
    assert out["outcome"] == "holding_position"


def test_missing_contract_marks_done_strike_lock_failed():
    locks = _FakeLocks()
    ce_only = [_CONTRACTS[0]]                     # PE strike absent from coverage
    out = run(evaluate_premium_momentum_bar(
        locks_col=locks, deployment=_dep(), instrument="NIFTY",
        candle_ts=TS_0931, spot_close=24000.0, contracts=ce_only,
        latest_tick_map=_tickmap({}), now_ts=TS_0931 / 1000 + 60,
    ))
    assert out["outcome"] == "done" and out["reason"] == "strike_lock_failed"
    assert "strike_lock_failed (PE itm1)" in out["blockers"]
    assert locks.docs[0]["done_reason"] == "strike_lock_failed"


def test_refs_missing_past_cutoff_marks_done_no_lock():
    locks = _FakeLocks()
    stale = {"CE|23950": {"last_price": 100.0, "ts": TS_0931 - 10 * 60_000},
             "PE|24050": {"last_price": 110.0, "ts": TS_0931 - 10 * 60_000}}
    run(evaluate_premium_momentum_bar(            # bar 1: strikes lock, refs HOLD
        locks_col=locks, deployment=_dep(), instrument="NIFTY",
        candle_ts=TS_0931, spot_close=24000.0, contracts=_CONTRACTS,
        latest_tick_map=_tickmap(stale), now_ts=TS_0931 / 1000 + 60,
    ))
    ts_1016 = TS_0931 + 45 * 60_000               # 10:16 IST, refs still missing
    out = run(evaluate_premium_momentum_bar(
        locks_col=locks, deployment=_dep(), instrument="NIFTY",
        candle_ts=ts_1016, spot_close=24000.0, contracts=_CONTRACTS,
        latest_tick_map=_tickmap(stale), now_ts=ts_1016 / 1000 + 60,
    ))
    assert out["outcome"] == "done" and out["reason"] == "no_lock"
    doc = locks.docs[0]
    assert doc["done_reason"] == "no_lock"
    assert doc["ce"]["strike"] == 23950           # audit doc keeps the locked strikes


def test_single_side_ce_locks_only_ce_and_triggers():
    locks = _FakeLocks()
    dep = _dep({"side": "ce"})
    ticks = {"CE|23950": {"last_price": 100.0, "ts": TS_0931 + 55_000}}
    out = run(evaluate_premium_momentum_bar(
        locks_col=locks, deployment=dep, instrument="NIFTY",
        candle_ts=TS_0931, spot_close=24000.0, contracts=_CONTRACTS,
        latest_tick_map=_tickmap(ticks), now_ts=TS_0931 / 1000 + 60,
    ))
    assert out["outcome"] == "monitoring"
    doc = locks.docs[0]
    assert doc["ce"]["strike"] == 23950 and "pe" not in doc
    assert doc["ce_ref_premium"] == 100.0 and "pe_ref_premium" not in doc
    ts2 = TS_0931 + 60_000
    ticks2 = {"CE|23950": {"last_price": 116.0, "ts": ts2 + 55_000}}   # +16% > 15%
    out = run(evaluate_premium_momentum_bar(
        locks_col=locks, deployment=dep, instrument="NIFTY",
        candle_ts=ts2, spot_close=24000.0, contracts=_CONTRACTS,
        latest_tick_map=_tickmap(ticks2), now_ts=ts2 / 1000 + 60,
    ))
    assert out["outcome"] == "triggered" and out["direction"] == "CE"


def test_single_side_pe_locks_only_pe():
    locks = _FakeLocks()
    dep = _dep({"side": "pe"})
    ticks = {"PE|24050": {"last_price": 110.0, "ts": TS_0931 + 55_000}}
    out = run(evaluate_premium_momentum_bar(
        locks_col=locks, deployment=dep, instrument="NIFTY",
        candle_ts=TS_0931, spot_close=24000.0, contracts=_CONTRACTS,
        latest_tick_map=_tickmap(ticks), now_ts=TS_0931 / 1000 + 60,
    ))
    assert out["outcome"] == "monitoring"
    doc = locks.docs[0]
    assert doc["pe"]["strike"] == 24050 and "ce" not in doc
    assert doc["pe_ref_premium"] == 110.0 and "ce_ref_premium" not in doc


def test_momentum_pts_wins_when_both_set_no_error():
    """The registration schema defaults momentum_pct=15.0 — a user-set
    momentum_pts must take precedence, NOT raise ValueError every bar."""
    locks = _FakeLocks()
    dep = _dep({"momentum_pts": 20.0})            # pct default 15.0 still present
    ticks = {"CE|23950": {"last_price": 100.0, "ts": TS_0931 + 55_000},
             "PE|24050": {"last_price": 110.0, "ts": TS_0931 + 55_000}}
    run(evaluate_premium_momentum_bar(            # bar 1: lock + refs
        locks_col=locks, deployment=dep, instrument="NIFTY",
        candle_ts=TS_0931, spot_close=24000.0, contracts=_CONTRACTS,
        latest_tick_map=_tickmap(ticks), now_ts=TS_0931 / 1000 + 60,
    ))
    ts2 = TS_0931 + 60_000
    ticks2 = {"CE|23950": {"last_price": 116.0, "ts": ts2 + 55_000},   # +16% BUT +16 pts < 20
              "PE|24050": {"last_price": 111.0, "ts": ts2 + 55_000}}
    out = run(evaluate_premium_momentum_bar(
        locks_col=locks, deployment=dep, instrument="NIFTY",
        candle_ts=ts2, spot_close=24000.0, contracts=_CONTRACTS,
        latest_tick_map=_tickmap(ticks2), now_ts=ts2 / 1000 + 60,
    ))
    assert out["outcome"] == "monitoring"         # pts rules: 16 pts < 20 — no trigger
    ts3 = TS_0931 + 120_000
    ticks3 = {"CE|23950": {"last_price": 120.5, "ts": ts3 + 55_000},   # +20.5 pts >= 20
              "PE|24050": {"last_price": 111.0, "ts": ts3 + 55_000}}
    out = run(evaluate_premium_momentum_bar(
        locks_col=locks, deployment=dep, instrument="NIFTY",
        candle_ts=ts3, spot_close=24000.0, contracts=_CONTRACTS,
        latest_tick_map=_tickmap(ticks3), now_ts=ts3 / 1000 + 60,
    ))
    assert out["outcome"] == "triggered" and out["direction"] == "CE"


class _RacyLocks(_FakeLocks):
    """Injects a concurrent CE ref-capture (60.0) right before the engine's own
    filtered capture update — the engine must LOSE first-wins and adopt 60.0."""

    async def update_one(self, q, upd):
        sets = upd.get("$set", {})
        if "ce_ref_premium" in sets and not getattr(self, "_raced", False):
            self._raced = True
            for d in self.docs:
                d["ce_ref_premium"] = 60.0
                d["ce_ref_ts"] = 1
        return await super().update_one(q, upd)


def test_capture_ref_race_adopts_persisted_winner():
    locks = _RacyLocks()
    ticks = {"CE|23950": {"last_price": 116.0, "ts": TS_0931 + 55_000},
             "PE|24050": {"last_price": 110.0, "ts": TS_0931 + 55_000}}
    out = run(evaluate_premium_momentum_bar(
        locks_col=locks, deployment=_dep(), instrument="NIFTY",
        candle_ts=TS_0931, spot_close=24000.0, contracts=_CONTRACTS,
        latest_tick_map=_tickmap(ticks), now_ts=TS_0931 / 1000 + 60,
    ))
    # persisted winner (60.0) is the ref — our losing 116.0 is discarded, and
    # monitoring on THIS bar evaluates against the PERSISTED value: 116 >= 60*1.15
    assert locks.docs[0]["ce_ref_premium"] == 60.0
    assert out["outcome"] == "triggered" and out["direction"] == "CE"
    assert out["ref_premium"] == 60.0 and out["premium_now"] == 116.0


# =============================================================================
# Phase 5B (Task A3) — both-mode independent legs, lazy pickup, entry_cutoff,
# VIX gate. See docs/superpowers/plans/2026-07-15-premium-momentum-phase5b-
# execution.md Task A3 + the parity-divergence table in the design spec.
# =============================================================================

def test_first_to_trigger_mode_ignores_lazy_armed_flag_by_design():
    """Documented, plan-directed limitation: the pinned first_to_trigger
    terminal check returns holding_position unconditionally once any side
    has latched — the lazy-pickup code below it is structurally unreachable
    in this mode. lazy_enabled is intended to pair with leg_mode="both"."""
    locks = _FakeLocks()
    run(locks.insert_one({"deployment_id": "D1", "session_date": "2026-07-10",
                          "triggered_side": "PE", "done_for_day": False,
                          "entered_norenordno": "N1",
                          "lazy_armed_ce": True}))   # simulate B6 arming (out of scope here)
    dep = _dep({"lazy_enabled": True})   # leg_mode defaults to first_to_trigger
    ts2 = TS_0931 + 120_000
    ce_ticks = {"CE|23950": {"last_price": 100.0, "ts": ts2 + 55_000}}
    out = run(evaluate_premium_momentum_bar(
        locks_col=locks, deployment=dep, instrument="NIFTY",
        candle_ts=ts2, spot_close=24000.0, contracts=_CONTRACTS,
        latest_tick_map=_tickmap(ce_ticks), now_ts=ts2 / 1000 + 60,
    ))
    assert out["outcome"] == "holding_position"
    assert "lce_instrument_key" not in locks.docs[0]


def test_both_mode_independent_legs_across_bars():
    locks = _FakeLocks()
    dep = _dep({"leg_mode": "both"})
    ticks = {"CE|23950": {"last_price": 100.0, "ts": TS_0931 + 55_000},
             "PE|24050": {"last_price": 110.0, "ts": TS_0931 + 55_000}}
    run(evaluate_premium_momentum_bar(              # bar 1: lock + refs, both sides
        locks_col=locks, deployment=dep, instrument="NIFTY",
        candle_ts=TS_0931, spot_close=24000.0, contracts=_CONTRACTS,
        latest_tick_map=_tickmap(ticks), now_ts=TS_0931 / 1000 + 60,
    ))
    ts2 = TS_0931 + 60_000
    ticks2 = {"CE|23950": {"last_price": 116.0, "ts": ts2 + 55_000},   # +16% > 15% -- CE fires
              "PE|24050": {"last_price": 111.0, "ts": ts2 + 55_000}}   # +0.9% -- PE quiet
    out = run(evaluate_premium_momentum_bar(
        locks_col=locks, deployment=dep, instrument="NIFTY",
        candle_ts=ts2, spot_close=24000.0, contracts=_CONTRACTS,
        latest_tick_map=_tickmap(ticks2), now_ts=ts2 / 1000 + 60,
    ))
    assert out["outcome"] == "triggered" and out["direction"] == "CE" and out["leg"] == "pce"
    # simulate the EVALUATOR's latch (this module never writes it itself)
    locks.docs[0]["ce_triggered"] = True

    ts3 = TS_0931 + 120_000
    ticks3 = {"CE|23950": {"last_price": 120.0, "ts": ts3 + 55_000},   # CE resolved -- skipped
              "PE|24050": {"last_price": 127.0, "ts": ts3 + 55_000}}   # +15.5% > 15% -- PE's turn
    out = run(evaluate_premium_momentum_bar(
        locks_col=locks, deployment=dep, instrument="NIFTY",
        candle_ts=ts3, spot_close=24000.0, contracts=_CONTRACTS,
        latest_tick_map=_tickmap(ticks3), now_ts=ts3 / 1000 + 60,
    ))
    assert out["outcome"] == "triggered" and out["direction"] == "PE" and out["leg"] == "ppe"


def test_both_mode_same_bar_double_cross_ce_this_bar_pe_next_bar():
    locks = _FakeLocks()
    dep = _dep({"leg_mode": "both"})
    ticks = {"CE|23950": {"last_price": 100.0, "ts": TS_0931 + 55_000},
             "PE|24050": {"last_price": 110.0, "ts": TS_0931 + 55_000}}
    run(evaluate_premium_momentum_bar(
        locks_col=locks, deployment=dep, instrument="NIFTY",
        candle_ts=TS_0931, spot_close=24000.0, contracts=_CONTRACTS,
        latest_tick_map=_tickmap(ticks), now_ts=TS_0931 / 1000 + 60,
    ))
    ts2 = TS_0931 + 60_000
    # BOTH sides cross their +15% threshold on the SAME bar.
    ticks2 = {"CE|23950": {"last_price": 116.0, "ts": ts2 + 55_000},   # +16%
              "PE|24050": {"last_price": 127.0, "ts": ts2 + 55_000}}   # +15.5%
    out = run(evaluate_premium_momentum_bar(
        locks_col=locks, deployment=dep, instrument="NIFTY",
        candle_ts=ts2, spot_close=24000.0, contracts=_CONTRACTS,
        latest_tick_map=_tickmap(ticks2), now_ts=ts2 / 1000 + 60,
    ))
    # ONE triggered leg per bar -- CE wins (CE-first loop order); PE's cross
    # THIS bar is not reported (parity-divergence: live is later/fewer).
    assert out["outcome"] == "triggered" and out["direction"] == "CE" and out["leg"] == "pce"
    locks.docs[0]["ce_triggered"] = True   # simulate the evaluator's latch

    ts3 = ts2 + 60_000
    ticks3 = {"CE|23950": {"last_price": 118.0, "ts": ts3 + 55_000},
              "PE|24050": {"last_price": 128.0, "ts": ts3 + 55_000}}   # still >= +15% -- fires NOW
    out2 = run(evaluate_premium_momentum_bar(
        locks_col=locks, deployment=dep, instrument="NIFTY",
        candle_ts=ts3, spot_close=24000.0, contracts=_CONTRACTS,
        latest_tick_map=_tickmap(ticks3), now_ts=ts3 / 1000 + 60,
    ))
    assert out2["outcome"] == "triggered" and out2["direction"] == "PE" and out2["leg"] == "ppe"


def test_both_mode_all_resolved_returns_holding_position():
    locks = _FakeLocks()
    run(locks.insert_one({"deployment_id": "D1", "session_date": "2026-07-10",
                          "done_for_day": False,
                          "ce_triggered": True, "ce_entered_norenordno": "N1",
                          "pe_triggered": True, "pe_entered_norenordno": "N2"}))
    dep = _dep({"leg_mode": "both"})
    ts2 = TS_0931 + 120_000
    out = run(evaluate_premium_momentum_bar(
        locks_col=locks, deployment=dep, instrument="NIFTY",
        candle_ts=ts2, spot_close=24000.0, contracts=_CONTRACTS,
        latest_tick_map=_tickmap({}), now_ts=ts2 / 1000 + 60,
    ))
    assert out["outcome"] == "holding_position"


def test_entry_cutoff_blocks_new_trigger():
    locks = _FakeLocks()
    dep = _dep({"entry_cutoff": "10:00"})
    ticks = {"CE|23950": {"last_price": 100.0, "ts": TS_0931 + 55_000},
             "PE|24050": {"last_price": 110.0, "ts": TS_0931 + 55_000}}
    run(evaluate_premium_momentum_bar(
        locks_col=locks, deployment=dep, instrument="NIFTY",
        candle_ts=TS_0931, spot_close=24000.0, contracts=_CONTRACTS,
        latest_tick_map=_tickmap(ticks), now_ts=TS_0931 / 1000 + 60,
    ))
    ts2 = TS_0931 + 30 * 60_000   # 10:01 IST -- past entry_cutoff (10:00)
    ticks2 = {"CE|23950": {"last_price": 116.0, "ts": ts2 + 55_000},   # would trigger otherwise
              "PE|24050": {"last_price": 111.0, "ts": ts2 + 55_000}}
    out = run(evaluate_premium_momentum_bar(
        locks_col=locks, deployment=dep, instrument="NIFTY",
        candle_ts=ts2, spot_close=24000.0, contracts=_CONTRACTS,
        latest_tick_map=_tickmap(ticks2), now_ts=ts2 / 1000 + 60,
    ))
    assert out["outcome"] == "monitoring"     # NOT triggered -- cutoff blocks it


def test_entry_cutoff_blocks_lazy_lock():
    locks = _FakeLocks()
    run(locks.insert_one({"deployment_id": "D1", "session_date": "2026-07-10",
                          "triggered_side": None, "done_for_day": False,
                          "ce_ref_premium": 100.0, "pe_ref_premium": 110.0,
                          "ce": {"instrument_key": "CE|23950", "strike": 23950},
                          "pe": {"instrument_key": "PE|24050", "strike": 24050},
                          "lazy_armed_pe": True}))
    dep = _dep({"leg_mode": "both", "lazy_enabled": True, "entry_cutoff": "10:00"})
    ts2 = TS_0931 + 30 * 60_000   # 10:01 IST -- past entry_cutoff
    ticks = {"CE|23950": {"last_price": 101.0, "ts": ts2 + 55_000},
             "PE|24050": {"last_price": 111.0, "ts": ts2 + 55_000}}
    out = run(evaluate_premium_momentum_bar(
        locks_col=locks, deployment=dep, instrument="NIFTY",
        candle_ts=ts2, spot_close=24000.0, contracts=_CONTRACTS,
        latest_tick_map=_tickmap(ticks), now_ts=ts2 / 1000 + 60,
    ))
    assert out["outcome"] == "monitoring"
    assert "lpe_instrument_key" not in locks.docs[0]   # lazy lock never attempted past cutoff


def test_vix_gate_blocks_when_unverifiable():
    locks = _FakeLocks()
    dep = _dep({"vix_min": 12.0, "vix_max": 20.0})
    out = run(evaluate_premium_momentum_bar(
        locks_col=locks, deployment=dep, instrument="NIFTY",
        candle_ts=TS_0931, spot_close=24000.0, contracts=_CONTRACTS,
        latest_tick_map=_tickmap({}), now_ts=TS_0931 / 1000 + 60,
        vix=None,
    ))
    assert out["outcome"] == "done" and out["reason"] == "vix_unverifiable"
    assert locks.docs[0]["done_reason"] == "vix_unverifiable"


def test_vix_gate_blocks_when_outside_range():
    locks = _FakeLocks()
    dep = _dep({"vix_min": 12.0, "vix_max": 20.0})
    out = run(evaluate_premium_momentum_bar(
        locks_col=locks, deployment=dep, instrument="NIFTY",
        candle_ts=TS_0931, spot_close=24000.0, contracts=_CONTRACTS,
        latest_tick_map=_tickmap({}), now_ts=TS_0931 / 1000 + 60,
        vix=25.0,
    ))
    assert out["outcome"] == "done" and out["reason"] == "vix_gate"


def test_vix_gate_allows_when_within_range():
    locks = _FakeLocks()
    dep = _dep({"vix_min": 12.0, "vix_max": 20.0})
    ticks = {"CE|23950": {"last_price": 100.0, "ts": TS_0931 + 55_000},
             "PE|24050": {"last_price": 110.0, "ts": TS_0931 + 55_000}}
    out = run(evaluate_premium_momentum_bar(
        locks_col=locks, deployment=dep, instrument="NIFTY",
        candle_ts=TS_0931, spot_close=24000.0, contracts=_CONTRACTS,
        latest_tick_map=_tickmap(ticks), now_ts=TS_0931 / 1000 + 60,
        vix=15.0,
    ))
    assert out["outcome"] == "monitoring"


def test_lazy_pickup_happy_path_locks_ref_then_triggers():
    locks = _FakeLocks()
    run(locks.insert_one({"deployment_id": "D1", "session_date": "2026-07-10",
                          "triggered_side": None, "done_for_day": False,
                          "ce_ref_premium": 100.0,
                          "ce": {"instrument_key": "CE|23950", "strike": 23950},
                          "pe_ref_premium": 110.0,
                          "pe": {"instrument_key": "PE|24050", "strike": 24050},
                          "lazy_armed_ce": True, "lazy_armed_ce_reason": "stop"}))
    dep = _dep({"leg_mode": "both", "lazy_enabled": True,
               "lazy_momentum_pct": 10.0, "lazy_stop_pct": 20.0})
    ts2 = TS_0931 + 60_000
    ticks = {"CE|23950": {"last_price": 90.0, "ts": ts2 + 55_000},
             "PE|24050": {"last_price": 110.0, "ts": ts2 + 55_000}}
    out = run(evaluate_premium_momentum_bar(   # bar: lazy pickup -- lock + ref capture
        locks_col=locks, deployment=dep, instrument="NIFTY",
        candle_ts=ts2, spot_close=24000.0, contracts=_CONTRACTS,
        latest_tick_map=_tickmap(ticks), now_ts=ts2 / 1000 + 60,
    ))
    assert out["outcome"] == "monitoring"          # ref==premium_now this bar -- no trigger yet
    doc = locks.docs[0]
    assert doc["lce_instrument_key"] == "CE|23950"
    assert doc["lce_ref_premium"] == 90.0

    ts3 = ts2 + 60_000
    ticks3 = {"CE|23950": {"last_price": 100.0, "ts": ts3 + 55_000},   # +11.1% > 10%
              "PE|24050": {"last_price": 110.0, "ts": ts3 + 55_000}}
    out2 = run(evaluate_premium_momentum_bar(
        locks_col=locks, deployment=dep, instrument="NIFTY",
        candle_ts=ts3, spot_close=24000.0, contracts=_CONTRACTS,
        latest_tick_map=_tickmap(ticks3), now_ts=ts3 / 1000 + 60,
    ))
    assert out2["outcome"] == "triggered"
    assert out2["direction"] == "CE" and out2["leg"] == "lce"
    assert out2["contract"]["instrument_key"] == "CE|23950"
    assert out2["ref_premium"] == 90.0 and out2["premium_now"] == 100.0


def test_lazy_pickup_is_one_shot_after_entered():
    locks = _FakeLocks()
    run(locks.insert_one({"deployment_id": "D1", "session_date": "2026-07-10",
                          "triggered_side": None, "done_for_day": False,
                          "ce_ref_premium": 100.0,
                          "ce": {"instrument_key": "CE|23950", "strike": 23950},
                          "pe_ref_premium": 110.0,
                          "pe": {"instrument_key": "PE|24050", "strike": 24050},
                          "ce_triggered": True, "pe_triggered": True,   # primaries resolved
                          "lazy_armed_ce": True,
                          "lce_instrument_key": "CE|23950", "lce_ref_premium": 90.0,
                          "lce_triggered": True, "lce_entered_norenordno": "N9"}))
    dep = _dep({"leg_mode": "both", "lazy_enabled": True, "lazy_momentum_pct": 10.0})
    ts2 = TS_0931 + 60_000
    ticks = {"CE|23950": {"last_price": 130.0, "ts": ts2 + 55_000},   # would ALSO cross again
             "PE|24050": {"last_price": 110.0, "ts": ts2 + 55_000}}
    out = run(evaluate_premium_momentum_bar(
        locks_col=locks, deployment=dep, instrument="NIFTY",
        candle_ts=ts2, spot_close=24000.0, contracts=_CONTRACTS,
        latest_tick_map=_tickmap(ticks), now_ts=ts2 / 1000 + 60,
    ))
    assert out["outcome"] == "holding_position"   # both primaries AND the lazy leg resolved


def test_lazy_disabled_ignores_armed_flag():
    locks = _FakeLocks()
    run(locks.insert_one({"deployment_id": "D1", "session_date": "2026-07-10",
                          "triggered_side": None, "done_for_day": False,
                          "ce_ref_premium": 100.0,
                          "ce": {"instrument_key": "CE|23950", "strike": 23950},
                          "pe_ref_premium": 110.0,
                          "pe": {"instrument_key": "PE|24050", "strike": 24050},
                          "lazy_armed_ce": True}))
    dep = _dep({"leg_mode": "both"})   # lazy_enabled NOT set -> False
    ts2 = TS_0931 + 60_000
    ticks = {"CE|23950": {"last_price": 100.0, "ts": ts2 + 55_000},
             "PE|24050": {"last_price": 110.0, "ts": ts2 + 55_000}}
    out = run(evaluate_premium_momentum_bar(
        locks_col=locks, deployment=dep, instrument="NIFTY",
        candle_ts=ts2, spot_close=24000.0, contracts=_CONTRACTS,
        latest_tick_map=_tickmap(ticks), now_ts=ts2 / 1000 + 60,
    ))
    assert out["outcome"] == "monitoring"
    assert "lce_instrument_key" not in locks.docs[0]


# ---------------------------------------------------------------------------
# Review closures (Cluster A safety review, wf_784c52b4):
#   C2 interim guard — both-mode LIVE blocked until B6/B7 land.
#   C1 — entry_cutoff must fire for unpadded-but-valid HH:MM inputs.
# ---------------------------------------------------------------------------
def test_c2_interim_guard_blocks_both_mode_live_deployments():
    locks = _FakeLocks()
    dep = _dep({"leg_mode": "both"})
    dep["mode"] = "live"
    out = run(evaluate_premium_momentum_bar(
        locks_col=locks, deployment=dep, instrument="NIFTY",
        candle_ts=TS_0931, spot_close=24000.0, contracts=_CONTRACTS,
        latest_tick_map=_tickmap({}), now_ts=TS_0931 / 1000 + 60,
    ))
    assert out["outcome"] == "blocked"
    assert out["reason"] == "both_mode_live_pending_b6_b7"
    assert locks.docs == [], "the interim guard must not create any lock state"


def test_c2_interim_guard_leaves_both_mode_paper_and_first_to_trigger_live_alone():
    # both-mode + shadow: proceeds to the normal ref-bar lock flow.
    locks = _FakeLocks()
    dep = _dep({"leg_mode": "both"})
    dep["mode"] = "shadow"
    ticks = {"CE|23950": {"last_price": 100.0, "ts": TS_0931 + 55_000},
             "PE|24050": {"last_price": 110.0, "ts": TS_0931 + 55_000}}
    out = run(evaluate_premium_momentum_bar(
        locks_col=locks, deployment=dep, instrument="NIFTY",
        candle_ts=TS_0931, spot_close=24000.0, contracts=_CONTRACTS,
        latest_tick_map=_tickmap(ticks), now_ts=TS_0931 / 1000 + 60,
    ))
    assert out["outcome"] != "blocked" and len(locks.docs) == 1
    # first_to_trigger + live: untouched by the guard (Track B shipped path).
    locks2 = _FakeLocks()
    dep2 = _dep({})
    dep2["mode"] = "live"
    out2 = run(evaluate_premium_momentum_bar(
        locks_col=locks2, deployment=dep2, instrument="NIFTY",
        candle_ts=TS_0931, spot_close=24000.0, contracts=_CONTRACTS,
        latest_tick_map=_tickmap(ticks), now_ts=TS_0931 / 1000 + 60,
    ))
    assert out2["outcome"] != "blocked"


def test_c1_unpadded_entry_cutoff_fires_in_live_engine():
    """entry_cutoff '9:31' must block the 09:31+ bars exactly like '09:31' —
    before the fix the lexicographic compare made it NEVER fire (fail-open)."""
    for cutoff in ("09:31", "9:31"):
        locks = _FakeLocks()
        dep = _dep({"entry_cutoff": cutoff})
        ticks = {"CE|23950": {"last_price": 100.0, "ts": TS_0931 + 55_000},
                 "PE|24050": {"last_price": 110.0, "ts": TS_0931 + 55_000}}
        run(evaluate_premium_momentum_bar(
            locks_col=locks, deployment=dep, instrument="NIFTY",
            candle_ts=TS_0931, spot_close=24000.0, contracts=_CONTRACTS,
            latest_tick_map=_tickmap(ticks), now_ts=TS_0931 / 1000 + 60,
        ))
        ts2 = TS_0931 + 60_000
        ticks2 = {"CE|23950": {"last_price": 120.0, "ts": ts2 + 55_000},
                  "PE|24050": {"last_price": 111.0, "ts": ts2 + 55_000}}
        out = run(evaluate_premium_momentum_bar(
            locks_col=locks, deployment=dep, instrument="NIFTY",
            candle_ts=ts2, spot_close=24000.0, contracts=_CONTRACTS,
            latest_tick_map=_tickmap(ticks2), now_ts=ts2 / 1000 + 60,
        ))
        assert out["outcome"] != "triggered", \
            f"cutoff {cutoff!r} must block the +20% cross at/after the cutoff bar"
