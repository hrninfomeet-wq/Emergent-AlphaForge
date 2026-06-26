"""Tests for app.live.close_loop — the live_trades realized-P&L close-loop.

Covers the two adversarially-flagged risks: (1) never journal a CLOSED on a
dry-run / failed / manual square; (2) idempotent, unambiguous close linked by the
per-order norenordno with the correct long-only P&L sign.
"""
from __future__ import annotations

import asyncio
import os
import sys
from typing import Any, Dict, List

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from app.live.close_loop import should_journal_close, close_live_trade  # noqa: E402


# ── minimal async Mongo stand-in (live_trades only) ────────────────────────
def _match(row: Dict[str, Any], q: Dict[str, Any]) -> bool:
    for k, v in q.items():
        rv = row.get(k)
        if isinstance(v, dict) and "$ne" in v:
            if rv == v["$ne"]:
                return False
        elif rv != v:
            return False
    return True


class _Coll:
    def __init__(self, rows=None):
        self.rows: List[Dict[str, Any]] = list(rows or [])

    async def find_one(self, q, projection=None):
        for r in self.rows:
            if _match(r, q):
                return dict(r)
        return None

    async def update_one(self, q, update):
        for r in self.rows:
            if _match(r, q):
                if "$set" in update:
                    r.update(update["$set"])
                return type("R", (), {"matched_count": 1, "modified_count": 1})()
        return type("R", (), {"matched_count": 0, "modified_count": 0})()


class _DB:
    def __init__(self, rows=None):
        self.live_trades = _Coll(rows)


def _open_doc(**kw):
    d = {
        "id": "uuid-1", "norenordno": "N100", "trading_symbol": "NIFTY25000CE",
        "deployment_id": "dep-1", "entry_price": 100.0, "quantity": 130,
        "status": "OPEN", "realized_pnl": None, "created_at": "2026-06-26T04:00:00+00:00",
    }
    d.update(kw)
    return d


# ── should_journal_close ───────────────────────────────────────────────────

def test_real_square_is_journaled():
    assert should_journal_close({"source": "auto_live"}, {"squared": True}) is True


def test_dry_run_is_not_journaled():
    # LIVE_GUARD_ARMED off → squared False + dry_run True → nothing transmitted.
    assert should_journal_close({"source": "auto_live"},
                                {"squared": False, "dry_run": True}) is False


def test_failed_square_is_not_journaled():
    assert should_journal_close({"source": "auto_live"},
                                {"squared": False, "error": "rejected"}) is False


def test_manual_source_is_never_journaled():
    # manual single-shot has no live_trades doc even on a real square.
    assert should_journal_close({"source": "manual"}, {"squared": True}) is False


def test_missing_inputs_are_safe():
    assert should_journal_close(None, None) is False
    assert should_journal_close({}, {"squared": True}) is True  # default source != manual


# ── close_live_trade ───────────────────────────────────────────────────────

def test_close_sets_status_pnl_and_exit_fields():
    db = _DB([_open_doc()])
    ok = asyncio.run(close_live_trade(db, norenordno="N100", exit_price=130.0,
                                      exit_reason="target", now_iso="2026-06-26T09:00:00+00:00"))
    assert ok is True
    row = db.live_trades.rows[0]
    assert row["status"] == "CLOSED"
    assert row["exit_price"] == 130.0
    assert row["exit_reason"] == "target"
    assert row["closed_at"] == "2026-06-26T09:00:00+00:00"
    # long-only buy: (130 - 100) * 130 = +3900
    assert row["realized_pnl"] == 3900.0


def test_close_loss_has_negative_pnl():
    db = _DB([_open_doc()])
    asyncio.run(close_live_trade(db, norenordno="N100", exit_price=60.0, exit_reason="stop"))
    assert db.live_trades.rows[0]["realized_pnl"] == (60.0 - 100.0) * 130  # -5200


