"""Safety invariants of the scalper engine (app.scalping.engine) — I1..I10 in its docstring.

The signal is stubbed so each test controls exactly when an entry fires; order events are fed in
the Noren `om` shape. A seeded fuzz drives the engine against the fault-injecting SimBroker and
asserts the no-short / no-orphan invariants after every step.
"""
from __future__ import annotations

import random
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

import pytest

from app.scalping import engine as eng_mod
from app.scalping.config import NIFTY_N1, ScalperConfig, validate, with_overrides
from app.scalping.engine import ScalperEngine, lpp_bounds
from app.scalping.market import Contract, MarketState
from app.scalping.replay import execute_actions
from app.scalping.signals import EntrySignal
from app.scalping.sim_broker import SimBroker, SimParams

IST = timezone(timedelta(hours=5, minutes=30))
DAY = "2026-09-29"          # a Tuesday: NIFTY weekly expiry -> DTE 0
LOT = 65
CE = Contract("NSE_FO|1001", "NIFTY", 22600.0, "CE", DAY, LOT, "NIFTY29SEP26C22600")
PE = Contract("NSE_FO|1002", "NIFTY", 22600.0, "PE", DAY, LOT, "NIFTY29SEP26P22600")


def ms(hh: int, mm: int, ss: int = 0) -> int:
    return int(datetime(2026, 9, 29, hh, mm, ss, tzinfo=IST).timestamp() * 1000)


T0 = ms(10, 0, 0)


def cfg(**kw) -> ScalperConfig:
    base = dict(sigma_min_samples=2, sigma_lookback_s=10, max_trades_per_day=50, cooldown_after_exit_s=0)
    base.update(kw)
    return with_overrides(NIFTY_N1, **base)


def tick(key, bid, ask, now, bq=6500, aq=6500, ltp=None):
    depth = [{"bid_price": round(bid - i * 0.05, 2), "bid_quantity": bq, "ask_price": round(ask + i * 0.05, 2),
              "ask_quantity": aq} for i in range(5)]
    return {"instrument_key": key, "best_bid_price": bid, "best_ask_price": ask, "best_bid_quantity": bq,
            "best_ask_quantity": aq, "market_depth": depth, "last_price": ltp or (bid + ask) / 2,
            "ingest_ts": now, "ts": now}


def market_at(now, ce=(60.0, 60.1), pe=(55.0, 55.1), spot=22600.0) -> MarketState:
    m = MarketState([CE, PE], lookback_s=10, min_samples=2, max_window_s=60)
    m.on_tick({"instrument_key": "NSE_INDEX|Nifty 50", "last_price": spot, "ingest_ts": now})
    m.on_tick(tick(CE.instrument_key, *ce, now))
    m.on_tick(tick(PE.instrument_key, *pe, now))
    m.on_second(now)
    return m


@pytest.fixture
def force_signal(monkeypatch):
    """Make the signal fire (CE) whenever the engine asks, unless state['on'] is False."""
    state = {"on": True, "contract": CE}

    def fake(m, c):
        if not state["on"]:
            return None
        return EntrySignal(state["contract"].side, state["contract"], "test", {"z": 3.5})
    monkeypatch.setattr(eng_mod, "evaluate_signal", fake)
    return state


def om(cid, status, fill=0, avg=None, nord="N1", qty=LOT):
    return {"remarks": cid, "norenordno": nord, "status": status, "fillshares": fill, "avgprc": avg, "qty": qty}


def enter(e: ScalperEngine, now=T0, **mk):
    acts = e.on_second(now, market_at(now, **mk))
    places = [a for a in acts if a.kind == "place"]
    assert len(places) == 1 and places[0].side == "B"
    return places[0]


# ------------------------------------------------------------------------------- config
def test_config_refuses_live_mode():
    with pytest.raises(ValueError, match="no real-money adapter"):
        validate(ScalperConfig(strategy_id="x", underlying="NIFTY", exchange="NFO", mode="live"))


def test_config_refuses_order_rate_above_sebi_threshold_and_broker_minute_cap():
    with pytest.raises(ValueError):
        with_overrides(NIFTY_N1, max_orders_per_sec=10)
    with pytest.raises(ValueError):
        with_overrides(NIFTY_N1, max_orders_per_min=19)


