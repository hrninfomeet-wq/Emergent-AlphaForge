# tests/test_premium_momentum_evaluator_5b.py
"""Phase 5B Task A4 — evaluator + auto_live per-leg plumbing.

Covers, per docs/superpowers/plans/2026-07-15-premium-momentum-phase5b-execution.md A4:
  - both-mode: two bars -> two independent CONFIRMED signals (one per leg),
    per-leg latches (ce_triggered/pe_triggered), never the session-global
    triggered_side; leg identity in the signal doc's premium_momentum sub-dict.
  - first_to_trigger: signal doc + latch shape BYTE-IDENTICAL to Track B
    (no "leg" key, legacy triggered_side latch) — the existing exact-equality
    pin in tests/test_premium_momentum_evaluator.py is the primary guarantee;
    this file adds the leg-absence assertion explicitly.
  - day-stop: realized-only breach -> outcome "day_stop", session marked done
    (reason day_stop), fire-once flag (second breach bar returns
    day_stop_squared=False), and NO deployment square for paper/shadow mode.
  - VIX gate: configured gate + out-of-band stored VIX -> no_setup
    (premium_done, reason vix_gate); configured gate + NO stored VIX ->
    vix_unverifiable (never a silent pass); NO gate -> zero VIX queries
    (inert for every pre-5B deployment).
  - exit_time -> risk_hints.square_at_ist, clamped strictly before 15:00.

Reuses the Track B evaluator harness verbatim (fakes, contracts, candles,
tick patching) by importing from tests.test_premium_momentum_evaluator.
CONTAINER-pattern test (branch imports app.runtime -> motor); runs on any
host whose venv has motor, same as the parent file.
"""
from __future__ import annotations

import asyncio
import importlib.util
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

pytestmark = pytest.mark.skipif(
    importlib.util.find_spec("motor") is None,
    reason="branch imports app.runtime (motor) — runs in the backend container",
)

from tests.test_premium_momentum_evaluator import (  # noqa: E402
    CE_KEY, PE_KEY, TS_0931, TS_0932, _FakeDB, _eval_with_ticks, _fresh_ticks,
    _dep_from_db, make_candles, make_contracts, make_deployment, now_ms, run,
    seed_db, _future_expiry,
)
from app.deployment_evaluator import evaluate_deployment_on_close  # noqa: E402

register_premium_momentum = pytest.fixture(autouse=True)(
    # Same real-plugin registration the parent harness uses.
    __import__("tests.test_premium_momentum_evaluator",
               fromlist=["register_premium_momentum"]).register_premium_momentum.__wrapped__
)

TS_0933 = TS_0932 + 60_000
_SESSION = "2026-07-10"


def _both_mode_dep(db: _FakeDB) -> Dict[str, Any]:
    dep = db.strategy_deployments.rows[0]
    dep["params"]["leg_mode"] = "both"
    return dict(dep)


def _add_bar(db: _FakeDB, ts: int, close: float = 24000.5) -> None:
    db.candles_1m.rows.append({
        "instrument": "NIFTY", "ts": ts,
        "open": 24000.0, "high": 24001.0, "low": 23999.0,
        "close": close, "volume": 2000,
    })


def _recent_tick(db: _FakeDB, key: str, price: float) -> None:
    db.ticks.rows = [{"instrument_key": key, "ts": now_ms(), "last_price": price}]


# --- both-mode: two bars, two signals, per-leg latches ------------------------

def test_both_mode_two_bars_two_signals_per_leg_latches():
    db = _FakeDB()
    seed_db(db, end_ts=TS_0931)
    dep = _both_mode_dep(db)

    _eval_with_ticks(db, dep, _fresh_ticks(100.0, 110.0))          # ref bar

    # Bar 2: CE crosses (+16%) -> pce signal + ce_triggered latch.
    _add_bar(db, TS_0932)
    _recent_tick(db, CE_KEY, 116.0)
    r1 = _eval_with_ticks(db, _dep_from_db(db), _fresh_ticks(116.0, 111.0))
    assert r1["outcome"] == "clean" and r1["direction"] == "CE"
    lock = db.premium_locks.docs[0]
    assert lock.get("ce_triggered") is True
    assert lock.get("triggered_side") is None, \
        "both-mode must never write the session-global first-to-trigger latch"

    # Bar 3: PE crosses (+16%) — its leg is NOT blocked by CE's latch.
    _add_bar(db, TS_0933)
    _recent_tick(db, PE_KEY, 128.0)
    r2 = _eval_with_ticks(db, _dep_from_db(db), _fresh_ticks(117.0, 128.0))
    assert r2["outcome"] == "clean" and r2["direction"] == "PE"
    lock = db.premium_locks.docs[0]
    assert lock.get("pe_triggered") is True and lock.get("ce_triggered") is True

    assert len(db.signals.rows) == 2
    legs = [s["premium_momentum"].get("leg") for s in db.signals.rows]
    assert legs == ["pce", "ppe"]


