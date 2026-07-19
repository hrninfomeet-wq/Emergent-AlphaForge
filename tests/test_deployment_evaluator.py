"""Tests for the 1-minute close deployment evaluator.

These tests use an in-memory async-mock Mongo collection and synthetic candle data
so they can run without Docker, MongoDB, or the Upstox broker.
"""
from __future__ import annotations

import asyncio
import importlib.util
import os
import sys
from datetime import datetime, time, timedelta, timezone
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock

import numpy as np
import pandas as pd
import pytest

# Tests importing app.live_deploy_context pull in motor via app.db — absent on
# the host. They run inside the backend container (DEVELOPER_GUIDE §B); on the
# host they SKIP instead of failing.
requires_motor = pytest.mark.skipif(
    importlib.util.find_spec("motor") is None,
    reason="imports motor-backed modules — runs in the backend container",
)


# Make backend/ importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from app.deployment_evaluator import (  # noqa: E402
    BLOCK_OPEN_UNTIL,
    BLOCK_CLOSE_FROM,
    compute_strategy_hash,
    evaluate_deployment_on_close,
    _is_blocked_by_window,
)
from app.signal_lifecycle import create_signal_doc  # noqa: E402
from app.strategies.base import StrategyBase, Signal, get_registry  # noqa: E402


IST_OFFSET = timedelta(hours=5, minutes=30)


def ist_to_epoch_ms(year: int, month: int, day: int, hh: int, mm: int) -> int:
    """Build an epoch-ms ts for a given IST minute."""
    ist_dt = datetime(year, month, day, hh, mm, 0, tzinfo=timezone(IST_OFFSET))
    return int(ist_dt.astimezone(timezone.utc).timestamp() * 1000)


def make_candles(n: int = 80, *, base_ist_hh: int = 11, base_ist_mm: int = 30) -> pd.DataFrame:
    """Build a synthetic NIFTY 1m candle frame with mild trend so indicators populate.

    Anchored at fixed IST time (default 11:30 -> last bar around 12:49) so window-block
    tests are deterministic. Tests that need 'recent option data' should insert the option
    candle with `ts = now_ms()` since `_has_recent_option_data` uses wall-clock time.
    """
    base_ts = ist_to_epoch_ms(2026, 5, 27, base_ist_hh, base_ist_mm)
    rows: List[Dict[str, Any]] = []
    price = 23900.0
    for i in range(n):
        ts = base_ts + i * 60_000
        ist_dt = datetime.fromtimestamp(ts / 1000, tz=timezone.utc) + IST_OFFSET
        price += 0.5  # tiny uptrend
        rows.append({
            "instrument": "NIFTY",
            "ts": ts,
            "open": price - 0.2,
            "high": price + 0.5,
            "low": price - 0.5,
            "close": price,
            "volume": 1000 + i,
            "datetime": ist_dt.replace(tzinfo=None).isoformat(),
            "ist_time": ist_dt.strftime("%H:%M"),
            "session_date": ist_dt.strftime("%Y-%m-%d"),
        })
    return pd.DataFrame(rows)


def now_ms() -> int:
    """Current wall-clock epoch ms — used by tests when seeding 'recent' option data."""
    return int(datetime.now(timezone.utc).timestamp() * 1000)


class FakeCursor:
    """Minimal Motor-cursor stand-in supporting the methods the evaluator uses."""

    def __init__(self, rows: List[Dict[str, Any]]):
        self._rows = list(rows)

    def sort(self, key: str, direction: int = 1):
        self._rows.sort(key=lambda r: r.get(key, 0), reverse=(direction == -1))
        return self

    def limit(self, n: int):
        self._rows = self._rows[: int(n)]
        return self

    async def to_list(self, length: Optional[int] = None):
        return list(self._rows if length is None else self._rows[: int(length)])


class FakeCollection:
    """In-memory async collection with the slice of the Motor API the evaluator touches."""

    def __init__(self):
        self.rows: List[Dict[str, Any]] = []

    def find(self, query: Optional[Dict[str, Any]] = None, projection: Optional[Dict[str, Any]] = None):
        rows = [r for r in self.rows if _matches(r, query or {})]
        # _id is stripped by projection — we never insert _id so this is a no-op for tests
        return FakeCursor(rows)

    async def find_one(self, query: Dict[str, Any], projection: Optional[Dict[str, Any]] = None):
        for r in self.rows:
            if _matches(r, query):
                return dict(r)
        return None

    async def insert_one(self, doc: Dict[str, Any]):
        self.rows.append(dict(doc))

    async def count_documents(self, query: Dict[str, Any]):
        return sum(1 for r in self.rows if _matches(r, query))

    async def distinct(self, key: str, query: Optional[Dict[str, Any]] = None):
        rows = [r for r in self.rows if _matches(r, query or {})]
        seen = []
        for r in rows:
            v = r.get(key)
            if v is not None and v not in seen:
                seen.append(v)
        return seen

    async def update_one(self, query: Dict[str, Any], update: Dict[str, Any], upsert: bool = False):
        for r in self.rows:
            if _matches(r, query):
                if "$set" in update:
                    r.update(update["$set"])
                if "$unset" in update:
                    for key in update["$unset"]:
                        r.pop(key, None)
                return MagicMock(matched_count=1, modified_count=1)
        if upsert:
            new = dict(query)
            if "$set" in update:
                new.update(update["$set"])
            self.rows.append(new)
        return MagicMock(matched_count=0, modified_count=0)

    async def replace_one(self, query: Dict[str, Any], replacement: Dict[str, Any], upsert: bool = False):
        for i, r in enumerate(self.rows):
            if _matches(r, query):
                self.rows[i] = dict(replacement)
                return MagicMock(matched_count=1, modified_count=1)
        if upsert:
            self.rows.append(dict(replacement))
        return MagicMock(matched_count=0, modified_count=0)


