"""Tests for the honest paper capital constraint (app/paper_capital.py).

Trading-critical invariants covered here:
- a paper deployment configured with fixed capital C can never hold concurrent
  premium whose required_capital (paper_analytics.deployment_period_stats)
  exceeds C in ANY bucket granularity — losses debit availability, profits
  never credit it (no compounding);
- cumulative basis credits profits and debits losses (capital + realized P&L);
- a blocked entry is skipped AND journaled (paper_trade_skip on the signal,
  claim released, state left CONFIRMED) — never a silent drop;
- deployments without a capital config keep today's unconstrained behavior;
- the opt-in account-wide ceiling gates the SUM across deployments.
"""
from __future__ import annotations

import asyncio
import os
import sys
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from app.paper_analytics import deployment_period_stats  # noqa: E402
from app.paper_auto import auto_paper_trade_for_signal  # noqa: E402
from app.paper_capital import (  # noqa: E402
    check_capital_gate,
    committed_exposure,
    evaluate_capital_gate,
    parse_capital_config,
    realized_windows,
)
from app.signal_lifecycle import create_signal_doc, transition_signal  # noqa: E402


# ---------- minimal in-memory Mongo stand-in (test_paper_auto idiom) ------------

class FakeCursor:
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


def _matches(row: Dict[str, Any], query: Dict[str, Any]) -> bool:
    for k, v in query.items():
        if isinstance(v, dict) and "$exists" in v:
            if bool(k in row) != bool(v["$exists"]):
                return False
        elif isinstance(v, dict) and "$gte" in v:
            rv = row.get(k)
            if rv is None or rv < v["$gte"]:
                return False
        elif row.get(k) != v:
            return False
    return True


class FakeCollection:
    def __init__(self):
        self.rows: List[Dict[str, Any]] = []

    def find(self, query=None, projection=None):
        return FakeCursor([dict(r) for r in self.rows if _matches(r, query or {})])

    async def find_one(self, query, projection=None):
        for r in self.rows:
            if _matches(r, query):
                return dict(r)
        return None

    async def insert_one(self, doc):
        self.rows.append(dict(doc))

    async def update_one(self, query, update, upsert=False):
        for r in self.rows:
            if _matches(r, query):
                if "$set" in update:
                    r.update(update["$set"])
                if "$unset" in update:
                    for key in update["$unset"]:
                        r.pop(key, None)
                return MagicMock(matched_count=1)
        if upsert and "$set" in update:
            self.rows.append(dict(update["$set"]))
            return MagicMock(matched_count=0)
        return MagicMock(matched_count=0)

    async def replace_one(self, query, replacement, upsert=False):
        for i, r in enumerate(self.rows):
            if _matches(r, query):
                self.rows[i] = dict(replacement)
                return MagicMock(matched_count=1)
        return MagicMock(matched_count=0)

    async def count_documents(self, query):
        return len([r for r in self.rows if _matches(r, query)])


class FakeDB:
    def __init__(self):
        self.options_1m = FakeCollection()
        self.signals = FakeCollection()
        self.paper_trades = FakeCollection()
        self.app_settings = FakeCollection()


KEY = "NSE_FO|TEST|23950CE"


def make_confirmed_signal(*, lot_size: int = 75, dep_id: str = "dep-1") -> Dict[str, Any]:
    doc = create_signal_doc(
        instrument="NIFTY",
        direction="CE",
        strategy_id="stub",
        entry_price=23950.0,
        confidence=80,
        reasons=["test"],
        option_contract={
            "instrument_key": KEY,
            "trading_symbol": "NIFTYTEST23950CE",
            "lot_size": lot_size,
            "strike": 23950.0,
            "side": "CE",
        },
        context={},
    )
    doc = transition_signal(doc, "FORMING", reason="test")
    doc = transition_signal(doc, "CONFIRMED", reason="test")
    doc["deployment_id"] = dep_id
    doc["blocked"] = False
    return doc


def make_paper_deployment(dep_id: str = "dep-1", **risk) -> Dict[str, Any]:
    return {"id": dep_id, "mode": "paper",
            "risk": {"auto_paper": True, "default_lots": 1, **risk}}


