"""A non-200 on a GTT/OCO PLACEMENT is indeterminate, never a clean reject.

``_post_alert`` converted ``_post``'s non-200 RuntimeError into
``{"ok": False}`` — the same bug class 0068f4c closed for PlaceOrder. A gateway
5xx (or a read timeout) can land AFTER the OMS created the alert, so "not
placed" may be false: the alert rests at the broker with no ``al_id`` anywhere
in AlphaForge, nothing ever cancels it, and when its position closes the
resting SELL legs can later fire against a flat account (a naked short).

The contract pinned here:

* placement non-200 / transport failure → resolve against GetPendingGTTOrder
  (by the remarks tag, else by tsym + triggers). Exactly one match → adopt its
  al_id (``ok=True``). Anything else → ``indeterminate=True``, never a plain
  reject.
* a 200 carrying ``stat:"Not_Ok"`` is a KNOWN reject — no book read.
* cancel non-200 stays fail-safe: ``ok=False`` (unknown ≠ cancelled), flagged
  indeterminate, and never "resolved" into a cancellation.
* the auto_live arm marks an unresolved placement on the guard entry, and the
  guard cancels any alert carrying that tag once the position leaves it.
"""
from __future__ import annotations

import functools
import sys
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import AsyncMock, patch

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT))

from app.live.flattrade_client import FlattradeClient  # noqa: E402
from app.live.gtt import LTP_BELOW, build_gtt_intent, build_oco_intent  # noqa: E402

_TSYM = "NIFTY26OCT26C25000"
_TAG = "oco:26100700012345"
_HTTP_502 = RuntimeError("Flattrade PlaceOCOOrder HTTP 502: Bad Gateway")


def _client(scripted: Dict[str, Any]) -> FlattradeClient:
    """FlattradeClient whose _post returns / raises scripted[route]."""
    c = FlattradeClient(jKey="JK", uid="FZ001", actid="FZ001")
    calls: List[Any] = []

    async def fake_post(route, jdata):
        calls.append((route, jdata))
        if route not in scripted:
            raise AssertionError(f"unexpected route {route}")
        resp = scripted[route]
        if isinstance(resp, BaseException):
            raise resp
        return resp

    c._post = fake_post  # type: ignore[assignment]
    c._calls = calls     # type: ignore[attr-defined]
    return c


def _routes(c) -> List[str]:
    return [r for r, _ in c._calls]


def _oco(remarks=_TAG):
    return build_oco_intent(
        exch="NFO", tsym=_TSYM, qty=65, prd="M",
        sl_trigger=40.05, sl_limit=39.95, tp_trigger=120.0, tp_limit=119.95,
        remarks=remarks,
    )


def _oco_row(al_id, *, tsym=_TSYM, remarks=_TAG, x="40.05", y="120.00"):
    return {"stat": "Ok", "ai_t": "LMT_BOS_O", "Al_id": al_id, "tsym": tsym,
            "exch": "NFO", "Remarks": remarks,
            "oivariable": [{"var_name": "x", "d": x}, {"var_name": "y", "d": y}]}


# ===========================================================================
# Client: placement non-200 is resolved against the GTT book
# ===========================================================================
@pytest.mark.asyncio
async def test_place_oco_non200_adopts_the_alert_found_by_remarks():
    c = _client({
        "PlaceOCOOrder": _HTTP_502,
        "GetPendingGTTOrder": [
            _oco_row("26100700000001", remarks="oco:26100700099999"),  # another entry
            _oco_row("26100700000123"),                                # ours
        ],
    })
    res = await c.place_oco(_oco())
    assert res["ok"] is True, "the broker created it — reporting not-placed orphans it"
    assert res["al_id"] == "26100700000123"
    assert not res.get("indeterminate")
    assert _routes(c) == ["PlaceOCOOrder", "GetPendingGTTOrder"]


@pytest.mark.asyncio
async def test_place_oco_non200_matches_a_decorated_remarks_tag_on_a_token_boundary():
    """The broker decorates remarks ("LMT_BOS_O: oco:<n>: Ltp ..."); a longer
    order number that merely STARTS with ours is someone else's alert."""
    c = _client({
        "PlaceOCOOrder": _HTTP_502,
        "GetPendingGTTOrder": [
            _oco_row("AL_LONGER", remarks=_TAG + "9"),
            _oco_row("AL_OURS", remarks=f"LMT_BOS_O: {_TAG}: Ltp 61.95 is above 21.45"),
        ],
    })
    res = await c.place_oco(_oco())
    assert res["ok"] is True
    assert res["al_id"] == "AL_OURS"


