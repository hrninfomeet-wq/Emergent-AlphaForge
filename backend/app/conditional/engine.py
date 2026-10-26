"""Durable conditional lifecycle. No browser timers or implicit broker retries."""
from __future__ import annotations

import asyncio
import copy
import hashlib
import logging
import math
import time
import uuid
from datetime import datetime
from decimal import Decimal, ROUND_FLOOR

from .models import normalize_spec
from .rules import (IST, arm_condition_ok, deadline, entry_ready, evaluate_exit,
                    exit_state, price_limit, resolve_contract, split_quantity, time_today)
from .store import Conflict, TERMINAL

log = logging.getLogger(__name__)
WORKING = {'SUBMITTING', 'OPEN', 'UNKNOWN', 'ABSENT', 'PARTIAL', 'TRIGGER_PENDING'}
FINAL_ACTION = {'COMPLETE', 'CANCELED', 'REJECTED'}


def fresh(tick, now, seconds):
    try:
        p, ts = float(tick['price']), float(tick['ts'])
        return math.isfinite(p) and p > 0 and math.isfinite(ts) and 0 <= now - ts <= seconds
    except (KeyError, TypeError, ValueError):
        return False


def market_open(now):
    from app.session_spec import session_spec
    d = datetime.fromtimestamp(now, IST)
    session = session_spec(d.date().isoformat(), 'options')
    return session.expected_candles > 0 and session.contains(d.hour * 60 + d.minute)


def event(order, name, now, **details):
    order.setdefault('events', []).append({'at': now, 'event': name, **details})
    # Bound the API document; broker action/fill records are never truncated.
    order['events'] = order['events'][-1000:]
    order['updated_at'] = now


def alert(order, reason, now):
    if order.get('last_alert') != reason:
        order.setdefault('alerts', []).append({'at': now, 'message': reason})
        order['alerts'] = order['alerts'][-100:]
        order['last_alert'] = reason
        event(order, 'ALERT', now, reason=reason)
        log.error('Conditional %s: %s', order['_id'], reason)


def observation(action, value):
    """Never replace known fills with a missing book or decreasing snapshot."""
    if not isinstance(value, dict):
        raise ValueError('invalid broker observation')
    qty = value.get('filled_qty', 0)
    if isinstance(qty, bool) or not isinstance(qty, (int, float)) or not math.isfinite(qty) or int(qty) != qty:
        raise ValueError('invalid filled quantity')
    qty = int(qty)
    old_qty = int(action.get('filled_qty', 0))
    if qty < old_qty and value.get('status') not in ('UNKNOWN', 'ABSENT'):
        raise ValueError('broker cumulative fill decreased')
    if qty < 0 or qty > action['quantity']:
        raise ValueError('broker fill exceeds claimed quantity')
    if qty > 0:
        px = value.get('fill_price')
        if not isinstance(px, (int, float)) or isinstance(px, bool) or not math.isfinite(px) or px <= 0:
            raise ValueError('positive fill has no proven price')
    if qty >= old_qty and value.get('status') not in ('UNKNOWN', 'ABSENT'):
        action['filled_qty'] = qty
        action['fill_price'] = value.get('fill_price')
    status = value.get('status', 'UNKNOWN')
    if status not in WORKING | FINAL_ACTION:
        status = 'UNKNOWN'
    action['status'] = status
    if value.get('amended') and action.get('pending_amendment'):
        action.update(action.pop('pending_amendment'))
    if value.get('broker_id'):
        action['broker_id'] = str(value['broker_id'])
    action['observation'] = copy.deepcopy(value)


def exit_chunks(quantity, contract):
    """Close actual filled units, including a broker-reported partial lot."""
    lot, freeze = contract['lot_size'], contract['freeze_quantity']
    cap = freeze // lot * lot
    full, tail = divmod(quantity, cap)
    return [cap] * full + ([tail] if tail else [])


def sell_limit(reference, tick_size):
    """Exit limits use a tick floor; a narrow entry band must not block closing."""
    ref, tick = Decimal(str(reference)), Decimal(str(tick_size))
    if not ref.is_finite() or not tick.is_finite() or ref <= 0 or tick <= 0:
        raise ValueError('Positive exit price and tick required')
    return float(max(tick, (ref * Decimal('0.995') / tick).to_integral_value(rounding=ROUND_FLOOR) * tick))