def test_engine_refuses_unresolved_lot_size():
    with pytest.raises(ValueError):
        ScalperEngine(NIFTY_N1, lot_size=0)


# ------------------------------------------------------------------------------- I1 ack != fill
def test_ack_is_not_a_fill(force_signal):
    e = ScalperEngine(cfg(), lot_size=LOT)
    a = enter(e)
    e.on_submit_result(a.cid, T0 + 140, ok=True, norenordno="N1")
    assert e.position()["qty"] == 0
    e.on_order_event(om(a.cid, "OPEN"), T0 + 300)
    assert e.position()["qty"] == 0
    e.on_order_event(om(a.cid, "COMPLETE", LOT, 60.1), T0 + 900)
    assert e.position()["qty"] == LOT


def test_entry_limit_crosses_ask_by_configured_ticks_and_is_tick_rounded(force_signal):
    e = ScalperEngine(cfg(entry_cross_ticks=2), lot_size=LOT)
    a = enter(e, ce=(60.0, 60.1))
    assert a.price == pytest.approx(60.2)
    assert round(a.price / 0.05, 6) == round(a.price / 0.05)


def test_duplicate_and_out_of_order_events_do_not_double_count(force_signal):
    e = ScalperEngine(cfg(), lot_size=LOT)
    a = enter(e)
    e.on_submit_result(a.cid, T0, ok=True, norenordno="N1")
    for _ in range(3):
        e.on_order_event(om(a.cid, "COMPLETE", LOT, 60.1), T0 + 500)
    e.on_order_event(om(a.cid, "OPEN", 0), T0 + 600)          # late, lower rank
    assert e.position()["qty"] == LOT
    assert e.orders[a.cid]["state"] == "COMPLETE"


# ------------------------------------------------------------------------------- I6 timeout / partial
def test_entry_timeout_cancels_and_partial_fill_becomes_the_position(force_signal):
    c = cfg(entry_timeout_ms=2000)
    e = ScalperEngine(c, lot_size=LOT)
    a = enter(e)
    e.on_submit_result(a.cid, T0, ok=True, norenordno="N1")
    e.on_order_event(om(a.cid, "PARTIALLY_FILLED", 26, 60.1, qty=LOT * 2), T0 + 500)
    acts = e.on_second(T0 + 2000, market_at(T0 + 2000))
    assert any(x.kind == "cancel" and x.cid == a.cid for x in acts)
    e.on_order_event(om(a.cid, "CANCELED", 26, 60.1, qty=LOT * 2), T0 + 2200)
    assert e.position()["qty"] == 26
    force_signal["on"] = False
    acts = e.on_second(T0 + 40_000, market_at(T0 + 40_000))           # time exit
    sells = [x for x in acts if x.kind == "place" and x.side == "S"]
    assert len(sells) == 1 and sells[0].qty == 26


def test_unfilled_entry_closes_trip_without_a_trade(force_signal):
    e = ScalperEngine(cfg(), lot_size=LOT)
    a = enter(e)
    e.on_submit_result(a.cid, T0, ok=True, norenordno="N1")
    e.on_second(T0 + 2000, market_at(T0 + 2000))
    e.on_order_event(om(a.cid, "CANCELED", 0), T0 + 2200)
    assert e.trip is None and e.closed_trades == [] and e.is_flat_and_idle()


# ------------------------------------------------------------------------------- I2 no unintended short
def test_cancel_fill_race_never_oversells(force_signal):
    """Entry partially fills, exit is placed for the partial, then the entry remainder fills before the
    cancel lands. The engine must sell the remainder separately and end flat, never short."""
    c = cfg(entry_timeout_ms=2000, max_hold_s=1)
    e = ScalperEngine(c, lot_size=LOT)
    a = enter(e)
    e.on_submit_result(a.cid, T0, ok=True, norenordno="N1")
    e.on_order_event(om(a.cid, "PARTIALLY_FILLED", 30, 60.1), T0 + 300)
    force_signal["on"] = False
    acts = e.on_second(T0 + 2000, market_at(T0 + 2000))
    sell1 = [x for x in acts if x.kind == "place" and x.side == "S"][0]
    assert sell1.qty == 30                                      # only what is confirmed
    e.on_submit_result(sell1.cid, T0 + 2100, ok=True, norenordno="S1")
    e.on_order_event(om(a.cid, "COMPLETE", LOT, 60.1), T0 + 2150)      # remainder lands (race)
    assert e.sellable() == LOT - 30
    e.on_order_event(om(sell1.cid, "COMPLETE", 30, 59.9, nord="S1", qty=30), T0 + 2300)
    acts = e.on_second(T0 + 3000, market_at(T0 + 3000))
    sell2 = [x for x in acts if x.kind == "place" and x.side == "S"]
    assert len(sell2) == 1 and sell2[0].qty == LOT - 30
    e.on_submit_result(sell2[0].cid, T0 + 3100, ok=True, norenordno="S2")
    e.on_order_event(om(sell2[0].cid, "COMPLETE", LOT - 30, 59.8, nord="S2", qty=LOT - 30), T0 + 3300)
    assert e.position()["qty"] == 0 and e.halted is None
    assert len(e.closed_trades) == 1 and e.closed_trades[0]["qty"] == LOT


