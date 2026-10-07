"""Scalper lab: statutory costs, execution simulator, replay determinism, paper-runner isolation."""
from __future__ import annotations

import ast
import asyncio
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

import pytest

from app.scalping import costs
from app.scalping.config import NIFTY_N1, SENSEX_S1E, with_overrides
from app.scalping.engine import Action, lpp_bounds
from app.scalping.market import Contract, MarketState, RollingZ, quote_from_tick
from app.scalping.replay import replay_session
from app.scalping.sim_broker import SimBroker, SimParams

SCALPING_DIR = ROOT / "backend" / "app" / "scalping"


# ------------------------------------------------------------------------------- costs
@pytest.mark.parametrize("exch,prem,qty,expected", [
    ("NFO", 50, 65, 7.71), ("NFO", 100, 65, 15.41), ("NFO", 200, 65, 30.82),
    ("BFO", 100, 20, 4.60), ("BFO", 300, 20, 13.80), ("BFO", 600, 20, 27.59),
])
def test_round_trip_charges_match_verified_2026_schedule(exch, prem, qty, expected):
    """Values independently recomputed by the regulatory verifier (STT 0.15 % from 2026-04-01,
    NSE 0.03553 %, BSE 0.0325 %): docs/scalping/01-feasibility-and-capabilities.md §6."""
    assert costs.charges_inr(exch, prem, prem, qty) == pytest.approx(expected, abs=0.011)


def test_flat_round_trip_pct_and_breakeven():
    assert costs.statutory_pct_of_premium("NFO", 100) == pytest.approx(0.2371, abs=1e-3)
    assert costs.statutory_pct_of_premium("BFO", 300) == pytest.approx(0.2299, abs=1e-3)
    assert costs.breakeven_exit_price("NFO", 100.0, 65) == pytest.approx(100.25)
    assert costs.breakeven_exit_price("BFO", 300.0, 20) == pytest.approx(300.70)


def test_unknown_exchange_refused():
    with pytest.raises(ValueError):
        costs.cost_config("NSE")


# ------------------------------------------------------------------------------- market
def test_quote_rejects_one_sided_and_crossed_books():
    base = {"instrument_key": "K", "ingest_ts": 1, "best_bid_quantity": 1, "best_ask_quantity": 1}
    assert quote_from_tick({**base, "best_bid_price": 0, "best_ask_price": 10}) is None
    assert quote_from_tick({**base, "best_bid_price": 10.1, "best_ask_price": 10.0}) is None
    q = quote_from_tick({**base, "best_bid_price": 10.0, "best_ask_price": 10.1})
    assert q.spread_ticks() == 2 and q.mid == pytest.approx(10.05)


def test_rolling_z_matches_definition():
    z = RollingZ(lookback_s=10, min_samples=3, max_window_s=5)
    for x in [100, 101, 100, 101, 100, 101, 100, 101]:
        z.push(float(x))
    assert z.sigma() == pytest.approx(1.0690, abs=1e-3)          # std of +-1 changes, ddof=1
    z.push(110.0)
    assert z.z(1) == pytest.approx(9.0 / (z.sigma() * 1.0))


def test_missing_second_breaks_change_series():
    z = RollingZ(lookback_s=10, min_samples=2, max_window_s=5)
    for x in [1.0, 2.0, None, 3.0]:
        z.push(x)
    assert z.z(1) is None                                           # no value 1 s before the gap


def test_lpp_band_is_max_of_40pct_and_20_rupees():
    assert lpp_bounds(30.0) == pytest.approx((10.0, 50.0))
    assert lpp_bounds(200.0) == pytest.approx((120.0, 280.0))


# ------------------------------------------------------------------------------- sim broker
def _q(key, bid, ask, t, bq=100, aq=100, ltp=None):
    return quote_from_tick({"instrument_key": key, "best_bid_price": bid, "best_ask_price": ask,
                            "best_bid_quantity": bq, "best_ask_quantity": aq, "ingest_ts": t,
                            "last_price": ltp or (bid + ask) / 2})


