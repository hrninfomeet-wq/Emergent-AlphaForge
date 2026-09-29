import asyncio
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest
from fastapi import HTTPException

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

import app.routers.live_broker as lb
from app.live.broker_protocol import BrokerReadError
from app.live.mock_noren import MockNoren
from app.live_mark_cache import StaleSnapshotError


class _Reg:
    def __init__(self, items): self._items = items
    def snapshot(self): return list(self._items)


class _Marks:
    """Stand-in for the shared LiveMarksService: the Greeks route reads the broker
    book through `.book()` (the same single-flight cache every stream shares)."""

    def __init__(self, rows=None, *, exc=None, stale=False, error=None):
        self._rows, self._exc, self._stale, self._error = rows or [], exc, stale, error

    async def book(self):
        if self._exc is not None:
            raise self._exc
        return {"rows": list(self._rows), "stale": self._stale, "age_ms": 5,
                "error": self._error}


def _run(coro): return asyncio.run(coro)


def _broker_row(tsym="NIFTY25000CE", netqty="65", exch="NFO"):
    return {"tsym": tsym, "exch": exch, "netqty": netqty}


def _exp(days=7):
    return (date.today() + timedelta(days=days)).strftime("%d-%b-%Y").upper()


def _client(with_contract=True):
    cl = MockNoren()
    cl.set_quotes({"stat": "Ok", "bp1": "99.5", "sp1": "100.5", "sptprc": "25000"})
    if with_contract:
        cl.set_search_scrip("NFO", [{
            "tsym": "NIFTY25000CE", "token": "TKN1", "optt": "CE",
            "exd": _exp(), "dname": "NIFTY 04JUL26 25000 CE ",
        }])
    return cl


def _wire(monkeypatch, *, client, marks, registry=()):
    lb._greeks_contract_cache.clear()
    monkeypatch.setattr(lb, "_get_client", lambda: client)
    monkeypatch.setattr(lb, "_marks_service", lambda: marks)
    monkeypatch.setattr(lb, "_get_live_registry", lambda: _Reg(list(registry)))


def _guarded(tsym="NIFTY25000CE"):
    return {"tsym": tsym, "exch": "NFO", "position": {"netqty": 65}}


# --------------------------------------------------------------------------- #
# the original contract (kept), now over the BROKER book
# --------------------------------------------------------------------------- #

def test_greeks_route_empty_when_no_client(monkeypatch):
    # A book that was READ and is empty needs no client to be a fact.
    _wire(monkeypatch, client=None, marks=_Marks([]))
    out = _run(lb.live_broker_greeks())
    assert out["n_computed"] == 0 and out["positions"] == []
    assert out["book"]["state"] == "flat"


def test_greeks_route_aggregates(monkeypatch):
    _wire(monkeypatch, client=_client(), marks=_Marks([_broker_row()]),
          registry=[_guarded()])
    out = _run(lb.live_broker_greeks())
    assert out["n_computed"] == 1 and out["net_theta_rupees_per_day"] < 0.0
    assert out["book"]["state"] == "open" and out["book"]["unguarded"] == []


def test_greeks_route_aggregates_via_underlying_prefix(monkeypatch):
    # SearchScrip returns NO row for the full-tsym query but DOES return the row
    # for the underlying-prefix ("NIFTY") query. The dual-query resolver must still
    # find the exact contract (correctness preserved — exact-tsym filter in both).
    row = {
        "tsym": "NIFTY25000CE", "token": "TKN1", "optt": "CE",
        "exd": _exp(), "dname": "NIFTY 04JUL26 25000 CE ",
    }
    cl = _client(with_contract=False)
    # Key the rows by the EXACT (exch, text) pair = the underlying-prefix query only,
    # so the full-tsym query returns [] and only the prefix fallback finds the row.
    cl._search_scrip_data[("NFO", "NIFTY")] = [row]
    _wire(monkeypatch, client=cl, marks=_Marks([_broker_row()]), registry=[_guarded()])
    out = _run(lb.live_broker_greeks())
    assert out["n_computed"] == 1 and out["net_theta_rupees_per_day"] < 0.0


def test_greeks_route_skips_unresolvable_tsym(monkeypatch):
    # The broker holds a position whose exact tsym is NOT in the search rows
    # (a different contract is returned). Neither the full-tsym nor the
    # underlying-prefix query yields an exact tsym match → the position is skipped,
    # never silently mis-priced against the wrong contract.
    cl = MockNoren()
    cl.set_quotes({"stat": "Ok", "bp1": "99.5", "sp1": "100.5", "sptprc": "25000"})
    cl.set_search_scrip("NFO", [{
        "tsym": "NIFTY24000PE", "token": "TKNX", "optt": "PE",
        "exd": _exp(), "dname": "NIFTY 04JUL26 24000 PE ",
    }])
    _wire(monkeypatch, client=cl, marks=_Marks([_broker_row()]), registry=[_guarded()])
    out = _run(lb.live_broker_greeks())
    assert out["n_skipped"] == 1 and out["n_computed"] == 0


# --------------------------------------------------------------------------- #
# the truth the card stands on: flat vs unknown vs unguarded
# --------------------------------------------------------------------------- #

def test_a_read_and_empty_broker_book_is_flat_and_the_only_zero(monkeypatch):
    _wire(monkeypatch, client=_client(), marks=_Marks([]))
    out = _run(lb.live_broker_greeks())
    assert out["book"] == {"state": "flat", "open_count": 0, "guarded_count": 0,
                           "unguarded": [], "error": None, "stale": False}
    assert out["net_delta_rupees_per_point"] == 0.0
    assert out["net_theta_rupees_per_day"] == 0.0


