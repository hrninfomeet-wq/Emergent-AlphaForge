"""Tests for FlattradeClient — host-only, no real network calls.

Covers:
  - _post() builds correct URL + form-encoded body (jData=...&jKey=...)
  - _post() parses ok/reject responses correctly
  - search_scrip() parses a canned Noren SearchScrip response into expected shape
      (fields: tsym, token, ls, strprc, optt, exd)
  - order_book() returns empty list on non-Ok / returns list on Ok
  - position_book(), trade_book(), limits() parse correctly
  - place_order() builds correct jData from OrderIntent and parses:
      {stat:"Ok", norenordno: ...} → OrderResult(ok=True, norenordno=...)
      {stat:"Not_Ok", emsg: ...} → OrderResult(ok=False, rejreason=...)
  - cancel_order(), modify_order() request-building + response-parsing
  - _dispatch() routes "om" message to on_om callback
  - _dispatch() ignores "ck" (auth ack) without calling on_om
  - _dispatch() routes "dk"/"tf" tick messages to on_tick if provided
  - _dispatch() ignores unknown types
"""
import asyncio
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.live.broker_protocol import OrderIntent, OrderResult
from app.live.flattrade_client import FlattradeClient, _dispatch


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def run(coro):
    return asyncio.run(coro)


def _client(jKey="JKEY1", uid="U1", actid="A1") -> FlattradeClient:
    return FlattradeClient(jKey=jKey, uid=uid, actid=actid)


def _intent(
    *,
    cid: str = "cid1",
    trantype: str = "B",
    prctyp: str = "LMT",
    exch: str = "NFO",
    tsym: str = "NIFTY25000CE",
    qty: int = 65,
    prc: float = 158.5,
    trgprc: Optional[float] = None,
) -> OrderIntent:
    return OrderIntent(
        client_order_id=cid,
        trantype=trantype,
        prctyp=prctyp,
        exch=exch,
        tsym=tsym,
        qty=qty,
        prc=prc,
        trgprc=trgprc,
        prd="I",
        ret="DAY",
    )


def _make_httpx_mock(status_code: int, json_data: Any):
    """Build a mock httpx.AsyncClient that returns a fixed response."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data
    resp.text = json.dumps(json_data) if isinstance(json_data, (dict, list)) else str(json_data)

    mock_client = MagicMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.post = AsyncMock(return_value=resp)
    return mock_client, resp


# ---------------------------------------------------------------------------
# FlattradeClient construction
# ---------------------------------------------------------------------------

def test_client_requires_jkey():
    import pytest
    with pytest.raises(ValueError, match="jKey"):
        FlattradeClient(jKey="", uid="U1", actid="A1")


# ---------------------------------------------------------------------------
# _make_body — form-encoded request body
# ---------------------------------------------------------------------------

def test_make_body_format():
    """Body must be jData=<json>&jKey=<token>."""
    client = _client(jKey="MYKEY")
    jdata = {"uid": "U1", "actid": "A1"}
    body = client._make_body(jdata)
    assert body.startswith("jData=")
    assert "&jKey=MYKEY" in body
    # The jData portion must be valid JSON
    jdata_part = body.split("&jKey=")[0].removeprefix("jData=")
    parsed = json.loads(jdata_part)
    assert parsed["uid"] == "U1"
    assert parsed["actid"] == "A1"


def test_make_body_includes_jkey():
    client = _client(jKey="SECRETTOKEN")
    body = client._make_body({"a": 1})
    assert "jKey=SECRETTOKEN" in body


# ---------------------------------------------------------------------------
# _post — URL + body verification via stubbed httpx
# ---------------------------------------------------------------------------

def test_post_sends_to_correct_url():
    """_post must POST to https://piconnect.flattrade.in/PiConnectAPI/<route>."""
    client = _client(jKey="K1")
    mock_httpx, resp = _make_httpx_mock(200, {"stat": "Ok"})

    captured_url = []
    captured_body = []

    async def fake_post(url, *, content=None, headers=None, **kw):
        captured_url.append(url)
        captured_body.append(content)
        return resp

    mock_httpx.post = AsyncMock(side_effect=fake_post)

    with patch("app.live.flattrade_client.httpx.AsyncClient", return_value=mock_httpx):
        result = run(client._post("OrderBook", {"uid": "U1"}))

    assert len(captured_url) == 1
    assert captured_url[0] == "https://piconnect.flattrade.in/PiConnectAPI/OrderBook"


