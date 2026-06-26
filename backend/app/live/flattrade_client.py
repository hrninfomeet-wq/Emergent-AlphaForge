"""Flattrade async client — satisfies the BrokerClient Protocol.

Transport: POST https://piconnect.flattrade.in/PiConnectAPI/<Route>
Body (form-encoded): jData=<json.dumps(jdata)>&jKey=<jKey>

All Noren responses carry a top-level "stat" field:
  "Ok"      → success; parse the response-specific fields
  otherwise → reject; "emsg" contains the reason

Order methods (place/cancel/modify) are implemented for Protocol conformance
but are host-tested only via request-building + response-parsing in this plan
(L0.4). Real-broker exercise deferred to L3.

WebSocket (order management stream):
  URL: wss://piconnect.flattrade.in/PiConnectWSAPI/
  On connect: send {"t": "c", "uid": uid, "actid": actid, "susertoken": jKey, "source": "API"}
  Messages: JSON with "t" field; "t"=="om" → dispatch to on_om callback.
"""
from __future__ import annotations

import json
import logging
import socket
from typing import Any, Callable, Dict, List, Optional

import httpx

from app.live._net import force_ipv4, ipv4_transport
from app.live.broker_protocol import OrderIntent, OrderResult

log = logging.getLogger(__name__)

_PICONNECT_BASE = "https://piconnect.flattrade.in/PiConnectAPI"
_PICONNECT_WS = "wss://piconnect.flattrade.in/PiConnectWSAPI/"


# ---------------------------------------------------------------------------
# FlattradeClient
# ---------------------------------------------------------------------------