def _matches(row: Dict[str, Any], query: Dict[str, Any]) -> bool:
    for k, v in query.items():
        if isinstance(v, dict) and "$exists" in v:
            if bool(k in row) != bool(v["$exists"]):
                return False
        elif isinstance(v, dict) and "$gte" in v:
            row_val = row.get(k)
            if row_val is None:
                return False
            try:
                if row_val < v["$gte"]:
                    return False
            except TypeError:
                return False
        elif row.get(k) != v:
            return False
    return True


class FakeDB:
    """Stand-in for motor's AsyncIOMotorDatabase."""

    def __init__(self):
        self.candles_1m = FakeCollection()
        self.options_1m = FakeCollection()
        self.ticks = FakeCollection()  # live option premiums (LTPC); empty -> falls back to options_1m
        self.option_contracts = FakeCollection()
        self.signals = FakeCollection()
        self.strategy_deployments = FakeCollection()
        self.pretrade_profiles = FakeCollection()
        self.paper_trades = FakeCollection()
        self.live_trades = FakeCollection()


def seed_db(
    db: FakeDB,
    *,
    candles: pd.DataFrame,
    deployment: Dict[str, Any],
    contracts: Optional[List[Dict[str, Any]]] = None,
    profiles: Optional[List[Dict[str, Any]]] = None,
    option_candles: Optional[List[Dict[str, Any]]] = None,
) -> None:
    db.candles_1m.rows = candles.to_dict("records")
    db.strategy_deployments.rows = [dict(deployment)]
    db.option_contracts.rows = list(contracts or [])
    db.pretrade_profiles.rows = list(profiles or [])
    db.options_1m.rows = list(option_candles or [])


class StubStrategy(StrategyBase):
    """Programmable strategy: returns the Signal we hand it."""

    id = "stub_eval_strategy"
    name = "Stub Evaluator Strategy"
    version = "1.2.3"
    parameter_schema = {
        "ema_fast": {"default": 9, "type": "int"},
        "spot_target_pts": {"default": 30, "type": "float"},
        "spot_stop_pts": {"default": 15, "type": "float"},
        "cooldown_bars": {"default": 5, "type": "int"},
        "signal_threshold": {"default": 55, "type": "int"},
    }
    is_builtin = False

    _next_signal: Optional[Signal] = None

    @classmethod
    def set_next(cls, sig: Signal) -> None:
        cls._next_signal = sig

    def evaluate(self, row, prev, params, ctx):
        return self.__class__._next_signal or Signal(direction="NONE")


@pytest.fixture(autouse=True)
def register_stub_strategy():
    """Register the stub strategy in the global registry; clean up after each test."""
    registry = get_registry()
    stub = StubStrategy()
    registry.register(stub)
    StubStrategy._next_signal = None
    yield
    registry._strategies.pop(stub.id, None)


def make_deployment(
    *,
    moneyness: List[str] = None,
    pretrade_profile: str = "Balanced",
    last_evaluated_ts: int = 0,
) -> Dict[str, Any]:
    return {
        "id": "test-deploy-1",
        "name": "Test deployment",
        "source_type": "preset",
        "source_id": "test-preset",
        "strategy_id": StubStrategy.id,
        "params": {"ema_fast": 9, "signal_threshold": 55},
        "instrument": "NIFTY",
        "timeframe": "1m",
        "confirmation_mode": "1m_close",
        "option_policy": {"moneyness": moneyness or ["atm"], "expiry_policy": "next_available"},
        "pretrade_profile": pretrade_profile,
        "mode": "shadow",
        "manual_approval_required": True,
        "risk": {},
        "status": "ACTIVE",
        "last_evaluated_ts": last_evaluated_ts,
    }


def make_contracts(*, atm_strike: int = 23950, expiry_date: Optional[str] = None) -> List[Dict[str, Any]]:
    """Build NIFTY ATM/OTM1/ITM1 CE/PE contracts spaced by 50.

    The expiry defaults to a few days in the FUTURE relative to wall-clock now so
    the evaluator's active-expiry filter (expiry_date >= today IST) keeps these
    fixtures valid over time instead of going stale on a hard-coded date.
    """
    if expiry_date is None:
        future = datetime.now(timezone.utc) + IST_OFFSET + timedelta(days=3)
        expiry_date = future.strftime("%Y-%m-%d")
    expiry_compact = datetime.fromisoformat(expiry_date).strftime("%y%b").upper()
    contracts = []
    for offset in (-100, -50, 0, 50, 100):
        strike = atm_strike + offset
        for side in ("CE", "PE"):
            contracts.append({
                "underlying": "NIFTY",
                "strike": float(strike),
                "side": side,
                "expiry": expiry_date,
                "expiry_date": expiry_date,
                "instrument_key": f"NSE_FO|TEST|{strike}{side}",
                "trading_symbol": f"NIFTY{expiry_compact}{strike}{side}",
            })
    return contracts


def make_profile(name: str = "Balanced", min_score: int = 55) -> Dict[str, Any]:
    return {
        "name": name,
        "settings": {"min_confidence_score": min_score},
        "is_default": True,
    }


# ---------- pure helpers -------------------------------------------------------