def tick(premium: float):
    return {KEY: {"last_price": premium}}.get


def _iso(dt: datetime) -> str:
    return dt.isoformat()


# ---------- parse_capital_config -------------------------------------------------

def test_parse_capital_config_valid_and_basis_default():
    assert parse_capital_config({"amount": 200000}) == {"amount": 200000.0, "basis": "fixed"}
    assert parse_capital_config({"amount": 1.5e5, "basis": "CUMULATIVE"}) == \
        {"amount": 150000.0, "basis": "cumulative"}
    assert parse_capital_config({"amount": 100, "basis": "bogus"})["basis"] == "fixed"


def test_parse_capital_config_rejects_garbage():
    assert parse_capital_config(None) is None
    assert parse_capital_config("200000") is None
    assert parse_capital_config({}) is None
    assert parse_capital_config({"amount": "abc"}) is None
    assert parse_capital_config({"amount": 0}) is None
    assert parse_capital_config({"amount": -5}) is None


# ---------- pure arithmetic -------------------------------------------------------

def test_committed_exposure_counts_open_only_unrounded():
    trades = [
        {"status": "OPEN", "entry_price": 150.0, "quantity": 75},
        {"status": "OPEN", "entry_price": 33.335, "quantity": 30},
        {"status": "CLOSED", "entry_price": 999.0, "quantity": 75},
    ]
    assert committed_exposure(trades) == pytest.approx(150.0 * 75 + 33.335 * 30)


# A fixed reference "now": Wed 2026-03-18 10:00 UTC = 15:30 IST the same day.
NOW = datetime(2026, 3, 18, 10, 0, tzinfo=timezone.utc)


def _closed(pnl: float, at: datetime) -> Dict[str, Any]:
    return {"status": "CLOSED", "realized_pnl": pnl, "closed_at": _iso(at)}


def test_realized_windows_bucket_membership():
    trades = [
        _closed(-100.0, NOW - timedelta(hours=1)),               # today
        _closed(-10.0, NOW - timedelta(days=1)),                 # this week (Tue)
        _closed(+50.0, datetime(2026, 3, 10, 10, 0, tzinfo=timezone.utc)),  # prev week, same month
        _closed(-1000.0, datetime(2026, 2, 20, 10, 0, tzinfo=timezone.utc)),  # same year only
        _closed(+7.0, datetime(2025, 12, 31, 10, 0, tzinfo=timezone.utc)),  # total only
    ]
    r = realized_windows(trades, _iso(NOW))
    assert r["day"] == pytest.approx(-100.0)
    assert r["week"] == pytest.approx(-110.0)
    assert r["month"] == pytest.approx(-60.0)
    assert r["year"] == pytest.approx(-1060.0)
    assert r["total"] == pytest.approx(-1053.0)


def test_fixed_basis_blocks_when_over_and_allows_when_fits():
    cfg = {"amount": 20000.0, "basis": "fixed"}
    open_one = [{"status": "OPEN", "entry_price": 150.0, "quantity": 75}]  # 11,250
    ok = evaluate_capital_gate(cfg, open_one, 8000.0, scope="deployment", now=_iso(NOW))
    assert ok["allowed"] is True
    blocked = evaluate_capital_gate(cfg, open_one, 11250.0, scope="deployment", now=_iso(NOW))
    assert blocked["allowed"] is False
    assert blocked["reason"].startswith("capital_gate:deployment:")
    assert blocked["committed"] == pytest.approx(11250.0)
    assert blocked["available"] == pytest.approx(8750.0)


def test_fixed_basis_losses_debit_but_profits_never_credit():
    cfg = {"amount": 20000.0, "basis": "fixed"}
    # A profit today does NOT raise the ceiling (no compounding).
    profit = [_closed(+50000.0, NOW - timedelta(hours=2))]
    v = evaluate_capital_gate(cfg, profit, 20001.0, scope="deployment", now=_iso(NOW))
    assert v["allowed"] is False
    # A loss today debits availability.
    loss = [_closed(-5000.0, NOW - timedelta(hours=2))]
    v = evaluate_capital_gate(cfg, loss, 16000.0, scope="deployment", now=_iso(NOW))
    assert v["allowed"] is False
    v = evaluate_capital_gate(cfg, loss, 15000.0, scope="deployment", now=_iso(NOW))
    assert v["allowed"] is True


