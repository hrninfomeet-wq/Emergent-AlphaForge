"""Host tests for the IPv4-forcing network helpers in app.live._net.

Covers:
  - force_ipv4() defaults True (FLATTRADE_FORCE_IPV4 not set / set to "1")
  - force_ipv4() returns False when FLATTRADE_FORCE_IPV4=0
  - ipv4_transport() returns an httpx.AsyncHTTPTransport when forcing is on
  - ipv4_transport() returns None when forcing is off
  - exchange_code_for_token() builds its httpx.AsyncClient with a non-None
    transport when FLATTRADE_FORCE_IPV4 is at its default ("1")
  - exchange_code_for_token() builds its httpx.AsyncClient with transport=None
    when FLATTRADE_FORCE_IPV4=0
"""
from __future__ import annotations

import asyncio
import hashlib
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))


def run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# force_ipv4()
# ---------------------------------------------------------------------------

def test_force_ipv4_default_true(monkeypatch):
    """FLATTRADE_FORCE_IPV4 not set → force_ipv4() is True."""
    monkeypatch.delenv("FLATTRADE_FORCE_IPV4", raising=False)
    # Re-import to ensure fresh env read (the function reads os.environ at
    # call time, so no module reload is needed)
    from app.live._net import force_ipv4
    assert force_ipv4() is True


def test_force_ipv4_explicit_1(monkeypatch):
    """FLATTRADE_FORCE_IPV4=1 → force_ipv4() is True."""
    monkeypatch.setenv("FLATTRADE_FORCE_IPV4", "1")
    from app.live._net import force_ipv4
    assert force_ipv4() is True


def test_force_ipv4_disabled(monkeypatch):
    """FLATTRADE_FORCE_IPV4=0 → force_ipv4() is False."""
    monkeypatch.setenv("FLATTRADE_FORCE_IPV4", "0")
    from app.live._net import force_ipv4
    assert force_ipv4() is False


# ---------------------------------------------------------------------------
# ipv4_transport()
# ---------------------------------------------------------------------------

def test_ipv4_transport_returns_transport_when_forcing(monkeypatch):
    """ipv4_transport() returns an AsyncHTTPTransport when force_ipv4() is True."""
    monkeypatch.delenv("FLATTRADE_FORCE_IPV4", raising=False)
    from app.live._net import ipv4_transport
    transport = ipv4_transport()
    assert transport is not None
    assert isinstance(transport, httpx.AsyncHTTPTransport)


def test_ipv4_transport_returns_none_when_disabled(monkeypatch):
    """ipv4_transport() returns None when FLATTRADE_FORCE_IPV4=0."""
    monkeypatch.setenv("FLATTRADE_FORCE_IPV4", "0")
    from app.live._net import ipv4_transport
    transport = ipv4_transport()
    assert transport is None


# ---------------------------------------------------------------------------
# exchange_code_for_token() — verify transport kwarg passed to AsyncClient
# ---------------------------------------------------------------------------

def _make_token_resp(stat: str = "Ok", token: str = "JKEY_TEST") -> MagicMock:
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {"stat": stat, "token": token}
    resp.text = f'{{"stat":"{stat}","token":"{token}"}}'
    return resp


def test_exchange_code_for_token_uses_ipv4_transport_by_default(monkeypatch):
    """exchange_code_for_token() passes a non-None transport when forcing is on."""
    monkeypatch.setenv("FLATTRADE_API_KEY", "TESTKEY")
    monkeypatch.setenv("FLATTRADE_API_SECRET", "TESTSECRET")
    monkeypatch.delenv("FLATTRADE_FORCE_IPV4", raising=False)

    captured: dict = {}

    def fake_async_client(*args, **kwargs):
        captured["transport"] = kwargs.get("transport")
        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=_make_token_resp())
        return mock_client

    with patch("app.live.flattrade_token.httpx.AsyncClient", side_effect=fake_async_client):
        from app.live import flattrade_token
        run(flattrade_token.exchange_code_for_token("MYCODE"))

    assert captured.get("transport") is not None, (
        "exchange_code_for_token must pass transport=ipv4_transport() to AsyncClient; "
        "got None (IPv4 forcing is disabled — Flattrade will reject with INVALID_IP)"
    )
    assert isinstance(captured["transport"], httpx.AsyncHTTPTransport)


def test_exchange_code_for_token_no_transport_when_forcing_off(monkeypatch):
    """exchange_code_for_token() passes transport=None when FLATTRADE_FORCE_IPV4=0."""
    monkeypatch.setenv("FLATTRADE_API_KEY", "TESTKEY")
    monkeypatch.setenv("FLATTRADE_API_SECRET", "TESTSECRET")
    monkeypatch.setenv("FLATTRADE_FORCE_IPV4", "0")

    captured: dict = {}

    def fake_async_client(*args, **kwargs):
        captured["transport"] = kwargs.get("transport", "NOT_SET")
        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=_make_token_resp())
        return mock_client

    with patch("app.live.flattrade_token.httpx.AsyncClient", side_effect=fake_async_client):
        from app.live import flattrade_token
        run(flattrade_token.exchange_code_for_token("MYCODE"))

    assert captured.get("transport") is None


# ---------------------------------------------------------------------------
# _post() in FlattradeClient — verify transport kwarg passed to AsyncClient
# ---------------------------------------------------------------------------

def test_flattrade_client_post_uses_ipv4_transport_by_default(monkeypatch):
    """FlattradeClient._post() passes a non-None transport when forcing is on."""
    monkeypatch.delenv("FLATTRADE_FORCE_IPV4", raising=False)

    captured: dict = {}

    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {"stat": "Ok"}
    resp.text = '{"stat":"Ok"}'

    def fake_async_client(*args, **kwargs):
        captured["transport"] = kwargs.get("transport")
        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=resp)
        return mock_client

    with patch("app.live.flattrade_client.httpx.AsyncClient", side_effect=fake_async_client):
        from app.live.flattrade_client import FlattradeClient
        client = FlattradeClient(jKey="J", uid="U", actid="A")
        run(client._post("Limits", {"uid": "U", "actid": "A"}))

    assert captured.get("transport") is not None, (
        "FlattradeClient._post must pass transport=ipv4_transport() to AsyncClient"
    )
    assert isinstance(captured["transport"], httpx.AsyncHTTPTransport)


def test_flattrade_client_post_no_transport_when_forcing_off(monkeypatch):
    """FlattradeClient._post() passes transport=None when FLATTRADE_FORCE_IPV4=0."""
    monkeypatch.setenv("FLATTRADE_FORCE_IPV4", "0")

    captured: dict = {}

    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {"stat": "Ok"}
    resp.text = '{"stat":"Ok"}'

    def fake_async_client(*args, **kwargs):
        captured["transport"] = kwargs.get("transport", "NOT_SET")
        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=resp)
        return mock_client

    with patch("app.live.flattrade_client.httpx.AsyncClient", side_effect=fake_async_client):
        from app.live.flattrade_client import FlattradeClient
        client = FlattradeClient(jKey="J", uid="U", actid="A")
        run(client._post("Limits", {"uid": "U", "actid": "A"}))

    assert captured.get("transport") is None