def test_close_is_idempotent():
    db = _DB([_open_doc()])
    first = asyncio.run(close_live_trade(db, norenordno="N100", exit_price=130.0, exit_reason="target"))
    second = asyncio.run(close_live_trade(db, norenordno="N100", exit_price=999.0, exit_reason="stop"))
    assert first is True
    assert second is False                          # already CLOSED → no-op
    assert db.live_trades.rows[0]["exit_price"] == 130.0   # not clobbered by the 999 retry
    assert db.live_trades.rows[0]["realized_pnl"] == 3900.0


def test_unknown_exit_price_closes_without_fabricating_pnl():
    db = _DB([_open_doc()])
    ok = asyncio.run(close_live_trade(db, norenordno="N100", exit_price=None, exit_reason="eod_square"))
    assert ok is True
    row = db.live_trades.rows[0]
    assert row["status"] == "CLOSED"
    assert "exit_price" not in row
    assert row["realized_pnl"] is None              # never fabricated


def test_no_matching_open_doc_is_noop():
    db = _DB([_open_doc(norenordno="OTHER")])
    assert asyncio.run(close_live_trade(db, norenordno="N100", exit_price=130.0, exit_reason="target")) is False


def test_falsy_norenordno_is_noop():
    db = _DB([_open_doc()])
    assert asyncio.run(close_live_trade(db, norenordno=None, exit_price=130.0, exit_reason="target")) is False
    assert asyncio.run(close_live_trade(db, norenordno="", exit_price=130.0, exit_reason="target")) is False


def test_same_norenordno_uniquely_targets_its_own_doc():
    # two OPEN docs share a tsym (re-entry) but have distinct norenordnos →
    # closing N101 must not touch N100.
    db = _DB([_open_doc(id="a", norenordno="N100"), _open_doc(id="b", norenordno="N101")])
    asyncio.run(close_live_trade(db, norenordno="N101", exit_price=120.0, exit_reason="target"))
    by_id = {r["id"]: r for r in db.live_trades.rows}
    assert by_id["b"]["status"] == "CLOSED"
    assert by_id["a"]["status"] == "OPEN"           # the same-tsym sibling untouched


# ── close_live_trade: broker-true fill_price (reboot reconciliation) ─────────

def test_fill_price_is_used_as_exit_price_and_for_pnl():
    # reboot reconciliation: the broker trade book gives the TRUE fill price,
    # used in preference to any guard exit-mark estimate.
    db = _DB([_open_doc(quantity=65, entry_price=100.0)])
    ok = asyncio.run(close_live_trade(db, norenordno="N100", exit_price=None,
                                      fill_price=132.0, exit_reason="reconciled"))
    assert ok is True
    row = db.live_trades.rows[0]
    assert row["status"] == "CLOSED"
    assert row["exit_price"] == 132.0
    assert row["exit_reason"] == "reconciled"
    assert row["realized_pnl"] == (132.0 - 100.0) * 65   # +2080


def test_fill_price_none_falls_back_to_exit_price():
    # fill_price omitted → behaviour is exactly as today (uses exit_price).
    db = _DB([_open_doc(quantity=130, entry_price=100.0)])
    ok = asyncio.run(close_live_trade(db, norenordno="N100", exit_price=130.0,
                                      fill_price=None, exit_reason="target"))
    assert ok is True
    row = db.live_trades.rows[0]
    assert row["exit_price"] == 130.0
    assert row["realized_pnl"] == 3900.0


def test_fill_price_wins_over_exit_price_when_both_given():
    db = _DB([_open_doc(quantity=65, entry_price=100.0)])
    asyncio.run(close_live_trade(db, norenordno="N100", exit_price=130.0,
                                 fill_price=132.0, exit_reason="reconciled"))
    row = db.live_trades.rows[0]
    assert row["exit_price"] == 132.0                    # broker fill wins
    assert row["realized_pnl"] == (132.0 - 100.0) * 65   # +2080, from fill_price
