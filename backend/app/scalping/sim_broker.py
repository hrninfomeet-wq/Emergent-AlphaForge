"""Deterministic execution simulator for the scalper (paper + replay). Seeded; no wall clock.

Execution model (docs/scalping/04-execution-and-protection.md §6):
* An order sent at t arrives at the exchange at t + latency (default 140 ms = measured p50 of the
  21 real Flattrade PlaceOrder round-trips, 65-320 ms). Its FIRST match is against the FIRST snapshot
  ingested at/after arrival — never the quote the decision was made on (the feed is ~1 Hz).
* First match = taker: walk the 5-level book on the opposite side, taking at most ``depth_take_ratio``
  of each displayed level (others compete for the same liquidity). Each snapshot is consumed at most
  once per order (a snapshot repeated because no new tick arrived adds no liquidity).
* Remainder RESTS at its limit. A resting order fills only at ITS OWN limit price, when a later snapshot
  shows the opposite side crossing it (an incoming aggressor trades at the resting price), up to the
  crossing quantity x ``depth_take_ratio`` -> partial fills.
* NO passive fills by default (a trade printing through the resting price does not fill it). M5
  measured that passive fills are adversely selected; optimistic fills would flatter results.
* Exchange LPP: a limit beyond reference +/- max(40 %, Rs 20) is rejected; reference = the CURRENT
  market quote's LTP (``set_reference``), not a quote cached from an earlier order.
* Cancels arrive after ``cancel_latency_ms``; fills before arrival stand (the cancel/fill race).
* Fault injection (seeded): definite rejects, indeterminate submits (order exists or not, 50/50),
  dropped and duplicated order updates. ``reconcile()`` always returns broker truth.
Events are emitted in the Noren ``om`` shape the engine consumes (remarks = client order id).
"""
from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from app.scalping.engine import Action, lpp_bounds
from app.scalping.market import Quote


@dataclass
class SimParams:
    ack_latency_ms: int = 140
    exchange_latency_ms: int = 140
    cancel_latency_ms: int = 140
    depth_take_ratio: float = 0.5
    allow_passive_fills: bool = False
    reject_prob: float = 0.0
    indeterminate_prob: float = 0.0
    drop_event_prob: float = 0.0
    duplicate_event_prob: float = 0.0
    seed: int = 7


@dataclass
class SubmitResult:
    ok: bool
    ack_ms: int
    norenordno: Optional[str] = None
    reject_reason: Optional[str] = None
    indeterminate: bool = False


