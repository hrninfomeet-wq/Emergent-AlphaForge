"""TDD tests for transient-safe reboot reconciliation (Task C2).

``app.live.reboot_reconcile.reconcile_on_startup(db, client)`` runs at startup to
close the two PC-down holes:
  1. a resting OCO fired (or the position was closed externally) while the backend
     was down → the ``live_trades`` doc is still OPEN forever → close it (journal the
     real exit fill price when we can match it; never fabricate a P&L when we can't).
  2. the resting OCO is now orphaned (its entry is closed) → cancel it best-effort.

CRITICAL SAFETY (an audit found a false-close hole): an EMPTY position_book (a
broker Not_Ok / transport hiccup returns ``[]``) must be treated as UNKNOWN, never
"flat" — otherwise EVERY open position looks closed and gets false-closed and every
OCO false-cancelled. The exit fill must be matched to THE entry (by the OCO's
``remarks`` tag ``oco:<entry_norenordno>`` / the entry norenordno), never "newest
SELL by tsym" (a same-strike re-entry would otherwise pick up stale fills).

Harness: a rich in-memory FakeDB cloned from tests/test_deployment_live_routes.py
(its ``$in``/``$ne``-capable matcher; the simpler FakeDBs elsewhere do NOT support
``$ne``, which ``close_live_trade`` + the OPEN-doc query both require). The broker is
a small async FakeClient returning injected book fixtures + recording cancel_oco calls.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.live.reboot_reconcile import reconcile_on_startup  # noqa: E402


# ---------------------------------------------------------------------------
# Rich in-memory Mongo stand-in ($in / $ne capable) — cloned from
# tests/test_deployment_live_routes.py (the simpler FakeDBs do NOT support $ne).
# ---------------------------------------------------------------------------

class _Cursor:
    def __init__(self, rows: List[Dict[str, Any]]):
        self._rows = list(rows)

    def sort(self, key, direction=-1):
        try:
            self._rows.sort(key=lambda r: r.get(key, 0), reverse=(direction == -1))
        except TypeError:
            pass
        return self

    def limit(self, n):
        self._rows = self._rows[: int(n)]
        return self

    async def to_list(self, length=None):
        return list(self._rows if length is None else self._rows[: int(length)])


def _get_dotted(row: Dict[str, Any], key: str) -> Any:
    cur: Any = row
    for part in key.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
    return cur


def _match(row: Dict[str, Any], query: Dict[str, Any]) -> bool:
    for k, v in query.items():
        rv = _get_dotted(row, k)
        if isinstance(v, dict) and "$in" in v:
            if rv not in v["$in"]:
                return False
        elif isinstance(v, dict) and "$ne" in v:
            if rv == v["$ne"]:
                return False
        elif rv != v:
            return False
    return True


class _Collection:
    def __init__(self, rows=None):
        self.rows: List[Dict[str, Any]] = list(rows or [])

    def find(self, query=None, projection=None):
        query = query or {}
        return _Cursor([dict(r) for r in self.rows if _match(r, query)])

    async def find_one(self, query, projection=None):
        for r in self.rows:
            if _match(r, query):
                return dict(r)
        return None

    async def insert_one(self, doc):
        self.rows.append(dict(doc))
        return type("R", (), {"inserted_id": doc.get("id")})()

    async def update_one(self, query, update, upsert=False):
        for r in self.rows:
            if _match(r, query):
                if "$set" in update:
                    r.update(update["$set"])
                return type("R", (), {"matched_count": 1, "modified_count": 1})()
        return type("R", (), {"matched_count": 0, "modified_count": 0})()

    async def update_many(self, query, update):
        n = 0
        for r in self.rows:
            if _match(r, query):
                if "$set" in update:
                    r.update(update["$set"])
                n += 1
        return type("R", (), {"matched_count": n, "modified_count": n})()


class FakeDB:
    def __init__(self):
        self.live_trades = _Collection()


# ---------------------------------------------------------------------------
# Broker stand-in — injected book fixtures + records cancel_oco calls.
# ---------------------------------------------------------------------------

class FakeClient:
    def __init__(
        self,
        *,
        position_book: Any = None,
        trade_book: Optional[List[Dict[str, Any]]] = None,
        gtt_book: Optional[List[Dict[str, Any]]] = None,
    ) -> None:
        self._position_book = position_book
        self._trade_book = trade_book or []
        self._gtt_book = gtt_book or []
        self.cancelled: List[Any] = []

    async def position_book(self) -> Any:
        return self._position_book

    async def trade_book(self) -> List[Dict[str, Any]]:
        return list(self._trade_book)

    async def gtt_book(self) -> List[Dict[str, Any]]:
        return list(self._gtt_book)

    async def cancel_oco(self, al_id: Any) -> Dict[str, Any]:
        self.cancelled.append(al_id)
        return {"ok": True, "al_id": str(al_id), "stat": "Oi delete success"}


def _open_doc(**overrides: Any) -> Dict[str, Any]:
    d = {
        "id": "T1",
        "norenordno": "N1",
        "trading_symbol": "NIFTY24X25000CE",
        "quantity": 65,
        "entry_price": 100.0,
        "status": "OPEN",
        "realized_pnl": None,
        "source": "auto_live_on_signal",
    }
    d.update(overrides)
    return d


def _run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_open_doc_flat_with_remarks_fill_is_closed_with_realized_pnl():
    """OPEN doc N1/tsym X qty65 entry100; non-empty book WITHOUT X (flat) + a SELL
    fill remarks="oco:N1" flprc=130 → CLOSED, realized_pnl=(130-100)*65."""
    db = FakeDB()
    db.live_trades.rows.append(_open_doc())
    client = FakeClient(
        position_book=[{"tsym": "BANKNIFTY24X50000CE", "netqty": "30", "lp": "5"}],
        trade_book=[
            {"tsym": "NIFTY24X25000CE", "trantype": "S", "flprc": "130",
             "norenordno": "EXIT9", "remarks": "oco:N1"},
        ],
    )
    summary = _run(reconcile_on_startup(db, client))
    doc = db.live_trades.rows[0]
    assert doc["status"] == "CLOSED"
    assert doc["exit_price"] == 130.0
    assert doc["realized_pnl"] == (130.0 - 100.0) * 65
    assert doc["exit_reason"] == "reconciled_closed"
    assert summary.get("closed", 0) >= 1


def test_transient_empty_position_book_leaves_open_and_cancels_nothing():
    """TRANSIENT-EMPTY GUARD: position_book=[] (broker hiccup) → the OPEN doc stays
    OPEN (nothing closed) AND gtt_book OCOs are NOT cancelled."""
    db = FakeDB()
    db.live_trades.rows.append(_open_doc())
    client = FakeClient(
        position_book=[],  # empty == UNKNOWN, never flat
        trade_book=[
            {"tsym": "NIFTY24X25000CE", "trantype": "S", "flprc": "130",
             "remarks": "oco:N1"},
        ],
        gtt_book=[{"al_id": "ALx", "tsym": "NIFTY24X25000CE", "remarks": "oco:N1"}],
    )
    summary = _run(reconcile_on_startup(db, client))
    doc = db.live_trades.rows[0]
    assert doc["status"] == "OPEN"
    assert "exit_price" not in doc
    assert client.cancelled == []
    assert summary.get("closed", 0) == 0


def test_none_position_book_is_unknown_too():
    """A None position_book (not a list) is also UNKNOWN → no close, no cancel."""
    db = FakeDB()
    db.live_trades.rows.append(_open_doc())
    client = FakeClient(
        position_book=None,
        gtt_book=[{"al_id": "ALx", "tsym": "NIFTY24X25000CE", "remarks": "oco:N1"}],
    )
    _run(reconcile_on_startup(db, client))
    assert db.live_trades.rows[0]["status"] == "OPEN"
    assert client.cancelled == []


def test_open_doc_still_held_is_left_open():
    """OPEN doc whose tsym IS in the non-empty book (netqty != 0) → still held → OPEN."""
    db = FakeDB()
    db.live_trades.rows.append(_open_doc())
    client = FakeClient(
        position_book=[{"tsym": "NIFTY24X25000CE", "netqty": "65", "lp": "120"}],
    )
    _run(reconcile_on_startup(db, client))
    assert db.live_trades.rows[0]["status"] == "OPEN"
    assert "exit_price" not in db.live_trades.rows[0]


def test_flat_with_zero_netqty_row_is_closed():
    """A row present in the book but with netqty 0 is flat → close the doc."""
    db = FakeDB()
    db.live_trades.rows.append(_open_doc())
    client = FakeClient(
        position_book=[{"tsym": "NIFTY24X25000CE", "netqty": "0", "lp": "0"}],
        trade_book=[{"tsym": "NIFTY24X25000CE", "trantype": "S", "flprc": "130",
                     "remarks": "oco:N1"}],
    )
    _run(reconcile_on_startup(db, client))
    doc = db.live_trades.rows[0]
    assert doc["status"] == "CLOSED"
    assert doc["realized_pnl"] == (130.0 - 100.0) * 65


def test_no_matching_fill_closes_with_realized_pnl_none():
    """Flat but NO matchable fill (no remarks, and >1 same-tsym SELL fills =
    ambiguous) → CLOSED but realized_pnl left None (never fabricated)."""
    db = FakeDB()
    db.live_trades.rows.append(_open_doc())
    client = FakeClient(
        position_book=[{"tsym": "OTHER", "netqty": "30"}],
        trade_book=[
            {"tsym": "NIFTY24X25000CE", "trantype": "S", "flprc": "130"},
            {"tsym": "NIFTY24X25000CE", "trantype": "S", "flprc": "999"},  # ambiguous
        ],
    )
    _run(reconcile_on_startup(db, client))
    doc = db.live_trades.rows[0]
    assert doc["status"] == "CLOSED"
    assert doc["realized_pnl"] is None
    assert "exit_price" not in doc
    assert doc["exit_reason"] == "reconciled_closed"


def test_single_same_tsym_sell_fallback_matches_price():
    """Flat, no remarks tag, but EXACTLY ONE same-tsym SELL fill → fallback uses it."""
    db = FakeDB()
    db.live_trades.rows.append(_open_doc())
    client = FakeClient(
        position_book=[{"tsym": "OTHER", "netqty": "30"}],
        trade_book=[
            {"tsym": "NIFTY24X25000CE", "trantype": "S", "flprc": "150"},
            {"tsym": "NIFTY24X25000CE", "trantype": "B", "flprc": "100"},  # entry, ignored
        ],
    )
    _run(reconcile_on_startup(db, client))
    doc = db.live_trades.rows[0]
    assert doc["status"] == "CLOSED"
    assert doc["realized_pnl"] == (150.0 - 100.0) * 65


def test_doc_without_norenordno_is_skipped():
    """A rehydrated/manual doc with no norenordno must be skipped (not closed)."""
    db = FakeDB()
    db.live_trades.rows.append(_open_doc(norenordno=None, id="T2"))
    client = FakeClient(position_book=[{"tsym": "OTHER", "netqty": "30"}])
    _run(reconcile_on_startup(db, client))
    assert db.live_trades.rows[0]["status"] == "OPEN"


def test_orphan_oco_sweep_cancels_when_entry_closed_not_when_open():
    """ORPHAN sweep: an OCO remarks=oco:N1 whose N1 doc is CLOSED → cancel ALx;
    an OCO remarks=oco:N2 whose N2 doc is still OPEN → NOT cancelled."""
    db = FakeDB()
    db.live_trades.rows.append(_open_doc(id="T1", norenordno="N1", status="CLOSED",
                                         trading_symbol="X1"))
    db.live_trades.rows.append(_open_doc(id="T2", norenordno="N2", status="OPEN",
                                         trading_symbol="X2"))
    client = FakeClient(
        # non-empty book; X2 still held so the OPEN doc is correctly left open
        position_book=[{"tsym": "X2", "netqty": "65"}],
        gtt_book=[
            {"al_id": "ALx", "tsym": "X1", "remarks": "oco:N1"},  # entry CLOSED → orphan
            {"Al_id": "ALy", "tsym": "X2", "remarks": "oco:N2"},  # entry OPEN → keep
        ],
    )
    _run(reconcile_on_startup(db, client))
    assert "ALx" in client.cancelled
    assert "ALy" not in client.cancelled


def test_orphan_oco_no_remarks_cancelled_only_when_flat_and_no_open_trade():
    """An OCO with no remarks link: cancel ONLY when its tsym is flat in the
    non-empty book AND there is no OPEN live_trade for it."""
    db = FakeDB()
    # OPEN doc for tsym HELD — its OCO (no remarks) must be kept.
    db.live_trades.rows.append(_open_doc(id="T1", norenordno="N1",
                                         trading_symbol="HELD"))
    client = FakeClient(
        position_book=[{"tsym": "HELD", "netqty": "65"}],  # FLATSY absent → flat
        gtt_book=[
            {"al_id": "ALheld", "tsym": "HELD"},   # position held → keep
            {"al_id": "ALflat", "tsym": "FLATSY"},  # flat + no open trade → orphan
        ],
    )
    _run(reconcile_on_startup(db, client))
    assert "ALflat" in client.cancelled
    assert "ALheld" not in client.cancelled


def test_never_raises_on_broker_error():
    """If a phase blows up the function must swallow it and return a summary dict."""
    class _Boom:
        async def position_book(self):
            raise RuntimeError("transport down")
        async def trade_book(self):
            return []
        async def gtt_book(self):
            return []
        async def cancel_oco(self, al_id):
            return {}

    db = FakeDB()
    db.live_trades.rows.append(_open_doc())
    summary = _run(reconcile_on_startup(db, _Boom()))
    assert isinstance(summary, dict)
    assert db.live_trades.rows[0]["status"] == "OPEN"


def test_closed_doc_is_never_retouched():
    """An already-CLOSED doc (status != OPEN) is excluded by the $ne query → not reclosed."""
    db = FakeDB()
    db.live_trades.rows.append(_open_doc(status="CLOSED", realized_pnl=42.0,
                                         trading_symbol="OTHER"))
    client = FakeClient(position_book=[{"tsym": "STILLHERE", "netqty": "30"}])
    summary = _run(reconcile_on_startup(db, client))
    assert db.live_trades.rows[0]["realized_pnl"] == 42.0  # untouched
    assert summary.get("closed", 0) == 0