def test_compute_strategy_hash_is_stable_and_param_sensitive():
    h1 = compute_strategy_hash("conf_scalper", "1.0.0", {"ema_fast": 9})
    h2 = compute_strategy_hash("conf_scalper", "1.0.0", {"ema_fast": 9})
    h3 = compute_strategy_hash("conf_scalper", "1.0.0", {"ema_fast": 10})
    h4 = compute_strategy_hash("conf_scalper", "1.0.1", {"ema_fast": 9})
    assert h1 == h2
    assert h1 != h3
    assert h1 != h4
    assert len(h1) == 16


def test_window_blocker_first_10_minutes():
    ts = ist_to_epoch_ms(2026, 5, 27, 9, 17)  # 09:17 IST -> in opening block
    reason = _is_blocked_by_window(ts)
    assert reason and "open" in reason


def test_window_blocker_last_30_minutes():
    ts = ist_to_epoch_ms(2026, 5, 27, 15, 0)  # 15:00 IST -> in closing block
    reason = _is_blocked_by_window(ts)
    assert reason and "close" in reason


def test_window_clear_midday():
    ts = ist_to_epoch_ms(2026, 5, 27, 11, 30)
    assert _is_blocked_by_window(ts) is None


# ---------- evaluator end-to-end ----------------------------------------------

@pytest.mark.asyncio
async def test_no_setup_does_not_journal_signal():
    db = FakeDB()
    candles = make_candles(n=80)
    seed_db(db, candles=candles, deployment=make_deployment(),
            contracts=make_contracts(), profiles=[make_profile()],
            option_candles=[])
    StubStrategy.set_next(Signal(direction="NONE"))

    res = await evaluate_deployment_on_close(db, db.strategy_deployments.rows[0])

    assert res["outcome"] == "no_setup"
    assert len(db.signals.rows) == 0
    # last_evaluated_ts must advance even on no-setup so we don't re-evaluate the same bar
    assert db.strategy_deployments.rows[0]["last_evaluated_ts"] == int(candles["ts"].iloc[-1])


@pytest.mark.asyncio
async def test_clean_signal_journals_with_audit_context():
    db = FakeDB()
    candles = make_candles(n=80)  # last bar is around 12:49 IST -> midday, no window block
    contracts = make_contracts(atm_strike=23950)
    seed_db(db, candles=candles, deployment=make_deployment(),
            contracts=contracts, profiles=[make_profile(min_score=50)])
    # Provide a recent option candle for the resolved ATM CE so option_no_data is False
    last_close = float(candles["close"].iloc[-1])
    last_ts = int(candles["ts"].iloc[-1])
    # Strike step 50 -> ATM round of close
    atm_strike = round(last_close / 50) * 50
    db.options_1m.rows.append({
        "instrument_key": f"NSE_FO|TEST|{atm_strike}CE",
        "ts": now_ms(),
    })

    StubStrategy.set_next(Signal(direction="CE", score=80, reasons=["ema_cross", "vol_spike"]))

    res = await evaluate_deployment_on_close(db, db.strategy_deployments.rows[0])

    assert res["outcome"] == "clean", f"unexpected outcome: {res}"
    assert res["direction"] == "CE"
    assert res["tracked_for_pnl"] is True
    assert len(db.signals.rows) == 1
    sig = db.signals.rows[0]
    assert sig["state"] == "CONFIRMED"
    assert sig["blocked"] is False
    assert sig["context"]["strategy_hash"]
    assert sig["context"]["strategy_version"] == "1.2.3"
    assert sig["context"]["pretrade_profile_name"] == "Balanced"
    assert sig["context"]["candle"]["close"] == last_close


@pytest.mark.asyncio
async def test_blocked_when_score_below_pretrade_min():
    db = FakeDB()
    candles = make_candles(n=80)
    seed_db(db, candles=candles, deployment=make_deployment(),
            contracts=make_contracts(), profiles=[make_profile(min_score=70)])
    StubStrategy.set_next(Signal(direction="CE", score=40, reasons=["weak_setup"]))

    res = await evaluate_deployment_on_close(db, db.strategy_deployments.rows[0])

    assert res["outcome"] == "blocked"
    assert any("pretrade_min_score" in b for b in res["blockers"])
    sig = db.signals.rows[0]
    assert sig["state"] == "AUDITED"
    assert sig["blocked"] is True


@pytest.mark.asyncio
async def test_blocked_in_opening_window():
    db = FakeDB()
    candles = make_candles(n=80, base_ist_hh=8, base_ist_mm=0)  # last bar ~ 09:19 IST
    seed_db(db, candles=candles, deployment=make_deployment(),
            contracts=make_contracts(), profiles=[make_profile(min_score=50)])
    StubStrategy.set_next(Signal(direction="CE", score=80, reasons=["ok"]))

    res = await evaluate_deployment_on_close(db, db.strategy_deployments.rows[0])

    assert res["outcome"] == "blocked"
    assert any("window_open" in b for b in res["blockers"])


@pytest.mark.asyncio
async def test_no_recent_option_data_blocks_with_specific_reason():
    db = FakeDB()
    candles = make_candles(n=80)
    seed_db(db, candles=candles, deployment=make_deployment(),
            contracts=make_contracts(), profiles=[make_profile(min_score=50)],
            option_candles=[])  # zero option candles -> no_data
    StubStrategy.set_next(Signal(direction="CE", score=80, reasons=["ok"]))

    res = await evaluate_deployment_on_close(db, db.strategy_deployments.rows[0])

    assert res["outcome"] == "blocked"
    assert any("option_no_data" in b for b in res["blockers"])
    sig = db.signals.rows[0]
    assert sig["context"].get("option_no_data") is True
    assert sig["context"].get("tracked_for_pnl") is False


