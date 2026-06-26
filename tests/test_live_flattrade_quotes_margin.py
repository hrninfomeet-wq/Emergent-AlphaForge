"""Tests for FlattradeClient GetOrderMargin (#8) + GetQuotes (#54) transport.

Two new read/pre-check methods feeding the OCO backstop:

  * order_margin (GetOrderMargin) — a broker margin PRE-CHECK before placing.
    Returns the RAW response dict INCLUDING ``stat:"Not_Ok"`` so a later task can
    fail-CLOSED on a broker reject (we must NOT swallow it into {}).
  * get_quotes (GetQuotes) — depth-aware fresh LTP read. Pure price read, so it
    returns {} on a non-Ok response.

We monkeypatch the client's _post (the EXACT seam from test_live_flattrade_gtt.py)
so no network is touched; the tests assert the request the client BUILDS
(route + jdata, incl. identity injection) and how it PARSES each response.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.live.flattrade_client import FlattradeClient  # noqa: E402


def _client(scripted):
    """A FlattradeClient whose _post returns scripted[route] and records calls."""
    c = FlattradeClient(jKey="JK", uid="FZ001", actid="FZ001")
    calls = []

    async def fake_post(route, jdata):
        calls.append((route, jdata))
        resp = scripted.get(route)
        if resp is None:
            raise AssertionError(f"unexpected route {route}")
        return resp

    c._post = fake_post  # type: ignore[assignment]
    c._calls = calls     # type: ignore[attr-defined]
    return c


# ===========================================================================
# order_margin — GetOrderMargin (#8)
# ===========================================================================
@pytest.mark.asyncio
async def test_order_margin_posts_to_right_route_with_identity():
    scripted = {"GetOrderMargin": {"stat": "Ok", "ordermargin": "6500.00", "cash": "200000.00"}}
    c = _client(scripted)
    res = await c.order_margin(
        exch="NFO", tsym="NIFTY26JUN26C25000", qty=65, prc=100.0,
        prd="M", trantype="B", prctyp="LMT",
    )
    route, jdata = c._calls[-1]
    assert route == "GetOrderMargin"
    assert jdata == {
        "uid": "FZ001", "actid": "FZ001", "exch": "NFO", "tsym": "NIFTY26JUN26C25000",
        "qty": "65", "prc": "100.00", "prd": "M", "trantype": "B", "prctyp": "LMT",
    }
    assert res == scripted["GetOrderMargin"]


@pytest.mark.asyncio
async def test_order_margin_returns_raw_not_ok_dict():
    """A broker reject must come back RAW (NOT swallowed to {}) so the caller fails CLOSED."""
    scripted = {"GetOrderMargin": {"stat": "Not_Ok", "emsg": "x"}}
    c = _client(scripted)
    res = await c.order_margin(
        exch="NFO", tsym="NIFTY26JUN26C25000", qty=65, prc=100.0,
        prd="M", trantype="B", prctyp="LMT",
    )
    assert res == {"stat": "Not_Ok", "emsg": "x"}


# ===========================================================================
# get_quotes — GetQuotes (#54)
# ===========================================================================
@pytest.mark.asyncio
async def test_get_quotes_posts_to_right_route_with_identity():
    scripted = {"GetQuotes": {"stat": "Ok", "lp": "101.25", "token": "12345"}}
    c = _client(scripted)
    res = await c.get_quotes(exch="NFO", token="12345")
    route, jdata = c._calls[-1]
    assert route == "GetQuotes"
    assert jdata == {"uid": "FZ001", "exch": "NFO", "token": "12345"}
    assert res == scripted["GetQuotes"]


@pytest.mark.asyncio
async def test_get_quotes_returns_empty_on_not_ok():
    """A pure price read returns {} on a non-Ok response."""
    c = _client({"GetQuotes": {"stat": "Not_Ok", "emsg": "no data"}})
    res = await c.get_quotes(exch="NFO", token="12345")
    assert res == {}
