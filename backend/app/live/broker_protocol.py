"""Broker-agnostic order contracts + the BrokerClient Protocol. The ONLY real
order-placing implementation in the L0-L2 plan is MockNoren; FlattradeClient's
order methods stay untested-against-real until L3."""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Protocol

ORDER_STATES = ("INTENT", "SUBMITTED", "ACKED", "OPEN", "TRIGGER_PENDING",
                "PARTIAL", "COMPLETE", "REJECTED", "CANCELED")
ALLOWED_PRCTYP = ("LMT", "SL-LMT")     # Flattrade API: market/CO/BO/IOC blocked
# NOTE: MKT is INTENTIONALLY excluded here. This is the strict L1/L2 broker-submission
# gate (validate_jdata) — no L1/L2 producer ever builds a MKT intent, and keeping it strict
# preserves defense-in-depth. The NEW exchange-aware choke-point (order_builder.
# validate_and_build) supports MARKET (prc=0) via its own _CHOKE_PRCTYP allow-list; the
# live-order-page executor trusts the choke-point's validated children rather than re-running
# this legacy gate.
ALLOWED_PRD = ("I", "M")
ALLOWED_RET = ("DAY",)


def _num_str(v: Any) -> str:
    f = float(v)
    return str(int(f)) if f == int(f) else str(f)


@dataclass
class OrderIntent:
    client_order_id: str
    trantype: str          # B / S
    prctyp: str            # LMT / SL-LMT
    exch: str              # NFO / BFO
    tsym: str
    qty: int               # units = lots * lot_size
    prc: float
    prd: str = "I"
    ret: str = "DAY"
    trgprc: Optional[float] = None
    remarks: Optional[str] = None

    def to_jdata(self, *, uid: str, actid: str) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "ordersource": "API", "uid": uid, "actid": actid,
            "trantype": self.trantype, "prd": self.prd, "exch": self.exch,
            "tsym": self.tsym, "qty": _num_str(self.qty), "dscqty": "0",
            "prctyp": self.prctyp, "prc": _num_str(self.prc), "ret": self.ret,
        }
        if self.trgprc is not None:
            d["trgprc"] = _num_str(self.trgprc)
        if self.remarks:
            d["remarks"] = self.remarks
        return d


@dataclass
class OrderResult:
    ok: bool
    norenordno: Optional[str] = None
    rejreason: Optional[str] = None
    raw: Dict[str, Any] = field(default_factory=dict)


class BrokerClient(Protocol):
    async def place_order(self, intent: OrderIntent) -> OrderResult: ...
    async def cancel_order(self, norenordno: str) -> OrderResult: ...
    async def modify_order(self, norenordno: str, *, prc: float, trgprc: Optional[float] = None) -> OrderResult: ...
    async def order_book(self) -> List[Dict[str, Any]]: ...
    async def position_book(self) -> List[Dict[str, Any]]: ...
    async def limits(self) -> Dict[str, Any]: ...
    async def search_scrip(self, exch: str, text: str) -> List[Dict[str, Any]]: ...