def test_no_sell_while_a_sell_status_is_unknown(force_signal):
    c = cfg(max_hold_s=1)
    e = ScalperEngine(c, lot_size=LOT)
    a = enter(e)
    e.on_submit_result(a.cid, T0, ok=True, norenordno="N1")
    e.on_order_event(om(a.cid, "COMPLETE", LOT, 60.1), T0 + 300)
    force_signal["on"] = False
    acts = e.on_second(T0 + 2000, market_at(T0 + 2000))
    s = [x for x in acts if x.kind == "place" and x.side == "S"][0]
    acts = e.on_submit_result(s.cid, T0 + 2100, ok=False, indeterminate=True)
    assert any(x.kind == "reconcile" for x in acts)
    for k in range(3, 10):
        acts = e.on_second(T0 + k * 1000, market_at(T0 + k * 1000))
        assert not [x for x in acts if x.kind == "place"], "a second SELL while the first is unknown risks a short"
    # broker truth: the first sell DID reach the book and filled
    e.on_reconcile(T0 + 11_000, broker_orders=[om(a.cid, "COMPLETE", LOT, 60.1),
                                              om(s.cid, "COMPLETE", LOT, 59.9, nord="S9")], broker_net_qty=0)
    assert e.position()["qty"] == 0 and e.reconcile_required is None and e.halted is None


def test_negative_position_halts():
    e = ScalperEngine(cfg(), lot_size=LOT)
    e.orders["x"] = {"client_order_id": "x", "side": "S", "purpose": "exit", "instrument_key": CE.instrument_key,
                     "trading_symbol": "", "qty": LOT, "price": 1.0, "state": "OPEN", "fillshares": 0, "avgprc": None,
                     "norenordno": "Z", "unknown": False, "sent_ms": T0, "ack_ms": T0, "first_fill_ms": None,
                     "last_fill_ms": None, "deadline_ms": T0, "ack_deadline_ms": T0 + 9999, "cancel_sent_ms": None,
                     "cancel_deadline_ms": None, "attempt": 0, "decision": {}}
    acts = e.on_order_event(om("x", "COMPLETE", LOT, 50.0, nord="Z"), T0)
    assert e.halted and "unintended_short" in e.halted
    assert any(a.kind == "halt" for a in acts)


# ------------------------------------------------------------------------------- I3 / I8 exits
def test_single_working_exit_and_reprice_requires_cancel_confirmation(force_signal):
    c = cfg(max_hold_s=1, exit_reprice_ms=1500, exit_cross_ticks=(2, 6, 20))
    e = ScalperEngine(c, lot_size=LOT)
    a = enter(e)
    e.on_submit_result(a.cid, T0, ok=True, norenordno="N1")
    e.on_order_event(om(a.cid, "COMPLETE", LOT, 60.1), T0 + 300)
    force_signal["on"] = False
    acts = e.on_second(T0 + 2000, market_at(T0 + 2000))
    s1 = [x for x in acts if x.kind == "place" and x.side == "S"][0]
    assert s1.price == pytest.approx(60.0 - 2 * 0.05)
    e.on_submit_result(s1.cid, T0 + 2100, ok=True, norenordno="S1")
    acts = e.on_second(T0 + 3000, market_at(T0 + 3000))
    assert not [x for x in acts if x.kind == "place"]                     # still working, no second exit
    acts = e.on_second(T0 + 4000, market_at(T0 + 4000))
    assert [x for x in acts if x.kind == "cancel" and x.cid == s1.cid]
    acts = e.on_second(T0 + 5000, market_at(T0 + 5000))
    assert not [x for x in acts if x.kind == "place"], "must wait for CANCELED before re-pricing"
    e.on_order_event(om(s1.cid, "CANCELED", 0, nord="S1"), T0 + 5200)
    acts = e.on_second(T0 + 6000, market_at(T0 + 6000))
    s2 = [x for x in acts if x.kind == "place" and x.side == "S"][0]
    assert s2.price == pytest.approx(60.0 - 6 * 0.05) and s2.qty == LOT


