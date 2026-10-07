"""Deterministic, event-driven scalper engine — one instance per underlying, one position at a time.

The engine does no I/O. The caller feeds it events and executes the Actions it returns:

    market.on_tick(...) for every tick; once per second: market.on_second(now) then
    actions = engine.on_second(now, market)
    for each "place"  action -> send it, then engine.on_submit_result(cid, ...)
    for each "cancel" action -> send it, then engine.on_cancel_result(cid, ...)
    broker order updates     -> engine.on_order_event(om, now)          (Noren `om` shape)
    "reconcile" action       -> read order book + position, engine.on_reconcile(...)
    operator kill            -> engine.kill(now, reason)

Order states reuse the app's pure Noren state machine (``app.live.order_sm.apply_om``): rank never
regresses, terminal is sticky, fills are cumulative (max), over-fill / post-terminal fill /
norenordno mismatch set ``reconcile_required``.

SAFETY INVARIANTS (each pinned by tests/test_scalping_engine.py):
  I1  An ACK is never a fill. Position quantity = sum(BUY fillshares) - sum(SELL fillshares).
  I2  No unintended short: a SELL is only sized from ``sellable = position - working SELL qty``, and
      no SELL is placed while any SELL's status is unknown. A negative position halts the engine.
  I3  At most one working EXIT order; re-pricing is cancel -> confirmed CANCELED -> new order.
  I4  No new ENTRY while any order is non-terminal, the position is non-zero, or reconcile is pending.
  I5  Unknown status (no submit result / no ack / unconfirmed cancel by its deadline) -> reconcile
      required: entries stop, a reconcile is requested, exits continue only for confirmed quantity.
  I6  An entry not fully filled by its timeout is cancelled; a partial fill becomes the position
      and is managed (exited) like any other.
  I7  Stop is evaluated before target/trail/time on the same snapshot; one exit order covers them all.
  I8  Exits never give up: the cross ladder escalates and then repeats its last rung, clamped to the
      exchange LPP floor; every rung beyond the first raises an alert.
  I9  Kill switch: cancel working entries, exit the position, HALT (no automatic resume).
  I10 Entries respect the order-rate budget with a reserve kept for exits; exits are deferred (never
      dropped) when the budget is exhausted.
  I11 Malformed broker data never becomes a price or a quantity: a fill without a valid average price, or a
      COMPLETE without a full parsable fill quantity, keeps the round trip open and requires a reconcile.
  I13 An exit is never re-priced before it could have executed: the re-price clock starts when the broker
      reports the order OPEN at the exchange (fallback 3x the wait from sending, if that event is lost)
      and the wait grows with each rung, so latency longer than the re-price interval cannot livelock exits.
  I12 Absence from one order-book read proves nothing: an unknown order is declared never-accepted only
      after two consecutive SUCCESSFUL reads without it and >= 2 x ack timeout since it was sent; an order
      found by a read is known again; a refused or lost cancel is re-sent with backoff.
"""
from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Deque, Dict, List, Optional

from app.dte import compute_dte
from app.live.order_sm import TERMINAL, apply_om
from app.scalping import costs
from app.scalping.config import ScalperConfig
from app.scalping.market import MarketState, Quote
from app.scalping.signals import EntrySignal, evaluate as evaluate_signal

IST = timezone(timedelta(hours=5, minutes=30))
ENTRY_RATE_RESERVE = 2   # orders/s and orders/min held back for exits and cancels


def _hhmm(now_ms: int) -> str:
    return datetime.fromtimestamp(now_ms / 1000, IST).strftime("%H:%M")


def _ist_date(now_ms: int) -> str:
    return datetime.fromtimestamp(now_ms / 1000, IST).strftime("%Y-%m-%d")


def _valid_px(v: Any) -> Optional[float]:
    if isinstance(v, bool):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) and f > 0 else None


def round_to_tick(px: float, tick: float, up: bool) -> float:
    n = px / tick
    n = math.ceil(n - 1e-9) if up else math.floor(n + 1e-9)
    return round(max(n, 1) * tick, 2)


def lpp_bounds(reference: float) -> tuple:
    """Exchange limit-price-protection band for index options (NSE FAOP/54242; BSE 20251128-56):
    reference +/- max(40 % of reference, Rs 20). The true reference is the 30-s average trade price,
    which the Upstox feed does not carry; the caller passes LTP/mid as the closest proxy, so the
    clamp is approximate — a residual reject risk the exit ladder handles by re-pricing."""
    band = max(0.40 * reference, 20.0)
    return max(0.05, reference - band), reference + band


@dataclass
class Action:
    kind: str                       # "place" | "cancel" | "reconcile" | "alert" | "halt"
    ts_ms: int
    cid: Optional[str] = None
    side: Optional[str] = None      # "B" | "S"
    instrument_key: Optional[str] = None
    trading_symbol: Optional[str] = None
    qty: int = 0
    price: float = 0.0
    purpose: Optional[str] = None   # "entry" | "exit"
    reason: str = ""
    level: str = "info"


@dataclass
class EngineStats:
    trades_today: int = 0
    realized_net_inr: float = 0.0
    realized_gross_inr: float = 0.0
    charges_inr: float = 0.0
    consecutive_losses: int = 0
    day: str = ""