# --- first_to_trigger: Track B shape preserved (no leg key, legacy latch) -----

def test_first_to_trigger_signal_doc_and_latch_shape_unchanged():
    db = _FakeDB()
    dep = seed_db(db, end_ts=TS_0931)
    _eval_with_ticks(db, dep, _fresh_ticks(100.0, 110.0))
    _add_bar(db, TS_0932)
    _recent_tick(db, CE_KEY, 116.0)
    res = _eval_with_ticks(db, _dep_from_db(db), _fresh_ticks(116.0, 111.0))
    assert res["outcome"] == "clean"
    sig = db.signals.rows[0]
    assert "leg" not in sig["premium_momentum"], \
        "first_to_trigger signal docs must stay byte-identical to Track B"
    lock = db.premium_locks.docs[0]
    assert lock["triggered_side"] == "CE"
    assert "ce_triggered" not in lock


# --- day-stop: realized-only breach, fire-once, paper never squares -----------

def _closed_trade(dep_id: str, pnl: float) -> Dict[str, Any]:
    closed_at = datetime(2026, 7, 10, 10, 0, tzinfo=timezone.utc).isoformat()
    return {"deployment_id": dep_id, "status": "CLOSED",
            "realized_pnl": pnl, "entry_value": 10000.0,
            "created_at": closed_at, "closed_at": closed_at}


def test_day_stop_breach_blocks_and_fires_once_paper_never_squares():
    db = _FakeDB()
    seed_db(db, end_ts=TS_0932)
    dep = _dep_from_db(db)
    dep["params"]["session_max_loss_rupees"] = 5000.0
    db.strategy_deployments.rows[0] = dict(dep)
    # shadow-mode deployment reads paper_trades; realized -6k breaches -5k.
    db.paper_trades.rows = [_closed_trade(dep["id"], -6000.0)]

    square_calls = []
    async def _fake_square(dep_id, *, reason):
        square_calls.append((dep_id, reason))
        return []

    import app.routers.deployments as dep_router
    with patch.object(dep_router, "_square_live_positions_for_deployment", _fake_square):
        r1 = _eval_with_ticks(db, dep, _fresh_ticks(100.0, 110.0))
    assert r1["outcome"] == "day_stop"
    assert r1["day_stop_squared"] is True          # fire-once winner
    assert square_calls == [], "paper/shadow mode must NEVER call the live square path"

    lock = db.premium_locks.docs[0]
    assert lock["day_stop_fired"] is True
    assert lock["done_for_day"] is True and lock["done_reason"] == "day_stop"

    # Next bar: still breached -> blocked again, but the flag already exists
    # so the finalizer is a no-op (day_stop_squared False).
    _add_bar(db, TS_0933)
    r2 = _eval_with_ticks(db, _dep_from_db(db), _fresh_ticks(100.0, 110.0))
    assert r2["outcome"] == "day_stop"
    assert r2["day_stop_squared"] is False


def test_day_stop_live_mode_squares_via_deployment_stop_path_once():
    db = _FakeDB()
    seed_db(db, end_ts=TS_0932)
    dep = _dep_from_db(db)
    dep["mode"] = "live"
    dep["params"]["session_max_loss_rupees"] = 5000.0
    db.strategy_deployments.rows[0] = dict(dep)
    db.live_trades.rows = [_closed_trade(dep["id"], -7000.0)]

    square_calls = []
    async def _fake_square(dep_id, *, reason):
        square_calls.append((dep_id, reason))
        return []

    import app.routers.deployments as dep_router
    with patch.object(dep_router, "_square_live_positions_for_deployment", _fake_square):
        r1 = _eval_with_ticks(db, dep, _fresh_ticks(100.0, 110.0))
        _add_bar(db, TS_0933)
        r2 = _eval_with_ticks(db, _dep_from_db(db), _fresh_ticks(100.0, 110.0))
    assert r1["outcome"] == "day_stop" and r1["day_stop_squared"] is True
    assert r2["outcome"] == "day_stop" and r2["day_stop_squared"] is False
    assert square_calls == [(dep["id"], "premium_day_stop")], \
        "live square must fire exactly ONCE through the existing deployment-stop path"