def test_post_body_contains_jdata_and_jkey():
    """_post must include both jData=<json> and &jKey=<token> in the body."""
    client = _client(jKey="TOKEN123")
    mock_httpx, resp = _make_httpx_mock(200, {"stat": "Ok"})

    captured_body = []

    async def fake_post(url, *, content=None, **kw):
        captured_body.append(content)
        return resp

    mock_httpx.post = AsyncMock(side_effect=fake_post)

    with patch("app.live.flattrade_client.httpx.AsyncClient", return_value=mock_httpx):
        run(client._post("Limits", {"uid": "U1", "actid": "A1"}))

    body = captured_body[0]
    assert "jData=" in body
    assert "&jKey=TOKEN123" in body


def test_post_raises_on_http_error():
    """Non-200 HTTP → RuntimeError."""
    import pytest
    client = _client()
    mock_httpx, _ = _make_httpx_mock(500, {})
    mock_httpx.post.return_value.text = "Server Error"

    with patch("app.live.flattrade_client.httpx.AsyncClient", return_value=mock_httpx):
        with pytest.raises(RuntimeError, match="500"):
            run(client._post("PlaceOrder", {}))


# ---------------------------------------------------------------------------
# order_book — parse canned Noren response
# ---------------------------------------------------------------------------

def test_order_book_returns_list_on_success():
    """order_book parses a list response correctly."""
    client = _client(uid="U1", actid="A1")
    noren_resp = [
        {
            "norenordno": "1234",
            "tsym": "NIFTY25000CE",
            "trantype": "B",
            "prctyp": "LMT",
            "qty": "65",
            "prc": "158.5",
            "status": "OPEN",
        }
    ]
    mock_httpx, _ = _make_httpx_mock(200, noren_resp)

    with patch("app.live.flattrade_client.httpx.AsyncClient", return_value=mock_httpx):
        result = run(client.order_book())

    assert isinstance(result, list)
    assert len(result) == 1
    assert result[0]["norenordno"] == "1234"
    assert result[0]["tsym"] == "NIFTY25000CE"


def test_order_book_returns_empty_on_not_ok():
    """order_book returns [] when stat != Ok (e.g., no orders)."""
    client = _client()
    mock_httpx, _ = _make_httpx_mock(200, {"stat": "Not_Ok", "emsg": "No Data"})

    with patch("app.live.flattrade_client.httpx.AsyncClient", return_value=mock_httpx):
        result = run(client.order_book())

    assert result == []


# ---------------------------------------------------------------------------
# position_book — parse canned response
# ---------------------------------------------------------------------------

def test_position_book_parses_list():
    client = _client()
    positions = [
        {"tsym": "NIFTY25000CE", "netqty": "65", "netavgprc": "158.5"}
    ]
    mock_httpx, _ = _make_httpx_mock(200, positions)

    with patch("app.live.flattrade_client.httpx.AsyncClient", return_value=mock_httpx):
        result = run(client.position_book())

    assert len(result) == 1
    assert result[0]["tsym"] == "NIFTY25000CE"


def test_position_book_returns_empty_on_failure():
    client = _client()
    mock_httpx, _ = _make_httpx_mock(200, {"stat": "Not_Ok", "emsg": "No positions"})
    with patch("app.live.flattrade_client.httpx.AsyncClient", return_value=mock_httpx):
        result = run(client.position_book())
    assert result == []


# ---------------------------------------------------------------------------
# limits — parse canned response
# ---------------------------------------------------------------------------

def test_limits_returns_dict_on_success():
    client = _client()
    noren_limits = {
        "stat": "Ok",
        "cash": "500000",
        "payin": "200000",
        "marginused": "100000",
    }
    mock_httpx, _ = _make_httpx_mock(200, noren_limits)

    with patch("app.live.flattrade_client.httpx.AsyncClient", return_value=mock_httpx):
        result = run(client.limits())

    assert result["cash"] == "500000"
    assert result["payin"] == "200000"
    assert result["stat"] == "Ok"


