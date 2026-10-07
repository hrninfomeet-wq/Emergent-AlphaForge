"""Regressions for the confirmed findings of the 2026-10-07 adversarial review of app.scalping.

Each test reproduces a finding that was demonstrated with a script against the shipped code
(docs/scalping/05-validation.md, "Adversarial review"). Engine findings are latent with the
simulator but real for any broker; simulator findings biased replay results.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "tests"))

import pytest

from test_scalping_engine import CE, LOT, PE, T0, cfg, enter, force_signal, market_at, om  # noqa: F401
from app.scalping.engine import Action, ScalperEngine
from app.scalping.market import quote_from_tick
from app.scalping.sim_broker import SimBroker, SimParams


def _held(e, now=T0, px=60.0, cid_out=None):
    a = enter(e, now=now)
    e.on_submit_result(a.cid, now, ok=True, norenordno="B1")
    e.on_order_event(om(a.cid, "COMPLETE", LOT, px, nord="B1"), now + 300)
    return a


def _exit_order(e, now):
    acts = e.on_second(now, market_at(now))
    return [x for x in acts if x.kind == "place" and x.side == "S"]


# ------------------------------------------------------------------------- E1 absent-once != rejected
def test_sell_absent_from_one_read_is_not_rejected_and_no_second_sell(force_signal):
    e = ScalperEngine(cfg(max_hold_s=1), lot_size=LOT)
    a = _held(e)
    force_signal["on"] = False
    s1 = _exit_order(e, T0 + 2000)[0]                          # PlaceOrder still in flight: no result
    e.on_second(T0 + 5100, market_at(T0 + 5100))                 # ack deadline -> unknown
    assert e.orders[s1.cid]["unknown"]
    e.on_reconcile(T0 + 5200, broker_orders=[om(a.cid, "COMPLETE", LOT, 60.0, nord="B1")], broker_net_qty=LOT)
    assert e.orders[s1.cid]["state"] != "REJECTED", "one read without the order must not declare it never-accepted"
    for k in range(6, 9):
        assert not _exit_order(e, T0 + k * 1000), "a second SELL while the first may still land risks a short"


def test_failed_read_never_converts_unknown_orders(force_signal):
    e = ScalperEngine(cfg(max_hold_s=1), lot_size=LOT)
    _held(e)
    force_signal["on"] = False
    s1 = _exit_order(e, T0 + 2000)[0]
    e.on_second(T0 + 5100, market_at(T0 + 5100))
    e.on_reconcile(T0 + 5200, broker_orders=[], broker_net_qty=None)          # the read itself failed
    assert e.orders[s1.cid]["state"] != "REJECTED"


def test_order_absent_on_two_reads_after_long_enough_is_resolved_not_found(force_signal):
    e = ScalperEngine(cfg(max_hold_s=1), lot_size=LOT)
    a = _held(e)
    force_signal["on"] = False
    s1 = _exit_order(e, T0 + 2000)[0]
    e.on_second(T0 + 5100, market_at(T0 + 5100))
    book = [om(a.cid, "COMPLETE", LOT, 60.0, nord="B1")]
    e.on_reconcile(T0 + 5200, broker_orders=book, broker_net_qty=LOT)
    e.on_reconcile(T0 + 10_500, broker_orders=book, broker_net_qty=LOT)
    assert e.orders.get(s1.cid, {}).get("state") == "REJECTED" or s1.cid not in e.orders
    assert e.reconcile_required is None
    assert _exit_order(e, T0 + 11_000), "after a proven non-arrival the exit must be re-sent"


# ------------------------------------------------------------------------- E2/E3 cancel retry, unknown cleared
def test_refused_cancel_is_resent(force_signal):
    e = ScalperEngine(cfg(max_hold_s=1, exit_reprice_ms=1000), lot_size=LOT)
    _held(e)
    force_signal["on"] = False
    s1 = _exit_order(e, T0 + 2000)[0]
    e.on_submit_result(s1.cid, T0 + 2100, ok=True, norenordno="S1")
    e.on_order_event(om(s1.cid, "OPEN", 0, nord="S1"), T0 + 2150)
    acts = e.on_second(T0 + 3200, market_at(T0 + 3200))
    c = [x for x in acts if x.kind == "cancel" and x.cid == s1.cid]
    assert c
    e.on_cancel_result(s1.cid, T0 + 3300, ok=False, error="Session Expired : Invalid Session Key")
    resent = []
    for k in range(4, 14):
        resent += [x for x in e.on_second(T0 + k * 1000, market_at(T0 + k * 1000)) if x.kind == "cancel" and x.cid == s1.cid]
    assert resent, "a refused cancel must be retried, or the exit rests at a stale price forever"


def test_reconcile_that_finds_an_unchanged_working_order_clears_unknown(force_signal):
    e = ScalperEngine(cfg(max_hold_s=1, exit_reprice_ms=60_000), lot_size=LOT)
    a = _held(e)
    force_signal["on"] = False
    s1 = _exit_order(e, T0 + 2000)[0]
    e.on_submit_result(s1.cid, T0 + 2100, ok=True, norenordno="S1")
    e.on_order_event(om(s1.cid, "OPEN", 0, nord="S1"), T0 + 2200)
    snap = e.snapshot()
    e2 = ScalperEngine.restore(cfg(max_hold_s=1, exit_reprice_ms=1000), snap, lot_size=LOT, now_ms=T0 + 3000)
    e2.on_reconcile(T0 + 3500, broker_orders=[om(a.cid, "COMPLETE", LOT, 60.0, nord="B1"),
                                              om(s1.cid, "OPEN", 0, nord="S1")], broker_net_qty=LOT)
    assert not e2.orders[s1.cid]["unknown"] and e2.reconcile_required is None
    acts = []
    for k in range(4, 8):
        acts += e2.on_second(T0 + k * 1000, market_at(T0 + k * 1000))
    assert any(x.kind == "cancel" and x.cid == s1.cid for x in acts), "the exit ladder must resume after restore"


# ------------------------------------------------------------------------- E4 last_quote does not leak
def test_last_quote_from_a_previous_contract_never_prices_a_new_trip(force_signal):
    e = ScalperEngine(cfg(max_hold_s=300, target_pct=1.0, trail_arm_pct=None), lot_size=LOT)
    a = _held(e)
    force_signal["on"] = False
    s = _exit_order(e, T0 + 1000)
    m = market_at(T0 + 1000, ce=(61.0, 61.1))
    acts = e.on_second(T0 + 1000, m)
    s = [x for x in acts if x.kind == "place" and x.side == "S"][0]
    e.on_submit_result(s.cid, T0 + 1100, ok=True, norenordno="S1")
    e.on_order_event(om(s.cid, "COMPLETE", LOT, 61.0, nord="S1"), T0 + 1200)
    assert e.trip is None
    force_signal["on"], force_signal["contract"] = True, PE
    a2 = enter(e, now=T0 + 10_000, pe=(40.0, 40.1))
    e.on_submit_result(a2.cid, T0 + 10_000, ok=True, norenordno="B2")
    e.on_order_event(om(a2.cid, "COMPLETE", LOT, 40.1, nord="B2"), T0 + 10_200)
    force_signal["on"] = False
    stale_pe = market_at(T0 + 10_000, pe=(40.0, 40.1))
    stale_pe.on_second(T0 + 11_700)                     # PE quote 1.7 s old: not fresh, not yet stale-feed
    e.on_second(T0 + 11_700, stale_pe)
    assert e.trip["exit_reason"] is None, "a quote from the previous contract must not trigger stale_feed"


# ------------------------------------------------------------------------- E5/E6 malformed broker data
def test_fill_without_avgprc_requires_reconcile_and_never_prices_at_zero(force_signal):
    e = ScalperEngine(cfg(), lot_size=LOT)
    a = enter(e)
    e.on_submit_result(a.cid, T0, ok=True, norenordno="B1")
    acts = e.on_order_event({"remarks": a.cid, "norenordno": "B1", "status": "PARTIALLY_FILLED",
                             "fillshares": 30, "avgprc": None, "qty": LOT}, T0 + 300)
    assert e.reconcile_required, "a fill with no price is not a known position"
    e.on_order_event(om(a.cid, "CANCELED", 30, 60.1, nord="B1"), T0 + 600)
    assert e.position()["avg_buy"] == pytest.approx(60.1)


def test_complete_without_parsable_fillshares_is_not_booked_unfilled(force_signal):
    for bad in (None, "", "65.0"):
        e = ScalperEngine(cfg(), lot_size=LOT)
        a = enter(e)
        e.on_submit_result(a.cid, T0, ok=True, norenordno="B1")
        e.on_order_event({"remarks": a.cid, "norenordno": "B1", "status": "COMPLETE", "fillshares": bad,
                          "avgprc": 60.1, "qty": LOT}, T0 + 300)
        assert e.trip is not None, f"fillshares={bad!r}: trip must stay open until reconciled"
        assert e.reconcile_required
        e.on_reconcile(T0 + 1000, broker_orders=[om(a.cid, "COMPLETE", LOT, 60.1, nord="B1")], broker_net_qty=LOT)
        assert e.position()["qty"] == LOT


# ------------------------------------------------------------------------- simulator realism
def _q(key, bid, ask, t, ltp=None, qty=1000):
    q = quote_from_tick({"instrument_key": key, "best_bid_price": bid, "best_ask_price": ask, "best_bid_quantity": qty,
                         "best_ask_quantity": qty, "ingest_ts": t, "last_price": ltp or (bid + ask) / 2})
    depth = [{"bid_price": round(bid - i * 0.05, 2), "bid_quantity": qty, "ask_price": round(ask + i * 0.05, 2),
              "ask_quantity": qty} for i in range(5)]
    return q, depth


def test_sim_lpp_uses_the_current_reference_not_a_stale_last_quote():
    b = SimBroker(SimParams())
    q, d = _q("K", 59.9, 60.0, 0, ltp=60.0)
    b.on_quote(q, d)                                    # an old order's quote, never refreshed
    cur, _ = _q("K", 32.9, 33.0, 60_000, ltp=33.0)
    b.set_reference({"K": cur})
    r = b.submit(Action("place", 60_000, cid="x", side="S", instrument_key="K", qty=65, price=30.0), 60_000)
    assert r.ok, "LPP band at ltp 33 is 13-53; a sell at 30 is inside it"


def test_sim_does_not_rematch_the_same_snapshot():
    b = SimBroker(SimParams(depth_take_ratio=0.5))
    q0, d0 = _q("K", 59.9, 60.0, 0, qty=40)
    b.on_quote(q0, d0)
    b.submit(Action("place", 0, cid="c", side="B", instrument_key="K", qty=130, price=60.10), 0)
    q1, d1 = _q("K", 59.9, 60.0, 500, qty=40)            # 3 marketable levels x 40 -> 60 at ratio 0.5
    b.on_quote(q1, d1)
    fills = []
    for t in (1000, 2000, 3000):                          # no new snapshot arrives
        b.on_quote(q1, d1)
        fills += [ev["fillshares"] for _, ev in b.advance(t) if ev["remarks"] == "c"]
    assert max(fills) == 60, f"the same snapshot was consumed repeatedly: {fills}"


def test_sim_resting_order_fills_at_its_own_limit():
    b = SimBroker(SimParams(depth_take_ratio=1.0))
    q0, d0 = _q("K", 59.9, 60.0, 0)
    b.on_quote(q0, d0)
    b.submit(Action("place", 0, cid="r", side="B", instrument_key="K", qty=65, price=60.10), 0)
    q1, d1 = _q("K", 60.2, 60.3, 500)                     # arrives: not marketable -> rests at 60.10
    b.on_quote(q1, d1)
    b.advance(600)
    q2, d2 = _q("K", 59.4, 59.5, 1500)                    # market falls through the resting bid
    b.on_quote(q2, d2)
    evs = [ev for _, ev in b.advance(1600) if ev["remarks"] == "r" and ev["fillshares"]]
    assert evs and evs[-1]["avgprc"] == pytest.approx(60.10), "a resting limit trades at its own price"


def test_sim_first_match_is_the_first_snapshot_after_arrival():
    b = SimBroker(SimParams(depth_take_ratio=1.0))
    q0, d0 = _q("K", 59.9, 60.0, 0)
    b.on_quote(q0, d0)
    b.submit(Action("place", 0, cid="f", side="B", instrument_key="K", qty=65, price=60.10), 0)
    qa, da = _q("K", 59.9, 60.0, 300)                     # first post-arrival snapshot: marketable at 60.00
    qb, db = _q("K", 60.4, 60.5, 900)                     # later in the same second: not marketable
    b.on_quote(qa, da)
    b.on_quote(qb, db)
    evs = [ev for _, ev in b.advance(999) if ev["remarks"] == "f" and ev["fillshares"]]
    assert evs and evs[-1]["avgprc"] == pytest.approx(60.00)


# ------------------------------------------------------------------------- paper runner
class _FakeManager:
    def __init__(self):
        self.q = None

    def subscribe(self, max_queue=256):
        self.q = asyncio.Queue(maxsize=max_queue)
        return self.q

    def unsubscribe(self, q):
        self.q = None


def test_runner_restore_keeps_daily_pause_counters_and_timed_pause(monkeypatch, force_signal):
    from app.scalping import paper_runner as pr
    from app.scalping.config import NIFTY_N1
    eng = ScalperEngine(cfg(), lot_size=LOT)
    eng.on_second(T0, market_at(T0))
    eng.stats.trades_today, eng.stats.realized_net_inr = 20, -2500.0
    eng.paused_for_day = "daily_loss_limit"
    eng.paused_until_ms, eng.paused_reason, eng.last_exit_ms = T0 + 600_000, "4_consecutive_losses", T0
    snap = eng.snapshot()

    async def fake_contracts(db, day, *a, **k):
        return [CE, PE]

    async def fake_load(db, sid, day):
        return snap

    async def noop(*a, **k):
        return 0
    monkeypatch.setattr(pr, "load_live_contracts", fake_contracts)
    monkeypatch.setattr(pr.journal, "load_snapshot", fake_load)
    monkeypatch.setattr(pr.journal, "ensure_indexes", noop)
    monkeypatch.setattr(pr.journal, "write_events", noop)
    r = pr.ScalpPaperRunner(_FakeManager(), db_factory=lambda: None, configs=[cfg()])
    asyncio.run(r._new_session(None, "2026-09-29", T0 + 1000))
    e, _ = r.pairs[0]
    e.on_second(T0 + 2000, market_at(T0 + 2000))
    assert e.paused_for_day == "daily_loss_limit" and e.stats.trades_today == 20
    assert e.stats.realized_net_inr == -2500.0 and e.paused_until_ms == T0 + 600_000


def test_runner_kill_executes_the_cancels(force_signal):
    from app.scalping import paper_runner as pr
    r = pr.ScalpPaperRunner(_FakeManager(), db_factory=lambda: None, configs=[cfg()])
    e, b = ScalperEngine(cfg(), lot_size=LOT), SimBroker(SimParams())
    r.pairs = [(e, b)]
    m = market_at(T0)
    b.set_reference(m.quotes)
    from app.scalping.replay import execute_actions
    execute_actions(e, b, e.on_second(T0, m), T0, [])
    entry = next(iter(e.orders.values()))
    assert b.orders[entry["client_order_id"]]["status"] == "OPEN"
    r.kill("operator_kill", now_ms=T0 + 100)
    assert b.orders[entry["client_order_id"]]["cancel_at_ms"] is not None, "kill must reach the simulator"


def test_runner_survives_an_exception_outside_the_engine_step(monkeypatch):
    from app.scalping import paper_runner as pr
    monkeypatch.setenv("SCALP_PAPER_ENABLED", "1")
    calls = {"n": 0}

    async def flaky(db):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("AutoReconnect (simulated)")

    async def fake_contracts(db, day, *a, **k):
        return [CE, PE]

    async def none(*a, **k):
        return None

    async def zero(*a, **k):
        return 0
    monkeypatch.setattr(pr.journal, "ensure_indexes", flaky)
    monkeypatch.setattr(pr, "load_live_contracts", fake_contracts)
    monkeypatch.setattr(pr.journal, "load_snapshot", none)
    monkeypatch.setattr(pr.journal, "write_events", zero)
    monkeypatch.setattr(pr.journal, "write_trades", zero)
    monkeypatch.setattr(pr.journal, "save_snapshot", none)
    import app.nse_calendar as cal
    monkeypatch.setattr(cal, "market_status", lambda now: {"phase": "open", "is_open": True})

    async def main():
        r = pr.ScalpPaperRunner(_FakeManager(), db_factory=lambda: object(), configs=[cfg()])
        assert r.start()
        await asyncio.sleep(3.2)
        st = r.status()
        await r.stop()
        return st, calls["n"]
    st, n = asyncio.run(main())
    assert n >= 2, "the session setup must be retried after a transient error"
    assert st["running"] and st["session_date"] is not None
    assert st["last_error"] and "AutoReconnect" in st["last_error"]


# ------------------------------------------------------------------------- I13 no cancel-before-arrival livelock
def test_slow_exchange_does_not_livelock_exits(force_signal):
    """Exchange latency (2.5 s) longer than the re-price interval (1 s): exits must still fill."""
    from app.scalping.replay import drive_second
    from app.scalping.market import MarketState
    import random
    rng = random.Random(5)
    e = ScalperEngine(cfg(max_hold_s=2, exit_reprice_ms=1000, entry_timeout_ms=5000), lot_size=LOT)
    b = SimBroker(SimParams(exchange_latency_ms=2500, cancel_latency_ms=140, depth_take_ratio=1.0))
    m = MarketState([CE, PE], lookback_s=10, min_samples=2)
    from test_scalping_engine import tick
    longest = 0
    held_since = None
    for s_ in range(600):
        now = T0 + s_ * 1000 + 999
        mid = 60 + rng.gauss(0, 0.3)
        tks = [{"instrument_key": "NSE_INDEX|Nifty 50", "last_price": 22600.0, "ingest_ts": now - 500},
               tick(CE.instrument_key, round(mid, 2), round(mid + 0.1, 2), now - 400),
               tick(PE.instrument_key, 50.0, 50.1, now - 400)]
        for t in tks:
            m.on_tick(t)
        force_signal["on"] = s_ % 30 == 0
        drive_second(now, m, [(e, b)], [], second_ticks=tks)
        net = sum(b.net_qty.values())
        held_since = (held_since if net > 0 and held_since is not None else (s_ if net > 0 else None))
        if held_since is not None:
            longest = max(longest, s_ - held_since)
    assert e.closed_trades, "no round trip completed"
    assert longest < 60, f"a long was held {longest}s: exits livelocked by cancel-before-arrival"
