"""Tests for GET /live-broker/margin-probe (NRML margin readback) — task A5.

A read-only readback that asks the broker (GetOrderMargin) what NRML (prd="M")
margin the actual option leg would block, so the operator can confirm the
margin before the live readback.

NRML ONLY: GetOrderMargin's prd enum is C/M/H (no MIS "I"); an I-leg would be
rejected, so this route never probes MIS. M-vs-MIS parity, if wanted, is read
from Limits, not here.

RULES: never instantiate a real FlattradeClient; patch the module getters.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict
from unittest.mock import patch, AsyncMock

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

import app.routers.live_broker as _routes  # noqa: E402


class _FakeClient:
    """Minimal async broker client exposing only order_margin(), recording kwargs."""

    def __init__(self, resp: Dict[str, Any]) -> None:
        self._resp = resp
        self.calls: list[Dict[str, Any]] = []

    async def order_margin(self, **kw: Any) -> Dict[str, Any]:
        self.calls.append(dict(kw))
        return dict(self._resp)


def _probe(*, client, params) -> "tuple[int, Dict[str, Any]]":
    """Call the real route with a fake client patched in. Returns (status, json)."""
    app = FastAPI()
    app.include_router(_routes.api)
    with patch.object(_routes, "_get_client", AsyncMock(return_value=client)):
        tc = TestClient(app, raise_server_exceptions=True)
        resp = tc.get("/live-broker/margin-probe", params=params)
    body = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
    return resp.status_code, body


def test_margin_probe_returns_cash_marginused_stat():
    client = _FakeClient({"stat": "Ok", "cash": "50000", "marginused": "13000"})
    status, body = _probe(
        client=client,
        params={"exch": "NFO", "tsym": "NIFTY26JUN26C25000", "qty": 65, "prc": 100.0},
    )
    assert status == 200, body
    assert body["cash"] == "50000"
    assert body["marginused"] == "13000"
    assert body["stat"] == "Ok"
    assert body["prd"] == "M"


def test_margin_probe_sends_nrml_buy_lmt_to_broker():
    client = _FakeClient({"stat": "Ok", "cash": "50000", "marginused": "13000"})
    _probe(
        client=client,
        params={"exch": "NFO", "tsym": "NIFTY26JUN26C25000", "qty": 65, "prc": 100.0},
    )
    assert len(client.calls) == 1
    call = client.calls[0]
    assert call["exch"] == "NFO"
    assert call["tsym"] == "NIFTY26JUN26C25000"
    assert int(call["qty"]) == 65
    assert float(call["prc"]) == 100.0
    assert call["prd"] == "M"          # NRML only — never MIS "I"
    assert call["trantype"] == "B"
    assert call["prctyp"] == "LMT"


def test_margin_probe_400_when_not_connected():
    app = FastAPI()
    app.include_router(_routes.api)
    not_connected = AsyncMock(side_effect=HTTPException(400, "Flattrade not connected."))
    with patch.object(_routes, "_get_client", not_connected):
        tc = TestClient(app, raise_server_exceptions=True)
        resp = tc.get(
            "/live-broker/margin-probe",
            params={"exch": "NFO", "tsym": "NIFTY26JUN26C25000", "qty": 65, "prc": 100.0},
        )
    assert resp.status_code == 400
