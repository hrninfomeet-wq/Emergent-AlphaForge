# tests/test_premium_momentum_evaluator.py
"""Track B Task 4 — evaluator branch wiring for premium_momentum deployments.

The evaluator must route premium_momentum deployments through the premium
session engine (strike lock at the reference bar, ref capture from FRESH
ticks, first-to-trigger monitor) INSTEAD of the generic spot evaluate +
per-bar contract re-resolution, then REJOIN the shared signal pipeline
(audit/lifecycle/dedupe) and latch the trigger ONLY after a clean journal.

CONTAINER test (the branch imports app.runtime -> motor). Fakes follow the
repo's in-memory async-collection pattern; premium_locks reuses _FakeLocks
from tests/test_premium_lock_store.py.
"""
from __future__ import annotations

import asyncio
import importlib.util
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import patch

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

pytestmark = pytest.mark.skipif(
    importlib.util.find_spec("motor") is None,
    reason="branch imports app.runtime (motor) — runs in the backend container",
)

from app.deployment_evaluator import evaluate_deployment_on_close  # noqa: E402
from tests.test_premium_lock_store import _FakeLocks  # noqa: E402  (fake premium_locks)


@pytest.fixture(autouse=True)
def register_premium_momentum():
    """Register the REAL plugin (tests bypass server-startup auto_discover)."""
    from app.strategies.base import get_registry
    from app.strategies.plugins.premium_momentum import PremiumMomentum
    registry = get_registry()
    inst = PremiumMomentum()
    registry.register(inst)
    yield
    registry._strategies.pop(inst.id, None)


def run(c):
    return asyncio.run(c)


IST_OFFSET = timedelta(hours=5, minutes=30)
_IST = timezone(IST_OFFSET)

# Real epoch-ms for 2026-07-10 IST bar times (09:31 IST == 04:01 UTC).
TS_0931 = int(datetime(2026, 7, 10, 9, 31, tzinfo=_IST).timestamp() * 1000)
TS_0932 = TS_0931 + 60_000
TS_1450 = int(datetime(2026, 7, 10, 14, 50, tzinfo=_IST).timestamp() * 1000)


def now_ms() -> int:
    return int(datetime.now(timezone.utc).timestamp() * 1000)


# --- fakes (repo in-memory async-collection style) ---------------------------

def _matches(row: Dict[str, Any], query: Dict[str, Any]) -> bool:
    for k, v in (query or {}).items():
        if isinstance(v, dict) and "$exists" in v:
            if bool(k in row) != bool(v["$exists"]):
                return False
        elif isinstance(v, dict) and "$gte" in v:
            rv = row.get(k)
            if rv is None or rv < v["$gte"]:
                return False
        elif isinstance(v, dict) and "$gt" in v:
            rv = row.get(k)
            if rv is None or rv <= v["$gt"]:
                return False
        elif row.get(k) != v:
            return False
    return True


class _FakeCursor:
    def __init__(self, rows: List[Dict[str, Any]]):
        self._rows = list(rows)

    def sort(self, key, direction: int = 1):
        if isinstance(key, list):
            for k, d in reversed(key):
                self._rows.sort(key=lambda r: r.get(k, 0), reverse=(d == -1))
        else:
            self._rows.sort(key=lambda r: r.get(key, 0), reverse=(direction == -1))
        return self

    def limit(self, n: int):
        self._rows = self._rows[: int(n)]
        return self

    async def to_list(self, length: Optional[int] = None):
        return list(self._rows if length is None else self._rows[: int(length)])


class _FakeCol:
    def __init__(self):
        self.rows: List[Dict[str, Any]] = []

    def find(self, query=None, projection=None):
        return _FakeCursor([dict(r) for r in self.rows if _matches(r, query or {})])

    async def find_one(self, query, projection=None):
        for r in self.rows:
            if _matches(r, query):
                return dict(r)
        return None

    async def insert_one(self, doc):
        self.rows.append(dict(doc))

    async def count_documents(self, query):
        return sum(1 for r in self.rows if _matches(r, query))

    async def distinct(self, key, query=None):
        seen: List[Any] = []
        for r in self.rows:
            if _matches(r, query or {}):
                v = r.get(key)
                if v is not None and v not in seen:
                    seen.append(v)
        return seen

    async def update_one(self, query, update, upsert=False):
        for r in self.rows:
            if _matches(r, query):
                r.update(update.get("$set", {}))
                for k in update.get("$unset", {}):
                    r.pop(k, None)
                return type("R", (), {"matched_count": 1, "modified_count": 1})()
        if upsert:
            new = dict(query)
            new.update(update.get("$set", {}))
            self.rows.append(new)
        return type("R", (), {"matched_count": 0, "modified_count": 0})()