def test_exit_ladder_never_gives_up_and_respects_lpp_floor(force_signal):
    c = cfg(max_hold_s=1, exit_reprice_ms=1000, exit_cross_ticks=(2, 600))
    e = ScalperEngine(c, lot_size=LOT)
    a = enter(e)
    e.on_submit_result(a.cid, T0, ok=True, norenordno="N1")
    e.on_order_event(om(a.cid, "COMPLETE", LOT, 60.1), T0 + 300)
    force_signal["on"] = False
    now = T0 + 2000
    placed = 0
    for _ in range(12):
        acts = e.on_second(now, market_at(now))
        for x in acts:
            if x.kind == "place":
                placed += 1
                lo, _ = lpp_bounds(60.05)
                assert x.price >= lo, "exit priced below the exchange LPP floor would be rejected"
                e.on_submit_result(x.cid, now, ok=True, norenordno=f"S{placed}")
            if x.kind == "cancel":
                e.on_order_event(om(x.cid, "CANCELED", 0, nord=f"S{placed}"), now + 200)
        now += 1000
    assert placed >= 5                       # keeps re-pricing at the last rung
    assert e.position()["qty"] == LOT and e.halted is None


@pytest.mark.parametrize("bid,expected", [(56.9, "stop"), (63.8, "target")])
def test_stop_and_target_on_the_bid(force_signal, bid, expected):
    e = ScalperEngine(cfg(stop_loss_pct=5.0, target_pct=6.0, trail_arm_pct=None), lot_size=LOT)
    a = enter(e)
    e.on_submit_result(a.cid, T0, ok=True, norenordno="N1")
    e.on_order_event(om(a.cid, "COMPLETE", LOT, 60.0), T0 + 300)
    force_signal["on"] = False
    e.on_second(T0 + 1000, market_at(T0 + 1000, ce=(bid, round(bid + 0.1, 2))))
    assert e.trip["exit_reason"] == expected


def test_stop_wins_over_time_exit_on_the_same_snapshot(force_signal):
    e = ScalperEngine(cfg(max_hold_s=1, stop_loss_pct=5.0), lot_size=LOT)
    a = enter(e)
    e.on_submit_result(a.cid, T0, ok=True, norenordno="N1")
    e.on_order_event(om(a.cid, "COMPLETE", LOT, 60.0), T0 + 300)
    force_signal["on"] = False
    e.on_second(T0 + 5000, market_at(T0 + 5000, ce=(50.0, 50.1)))
    assert e.trip["exit_reason"] == "stop"


def test_trail_exit_after_arming(force_signal):
    e = ScalperEngine(cfg(trail_arm_pct=3.0, trail_giveback_pct=50.0, target_pct=None), lot_size=LOT)
    a = enter(e)
    e.on_submit_result(a.cid, T0, ok=True, norenordno="N1")
    e.on_order_event(om(a.cid, "COMPLETE", LOT, 60.0), T0 + 300)
    force_signal["on"] = False
    e.on_second(T0 + 1000, market_at(T0 + 1000, ce=(64.0, 64.1)))     # +6.7 % -> armed, peak 64
    assert e.trip["exit_reason"] is None
    e.on_second(T0 + 2000, market_at(T0 + 2000, ce=(61.9, 62.0)))     # floor = 60 + 4*0.5 = 62
    assert e.trip["exit_reason"] == "trail"