@pytest.mark.asyncio
async def test_missing_contract_metadata_blocks():
    db = FakeDB()
    candles = make_candles(n=80)
    seed_db(db, candles=candles, deployment=make_deployment(),
            contracts=[], profiles=[make_profile(min_score=50)])
    StubStrategy.set_next(Signal(direction="CE", score=80, reasons=["ok"]))

    res = await evaluate_deployment_on_close(db, db.strategy_deployments.rows[0])

    assert res["outcome"] == "blocked"
    assert any("option_contract_metadata_missing" in b for b in res["blockers"])


@pytest.mark.asyncio
async def test_idempotency_skips_already_evaluated_bar():
    db = FakeDB()
    candles = make_candles(n=80)
    last_ts = int(candles["ts"].iloc[-1])
    deployment = make_deployment(last_evaluated_ts=last_ts)
    seed_db(db, candles=candles, deployment=deployment,
            contracts=make_contracts(), profiles=[make_profile()])
    StubStrategy.set_next(Signal(direction="CE", score=80))

    res = await evaluate_deployment_on_close(db, db.strategy_deployments.rows[0])
    assert res["outcome"] == "skipped"
    assert "already_evaluated" in res["reason"]
    assert len(db.signals.rows) == 0


@pytest.mark.asyncio
async def test_inactive_deployment_is_skipped():
    db = FakeDB()
    candles = make_candles(n=80)
    deployment = make_deployment()
    deployment["status"] = "PAUSED"
    seed_db(db, candles=candles, deployment=deployment,
            contracts=make_contracts(), profiles=[make_profile()])
    StubStrategy.set_next(Signal(direction="CE", score=80))

    res = await evaluate_deployment_on_close(db, db.strategy_deployments.rows[0])
    assert res["outcome"] == "skipped"
    assert res["reason"] == "deployment_not_active"


@pytest.mark.asyncio
async def test_otm1_strike_step_aware():
    """OTM1 for a CE signal must pick the strike one step (50 pts) above ATM, from option_contracts."""
    db = FakeDB()
    candles = make_candles(n=80)
    last_close = float(candles["close"].iloc[-1])
    atm_strike = round(last_close / 50) * 50
    contracts = make_contracts(atm_strike=atm_strike)
    deployment = make_deployment(moneyness=["otm1"])
    seed_db(db, candles=candles, deployment=deployment,
            contracts=contracts, profiles=[make_profile(min_score=50)])
    # Provide recent option data for the OTM1 CE strike (atm + 50)
    db.options_1m.rows.append({
        "instrument_key": f"NSE_FO|TEST|{atm_strike + 50}CE",
        "ts": now_ms(),
    })
    StubStrategy.set_next(Signal(direction="CE", score=80))

    res = await evaluate_deployment_on_close(db, db.strategy_deployments.rows[0])

    assert res["outcome"] == "clean", res
    sig = db.signals.rows[0]
    chosen = sig["option_contract"]
    assert chosen["side"] == "CE"
    assert int(chosen["strike"]) == atm_strike + 50  # one step above ATM


# ---------- expiry-day cutoff -------------------------------------------------

from app.deployment_evaluator import _is_blocked_by_expiry_day_cutoff  # noqa: E402


def test_expiry_cutoff_no_expiry_returns_none():
    """If we cannot resolve a next expiry, never block on expiry-day rule."""
    ist_dt = datetime(2026, 5, 27, 15, 30, tzinfo=IST_OFFSET_TZ if False else timezone(IST_OFFSET))
    assert _is_blocked_by_expiry_day_cutoff(ist_dt, None) is None


def test_expiry_cutoff_today_not_expiry_returns_none():
    """Today is not the next expiry date -> no block, even past 15:00."""
    ist_dt = datetime(2026, 5, 27, 15, 30, tzinfo=timezone(IST_OFFSET))
    assert _is_blocked_by_expiry_day_cutoff(ist_dt, "2026-06-02") is None


def test_expiry_cutoff_today_is_expiry_before_cutoff_returns_none():
    """Today IS the expiry day, but it's only 14:55 -> not yet blocked."""
    ist_dt = datetime(2026, 5, 27, 14, 55, tzinfo=timezone(IST_OFFSET))
    assert _is_blocked_by_expiry_day_cutoff(ist_dt, "2026-05-27") is None


def test_expiry_cutoff_today_is_expiry_at_cutoff_returns_block():
    """Today IS the expiry day and clock has hit 15:00 -> blocked."""
    ist_dt = datetime(2026, 5, 27, 15, 0, tzinfo=timezone(IST_OFFSET))
    reason = _is_blocked_by_expiry_day_cutoff(ist_dt, "2026-05-27")
    assert reason and "expiry_day_cutoff" in reason and "2026-05-27" in reason


def test_expiry_cutoff_today_is_expiry_after_cutoff_returns_block():
    """Today IS expiry, 15:25 IST -> blocked."""
    ist_dt = datetime(2026, 5, 27, 15, 25, tzinfo=timezone(IST_OFFSET))
    reason = _is_blocked_by_expiry_day_cutoff(ist_dt, "2026-05-27")
    assert reason and "expiry_day_cutoff" in reason


# ---------- idempotency: duplicate-key handling (slice 11) -------------------