@pytest.mark.asyncio
async def test_place_oco_non200_absent_from_book_is_indeterminate_not_a_reject():
    """Absent from an immediate book read is NOT proof: a gateway timeout can
    return before the OMS persists the alert."""
    c = _client({"PlaceOCOOrder": _HTTP_502, "GetPendingGTTOrder": []})
    res = await c.place_oco(_oco())
    assert res["ok"] is False
    assert res["al_id"] is None
    assert res["indeterminate"] is True
    assert "502" in (res["emsg"] or "")


@pytest.mark.asyncio
async def test_place_oco_non200_with_unreadable_book_is_indeterminate():
    c = _client({
        "PlaceOCOOrder": _HTTP_502,
        "GetPendingGTTOrder": RuntimeError("Flattrade GetPendingGTTOrder HTTP 503"),
    })
    res = await c.place_oco(_oco())
    assert res["ok"] is False
    assert res["indeterminate"] is True


@pytest.mark.asyncio
async def test_place_oco_non200_with_two_matching_rows_adopts_neither():
    c = _client({
        "PlaceOCOOrder": _HTTP_502,
        "GetPendingGTTOrder": [_oco_row("AL_A"), _oco_row("AL_B")],
    })
    res = await c.place_oco(_oco())
    assert res["ok"] is False
    assert res["al_id"] is None
    assert res["indeterminate"] is True


@pytest.mark.asyncio
async def test_place_oco_non200_without_remarks_matches_tsym_and_triggers():
    c = _client({
        "PlaceOCOOrder": _HTTP_502,
        "GetPendingGTTOrder": [
            _oco_row("AL_OTHER_SYM", tsym="NIFTY26OCT26P25000", remarks=""),
            _oco_row("AL_OTHER_TRIG", remarks="", x="35.00"),
            _oco_row("AL_OURS", remarks="", x="40.050000", y="120"),
        ],
    })
    res = await c.place_oco(_oco(remarks=None))
    assert res["ok"] is True
    assert res["al_id"] == "AL_OURS"


@pytest.mark.asyncio
async def test_place_gtt_non200_resolves_a_single_leg_by_tsym_and_trigger():
    intent = build_gtt_intent(
        exch="NFO", tsym=_TSYM, qty=65, trantype="S", ai_t=LTP_BELOW,
        d_trigger=40.05, prc_limit=39.95, prd="M",
    )
    c = _client({
        "PlaceGTTOrder": RuntimeError("Flattrade PlaceGTTOrder HTTP 504: Gateway Timeout"),
        "GetPendingGTTOrder": [
            {"Al_id": "AL_OTHER", "tsym": _TSYM, "ai_t": "LTP_B_O", "d": "38.00"},
            {"Al_id": "AL_OURS", "tsym": _TSYM, "ai_t": "LTP_B_O", "d": "40.05"},
        ],
    })
    res = await c.place_gtt(intent)
    assert res["ok"] is True
    assert res["al_id"] == "AL_OURS"


@pytest.mark.asyncio
async def test_place_oco_read_timeout_is_resolved_not_raised():
    """A timeout is the same unknown as a 5xx — the request may have landed."""
    c = _client({
        "PlaceOCOOrder": httpx.ReadTimeout("timed out"),
        "GetPendingGTTOrder": [_oco_row("AL_OURS")],
    })
    res = await c.place_oco(_oco())
    assert res["ok"] is True
    assert res["al_id"] == "AL_OURS"


@pytest.mark.asyncio
async def test_place_oco_200_not_ok_is_a_known_reject_with_no_book_read():
    c = _client({"PlaceOCOOrder": {"stat": "Not_Ok", "emsg": "Invalid Input"}})
    res = await c.place_oco(_oco())
    assert res["ok"] is False
    assert not res.get("indeterminate")
    assert _routes(c) == ["PlaceOCOOrder"]


# ===========================================================================
# Client: cancel non-200 stays fail-safe
# ===========================================================================
@pytest.mark.asyncio
async def test_cancel_oco_non200_is_unknown_never_cancelled():
    c = _client({
        "CancelOCOOrder": RuntimeError("Flattrade CancelOCOOrder HTTP 502"),
        "GetPendingGTTOrder": [],      # an absent row must NOT be read as "cancelled"
    })
    res = await c.cancel_oco("26100700000123")
    assert res["ok"] is False
    assert res["indeterminate"] is True
    assert _routes(c) == ["CancelOCOOrder"]