class FlattradeClient:
    """Async Flattrade (Noren) client implementing the BrokerClient Protocol.

    Parameters
    ----------
    jKey:   Session token from flattrade_token.exchange_code_for_token.
    uid:    Flattrade user ID (e.g. "FT1234").
    actid:  Account ID — usually same as uid for retail single-account users.
    """

    def __init__(self, jKey: str, uid: str, actid: str) -> None:
        if not jKey:
            raise ValueError("jKey is required")
        self._jKey = jKey
        self._uid = uid
        self._actid = actid

    # ------------------------------------------------------------------
    # Internal transport
    # ------------------------------------------------------------------

    def _make_body(self, jdata: Dict[str, Any]) -> str:
        """Build form-encoded body: jData=<json>&jKey=<token>."""
        return f"jData={json.dumps(jdata)}&jKey={self._jKey}"

    async def _post(self, route: str, jdata: Dict[str, Any]) -> Dict[str, Any]:
        """POST to PiConnectAPI/<route>, parse JSON response.

        Returns the parsed dict. Raises RuntimeError if the HTTP call fails
        or stat != "Ok". Callers that need to inspect the raw response on
        "Not_Ok" should use _post_raw instead.
        """
        url = f"{_PICONNECT_BASE}/{route}"
        body = self._make_body(jdata)
        headers = {"Content-Type": "application/x-www-form-urlencoded"}

        async with httpx.AsyncClient(timeout=20.0, transport=ipv4_transport()) as client:
            resp = await client.post(url, content=body, headers=headers)

        if resp.status_code != 200:
            raise RuntimeError(
                f"Flattrade {route} HTTP {resp.status_code}: {resp.text[:300]}"
            )

        data = resp.json()
        return data

    async def _post_ok(self, route: str, jdata: Dict[str, Any]) -> Dict[str, Any]:
        """Like _post but raises RuntimeError if stat != 'Ok'."""
        data = await self._post(route, jdata)
        if data.get("stat") != "Ok":
            emsg = data.get("emsg", "unknown error")
            raise RuntimeError(f"Flattrade {route} rejected: {emsg}")
        return data

    # ------------------------------------------------------------------
    # Read methods
    # ------------------------------------------------------------------

    async def order_book(self) -> List[Dict[str, Any]]:
        """Return the current order book as a list of order dicts.

        Noren returns a list on success, or {"stat": "Not_Ok", ...} on empty/error.
        We return an empty list on stat != Ok.
        """
        jdata: Dict[str, Any] = {"uid": self._uid, "actid": self._actid}
        data = await self._post("OrderBook", jdata)
        if isinstance(data, list):
            return data
        if isinstance(data, dict) and data.get("stat") == "Ok":
            # Shouldn't happen but be defensive
            return [data]
        return []

    async def trade_book(self) -> List[Dict[str, Any]]:
        """Return the current trade book (filled orders) as a list of dicts."""
        jdata: Dict[str, Any] = {"uid": self._uid, "actid": self._actid}
        data = await self._post("TradeBook", jdata)
        if isinstance(data, list):
            return data
        if isinstance(data, dict) and data.get("stat") == "Ok":
            return [data]
        return []

    async def position_book(self) -> List[Dict[str, Any]]:
        """Return net positions as a list of position dicts."""
        jdata: Dict[str, Any] = {"uid": self._uid, "actid": self._actid}
        data = await self._post("PositionBook", jdata)
        if isinstance(data, list):
            return data
        if isinstance(data, dict) and data.get("stat") == "Ok":
            return [data]
        return []

    async def limits(self) -> Dict[str, Any]:
        """Return account limits/margin as a flat dict.

        Noren Limits response shape: {stat, cash, payin, marginused, ...}
        We return the full dict (empty dict on failure).
        """
        jdata: Dict[str, Any] = {"uid": self._uid, "actid": self._actid}
        data = await self._post("Limits", jdata)
        if isinstance(data, dict) and data.get("stat") == "Ok":
            return data
        return {}

    async def search_scrip(self, exch: str, text: str) -> List[Dict[str, Any]]:
        """Search for scrips matching text on an exchange.

        Noren SearchScrip response: list of scrip dicts with fields:
            tsym, token, ls (lot_size), strprc (strike), optt (CE/PE), exd (expiry)

        Returns an empty list on failure / no match.
        """
        jdata: Dict[str, Any] = {
            "uid": self._uid,
            "stext": text,
            "exch": exch,
        }
        data = await self._post("SearchScrip", jdata)
        if isinstance(data, dict):
            if data.get("stat") != "Ok":
                return []
            # Noren returns values in a "values" key on some responses
            values = data.get("values")
            if isinstance(values, list):
                return values
            return []
        if isinstance(data, list):
            return data
        return []

    async def single_order_history(self, norenordno: str) -> List[Dict[str, Any]]:
        """Return the order history for a single order (audit trail of state changes)."""
        jdata: Dict[str, Any] = {"uid": self._uid, "norenordno": norenordno}
        data = await self._post("SingleOrdHist", jdata)
        if isinstance(data, list):
            return data
        return []

    async def order_margin(
        self,
        *,
        exch: str,
        tsym: str,
        qty: Any,
        prc: Any,
        prd: str,
        trantype: str,
        prctyp: str,
        trgprc: Optional[float] = None,
    ) -> Dict[str, Any]:
        """GetOrderMargin (#8): broker margin pre-check for a prospective order.

        Returns the RAW response dict (including ``stat:"Not_Ok"``) so the caller
        can fail-CLOSED on a broker reject — we deliberately do NOT swallow a
        reject into {}.
        """
        jdata: Dict[str, Any] = {
            "uid": self._uid, "actid": self._actid, "exch": exch, "tsym": tsym,
            "qty": str(int(qty)), "prc": f"{float(prc):.2f}", "prd": prd,
            "trantype": trantype, "prctyp": prctyp,
        }
        if trgprc is not None:
            jdata["trgprc"] = f"{float(trgprc):.2f}"
        data = await self._post("GetOrderMargin", jdata)
        return data if isinstance(data, dict) else {}

    async def get_quotes(self, exch: str, token: Any) -> Dict[str, Any]:
        """GetQuotes (#54): fresh LTP + depth read (uid mandatory).

        Pure price read → returns {} on a non-Ok response.
        """
        data = await self._post("GetQuotes", {"uid": self._uid, "exch": exch, "token": str(token)})
        return data if isinstance(data, dict) and data.get("stat") == "Ok" else {}

    # ------------------------------------------------------------------
    # Order methods (Protocol-required; host-tested on request/response only)
    # ------------------------------------------------------------------

    async def place_order(self, intent: OrderIntent) -> OrderResult:
        """Place a new order. Returns OrderResult.

        Builds jData from intent.to_jdata(uid, actid). Parses Noren response:
          success: {stat:"Ok", norenordno:<id>}
          failure: {stat:<other>, emsg:<reason>}
        """
        jdata = intent.to_jdata(uid=self._uid, actid=self._actid)
        try:
            data = await self._post("PlaceOrder", jdata)
        except RuntimeError as exc:
            return OrderResult(ok=False, rejreason=str(exc), raw={})

        if data.get("stat") == "Ok":
            return OrderResult(
                ok=True,
                norenordno=data.get("norenordno"),
                raw=data,
            )
        return OrderResult(
            ok=False,
            rejreason=data.get("emsg", data.get("stat", "unknown")),
            raw=data,
        )

    async def cancel_order(self, norenordno: str) -> OrderResult:
        """Cancel a working order by norenordno."""
        jdata: Dict[str, Any] = {
            "uid": self._uid,
            "norenordno": norenordno,
        }
        try:
            data = await self._post("CancelOrder", jdata)
        except RuntimeError as exc:
            return OrderResult(ok=False, rejreason=str(exc), raw={})

        if data.get("stat") == "Ok":
            return OrderResult(ok=True, norenordno=norenordno, raw=data)
        return OrderResult(
            ok=False,
            rejreason=data.get("emsg", data.get("stat", "unknown")),
            raw=data,
        )

    async def modify_order(
        self,
        norenordno: str,
        *,
        prc: float,
        trgprc: Optional[float] = None,
    ) -> OrderResult:
        """Modify price (and optionally trigger price) of an existing order."""
        jdata: Dict[str, Any] = {
            "uid": self._uid,
            "norenordno": norenordno,
            "prc": str(int(prc)) if prc == int(prc) else str(prc),
        }
        if trgprc is not None:
            jdata["trgprc"] = str(int(trgprc)) if trgprc == int(trgprc) else str(trgprc)
        try:
            data = await self._post("ModifyOrder", jdata)
        except RuntimeError as exc:
            return OrderResult(ok=False, rejreason=str(exc), raw={})

        if data.get("stat") == "Ok":
            return OrderResult(ok=True, norenordno=norenordno, raw=data)
        return OrderResult(
            ok=False,
            rejreason=data.get("emsg", data.get("stat", "unknown")),
            raw=data,
        )

    # ------------------------------------------------------------------
    # GTT / OCO (the NRML PC-died disaster backstop)
    #
    # Schema confirmed against the PiConnect PDF (ch.1.13–1.20). The pure jdata
    # is built by app.live.gtt; this layer injects identity (uid/actid) and
    # parses the broker's response quirks (list-or-dict, Al_id/al_id casing,
    # "Oi created"/"OI created" success stats rather than "Ok").
    # ------------------------------------------------------------------

    def _inject_identity(self, intent: Dict[str, Any]) -> Dict[str, Any]:
        """Inject uid/actid at the top level AND into every place_order_params*
        leg block, working on a copy so the caller's intent is not mutated.

        Both the single GTT and the OCO are the WRAPPED form (place_order_params
        carries the order to fire), so the order identity must live inside each
        leg as well as at the top level.
        """
        jdata: Dict[str, Any] = {**intent, "uid": self._uid, "actid": self._actid}
        for leg_key in ("place_order_params", "place_order_params_leg2"):
            leg = jdata.get(leg_key)
            if isinstance(leg, dict):
                jdata[leg_key] = {**leg, "uid": self._uid, "actid": self._actid}
        return jdata

    async def place_gtt(self, intent: Dict[str, Any]) -> Dict[str, Any]:
        """Transmit a single-leg GTT (built by gtt.build_gtt_intent).

        Injects identity, POSTs PlaceGTTOrder, returns the parsed alert result
        {ok, al_id, stat, emsg, raw}.
        """
        return await self._post_alert("PlaceGTTOrder", self._inject_identity(intent))

    async def place_oco(self, intent: Dict[str, Any]) -> Dict[str, Any]:
        """Transmit a two-leg OCO (built by gtt.build_oco_intent).

        Injects identity into the top level + both leg blocks, POSTs
        PlaceOCOOrder, returns the parsed alert result.
        """
        return await self._post_alert("PlaceOCOOrder", self._inject_identity(intent))

    async def cancel_gtt(self, al_id: Any) -> Dict[str, Any]:
        """Cancel a single-leg GTT by alert id (POST CancelGTTOrder)."""
        from app.live.gtt import cancel_gtt_jdata  # local import to avoid cycle
        jdata = {"uid": self._uid, **cancel_gtt_jdata(al_id)}
        return await self._post_alert("CancelGTTOrder", jdata)

    async def cancel_oco(self, al_id: Any) -> Dict[str, Any]:
        """Cancel a two-leg OCO by alert id (POST CancelOCOOrder)."""
        from app.live.gtt import cancel_gtt_jdata  # local import to avoid cycle
        jdata = {"uid": self._uid, **cancel_gtt_jdata(al_id)}
        return await self._post_alert("CancelOCOOrder", jdata)

    async def gtt_book(self) -> List[Dict[str, Any]]:
        """Return the pending GTT/OCO book (POST GetPendingGTTOrder).

        Returns a list of GTT rows on success, or an empty list on stat != Ok /
        any non-list payload.
        """
        data = await self._post("GetPendingGTTOrder", {"uid": self._uid})
        if isinstance(data, list):
            return data
        if isinstance(data, dict) and data.get("stat") == "Ok":
            return [data]
        return []

    async def enabled_gtts(self) -> List[str]:
        """Return the alert types this account supports (POST GetEnabledGTTs)."""
        data = await self._post("GetEnabledGTTs", {"uid": self._uid})
        if isinstance(data, dict) and data.get("stat") == "Ok":
            return [
                row.get("ai_t")
                for row in data.get("ai_ts", [])
                if isinstance(row, dict) and row.get("ai_t")
            ]
        return []

    async def _post_alert(self, route: str, jdata: Dict[str, Any]) -> Dict[str, Any]:
        """POST a GTT/OCO place/cancel and parse the alert response.

        Handles the documented response quirks: success may be a single-element
        LIST or a DICT; the alert id is keyed ``al_id`` OR ``Al_id``; the success
        ``stat`` is "Oi created"/"OI created"/"Oi delete success" (NOT "Ok").
        Returns {ok, al_id, stat, emsg, raw}. ``ok`` requires a non-empty al_id
        and a stat that is not "Not_Ok".
        """
        try:
            data = await self._post(route, jdata)
        except RuntimeError as exc:
            return {"ok": False, "al_id": None, "stat": None, "emsg": str(exc), "raw": {}}
        return _parse_alert_response(data)

    # ------------------------------------------------------------------
    # WebSocket: order management stream
    # ------------------------------------------------------------------

    async def start_order_ws(
        self,
        on_om: Callable[[Dict[str, Any]], None],
        on_tick: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> None:
        """Connect to the Flattrade order-management WebSocket and dispatch messages.

        On connect: send an auth packet with susertoken=jKey.
        On message: parse JSON; if "t"=="om" call on_om(msg); other types can
        optionally be routed via on_tick.

        NOTE: This is a long-running coroutine. In L0 the WS connect is an
        integration concern; host tests exercise _dispatch() only (pure function).
        """
        import websockets  # type: ignore  # noqa: F401 — only imported when WS is started

        auth_packet = json.dumps({
            "t": "c",
            "uid": self._uid,
            "actid": self._actid,
            "susertoken": self._jKey,
            "source": "API",
        })

        # Force IPv4: Flattrade whitelists a static IPv4 address; a dual-stack
        # host may resolve the hostname to an AAAA record and egress over IPv6,
        # causing 'Invalid Input : INVALID_IP'.  Passing family=AF_INET
        # restricts DNS resolution + socket creation to IPv4 only.
        ws_kwargs = {"family": socket.AF_INET} if force_ipv4() else {}
        async with websockets.connect(_PICONNECT_WS, **ws_kwargs) as ws:
            await ws.send(auth_packet)
            async for raw in ws:
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    log.warning(f"Flattrade WS: non-JSON message: {raw[:100]}")
                    continue
                _dispatch(msg, on_om=on_om, on_tick=on_tick)


# ---------------------------------------------------------------------------
# Pure GTT/OCO response parser — host-testable, no network
# ---------------------------------------------------------------------------

def _parse_alert_response(data: Any) -> Dict[str, Any]:
    """Normalize a Noren GTT/OCO place/cancel response to a flat result dict.

    Accepts the documented shapes:
      success LIST: [{"stat":"Oi created","Al_id":"…"}]
      success DICT: {"stat":"OI created","al_id":"…"}
      failure DICT: {"stat":"Not_Ok","emsg":"…"}

    Returns {ok, al_id, stat, emsg, raw}. ``ok`` is True iff a non-empty alert id
    is present AND stat is not "Not_Ok".
    """
    rec = data[0] if isinstance(data, list) and data else data
    if not isinstance(rec, dict):
        return {"ok": False, "al_id": None, "stat": None,
                "emsg": "unexpected GTT response", "raw": data}
    stat = rec.get("stat") or rec.get("Stat")
    raw_alid = rec.get("al_id") or rec.get("Al_id") or rec.get("AL_id")
    al_id = str(raw_alid).strip() if raw_alid not in (None, "") else None
    emsg = rec.get("emsg")
    ok = al_id is not None and str(stat).strip().lower() != "not_ok"
    return {"ok": ok, "al_id": al_id, "stat": stat, "emsg": emsg, "raw": data}


# ---------------------------------------------------------------------------
# Pure dispatch helper — host-testable with no WS connection
# ---------------------------------------------------------------------------

def _dispatch(
    msg: Dict[str, Any],
    *,
    on_om: Callable[[Dict[str, Any]], None],
    on_tick: Optional[Callable[[Dict[str, Any]], None]] = None,
) -> None:
    """Route a parsed WS message to the appropriate callback.

    Noren "t" field values:
      "ck"  — connection acknowledgement (auth response)
      "om"  — order management update → on_om(msg)
      "dk"  — depth/quote tick → on_tick(msg) if provided
      "tf"  — touch-line feed tick → on_tick(msg) if provided

    Unknown types are logged and ignored.
    """
    t = msg.get("t")
    if t == "om":
        on_om(msg)
    elif t in ("dk", "tf"):
        if on_tick is not None:
            on_tick(msg)
    elif t == "ck":
        stat = msg.get("s", "")
        if stat == "OK":
            log.info("Flattrade WS: auth acknowledged")
        else:
            log.warning(f"Flattrade WS: auth response: {msg}")
    else:
        log.debug(f"Flattrade WS: unhandled message type {t!r}: {str(msg)[:120]}")