def test_fixed_basis_uses_worst_window_not_netted_total():
    # +8k banked last week, -5k this week: total = +3k but the WEEK window is
    # -5k — fixed basis must gate off the worst window so required_capital
    # stays bounded for every bucket granularity.
    cfg = {"amount": 20000.0, "basis": "fixed"}
    trades = [
        _closed(+8000.0, datetime(2026, 3, 10, 10, 0, tzinfo=timezone.utc)),
        _closed(-5000.0, NOW - timedelta(days=1)),
    ]
    v = evaluate_capital_gate(cfg, trades, 15000.0, scope="deployment", now=_iso(NOW))
    assert v["allowed"] is True
    v = evaluate_capital_gate(cfg, trades, 15001.0, scope="deployment", now=_iso(NOW))
    assert v["allowed"] is False


def test_cumulative_basis_credits_profits_and_debits_losses():
    cfg = {"amount": 20000.0, "basis": "cumulative"}
    profit = [_closed(+10000.0, NOW - timedelta(days=30))]
    assert evaluate_capital_gate(cfg, profit, 30000.0, scope="deployment", now=_iso(NOW))["allowed"] is True
    assert evaluate_capital_gate(cfg, profit, 30001.0, scope="deployment", now=_iso(NOW))["allowed"] is False
    loss = [_closed(-10000.0, NOW - timedelta(days=30))]
    assert evaluate_capital_gate(cfg, loss, 10001.0, scope="deployment", now=_iso(NOW))["allowed"] is False


# ---------- entry-pipeline integration -------------------------------------------

def _run(coro):
    return asyncio.run(coro)


def test_no_capital_config_keeps_legacy_behavior():
    db = FakeDB()
    sig = make_confirmed_signal()
    db.signals.rows.append(dict(sig))
    dep = make_paper_deployment()
    res = _run(auto_paper_trade_for_signal(db, dep, sig, latest_tick_lookup=tick(150.0)))
    assert res["created"] is True
    assert len(db.paper_trades.rows) == 1
    assert "paper_trade_skip" not in db.signals.rows[0]


def test_capital_gate_allows_within_capital_and_creates_trade():
    db = FakeDB()
    sig = make_confirmed_signal()
    db.signals.rows.append(dict(sig))
    dep = make_paper_deployment(capital={"amount": 20000, "basis": "fixed"})
    res = _run(auto_paper_trade_for_signal(db, dep, sig, latest_tick_lookup=tick(150.0)))
    assert res["created"] is True  # 150 * 75 = 11,250 <= 20,000
    assert db.paper_trades.rows[0]["entry_value"] == pytest.approx(11250.0)


def test_capital_gate_skips_and_journals_when_it_does_not_fit():
    db = FakeDB()
    dep = make_paper_deployment(capital={"amount": 20000, "basis": "fixed"})
    sig1 = make_confirmed_signal()
    db.signals.rows.append(dict(sig1))
    res1 = _run(auto_paper_trade_for_signal(db, dep, sig1, latest_tick_lookup=tick(150.0)))
    assert res1["created"] is True

    sig2 = make_confirmed_signal()
    db.signals.rows.append(dict(sig2))
    res2 = _run(auto_paper_trade_for_signal(db, dep, sig2, latest_tick_lookup=tick(150.0)))
    assert res2["created"] is False
    assert res2["reason"].startswith("capital_gate:deployment:")
    assert res2["capital_gate"]["committed"] == pytest.approx(11250.0)
    # Journaled on the signal, claim released, state untouched (CONFIRMED).
    journaled = next(r for r in db.signals.rows if r["id"] == sig2["id"])
    assert journaled["paper_trade_skip"].startswith("capital_gate:deployment:")
    assert "paper_trade_claim" not in journaled
    assert journaled["state"] == "CONFIRMED"
    # No second trade was inserted.
    assert len(db.paper_trades.rows) == 1