def test_limits_returns_empty_on_failure():
    client = _client()
    mock_httpx, _ = _make_httpx_mock(200, {"stat": "Not_Ok", "emsg": "Session expired"})
    with patch("app.live.flattrade_client.httpx.AsyncClient", return_value=mock_httpx):
        result = run(client.limits())
    assert result == {}


# ---------------------------------------------------------------------------
# search_scrip — parse canned Noren SearchScrip response
# The symbol resolver (L0.3) relies on: tsym, token, ls, strprc, optt, exd
# ---------------------------------------------------------------------------

def test_search_scrip_parses_values_list():
    """Noren SearchScrip returns {stat:"Ok", values:[...]}."""
    client = _client()
    scrip_rows = [
        {
            "tsym": "NIFTY25JUN2025C25000",
            "token": "43215",
            "ls": "65",
            "strprc": "25000.00",
            "optt": "CE",
            "exd": "26-Jun-2025",
        },
        {
            "tsym": "NIFTY25JUN2025P25000",
            "token": "43216",
            "ls": "65",
            "strprc": "25000.00",
            "optt": "PE",
            "exd": "26-Jun-2025",
        },
    ]
    noren_resp = {"stat": "Ok", "values": scrip_rows}
    mock_httpx, _ = _make_httpx_mock(200, noren_resp)

    with patch("app.live.flattrade_client.httpx.AsyncClient", return_value=mock_httpx):
        result = run(client.search_scrip("NFO", "NIFTY 25000"))

    assert len(result) == 2
    row = result[0]
    # Verify all fields the symbol resolver expects
    assert row["tsym"] == "NIFTY25JUN2025C25000"
    assert row["token"] == "43215"
    assert row["ls"] == "65"
    assert row["strprc"] == "25000.00"
    assert row["optt"] == "CE"
    assert row["exd"] == "26-Jun-2025"


def test_search_scrip_returns_empty_on_not_ok():
    client = _client()
    mock_httpx, _ = _make_httpx_mock(200, {"stat": "Not_Ok", "emsg": "No scrip found"})
    with patch("app.live.flattrade_client.httpx.AsyncClient", return_value=mock_httpx):
        result = run(client.search_scrip("NFO", "UNKNOWN"))
    assert result == []


def test_search_scrip_request_has_stext_and_exch():
    """search_scrip jData must include uid, stext, exch."""
    client = _client(uid="USER1", jKey="KEY1")
    mock_httpx, _ = _make_httpx_mock(200, {"stat": "Ok", "values": []})
    captured_body = []

    async def fake_post(url, *, content=None, **kw):
        captured_body.append(content)
        return mock_httpx.post.return_value

    mock_httpx.post = AsyncMock(side_effect=fake_post)
    mock_httpx.post.return_value = MagicMock(
        status_code=200,
        json=lambda: {"stat": "Ok", "values": []},
        text='{"stat":"Ok","values":[]}',
    )

    with patch("app.live.flattrade_client.httpx.AsyncClient", return_value=mock_httpx):
        run(client.search_scrip("BFO", "SENSEX 72000"))

    body = captured_body[0]
    jdata_str = body.split("&jKey=")[0].removeprefix("jData=")
    jdata = json.loads(jdata_str)
    assert jdata["uid"] == "USER1"
    assert jdata["stext"] == "SENSEX 72000"
    assert jdata["exch"] == "BFO"


def test_search_scrip_handles_direct_list_response():
    """Some Noren versions return a bare list instead of {stat, values}."""
    client = _client()
    rows = [{"tsym": "NIFTY25000CE", "token": "1", "ls": "65", "strprc": "25000", "optt": "CE", "exd": "26-Jun-2025"}]
    mock_httpx, _ = _make_httpx_mock(200, rows)
    with patch("app.live.flattrade_client.httpx.AsyncClient", return_value=mock_httpx):
        result = run(client.search_scrip("NFO", "NIFTY"))
    assert len(result) == 1
    assert result[0]["tsym"] == "NIFTY25000CE"


