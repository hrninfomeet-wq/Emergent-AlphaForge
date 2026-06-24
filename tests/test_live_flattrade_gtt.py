"""Tests for FlattradeClient GTT/OCO transport methods.

The Noren GTT/OCO responses have real-money-critical quirks captured from the
official PiConnect PDF (chapters 1.13–1.20):

  * PlaceGTTOrder success  -> LIST: [{"stat":"Oi created","Al_id":"…"}]   (capital Al_id)
  * PlaceOCOOrder success  -> DICT: {"stat":"OI created","al_id":"…"}      (lowercase al_id)
  * Cancel* success        -> "stat":"Oi delete success" (NOT "Ok")
  * GetPendingGTTOrder      -> LIST of GTT rows (the GTT book)
  * Any failure            -> DICT: {"stat":"Not_Ok","emsg":"…"}

We monkeypatch the client's _post so no network is touched; the tests assert the
request the client BUILDS (route + jdata, incl. identity injection) and how it
PARSES each documented response shape.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.live.flattrade_client import FlattradeClient  # noqa: E402
from app.live.gtt import build_gtt_intent, build_oco_intent, LTP_BELOW  # noqa: E402


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


def _gtt_intent():
    return build_gtt_intent(
        exch="NFO", tsym="NIFTY26JUN26C25000", qty=65, trantype="S",
        ai_t=LTP_BELOW, d_trigger=40.05, prc_limit=39.95, prd="M",
        remarks="cid-1",
    )


def _oco_intent():
    return build_oco_intent(
        exch="NFO", tsym="NIFTY26JUN26C25000", qty=65, prd="M",
        sl_trigger=40.05, sl_limit=39.95, tp_trigger=120.0, tp_limit=119.95,
        remarks="cid-oco",
    )


# ===========================================================================
# place_gtt — single-leg
# ===========================================================================
@pytest.mark.asyncio
async def test_place_gtt_posts_to_right_route_with_identity():
    c = _client({"PlaceGTTOrder": [{"stat": "Oi created", "Al_id": "25062500000010"}]})
    res = await c.place_gtt(_gtt_intent())
    route, jdata = c._calls[-1]
    assert route == "PlaceGTTOrder"
    # single GTT is the FLAT documented form → identity is top-level only
    assert jdata["uid"] == "FZ001"
    assert jdata["actid"] == "FZ001"
    assert "place_order_params" not in jdata
    assert jdata["ai_t"] == "LTP_B_O"          # confirmed below-trigger alert type
    assert jdata["validity"] == "GTT"
    assert jdata["prc"]                          # flat order limit field
    assert res["ok"] is True
    assert res["al_id"] == "25062500000010"


@pytest.mark.asyncio
async def test_place_gtt_parses_list_response_capital_alid():
    c = _client({"PlaceGTTOrder": [{"request_time": "t", "stat": "Oi created", "Al_id": "999"}]})
    res = await c.place_gtt(_gtt_intent())
    assert res["ok"] is True
    assert res["al_id"] == "999"


@pytest.mark.asyncio
async def test_place_gtt_failure_response():
    c = _client({"PlaceGTTOrder": {"stat": "Not_Ok", "emsg": "Session Expired"}})
    res = await c.place_gtt(_gtt_intent())
    assert res["ok"] is False
    assert res["al_id"] is None
    assert "Session Expired" in (res["emsg"] or "")


# ===========================================================================
# place_oco — two-leg
# ===========================================================================
@pytest.mark.asyncio
async def test_place_oco_injects_identity_top_level_and_both_legs():
    c = _client({"PlaceOCOOrder": {"stat": "OI created", "al_id": "21100800000009"}})
    res = await c.place_oco(_oco_intent())
    route, jdata = c._calls[-1]
    assert route == "PlaceOCOOrder"
    assert jdata["uid"] == "FZ001"
    for key in ("place_order_params", "place_order_params_leg2"):
        assert jdata[key]["uid"] == "FZ001"
        assert jdata[key]["actid"] == "FZ001"
    assert res["ok"] is True
    assert res["al_id"] == "21100800000009"


@pytest.mark.asyncio
async def test_place_oco_does_not_mutate_caller_intent():
    intent = _oco_intent()
    c = _client({"PlaceOCOOrder": {"stat": "OI created", "al_id": "1"}})
    await c.place_oco(intent)
    # the caller's intent legs must stay identity-free (client works on a copy)
    assert "uid" not in intent["place_order_params"]
    assert "actid" not in intent["place_order_params_leg2"]
    assert "uid" not in intent


# ===========================================================================
# cancel_gtt / cancel_oco
# ===========================================================================
@pytest.mark.asyncio
async def test_cancel_gtt_posts_uid_and_alid():
    c = _client({"CancelGTTOrder": [{"stat": "Oi delete success", "Al_id": "25062500000010"}]})
    res = await c.cancel_gtt("25062500000010")
    route, jdata = c._calls[-1]
    assert route == "CancelGTTOrder"
    assert jdata == {"uid": "FZ001", "al_id": "25062500000010"}
    assert res["ok"] is True
    assert res["al_id"] == "25062500000010"


@pytest.mark.asyncio
async def test_cancel_oco_posts_to_oco_route():
    c = _client({"CancelOCOOrder": {"stat": "Oi delete success", "al_id": "21083000000040"}})
    res = await c.cancel_oco("21083000000040")
    route, jdata = c._calls[-1]
    assert route == "CancelOCOOrder"
    assert jdata == {"uid": "FZ001", "al_id": "21083000000040"}
    assert res["ok"] is True


@pytest.mark.asyncio
async def test_cancel_rejects_empty_alid_without_calling_broker():
    c = _client({"CancelGTTOrder": {"stat": "Ok"}})
    with pytest.raises(ValueError):
        await c.cancel_gtt("")
    assert c._calls == []  # never hit the broker


# ===========================================================================
# gtt_book (GetPendingGTTOrder) + enabled_gtts
# ===========================================================================
@pytest.mark.asyncio
async def test_gtt_book_returns_list():
    rows = [
        {"stat": "Ok", "ai_t": "LTP_A", "Al_id": "1", "tsym": "ACC-EQ", "d": "1900.00"},
        {"stat": "Ok", "ai_t": "LTP_B", "Al_id": "2", "tsym": "ACC-EQ", "d": "1200.00"},
    ]
    c = _client({"GetPendingGTTOrder": rows})
    got = await c.gtt_book()
    route, jdata = c._calls[-1]
    assert route == "GetPendingGTTOrder"
    assert jdata == {"uid": "FZ001"}
    assert got == rows


@pytest.mark.asyncio
async def test_gtt_book_empty_on_not_ok():
    c = _client({"GetPendingGTTOrder": {"stat": "Not_Ok", "emsg": "no data"}})
    got = await c.gtt_book()
    assert got == []


@pytest.mark.asyncio
async def test_enabled_gtts_extracts_ai_t_values():
    c = _client({"GetEnabledGTTs": {"stat": "Ok", "ai_ts": [{"ai_t": "ATP"}, {"ai_t": "LTP"}]}})
    got = await c.enabled_gtts()
    assert got == ["ATP", "LTP"]