def test_capital_gate_fixed_basis_debits_intraday_loss_in_pipeline():
    db = FakeDB()
    dep = make_paper_deployment(capital={"amount": 20000, "basis": "fixed"})
    # A trade entered and closed today at a Rs 10k loss: availability is now
    # 10k, so an 11,250 entry must be refused even with nothing open.
    db.paper_trades.rows.append({
        "id": "t-loss", "deployment_id": "dep-1", "status": "CLOSED",
        "entry_price": 200.0, "quantity": 75, "realized_pnl": -10000.0,
        "created_at": _iso(datetime.now(timezone.utc) - timedelta(hours=2)),
        "closed_at": _iso(datetime.now(timezone.utc) - timedelta(hours=1)),
    })
    sig = make_confirmed_signal()
    db.signals.rows.append(dict(sig))
    res = _run(auto_paper_trade_for_signal(db, dep, sig, latest_tick_lookup=tick(150.0)))
    assert res["created"] is False
    assert res["reason"].startswith("capital_gate:deployment:")


def test_account_wide_ceiling_blocks_across_deployments():
    db = FakeDB()
    db.app_settings.rows.append({"key": "paper_account", "starting_capital": 20000.0,
                                 "enforce_capital": True, "capital_basis": "fixed"})
    dep_a = make_paper_deployment("dep-1")
    dep_b = make_paper_deployment("dep-2")
    sig_a = make_confirmed_signal(dep_id="dep-1")
    db.signals.rows.append(dict(sig_a))
    assert _run(auto_paper_trade_for_signal(db, dep_a, sig_a, latest_tick_lookup=tick(150.0)))["created"] is True
    # dep-2 has NO per-deployment capital, but the account is now 11,250/20,000
    # committed — another 11,250 across the account must be refused.
    sig_b = make_confirmed_signal(dep_id="dep-2")
    db.signals.rows.append(dict(sig_b))
    res = _run(auto_paper_trade_for_signal(db, dep_b, sig_b, latest_tick_lookup=tick(150.0)))
    assert res["created"] is False
    assert res["reason"].startswith("capital_gate:account:")
    journaled = next(r for r in db.signals.rows if r["id"] == sig_b["id"])
    assert journaled["paper_trade_skip"].startswith("capital_gate:account:")


def test_account_setting_without_enforce_flag_stays_display_only():
    db = FakeDB()
    db.app_settings.rows.append({"key": "paper_account", "starting_capital": 1000.0})
    sig = make_confirmed_signal()
    db.signals.rows.append(dict(sig))
    res = _run(auto_paper_trade_for_signal(
        db, make_paper_deployment(), sig, latest_tick_lookup=tick(150.0)))
    assert res["created"] is True  # 11,250 > 1,000 but the ceiling is opt-in


def test_fake_db_without_app_settings_collection_is_tolerated():
    db = FakeDB()
    delattr(db, "app_settings")
    verdict = _run(check_capital_gate(db, make_paper_deployment(), new_exposure=1e12))
    assert verdict["allowed"] is True


# ---------- the headline invariant ------------------------------------------------