def _depth(ask0, qty, n=5):
    return [{"bid_price": round(ask0 - 0.1 - i * 0.05, 2), "bid_quantity": qty,
             "ask_price": round(ask0 + i * 0.05, 2), "ask_quantity": qty} for i in range(n)]


def test_sim_never_fills_on_the_decision_quote_and_partially_fills_against_depth():
    b = SimBroker(SimParams(depth_take_ratio=0.5))
    b.on_quote(_q("K", 59.9, 60.0, 1000), _depth(60.0, 40))
    res = b.submit(Action("place", 1000, cid="c1", side="B", instrument_key="K", qty=130, price=60.10), 1000)
    assert res.ok
    assert all(ev["fillshares"] == 0 for _, ev in b.advance(1100))     # quote predates arrival
    b.on_quote(_q("K", 59.9, 60.0, 1500), _depth(60.0, 40))            # 3 marketable levels x 20 = 60
    evs = [ev for _, ev in b.advance(1600)]
    assert evs[-1]["fillshares"] == 60 and evs[-1]["status"] == "PARTIALLY_FILLED"
    assert evs[-1]["avgprc"] == pytest.approx(60.05)
    assert b.net_qty["K"] == 60


def test_sim_lpp_reject_and_cancel_race():
    b = SimBroker(SimParams())
    b.on_quote(_q("K", 99.9, 100.0, 0, ltp=100.0), _depth(100.0, 1000))
    r = b.submit(Action("place", 0, cid="x", side="S", instrument_key="K", qty=10, price=50.0), 0)
    assert not r.ok and "LPP" in r.reject_reason
    # (a) cancel lands (t=150) before any post-arrival quote -> CANCELED, nothing filled
    r = b.submit(Action("place", 0, cid="y", side="B", instrument_key="K", qty=10, price=100.0), 0)
    assert r.ok and b.cancel("y", 10)
    b.on_quote(_q("K", 99.9, 100.0, 100), _depth(100.0, 1000))          # quote predates arrival (140)
    evs = [ev for _, ev in b.advance(200) if ev["remarks"] == "y"]
    assert evs[-1]["status"] == "CANCELED" and evs[-1]["fillshares"] == 0
    # (b) a quote after arrival (145) but before the cancel lands (150) fills first: the race is lost
    b2 = SimBroker(SimParams())
    b2.on_quote(_q("K", 99.9, 100.0, 0, ltp=100.0), _depth(100.0, 1000))
    b2.submit(Action("place", 0, cid="z", side="B", instrument_key="K", qty=10, price=100.0), 0)
    b2.cancel("z", 10)
    b2.on_quote(_q("K", 99.9, 100.0, 145), _depth(100.0, 1000))
    evs = [ev for _, ev in b2.advance(149) if ev["remarks"] == "z"]
    assert evs[-1]["status"] == "COMPLETE" and evs[-1]["fillshares"] == 10
    assert b2.cancel("z", 160) is False


def test_sim_no_passive_fills_by_default():
    b = SimBroker(SimParams())
    b.on_quote(_q("K", 59.9, 60.0, 0), _depth(60.0, 1000))
    b.submit(Action("place", 0, cid="p", side="B", instrument_key="K", qty=10, price=59.95), 0)
    b.on_quote(_q("K", 59.9, 60.0, 1000, ltp=59.5), _depth(60.0, 1000))   # trades print below our bid
    assert all(ev["fillshares"] == 0 for _, ev in b.advance(1100))