class _FakeDB:
    def __init__(self):
        self.candles_1m = _FakeCol()
        self.options_1m = _FakeCol()
        self.ticks = _FakeCol()
        self.option_contracts = _FakeCol()
        self.signals = _FakeCol()
        self.strategy_deployments = _FakeCol()
        self.pretrade_profiles = _FakeCol()
        self.paper_trades = _FakeCol()
        self.live_trades = _FakeCol()
        self.premium_locks = _FakeLocks()


# --- fixtures ----------------------------------------------------------------

def _future_expiry() -> str:
    return ((datetime.now(timezone.utc) + IST_OFFSET) + timedelta(days=3)).strftime("%Y-%m-%d")


def make_contracts(expiry_date: str) -> List[Dict[str, Any]]:
    out = []
    for strike in (23850, 23900, 23950, 24000, 24050, 24100, 24150):
        for side in ("CE", "PE"):
            out.append({
                "underlying": "NIFTY",
                "strike": float(strike),
                "side": side,
                "expiry": expiry_date,
                "expiry_date": expiry_date,
                "instrument_key": f"NSE_FO|T|{strike}{side}",
                "trading_symbol": f"NIFTY{strike}{side}",
                "lot_size": 65,
            })
    return out


CE_KEY = "NSE_FO|T|23950CE"   # itm1 CE for spot 24000 (step 50)
PE_KEY = "NSE_FO|T|24050PE"   # itm1 PE for spot 24000


def make_candles(end_ts: int, n: int = 60) -> List[Dict[str, Any]]:
    rows = []
    price = 23990.0
    for i in range(n):
        ts = end_ts - (n - 1 - i) * 60_000
        price += 10.0 / n
        rows.append({
            "instrument": "NIFTY", "ts": ts,
            "open": price - 0.2, "high": price + 0.5, "low": price - 0.5,
            "close": 24000.0 if i == n - 1 else price,
            "volume": 1000 + i,
        })
    return rows


def make_deployment(last_evaluated_ts: int = 0) -> Dict[str, Any]:
    return {
        "id": "pm-deploy-1",
        "name": "PM test deployment",
        "source_type": "preset",
        "source_id": "pm-preset",
        "strategy_id": "premium_momentum",
        "params": {"reference_time": "09:31", "moneyness": "itm1",
                   "side": "first_to_trigger", "momentum_pct": 15.0,
                   "stop_pct": 20.0, "late_lock_cutoff": "10:15"},
        "instrument": "NIFTY",
        "timeframe": "1m",
        "confirmation_mode": "1m_close",
        "option_policy": {"moneyness": ["atm"], "expiry_policy": "next_available"},
        "pretrade_profile": "Balanced",   # branch bypasses the score/regime filter (see test below with the REAL seeded profile)
        "mode": "shadow",
        "risk": {},
        "status": "ACTIVE",
        "last_evaluated_ts": last_evaluated_ts,
    }


def seed_db(db: _FakeDB, *, end_ts: int, last_evaluated_ts: int = 0) -> Dict[str, Any]:
    dep = make_deployment(last_evaluated_ts=last_evaluated_ts)
    db.candles_1m.rows = make_candles(end_ts)
    db.strategy_deployments.rows = [dict(dep)]
    db.option_contracts.rows = make_contracts(_future_expiry())
    return dep


def _eval_with_ticks(db: _FakeDB, dep: Dict[str, Any], ticks: Dict[str, Dict[str, Any]]):
    """Run the evaluator with the live stream's latest_tick_map faked."""
    from app import runtime as rt
    with patch.object(rt.upstox_stream_manager, "latest_tick_map", lambda: ticks):
        return run(evaluate_deployment_on_close(db, dep))


def _fresh_ticks(ce: float, pe: float) -> Dict[str, Dict[str, Any]]:
    ts = now_ms()   # branch freshness is vs wall-clock now_ts
    return {CE_KEY: {"last_price": ce, "ts": ts},
            PE_KEY: {"last_price": pe, "ts": ts}}


def _dep_from_db(db: _FakeDB) -> Dict[str, Any]:
    return dict(db.strategy_deployments.rows[0])


# --- (a) ref bar: lock created, outcome premium_monitoring --------------------

