"""The operator-approved backfill of ONE closed trade from recorded fills.

It must add nothing of its own judgement: the same proven aggregate as the
reconcile, the same journal helper as every close, dry-run unless told otherwise,
and it must never overwrite a number that already exists.
"""
from __future__ import annotations

import asyncio
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from tests.test_reboot_reconcile import FakeDB  # noqa: E402
from tests.test_reconcile_partial_fill_pnl import ENTRY, REAL_BOOK, TSYM  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "backfill_script", ROOT / "backend" / "scripts" / "backfill_realized_from_recorded_fills.py")
backfill_script = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(backfill_script)


def _closed(**kw):
    d = {"norenordno": ENTRY, "noren_tsym": TSYM, "quantity": 100, "exch": "BFO",
         "entry_price": 354.0, "entry_fill_price": 352.78, "status": "CLOSED",
         "realized_pnl": None, "exit_reason": "reconciled_closed"}
    d.update(kw)
    return d


def _go(db, *, apply, fills=REAL_BOOK, attest="rpnl 822.00 == proven round trip"):
    return asyncio.run(backfill_script.backfill(
        db, norenordno=ENTRY, fills=fills, source="test", attest_no_carry=attest,
        apply=apply))


def test_dry_run_reports_the_brokers_number_and_writes_nothing():
    db = FakeDB()
    db.live_trades.rows.append(_closed())
    out = _go(db, apply=False)
    assert out["ok"] and not out["applied"]
    assert out["realized_pnl"] == 822.00
    assert db.live_trades.rows[0]["realized_pnl"] is None


def test_apply_writes_it_with_provenance():
    db = FakeDB()
    db.live_trades.rows.append(_closed())
    out = _go(db, apply=True)
    row = db.live_trades.rows[0]
    assert out["applied"]
    assert row["realized_pnl"] == 822.00 and row["exit_price"] == 361.00
    assert row["realized_pnl_backfilled"] is True
    assert row["realized_pnl_backfill_source"] == "test"
    assert row["realized_pnl_backfill_attestation"] == "rpnl 822.00 == proven round trip"
    assert row["net_realized_pnl"] < row["realized_pnl"]


def test_an_existing_number_is_never_overwritten():
    db = FakeDB()
    db.live_trades.rows.append(_closed(realized_pnl=-500.0))
    out = _go(db, apply=True)
    assert not out["ok"] and out["reason"] == "already_journalled"
    assert db.live_trades.rows[0]["realized_pnl"] == -500.0


def test_an_open_trade_is_refused():
    db = FakeDB()
    db.live_trades.rows.append(_closed(status="OPEN"))
    assert _go(db, apply=True)["reason"] == "not_closed:OPEN"


def test_fills_that_do_not_prove_the_exit_write_nothing():
    """The entry's own fills missing → not provable → nothing written."""
    db = FakeDB()
    db.live_trades.rows.append(_closed())
    sells_only = [r for r in REAL_BOOK if r["trantype"] == "S"]
    out = _go(db, apply=True, fills=sells_only)
    assert out["reason"] == "exit_not_provable_from_these_fills"
    assert db.live_trades.rows[0]["realized_pnl"] is None


def test_without_an_attestation_nothing_is_written():
    """Recorded fills cannot prove no position was CARRIED FORWARD on the contract
    (the live reconcile reads that from the position book). The operator must say
    so, with evidence — or nothing happens."""
    db = FakeDB()
    db.live_trades.rows.append(_closed())
    out = _go(db, apply=True, attest="  ")
    assert not out["ok"] and out["reason"].startswith("attestation_required")
    assert db.live_trades.rows[0]["realized_pnl"] is None


def test_an_unknown_exit_day_is_dated_from_the_proven_exit_fill():
    """A stale doc closed days later carries closed_at = the day it was NOTICED.
    Backfilling a price onto that would charge the P&L to the wrong day's
    day-stop; the proven exit fill (10:20:47 IST) says when it really happened."""
    db = FakeDB()
    db.live_trades.rows.append(_closed(exit_day_unknown=True,
                                       closed_at="2026-09-29T04:00:00+00:00"))
    _go(db, apply=True)
    row = db.live_trades.rows[0]
    assert row["closed_at"] == "2026-09-16T04:50:47+00:00"
    assert row["exit_day_unknown"] is False


def test_a_known_exit_day_keeps_its_closed_at():
    db = FakeDB()
    db.live_trades.rows.append(_closed(closed_at="2026-09-16T04:52:22+00:00"))
    _go(db, apply=True)
    assert db.live_trades.rows[0]["closed_at"] == "2026-09-16T04:52:22+00:00"
