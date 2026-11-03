"""Pure conditional-order decisions; no clocks, feeds, databases or brokers."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR
import math

from .models import number, integer, clock

IST = timezone(timedelta(hours=5, minutes=30))


def _decimal(value):
    return Decimal(str(value))


def _difference(amount, entry):
    return float(_decimal(amount['value']) * _decimal(entry) / 100
                 if amount['unit'] == 'percent' else _decimal(amount['value']))


def arm_condition_ok(spec, spot):
    spot = number(spot, 'spot')
    cond = spec.get('spot_condition')
    if not cond or cond['mode'] == 'level':
        return True
    return spot < cond['price'] if cond['direction'] == 'above' else spot > cond['price']


def entry_ready(spec, previous_spot, spot, premium, reference):
    """All predicates must hold on this evaluation; no historical edge latching."""
    try:
        spot = number(spot, 'spot')
        cond = spec.get('spot_condition')
        if cond:
            threshold = cond['price']
            reached = spot >= threshold if cond['direction'] == 'above' else spot <= threshold
            if not reached:
                return False
            if cond['mode'] == 'cross':
                prev = number(previous_spot, 'previous_spot')
                if not (prev < threshold if cond['direction'] == 'above' else prev > threshold):
                    return False
        move = spec.get('premium_condition')
        if move:
            premium = number(premium, 'premium')
            reference = number(reference, 'reference')
            delta = _decimal(move['value'])
            if move['unit'] == 'percent':
                delta = _decimal(reference) * delta / 100
            change = _decimal(premium) - _decimal(reference)
            if not (change >= delta if move['direction'] == 'rise' else change <= -delta):
                return False
        return True
    except (ValueError, TypeError, KeyError):
        return False


def time_today(value, now):
    """Return today's IST HH:MM timestamp. Does not roll past times to tomorrow."""
    clock(value, 'time')
    now = number(now, 'now')
    hour, minute = map(int, value.split(':'))
    return datetime.fromtimestamp(now, IST).replace(hour=hour, minute=minute, second=0, microsecond=0).timestamp()


def deadline(spec, now):
    now = number(now, 'now')
    if spec['expiry'] < datetime.fromtimestamp(now, IST).date().isoformat():
        raise ValueError('selected contract expiry is in the past')
    end = time_today(spec['valid_until'], now) if spec.get('valid_until') else now + spec['valid_minutes'] * 60
    end = min(end, time_today(spec['exits']['square_off'], now))
    if end <= now:
        raise ValueError('validity/square-off deadline must be in the future')
    return end


def validate_contract(contract):
    if not isinstance(contract, dict):
        raise ValueError('contract must be an object')
    for key in ('instrument_key', 'underlying', 'option_type', 'expiry', 'segment', 'exchange'):
        if not isinstance(contract.get(key), str) or not contract[key].strip():
            raise ValueError(f'missing contract metadata: {key}')
    for key in ('strike', 'tick_size'):
        number(contract.get(key), 'contract.' + key)
    lot = integer(contract.get('lot_size'), 'contract.lot_size')
    freeze = integer(contract.get('freeze_quantity'), 'contract.freeze_quantity')
    if freeze < lot:
        raise ValueError('freeze quantity is less than one lot')
    return dict(contract)


def resolve_contract(spec, contracts, spot):
    """Resolve from the current listed strike ladder, never a hardcoded step."""
    spot = number(spot, 'spot')
    matches = [validate_contract(c) for c in contracts
               if isinstance(c, dict) and c.get('underlying') == spec['underlying']
               and c.get('option_type') == spec['option_type'] and c.get('expiry') == spec['expiry']]
    if not matches:
        raise ValueError('no contracts for selected underlying, type and expiry')
    prices = [c['strike'] for c in matches]
    identities = [c['instrument_key'] for c in matches]
    if len(set(prices)) != len(prices) or len(set(identities)) != len(identities):
        raise ValueError('ambiguous duplicate contracts in instrument master')
    ladder = sorted(matches, key=lambda c: c['strike'])
    rule = spec['strike']
    if rule['kind'] == 'DIRECT':
        direct = [c for c in ladder if c['strike'] == rule['price']]
        if not direct:
            raise ValueError('direct strike is not listed in selected master expiry')
        return dict(direct[0])
    # Equidistant ATM chooses the lower listed strike, consistently for CE/PE.
    atm = min(range(len(ladder)), key=lambda i: (abs(_decimal(ladder[i]['strike']) - _decimal(spot)), ladder[i]['strike']))
    offset = rule['offset']
    direction = 0
    if rule['kind'] != 'ATM':
        direction = -1 if (rule['kind'] == 'ITM') == (spec['option_type'] == 'CE') else 1
    index = atm + direction * offset
    if index < 0 or index >= len(ladder):
        raise ValueError('requested moneyness is outside the listed strike ladder')
    return dict(ladder[index])