# ------------------------------------------------------------------------------- replay determinism
def _synthetic_tape():
    import math
    import random
    rng = random.Random(3)
    t0 = 1790650800000          # 2026-09-29 09:20:00 IST
    exp = "2026-09-29"
    contracts = []
    for k in range(22500, 22751, 50):
        for side in ("CE", "PE"):
            contracts.append(Contract(f"NSE_FO|{k}{side}", "NIFTY", float(k), side, exp, 65, f"N{k}{side}"))
    tape, spot = [], 22600.0
    for s in range(3600):
        now = t0 + s * 1000 + 300
        spot += rng.gauss(0, 2.0) + (25 if s % 700 == 650 else 0)
        tape.append({"instrument_key": "NSE_INDEX|Nifty 50", "last_price": round(spot, 2), "ingest_ts": now, "ts": now})
        for c in contracts:
            intrinsic = max(0.0, spot - c.strike) if c.side == "CE" else max(0.0, c.strike - spot)
            mid = intrinsic + 40 * math.exp(-abs(spot - c.strike) / 120)
            tape.append({"instrument_key": c.instrument_key, "best_bid_price": round(mid - 0.05, 2),
                         "best_ask_price": round(mid + 0.05, 2), "best_bid_quantity": 3000, "best_ask_quantity": 3000,
                         "market_depth": [{"bid_price": round(mid - 0.05 - i * .05, 2), "bid_quantity": 3000,
                                           "ask_price": round(mid + 0.05 + i * .05, 2), "ask_quantity": 3000} for i in range(5)],
                         "last_price": round(mid, 2), "ingest_ts": now + 50, "ts": now + 50})
    return tape, contracts


def test_replay_is_deterministic():
    tape, contracts = _synthetic_tape()
    cfg = with_overrides(NIFTY_N1, z_threshold=2.0, sigma_min_samples=120, sigma_lookback_s=300)
    a = replay_session("2026-09-29", [cfg], tape=tape, contracts=contracts)
    b = replay_session("2026-09-29", [cfg], tape=tape, contracts=contracts)
    ta, tb = a["engines"][0]["trades"], b["engines"][0]["trades"]
    assert ta, "synthetic tape produced no trades — the determinism check would be vacuous"
    assert [(t["entry_avg"], t["exit_avg"], t["net_inr"], t["exit_reason"]) for t in ta] == \
           [(t["entry_avg"], t["exit_avg"], t["net_inr"], t["exit_reason"]) for t in tb]


# ------------------------------------------------------------------------------- isolation
def test_scalping_package_cannot_reach_a_broker():
    """No module in app.scalping imports app.live (executor, client, guard ...) or any HTTP client."""
    banned = ("app.live", "httpx", "requests", "app.auto_live", "app.live_deploy_context")
    for py in SCALPING_DIR.glob("*.py"):
        tree = ast.parse(py.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            mods = []
            if isinstance(node, ast.Import):
                mods = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                mods = [node.module]
            for m in mods:
                if m == "app.live.order_sm":       # the pure state machine is the one allowed reuse
                    continue
                assert not m.startswith(banned), f"{py.name} imports {m}"


def test_paper_runner_is_off_by_default_and_refuses_non_paper_configs(monkeypatch):
    from app.scalping import paper_runner as pr
    monkeypatch.delenv("SCALP_PAPER_ENABLED", raising=False)
    assert pr.paper_enabled() is False
    r = pr.ScalpPaperRunner(object(), db_factory=lambda: None)
    assert r.start() is False
    from dataclasses import replace
    with pytest.raises(ValueError):
        pr.ScalpPaperRunner(object(), db_factory=lambda: None, configs=[replace(NIFTY_N1, mode="replay")])


def test_tape_archive_pipeline_is_idempotent_and_ttl_proof():
    from app.scalping.recorder import archive_pipeline
    p = archive_pipeline(None)
    merge = p[-1]["$merge"]
    assert merge["on"] == "_id" and merge["whenMatched"] == "keepExisting"
    assert {"$unset": "stored_at"} in p            # no BSON date field reaches the archive
    assert p[0]["$match"]["mode"] == "full"


def test_specs_are_paper_and_distinct_per_index():
    assert NIFTY_N1.mode == SENSEX_S1E.mode == "paper"
    assert NIFTY_N1.exchange == "NFO" and SENSEX_S1E.exchange == "BFO"
    assert NIFTY_N1.signal_kind != SENSEX_S1E.signal_kind
    assert SENSEX_S1E.allowed_dte == frozenset({0})
    assert all(k in NIFTY_N1.provenance for k in ("signal", "max_spread_pct", "money"))