def test_stale_feed_forces_protective_exit(force_signal):
    c = cfg(stale_feed_exit_ms=5000, max_quote_age_ms=1500, max_hold_s=300)
    e = ScalperEngine(c, lot_size=LOT)
    a = enter(e)
    e.on_submit_result(a.cid, T0, ok=True, norenordno="N1")
    e.on_order_event(om(a.cid, "COMPLETE", LOT, 60.0), T0 + 300)
    force_signal["on"] = False
    e.on_second(T0 + 1000, market_at(T0 + 1000))
    m = market_at(T0 + 1000)
    m.on_second(T0 + 7000)                                           # no new ticks for 6 s
    acts = e.on_second(T0 + 7000, m)
    assert e.trip["exit_reason"] == "stale_feed"
    s = [x for x in acts if x.kind == "place" and x.side == "S"][0]
    assert s.price < 60.0 * 0.96                                      # priced defensively


# ------------------------------------------------------------------------------- I5 unknown / reconcile
def test_indeterminate_entry_blocks_entries_until_reconciled(force_signal):
    e = ScalperEngine(cfg(), lot_size=LOT)
    a = enter(e)
    acts = e.on_submit_result(a.cid, T0 + 100, ok=False, indeterminate=True)
    assert e.reconcile_required and any(x.kind == "reconcile" for x in acts)
    e.on_reconcile(T0 + 2000, broker_orders=[], broker_net_qty=0)      # broker never saw it
    assert e.reconcile_required is None and e.orders == {} or e.trip is None
    acts = e.on_second(T0 + 3000, market_at(T0 + 3000))
    assert [x for x in acts if x.kind == "place"]                      # entries resume


def test_no_ack_by_deadline_is_unknown(force_signal):
    e = ScalperEngine(cfg(ack_timeout_ms=3000), lot_size=LOT)
    enter(e)
    acts = e.on_second(T0 + 3000, market_at(T0 + 3000))
    assert e.reconcile_required and any(x.kind == "reconcile" for x in acts)


def test_reconcile_mismatch_halts_on_second_consecutive_read(force_signal):
    e = ScalperEngine(cfg(), lot_size=LOT)
    a = enter(e)
    e.on_submit_result(a.cid, T0, ok=True, norenordno="N1")
    acts = e.on_reconcile(T0 + 1000, broker_orders=[om(a.cid, "OPEN", 0)], broker_net_qty=LOT)
    assert not e.halted and e.reconcile_required == "mismatch_unconfirmed"     # one read may be a race
    assert not [x for x in e.on_second(T0 + 2000, market_at(T0 + 2000)) if x.kind == "place" and x.side == "B"]
    acts = e.on_reconcile(T0 + 3000, broker_orders=[om(a.cid, "OPEN", 0)], broker_net_qty=LOT)
    assert e.halted and "reconcile_mismatch" in e.halted
    assert any(x.kind == "halt" for x in acts)


def test_single_mismatch_followed_by_agreement_clears(force_signal):
    e = ScalperEngine(cfg(), lot_size=LOT)
    a = enter(e)
    e.on_submit_result(a.cid, T0, ok=True, norenordno="N1")
    e.on_reconcile(T0 + 1000, broker_orders=[om(a.cid, "OPEN", 0)], broker_net_qty=LOT)
    e.on_reconcile(T0 + 2000, broker_orders=[om(a.cid, "COMPLETE", LOT, 60.1)], broker_net_qty=LOT)
    assert e.halted is None and e.reconcile_required is None and e.position()["qty"] == LOT


def test_routine_reconcile_requested_while_live(force_signal):
    e = ScalperEngine(cfg(reconcile_interval_s=10, entry_timeout_ms=60_000), lot_size=LOT)
    a = enter(e)
    e.on_submit_result(a.cid, T0, ok=True, norenordno="N1")
    acts = e.on_second(T0 + 11_000, market_at(T0 + 11_000))
    assert any(x.kind == "reconcile" and x.reason == "routine" for x in acts)


def test_restart_restores_into_reconcile_required(force_signal):
    e = ScalperEngine(cfg(), lot_size=LOT)
    a = enter(e)
    e.on_submit_result(a.cid, T0, ok=True, norenordno="N1")
    snap = e.snapshot()
    e2 = ScalperEngine.restore(cfg(), snap, lot_size=LOT, now_ms=T0 + 5000)
    assert e2.reconcile_required == "restart"
    acts = e2.on_second(T0 + 6000, market_at(T0 + 6000))
    assert not [x for x in acts if x.kind == "place" and x.side == "B"]
    e2.on_reconcile(T0 + 7000, broker_orders=[om(a.cid, "COMPLETE", LOT, 60.1)], broker_net_qty=LOT)
    assert e2.reconcile_required is None and e2.position()["qty"] == LOT