@pytest.mark.asyncio
async def test_evaluator_handles_duplicate_key_as_skipped():
    """If signals.insert_one raises a duplicate-key error (live unique index on
    (deployment_id, candle_ts) catching a race), the evaluator must treat it as
    'already_journaled' instead of crashing.
    """
    db = FakeDB()
    candles = make_candles(n=80)
    seed_db(db, candles=candles, deployment=make_deployment(),
            contracts=make_contracts(), profiles=[make_profile(min_score=50)])
    last_ts = int(candles["ts"].iloc[-1])
    db.options_1m.rows.append({
        "instrument_key": f"NSE_FO|TEST|{round(float(candles['close'].iloc[-1]) / 50) * 50}CE",
        "ts": now_ms(),
    })
    StubStrategy.set_next(Signal(direction="CE", score=80, reasons=["ok"]))

    # Monkey-patch the signals collection's insert_one to raise duplicate-key
    original = db.signals.insert_one
    async def raise_dup(doc):
        raise RuntimeError("E11000 duplicate key error: dup on signals_deployment_bar_unique")
    db.signals.insert_one = raise_dup  # type: ignore

    res = await evaluate_deployment_on_close(db, db.strategy_deployments.rows[0])

    assert res["outcome"] == "skipped"
    assert "already_journaled" in res["reason"]
    # last_evaluated_ts must still advance so we don't keep retrying
    assert db.strategy_deployments.rows[0]["last_evaluated_ts"] == last_ts

    # restore
    db.signals.insert_one = original  # type: ignore


# ---------- auto paper trading on clean signals (2026-06-10) -------------------

from app.deployment_evaluator import evaluate_active_deployments  # noqa: E402


def _seed_clean_signal_setup(db: FakeDB, deployment: Dict[str, Any]) -> str:
    """Seed candles/contracts/profile + a fresh option candle so the evaluator
    produces a CLEAN signal. Returns the ATM CE instrument_key."""
    candles = make_candles(n=80)
    seed_db(db, candles=candles, deployment=deployment,
            contracts=make_contracts(), profiles=[make_profile(min_score=50)])
    atm_strike = round(float(candles["close"].iloc[-1]) / 50) * 50
    key = f"NSE_FO|TEST|{atm_strike}CE"
    db.options_1m.rows.append({"instrument_key": key, "ts": now_ms(), "close": 142.0})
    return key


@pytest.mark.asyncio
async def test_auto_paper_creates_trade_at_option_premium_on_clean_signal():
    db = FakeDB()
    deployment = make_deployment()
    deployment["mode"] = "paper"
    deployment["risk"] = {"auto_paper": True, "default_lots": 1}
    key = _seed_clean_signal_setup(db, deployment)
    StubStrategy.set_next(Signal(direction="CE", score=80, reasons=["ok"],
                                 target_pct=40, stop_pct=30))

    results = await evaluate_active_deployments(
        db, latest_tick_lookup={key: {"last_price": 150.0}}.get)

    assert results[0]["outcome"] == "clean"
    assert results[0]["auto_paper"]["created"] is True
    assert len(db.paper_trades.rows) == 1
    trade = db.paper_trades.rows[0]
    # Entry MUST be the option premium from the live tick, never the spot close.
    assert trade["entry_price"] == 150.0
    assert trade["source"] == "paper_auto_on_signal"
    # Strategy risk hints define the exits (shared decision engine).
    assert trade["risk"]["target_price"] == round(150.0 * 1.4, 2)
    assert trade["risk"]["stop_price"] == round(150.0 * 0.7, 2)
    # Signal advanced past approval and linked to the trade.
    sig = db.signals.rows[0]
    assert sig["state"] == "ACTIVE"
    assert sig["paper_trade_id"] == trade["id"]
    assert sig["risk_hints"]["target_pct"] == 40


@pytest.mark.asyncio
async def test_auto_paper_not_triggered_for_shadow_deployment():
    db = FakeDB()
    deployment = make_deployment()  # mode stays "shadow"
    deployment["risk"] = {"auto_paper": True, "default_lots": 1}
    key = _seed_clean_signal_setup(db, deployment)
    StubStrategy.set_next(Signal(direction="CE", score=80, reasons=["ok"]))

    results = await evaluate_active_deployments(
        db, latest_tick_lookup={key: {"last_price": 150.0}}.get)

    assert results[0]["outcome"] == "clean"
    assert "auto_paper" not in results[0]
    assert len(db.paper_trades.rows) == 0
    assert db.signals.rows[0]["state"] == "CONFIRMED"  # still awaiting manual approval


@pytest.mark.asyncio
async def test_auto_paper_falls_back_to_option_candle_without_tick():
    db = FakeDB()
    deployment = make_deployment()
    deployment["mode"] = "paper"
    deployment["risk"] = {"auto_paper": True, "default_lots": 1}
    _seed_clean_signal_setup(db, deployment)  # fresh candle close = 142.0
    StubStrategy.set_next(Signal(direction="CE", score=80, reasons=["ok"]))

    results = await evaluate_active_deployments(db, latest_tick_lookup=None)

    assert results[0]["auto_paper"]["created"] is True
    assert db.paper_trades.rows[0]["entry_price"] == 142.0  # options_1m close fallback


@pytest.mark.asyncio
async def test_auto_paper_blocked_by_max_open_paper_trades_kill_switch():
    db = FakeDB()
    deployment = make_deployment()
    deployment["mode"] = "paper"
    deployment["risk"] = {"auto_paper": True, "default_lots": 1, "max_open_paper_trades": 1}
    key = _seed_clean_signal_setup(db, deployment)
    # One trade already OPEN for this deployment -> soft block on new signals.
    db.paper_trades.rows.append({
        "id": "existing-open", "deployment_id": deployment["id"], "status": "OPEN",
        "instrument_key": "", "quantity": 75, "entry_price": 100.0,
    })
    StubStrategy.set_next(Signal(direction="CE", score=80, reasons=["ok"]))

    results = await evaluate_active_deployments(
        db, latest_tick_lookup={key: {"last_price": 150.0}}.get)

    # The kill switch makes the signal blocked, so no second trade may open.
    assert results[0]["outcome"] == "blocked"
    assert len([t for t in db.paper_trades.rows if t.get("source") == "paper_auto_on_signal"]) == 0