class ConditionalEngine:
    def __init__(self, store, market, gateways, *, now=time.time, session_open=market_open, max_lots=5):
        self.store, self.market, self.gateways = store, market, gateways
        self.now, self.session_open, self.max_lots = now, session_open, max_lots
        self.owner = uuid.uuid4().hex
        self.task = None
        self.stopping = False
        self.last_error = None
        self.last_progress = self.now()
        self.recovery_only = False

    async def live_ready(self):
        gateway = self.gateways['live']
        fn = getattr(gateway, 'ready', None)
        if fn:
            return await fn()
        return False, 'Live adapter readiness unavailable'

    async def preview(self, payload):
        spec = normalize_spec(payload, self.max_lots)
        now = self.now()
        if not self.session_open(now):
            raise ValueError('Arming is available during the exchange trading session only')
        if not self.session_open(time_today(spec['exits']['square_off'], now)):
            raise ValueError('Forced square-off must fall inside the exchange trading session')
        control = await self.store.control(spec['mode'])
        if control['blocked']:
            raise ValueError(f"{spec['mode']} conditional kill latch is set")
        contracts = await self.market.contracts(spec['underlying'])
        snap = await self.market.snapshot(spec)
        if not fresh(snap.get('spot'), now, spec['stale_seconds']):
            raise ValueError('Fresh underlying tick required; connect the market data feed')
        contract = resolve_contract(spec, contracts, snap['spot']['price'])
        await self.market.ensure_subscription([contract])
        snap = await self.market.snapshot(spec, reference_contract=contract)
        if not fresh(snap.get('reference'), now, spec['stale_seconds']):
            raise ValueError('Waiting for a fresh option tick; preview again after the subscription receives data')
        if not arm_condition_ok(spec, snap['spot']['price']):
            raise ValueError('Cross condition is already true; use level mode or change the level')
        premium = snap['reference']['price']
        exit_state(spec, premium)  # refuse a points stop that is already nonsensical
        due = deadline(spec, now)
        cap = spec.get('max_entry_premium') or premium
        qty = spec['lots'] * contract['lot_size']
        stop = spec['exits']['stop']
        loss = cap * stop['value'] / 100 if stop['unit'] == 'percent' else stop['value']
        live_available, live_reason = await self.live_ready() if spec['mode'] == 'live' else (False, 'Paper mode')
        if spec['mode'] == 'live' and not live_available:
            raise ValueError(live_reason)
        oid = uuid.uuid4().hex
        reference = {'contract': contract, 'premium': premium, 'spot': snap['spot']['price'], 'at': now}
        summary = {'instrument': spec['underlying'], 'option_type': spec['option_type'],
                   'expiry': spec['expiry'], 'strike_rule': spec['strike'], 'lots': spec['lots'],
                   'quantity': qty, 'planned_loss': round(qty * min(loss, cap), 2),
                   'premium_exposure': round(qty * cap, 2), 'deadline': due,
                   'max_entry_premium': spec.get('max_entry_premium'),
                   'conditions': {'spot': spec['spot_condition'], 'premium': spec['premium_condition']},
                   'protection': 'Broker SL-LMT' if self.gateways[spec['mode']].protection_supported else 'Software only',
                   'warning': 'Stop loss is not a guaranteed maximum loss. Full premium plus charges is at risk. Host/session loss can prevent flattening.'}
        doc = {'_id': oid, 'id': oid, 'version': 0, 'mode': spec['mode'], 'spec': spec,
               'status': 'DRAFT', 'created_at': now, 'updated_at': now, 'preview_until': now + 120,
               'summary': summary, 'reference': reference, 'actions': [], 'events': [], 'alerts': [],
               'generation': control['generation'], 'cancel_requested': False}
        await self.store.insert(doc)
        return {'preview_id': oid, 'summary': summary, 'reference': reference,
                'expires_at': doc['preview_until'], 'live_available': live_available}

    async def arm(self, oid, confirm_live=False):
        doc = await self.store.get(oid)
        if not doc:
            raise ValueError('Preview not found')
        # Network retry can retrieve the same armed order, never create another.
        if doc['status'] != 'DRAFT':
            return doc
        if doc.get('cancel_requested'):
            raise ValueError('Preview was canceled; prepare a new order')
        now, spec = self.now(), doc['spec']
        if now >= doc['preview_until']:
            raise ValueError('Preview expired; prepare a new summary')
        if not self.session_open(now):
            raise ValueError('Market is closed')
        if doc['mode'] == 'live':
            if confirm_live is not True:
                raise ValueError('Explicit live confirmation required')
            ready, why = await self.live_ready()
            if not ready:
                raise ValueError(why)
        control = await self.store.control(doc['mode'])
        if control['blocked'] or control['generation'] != doc['generation']:
            raise ValueError('Kill/reset state changed; create a fresh preview')
        contract = doc['reference']['contract']
        snap = await self.market.snapshot(spec, reference_contract=contract)
        if not all(fresh(snap.get(k), now, spec['stale_seconds']) for k in ('spot', 'reference')):
            raise ValueError('Fresh spot and reference-contract ticks required at arm time')
        if not arm_condition_ok(spec, snap['spot']['price']):
            raise ValueError('Cross condition is already true at arm time')
        exit_state(spec, snap['reference']['price'])
        doc.update(status='ARMED', armed_at=now, deadline=deadline(spec, now),
                   square_off_at=time_today(spec['exits']['square_off'], now),
                   previous_spot=snap['spot']['price'], previous_spot_ts=snap['spot']['ts'])
        doc['reference'] = {'contract': contract, 'premium': snap['reference']['price'],
                            'spot': snap['spot']['price'], 'at': now}
        event(doc, 'ARMED', now, reference_premium=doc['reference']['premium'],
              reference_contract=contract['instrument_key'])
        await self.store.save(doc)
        return doc

    async def kill(self, mode):
        control = await self.store.latch(mode, True)
        affected = []
        for doc in await self.store.list(active=True):
            if doc['mode'] == mode:
                await self.store.request_cancel(doc['_id'])
                affected.append(doc['_id'])
        return {'mode': mode, 'blocked': True, 'orders': affected, 'control': control,
                'message': 'Entries blocked; cancellation/flatten requested, awaiting reconciliation'}

    async def reset(self, mode):
        # Reset permits future previews only; generation fences old arms.
        return await self.store.latch(mode, False)

    async def _save(self, doc):
        doc['updated_at'] = self.now()
        await self.store.save(doc)

    async def _terminal(self, doc, status, reason):
        doc['status'] = status
        event(doc, status, self.now(), reason=reason)
        await self._save(doc)
        await self.store.release(doc)

    async def _allowed(self, doc):
        control = await self.store.control(doc['mode'])
        current = await self.store.get(doc['_id'])
        return (not control['blocked'] and control['generation'] == doc['generation']
                and current and not current.get('cancel_requested'))

    async def _dispatch(self, doc, *, kind, qty, premium, limit=None, trigger=None):
        if kind == 'entry' and self.recovery_only:
            return
        if kind == 'entry' and not await self._allowed(doc):
            doc['cancel_requested'] = True
            await self._save(doc)
            return
        contract, spec = doc['contract'], doc['spec']
        side = 'B' if kind == 'entry' else 'S'
        if limit is None:
            limit = (price_limit(premium, spec['band_pct'], contract['tick_size'], side)
                     if kind == 'entry' else sell_limit(premium, contract['tick_size']))
        if kind == 'entry' and spec.get('max_entry_premium') is not None:
            cap = spec['max_entry_premium']
            if premium > cap:
                raise ValueError('Execution premium exceeds confirmed maximum entry premium')
            tick = Decimal(str(contract['tick_size']))
            limit = min(limit, float((Decimal(str(cap)) / tick).to_integral_value(rounding=ROUND_FLOOR) * tick))
        seq = len(doc['actions'])
        action = {'id': hashlib.sha256(f"{doc['_id']}:{seq}".encode()).hexdigest()[:28],
                  'kind': kind, 'side': side, 'quantity': qty, 'contract': copy.deepcopy(contract),
                  'limit_price': limit, 'reference_price': premium, 'trigger_price': trigger,
                  'submitted_at': self.now(), 'filled_qty': 0, 'fill_price': None, 'status': 'SUBMITTING'}
        gateway = self.gateways[doc['mode']]
        prepared = await gateway.prepare(action)
        if prepared:
            action = prepared
        doc['contract'] = copy.deepcopy(action['contract'])
        if kind == 'entry' and not await self._allowed(doc):
            return
        doc['actions'].append(action)
        event(doc, 'ACTION_CLAIMED', self.now(), action=action['id'], kind=kind, quantity=qty)
        await self._save(doc)  # durable BEFORE side effect; CAS failure forbids send
        heartbeat = await self.store.heartbeat()
        if heartbeat.get('owner') != self.owner or heartbeat.get('until', 0) <= self.now():
            # Leave claimed/unknown for reconciliation; a stale worker cannot send.
            return
        if kind == 'entry' and not await self._allowed(doc):
            action['status'] = 'CANCELED'
            action['reason'] = 'latch_before_transmit'
            await self._save(doc)
            return
        try:
            # Only a transient copy carries sibling evidence, never recursively
            # nest the full order inside its persisted action documents.
            sending = dict(action, owned_actions=copy.deepcopy(doc['actions'][:-1]))
            result = await gateway.submit(sending)
            observation(action, result)
            if action['status'] == 'REJECTED':
                event(doc, 'ACTION_REJECTED', self.now(), action=action['id'], reason=result.get('reason'))
        except Exception as exc:
            action['status'] = 'UNKNOWN'
            alert(doc, f"{kind} acknowledgement unknown; reconcile before any retry: {exc}", self.now())
        await self._save(doc)

    async def _observe(self, doc, premium=None):
        gateway = self.gateways[doc['mode']]
        for action in doc['actions']:
            if action['status'] not in FINAL_ACTION:
                action['reference_price'] = premium
                result = await gateway.observe(action)
                old = (action.get('filled_qty', 0), action['status'])
                observation(action, result)
                if old != (action.get('filled_qty', 0), action['status']):
                    event(doc, 'BROKER_OBSERVATION', self.now(), action=action['id'],
                          status=action['status'], filled_qty=action['filled_qty'])
        await self._save(doc)

    async def _cancel_actions(self, doc, kinds):
        gateway = self.gateways[doc['mode']]
        pending = False
        for action in doc['actions']:
            if action['kind'] not in kinds or action['status'] in FINAL_ACTION:
                continue
            if action['status'] in ('UNKNOWN', 'ABSENT', 'SUBMITTING') and not action.get('broker_id'):
                pending = True
                alert(doc, 'Order outcome unknown: cannot prove cancellation; reconciliation continues', self.now())
                continue
            action['cancel_requested_at'] = self.now()
            await self._save(doc)
            result = await gateway.cancel(action)
            observation(action, result)
            pending = pending or action['status'] not in FINAL_ACTION
            await self._save(doc)
        return not pending

    def _totals(self, doc):
        buys = [a for a in doc['actions'] if a['kind'] == 'entry']
        sells = [a for a in doc['actions'] if a['kind'] != 'entry']
        bought = sum(a.get('filled_qty', 0) for a in buys)
        sold = sum(a.get('filled_qty', 0) for a in sells)
        if sold > bought:
            raise ValueError('Exit fills exceed entry fills; manual reconciliation required')
        vwap = sum(a.get('filled_qty', 0) * (a.get('fill_price') or 0) for a in buys) / bought if bought else None
        doc['filled_quantity'], doc['closed_quantity'], doc['entry_fill_price'] = bought, sold, vwap
        return bought, sold, vwap

    async def _protect(self, doc, residual, premium):
        gateway = self.gateways[doc['mode']]
        if not gateway.protection_supported:
            doc['protection'] = 'software_only'
            return
        stops = [a for a in doc['actions'] if a['kind'] == 'stop' and a['status'] not in FINAL_ACTION]
        coverage = sum(a['quantity'] - a.get('filled_qty', 0) for a in stops)
        if any(a['status'] in ('UNKNOWN', 'ABSENT', 'SUBMITTING') for a in stops):
            alert(doc, 'Protective stop outcome unknown; entry expansion blocked', self.now())
            return
        tick = Decimal(str(doc['contract']['tick_size']))
        trigger = float(max(tick, (Decimal(str(doc['exit_state']['stop'])) / tick).to_integral_value(rounding=ROUND_FLOOR) * tick))
        limit = sell_limit(trigger, float(tick))
        if coverage != residual:
            if stops and not await self._cancel_actions(doc, {'stop'}):
                return
            # Stop could fill during cancellation. Caller must re-evaluate totals
            # before replacing it or submitting another entry.
            if self._totals(doc)[1] != doc.get('protection_sold_basis', 0):
                return
            for qty in exit_chunks(residual, doc['contract']):
                await self._dispatch(doc, kind='stop', qty=qty, premium=premium, limit=limit, trigger=trigger)
            if any(a['kind'] == 'stop' and a['status'] == 'REJECTED' for a in doc['actions']):
                alert(doc, 'Broker rejected protective stop; flatten requested', self.now())
                doc['exit_reason'] = 'protection_failed'
            doc['protection'] = 'broker_stop_pending_readback'
        else:
            for action in stops:
                if trigger > (action.get('trigger_price') or 0):
                    # Save desired ratchet first; a failed modify does not erase
                    # the old broker stop or permit a second sell.
                    action['desired_trigger'] = trigger
                    action['pending_amendment'] = {'trigger_price': trigger, 'limit_price': limit}
                    await self._save(doc)
                    result = await gateway.amend_stop(action, trigger, limit)
                    observation(action, result)
                    if result.get('amended'):
                        action.update(trigger_price=trigger, limit_price=limit)
                    else:
                        alert(doc, 'Protective stop amendment not confirmed; software guard remains active', self.now())
            doc['protection'] = 'broker_stop'

    async def step(self, doc):
        now, spec = self.now(), doc['spec']
        control = await self.store.control(doc['mode'])
        killed = control['blocked'] or control['generation'] != doc['generation'] or doc.get('cancel_requested')
        if doc['status'] == 'ARMED':
            if self.recovery_only:
                return await self._terminal(doc, 'CANCELED', 'backend_heartbeat_lost')
            if killed:
                return await self._terminal(doc, 'CANCELED', 'kill_or_cancel')
            if now >= doc['deadline']:
                return await self._terminal(doc, 'EXPIRED', 'validity_elapsed')
            if not self.session_open(now):
                return await self._terminal(doc, 'EXPIRED', 'session_closed')
            replay = getattr(self.market, 'entry_snapshots', None)
            snapshots = (await replay(spec, doc['reference']['contract'], doc.get('previous_spot_ts', 0))
                         if replay else [await self.market.snapshot(spec, reference_contract=doc['reference']['contract'])])
            needed = ['spot'] + (['reference'] if spec.get('premium_condition') else [])
            ready = False
            for snap in snapshots:
                if not all(fresh(snap.get(k), now, spec['stale_seconds']) for k in needed):
                    doc['previous_spot'] = None  # never synthesize a crossing over a feed gap
                    continue
                spot, stamp = snap['spot']['price'], snap['spot']['ts']
                previous_stamp = doc.get('previous_spot_ts', 0)
                previous = (doc.get('previous_spot') if not snap.get('reset_cross')
                            and 0 < stamp - previous_stamp <= spec['stale_seconds'] else None)
                ready = entry_ready(spec, previous, spot, (snap.get('reference') or {}).get('price'), doc['reference']['premium'])
                doc.update(previous_spot=spot, previous_spot_ts=stamp)
                if ready:
                    break
            if not ready:
                if doc.get('previous_spot') is None:
                    alert(doc, 'Stale or incomplete entry feed: no entry; waiting within validity', now)
                return await self._save(doc)
            contract = resolve_contract(spec, await self.market.contracts(spec['underlying']), spot)
            if contract['lot_size'] != doc['reference']['contract']['lot_size']:
                return await self._terminal(doc, 'REJECTED', 'master_lot_changed_since_confirmation')
            if not await self.store.reserve(doc['mode'], contract['instrument_key'], doc['_id']):
                return await self._terminal(doc, 'REJECTED', 'contract_owned_by_another_conditional_order')
            doc.update(status='TRIGGERED', contract=contract, triggered_at=now,
                       trigger_spot=spot, resolved_strike=contract['strike'],
                       desired_quantity=spec['lots'] * contract['lot_size'],
                       entry_until=min(doc['deadline'], now + spec['entry_timeout_seconds']))
            event(doc, 'TRIGGERED', now, spot=spot, strike=contract['strike'], contract=contract['instrument_key'])
            await self._save(doc)
            await self.market.ensure_subscription([contract])
            return

        if 'contract' not in doc:
            return
        gateway = self.gateways[doc['mode']]
        if self.recovery_only:
            doc['entry_abandoned'] = True
            alert(doc, 'Backend heartbeat lost; independent watchdog has taken over reconciliation', now)
            if spec['failsafe'] == 'flatten':
                doc['exit_reason'] = doc.get('exit_reason') or 'backend_heartbeat_lost'
        # Re-subscribe after a backend restart without re-resolving the contract.
        try:
            await self.market.ensure_subscription([doc['contract']])
            snap = await self.market.snapshot(spec, execution_contract=doc['contract'])
        except Exception as exc:
            snap = {}
            alert(doc, f'Market feed unavailable: {exc}', now)
        tick = snap.get('execution')
        tick_fresh = fresh(tick, now, spec['stale_seconds'])
        premium = tick['price'] if tick_fresh else None
        if premium is not None:
            doc['last_premium'], doc['last_tick_at'] = premium, tick['ts']
        # Broker reads are required even during feed loss and after restarts.
        await self._observe(doc, premium)
        if self.recovery_only:
            await self._cancel_actions(doc, {'entry'})
        bought, sold, vwap = self._totals(doc)
        residual = bought - sold
        reconciliation = await gateway.reconcile(doc)
        if reconciliation.get('flat') and bought and not residual == 0:
            # An external/global kill can close the account position. Do not
            # invent an exit fill price or realized P&L for that close.
            if await self._cancel_actions(doc, {'entry', 'stop', 'exit'}):
                doc['external_flat'] = True
                return await self._terminal(doc, 'CLOSED', 'broker_flat_external_exit_fill_unverified')
        if not reconciliation.get('ok', False):
            alert(doc, reconciliation.get('conflict') or reconciliation.get('reason') or 'Broker reconciliation unavailable', now)
            doc['status'] = 'NEEDS_ATTENTION'
            if bought and spec['failsafe'] == 'flatten':
                doc['exit_reason'] = doc.get('exit_reason') or 'reconciliation_failure'
            return await self._save(doc)
        if bought:
            try:
                doc['exit_state'] = exit_state(spec, vwap, doc.get('exit_state'))
            except ValueError as exc:
                doc['exit_reason'] = 'invalid_fill_protection'
                alert(doc, str(exc), now)
        if any(a['kind'] == 'stop' and a.get('filled_qty', 0) > 0 for a in doc['actions']):
            doc['exit_reason'] = doc.get('exit_reason') or 'broker_stop'
        if killed:
            doc['exit_reason'] = 'kill_or_cancel'
        elif now >= doc['square_off_at']:
            doc['exit_reason'] = 'forced_square_off'
        elif not tick_fresh and bought:
            alert(doc, 'Stale feed: ' + ('flatten requested' if spec['failsafe'] == 'flatten' else 'alert-only policy'), now)
            if spec['failsafe'] == 'flatten':
                doc['exit_reason'] = 'stale_feed'
        if premium is not None and bought and doc.get('exit_state'):
            state, reason = evaluate_exit(spec, doc['exit_state'], premium)
            doc['exit_state'] = state
            if reason:
                doc['exit_reason'] = doc.get('exit_reason') or reason
        # Any partial entry cancels its remainder before expanding exposure.
        partial = any(a['kind'] == 'entry' and 0 < a.get('filled_qty', 0) < a['quantity'] for a in doc['actions'])
        if partial:
            doc['entry_abandoned'] = True
            await self._cancel_actions(doc, {'entry'})
        if doc.get('exit_reason'):
            doc['status'] = 'EXITING'
            await self._save(doc)  # persist winner/ratchet before cancellation/transmit
            if not await self._cancel_actions(doc, {'entry', 'stop'}):
                return
            bought, sold, vwap = self._totals(doc)
            residual = bought - sold
            if not residual:
                return await self._terminal(doc, 'CLOSED' if bought else 'CANCELED', doc['exit_reason'])
            pending = [a for a in doc['actions'] if a['kind'] == 'exit' and a['status'] not in FINAL_ACTION]
            if pending:
                if all(now - a['submitted_at'] >= spec['entry_timeout_seconds'] for a in pending):
                    await self._cancel_actions(doc, {'exit'})
                return
            if now < doc.get('next_exit_attempt', 0):
                return
            doc['next_exit_attempt'] = now + 5
            event(doc, 'OCO_WINNER', now, winner=doc['exit_reason'], other='canceled_before_exit')
            # Live prepare obtains a fresh broker mark if the primary feed is
            # unavailable. Paper cannot fabricate a fill from a stale tick.
            if premium is None:
                getter = getattr(gateway, 'exit_reference', None)
                premium = await getter(doc) if getter else None
            if premium is None:
                alert(doc, 'Flatten pending: no usable exit price; position is still open', now)
                return await self._save(doc)
            chunks = exit_chunks(residual, doc['contract'])
            for qty in chunks:
                await self._dispatch(doc, kind='exit', qty=qty, premium=premium)
            return
        if residual:
            doc['protection_sold_basis'] = sold
            await self._protect(doc, residual, premium or doc.get('last_premium') or vwap)
            await self._save(doc)
            if doc.get('exit_reason'):
                return
        if bought and residual == 0:
            if await self._cancel_actions(doc, {'entry', 'stop', 'exit'}):
                return await self._terminal(doc, 'CLOSED', 'confirmed_exit_fills')
        entries = [a for a in doc['actions'] if a['kind'] == 'entry']
        working_entries = [a for a in entries if a['status'] not in FINAL_ACTION]
        if working_entries:
            for action in working_entries:
                if now - action['submitted_at'] >= spec['entry_timeout_seconds'] or now >= doc['deadline']:
                    await self._cancel_actions(doc, {'entry'})
                    break
            return
        if any(a['status'] == 'REJECTED' for a in entries):
            doc['entry_abandoned'] = True
        if now >= doc['deadline'] or doc.get('entry_abandoned') or bought >= doc['desired_quantity']:
            if not bought:
                return await self._terminal(doc, 'EXPIRED' if now >= doc['deadline'] else 'REJECTED', 'no_entry_fill')
            doc['status'] = 'OPEN'
            return await self._save(doc)
        failed_attempts = sum(a['status'] == 'CANCELED' and not a.get('filled_qty') for a in entries)
        if failed_attempts > spec['max_reprices']:
            doc['entry_abandoned'] = True
            if not bought:
                return await self._terminal(doc, 'EXPIRED', 'unfilled_timeout')
            return await self._save(doc)
        if not tick_fresh or not self.session_open(now):
            if not bought and now >= doc['entry_until']:
                return await self._terminal(doc, 'EXPIRED', 'no_fresh_execution_quote')
            return
        if now >= doc['entry_until'] and not entries:
            return await self._terminal(doc, 'EXPIRED', 'execution_quote_timeout')
        if not await self._allowed(doc):
            return
        if gateway.protection_supported and residual:
            stops = [a for a in doc['actions'] if a['kind'] == 'stop' and a['status'] in ('OPEN', 'TRIGGER_PENDING')]
            if sum(a['quantity'] - a.get('filled_qty', 0) for a in stops) < residual:
                return  # never add exposure while protection is unknown
        qty = split_quantity(doc['desired_quantity'] - bought, doc['contract']['lot_size'], doc['contract']['freeze_quantity'])[0]
        doc['status'] = 'ENTERING'
        await self._dispatch(doc, kind='entry', qty=qty, premium=premium)

    async def cycle(self):
        if not await self.store.lease(self.owner, self.now()):
            return
        for doc in await self.store.list(active=True):
            if not await self.store.lease(self.owner, self.now()):
                return
            try:
                await self.step(doc)
            except Conflict:
                continue
            except Exception as exc:
                # A failed broker read is never a flat position. Keep all action
                # identities and protection state for the next reconciliation.
                try:
                    alert(doc, f'Worker error; exposure unresolved: {exc}', self.now())
                    if doc.get('filled_quantity') and doc['spec']['failsafe'] == 'flatten':
                        doc['exit_reason'] = doc.get('exit_reason') or 'broker_or_worker_failure'
                    await self._save(doc)
                except Exception:
                    log.exception('Conditional state could not be checkpointed')
                self.last_error = str(exc)

    async def run(self):
        while not self.stopping:
            try:
                await self.cycle()
                self.last_progress = self.now()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.last_error = str(exc)
                log.exception('Conditional worker unavailable')
            await asyncio.sleep(1)

    async def start(self):
        await self.store.initialize()
        self.stopping = False
        self.task = asyncio.create_task(self.run(), name='conditional-orders')

    async def stop(self):
        self.stopping = True
        if self.task:
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass
        await self.store.drop_lease(self.owner)
        close_market = getattr(self.market, 'close', None)
        if close_market:
            await close_market()