def test_ref_bar_creates_lock_and_returns_premium_monitoring():
    db = _FakeDB()
    dep = seed_db(db, end_ts=TS_0931)
    res = _eval_with_ticks(db, dep, _fresh_ticks(100.0, 110.0))
    assert res["outcome"] == "no_setup"
    assert res["reason"] == "premium_monitoring"
    # lock doc exists with BOTH locked strikes + captured refs
    lock = db.premium_locks.docs[0]
    assert lock["ce"]["instrument_key"] == CE_KEY
    assert lock["pe"]["instrument_key"] == PE_KEY
    assert lock["ce_ref_premium"] == 100.0 and lock["pe_ref_premium"] == 110.0
    # no signal journaled; idempotency pointer advanced
    assert db.signals.rows == []
    assert db.strategy_deployments.rows[0]["last_evaluated_ts"] == TS_0931


# --- (b) trigger bar: CONFIRMED signal from the LOCKED contract + latch -------

def test_trigger_bar_journals_confirmed_signal_from_lock_and_latches():
    db = _FakeDB()
    dep = seed_db(db, end_ts=TS_0931)
    _eval_with_ticks(db, dep, _fresh_ticks(100.0, 110.0))          # ref bar

    # next bar: CE premium +16% > 15% -> trigger. Spot in candles stays 24000;
    # the contract MUST come from the lock, never re-resolved.
    db.candles_1m.rows.append({
        "instrument": "NIFTY", "ts": TS_0932,
        "open": 24000.0, "high": 24001.0, "low": 23999.0,
        "close": 24000.5, "volume": 2000,
    })
    # recent tradable data for the locked contract (tracked_for_pnl path)
    db.ticks.rows = [{"instrument_key": CE_KEY, "ts": now_ms(), "last_price": 116.0}]
    res = _eval_with_ticks(db, _dep_from_db(db), _fresh_ticks(116.0, 111.0))

    assert res["outcome"] == "clean"
    assert res["direction"] == "CE"
    assert len(db.signals.rows) == 1
    sig = db.signals.rows[0]
    assert sig["state"] == "CONFIRMED"
    assert sig["option_contract"]["instrument_key"] == CE_KEY     # the LOCKED key
    assert sig["risk_hints"]["stop_pct"] == 20.0
    assert sig["risk_hints"]["target_pct"] is None
    assert sig["confidence"] == 100
    assert sig["premium_momentum"] == {"ref_premium": 100.0, "premium_now": 116.0}
    assert any("premium" in r for r in sig.get("reasons", []))
    # latch fired AFTER the clean journal
    assert db.premium_locks.docs[0]["triggered_side"] == "CE"


# --- (c) same trigger bar re-run: idempotency intact ---------------------------

def test_rerun_same_trigger_bar_is_idempotent():
    db = _FakeDB()
    dep = seed_db(db, end_ts=TS_0931)
    _eval_with_ticks(db, dep, _fresh_ticks(100.0, 110.0))          # ref bar
    db.candles_1m.rows.append({
        "instrument": "NIFTY", "ts": TS_0932,
        "open": 24000.0, "high": 24001.0, "low": 23999.0,
        "close": 24000.5, "volume": 2000,
    })
    db.ticks.rows = [{"instrument_key": CE_KEY, "ts": now_ms(), "last_price": 116.0}]
    r1 = _eval_with_ticks(db, _dep_from_db(db), _fresh_ticks(116.0, 111.0))
    assert r1["outcome"] == "clean"
    r2 = _eval_with_ticks(db, _dep_from_db(db), _fresh_ticks(116.0, 111.0))
    assert r2["outcome"] == "skipped"
    assert r2["reason"] == "already_evaluated_this_bar"
    assert len(db.signals.rows) == 1                               # no double journal


# --- review fix (critical): the PRODUCTION-seeded profile must not block ------

def test_trigger_journals_clean_with_real_seeded_balanced_profile():
    """Seed the REAL Balanced profile (server startup seeds DEFAULT_PROFILES).

    sig is None on the Track B branch; before the fix the pipeline fed score=0
    into _apply_pretrade_filter, so Balanced's min_confidence_score=60 (and its
    allowed_regimes) blocked EVERY trigger in production — outcome 'blocked'
    each bar, latch never set, strategy could never trade."""
    from app.runtime import DEFAULT_PROFILES
    db = _FakeDB()
    dep = seed_db(db, end_ts=TS_0931)
    db.pretrade_profiles.rows = [
        {"name": "Balanced", "settings": dict(DEFAULT_PROFILES["Balanced"])}
    ]
    _eval_with_ticks(db, dep, _fresh_ticks(100.0, 110.0))          # ref bar
    db.candles_1m.rows.append({
        "instrument": "NIFTY", "ts": TS_0932,
        "open": 24000.0, "high": 24001.0, "low": 23999.0,
        "close": 24000.5, "volume": 2000,
    })
    db.ticks.rows = [{"instrument_key": CE_KEY, "ts": now_ms(), "last_price": 116.0}]
    res = _eval_with_ticks(db, _dep_from_db(db), _fresh_ticks(116.0, 111.0))

    assert res["outcome"] == "clean"
    assert res["blockers"] == []                                   # no pretrade_min_score / pretrade_regime
    sig = db.signals.rows[0]
    assert sig["state"] == "CONFIRMED"
    # audit is honest + internally consistent: bypass recorded, score==confidence
    assert "pretrade_bypassed" in sig["context"]
    assert sig["context"]["score"] == 100
    assert sig["confidence"] == 100
    assert db.premium_locks.docs[0]["triggered_side"] == "CE"


