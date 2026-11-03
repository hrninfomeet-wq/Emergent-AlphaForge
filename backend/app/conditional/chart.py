"""Chart presentation/validation for the SAME conditional-order lifecycle.

No broker, database, scheduler or independent position book lives here.
"""
from decimal import Decimal
from .models import choice, integer, mapping, number


def aligned(value, tick, name):
    price = number(value, name)
    if Decimal(str(price)) % Decimal(str(tick)):
        raise ValueError(f'{name} must align to tick size {tick}')
    return price


def chart_spec(payload, contract, premium):
    p = mapping(payload, 'chart order', {'instrument_key', 'mode', 'lots', 'order_type',
                                        'limit_price', 'trigger_price', 'stop_price', 'target_price'})
    mode = choice(p.get('mode', 'paper'), 'mode', ('paper', 'live'))
    kind = choice(p.get('order_type', 'LIMIT'), 'order_type', ('MARKET', 'LIMIT', 'STOP_LIMIT'))
    if mode == 'live' and kind == 'MARKET':
        raise ValueError('Flattrade API permits limit and stop-limit orders only')
    tick = contract['tick_size']
    limit = aligned(p.get('limit_price'), tick, 'limit_price') if kind != 'MARKET' else None
    trigger = aligned(p.get('trigger_price'), tick, 'trigger_price') if kind == 'STOP_LIMIT' else None
    if trigger is not None and limit < trigger:
        raise ValueError('BUY stop-limit requires limit price >= trigger price')
    stop = aligned(p.get('stop_price'), tick, 'stop_price')
    target = aligned(p['target_price'], tick, 'target_price') if p.get('target_price') is not None else None
    basis = limit if limit is not None else premium
    if stop >= basis:
        raise ValueError('Initial stop must be below entry price')
    if target is not None and target <= basis:
        raise ValueError('Target must be above entry price')
    return {'mode': mode, 'underlying': contract['underlying'], 'option_type': contract['option_type'],
            'expiry': contract['expiry'], 'strike': {'kind': 'DIRECT', 'price': contract['strike']},
            'lots': integer(p.get('lots', 1), 'lots'), 'max_entry_premium': limit,
            'valid_until': '15:00', 'entry_timeout_seconds': 300, 'max_reprices': 0,
            'exits': {'stop': {'unit': 'points', 'value': basis - stop},
                      'target': {'unit': 'points', 'value': target - basis} if target is not None else None,
                      'square_off': '15:00'},
            'chart': {'instrument_key': contract['instrument_key'], 'order_type': kind,
                      'limit_price': limit, 'trigger_price': trigger, 'stop_price': stop, 'target_price': target}}


def normalize_chart(value, mode):
    c = mapping(value, 'chart', {'instrument_key', 'order_type', 'limit_price', 'trigger_price', 'stop_price', 'target_price'})
    key = c.get('instrument_key')
    if not isinstance(key, str) or not key or len(key) > 200:
        raise ValueError('chart instrument_key required')
    kind = choice(c.get('order_type'), 'chart.order_type', ('MARKET', 'LIMIT', 'STOP_LIMIT'))
    if mode == 'live' and kind == 'MARKET':
        raise ValueError('Live market orders are unavailable')
    result = {'instrument_key': key, 'order_type': kind,
              'stop_price': number(c.get('stop_price'), 'chart.stop_price'),
              'target_price': number(c['target_price'], 'chart.target_price') if c.get('target_price') is not None else None,
              'limit_price': number(c.get('limit_price'), 'chart.limit_price') if kind != 'MARKET' else None,
              'trigger_price': number(c.get('trigger_price'), 'chart.trigger_price') if kind == 'STOP_LIMIT' else None}
    if kind == 'STOP_LIMIT' and result['limit_price'] < result['trigger_price']:
        raise ValueError('BUY stop-limit requires limit >= trigger')
    return result


def chart_row(doc, now):
    c = doc.get('contract') or (doc.get('reference') or {}).get('contract') or {}
    spec = doc['spec']; config = spec.get('chart') or {}
    qty = int(doc.get('filled_quantity', 0)) - int(doc.get('closed_quantity', 0))
    entry = doc.get('entry_fill_price'); mark = doc.get('last_premium')
    age = now - doc.get('last_tick_at', 0)
    fresh = mark is not None and 0 <= age <= spec['stale_seconds']
    active = doc['status'] not in ('CLOSED', 'CANCELED', 'EXPIRED', 'REJECTED')
    state = doc.get('exit_state') or {}
    return {'id': doc['_id'], 'edit_version': doc.get('edit_version', 0), 'mode': doc['mode'],
            'instrument_key': c.get('instrument_key'), 'symbol': c.get('tsym') or f"{c.get('underlying')} {c.get('strike')} {c.get('option_type')}",
            'status': doc['status'], 'active': active, 'quantity': qty,
            'ordered_quantity': (doc.get('summary') or {}).get('quantity'), 'side': 'BUY',
            'order_type': config.get('order_type', 'CONDITIONAL'), 'limit_price': config.get('limit_price'),
            'trigger_price': config.get('trigger_price'), 'entry_price': entry,
            'stop_price': state.get('stop', config.get('stop_price')),
            'target_price': state.get('target', config.get('target_price')),
            'mark_price': mark, 'mark_fresh': fresh, 'mark_age_ms': max(0, age * 1000) if doc.get('last_tick_at') else None,
            'pnl': (mark - entry) * qty if entry is not None and mark is not None and qty > 0 else None,
            'protection': doc.get('protection', (doc.get('summary') or {}).get('protection', 'software_only')),
            'pending_change': bool(doc.get('pending_chart_change')),
            'cancel_requested': bool(doc.get('cancel_requested')), 'chart_owned': bool(config),
            'error': doc.get('last_alert'), 'actions': [
                {key: a.get(key) for key in ('id', 'kind', 'status', 'broker_id', 'quantity', 'filled_qty', 'limit_price', 'trigger_price')}
                for a in doc.get('actions', [])]}
