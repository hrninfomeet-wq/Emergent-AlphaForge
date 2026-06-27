import asyncio
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

import app.routers.live_broker as lb
from app.live.mock_noren import MockNoren


class _Reg:
    def __init__(self, items): self._items = items
    def snapshot(self): return list(self._items)


def _run(coro): return asyncio.run(coro)


def test_greeks_route_empty_when_no_client(monkeypatch):
    monkeypatch.setattr(lb, "_get_client", lambda: None)
    out = _run(lb.live_broker_greeks())
    assert out["n_computed"] == 0 and out["positions"] == []


def test_greeks_route_aggregates(monkeypatch):
    exp = (date.today() + timedelta(days=7)).strftime("%d-%b-%Y").upper()  # e.g. 04-JUL-2026
    cl = MockNoren()
    cl.set_quotes({"stat": "Ok", "bp1": "99.5", "sp1": "100.5", "sptprc": "25000"})
    cl.set_search_scrip("NFO", [{
        "tsym": "NIFTY25000CE", "token": "TKN1", "optt": "CE",
        "exd": exp, "dname": "NIFTY 04JUL26 25000 CE ",
    }])
    monkeypatch.setattr(lb, "_get_client", lambda: cl)
    monkeypatch.setattr(lb, "_get_live_registry",
                        lambda: _Reg([{"tsym": "NIFTY25000CE", "exch": "NFO", "position": {"netqty": 65}}]))
    out = _run(lb.live_broker_greeks())
    assert out["n_computed"] == 1 and out["net_theta_rupees_per_day"] < 0.0


def test_greeks_route_aggregates_via_underlying_prefix(monkeypatch):
    # SearchScrip returns NO row for the full-tsym query but DOES return the row
    # for the underlying-prefix ("NIFTY") query. The dual-query resolver must still
    # find the exact contract (correctness preserved — exact-tsym filter in both).
    lb._greeks_contract_cache.clear()
    exp = (date.today() + timedelta(days=7)).strftime("%d-%b-%Y").upper()
    row = {
        "tsym": "NIFTY25000CE", "token": "TKN1", "optt": "CE",
        "exd": exp, "dname": "NIFTY 04JUL26 25000 CE ",
    }
    cl = MockNoren()
    cl.set_quotes({"stat": "Ok", "bp1": "99.5", "sp1": "100.5", "sptprc": "25000"})
    # Key the rows by the EXACT (exch, text) pair = the underlying-prefix query only,
    # so the full-tsym query returns [] and only the prefix fallback finds the row.
    cl._search_scrip_data[("NFO", "NIFTY")] = [row]
    monkeypatch.setattr(lb, "_get_client", lambda: cl)
    monkeypatch.setattr(lb, "_get_live_registry",
                        lambda: _Reg([{"tsym": "NIFTY25000CE", "exch": "NFO", "position": {"netqty": 65}}]))
    out = _run(lb.live_broker_greeks())
    assert out["n_computed"] == 1 and out["net_theta_rupees_per_day"] < 0.0


def test_greeks_route_skips_unresolvable_tsym(monkeypatch):
    # The registry holds a position whose exact tsym is NOT in the search rows
    # (a different contract is returned). Neither the full-tsym nor the
    # underlying-prefix query yields an exact tsym match → the position is skipped,
    # never silently mis-priced against the wrong contract.
    lb._greeks_contract_cache.clear()
    exp = (date.today() + timedelta(days=7)).strftime("%d-%b-%Y").upper()
    cl = MockNoren()
    cl.set_quotes({"stat": "Ok", "bp1": "99.5", "sp1": "100.5", "sptprc": "25000"})
    cl.set_search_scrip("NFO", [{
        "tsym": "NIFTY24000PE", "token": "TKNX", "optt": "PE",
        "exd": exp, "dname": "NIFTY 04JUL26 24000 PE ",
    }])
    monkeypatch.setattr(lb, "_get_client", lambda: cl)
    monkeypatch.setattr(lb, "_get_live_registry",
                        lambda: _Reg([{"tsym": "NIFTY25000CE", "exch": "NFO", "position": {"netqty": 65}}]))
    out = _run(lb.live_broker_greeks())
    assert out["n_skipped"] == 1 and out["n_computed"] == 0