class ScalperEngine:
    def __init__(self, cfg: ScalperConfig, *, lot_size: int, cid_prefix: str = "scalp"):
        if lot_size <= 0:
            raise ValueError("lot_size must be resolved from contract data before trading")
        self.cfg = cfg
        self.lot_size = int(lot_size)
        self.cid_prefix = cid_prefix
        self._seq = 0
        self.orders: Dict[str, Dict[str, Any]] = {}          # this round trip's orders, by cid
        self.history: List[Dict[str, Any]] = []              # archived orders of closed round trips
        self._hist_by_id: Dict[str, Dict[str, Any]] = {}      # cid / norenordno -> archived order
        self.closed_trades: List[Dict[str, Any]] = []
        self.events: List[Dict[str, Any]] = []               # drained by the runner for journaling
        self.halted: Optional[str] = None
        self.paused_until_ms: int = 0
        self.paused_reason: str = ""
        self.paused_for_day: Optional[str] = None
        self.reconcile_required: Optional[str] = None
        self.reconcile_requested_ms: int = 0
        self.last_reconcile_ms: int = 0
        self._mismatch_reads: int = 0
        self._in_reconcile: bool = False
        self.stats = EngineStats()
        self.last_exit_ms: int = 0
        # current round trip
        self.trip: Optional[Dict[str, Any]] = None
        self._sent_s: Deque[int] = deque()
        self._sent_m: Deque[int] = deque()
        self.last_quote: Optional[Quote] = None

    # ------------------------------------------------------------------ bookkeeping
    def _cid(self) -> str:
        self._seq += 1
        return f"{self.cid_prefix}-{self.cfg.underlying[:1]}{self._seq:06d}"

    def _event(self, kind: str, now_ms: int, **kw) -> None:
        rec = {"kind": kind, "ts_ms": int(now_ms), "strategy_id": self.cfg.strategy_id}
        rec.update(kw)
        self.events.append(rec)

    def drain_events(self) -> List[Dict[str, Any]]:
        out, self.events = self.events, []
        return out

    def position(self) -> Dict[str, Any]:
        """Confirmed position from cumulative fills only (I1). An average is reported only when EVERY
        filled order on that side carries a valid price (I11) - an unpriced fill is never priced at 0."""
        def side(sd):
            filled = [o for o in self.orders.values() if o["side"] == sd and int(o.get("fillshares") or 0) > 0]
            q = sum(int(o["fillshares"]) for o in filled)
            if not filled or any(_valid_px(o.get("avgprc")) is None for o in filled):
                return q, None
            return q, sum(int(o["fillshares"]) * float(o["avgprc"]) for o in filled) / q
        bq, ab = side("B")
        sq, as_ = side("S")
        return {"qty": bq - sq, "bought": bq, "sold": sq, "avg_buy": ab, "avg_sell": as_,
                "price_unknown": (bq > 0 and ab is None) or (sq > 0 and as_ is None)}

    def working(self, side: Optional[str] = None, purpose: Optional[str] = None) -> List[Dict[str, Any]]:
        return [o for o in self.orders.values()
                if o["state"] not in TERMINAL
                and (side is None or o["side"] == side) and (purpose is None or o["purpose"] == purpose)]

    def _working_sell_qty(self) -> int:
        return sum(int(o["qty"]) - int(o.get("fillshares") or 0) for o in self.working("S"))

    def sellable(self) -> int:
        return self.position()["qty"] - self._working_sell_qty()

    def is_flat_and_idle(self) -> bool:
        return self.position()["qty"] == 0 and not self.working()

    # ------------------------------------------------------------------ rate budget (I10)
    def _prune(self, now_ms: int) -> None:
        while self._sent_s and now_ms - self._sent_s[0] >= 1000:
            self._sent_s.popleft()
        while self._sent_m and now_ms - self._sent_m[0] >= 60_000:
            self._sent_m.popleft()

    def _budget_ok(self, now_ms: int, reserve: int) -> bool:
        self._prune(now_ms)
        return (len(self._sent_s) + reserve < self.cfg.max_orders_per_sec
                and len(self._sent_m) + reserve < self.cfg.max_orders_per_min)

    def _spend(self, now_ms: int) -> None:
        self._sent_s.append(now_ms)
        self._sent_m.append(now_ms)

    # ------------------------------------------------------------------ order creation
    def _new_order(self, now_ms: int, *, side: str, purpose: str, key: str, tsym: str, qty: int,
                   price: float, deadline_ms: int, decision: Dict[str, Any]) -> Action:
        cid = self._cid()
        self.orders[cid] = {
            "client_order_id": cid, "side": side, "purpose": purpose, "instrument_key": key,
            "trading_symbol": tsym, "qty": int(qty), "price": float(price), "state": "INTENT",
            "fillshares": 0, "avgprc": None, "norenordno": None, "unknown": False,
            "sent_ms": now_ms, "ack_ms": None, "first_fill_ms": None, "last_fill_ms": None,
            "deadline_ms": deadline_ms, "ack_deadline_ms": now_ms + self.cfg.ack_timeout_ms,
            "cancel_sent_ms": None, "cancel_deadline_ms": None, "attempt": decision.get("attempt", 0),
            "decision": decision,
        }
        self._spend(now_ms)
        self._event("place", now_ms, cid=cid, side=side, purpose=purpose, instrument_key=key, qty=qty,
                    price=price, **{k: v for k, v in decision.items() if k != "attempt"})
        return Action("place", now_ms, cid=cid, side=side, instrument_key=key, trading_symbol=tsym,
                      qty=int(qty), price=float(price), purpose=purpose, reason=str(decision.get("reason", "")))

    def _cancel(self, now_ms: int, o: Dict[str, Any], reason: str) -> Optional[Action]:
        if o["state"] in TERMINAL or o["cancel_sent_ms"] is not None:
            return None
        if now_ms < int(o.get("cancel_retry_after_ms") or 0):
            return None
        o["cancel_attempts"] = int(o.get("cancel_attempts") or 0) + 1
        o["cancel_sent_ms"] = now_ms
        o["cancel_deadline_ms"] = now_ms + self.cfg.cancel_confirm_timeout_ms
        self._spend(now_ms)
        self._event("cancel", now_ms, cid=o["client_order_id"], reason=reason)
        return Action("cancel", now_ms, cid=o["client_order_id"], reason=reason)

    # ------------------------------------------------------------------ broker feedback
    def on_submit_result(self, cid: str, now_ms: int, *, ok: bool, norenordno: Optional[str] = None,
                         reject_reason: Optional[str] = None, indeterminate: bool = False) -> List[Action]:
        """Result of the place call. ok -> SUBMITTED (an ACK, NOT a fill). A definite broker reject ->
        REJECTED. A transport failure / timeout -> status UNKNOWN -> reconcile (I5)."""
        o = self.orders.get(cid)
        if o is None:
            self._event("orphan_submit_result", now_ms, cid=cid)
            return [self._require_reconcile(now_ms, f"submit_result_for_unknown_cid:{cid}")]
        if indeterminate:
            o["unknown"] = True
            self._event("submit_indeterminate", now_ms, cid=cid)
            return [self._require_reconcile(now_ms, f"submit_indeterminate:{cid}")]
        if ok:
            o2 = apply_om(o, {"status": "PENDING", "norenordno": norenordno})
            o2["ack_ms"] = now_ms
            self.orders[cid] = o2
            self._event("ack", now_ms, cid=cid, norenordno=norenordno, ack_latency_ms=now_ms - o["sent_ms"])
            return []
        o2 = apply_om(o, {"status": "REJECTED", "rejreason": reject_reason, "norenordno": norenordno})
        self.orders[cid] = o2
        self._event("reject", now_ms, cid=cid, reason=reject_reason, reject_class=o2.get("reject_class"))
        acts: List[Action] = []
        if o["purpose"] == "exit":
            acts.append(Action("alert", now_ms, cid=cid, level="warning", reason=f"exit rejected: {reject_reason}"))
        acts += self._maybe_close_trip(now_ms)
        return acts

    def on_cancel_result(self, cid: str, now_ms: int, *, ok: bool, error: Optional[str] = None) -> List[Action]:
        """A cancel REQUEST outcome. Acceptance is not cancellation: the order is CANCELED only when the
        broker's order update says so (fills may still arrive). A refused cancel usually means the
        order already traded — the next order update / reconcile settles it."""
        o = self.orders.get(cid)
        if o is None:
            return []
        self._event("cancel_result", now_ms, cid=cid, ok=ok, error=error)
        if ok or o["state"] in TERMINAL:
            return []
        # Refused (session/throttle error, or the order already traded). Re-arm the cancel with backoff so
        # an exit never rests at a stale price forever (I8/I12); a fill that explains the refusal makes the
        # order terminal first, and then no re-send happens.
        self._rearm_cancel(now_ms, o, f"cancel_refused:{error}")
        return [Action("alert", now_ms, cid=cid, level="warning", reason=f"cancel refused: {error}")]

    def _rearm_cancel(self, now_ms: int, o: Dict[str, Any], why: str) -> None:
        n = int(o.get("cancel_attempts") or 1)
        o["cancel_sent_ms"] = None
        o["cancel_deadline_ms"] = None
        o["cancel_retry_after_ms"] = now_ms + min(30_000, 1000 * (2 ** n))
        self._event("cancel_rearmed", now_ms, cid=o["client_order_id"], why=why,
                    retry_after_ms=o["cancel_retry_after_ms"])

    def on_order_event(self, om: Dict[str, Any], now_ms: int) -> List[Action]:
        cid = om.get("remarks") or om.get("client_order_id")
        o = self.orders.get(cid) if cid else None
        if o is None and om.get("norenordno"):
            o = next((x for x in self.orders.values() if x.get("norenordno") == om["norenordno"]), None)
        if o is None:
            # An update for an order this engine does not hold: an archived order (late echo / duplicate /
            # reconcile re-read) or something foreign. Never trade on it. An archived order only matters
            # if it now shows MORE fills than were booked when its round trip closed.
            h = self._hist_by_id.get(cid or "") or self._hist_by_id.get(om.get("norenordno") or "")
            if h is not None:
                try:
                    newer = int(om.get("fillshares") or 0) > int(h.get("fillshares") or 0)
                except (TypeError, ValueError):
                    newer = False
                if newer:
                    self._event("late_fill_on_closed_trip", now_ms, cid=cid, fillshares=om.get("fillshares"),
                                booked=h.get("fillshares"))
                    return self._halt(now_ms, f"late_fill_on_closed_trip:{cid}")
                return []
            self._event("foreign_order_event", now_ms, cid=cid, norenordno=om.get("norenordno"))
            return []
        before = int(o.get("fillshares") or 0)
        om2 = dict(om)
        om2.setdefault("qty", o["qty"])
        o2 = apply_om(o, om2)
        # apply_om stores avgprc only on the event whose fills increased; accept a valid price from a later
        # event while none is stored (I11).
        if (_valid_px(o2.get("avgprc")) is None and int(o2.get("fillshares") or 0) > 0
                and _valid_px(om.get("avgprc")) is not None):
            o2["avgprc"] = _valid_px(om.get("avgprc"))
        if o2.get("unknown") and (o2["state"] != o["state"] or int(o2.get("fillshares") or 0) != before):
            o2["unknown"] = False
        after = int(o2.get("fillshares") or 0)
        malformed = self._malformed(o2)
        if malformed:
            o2["unknown"] = True
        if after > before:
            o2["first_fill_ms"] = o2["first_fill_ms"] or now_ms
            o2["last_fill_ms"] = now_ms
            self._event("fill", now_ms, cid=o2["client_order_id"], side=o2["side"], purpose=o2["purpose"],
                        delta=after - before, cum=after, avgprc=o2.get("avgprc"),
                        fill_latency_ms=now_ms - o2["sent_ms"])
        if o2["state"] != o["state"]:
            self._event("state", now_ms, cid=o2["client_order_id"], frm=o["state"], to=o2["state"],
                        reason=o2.get("rejreason"))
        if o2.get("open_ms") is None and o2["state"] in ("OPEN", "PARTIAL", "COMPLETE"):
            o2["open_ms"] = now_ms          # at the exchange: the earliest moment it could execute (I13)
        self.orders[o2["client_order_id"]] = o2
        acts: List[Action] = []
        if malformed:
            self._event("malformed_order_event", now_ms, cid=o2["client_order_id"], why=malformed,
                        status=om.get("status"), fillshares=om.get("fillshares"), avgprc=om.get("avgprc"))
            acts += self._require_reconcile_acts(now_ms, f"{malformed}:{o2['client_order_id']}")
        if o2.get("reconcile_required") and not o.get("reconcile_required"):
            acts += self._require_reconcile_acts(now_ms, f"order_sm_flag:{o2['client_order_id']}")
        if o2["side"] == "B" and after > before and self.trip is not None:
            self.trip.setdefault("first_fill_ms", now_ms)
            avg = self.position()["avg_buy"]
            self.trip["entry_avg"] = avg
        pos = self.position()
        if pos["qty"] < 0:
            acts += self._halt(now_ms, f"unintended_short qty={pos['qty']}")
        acts += self._maybe_close_trip(now_ms)
        return acts

    @staticmethod
    def _malformed(o: Dict[str, Any]) -> Optional[str]:
        filled = int(o.get("fillshares") or 0)
        if filled > 0 and _valid_px(o.get("avgprc")) is None:
            return "fill_without_price"
        if o["state"] == "COMPLETE" and filled != int(o["qty"]):
            return "complete_without_full_fill"
        return None

    def on_reconcile(self, now_ms: int, *, broker_orders: List[Dict[str, Any]], broker_net_qty: Optional[int]) -> List[Action]:
        """Authoritative read. Apply every broker order status we know by cid/norenordno; then compare
        the broker net quantity with our fill-derived position. Any mismatch HALTS (never guess)."""
        acts: List[Action] = []
        found = set()
        self._in_reconcile = True
        try:
            for om in broker_orders or []:
                cid = om.get("remarks") or om.get("client_order_id")
                o = self.orders.get(cid) if cid else None
                if o is None and om.get("norenordno"):
                    o = next((x for x in self.orders.values() if x.get("norenordno") == om["norenordno"]), None)
                if o is not None:
                    found.add(o["client_order_id"])
                acts += self.on_order_event(dict(om), now_ms)
        finally:
            self._in_reconcile = False
        self.last_reconcile_ms = now_ms
        if broker_net_qty is None:
            # A failed read proves nothing about any order (I12): no conversion, no clearing.
            self._event("reconcile_unreadable", now_ms)
            return acts + [Action("alert", now_ms, level="warning", reason="reconcile read failed; still blocked")]
        for cid in found:
            o = self.orders.get(cid)
            if o is None:
                continue
            o["absent_reads"] = 0
            if o.get("unknown") and not self._malformed(o):
                o["unknown"] = False            # found by a successful read: its status is known (I12)
            if (o["state"] not in TERMINAL and o["cancel_sent_ms"] is not None
                    and o["cancel_deadline_ms"] is not None and now_ms >= o["cancel_deadline_ms"]):
                self._rearm_cancel(now_ms, o, "cancel_unconfirmed_order_still_working")
        # Still unknown and absent from TWO consecutive successful reads, long after it was sent: it never
        # reached the broker. One read is not enough - a slow PlaceOrder may still be in flight.
        for o in list(self.orders.values()):
            if o.get("unknown") and o["client_order_id"] not in found and o["state"] not in TERMINAL:
                o["absent_reads"] = int(o.get("absent_reads") or 0) + 1
                if o["absent_reads"] >= 2 and now_ms - o["sent_ms"] >= 2 * self.cfg.ack_timeout_ms:
                    self.orders[o["client_order_id"]] = dict(o, state="REJECTED", unknown=False,
                                                             rejreason="not_found_at_broker_on_reconcile")
                    self._event("unknown_resolved_not_found", now_ms, cid=o["client_order_id"],
                                absent_reads=o["absent_reads"])
        pos = self.position()
        if int(broker_net_qty) != pos["qty"]:
            # The order book and the position book are two separate broker reads, so one mismatch can be
            # a read race; two consecutive mismatched reads are treated as real (the app's own
            # flat_confirm_reads=2 rule). Entries stay blocked in between.
            self._mismatch_reads += 1
            self._event("reconcile_mismatch", now_ms, broker=int(broker_net_qty), engine=pos["qty"],
                        consecutive=self._mismatch_reads)
            if self._mismatch_reads >= 2:
                return acts + self._halt(now_ms, f"reconcile_mismatch broker={broker_net_qty} engine={pos['qty']}")
            if self.reconcile_required is None:
                self.reconcile_required = "mismatch_unconfirmed"
            self.reconcile_requested_ms = now_ms           # the timer re-reads in 5 s
            return acts
        self._mismatch_reads = 0
        if any(o.get("unknown") for o in self.orders.values()):
            return acts
        self.reconcile_required = None
        self._event("reconciled", now_ms, qty=pos["qty"])
        acts += self._maybe_close_trip(now_ms)
        return acts

    # ------------------------------------------------------------------ halts / pauses
    def _require_reconcile(self, now_ms: int, reason: str) -> Action:
        if self.reconcile_required is None:
            self.reconcile_required = reason
        self.reconcile_requested_ms = now_ms
        self._event("reconcile_required", now_ms, reason=reason)
        return Action("reconcile", now_ms, reason=reason, level="warning")

    def _require_reconcile_acts(self, now_ms: int, reason: str) -> List[Action]:
        """Flag reconcile; emit the request unless we are inside a reconcile already (the 5-s timer
        re-asks), so one broker read can never trigger another in the same instant."""
        if self._in_reconcile:
            if self.reconcile_required is None:
                self.reconcile_required = reason
            self._event("reconcile_required", now_ms, reason=reason)
            return []
        return [self._require_reconcile(now_ms, reason)]

    def _halt(self, now_ms: int, reason: str) -> List[Action]:
        acts: List[Action] = []
        if self.halted is None:
            self.halted = reason
            self._event("halt", now_ms, reason=reason)
            acts.append(Action("halt", now_ms, reason=reason, level="critical"))
        for o in self.working("B"):
            a = self._cancel(now_ms, o, "halt")
            if a:
                acts.append(a)
        return acts

    def kill(self, now_ms: int, reason: str = "kill_switch") -> List[Action]:
        """I9: cancel entries, exit what is held (at the next on_second), halt without auto-resume."""
        acts = self._halt(now_ms, reason)
        if self.trip is not None:
            self.trip["force_exit"] = reason
        return acts

    # ------------------------------------------------------------------ main step
    def on_second(self, now_ms: int, market: MarketState) -> List[Action]:
        day = _ist_date(now_ms)
        if self.stats.day != day:
            self.stats = EngineStats(day=day)
            self.paused_for_day = None
        acts: List[Action] = []
        acts += self._timers(now_ms)
        pos = self.position()
        if pos["qty"] > 0:
            acts += self._manage_position(now_ms, market)
        elif self.working():
            pass            # entry working with no fill yet: the timers own it (timeout -> cancel)
        elif self.trip is not None:
            acts += self._maybe_close_trip(now_ms)
        else:
            acts += self._maybe_enter(now_ms, market)
        return acts

    def _timers(self, now_ms: int) -> List[Action]:
        acts: List[Action] = []
        for o in list(self.orders.values()):
            if o["state"] in TERMINAL:
                continue
            if o["state"] == "INTENT" and not o.get("unknown") and now_ms >= o["ack_deadline_ms"]:
                o["unknown"] = True
                acts.append(self._require_reconcile(now_ms, f"no_ack:{o['client_order_id']}"))
            if o["cancel_deadline_ms"] is not None and now_ms >= o["cancel_deadline_ms"] and not o.get("unknown"):
                o["unknown"] = True
                acts.append(self._require_reconcile(now_ms, f"cancel_unconfirmed:{o['client_order_id']}"))
            if o["purpose"] == "entry" and now_ms >= o["deadline_ms"] and o["cancel_sent_ms"] is None:
                a = self._cancel(now_ms, o, "entry_timeout")
                if a:
                    acts.append(a)
        if self.reconcile_required and now_ms - self.reconcile_requested_ms >= 5000:
            acts.append(self._require_reconcile(now_ms, self.reconcile_required))   # re-ask every 5 s
        elif (not self.reconcile_required and (self.working() or self.position()["qty"] != 0)
              and now_ms - max(self.last_reconcile_ms, self.reconcile_requested_ms) >= self.cfg.reconcile_interval_s * 1000):
            self.reconcile_requested_ms = now_ms
            acts.append(Action("reconcile", now_ms, reason="routine"))            # lost-update backstop
        return acts

    # ------------------------------------------------------------------ entries
    def _entry_block_reason(self, now_ms: int) -> Optional[str]:
        c = self.cfg
        if self.halted:
            return f"halted:{self.halted}"
        if self.reconcile_required:
            return "reconcile_required"
        if self.paused_for_day:
            return f"paused_for_day:{self.paused_for_day}"
        if now_ms < self.paused_until_ms:
            return f"paused:{self.paused_reason}"
        t = _hhmm(now_ms)
        if not any(a <= t < b for a, b in c.entry_windows_ist):
            return "outside_entry_window"
        if self.stats.trades_today >= c.max_trades_per_day:
            return "max_trades_per_day"
        if now_ms - self.last_exit_ms < c.cooldown_after_exit_s * 1000:
            return "cooldown"
        if not self._budget_ok(now_ms, ENTRY_RATE_RESERVE):
            return "order_rate_budget"
        return None

    def _entry_filters(self, now_ms: int, market: MarketState, sig: EntrySignal) -> Optional[str]:
        c = self.cfg
        k = sig.contract
        if market.contracts.get(k.instrument_key) is None:
            return "contract_unknown"
        exps = market.expiries(c.underlying)
        dte = compute_dte(_ist_date(now_ms), [k.expiry])     # holiday-aware trading-day DTE (app.dte)
        if dte is None:
            return "dte_unknown"
        if dte not in c.allowed_dte:
            return f"dte_{dte}_not_allowed"
        if not exps or k.expiry != exps[c.expiry_rank]:
            return "wrong_expiry"
        q = market.fresh_quote(k.instrument_key, c.max_quote_age_ms)
        if q is None:
            return "quote_stale_or_missing"
        if not (c.min_premium <= q.ask <= c.max_premium):
            return f"premium_out_of_range:{q.ask}"
        if q.spread_ticks(c.tick_size) > c.max_spread_ticks:
            return f"spread_ticks:{q.spread_ticks(c.tick_size)}"
        if q.spread_pct() > c.max_spread_pct:
            return f"spread_pct:{q.spread_pct():.3f}"
        qty = c.lots * self.lot_size
        if q.ask_qty < c.min_ask_touch_qty_x * qty:
            return f"ask_touch_qty:{q.ask_qty}"
        if q.ask_depth5_qty < c.min_ask_depth5_qty_x * qty:
            return f"ask_depth5_qty:{q.ask_depth5_qty}"
        return None

    def _maybe_enter(self, now_ms: int, market: MarketState) -> List[Action]:
        block = self._entry_block_reason(now_ms)
        if block:
            return []
        sig = evaluate_signal(market, self.cfg)
        if sig is None:
            return []
        why = self._entry_filters(now_ms, market, sig)
        if why:
            self._event("signal_filtered", now_ms, reason=why, signal=sig.reason, **sig.metrics)
            return []
        c = self.cfg
        q = market.fresh_quote(sig.contract.instrument_key, c.max_quote_age_ms)
        assert q is not None
        limit = min(q.ask + c.entry_cross_ticks * c.tick_size, q.ask * (1 + c.entry_max_slip_pct / 100.0))
        limit = round_to_tick(max(limit, q.ask), c.tick_size, up=True)
        _, upper = lpp_bounds(q.ltp or q.mid)
        if limit > upper:
            self._event("signal_filtered", now_ms, reason="entry_limit_beyond_lpp", signal=sig.reason)
            return []
        qty = c.lots * self.lot_size
        self.last_quote = q
        self.trip = {"opened_ms": now_ms, "side": sig.side, "instrument_key": sig.contract.instrument_key,
                     "trading_symbol": sig.contract.trading_symbol, "signal": sig.reason,
                     "signal_metrics": dict(sig.metrics), "entry_quote": {"bid": q.bid, "ask": q.ask,
                     "bid_qty": q.bid_qty, "ask_qty": q.ask_qty, "age_ms": q.age_ms(now_ms)},
                     "peak_bid": None, "exit_reason": None, "exit_attempt": -1, "force_exit": None}
        decision = {"reason": sig.reason, "signal_ms": now_ms, "quote_age_ms": q.age_ms(now_ms),
                    "ask": q.ask, "bid": q.bid, **{f"sig_{k}": v for k, v in sig.metrics.items()}}
        return [self._new_order(now_ms, side="B", purpose="entry", key=sig.contract.instrument_key,
                                tsym=sig.contract.trading_symbol, qty=qty, price=limit,
                                deadline_ms=now_ms + c.entry_timeout_ms, decision=decision)]

    # ------------------------------------------------------------------ exits
    def _exit_reason(self, now_ms: int, market: MarketState, q: Optional[Quote], entry: float) -> Optional[str]:
        c, trip = self.cfg, self.trip
        if self.halted:
            return f"halt:{self.halted}"
        if trip.get("force_exit"):
            return f"forced:{trip['force_exit']}"
        if _hhmm(now_ms) >= c.force_exit_ist:
            return "force_exit_time"
        if self.paused_for_day:
            return f"daily_loss_limit"
        if q is None:
            last = self._trip_quote()
            seen_ms = last.ingest_ms if last is not None else (trip.get("first_fill_ms") or trip["opened_ms"])
            if now_ms - seen_ms >= c.stale_feed_exit_ms:
                return "stale_feed"
            return None
        bid = q.bid
        trip["peak_bid"] = bid if trip.get("peak_bid") is None else max(trip["peak_bid"], bid)
        if bid <= entry * (1 - c.stop_loss_pct / 100.0):                       # I7: stop first
            return "stop"
        if c.trail_arm_pct is not None and trip["peak_bid"] >= entry * (1 + c.trail_arm_pct / 100.0):
            floor = entry + (trip["peak_bid"] - entry) * (1 - c.trail_giveback_pct / 100.0)
            if bid <= floor:
                return "trail"
        if c.target_pct is not None and bid >= entry * (1 + c.target_pct / 100.0):
            return "target"
        first = trip.get("first_fill_ms")
        if first is not None and now_ms - first >= c.max_hold_s * 1000:
            return "time"
        return None

    def _manage_position(self, now_ms: int, market: MarketState) -> List[Action]:
        c, trip = self.cfg, self.trip
        acts: List[Action] = []
        if trip is None:   # position without a trip (late fill after archive): treat as forced exit
            self.trip = trip = {"opened_ms": now_ms, "instrument_key": None, "force_exit": "orphan_position",
                                "peak_bid": None, "exit_attempt": -1, "exit_reason": None}
        pos = self.position()
        key = trip.get("instrument_key") or next(iter(o["instrument_key"] for o in self.orders.values()), None)
        q = market.fresh_quote(key, c.max_quote_age_ms) if key else None
        if q is not None:
            self.last_quote = q
        # mark-to-market risk: realized + open at the bid
        if pos["qty"] > 0 and pos["avg_buy"] is not None and q is not None and self.paused_for_day is None:
            open_mtm = (q.bid - pos["avg_buy"]) * pos["qty"] - costs.charges_inr(c.exchange, pos["avg_buy"], q.bid, pos["qty"])
            if self.stats.realized_net_inr + open_mtm <= -c.daily_loss_limit_inr:
                self.paused_for_day = "daily_loss_limit"
                self._event("pause_day", now_ms, reason="daily_loss_limit",
                            realized=self.stats.realized_net_inr, open_mtm=round(open_mtm, 2))
                acts.append(Action("alert", now_ms, level="critical", reason="daily loss limit reached"))
        if pos["qty"] <= 0:
            return acts + self._maybe_close_trip(now_ms)
        # cancel any working entry remainder before/while exiting (I6); the fill race is handled by
        # sizing exits from confirmed fills only
        entry_px = pos["avg_buy"]
        if trip.get("exit_reason") is None:
            # With an unpriced fill (I11) only non-price exits can fire (NaN comparisons are all False);
            # stop/target/trail wait for the reconcile that supplies the price.
            r = self._exit_reason(now_ms, market, q, entry_px if entry_px is not None else float("nan"))
            if r is None:
                return acts
            trip["exit_reason"] = r
            trip["exit_decided_ms"] = now_ms
            self._event("exit_decision", now_ms, reason=r, bid=q.bid if q else None, entry=entry_px)
        for o in self.working("B"):
            a = self._cancel(now_ms, o, "exiting")
            if a:
                acts.append(a)
        return acts + self._work_exit(now_ms, q)

    def _work_exit(self, now_ms: int, q: Optional[Quote]) -> List[Action]:
        c, trip = self.cfg, self.trip
        acts: List[Action] = []
        sells = self.working("S")
        if any(o.get("unknown") for o in self.orders.values() if o["side"] == "S"):
            return acts                                                        # I2: wait for reconcile
        if sells:                                                              # I3: one working exit
            o = sells[0]
            wait = c.exit_reprice_ms * min(1 + int(o.get("attempt") or 0), 4)
            ripe = ((o.get("open_ms") is not None and now_ms - o["open_ms"] >= wait)
                    or now_ms - o["sent_ms"] >= 3 * wait)                     # I13: no cancel-before-arrival
            if ripe and o["cancel_sent_ms"] is None:
                a = self._cancel(now_ms, o, "exit_reprice")
                if a:
                    acts.append(a)
            return acts
        qty = self.sellable()
        if qty <= 0:
            return acts
        if not self._budget_ok(now_ms, 0):                                     # I10: defer, never drop
            self._event("exit_deferred_rate", now_ms)
            return acts
        ref = q or self._trip_quote()
        if ref is None:
            return acts + [Action("alert", now_ms, level="critical", reason="no quote ever seen for held contract")]
        attempt = min(trip["exit_attempt"] + 1, len(c.exit_cross_ticks) - 1)
        trip["exit_attempt"] = trip["exit_attempt"] + 1
        cross = c.exit_cross_ticks[attempt]
        stale_penalty = 0.95 if q is None else 1.0                             # stale feed: assume worse
        px = (ref.bid * stale_penalty) - cross * c.tick_size
        lower, _ = lpp_bounds(ref.ltp or ref.mid)
        px = round_to_tick(max(px, lower + c.tick_size, c.tick_size), c.tick_size, up=True)
        if trip["exit_attempt"] >= 1:
            acts.append(Action("alert", now_ms, level="warning" if trip["exit_attempt"] < len(c.exit_cross_ticks) else "critical",
                               reason=f"exit re-price attempt {trip['exit_attempt']} ({trip['exit_reason']})"))
        acts.append(self._new_order(now_ms, side="S", purpose="exit", key=trip["instrument_key"] or ref.instrument_key,
                                    tsym=trip.get("trading_symbol") or "", qty=qty, price=px,
                                    deadline_ms=now_ms + c.exit_reprice_ms,
                                    decision={"reason": trip["exit_reason"], "attempt": attempt, "bid": ref.bid,
                                              "quote_age_ms": ref.age_ms(now_ms)}))
        return acts

    # ------------------------------------------------------------------ round-trip close
    def _maybe_close_trip(self, now_ms: int) -> List[Action]:
        if self.trip is None or self.working():
            return []
        if any(o.get("unknown") for o in self.orders.values()):
            return []                       # I11/I12: never book or archive what is not yet known
        pos = self.position()
        if pos["qty"] != 0 or pos["price_unknown"]:
            return []
        trip = self.trip
        acts: List[Action] = []
        if pos["bought"] > 0:
            qty = pos["bought"]
            ent, ex = pos["avg_buy"], pos["avg_sell"]
            gross = (ex - ent) * qty
            ch = costs.charges_inr(self.cfg.exchange, ent, ex, qty)
            net = gross - ch
            rec = {"strategy_id": self.cfg.strategy_id, "underlying": self.cfg.underlying,
                   "instrument_key": trip.get("instrument_key"), "trading_symbol": trip.get("trading_symbol"),
                   "side": trip.get("side"), "qty": qty, "lots": qty // self.lot_size,
                   "entry_avg": round(ent, 4), "exit_avg": round(ex, 4),
                   "gross_inr": round(gross, 2), "charges_inr": round(ch, 2), "net_inr": round(net, 2),
                   "ret_pct": round(100 * (ex - ent) / ent, 4) if ent else None, "exit_reason": trip.get("exit_reason"),
                   "signal": trip.get("signal"), "signal_metrics": trip.get("signal_metrics"),
                   "entry_quote": trip.get("entry_quote"), "opened_ms": trip["opened_ms"],
                   "first_fill_ms": trip.get("first_fill_ms"), "closed_ms": now_ms,
                   "hold_ms": now_ms - (trip.get("first_fill_ms") or trip["opened_ms"]),
                   "exit_attempts": trip.get("exit_attempt", -1) + 1,
                   "orders": [{k: o.get(k) for k in ("client_order_id", "side", "purpose", "qty", "price", "state",
                                                     "fillshares", "avgprc", "sent_ms", "ack_ms",
                                                     "first_fill_ms", "last_fill_ms")} for o in self.orders.values()]}
            self.closed_trades.append(rec)
            self.stats.trades_today += 1
            self.stats.realized_gross_inr += gross
            self.stats.charges_inr += ch
            self.stats.realized_net_inr += net
            self.stats.consecutive_losses = self.stats.consecutive_losses + 1 if net < 0 else 0
            self.last_exit_ms = now_ms
            self._event("trade_closed", now_ms, **{k: v for k, v in rec.items() if k != "orders"})
            if self.stats.consecutive_losses >= self.cfg.max_consecutive_losses:
                self.paused_until_ms = now_ms + self.cfg.pause_after_losses_s * 1000
                self.paused_reason = f"{self.stats.consecutive_losses}_consecutive_losses"
                self.stats.consecutive_losses = 0
                self._event("pause", now_ms, reason=self.paused_reason, until_ms=self.paused_until_ms)
                acts.append(Action("alert", now_ms, level="warning", reason=f"paused: {self.paused_reason}"))
            if self.stats.realized_net_inr <= -self.cfg.daily_loss_limit_inr and not self.paused_for_day:
                self.paused_for_day = "daily_loss_limit"
                self._event("pause_day", now_ms, reason="daily_loss_limit", realized=self.stats.realized_net_inr)
        else:
            self._event("entry_unfilled", now_ms, reason=trip.get("signal"))
        for o in self.orders.values():
            self.history.append(o)
            self._hist_by_id[o["client_order_id"]] = o
            if o.get("norenordno"):
                self._hist_by_id[o["norenordno"]] = o
        self.orders = {}
        self.trip = None
        self.last_quote = None
        return acts

    def _trip_quote(self) -> Optional[Quote]:
        """The last quote seen for the CURRENT trip's contract (never another contract's)."""
        q = self.last_quote
        key = (self.trip or {}).get("instrument_key")
        return q if (q is not None and key is not None and q.instrument_key == key) else None

    # ------------------------------------------------------------------ restart
    def snapshot(self) -> Dict[str, Any]:
        return {"strategy_id": self.cfg.strategy_id, "seq": self._seq, "orders": list(self.orders.values()),
                "trip": self.trip, "halted": self.halted, "paused_until_ms": self.paused_until_ms,
                "paused_reason": self.paused_reason, "paused_for_day": self.paused_for_day,
                "stats": vars(self.stats), "last_exit_ms": self.last_exit_ms}

    @classmethod
    def restore(cls, cfg: ScalperConfig, snap: Dict[str, Any], *, lot_size: int, now_ms: int) -> "ScalperEngine":
        """Rebuild after a restart. Always starts in RECONCILE_REQUIRED: nothing trades until the
        broker's order book and position agree with the restored state."""
        e = cls(cfg, lot_size=lot_size)
        e._seq = int(snap.get("seq") or 0)
        e.orders = {o["client_order_id"]: dict(o) for o in snap.get("orders") or []}
        e.trip = snap.get("trip")
        e.halted = snap.get("halted")
        e.paused_until_ms = int(snap.get("paused_until_ms") or 0)
        e.paused_reason = snap.get("paused_reason") or ""
        e.paused_for_day = snap.get("paused_for_day")
        e.stats = EngineStats(**(snap.get("stats") or {}))
        e.last_exit_ms = int(snap.get("last_exit_ms") or 0)
        for o in e.orders.values():
            if o["state"] not in TERMINAL:
                o["unknown"] = True
        e._require_reconcile(now_ms, "restart")
        return e
