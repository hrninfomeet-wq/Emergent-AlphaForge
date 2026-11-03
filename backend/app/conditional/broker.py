"""Durable-action adapters; no gateway may blindly retry a placement.

The engine persists action IDs before calling submit. These adapters make no
database writes, login calls or background tasks. Observations are explicit and
identity-checked; UNKNOWN never means flat, rejected or safe to resubmit.
"""
from __future__ import annotations

import asyncio
import copy
import math
import time
from decimal import Decimal
from typing import Any

from app.conditional.market import positive
from app.live.broker_protocol import OrderIntent


def observation(status, *, broker_id=None, filled_qty=0, fill_price=None, reason=None):
    return dict(status=status, broker_id=broker_id, filled_qty=filled_qty,
                fill_price=fill_price, reason=reason)


def price_type(action):
    return 'SL-LMT' if action['kind'] == 'stop' or action.get('order_type') == 'STOP_LIMIT' else 'LMT'


def validate_action(action: dict) -> dict:
    action = copy.deepcopy(action)
    ident = action.get("id")
    if not isinstance(ident, str) or not ident or len(ident) > 30 or not ident.isalnum():
        raise ValueError("action id must be a stable alphanumeric broker remark, max 30 characters")
    if action.get("kind") not in ("entry", "stop", "exit"):
        raise ValueError("unknown action kind")
    if action.get("side") != ("B" if action["kind"] == "entry" else "S"):
        raise ValueError("conditional orders are long-option entries and sell-to-close exits")
    contract = action.get("contract") or {}
    qty = positive(action.get("quantity"), "quantity", integer=True)
    lot = positive(contract.get("lot_size"), "lot_size", integer=True)
    freeze = positive(contract.get("freeze_quantity"), "freeze", integer=True)
    tick = positive(contract.get("tick_size"), "tick_size")
    if (action["kind"] == "entry" and qty % lot) or qty > freeze:
        raise ValueError("quantity exceeds freeze or entry is not whole-lot")
    price = positive(action.get("limit_price"), "limit price")
    if Decimal(str(price)) % Decimal(str(tick)) != 0:
        raise ValueError("limit price is not tick aligned")
    if action.get('order_type') not in (None, 'MARKET', 'LIMIT', 'STOP_LIMIT'):
        raise ValueError('Unsupported entry order type')
    if price_type(action) == 'SL-LMT':
        trigger = positive(action.get("trigger_price"), "stop trigger")
        if Decimal(str(trigger)) % Decimal(str(tick)) != 0 or (price > trigger if action['side'] == 'S' else price < trigger):
            raise ValueError('Stop-limit price must be tick aligned and on the executable side of trigger')
    action["quantity"] = qty
    return action


class PaperGateway:
    mode = "paper"
    protection_supported = False
    live_available = False

    async def prepare(self, action):
        return validate_action(action)

    async def submit(self, action):
        action = await self.prepare(action)
        return self._fill(action)

    def _fill(self, action):
        prior = action.get("observation") or {}
        if prior.get("status") in ("COMPLETE", "CANCELED", "REJECTED"):
            return dict(prior)
        try:
            price = positive(action.get("reference_price"), "current simulation premium")
        except ValueError:
            return dict(observation("OPEN", broker_id="paper:" + action["id"]), triggered=prior.get('triggered', False))
        marketable = action.get('order_type') == 'MARKET' or (action["limit_price"] >= price if action["side"] == "B"
                      else action["limit_price"] <= price)
        triggered = prior.get('triggered', False)
        if price_type(action) == 'SL-LMT':
            triggered = triggered or (price <= action['trigger_price'] if action['side'] == 'S' else price >= action['trigger_price'])
            marketable = marketable and triggered
        if not marketable:
            return dict(observation("OPEN", broker_id="paper:" + action["id"]), triggered=triggered)
        return observation("COMPLETE", broker_id="paper:" + action["id"],
                           filled_qty=action["quantity"], fill_price=price)

    async def observe(self, action):
        return self._fill(action)

    async def cancel(self, action):
        prior = action.get("observation") or {}
        if prior.get("status") == "COMPLETE":
            return dict(prior)
        return observation("CANCELED", broker_id=prior.get("broker_id") or "paper:" + action["id"],
                           filled_qty=prior.get("filled_qty", 0), fill_price=prior.get("fill_price"))

    async def amend_stop(self, action, trigger, limit):
        if action.get("kind") != "stop" or trigger < action["trigger_price"]:
            raise ValueError("a protective stop must never loosen")
        amended = dict(action, trigger_price=trigger, limit_price=limit)
        await self.prepare(amended)
        result = self._fill(amended)
        return dict(result, amended=result["status"] in ("OPEN", "COMPLETE"))

    async def amend_entry(self, action):
        if action['kind'] != 'entry' or not action.get('pending_amendment'):
            raise ValueError('Pending entry amendment required')
        if (action.get('observation') or {}).get('status') in ('COMPLETE', 'CANCELED', 'REJECTED'):
            return dict(action['observation'], amended=False)
        amended = await self.prepare({**action, **action['pending_amendment']})
        return dict(self._fill(amended), amended=True)

    async def reconcile(self, order):
        return {"ok": True, "reason": None}