@pytest.mark.asyncio
async def test_cancel_gtt_non200_is_unknown_never_cancelled():
    c = _client({"CancelGTTOrder": RuntimeError("Flattrade CancelGTTOrder HTTP 500")})
    res = await c.cancel_gtt("26100700000123")
    assert res["ok"] is False
    assert res["indeterminate"] is True


# ===========================================================================
# auto_live arm: an unresolved placement is marked on the guard entry
# ===========================================================================
class _Intent:
    tsym = _TSYM
    exch = "NFO"
    qty = 65
    prd = "M"


class _Registry:
    def __init__(self):
        self.items: Dict[str, Dict[str, Any]] = {}

    def register(self, **kwargs):
        self.items[str(kwargs["key"])] = dict(kwargs)
        return self.items[str(kwargs["key"])]

    def get(self, key):
        return self.items.get(str(key))


def _plan():
    return {"levels": {"stop_pct": 30.0, "target_pct": 60.0, "stop_pts": None,
                       "target_pts": None, "trail": None},
            "spot_exit": None, "time_stop_minutes": None}


async def _arm_with(monkeypatch, client, ordno="26100700012345"):
    from app import live_deploy_context as ldc
    monkeypatch.setenv("LIVE_BROKER_OCO_ENABLED", "1")
    reg = _Registry()
    monkeypatch.setattr(ldc, "get_registry", lambda: reg)
    arm = functools.partial(ldc.arm_for, client=client, uid="U", actid="A")(
        _plan(), {"deployment_id": "dep-1"}, ref_ltp=100.0, catastrophe_stop_pct=48)
    al_id = await arm(_Intent(), ordno)
    return al_id, reg.get(ordno)


class _OcoResult:
    def __init__(self, result=None, exc=None):
        self._result, self._exc = result, exc
        self.sent: List[Dict[str, Any]] = []

    async def place_oco(self, intent):
        self.sent.append(intent)
        if self._exc is not None:
            raise self._exc
        return dict(self._result)


@pytest.mark.asyncio
async def test_arm_marks_an_indeterminate_oco_unresolved_on_the_guard_entry(monkeypatch):
    client = _OcoResult({"ok": False, "al_id": None, "indeterminate": True,
                         "emsg": "HTTP 502"})
    al_id, ent = await _arm_with(monkeypatch, client)
    assert al_id is None
    assert not ent.get("oco_al_id")
    assert ent["oco_unresolved_remarks"] == "oco:26100700012345"
    assert client.sent[0]["remarks"] == ent["oco_unresolved_remarks"]


@pytest.mark.asyncio
async def test_arm_marks_unresolved_when_place_oco_raises_after_sending(monkeypatch):
    client = _OcoResult(exc=RuntimeError("connection reset"))
    al_id, ent = await _arm_with(monkeypatch, client)
    assert al_id is None
    assert ent["oco_unresolved_remarks"] == "oco:26100700012345"


@pytest.mark.asyncio
async def test_arm_does_not_mark_a_known_reject(monkeypatch):
    client = _OcoResult({"ok": False, "al_id": None, "stat": "Not_Ok",
                         "emsg": "Invalid Input"})
    al_id, ent = await _arm_with(monkeypatch, client)
    assert al_id is None
    assert not ent.get("oco_unresolved_remarks")


@pytest.mark.asyncio
async def test_arm_adopts_a_book_resolved_al_id(monkeypatch):
    client = _OcoResult({"ok": True, "al_id": "AL_FROM_BOOK", "indeterminate": False})
    al_id, ent = await _arm_with(monkeypatch, client)
    assert al_id == "AL_FROM_BOOK"
    assert ent["oco_al_id"] == "AL_FROM_BOOK"
    assert not ent.get("oco_unresolved_remarks")


# ===========================================================================
# Guard: an unresolved OCO is cancelled when its position leaves the guard
# ===========================================================================
from tests.test_live_position_guard import (  # noqa: E402
    _NOW, _TSYM as _GTSYM, _Recorder, _aw, _pos, run,
)
from app.live.live_position_guard import (  # noqa: E402
    LiveMonitorRegistry, LivePositionGuard,
)
from app.live.live_sl_monitor import build_monitor_state  # noqa: E402