def price_limit(reference, band, tick, side='B'):
    reference = number(reference, 'reference')
    band = number(band, 'band', inclusive=True)
    tick = number(tick, 'tick')
    if side not in ('B', 'S'):
        raise ValueError('side must be B or S')
    ref, band_d, quantum = map(_decimal, (reference, band, tick))
    sign = 1 if side == 'B' else -1
    raw = ref * (1 + sign * min(Decimal('0.5'), band_d) / 100)
    result = (raw / quantum).to_integral_value(rounding=ROUND_CEILING if side == 'B' else ROUND_FLOOR) * quantum
    if result <= 0 or abs(result - ref) > ref * band_d / 100:
        raise ValueError('no marketable tick-valid limit fits the selected band')
    return float(result)


def split_quantity(qty, lot, freeze):
    qty, lot, freeze = integer(qty, 'qty'), integer(lot, 'lot'), integer(freeze, 'freeze')
    if qty % lot:
        raise ValueError('quantity is not a whole lot multiple')
    child = freeze // lot * lot
    if child <= 0:
        raise ValueError('freeze quantity is less than one lot')
    full, residual = divmod(qty, child)
    return [child] * full + ([residual] if residual else [])


def exit_state(spec, fill_price, previous_state=None):
    fill = number(fill_price, 'fill_price')
    exits = spec['exits']
    initial = float(_decimal(fill) - _decimal(_difference(exits['stop'], fill)))
    if initial <= 0 and not spec.get('chart'):
        raise ValueError('stop distance must be below entry premium')
    target = float(_decimal(fill) + _decimal(_difference(exits['target'], fill))) if exits.get('target') else None
    if spec.get('chart'):
        initial = spec['chart']['stop_price']
        target = spec['chart'].get('target_price')
        if previous_state is None and (initial >= fill or (target is not None and target <= fill)):
            raise ValueError('Actual fill is outside confirmed stop/target levels')
    state = {'entry': fill, 'stop': initial, 'initial_stop': initial, 'target': target,
             'peak': fill, 'stop_reason': 'stop', 'trail_active': False, 'lock_active': False}
    if previous_state is not None:
        old_stop = number(previous_state.get('stop'), 'previous stop')
        state['stop'] = max(initial, old_stop)
        state['peak'] = max(fill, number(previous_state.get('peak'), 'previous peak'))
        state['trail_active'] = bool(previous_state.get('trail_active'))
        state['lock_active'] = bool(previous_state.get('lock_active'))
        if old_stop >= initial:
            state['stop_reason'] = previous_state.get('stop_reason', 'stop')
    return state


def evaluate_exit(spec, state, ltp):
    """Return copied state and one exit reason; never loosen the stored stop."""
    result = dict(state)
    try:
        premium = number(ltp, 'ltp')
    except ValueError:
        return result, None
    entry = number(state['entry'], 'entry')
    stop = number(state['stop'], 'stop')
    peak = max(number(state['peak'], 'peak'), premium)
    result['peak'] = peak
    gain = _decimal(peak) - _decimal(entry)
    trail = spec['exits'].get('trailing')
    lock = spec['exits'].get('profit_lock')
    if trail and gain >= _decimal(trail['activation']):
        result['trail_active'] = True
        steps = ((gain - _decimal(trail['activation'])) / _decimal(trail['step'])).to_integral_value(rounding=ROUND_FLOOR)
        candidate = float(_decimal(entry) + _decimal(trail['activation']) - _decimal(trail['distance']) + steps * _decimal(trail['step']))
        if candidate > stop:
            stop, result['stop_reason'] = candidate, 'trailing_stop'
    if lock and gain >= _decimal(lock['activation']):
        result['lock_active'] = True
        steps = ((gain - _decimal(lock['activation'])) / _decimal(lock['step'])).to_integral_value(rounding=ROUND_FLOOR)
        candidate = float(_decimal(entry) + _decimal(lock['profit']) + steps * _decimal(lock['raise_by']))
        if candidate > stop:
            stop, result['stop_reason'] = candidate, 'profit_lock'
    result['stop'] = stop
    if premium <= stop:
        return result, result['stop_reason']
    if state.get('target') is not None and premium >= number(state['target'], 'target'):
        return result, 'target'
    return result, None