# ------------------------------------------------------------------------------- I9 kill / risk / time
def test_kill_switch_cancels_entry_exits_position_and_stays_halted(force_signal):
    e = ScalperEngine(cfg(), lot_size=LOT)
    a = enter(e)
    e.on_submit_result(a.cid, T0, ok=True, norenordno="N1")
    e.on_order_event(om(a.cid, "PARTIALLY_FILLED", 30, 60.1), T0 + 300)
    acts = e.kill(T0 + 500, "operator")
    assert any(x.kind == "cancel" and x.cid == a.cid for x in acts) and e.halted == "operator"
    acts = e.on_second(T0 + 1000, market_at(T0 + 1000))
    assert [x for x in acts if x.kind == "place" and x.side == "S" and x.qty == 30]
    assert e.trip["exit_reason"].startswith("halt")


def test_daily_loss_limit_pauses_for_the_day_and_exits(force_signal):
    e = ScalperEngine(cfg(daily_loss_limit_inr=200.0, stop_loss_pct=50.0), lot_size=LOT)
    a = enter(e)
    e.on_submit_result(a.cid, T0, ok=True, norenordno="N1")
    e.on_order_event(om(a.cid, "COMPLETE", LOT, 60.0), T0 + 300)
    acts = e.on_second(T0 + 1000, market_at(T0 + 1000, ce=(56.0, 56.1)))    # -4 x 65 = -260
    assert e.paused_for_day == "daily_loss_limit"
    assert [x for x in acts if x.kind == "place" and x.side == "S"]


def test_consecutive_losses_pause(force_signal):
    e = ScalperEngine(cfg(max_consecutive_losses=2, pause_after_losses_s=600, max_hold_s=1), lot_size=LOT)
    now = T0
    for k in range(2):
        a = enter(e, now=now)
        e.on_submit_result(a.cid, now, ok=True, norenordno=f"B{k}")
        e.on_order_event(om(a.cid, "COMPLETE", LOT, 60.1, nord=f"B{k}"), now + 200)
        acts = e.on_second(now + 2000, market_at(now + 2000))
        s = [x for x in acts if x.kind == "place" and x.side == "S"][0]
        e.on_submit_result(s.cid, now + 2100, ok=True, norenordno=f"S{k}")
        e.on_order_event(om(s.cid, "COMPLETE", LOT, 59.0, nord=f"S{k}"), now + 2300)
        now += 10_000
    assert e.paused_until_ms > now
    acts = e.on_second(now, market_at(now))
    assert not [x for x in acts if x.kind == "place"]


def test_no_entry_outside_window_and_forced_exit_at_cutoff(force_signal):
    e = ScalperEngine(cfg(), lot_size=LOT)
    late = ms(14, 50)
    assert not [x for x in e.on_second(late, market_at(late)) if x.kind == "place"]
    a = enter(e, now=ms(14, 44, 50))
    e.on_submit_result(a.cid, ms(14, 44, 50), ok=True, norenordno="N1")
    e.on_order_event(om(a.cid, "COMPLETE", LOT, 60.1), ms(14, 44, 51))
    e.cfg = with_overrides(e.cfg, max_hold_s=3600, stop_loss_pct=99.0, target_pct=None, trail_arm_pct=None)
    e.on_second(ms(14, 59, 59), market_at(ms(14, 59, 59)))
    assert e.trip["exit_reason"] is None
    e.on_second(ms(15, 0, 0), market_at(ms(15, 0, 0)))
    assert e.trip["exit_reason"] == "force_exit_time"


def test_entry_rate_budget_keeps_a_reserve_for_exits(force_signal):
    e = ScalperEngine(cfg(max_orders_per_sec=3, max_orders_per_min=4), lot_size=LOT)
    for k in range(2):
        e._spend(T0 - 30_000 + k)
    acts = e.on_second(T0, market_at(T0))
    assert not [x for x in acts if x.kind == "place"], "entry must not consume the exit reserve"


