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
from app.live.live_position_guard import (  # noqa: E402
    LiveMonitorRegistry,
    get_registry,
)
from app.live.live_sl_monitor import build_monitor_state  # noqa: E402


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


# ---------------------------------------------------------------------------
# Re-link phase (Fix 3): on startup, re-attach a still-resting OCO to a
# rehydrated HELD position so the guard can cancel it on a later square
# (no orphan misfire) + flag HELD positions with no broker backstop.
#
# The guard registry is a PROCESS SINGLETON — these tests clear it first so a
# prior test's entries never leak in.
# ---------------------------------------------------------------------------

def _fresh_registry() -> LiveMonitorRegistry:
    r = get_registry()
    r.clear()
    return r


def _register_rehydrated(r: LiveMonitorRegistry, *, tsym: str, oco_al_id=None):
    """A rehydrated entry as guard.rehydrate_from_broker builds it: keyed by tsym,
    source="rehydrated", default 50% stop, oco_al_id None by default."""
    state = build_monitor_state(100.0, stop_pct=50.0)
    return r.register(
        key=tsym, tsym=tsym, exch="NFO", qty=65, prd="I",
        entry_price=100.0, state=state, source="rehydrated", oco_al_id=oco_al_id,
    )


def test_relink_resting_oco_to_held_rehydrated_position():
    """A rehydrated entry (tsym X, oco_al_id None) + non-empty book HOLDING X +
    a gtt_book OCO for X → the entry's oco_al_id is set to the OCO's al_id;
    summary relinked >= 1."""
    r = _fresh_registry()
    _register_rehydrated(r, tsym="X")
    db = FakeDB()
    client = FakeClient(
        position_book=[{"tsym": "X", "netqty": "65", "lp": "120"}],
        gtt_book=[{"al_id": "ALrelink", "tsym": "X"}],
    )
    summary = _run(reconcile_on_startup(db, client))
    assert r.get("X")["oco_al_id"] == "ALrelink"
    assert summary.get("relinked", 0) >= 1
    # re-linked held OCO must NOT be swept (held, not orphan)
    assert "ALrelink" not in client.cancelled


def test_held_rehydrated_with_no_oco_is_flagged_no_backstop():
    """A held rehydrated entry (tsym Y) with NO gtt_book OCO for Y → oco_al_id
    stays None and summary no_backstop >= 1 (software-guard-only signal)."""
    r = _fresh_registry()
    _register_rehydrated(r, tsym="Y")
    db = FakeDB()
    client = FakeClient(
        position_book=[{"tsym": "Y", "netqty": "65", "lp": "120"}],
        gtt_book=[],  # no resting OCO for Y
    )
    summary = _run(reconcile_on_startup(db, client))
    assert r.get("Y")["oco_al_id"] is None
    assert summary.get("no_backstop", 0) >= 1


def test_relink_does_not_overwrite_existing_oco_al_id():
    """A held entry that ALREADY has an oco_al_id is left unchanged (not
    overwritten by a same-tsym gtt row) and is NOT counted as no_backstop."""
    r = _fresh_registry()
    _register_rehydrated(r, tsym="Z", oco_al_id="ALoriginal")
    db = FakeDB()
    client = FakeClient(
        position_book=[{"tsym": "Z", "netqty": "65", "lp": "120"}],
        gtt_book=[{"al_id": "ALother", "tsym": "Z"}],
    )
    summary = _run(reconcile_on_startup(db, client))
    assert r.get("Z")["oco_al_id"] == "ALoriginal"  # untouched
    assert summary.get("no_backstop", 0) == 0


def test_relink_only_held_flat_oco_still_orphan_swept():
    """A FLAT tsym's OCO is still orphan-swept (cancelled) and NOT re-linked;
    a HELD tsym's OCO is re-linked, never swept."""
    r = _fresh_registry()
    _register_rehydrated(r, tsym="HELD")  # held → re-link target
    db = FakeDB()
    client = FakeClient(
        position_book=[{"tsym": "HELD", "netqty": "65"}],  # FLAT tsym absent → flat
        gtt_book=[
            {"al_id": "ALheld", "tsym": "HELD"},   # held → re-link, never sweep
            {"al_id": "ALflat", "tsym": "FLAT"},   # flat + no open trade → orphan
        ],
    )
    summary = _run(reconcile_on_startup(db, client))
    assert r.get("HELD")["oco_al_id"] == "ALheld"  # re-linked
    assert summary.get("relinked", 0) >= 1
    assert "ALheld" not in client.cancelled  # held OCO never swept
    assert "ALflat" in client.cancelled       # flat OCO orphan-swept


def test_relink_empty_book_is_unknown_no_relink():
    """The transient-empty guard still holds for re-link: an empty position_book
    → no re-link (oco_al_id stays None), no sweep, no_backstop not reported as a
    held flag (UNKNOWN short-circuits the whole routine)."""
    r = _fresh_registry()
    _register_rehydrated(r, tsym="X")
    db = FakeDB()
    client = FakeClient(
        position_book=[],  # empty == UNKNOWN
        gtt_book=[{"al_id": "ALx", "tsym": "X"}],
    )
    summary = _run(reconcile_on_startup(db, client))
    assert r.get("X")["oco_al_id"] is None
    assert client.cancelled == []
    assert summary.get("relinked", 0) == 0


def test_relink_skips_non_held_registry_entry():
    """A registry entry whose tsym is FLAT (not held) is NOT a re-link target and
    is NOT flagged no_backstop (re-link/flag are HELD-only)."""
    r = _fresh_registry()
    _register_rehydrated(r, tsym="GONE")  # flat at broker
    db = FakeDB()
    client = FakeClient(
        position_book=[{"tsym": "OTHER", "netqty": "30"}],  # GONE absent → flat
        gtt_book=[{"al_id": "ALgone", "tsym": "GONE"}],
    )
    summary = _run(reconcile_on_startup(db, client))
    assert r.get("GONE")["oco_al_id"] is None
    assert summary.get("no_backstop", 0) == 0  # not held → not flagged