# --- review fix (important): risk_hints come from merged_params ---------------

def test_risk_hints_use_schema_defaults_when_deployment_omits_stop_pct():
    """A deployment whose params OMIT stop_pct must journal the plugin-schema
    default (20.0) via merged_params — raw params journaled None, and the live
    exit plan then fell through to the 50% deep-default floor (2.5x the stop)."""
    db = _FakeDB()
    dep = seed_db(db, end_ts=TS_0931)
    dep["params"].pop("stop_pct")                                  # partial override set
    db.strategy_deployments.rows = [dict(dep)]
    _eval_with_ticks(db, dep, _fresh_ticks(100.0, 110.0))          # ref bar
    db.candles_1m.rows.append({
        "instrument": "NIFTY", "ts": TS_0932,
        "open": 24000.0, "high": 24001.0, "low": 23999.0,
        "close": 24000.5, "volume": 2000,
    })
    db.ticks.rows = [{"instrument_key": CE_KEY, "ts": now_ms(), "last_price": 116.0}]
    res = _eval_with_ticks(db, _dep_from_db(db), _fresh_ticks(116.0, 111.0))

    assert res["outcome"] == "clean"
    sig = db.signals.rows[0]
    assert sig["risk_hints"]["stop_pct"] == 20.0                   # schema default, not None
    assert sig["risk_hints"]["target_pct"] is None                 # schema default None preserved


# --- review fix (minor): a refused latch must not route the signal ------------

def test_latch_refusal_downgrades_outcome_so_signal_is_not_routed():
    """If latch_trigger returns False (concurrent latch / done_for_day flipped
    mid-bar) the pass must NOT stay 'clean' — the sink tee routes exactly
    outcome=='clean', so a refused latch would otherwise place a trade for a
    session the lock store considers closed."""
    db = _FakeDB()
    dep = seed_db(db, end_ts=TS_0931)
    _eval_with_ticks(db, dep, _fresh_ticks(100.0, 110.0))          # ref bar
    db.candles_1m.rows.append({
        "instrument": "NIFTY", "ts": TS_0932,
        "open": 24000.0, "high": 24001.0, "low": 23999.0,
        "close": 24000.5, "volume": 2000,
    })
    db.ticks.rows = [{"instrument_key": CE_KEY, "ts": now_ms(), "last_price": 116.0}]

    async def _refuse(*a, **k):
        return False

    with patch("app.premium_lock_store.latch_trigger", _refuse):
        res = _eval_with_ticks(db, _dep_from_db(db), _fresh_ticks(116.0, 111.0))

    assert res["outcome"] == "latch_refused"                       # tee skips (!= 'clean')
    assert len(db.signals.rows) == 1                               # journal kept (audit trail)
    assert db.signals.rows[0]["state"] == "CONFIRMED"
    # the real store was never latched by this pass
    assert db.premium_locks.docs[0].get("triggered_side") is None


# --- hard requirement (b): a BLOCKED journal must NOT burn the session --------

def test_blocked_signal_does_not_latch_the_trigger():
    db = _FakeDB()
    dep = seed_db(db, end_ts=TS_0931)
    _eval_with_ticks(db, dep, _fresh_ticks(100.0, 110.0))          # ref bar
    # trigger fires inside the 14:50 close-window block -> journaled as blocked
    db.candles_1m.rows.append({
        "instrument": "NIFTY", "ts": TS_1450,
        "open": 24000.0, "high": 24001.0, "low": 23999.0,
        "close": 24000.5, "volume": 2000,
    })
    db.ticks.rows = [{"instrument_key": CE_KEY, "ts": now_ms(), "last_price": 116.0}]
    res = _eval_with_ticks(db, _dep_from_db(db), _fresh_ticks(116.0, 111.0))
    assert res["outcome"] == "blocked"
    assert any("window_close_block" in b for b in res["blockers"])
    assert len(db.signals.rows) == 1
    # the session is NOT burned: latch stays empty for a (hypothetical) retry
    assert db.premium_locks.docs[0].get("triggered_side") is None
