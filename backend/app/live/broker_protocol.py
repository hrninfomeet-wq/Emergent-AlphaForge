"""Broker-agnostic order contracts + the BrokerClient Protocol. The ONLY real
order-placing implementation in the L0-L2 plan is MockNoren; FlattradeClient's
order methods stay untested-against-real until L3."""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Protocol

class BrokerReadError(RuntimeError):
    """Raised by a BrokerClient read method (``order_book`` / ``position_book`` /
    ``trade_book`` / ``limits``) when the broker returns an authenticated-session
    FAILURE — most importantly the in-band daily-token expiry
    ``{"stat":"Not_Ok","emsg":"Session Expired : Invalid Session Key"}``.

    CRITICAL CONTRACT — read this before catching it:

    A genuinely EMPTY book is NOT an error. Noren signals empty with an ``emsg``
    containing ``"no data"`` (a flat account's PositionBook returns
    ``{"stat":"Not_Ok","emsg":'Error Occurred : 5 "no data"'}``); the readers
    return ``[]`` / ``{}`` for that case and do NOT raise.

    This exception therefore means the read could not be TRUSTED. A caller must
    treat the result as UNKNOWN and must NEVER infer "the account is flat" or
    "there are no working orders" from it — that inference on a swallowed error
    is exactly the bug this type exists to prevent (kill switch reporting a false
    ALL FLAT on an expired token, auto-square marking a session "squared", the
    guard un-watching a live position).

    Subclasses ``RuntimeError`` so pre-existing broad ``except RuntimeError`` /
    ``except Exception`` handlers keep degrading safely (they just must not treat
    the degraded path as "flat").
    """

    def __init__(self, emsg: str, *, route: str = "") -> None:
        self.emsg = emsg or ""
        self.route = route
        super().__init__(f"Flattrade {route} read failed: {emsg}" if route else str(emsg))

    @property
    def is_session_expired(self) -> bool:
        """True when the broker rejected with a session/auth failure (daily OAuth
        token expired or invalid) — the actionable 'reconnect Flattrade' case."""
        low = self.emsg.lower()
        return "session expired" in low or "invalid session" in low


# The user-facing remediation shown wherever a BrokerReadError bubbles to the UI.
TOKEN_EXPIRED_HINT = "token expired — reconnect Flattrade"


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