# ---------- strategy source drift (slice 8) ----------------------------------


@pytest.mark.asyncio
async def test_evaluator_auto_pauses_on_strategy_source_drift(monkeypatch):
    """When the deployment's pinned source SHA does not match the current file
    SHA, the evaluator auto-pauses the deployment and journals the drift event.
    """
    from app import strategy_source_hash as ssh

    db = FakeDB()
    candles = make_candles(n=80)
    deployment = make_deployment()
    deployment["strategy_source_sha"] = "PINNED_SHA_OLD"
    deployment["mode"] = "live"   # a LIVE deployment that drifts (v0.56.0 pin)
    seed_db(db, candles=candles, deployment=deployment,
            contracts=make_contracts(), profiles=[make_profile(min_score=50)])

    # Force the current source hash to differ from the pinned one
    monkeypatch.setattr(ssh, "hash_strategy_source", lambda obj: "CURRENT_SHA_NEW")

    StubStrategy.set_next(Signal(direction="CE", score=80, reasons=["should not fire"]))

    res = await evaluate_deployment_on_close(db, db.strategy_deployments.rows[0])

    assert res["outcome"] == "skipped"
    assert "strategy_source_drift" in res["reason"]
    assert res["drift_pinned_sha"] == "PINNED_SHA_OLD"
    assert res["drift_current_sha"] == "CURRENT_SHA_NEW"
    # Deployment must be auto-paused
    updated = db.strategy_deployments.rows[0]
    assert updated["status"] == "PAUSED"
    assert updated["drift_reason"] == "strategy_source_drift"
    # v0.56.0 invariant: a live deployment that drifts is demoted to paper, so a
    # subsequent re-pin (status→ACTIVE) cannot silently resume REAL trading against
    # the changed, never-live-validated code that triggered the drift.
    assert updated["mode"] == "paper"
    # No signal should have been journaled
    assert len(db.signals.rows) == 0


@pytest.mark.asyncio
async def test_evaluator_does_not_pause_when_source_sha_matches(monkeypatch):
    """When pinned and current SHAs match, evaluation proceeds normally."""
    from app import strategy_source_hash as ssh

    db = FakeDB()
    candles = make_candles(n=80)
    deployment = make_deployment()
    deployment["strategy_source_sha"] = "MATCHING_SHA"
    seed_db(db, candles=candles, deployment=deployment,
            contracts=make_contracts(), profiles=[make_profile(min_score=50)])
    last_close = float(candles["close"].iloc[-1])
    atm_strike = round(last_close / 50) * 50
    db.options_1m.rows.append({
        "instrument_key": f"NSE_FO|TEST|{atm_strike}CE",
        "ts": now_ms(),
    })
    monkeypatch.setattr(ssh, "hash_strategy_source", lambda obj: "MATCHING_SHA")

    StubStrategy.set_next(Signal(direction="CE", score=80, reasons=["should fire"]))

    res = await evaluate_deployment_on_close(db, db.strategy_deployments.rows[0])

    assert res["outcome"] == "clean"
    assert "drift" not in (res.get("reason") or "")
    assert db.strategy_deployments.rows[0]["status"] == "ACTIVE"


@pytest.mark.asyncio
async def test_evaluator_no_pinned_sha_means_no_drift_check(monkeypatch):
    """Deployments created before slice 8 have no pinned SHA. They keep working."""
    from app import strategy_source_hash as ssh

    db = FakeDB()
    candles = make_candles(n=80)
    deployment = make_deployment()
    # NOT setting strategy_source_sha - simulates a pre-slice-8 deployment
    seed_db(db, candles=candles, deployment=deployment,
            contracts=make_contracts(), profiles=[make_profile(min_score=50)])
    last_close = float(candles["close"].iloc[-1])
    atm_strike = round(last_close / 50) * 50
    db.options_1m.rows.append({
        "instrument_key": f"NSE_FO|TEST|{atm_strike}CE",
        "ts": now_ms(),
    })
    # Even if hash_strategy_source would return something different, the absence
    # of a pinned SHA on the deployment means we never compare and never pause.
    monkeypatch.setattr(ssh, "hash_strategy_source", lambda obj: "DOESNT_MATTER")

    StubStrategy.set_next(Signal(direction="CE", score=80, reasons=["should fire"]))

    res = await evaluate_deployment_on_close(db, db.strategy_deployments.rows[0])

    assert res["outcome"] == "clean"
    assert db.strategy_deployments.rows[0]["status"] == "ACTIVE"


# ---------- kill switches (Slice 12) ------------------------------------------

@pytest.mark.asyncio
async def test_kill_switch_pauses_paper_deployment_on_consecutive_losses():
    """A paper deployment that has hit max_consecutive_losses must auto-pause
    before generating any new signal."""
    db = FakeDB()
    candles = make_candles(n=80)
    deployment = make_deployment()
    deployment["mode"] = "paper"
    deployment["risk"] = {"max_consecutive_losses": 2}
    seed_db(db, candles=candles, deployment=deployment,
            contracts=make_contracts(), profiles=[make_profile()])
    # Two trailing losing closed paper trades for this deployment.
    db.paper_trades.rows = [
        {"deployment_id": "test-deploy-1", "status": "CLOSED", "realized_pnl": -10,
         "closed_at": "2026-05-30T05:00:00+00:00", "entry_value": 1000},
        {"deployment_id": "test-deploy-1", "status": "CLOSED", "realized_pnl": -25,
         "closed_at": "2026-05-31T05:00:00+00:00", "entry_value": 1000},
    ]
    StubStrategy.set_next(Signal(direction="CE", score=80))

    res = await evaluate_deployment_on_close(db, deployment)

    assert res["outcome"] == "skipped"
    assert res.get("kill_switch") == "max_consecutive_losses"
    # Deployment auto-paused; no signal journaled.
    assert db.strategy_deployments.rows[0]["status"] == "PAUSED"
    assert len(db.signals.rows) == 0