class _GttClient:
    def __init__(self, positions, book=None, book_error=None):
        self._positions = positions
        self.book = list(book or [])
        self.book_error = book_error
        self.book_reads = 0
        self.cancel_oco_calls: List[str] = []

    def set(self, positions):
        self._positions = positions

    async def position_book(self):
        return list(self._positions)

    async def gtt_book(self):
        self.book_reads += 1
        if self.book_error is not None:
            raise self.book_error
        return list(self.book)

    async def cancel_oco(self, al_id):
        self.cancel_oco_calls.append(al_id)
        return {"ok": True, "al_id": str(al_id)}


def _guard_with(client, *, unresolved=_TAG):
    reg = LiveMonitorRegistry()
    reg.register(key="ORD1", tsym=_GTSYM, exch="BFO", qty=20, prd="M",
                 entry_price=250.0, state=build_monitor_state(250.0, stop_pct=30))
    if unresolved:
        reg.get("ORD1")["oco_unresolved_remarks"] = unresolved
    g = LivePositionGuard(registry=reg, client_factory=lambda: _aw(client),
                          square_fn=_Recorder().square_fn, now_fn=lambda: _NOW)
    return reg, g


def _close_elsewhere(client, g):
    run(g._cycle())                         # held → seen filled
    client.set([_pos(netqty=0, lp=170.0)])
    run(g._cycle())
    run(g._cycle())                         # 2nd consecutive flat read


def test_confirmed_flat_cancels_the_unresolved_oco_found_by_its_tag():
    client = _GttClient([_pos(netqty=20, lp=250.0)], book=[
        {"al_id": "AL_OTHER", "tsym": _GTSYM, "remarks": "oco:26100700099999"},
        {"Al_id": "AL_OURS", "tsym": _GTSYM, "Remarks": _TAG},
    ])
    reg, g = _guard_with(client)
    _close_elsewhere(client, g)
    assert client.cancel_oco_calls == ["AL_OURS"], (
        "an untracked OCO left resting against a flat account can fire a naked short")
    assert len(reg) == 0


def test_unreadable_gtt_book_still_drops_the_entry_without_raising():
    client = _GttClient([_pos(netqty=20, lp=250.0)],
                        book_error=RuntimeError("GetPendingGTTOrder HTTP 503"))
    reg, g = _guard_with(client)
    _close_elsewhere(client, g)
    assert client.book_reads >= 1
    assert client.cancel_oco_calls == []
    assert len(reg) == 0


def test_no_unresolved_tag_spends_no_gtt_book_read():
    client = _GttClient([_pos(netqty=20, lp=250.0)], book=[
        {"Al_id": "AL_OURS", "tsym": _GTSYM, "Remarks": _TAG}])
    reg, g = _guard_with(client, unresolved=None)
    _close_elsewhere(client, g)
    assert client.book_reads == 0, "the GTT read budget is shared per API key"
    assert client.cancel_oco_calls == []


def test_age_out_cancels_the_unresolved_oco():
    client = _GttClient([], book=[{"Al_id": "AL_OURS", "tsym": _GTSYM, "Remarks": _TAG}])
    reg, g = _guard_with(client)
    entry = reg.get("ORD1")
    run(g._age_out(client, entry, cancel_entry=False))
    assert client.cancel_oco_calls == ["AL_OURS"]
    assert len(reg) == 0


# ===========================================================================
# Manual route: the operator sees "unknown", not "not placed"
# ===========================================================================
def test_manual_gtt_route_surfaces_indeterminate():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    import app.routers.live_broker as routes

    class _Client:
        async def place_oco(self, intent):
            return {"ok": False, "al_id": None, "indeterminate": True,
                    "stat": None, "emsg": "HTTP 502", "raw": {}}

    app = FastAPI()
    app.include_router(routes.api)
    with patch.object(routes, "_get_client", AsyncMock(return_value=_Client())):
        d = TestClient(app).post("/live-broker/gtt", json={
            "kind": "oco", "exch": "NFO", "tsym": _TSYM, "qty": 65, "prd": "M",
            "sl_trigger": 40.0, "sl_limit": 39.9, "tp_trigger": 120.0,
            "tp_limit": 119.9, "transmit": True}).json()
    assert d["placed"] is False
    assert d["indeterminate"] is True