# ---------------------------------------------------------------------------
# place_order — jData building + response parsing
# ---------------------------------------------------------------------------

def test_place_order_builds_jdata_from_intent():
    """place_order must include all OrderIntent fields in jData."""
    client = _client(uid="U1", actid="A1", jKey="K1")
    mock_httpx, _ = _make_httpx_mock(200, {"stat": "Ok", "norenordno": "ORD001"})
    captured_body = []

    async def fake_post(url, *, content=None, **kw):
        captured_body.append(content)
        return mock_httpx.post.return_value

    mock_httpx.post = AsyncMock(side_effect=fake_post)
    mock_httpx.post.return_value = MagicMock(
        status_code=200,
        json=lambda: {"stat": "Ok", "norenordno": "ORD001"},
        text='{"stat":"Ok","norenordno":"ORD001"}',
    )

    intent = _intent(cid="CID1", trantype="B", prctyp="LMT", exch="NFO",
                     tsym="NIFTY25000CE", qty=65, prc=158.5)

    with patch("app.live.flattrade_client.httpx.AsyncClient", return_value=mock_httpx):
        result = run(client.place_order(intent))

    assert result.ok is True
    assert result.norenordno == "ORD001"

    # Verify jData fields
    body = captured_body[0]
    jdata_str = body.split("&jKey=")[0].removeprefix("jData=")
    jdata = json.loads(jdata_str)

    assert jdata["uid"] == "U1"
    assert jdata["actid"] == "A1"
    assert jdata["trantype"] == "B"
    assert jdata["prctyp"] == "LMT"
    assert jdata["exch"] == "NFO"
    assert jdata["tsym"] == "NIFTY25000CE"
    assert jdata["qty"] == "65"
    assert jdata["prc"] == "158.5"
    assert jdata["ordersource"] == "API"
    assert "trgprc" not in jdata  # LMT order, no trigger


def test_place_order_sl_lmt_includes_trgprc():
    """SL-LMT intent must include trgprc in jData."""
    client = _client(uid="U1", actid="A1", jKey="K1")
    mock_httpx, _ = _make_httpx_mock(200, {"stat": "Ok", "norenordno": "ORD002"})
    captured_body = []

    async def fake_post(url, *, content=None, **kw):
        captured_body.append(content)
        return mock_httpx.post.return_value

    mock_httpx.post = AsyncMock(side_effect=fake_post)
    mock_httpx.post.return_value = MagicMock(
        status_code=200,
        json=lambda: {"stat": "Ok", "norenordno": "ORD002"},
        text='{"stat":"Ok","norenordno":"ORD002"}',
    )

    intent = _intent(prctyp="SL-LMT", prc=119.0, trgprc=120.0, trantype="S")

    with patch("app.live.flattrade_client.httpx.AsyncClient", return_value=mock_httpx):
        result = run(client.place_order(intent))

    assert result.ok is True
    body = captured_body[0]
    jdata = json.loads(body.split("&jKey=")[0].removeprefix("jData="))
    assert jdata["prctyp"] == "SL-LMT"
    assert jdata["trgprc"] == "120"
    assert jdata["prc"] == "119"


def test_place_order_ok_response_maps_norenordno():
    """Successful Noren response → OrderResult(ok=True, norenordno=...)."""
    client = _client()
    mock_httpx, _ = _make_httpx_mock(200, {"stat": "Ok", "norenordno": "FT12345"})
    with patch("app.live.flattrade_client.httpx.AsyncClient", return_value=mock_httpx):
        result = run(client.place_order(_intent()))
    assert result.ok is True
    assert result.norenordno == "FT12345"
    assert result.rejreason is None


def test_place_order_not_ok_response_maps_rejreason():
    """{stat:"Not_Ok", emsg:...} → OrderResult(ok=False, rejreason=emsg)."""
    client = _client()
    mock_httpx, _ = _make_httpx_mock(200, {"stat": "Not_Ok", "emsg": "RMS limit exceeded"})
    with patch("app.live.flattrade_client.httpx.AsyncClient", return_value=mock_httpx):
        result = run(client.place_order(_intent()))
    assert result.ok is False
    assert result.norenordno is None
    assert "RMS limit exceeded" in result.rejreason