@pytest.mark.asyncio
async def test_kill_switch_blocks_signal_on_max_open_without_pausing():
    """max_open_paper_trades is a soft block: the signal is journaled as blocked
    but the deployment stays ACTIVE so it self-clears as trades close."""
    db = FakeDB()
    candles = make_candles(n=80)
    deployment = make_deployment()
    deployment["mode"] = "paper"
    deployment["risk"] = {"max_open_paper_trades": 1}
    seed_db(db, candles=candles, deployment=deployment,
            contracts=make_contracts(), profiles=[make_profile()])
    db.paper_trades.rows = [
        {"deployment_id": "test-deploy-1", "status": "OPEN"},
    ]
    # Seed recent option data so the signal would otherwise be clean.
    last_ts = int(candles.iloc[-1]["ts"])
    db.options_1m.rows = [{"instrument_key": "NSE_FO|TEST|23950CE", "ts": now_ms()}]
    StubStrategy.set_next(Signal(direction="CE", score=80))

    res = await evaluate_deployment_on_close(db, deployment)

    assert res["outcome"] == "blocked"
    assert any("max_open_paper_trades" in b for b in res.get("blockers", []))
    # Still ACTIVE — soft block only.
    assert db.strategy_deployments.rows[0]["status"] == "ACTIVE"


# ---------- continuous live tee (armed -> live replace, else paper) ----------
#
# An ARMED deployment routes its confirmed signal to auto_live AND suppresses
# the paper path (if/elif). A non-armed paper deployment still papers, unchanged.
# When no live context is available (broker not connected) the armed deployment
# falls through to auto_paper (or nothing).

# armed_until 15:00 IST. The clean signal's bar is anchored at the make_candles
# default (last bar ~12:49 IST on 2026-05-27). We force `now` inside the window.
_TEE_NOW = datetime(2026, 5, 27, 6, 0, tzinfo=timezone.utc)              # ~11:30 IST
_TEE_ARMED_UNTIL = "2026-05-27T09:30:00+00:00"                          # 15:00 IST


def _fresh_tick(price: float) -> Dict[str, Any]:
    return {"last_price": price, "ts": _TEE_NOW.timestamp()}


def _armed_live_deployment(*, lots: int = 2) -> Dict[str, Any]:
    """A paper-mode deployment ARMED for live within the window, with auto_paper
    also on. When connected, the live sink wins and SUPPRESSES paper (the if/elif).
    When NOT connected, auto_live is disabled and it falls through to auto_paper —
    which requires mode=="paper", so the fall-through path is exercisable here."""
    dep = make_deployment()
    dep["mode"] = "paper"
    dep["risk"] = {
        "auto_paper": True,                      # would paper if live did not win
        "default_lots": 1,
        "live": {"armed": True, "armed_until": _TEE_ARMED_UNTIL, "lots": lots},
    }
    return dep


_LIVE_SUCCESS = {"placed": True, "protected": True, "norenordno": "ZZZ",
                 "cid": "c9", "verdicts": []}


def _make_place_fn(result: Dict[str, Any], calls: List[Dict[str, Any]]):
    async def _place(contract, **kwargs):
        calls.append({"contract": contract, **kwargs})
        return dict(result)
    return _place


def _fake_arm_for(plan, signal_doc, ref_ltp, **kwargs):
    # Mirrors the real arm_for signature: auto_live forwards the per-deployment
    # catastrophe pct as keyword args (catastrophe_stop_pct/target_pct).
    return MagicMock(name="arm_callable")


def _live_ctx(*, place_fn, connected: bool = True) -> Dict[str, Any]:
    """An injectable live context for the tee (no real broker)."""
    return {
        "connected": connected,
        "place_fn": place_fn,
        "arm_for": _fake_arm_for,
        "client": MagicMock(name="client"),
        "intent_store": MagicMock(name="intent_store"),
        "engine": MagicMock(name="engine"),
        "search_fn": (lambda exch, q: []),
        "throttle": MagicMock(name="throttle"),
        "account_max": 20,
        "band_pct": 5.0,
        "uid": "U1",
        "actid": "A1",
    }