async def _client():
    from app.routers.live_broker import _get_client
    return await _get_client()


def _engine():
    from app.routers.live_broker import _l3_engine
    return _l3_engine()


async def _submit(client, intent, *, engine, entry):
    from app.live.executor import submit_conditional_intent
    return await submit_conditional_intent(client, intent, engine=engine, entry=entry)


class FlattradeGateway:
    mode = "live"
    # Account/product protective capability must be operator-established. False
    # is software-only protection, not a claim that exchange SL-LMT is forbidden.
    def __init__(self, *, client_factory=None, engine_factory=None, submitter=None,
                 protection_supported=False, clock=time.time, book_ttl=2.0):
        self._client = client_factory or _client
        self._engine = engine_factory or _engine
        self._submitter = submitter or _submit
        self.protection_supported = bool(protection_supported)
        self.live_available = True
        self._clock, self._book_ttl = clock, book_ttl
        self._books = None
        self._books_at = 0.0
        self._positions = None
        self._positions_at = 0.0
        self._read_lock = asyncio.Lock()

    async def ready(self):
        """Configuration readiness; does not assert that a broker session is alive."""
        try:
            from app.live.executor import _autoplace_armed
            if not _autoplace_armed():
                return False, "live automatic placement is disabled"
            from app.live.flattrade_token import DEFAULT_USER_ID, get_status
            status = await get_status(DEFAULT_USER_ID)
            if not status.get('connected') or status.get('expired') or status.get('regenerate_after_6am'):
                return False, 'Flattrade unavailable; log in from the registered static IP'
            allowed, why = await self._engine().can_trade()
            if not allowed:
                return False, why
            if await self._client() is None:
                return False, "broker client unavailable"
            return True, "configured; broker session is checked by authenticated operations"
        except Exception as exc:
            return False, f"live readiness unavailable: {type(exc).__name__}"

    async def prepare(self, action):
        action = validate_action(action)
        action["contract"] = await self._resolve_contract(action["contract"])
        return action

    async def _resolve_contract(self, contract):
        contract = dict(contract)
        if contract.get("exchange") not in ("NFO", "BFO"):
            raise ValueError("unrecognized broker segment")
        if (contract.get("segment"), contract["exchange"]) not in (("NSE_FO", "NFO"), ("BSE_FO", "BFO")):
            raise ValueError("master/broker segment mismatch")
        client = await self._client()
        rows = await client.search_scrip(contract["exchange"],
                                         f"{contract['underlying']} {contract['strike']:g}")
        from app.live.flattrade_symbol import _parse_exd, _strike_from_dname, SymbolResolutionError
        matches = []
        for row in rows:
            try:
                display = str(row.get("dname") or "").strip()
                if (display.split()[0] == contract["underlying"]
                        and row.get("optt") == contract["option_type"]
                        and _parse_exd(row.get("exd", "")) == contract["expiry"]
                        and _strike_from_dname(display) == contract["strike"]):
                    matches.append(row)
            except (ValueError, IndexError, AttributeError, SymbolResolutionError):
                continue
        if len(matches) != 1:
            raise ValueError("broker contract identity missing or ambiguous")
        row = matches[0]
        if (positive(row.get("ls"), "broker lot", integer=True) != contract["lot_size"]
                or positive(row.get("ti"), "broker tick") != contract["tick_size"]):
            raise ValueError("broker/master lot or tick mismatch")
        tsym, token = str(row.get("tsym") or "").strip(), str(row.get("token") or "").strip()
        if not tsym or not token:
            raise ValueError("broker identity missing")
        if contract.get("tsym") and contract["tsym"] != tsym:
            raise ValueError("broker identity changed after resolution")
        contract.update(tsym=tsym, token=token)
        return contract

    async def _book(self, *, fresh=False):
        async with self._read_lock:
            now = self._clock()
            if fresh or self._books is None or now - self._books_at >= self._book_ttl:
                client = await self._client()
                # One shared read serves all children per worker cycle.
                rows = await client.order_book()
                if not isinstance(rows, list):
                    raise ValueError("broker order book is not a list")
                self._books, self._books_at = rows, now
            return self._books

    def _invalidate(self):
        self._books = None
        self._positions = None

    async def _position_book(self, *, fresh=False):
        async with self._read_lock:
            now = self._clock()
            if fresh or self._positions is None or now - self._positions_at >= self._book_ttl:
                client = await self._client()
                rows = await client.position_book()
                if not isinstance(rows, list):
                    raise ValueError("broker position book is not a list")
                self._positions, self._positions_at = rows, now
            return self._positions

    @staticmethod
    def _intent(action):
        contract = action["contract"]
        return OrderIntent(client_order_id=action["id"], remarks=action["id"],
                           trantype=action["side"], prctyp=price_type(action),
                           exch=contract["exchange"], tsym=contract["tsym"], qty=action["quantity"],
                           prc=action["limit_price"], trgprc=action.get("trigger_price"), prd="I", ret="DAY")

    async def submit(self, action):
        try:
            validate_action(action)
            if action.get('order_type') == 'MARKET':
                return observation('REJECTED', reason='Flattrade API market orders are unavailable')
            if not action["contract"].get("tsym"):
                raise ValueError("prepare must resolve and persist identity before submit")
            if action["kind"] == "stop" and not self.protection_supported:
                return observation("REJECTED", reason="broker protection capability not established")
            client = await self._client()
            from app.live.exit_claims import claim_exit
            async with claim_exit(action["contract"]["tsym"], label="conditional") as got:
                if not got:
                    return observation("REJECTED", reason="symbol busy; no submission")
                if action["kind"] == "entry":
                    owned = [a for a in action.get("owned_actions", []) if a.get("id") != action["id"]]
                    check = await self.reconcile({"contract": action["contract"], "actions": owned}, fresh=True)
                    if not check["ok"]:
                        return observation("REJECTED", reason=check["reason"])
                    # A repeated ID is indeterminate even if its first submission
                    # has not yet appeared in the supplied parent snapshot.
                    existing = self._match(action, await self._book())
                    if existing["status"] != "ABSENT":
                        return observation("UNKNOWN", reason="action already present; no resubmission")
                    result = await self._submitter(client, self._intent(action),
                                                   engine=self._engine(), entry=True)
                else:
                    check = await self._sell_capacity(action)
                    if not check["ok"]:
                        return observation("REJECTED", reason=check["reason"])
                    result = await self._submitter(client, self._intent(action),
                                                   engine=self._engine(), entry=False)
            self._invalidate()
            if not result.ok:
                return observation("REJECTED", reason=result.rejreason)
            if not result.norenordno:
                return observation("UNKNOWN", reason="accepted response missing broker id")
            return observation("OPEN", broker_id=result.norenordno)
        except Exception as exc:
            self._invalidate()
            return observation("UNKNOWN", reason=f"submission outcome unknown: {type(exc).__name__}: {exc}")

    async def _sell_capacity(self, action):
        owned = [a for a in action.get("owned_actions", []) if a.get("id") != action["id"]]
        if not owned:
            return {"ok": False, "reason": "sell requires persisted owned-action fill evidence"}
        check = await self.reconcile({"contract": action["contract"], "actions": owned}, fresh=True)
        if not check["ok"]:
            return check
        rows = await self._book()
        c = action["contract"]
        reserved = 0
        for row in rows:
            if (row.get("tsym") != c["tsym"] or row.get("exch") != c["exchange"]
                    or row.get("trantype") != "S"):
                continue
            status = str(row.get("status") or "").upper().replace(" ", "_")
            if status in ("COMPLETE", "CANCELED", "CANCELLED", "REJECTED", "REJECT"):
                continue
            # Treat any nonterminal sell, including unknown status, as reserved.
            qty = positive(row.get("qty"), "working sell quantity", integer=True)
            filled = float(row.get("fillshares", 0))
            if not math.isfinite(filled) or filled < 0 or filled > qty or not filled.is_integer():
                return {"ok": False, "reason": "working sell fill evidence invalid"}
            reserved += qty - int(filled)
        if reserved + action["quantity"] > check["actual_qty"]:
            return {"ok": False, "reason": "sell exceeds broker residual after working sell reservations"}
        return {"ok": True}

    async def quote(self, contract):
        """Authenticated fallback for stale-feed exits; never fabricates freshness."""
        client = await self._client()
        token = contract.get("token")
        if not token:
            raise ValueError("resolved broker token required for fallback quote")
        before = self._clock()
        quote = await client.get_quotes(contract["exchange"], token)
        if quote.get("stat") != "Ok":
            raise ValueError("broker quote unavailable")
        return {"price": positive(quote.get("lp"), "broker premium"), "ts": before}

    async def exit_reference(self, order):
        actions = order.get("actions") or []
        if isinstance(actions, dict):
            actions = actions.values()
        contract = next((a["contract"] for a in actions
                         if a.get("kind") == "entry" and (a.get("contract") or {}).get("token")),
                        order.get("contract") or {})
        return (await self.quote(contract))["price"]

    @staticmethod
    def _match(action, rows):
        prior = action.get("observation") or {}
        broker_id = action.get("broker_id") or prior.get("broker_id")
        matching = [r for r in rows if
                    (broker_id and str(r.get("norenordno")) == str(broker_id))
                    or str(r.get("remarks") or "") == action["id"]]
        if len(matching) != 1:
            return observation("ABSENT" if not matching else "UNKNOWN", broker_id=broker_id,
                               reason="no matching broker order" if not matching else "duplicate broker identity")
        row = matching[0]
        c = action["contract"]
        if (row.get("tsym") != c.get("tsym") or row.get("exch") != c.get("exchange")
                or row.get("trantype") != action["side"] or row.get("prd") != "I"
                or str(row.get("remarks") or "") != action["id"]
                or row.get("prctyp") != price_type(action)):
            return observation("UNKNOWN", broker_id=broker_id, reason="broker identity contradicts persisted action")
        amended = False
        try:
            observed_limit = positive(row.get("prc"), "broker limit")
            observed_trigger = positive(row.get("trgprc"), "broker trigger") if price_type(action) == 'SL-LMT' else None
            observed_pair = (observed_limit, observed_trigger)
            original_pair = (action["limit_price"], action.get("trigger_price") if price_type(action) == 'SL-LMT' else None)
            pending = action.get("pending_amendment")
            if pending:
                pending_limit = positive(pending.get("limit_price"), "pending limit")
                pending_trigger = positive(pending.get("trigger_price"), "pending trigger") if price_type(action) == 'SL-LMT' else None
                validate_action(dict(action, **pending))
                if action['kind'] == 'stop' and pending_trigger < action['trigger_price']:
                    raise ValueError("invalid persisted amendment")
                amended = observed_pair == (pending_limit, pending_trigger)
            if observed_pair != original_pair and not amended:
                raise ValueError("broker prices differ from durable original and pending intent")
            qty = positive(row.get("qty"), "broker quantity", integer=True)
            filled = float(row.get("fillshares", 0))
            if not math.isfinite(filled) or not filled.is_integer() or filled < 0 or filled > qty or qty != action["quantity"]:
                raise ValueError("contradictory filled quantity")
            filled = int(filled)
            price = positive(row.get("avgprc"), "fill average") if filled else None
        except (ValueError, TypeError, OverflowError):
            return observation("UNKNOWN", broker_id=broker_id, reason="invalid broker fill evidence")
        raw_status = str(row.get("status") or "").upper().replace(" ", "_")
        mapping = {"OPEN": "OPEN", "PENDING": "OPEN", "TRIGGER_PENDING": "OPEN",
                   "COMPLETE": "COMPLETE", "CANCELED": "CANCELED", "CANCELLED": "CANCELED",
                   "REJECTED": "REJECTED", "REJECT": "REJECTED", "PARTIAL": "OPEN"}
        status = mapping.get(raw_status, "UNKNOWN")
        if status == "COMPLETE" and filled != qty:
            status = "UNKNOWN"
        previous_fill = prior.get("filled_qty", 0)
        if filled < previous_fill:
            return observation("UNKNOWN", broker_id=broker_id, reason="cumulative fill moved backwards")
        result = observation(status, broker_id=str(row.get("norenordno")), filled_qty=filled,
                             fill_price=price, reason=row.get("rejreason"))
        if action["kind"] == "stop" or pending:
            result.update(amended=amended, limit_price=observed_limit, trigger_price=observed_trigger)
        return result

    async def observe(self, action):
        try:
            return self._match(action, await self._book())
        except Exception as exc:
            return observation("UNKNOWN", broker_id=(action.get("observation") or {}).get("broker_id"),
                               reason=f"broker observation failed: {exc}")

    async def cancel(self, action):
        from app.live.exit_claims import claim_exit
        async with claim_exit(action["contract"].get("tsym"), label="conditional-cancel") as got:
            if not got:
                return observation("UNKNOWN", reason="symbol busy; cancellation not submitted")
            return await self._cancel(action)

    async def _cancel(self, action):
        try:
            before = self._match(action, await self._book(fresh=True))
            if before["status"] in ("COMPLETE", "CANCELED", "REJECTED", "UNKNOWN", "ABSENT"):
                return before
            client = await self._client()
            await client.cancel_order(before["broker_id"])
            self._invalidate()
            # Cancellation ACK is not evidence of cancellation or zero fills.
            return self._match(action, await self._book(fresh=True))
        except Exception as exc:
            self._invalidate()
            return observation("UNKNOWN", reason=f"cancel outcome unresolved: {exc}")

    async def amend_stop(self, action, trigger, limit):
        from app.live.exit_claims import claim_exit
        async with claim_exit(action["contract"].get("tsym"), label="conditional-amend") as got:
            if not got:
                return dict(observation("UNKNOWN", reason="symbol busy; modification not submitted"), amended=False)
            return await self._amend_stop(action, trigger, limit)

    async def _amend_stop(self, action, trigger, limit):
        try:
            validate_action(dict(action, trigger_price=trigger, limit_price=limit))
            if action["kind"] != "stop" or trigger < action["trigger_price"]:
                raise ValueError("a protective stop must never loosen")
            if action.get("pending_amendment") != {"trigger_price": trigger, "limit_price": limit}:
                raise ValueError("persist pending amendment before transmission")
            before = self._match(action, await self._book(fresh=True))
            if before["status"] != "OPEN":
                return dict(before, amended=bool(before.get("amended")))
            if before.get("amended"):
                return before
            client = await self._client()
            result = await client.modify_order(before["broker_id"], prc=limit, trgprc=trigger)
            self._invalidate()
            after = self._match(action, await self._book(fresh=True))
            if not result.ok:
                after["reason"] = f"stop amendment not acknowledged: {result.rejreason}"
            return dict(after, amended=bool(after.get("amended")))
        except Exception as exc:
            self._invalidate()
            return dict(observation("UNKNOWN", reason=f"stop amendment unresolved: {exc}"), amended=False)

    async def amend_entry(self, action):
        from app.live.exit_claims import claim_exit
        async with claim_exit(action['contract'].get('tsym'), label='conditional-entry-amend') as got:
            if not got:
                return dict(observation('UNKNOWN', reason='symbol busy'), amended=False)
            try:
                pending = action.get('pending_amendment')
                if action['kind'] != 'entry' or not pending:
                    raise ValueError('Persisted entry amendment required')
                validate_action({**action, **pending})
                before = self._match(action, await self._book(fresh=True))
                if before['status'] != 'OPEN' or before.get('filled_qty') or before.get('amended'):
                    return dict(before, amended=bool(before.get('amended')))
                allowed, why = await self._engine().can_trade()
                if not allowed:
                    return dict(before, amended=False, reason=why)
                client = await self._client()
                args = {'prc': pending['limit_price']}
                if pending.get('trigger_price') is not None:
                    args['trgprc'] = pending['trigger_price']
                await client.modify_order(before['broker_id'], **args)
                self._invalidate()
                after = self._match(action, await self._book(fresh=True))
                return dict(after, amended=bool(after.get('amended')))
            except Exception as exc:
                self._invalidate()
                return dict(observation('UNKNOWN', reason=f'entry amendment unresolved: {exc}'), amended=False)

    @staticmethod
    def _terminal_evidence(action):
        """Prior broker-confirmed terminal fills survive a daily book rollover."""
        prior = action.get("observation") or {}
        if (action.get("status") == "CANCELED" and action.get("reason") == "latch_before_transmit"
                and not action.get("broker_id") and not prior and not action.get("filled_qty", 0)):
            return observation("CANCELED")
        if prior.get("status") not in ("COMPLETE", "CANCELED", "REJECTED"):
            return None
        try:
            qty = positive(action["quantity"], "quantity", integer=True)
            filled = float(prior.get("filled_qty", 0))
            if not math.isfinite(filled) or not filled.is_integer() or not 0 <= filled <= qty:
                return None
            if prior["status"] == "COMPLETE" and filled != qty:
                return None
            if filled:
                positive(prior.get("fill_price"), "persisted fill average")
                if not prior.get("broker_id"):
                    return None
            if (action.get("broker_id") and prior.get("broker_id")
                    and action["broker_id"] != prior["broker_id"]):
                return None
            return dict(prior, filled_qty=int(filled))
        except (ValueError, TypeError, KeyError):
            return None

    async def reconcile(self, order, *, fresh=False):
        """Detect symbol-sharing exposure; never adopt a foreign position."""
        try:
            actions = order.get("actions") or []
            if isinstance(actions, dict):
                actions = list(actions.values())
            contract = order.get("contract") or order.get("execution_contract")
            if (not contract or not contract.get("tsym")) and actions:
                contract = actions[0].get("contract")
            if not contract:
                return {"ok": False, "reason": "execution contract unresolved", "flat": False}
            if not contract.get("tsym"):
                contract = await self._resolve_contract(contract)
            rows = await self._book(fresh=fresh)
            ids = {a["id"] for a in actions}
            relevant = [r for r in rows if r.get("tsym") == contract["tsym"]
                        and r.get("exch") == contract["exchange"]]
            if any(str(r.get("remarks") or "") not in ids and
                   str(r.get("status") or "").upper() not in ("COMPLETE", "CANCELED", "CANCELLED", "REJECTED", "REJECT")
                   for r in relevant):
                return {"ok": False, "reason": "foreign working order on execution contract",
                        "conflict": "foreign working order", "flat": False}
            expected = 0
            for action in actions:
                if ((action.get("contract") or {}).get("tsym") != contract["tsym"]
                        or (action.get("contract") or {}).get("exchange") != contract["exchange"]):
                    return {"ok": False, "reason": "owned action contract identity differs", "flat": False}
                obs = self._match(action, rows)
                if obs["status"] == "ABSENT":
                    obs = self._terminal_evidence(action) or obs
                if obs["status"] in ("UNKNOWN", "ABSENT"):
                    return {"ok": False, "reason": "action reconciliation unresolved", "flat": False}
                expected += obs["filled_qty"] * (1 if action["side"] == "B" else -1)
            positions = await self._position_book(fresh=fresh)
            matching = [p for p in positions if p.get("tsym") == contract["tsym"]
                        and p.get("exch") == contract["exchange"]]
            actual = 0
            for pos in matching:
                q = float(pos.get("netqty"))
                if not math.isfinite(q) or not q.is_integer():
                    raise ValueError("invalid position quantity")
                if q and pos.get("prd") != "I":
                    return {"ok": False, "reason": "foreign product exposure on execution contract",
                            "conflict": "foreign product exposure", "flat": False}
                actual += int(q)
            if expected < 0 or actual != expected:
                return {"ok": False, "reason": "broker position differs from owned filled residual",
                        "expected_qty": expected, "actual_qty": actual,
                        "flat": actual == 0 and not any(str(r.get("status", "")).upper() in
                            ("OPEN", "PENDING", "TRIGGER_PENDING", "PARTIAL") for r in relevant),
                        "conflict": "external close or unowned exposure; exit fill attribution unavailable"}
            return {"ok": True, "reason": None, "expected_qty": expected, "actual_qty": actual,
                    "flat": actual == 0 and not any(str(r.get("status", "")).upper() in
                        ("OPEN", "PENDING", "TRIGGER_PENDING", "PARTIAL") for r in relevant)}
        except Exception as exc:
            return {"ok": False, "reason": f"ownership reconciliation failed: {exc}", "flat": False}