def test_place_order_http_error_returns_not_ok():
    """HTTP error → OrderResult(ok=False) — never raises."""
    client = _client()
    mock_httpx, _ = _make_httpx_mock(503, {})
    mock_httpx.post.return_value.text = "Service Unavailable"
    with patch("app.live.flattrade_client.httpx.AsyncClient", return_value=mock_httpx):
        result = run(client.place_order(_intent()))
    assert result.ok is False
    assert result.rejreason  # non-empty reason


# ---------------------------------------------------------------------------
# cancel_order — request-building + response parsing
# ---------------------------------------------------------------------------

def test_cancel_order_ok_response():
    client = _client(uid="U1", jKey="K1")
    mock_httpx, _ = _make_httpx_mock(200, {"stat": "Ok", "result": "canceled"})
    with patch("app.live.flattrade_client.httpx.AsyncClient", return_value=mock_httpx):
        result = run(client.cancel_order("ORD999"))
    assert result.ok is True
    assert result.norenordno == "ORD999"


def test_cancel_order_not_ok_response():
    client = _client()
    mock_httpx, _ = _make_httpx_mock(200, {"stat": "Not_Ok", "emsg": "Order already completed"})
    with patch("app.live.flattrade_client.httpx.AsyncClient", return_value=mock_httpx):
        result = run(client.cancel_order("BAD"))
    assert result.ok is False
    assert "already completed" in result.rejreason


def test_cancel_order_includes_norenordno_in_jdata():
    client = _client(uid="U1", jKey="K1")
    mock_httpx, _ = _make_httpx_mock(200, {"stat": "Ok"})
    captured_body = []

    async def fake_post(url, *, content=None, **kw):
        captured_body.append(content)
        return mock_httpx.post.return_value

    mock_httpx.post = AsyncMock(side_effect=fake_post)
    mock_httpx.post.return_value = MagicMock(
        status_code=200,
        json=lambda: {"stat": "Ok"},
        text='{"stat":"Ok"}',
    )

    with patch("app.live.flattrade_client.httpx.AsyncClient", return_value=mock_httpx):
        run(client.cancel_order("MY_ORD_123"))

    body = captured_body[0]
    jdata = json.loads(body.split("&jKey=")[0].removeprefix("jData="))
    assert jdata["uid"] == "U1"
    assert jdata["norenordno"] == "MY_ORD_123"


# ---------------------------------------------------------------------------
# modify_order — request-building + response parsing
# ---------------------------------------------------------------------------

def test_modify_order_ok():
    client = _client()
    mock_httpx, _ = _make_httpx_mock(200, {"stat": "Ok"})
    with patch("app.live.flattrade_client.httpx.AsyncClient", return_value=mock_httpx):
        result = run(client.modify_order("ORD1", prc=110.0))
    assert result.ok is True
    assert result.norenordno == "ORD1"


def test_modify_order_includes_prc_string():
    client = _client(uid="U1", jKey="K1")
    mock_httpx, _ = _make_httpx_mock(200, {"stat": "Ok"})
    captured_body = []

    async def fake_post(url, *, content=None, **kw):
        captured_body.append(content)
        return mock_httpx.post.return_value

    mock_httpx.post = AsyncMock(side_effect=fake_post)
    mock_httpx.post.return_value = MagicMock(
        status_code=200,
        json=lambda: {"stat": "Ok"},
        text='{"stat":"Ok"}',
    )

    with patch("app.live.flattrade_client.httpx.AsyncClient", return_value=mock_httpx):
        run(client.modify_order("ORD1", prc=110.0, trgprc=111.0))

    body = captured_body[0]
    jdata = json.loads(body.split("&jKey=")[0].removeprefix("jData="))
    assert jdata["prc"] == "110"
    assert jdata["trgprc"] == "111"
    assert jdata["norenordno"] == "ORD1"


def test_modify_order_not_ok():
    client = _client()
    mock_httpx, _ = _make_httpx_mock(200, {"stat": "Not_Ok", "emsg": "Cannot modify completed order"})
    with patch("app.live.flattrade_client.httpx.AsyncClient", return_value=mock_httpx):
        result = run(client.modify_order("ORD1", prc=100.0))
    assert result.ok is False
    assert "Cannot modify" in result.rejreason