class SimBroker:
    def __init__(self, params: Optional[SimParams] = None):
        self.p = params or SimParams()
        self.rng = random.Random(self.p.seed)
        self._n = 0
        self.orders: Dict[str, Dict[str, Any]] = {}       # by cid
        self.net_qty: Dict[str, int] = {}                 # by instrument_key
        self._pending: List[Tuple[int, Dict[str, Any]]] = []   # (deliver_at_ms, om)
        self.last_quote: Dict[str, Quote] = {}
        self.depth: Dict[str, List[dict]] = {}
        self.stats = {"submits": 0, "rejects": 0, "lpp_rejects": 0, "indeterminate": 0,
                      "dropped_events": 0, "dup_events": 0, "fills": 0, "partial_fill_events": 0}

    # ------------------------------------------------------------------ market input
    def on_quote(self, q: Quote, depth: Optional[List[dict]] = None) -> None:
        """Feed one snapshot (in ingest order). Matches working orders on that contract immediately."""
        self.last_quote[q.instrument_key] = q
        if depth is not None:
            self.depth[q.instrument_key] = depth
        for o in self.orders.values():
            if o["status"] != "OPEN" or o["key"] != q.instrument_key:
                continue
            if o["cancel_at_ms"] is not None and q.ingest_ms >= o["cancel_at_ms"]:
                self._cancel_now(o)
                continue
            if q.ingest_ms < o["arrive_ms"] or q.ingest_ms <= o["last_match_ingest"]:
                continue                                   # not yet at the exchange / already consumed
            o["last_match_ingest"] = q.ingest_ms
            self._match(o, q, q.ingest_ms)

    def ref_quote(self, key: str) -> Optional[Quote]:
        return self._ref.get(key) if hasattr(self, "_ref") else None

    def set_reference(self, quotes: Dict[str, Quote]) -> None:
        """Latest market quotes, used ONLY for the LPP check at submission (not for matching)."""
        self._ref = quotes

    def _levels(self, key: str, side: str) -> List[Tuple[float, int]]:
        """Opposite-side levels to consume: asks for a buy, bids for a sell."""
        q = self.last_quote.get(key)
        if q is None:
            return []
        d = self.depth.get(key) or []
        if side == "B":   # we buy: consume asks
            lv = [(float(l["ask_price"]), int(l.get("ask_quantity") or 0)) for l in d if l.get("ask_price")]
            return lv or [(q.ask, q.ask_qty)]
        lv = [(float(l["bid_price"]), int(l.get("bid_quantity") or 0)) for l in d if l.get("bid_price")]
        return lv or [(q.bid, q.bid_qty)]

    def orders_keys(self) -> set:
        return {o["key"] for o in self.orders.values() if o["status"] == "OPEN"}

    # ------------------------------------------------------------------ order entry
    def submit(self, a: Action, now_ms: int) -> SubmitResult:
        assert a.kind == "place" and a.cid
        self.stats["submits"] += 1
        ack_ms = now_ms + self.p.ack_latency_ms
        r = self.rng.random()
        if r < self.p.reject_prob:
            self.stats["rejects"] += 1
            return SubmitResult(False, ack_ms, reject_reason="RMS:Margin Exceeds (simulated)")
        indeterminate = self.rng.random() < self.p.indeterminate_prob
        exists = (not indeterminate) or (self.rng.random() < 0.5)
        self._n += 1
        nord = f"SIM{self._n:08d}"
        if exists:
            q = self.ref_quote(a.instrument_key) or self.last_quote.get(a.instrument_key)
            if q is not None:
                lo, hi = lpp_bounds(q.ltp or q.mid)
                if (a.side == "B" and a.price > hi) or (a.side == "S" and a.price < lo):
                    self.stats["lpp_rejects"] += 1
                    if indeterminate:
                        self.orders[a.cid] = self._doc(a, nord, now_ms, "REJECTED", reason="LPP")
                        self.stats["indeterminate"] += 1
                        return SubmitResult(False, ack_ms, indeterminate=True)
                    return SubmitResult(False, ack_ms, norenordno=nord,
                                        reject_reason=f"ORDER PRICE [{a.price}] IS BEYOND LPP LIMIT")
            self.orders[a.cid] = self._doc(a, nord, now_ms, "OPEN")
            # OPEN is reported when the order is at the exchange, not when the broker acknowledged it
            self._emit(self.orders[a.cid]["arrive_ms"], self._om(self.orders[a.cid]))
        if indeterminate:
            self.stats["indeterminate"] += 1
            return SubmitResult(False, ack_ms, indeterminate=True)
        return SubmitResult(True, ack_ms, norenordno=nord)

    def _doc(self, a: Action, nord: str, now_ms: int, status: str, reason: Optional[str] = None) -> Dict[str, Any]:
        return {"cid": a.cid, "norenordno": nord, "key": a.instrument_key, "side": a.side, "qty": int(a.qty),
                "price": float(a.price), "filled": 0, "cost": 0.0, "status": status, "rejreason": reason,
                "arrive_ms": now_ms + self.p.exchange_latency_ms, "cancel_at_ms": None, "sent_ms": now_ms,
                "last_match_ingest": -1, "tried": False}

    def cancel(self, cid: str, now_ms: int) -> bool:
        o = self.orders.get(cid)
        if o is None or o["status"] in ("COMPLETE", "CANCELED", "REJECTED"):
            return False
        o["cancel_at_ms"] = now_ms + self.p.cancel_latency_ms
        return True

    # ------------------------------------------------------------------ matching
    def _cancel_now(self, o: Dict[str, Any]) -> None:
        o["status"] = "CANCELED"
        self._emit(o["cancel_at_ms"], self._om(o))

    def advance(self, now_ms: int) -> List[Tuple[int, Dict[str, Any]]]:
        """Process cancels that have landed, then deliver due order events (matching happens in on_quote)."""
        for o in self.orders.values():
            if o["status"] == "OPEN" and o["cancel_at_ms"] is not None and now_ms >= o["cancel_at_ms"]:
                self._cancel_now(o)
        due = [(t, om) for t, om in self._pending if t <= now_ms]
        self._pending = [(t, om) for t, om in self._pending if t > now_ms]
        out: List[Tuple[int, Dict[str, Any]]] = []
        for t, om in sorted(due, key=lambda x: x[0]):
            if self.rng.random() < self.p.drop_event_prob:
                self.stats["dropped_events"] += 1
                continue
            out.append((t, om))
            if self.rng.random() < self.p.duplicate_event_prob:
                self.stats["dup_events"] += 1
                out.append((t, dict(om)))
        return out

    def _match(self, o: Dict[str, Any], q: Quote, now_ms: int) -> None:
        remaining = o["qty"] - o["filled"]
        if remaining <= 0:
            return
        if o["cancel_at_ms"] is not None and q.ingest_ms >= o["cancel_at_ms"]:
            return
        took = 0
        cost = 0.0
        resting = o["tried"]
        o["tried"] = True
        for px, avail in self._levels(o["key"], o["side"]):
            marketable = px <= o["price"] if o["side"] == "B" else px >= o["price"]
            if not marketable:
                break
            n = min(remaining - took, int(avail * self.p.depth_take_ratio))
            if n <= 0:
                continue
            took += n
            # taker on arrival pays the displayed level; a RESTING order trades at its own limit
            cost += n * (o["price"] if resting else px)
            if took >= remaining:
                break
        if took == 0 and resting and self.p.allow_passive_fills and q.ltp is not None:
            if (o["side"] == "B" and q.ltp < o["price"]) or (o["side"] == "S" and q.ltp > o["price"]):
                took, cost = remaining, remaining * o["price"]
        if took <= 0:
            return
        o["filled"] += took
        o["cost"] += cost
        self.stats["fills"] += 1
        sign = 1 if o["side"] == "B" else -1
        self.net_qty[o["key"]] = self.net_qty.get(o["key"], 0) + sign * took
        if o["filled"] >= o["qty"]:
            o["status"] = "COMPLETE"
        else:
            self.stats["partial_fill_events"] += 1
        self._emit(max(now_ms, o["arrive_ms"]), self._om(o))

    def _om(self, o: Dict[str, Any]) -> Dict[str, Any]:
        st = o["status"]
        if st == "OPEN" and 0 < o["filled"] < o["qty"]:
            st = "PARTIALLY_FILLED"
        return {"remarks": o["cid"], "norenordno": o["norenordno"], "status": st, "qty": o["qty"],
                "fillshares": o["filled"], "avgprc": round(o["cost"] / o["filled"], 4) if o["filled"] else None,
                "rejreason": o.get("rejreason")}

    def _emit(self, at_ms: int, om: Dict[str, Any]) -> None:
        self._pending.append((int(at_ms), om))

    # ------------------------------------------------------------------ truth
    def reconcile(self, key: Optional[str] = None) -> Tuple[List[Dict[str, Any]], Optional[int]]:
        oms = [self._om(o) for o in self.orders.values()]
        net = self.net_qty.get(key, 0) if key else sum(self.net_qty.values())
        return oms, net