def test_a_closed_row_at_zero_qty_is_still_flat(monkeypatch):
    _wire(monkeypatch, client=_client(), marks=_Marks([_broker_row(netqty="0")]))
    assert _run(lb.live_broker_greeks())["book"]["state"] == "flat"


def test_an_empty_guard_registry_does_not_make_the_account_flat(monkeypatch):
    """THE defect: after a restart with an expired token the registry is empty while
    a carried broker position exists. The old route returned zeros ("No open live
    positions."). The broker book has the position, so the route prices it and says
    it is NOT under the guard."""
    _wire(monkeypatch, client=_client(), marks=_Marks([_broker_row()]), registry=[])
    out = _run(lb.live_broker_greeks())
    assert out["book"]["state"] == "open"
    assert out["book"]["unguarded"] == ["NIFTY25000CE"]
    assert out["book"]["guarded_count"] == 0 and out["book"]["open_count"] == 1
    assert out["n_computed"] == 1 and out["net_theta_rupees_per_day"] < 0.0


def test_a_guarded_position_is_not_reported_unguarded(monkeypatch):
    _wire(monkeypatch, client=_client(), marks=_Marks([_broker_row()]),
          registry=[_guarded()])
    book = _run(lb.live_broker_greeks())["book"]
    assert book["unguarded"] == [] and book["guarded_count"] == 1


@pytest.mark.parametrize("exc, needle", [
    (HTTPException(400, "Flattrade not connected. Complete OAuth."), "not connected"),
    (BrokerReadError("Session Expired : Invalid Session Key", route="position_book"),
     "token expired"),
    (StaleSnapshotError("snapshot is 90000 ms old and the source is failing"), "old"),
    (RuntimeError("boom"), "unreachable"),
])
def test_an_unreadable_book_is_unknown_never_zero(monkeypatch, exc, needle):
    """Expired session / no token / stale last-good / broker down: 'no positions'
    cannot be said, so the figures are None (rendered '—'), not 0.0."""
    _wire(monkeypatch, client=_client(), marks=_Marks(exc=exc), registry=[])
    out = _run(lb.live_broker_greeks())
    assert out["book"]["state"] == "unknown"
    assert needle in out["book"]["error"].lower()
    assert out["book"]["open_count"] is None
    assert out["net_delta_rupees_per_point"] is None
    assert out["net_theta_rupees_per_day"] is None


def test_a_stale_last_good_book_is_unknown_not_flat(monkeypatch):
    """The cache serves last-good while the source fails: that is not a confirmation
    the account is flat (or open) now."""
    _wire(monkeypatch, client=_client(), marks=_Marks([], stale=True,
                                                       error="Session Expired"))
    out = _run(lb.live_broker_greeks())
    assert out["book"]["state"] == "unknown" and out["book"]["stale"] is True
    assert out["net_delta_rupees_per_point"] is None


def test_unknown_book_still_prices_what_the_guard_can_see(monkeypatch):
    """Book unreadable but a guarded position + a working quote client: the figures
    are real but partial, flagged by book.state == unknown (the UI says so)."""
    _wire(monkeypatch, client=_client(), marks=_Marks(exc=RuntimeError("down")),
          registry=[_guarded()])
    out = _run(lb.live_broker_greeks())
    assert out["book"]["state"] == "unknown"
    assert out["n_computed"] == 1 and out["net_theta_rupees_per_day"] < 0.0


def test_open_positions_none_of_which_can_be_priced_are_unknown_not_zero(monkeypatch):
    cl = MockNoren()   # no search rows -> the contract cannot be resolved
    cl.set_quotes({"stat": "Ok", "bp1": "99.5", "sp1": "100.5", "sptprc": "25000"})
    _wire(monkeypatch, client=cl, marks=_Marks([_broker_row()]), registry=[_guarded()])
    out = _run(lb.live_broker_greeks())
    assert out["book"]["state"] == "open"
    assert out["n_computed"] == 0 and out["n_skipped"] == 1
    assert out["net_delta_rupees_per_point"] is None
    assert out["net_theta_rupees_per_day"] is None


def test_open_position_with_no_quote_client_is_unknown_with_a_reason(monkeypatch):
    _wire(monkeypatch, client=None, marks=_Marks([_broker_row()]), registry=[])
    out = _run(lb.live_broker_greeks())
    assert out["book"]["state"] == "open"
    assert out["net_delta_rupees_per_point"] is None
    assert "cannot be priced" in out["error"]


def test_a_compute_failure_is_unknown_not_zero(monkeypatch):
    async def boom(*a, **k):
        raise RuntimeError("bs failed")

    _wire(monkeypatch, client=_client(), marks=_Marks([_broker_row()]),
          registry=[_guarded()])
    monkeypatch.setattr(lb, "compute_portfolio_greeks", boom)
    out = _run(lb.live_broker_greeks())
    assert out["book"]["state"] == "open"
    assert out["net_delta_rupees_per_point"] is None and "failed" in out["error"]


def test_the_route_reads_the_book_through_the_shared_cache_not_the_broker(monkeypatch):
    """No second broker read: the book comes from the single-flight cache every
    stream shares (the Flattrade rate budget is shared per key). The client is used
    for quotes/search only — its position_book must never be called by this route."""
    calls = {"position_book": 0, "book": 0}
    cl = _client()

    async def counted_position_book():
        calls["position_book"] += 1
        return []

    cl.position_book = counted_position_book

    class _CountingMarks(_Marks):
        async def book(self):
            calls["book"] += 1
            return await super().book()

    _wire(monkeypatch, client=cl, marks=_CountingMarks([_broker_row()]),
          registry=[_guarded()])
    _run(lb.live_broker_greeks())
    assert calls == {"position_book": 0, "book": 1}