# ---------------------------------------------------------------------------
# _dispatch — pure function, no WS connection
# ---------------------------------------------------------------------------

def test_dispatch_om_calls_on_om():
    """t=='om' → on_om callback invoked with the full message."""
    received = []
    msg = {
        "t": "om",
        "norenordno": "ORD001",
        "status": "OPEN",
        "reporttype": "New",
        "fillshares": "0",
        "avgprc": "0",
        "rejreason": "",
    }
    _dispatch(msg, on_om=received.append)
    assert len(received) == 1
    assert received[0]["norenordno"] == "ORD001"
    assert received[0]["status"] == "OPEN"


def test_dispatch_om_full_fill():
    received = []
    msg = {
        "t": "om",
        "norenordno": "ORD002",
        "status": "COMPLETE",
        "reporttype": "Fill",
        "fillshares": "65",
        "avgprc": "158.75",
        "rejreason": "",
    }
    _dispatch(msg, on_om=received.append)
    assert received[0]["fillshares"] == "65"
    assert received[0]["avgprc"] == "158.75"


def test_dispatch_om_reject():
    received = []
    msg = {"t": "om", "norenordno": "ORD003", "status": "REJECTED",
           "reporttype": "Rejected", "rejreason": "RMS limit exceeded",
           "fillshares": "0", "avgprc": "0"}
    _dispatch(msg, on_om=received.append)
    assert received[0]["rejreason"] == "RMS limit exceeded"


def test_dispatch_ck_does_not_call_on_om():
    """Connection ack 't'=='ck' must NOT call on_om."""
    received = []
    msg = {"t": "ck", "s": "OK"}
    _dispatch(msg, on_om=received.append)
    assert received == []


def test_dispatch_ck_with_bad_stat_does_not_raise():
    """Even a bad ck should not raise — just log."""
    _dispatch({"t": "ck", "s": "FAILED"}, on_om=lambda m: None)


def test_dispatch_tick_dk_calls_on_tick():
    """'dk' depth tick → on_tick called."""
    ticks = []
    msg = {"t": "dk", "tk": "43215", "lp": "158.5"}
    _dispatch(msg, on_om=lambda m: None, on_tick=ticks.append)
    assert len(ticks) == 1
    assert ticks[0]["tk"] == "43215"


def test_dispatch_tf_calls_on_tick():
    """'tf' touch-line tick → on_tick called."""
    ticks = []
    msg = {"t": "tf", "tk": "43215", "lp": "160.0"}
    _dispatch(msg, on_om=lambda m: None, on_tick=ticks.append)
    assert ticks[0]["lp"] == "160.0"


def test_dispatch_tick_without_on_tick_does_not_raise():
    """Tick message with on_tick=None must not raise."""
    _dispatch({"t": "dk", "tk": "1"}, on_om=lambda m: None, on_tick=None)


def test_dispatch_unknown_type_ignored():
    """Unknown 't' value must not call on_om and must not raise."""
    received = []
    _dispatch({"t": "unknown_type", "data": "x"}, on_om=received.append)
    assert received == []


def test_dispatch_missing_t_ignored():
    """Message without 't' key must not raise."""
    received = []
    _dispatch({"no_t": True}, on_om=received.append)
    assert received == []


# ---------------------------------------------------------------------------
# Protocol conformance — async method signatures
# ---------------------------------------------------------------------------

def test_flattrade_client_has_all_protocol_methods():
    """FlattradeClient must have all methods required by BrokerClient Protocol."""
    import asyncio
    client = _client()
    assert asyncio.iscoroutinefunction(client.place_order)
    assert asyncio.iscoroutinefunction(client.cancel_order)
    assert asyncio.iscoroutinefunction(client.modify_order)
    assert asyncio.iscoroutinefunction(client.order_book)
    assert asyncio.iscoroutinefunction(client.position_book)
    assert asyncio.iscoroutinefunction(client.limits)
    assert asyncio.iscoroutinefunction(client.search_scrip)