def test_day_stop_not_breached_is_fully_inert():
    db = _FakeDB()
    seed_db(db, end_ts=TS_0931)
    dep = _dep_from_db(db)
    dep["params"]["session_max_loss_rupees"] = 5000.0
    db.strategy_deployments.rows[0] = dict(dep)
    db.paper_trades.rows = [_closed_trade(dep["id"], -1000.0)]   # under the cap
    res = _eval_with_ticks(db, dep, _fresh_ticks(100.0, 110.0))
    assert res["outcome"] == "no_setup" and res["reason"] == "premium_monitoring"
    assert "day_stop_fired" not in db.premium_locks.docs[0]


# --- VIX gate ------------------------------------------------------------------

def _vix_row(ts: int, close: float) -> Dict[str, Any]:
    return {"instrument": "INDIAVIX", "ts": ts, "close": close}


def test_vix_gate_out_of_band_blocks_session_honestly():
    db = _FakeDB()
    seed_db(db, end_ts=TS_0931)
    dep = _dep_from_db(db)
    dep["params"]["vix_min"] = 14.0
    db.strategy_deployments.rows[0] = dict(dep)
    db.candles_1m.rows.append(_vix_row(TS_0931 - 60_000, 11.5))   # below the gate
    res = _eval_with_ticks(db, dep, _fresh_ticks(100.0, 110.0))
    assert res["outcome"] == "no_setup"
    assert res["reason"] == "premium_done"
    assert res["pm"]["reason"] == "vix_gate"
    assert db.premium_locks.docs[0]["done_for_day"] is True


def test_vix_gate_unverifiable_blocks_never_silently_passes():
    db = _FakeDB()
    seed_db(db, end_ts=TS_0931)
    dep = _dep_from_db(db)
    dep["params"]["vix_max"] = 20.0
    db.strategy_deployments.rows[0] = dict(dep)
    # NO INDIAVIX rows anywhere -> unverifiable.
    res = _eval_with_ticks(db, dep, _fresh_ticks(100.0, 110.0))
    assert res["outcome"] == "no_setup"
    assert res["pm"]["reason"] == "vix_unverifiable"


def test_no_vix_gate_means_no_vix_lookup_and_normal_flow():
    db = _FakeDB()
    dep = seed_db(db, end_ts=TS_0931)
    # A stored VIX row that WOULD gate if a gate were configured — must be ignored.
    db.candles_1m.rows.append(_vix_row(TS_0931 - 60_000, 99.0))
    res = _eval_with_ticks(db, dep, _fresh_ticks(100.0, 110.0))
    assert res["outcome"] == "no_setup" and res["reason"] == "premium_monitoring"


# --- exit_time -> square_at_ist risk hint (clamped) ----------------------------

def _trigger_clean_signal(db: _FakeDB) -> Dict[str, Any]:
    _add_bar(db, TS_0932)
    _recent_tick(db, CE_KEY, 116.0)
    res = _eval_with_ticks(db, _dep_from_db(db), _fresh_ticks(116.0, 111.0))
    assert res["outcome"] == "clean"
    return db.signals.rows[-1]


def test_exit_time_before_eod_lands_in_risk_hints():
    db = _FakeDB()
    seed_db(db, end_ts=TS_0931)
    dep = _dep_from_db(db)
    dep["params"]["exit_time"] = "14:30"
    db.strategy_deployments.rows[0] = dict(dep)
    _eval_with_ticks(db, dep, _fresh_ticks(100.0, 110.0))
    sig = _trigger_clean_signal(db)
    assert sig["risk_hints"]["square_at_ist"] == "14:30"


def test_exit_time_at_or_after_eod_is_clamped_out():
    db = _FakeDB()
    seed_db(db, end_ts=TS_0931)
    dep = _dep_from_db(db)
    dep["params"]["exit_time"] = "15:13"     # EXP2's value: backtest-only
    db.strategy_deployments.rows[0] = dict(dep)
    _eval_with_ticks(db, dep, _fresh_ticks(100.0, 110.0))
    sig = _trigger_clean_signal(db)
    assert "square_at_ist" not in sig["risk_hints"], \
        "exit_time >= the 15:00 EOD square must be ignored (EOD backstop wins)"