@pytest.mark.asyncio
async def test_tee_armed_deployment_routes_to_live_and_suppresses_paper():
    """mode == "live" IS the authorization now (the per-session arm ceremony is
    gone — is_deployment_live_allowed no longer reads risk.live.armed). Override
    the shared paper-mode fixture to mode="live" with realistic caps (check_live_caps
    fails closed for a live deployment with none configured) so this signal routes
    to auto_live. auto_paper_enabled hard-requires mode=="paper", so the paper leg
    of the if/elif can never fire for the same deployment — suppression is now
    structural, not just a runtime race the if/elif happens to win."""
    db = FakeDB()
    deployment = _armed_live_deployment(lots=2)
    deployment["mode"] = "live"
    deployment["risk"]["live"]["max_concurrent"] = 5
    deployment["risk"]["live"]["max_lots_per_day"] = 100
    key = _seed_clean_signal_setup(db, deployment)
    StubStrategy.set_next(Signal(direction="CE", score=80, reasons=["ok"],
                                 target_pct=40, stop_pct=30))
    calls: List[Dict[str, Any]] = []

    results = await evaluate_active_deployments(
        db,
        latest_tick_lookup={key: _fresh_tick(150.0)}.get,
        live_ctx=_live_ctx(place_fn=_make_place_fn(_LIVE_SUCCESS, calls)),
        now_utc=_TEE_NOW,
    )

    assert results[0]["outcome"] == "clean"
    # LIVE ran ...
    assert results[0]["auto_live"]["created"] is True
    assert results[0]["auto_live"]["norenordno"] == "ZZZ"
    assert len(db.live_trades.rows) == 1
    assert len(calls) == 1                       # the executor place_fn was called
    # ... and PAPER was SUPPRESSED for this signal.
    assert "auto_paper" not in results[0]
    assert len(db.paper_trades.rows) == 0
    # signal advanced to ACTIVE via the live path, linked to the live trade.
    sig = db.signals.rows[0]
    assert sig["state"] == "ACTIVE"
    assert sig["live_trade_id"] == db.live_trades.rows[0]["id"]


@pytest.mark.asyncio
async def test_tee_non_armed_paper_deployment_still_papers():
    db = FakeDB()
    deployment = make_deployment()
    deployment["mode"] = "paper"
    deployment["risk"] = {"auto_paper": True, "default_lots": 1}   # no risk.live
    key = _seed_clean_signal_setup(db, deployment)
    StubStrategy.set_next(Signal(direction="CE", score=80, reasons=["ok"]))
    calls: List[Dict[str, Any]] = []

    results = await evaluate_active_deployments(
        db,
        latest_tick_lookup={key: {"last_price": 150.0}}.get,
        live_ctx=_live_ctx(place_fn=_make_place_fn(_LIVE_SUCCESS, calls)),
        now_utc=_TEE_NOW,
    )

    assert results[0]["outcome"] == "clean"
    assert "auto_live" not in results[0]
    assert results[0]["auto_paper"]["created"] is True
    assert len(db.paper_trades.rows) == 1
    assert len(db.live_trades.rows) == 0
    assert len(calls) == 0                       # live place_fn never called


@pytest.mark.asyncio
async def test_tee_armed_but_not_connected_falls_through_to_paper():
    """When live_ctx says connected=False, the armed deployment does NOT live-trade;
    with auto_paper on it falls through and papers instead."""
    db = FakeDB()
    deployment = _armed_live_deployment(lots=2)
    key = _seed_clean_signal_setup(db, deployment)
    StubStrategy.set_next(Signal(direction="CE", score=80, reasons=["ok"]))
    calls: List[Dict[str, Any]] = []

    results = await evaluate_active_deployments(
        db,
        latest_tick_lookup={key: {"last_price": 150.0}}.get,
        live_ctx=_live_ctx(place_fn=_make_place_fn(_LIVE_SUCCESS, calls),
                           connected=False),
        now_utc=_TEE_NOW,
    )

    assert results[0]["outcome"] == "clean"
    assert "auto_live" not in results[0]
    assert results[0]["auto_paper"]["created"] is True
    assert len(db.live_trades.rows) == 0
    assert len(calls) == 0


@pytest.mark.asyncio
@requires_motor
async def test_tee_no_live_ctx_does_not_crash_and_papers(monkeypatch):
    """live_ctx=None (production lazy-build) → build_live_deploy_context returns
    None when the broker is unconfigured; the armed deployment must not crash and
    falls through to auto_paper. We patch the builder to return None (exactly what
    it does unconnected) so the test never touches the real broker/DB."""
    import app.live_deploy_context as _ldc

    async def _none(_db):
        return None
    monkeypatch.setattr(_ldc, "build_live_deploy_context", _none)

    db = FakeDB()
    deployment = _armed_live_deployment(lots=2)
    key = _seed_clean_signal_setup(db, deployment)
    StubStrategy.set_next(Signal(direction="CE", score=80, reasons=["ok"]))

    # No live_ctx injected → the lazy builder runs (patched to None). Must not raise.
    results = await evaluate_active_deployments(
        db,
        latest_tick_lookup={key: {"last_price": 150.0}}.get,
        now_utc=_TEE_NOW,
    )

    assert results[0]["outcome"] == "clean"
    assert "auto_live" not in results[0]
    assert results[0]["auto_paper"]["created"] is True
    assert len(db.live_trades.rows) == 0


@pytest.mark.asyncio
async def test_tee_blocked_signal_takes_neither_path():
    """A blocked (non-CONFIRMED) signal must trigger neither live nor paper."""
    db = FakeDB()
    deployment = _armed_live_deployment(lots=2)
    # Seed candles/contracts but NO recent option data → option_no_data block.
    candles = make_candles(n=80)
    seed_db(db, candles=candles, deployment=deployment,
            contracts=make_contracts(), profiles=[make_profile(min_score=50)],
            option_candles=[])
    StubStrategy.set_next(Signal(direction="CE", score=80, reasons=["ok"]))
    calls: List[Dict[str, Any]] = []

    results = await evaluate_active_deployments(
        db,
        latest_tick_lookup={"NSE_FO|TEST|23950CE": {"last_price": 150.0}}.get,
        live_ctx=_live_ctx(place_fn=_make_place_fn(_LIVE_SUCCESS, calls)),
        now_utc=_TEE_NOW,
    )

    assert results[0]["outcome"] == "blocked"
    assert "auto_live" not in results[0]
    assert "auto_paper" not in results[0]
    assert len(db.live_trades.rows) == 0
    assert len(db.paper_trades.rows) == 0
    assert len(calls) == 0