def test_filters_reject_wide_spread_thin_book_and_stale_quote(force_signal):
    e = ScalperEngine(cfg(), lot_size=LOT)
    assert not [x for x in e.on_second(T0, market_at(T0, ce=(60.0, 60.9))) if x.kind == "place"]   # 18 ticks
    m = MarketState([CE, PE], lookback_s=10, min_samples=2)
    m.on_tick({"instrument_key": "NSE_INDEX|Nifty 50", "last_price": 22600.0, "ingest_ts": T0})
    m.on_tick(tick(CE.instrument_key, 60.0, 60.1, T0, aq=10))
    m.on_second(T0)
    assert not [x for x in e.on_second(T0, m) if x.kind == "place"]                                # thin ask
    m2 = market_at(T0 - 5000)
    m2.on_second(T0)
    assert not [x for x in e.on_second(T0, m2) if x.kind == "place"]                               # stale
    kinds = [ev.get("reason", "") for ev in e.events if ev["kind"] == "signal_filtered"]
    assert any(r.startswith("spread") for r in kinds) and any("ask_touch" in r for r in kinds)
    assert any("quote_stale" in r for r in kinds)


# ------------------------------------------------------------------------------- fuzz vs SimBroker
@pytest.mark.parametrize("seed", range(12))
def test_fuzz_no_short_no_orphan_under_faults(force_signal, seed):
    """Random walk market + every fault the simulator can inject. After every second: the broker's
    net quantity is never negative, and the engine's fill-derived position equals the broker's
    unless the engine has halted or is waiting on a reconcile."""
    rng = random.Random(seed)
    c = cfg(max_hold_s=rng.choice([3, 10, 30]), entry_timeout_ms=2000, exit_reprice_ms=1000,
            stop_loss_pct=3.0, target_pct=4.0, max_trades_per_day=200, daily_loss_limit_inr=1e9,
            max_consecutive_losses=99)
    e = ScalperEngine(c, lot_size=LOT)
    b = SimBroker(SimParams(seed=seed, reject_prob=0.05, indeterminate_prob=0.05, drop_event_prob=0.1,
                            duplicate_event_prob=0.1, depth_take_ratio=rng.choice([0.05, 0.3, 1.0])))
    m = MarketState([CE, PE], lookback_s=10, min_samples=2)
    mid = 60.0
    log = []
    diverged_since = None
    for s in range(1500):
        now = T0 + s * 1000 + 999
        mid = max(5.0, mid + rng.gauss(0, 0.4))
        spread = 0.05 * rng.choice([1, 2, 3])
        bq, aq = rng.choice([10, 65, 130, 6500]), rng.choice([10, 65, 130, 6500])
        m.on_tick({"instrument_key": "NSE_INDEX|Nifty 50", "last_price": 22600.0, "ingest_ts": now - 500})
        if rng.random() > 0.05:                                         # occasional missing quote
            m.on_tick(tick(CE.instrument_key, round(mid, 2), round(mid + spread, 2), now - 400, bq, aq))
        m.on_tick(tick(PE.instrument_key, 50.0, 50.1, now - 400))
        force_signal["on"] = rng.random() < 0.2
        m.on_second(now)
        b.set_reference(m.quotes)
        for key in b.orders_keys():
            if key in m.quotes:
                b.on_quote(m.quotes[key], m.depth.get(key))
        for t, ev in b.advance(now):
            execute_actions(e, b, e.on_order_event(ev, t), now, log)
        execute_actions(e, b, e.on_second(now, m), now, log)
        assert all(v >= 0 for v in b.net_qty.values()), f"broker short at s={s}"
        assert e.position()["qty"] >= 0 or e.halted, f"engine short without halting at s={s}"
        diverged = e.position()["qty"] != sum(b.net_qty.values())
        diverged_since = (diverged_since if diverged and diverged_since is not None else (s if diverged else None))
        if diverged and not e.halted:
            # lost updates are tolerated only until the cancel-confirm timeout / routine reconcile catch them
            assert s - diverged_since <= 20, f"engine/broker divergence persisted {s - diverged_since}s at s={s}"
    assert e.closed_trades, "fuzz produced no trades — harness is not exercising the engine"
    for t in e.closed_trades:
        assert t["qty"] > 0 and t["exit_avg"] is not None