def test_required_capital_never_exceeds_configured_fixed_capital():
    """A paper run with Rs 2L FIXED capital: flood it with concurrent signals,
    bank a loss mid-day, flood again — deployment_period_stats.required_capital
    must stay <= 2L in every bucket of every granularity.

    Timestamps are rewritten to deterministic, spaced values (and the gate is
    given the matching fixed `now`): sub-millisecond test execution would
    otherwise collide an open with a close on the SAME ms, which the analytics
    sweep deliberately double-counts (opens sort before same-ts closes)."""
    CAPITAL = 200_000.0
    db = FakeDB()
    dep = make_paper_deployment(
        capital={"amount": CAPITAL, "basis": "fixed"}, default_lots=2)

    created = skipped = 0
    for _ in range(20):  # each entry: 150 * 2 lots * 75 = 22,500
        sig = make_confirmed_signal()
        db.signals.rows.append(dict(sig))
        res = _run(auto_paper_trade_for_signal(
            db, dep, sig, latest_tick_lookup=tick(150.0), now_utc=NOW))
        created += 1 if res["created"] else 0
        skipped += 0 if res["created"] else 1
    assert created == 8   # floor(200,000 / 22,500)
    assert skipped == 12
    for i, t in enumerate(db.paper_trades.rows):  # space the entries out
        t["created_at"] = _iso(NOW - timedelta(minutes=40 - i))

    # Close two trades at a combined Rs 30k loss (long option: loss <= premium),
    # 30 minutes before "now" — same IST day, after every batch-1 entry.
    for i, loss in ((0, -22000.0), (1, -8000.0)):
        t = db.paper_trades.rows[i]
        t.update({"status": "CLOSED", "realized_pnl": loss,
                  "closed_at": _iso(NOW - timedelta(minutes=30)), "exit_price": 0.0})

    # More signals: base is now 200k - 30k = 170k with 6 * 22.5k = 135k open.
    post_created = 0
    for _ in range(5):
        sig = make_confirmed_signal()
        db.signals.rows.append(dict(sig))
        res = _run(auto_paper_trade_for_signal(
            db, dep, sig, latest_tick_lookup=tick(150.0), now_utc=NOW))
        post_created += 1 if res["created"] else 0
    assert post_created == 1  # 135k + 22.5k = 157.5k <= 170k; the next doesn't fit
    for t in db.paper_trades.rows[8:]:
        t["created_at"] = _iso(NOW - timedelta(minutes=20))

    stats = deployment_period_stats(db.paper_trades.rows, starting_capital=CAPITAL)
    for period, rows in stats["periods"].items():
        for row in rows:
            assert row["required_capital"] <= CAPITAL + 1.0, (
                f"{period} bucket {row['bucket']} required "
                f"{row['required_capital']} > configured {CAPITAL}")
    # And the invariant is not vacuous: capital was actually deployed.
    day_rows = stats["periods"]["day"]
    assert any(r["max_deployed_value"] > 0 for r in day_rows)


# ---------- route surface pins ----------------------------------------------------

def test_backend_exposes_capital_fields_and_skip_passthrough():
    from tests.contract_corpus import backend_api_text
    text = backend_api_text()
    assert "capital_amount" in text
    assert "capital_basis" in text
    assert "lots_override must be 1..100" in text
    assert "paper_trade_skip" in text
    assert "enforce_capital" in text


def test_paper_caps_route_accepts_and_validates_capital(monkeypatch):
    import app.routers.deployments as dep_router
    from fastapi import HTTPException

    db = FakeDB()
    db.strategy_deployments = FakeCollection()
    db.strategy_deployments.rows.append({"id": "dep-1", "mode": "paper", "risk": {}})
    monkeypatch.setattr(dep_router, "get_db", lambda: db)

    body = dep_router._PaperCapsBody(capital={"amount": 200000, "basis": "cumulative"})
    out = _run(dep_router.set_paper_caps("dep-1", body))
    assert out["risk"]["capital"] == {"amount": 200000, "basis": "cumulative"}

    # null clears
    out = _run(dep_router.set_paper_caps("dep-1", dep_router._PaperCapsBody()))
    assert "capital" not in out["risk"]

    with pytest.raises(HTTPException) as exc:
        _run(dep_router.set_paper_caps(
            "dep-1", dep_router._PaperCapsBody(capital={"amount": -5})))
    assert exc.value.status_code == 400


def test_account_config_route_roundtrips_ceiling(monkeypatch):
    import app.routers.journals as journals_router

    db = FakeDB()
    monkeypatch.setattr(journals_router, "get_db", lambda: db)

    req = journals_router.AccountConfigReq(
        starting_capital=250000, enforce_capital=True, capital_basis="cumulative")
    out = _run(journals_router.set_paper_account_config(req))
    assert out == {"starting_capital": 250000.0, "enforce_capital": True,
                   "capital_basis": "cumulative"}
    got = _run(journals_router.get_paper_account_config())
    assert got["enforce_capital"] is True and got["capital_basis"] == "cumulative"

    # Omitted flags keep their stored value (starting_capital-only update).
    out = _run(journals_router.set_paper_account_config(
        journals_router.AccountConfigReq(starting_capital=300000)))
    assert out["enforce_capital"] is True
