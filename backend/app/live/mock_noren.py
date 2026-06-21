"""Deterministic in-memory mock of the Flattrade (Noren) BrokerClient.

Used exclusively in host tests (no network). Satisfies the BrokerClient Protocol
defined in broker_protocol.py. All async methods; no I/O.

Noren om event shape (§4 of the spec):
    {
        "norenordno": str,       # broker order number
        "status": str,           # e.g. "OPEN", "COMPLETE", "REJECTED", "CANCELED"
        "reporttype": str,       # "Fill", "Rejected", "Canceled", "New", "Trigger Pending"
        "fillshares": str,       # cumulative filled qty (string)
        "avgprc": str,           # average fill price (string)
        "rejreason": str,        # non-empty on REJECTED
    }
"""
from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

from app.live.broker_protocol import OrderIntent, OrderResult


def make_om(
    norenordno: str,
    status: str,
    reporttype: str,
    fillshares: str = "0",
    avgprc: str = "0",
    rejreason: str = "",
) -> Dict[str, Any]:
    """Build a well-formed Noren order-management event dict."""
    return {
        "norenordno": norenordno,
        "status": status,
        "reporttype": reporttype,
        "fillshares": fillshares,
        "avgprc": avgprc,
        "rejreason": rejreason,
    }


class MockNoren:
    """Deterministic in-memory BrokerClient implementation for host tests.

    Usage
    -----
    client = MockNoren()

    # Inject a scripted reject for the NEXT place_order call:
    client.script_reject("RMS limit exceeded")

    # Drive the om stream:
    client.emit_om(make_om("MOCK1", "OPEN", "New"))

    # Register a callback for om events:
    def on_om(event): ...
    client = MockNoren(om_callback=on_om)

    # Pre-load fixtures:
    client = MockNoren(
        position_book_data=[...],
        limits_data={...},
        search_scrip_data={"NFO": [...]},
    )
    """

    def __init__(
        self,
        *,
        order_book_data: Optional[List[Dict[str, Any]]] = None,
        position_book_data: Optional[List[Dict[str, Any]]] = None,
        limits_data: Optional[Dict[str, Any]] = None,
        search_scrip_data: Optional[Dict[str, List[Dict[str, Any]]]] = None,
        om_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> None:
        # Internal order store: norenordno -> order dict
        self._orders: Dict[str, Dict[str, Any]] = {}
        self._order_counter: int = 0

        # Scripted next-reject: if set, the next place_order returns ok=False
        self._next_reject_reason: Optional[str] = None

        # Injected fixture data
        self._position_book_data: List[Dict[str, Any]] = position_book_data or []
        self._limits_data: Dict[str, Any] = limits_data or {}
        # search_scrip_data keyed by (exch, text) or just exch; we support both
        self._search_scrip_data: Dict[str, List[Dict[str, Any]]] = search_scrip_data or {}

        # om event log and optional callback
        self.om_events: List[Dict[str, Any]] = []
        self._om_callback = om_callback

    # ------------------------------------------------------------------
    # Script helpers
    # ------------------------------------------------------------------

    def script_reject(self, rejreason: str) -> None:
        """Make the next place_order call return ok=False with this reason."""
        self._next_reject_reason = rejreason

    def set_position_book(self, data: List[Dict[str, Any]]) -> None:
        self._position_book_data = data

    def set_limits(self, data: Dict[str, Any]) -> None:
        self._limits_data = data

    def set_search_scrip(self, exch: str, rows: List[Dict[str, Any]]) -> None:
        """Set the rows returned by search_scrip for a given exchange."""
        self._search_scrip_data[exch] = rows

    def set_om_callback(self, cb: Callable[[Dict[str, Any]], None]) -> None:
        self._om_callback = cb

    # ------------------------------------------------------------------
    # BrokerClient Protocol — async methods
    # ------------------------------------------------------------------

    async def place_order(self, intent: OrderIntent) -> OrderResult:
        """Append a new order to the in-memory book.

        If a scripted reject is queued, return ok=False immediately without
        adding the order to the book.
        """
        if self._next_reject_reason is not None:
            reason = self._next_reject_reason
            self._next_reject_reason = None
            return OrderResult(ok=False, rejreason=reason, raw={"stat": "Not_Ok", "emsg": reason})

        self._order_counter += 1
        norenordno = f"MOCK{self._order_counter}"
        order_doc: Dict[str, Any] = {
            "norenordno": norenordno,
            "client_order_id": intent.client_order_id,
            "trantype": intent.trantype,
            "prctyp": intent.prctyp,
            "exch": intent.exch,
            "tsym": intent.tsym,
            "qty": intent.qty,
            "prc": intent.prc,
            "trgprc": intent.trgprc,
            "prd": intent.prd,
            "ret": intent.ret,
            "remarks": intent.remarks,
            "status": "OPEN",
            "fillshares": "0",
            "avgprc": "0",
        }
        self._orders[norenordno] = order_doc
        return OrderResult(ok=True, norenordno=norenordno, raw={"stat": "Ok", "norenordno": norenordno})

    async def cancel_order(self, norenordno: str) -> OrderResult:
        """Mark an order as CANCELED in the in-memory book."""
        if norenordno not in self._orders:
            return OrderResult(ok=False, rejreason=f"order {norenordno} not found",
                               raw={"stat": "Not_Ok", "emsg": f"order {norenordno} not found"})
        self._orders[norenordno]["status"] = "CANCELED"
        return OrderResult(ok=True, norenordno=norenordno, raw={"stat": "Ok", "norenordno": norenordno})

    async def modify_order(
        self, norenordno: str, *, prc: float, trgprc: Optional[float] = None
    ) -> OrderResult:
        """Mutate price/trigger of an existing order."""
        if norenordno not in self._orders:
            return OrderResult(ok=False, rejreason=f"order {norenordno} not found",
                               raw={"stat": "Not_Ok", "emsg": f"order {norenordno} not found"})
        self._orders[norenordno]["prc"] = prc
        if trgprc is not None:
            self._orders[norenordno]["trgprc"] = trgprc
        return OrderResult(ok=True, norenordno=norenordno, raw={"stat": "Ok", "norenordno": norenordno})

    async def order_book(self) -> List[Dict[str, Any]]:
        """Return a snapshot of all in-memory orders."""
        return list(self._orders.values())

    async def position_book(self) -> List[Dict[str, Any]]:
        """Return injected position fixture."""
        return list(self._position_book_data)

    async def limits(self) -> Dict[str, Any]:
        """Return injected limits fixture."""
        return dict(self._limits_data)

    async def search_scrip(self, exch: str, text: str) -> List[Dict[str, Any]]:
        """Return injected scrip rows for the given exchange.

        Looks up self._search_scrip_data by (exch, text) first, then by exch alone,
        then returns an empty list. This covers both exact-query keying and
        exchange-level fixture injection.
        """
        key = (exch, text)
        if key in self._search_scrip_data:
            return list(self._search_scrip_data[key])
        if exch in self._search_scrip_data:
            return list(self._search_scrip_data[exch])
        return []

    # ------------------------------------------------------------------
    # om stream helper
    # ------------------------------------------------------------------

    def emit_om(self, event: Dict[str, Any]) -> None:
        """Append an om event to om_events and invoke the registered callback."""
        self.om_events.append(event)
        if self._om_callback is not None:
            self._om_callback(event)
