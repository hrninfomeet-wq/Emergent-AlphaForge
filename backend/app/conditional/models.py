"""Strict JSON contract for conditional orders; pure and safe to import in tests."""
from __future__ import annotations

import math
import re
from datetime import date
from typing import Any


def number(value: Any, name: str, *, minimum: float = 0, inclusive: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f'{name} must be a finite number')
    result = float(value)
    if not math.isfinite(result) or (result < minimum if inclusive else result <= minimum):
        raise ValueError(f'{name} must be finite and {">=" if inclusive else ">"} {minimum}')
    return result


def integer(value: Any, name: str, minimum: int = 1) -> int:
    if type(value) is not int or value < minimum:
        raise ValueError(f'{name} must be an integer >= {minimum}')
    return value


def clock(value: Any, name: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d', value):
        raise ValueError(f'{name} must be HH:MM in IST')
    return value


def mapping(value: Any, name: str, allowed: set) -> dict:
    if not isinstance(value, dict):
        raise ValueError(f'{name} must be an object')
    if set(value) - allowed:
        raise ValueError(f'{name} contains unknown fields: {sorted(set(value) - allowed)}')
    return value


def choice(value: Any, name: str, choices: tuple) -> str:
    if not isinstance(value, str) or value not in choices:
        raise ValueError(f'{name} must be one of {choices}')
    return value


def amount(value: Any, name: str, *, stop=False) -> dict:
    value = mapping(value, name, {'value', 'unit'})
    unit = choice(value.get('unit'), name + '.unit', ('points', 'percent'))
    n = number(value.get('value'), name + '.value')
    if stop and unit == 'percent' and n >= 100:
        raise ValueError('stop percent must be below 100')
    return {'value': n, 'unit': unit}


def normalize_spec(value: dict, max_lots: int = 5) -> dict:
    """Validate/normalize without coercing bools, strings, NaN or unknown fields."""
    value = mapping(value, 'order', {
        'mode', 'underlying', 'option_type', 'expiry', 'strike', 'lots', 'band_pct',
        'spot_condition', 'premium_condition', 'valid_minutes', 'valid_until', 'exits',
        'entry_timeout_seconds', 'max_reprices', 'stale_seconds', 'failsafe',
        'max_entry_premium',
    })
    expiry = value.get('expiry')
    if not isinstance(expiry, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', expiry):
        raise ValueError('expiry must be YYYY-MM-DD from instrument master')
    try:
        date.fromisoformat(expiry)
    except ValueError as exc:
        raise ValueError('expiry is not a calendar date') from exc
    lots = integer(value.get('lots', 1), 'lots')
    if lots > integer(max_lots, 'max_lots'):
        raise ValueError(f'lots exceeds configured maximum {max_lots}')
    strike = mapping(value.get('strike', {'kind': 'ATM'}), 'strike', {'kind', 'offset', 'price'})
    kind = choice(strike.get('kind', 'ATM'), 'strike.kind', ('ATM', 'ITM', 'OTM', 'DIRECT'))
    offset = integer(strike.get('offset', 0 if kind in ('ATM', 'DIRECT') else 1), 'strike.offset', 0)
    if kind in ('ATM', 'DIRECT') and offset != 0:
        raise ValueError('ATM and DIRECT cannot carry an offset')
    if kind in ('ITM', 'OTM') and offset == 0:
        raise ValueError('ITM/OTM offset must be at least one')
    selected = {'kind': kind, 'offset': offset}
    if kind == 'DIRECT':
        selected['price'] = number(strike.get('price'), 'strike.price')
    elif strike.get('price') is not None:
        raise ValueError('strike.price is only valid for DIRECT')

    spot = value.get('spot_condition')
    if spot is not None:
        spot = mapping(spot, 'spot_condition', {'direction', 'price', 'mode'})
        spot = {'direction': choice(spot.get('direction'), 'spot.direction', ('above', 'below')),
                'mode': choice(spot.get('mode', 'cross'), 'spot.mode', ('cross', 'level')),
                'price': number(spot.get('price'), 'spot.price')}
    premium = value.get('premium_condition')
    if premium is not None:
        premium = mapping(premium, 'premium_condition', {'direction', 'value', 'unit'})
        premium = {'direction': choice(premium.get('direction'), 'premium.direction', ('rise', 'fall')),
                   **amount({k: premium.get(k) for k in ('value', 'unit')}, 'premium')}
        if premium['direction'] == 'fall' and premium['unit'] == 'percent' and premium['value'] >= 100:
            raise ValueError('premium fall percent must be below 100')

    exits = mapping(value.get('exits', {}), 'exits', {'stop', 'target', 'trailing', 'profit_lock', 'square_off'})
    normalized_exits = {'stop': amount(exits.get('stop', {'value': 50, 'unit': 'percent'}), 'stop', stop=True),
                        'target': None, 'trailing': None, 'profit_lock': None,
                        'square_off': clock(exits.get('square_off', '15:00'), 'square_off')}
    if exits.get('target') is not None:
        normalized_exits['target'] = amount(exits['target'], 'target')
    if exits.get('trailing') is not None:
        trail = mapping(exits['trailing'], 'trailing', {'activation', 'distance', 'step'})
        normalized_exits['trailing'] = {
            'activation': number(trail.get('activation'), 'trailing.activation', inclusive=True),
            'distance': number(trail.get('distance'), 'trailing.distance'),
            'step': number(trail.get('step'), 'trailing.step'),
        }
    if exits.get('profit_lock') is not None:
        lock = mapping(exits['profit_lock'], 'profit_lock', {'activation', 'profit', 'step', 'raise_by'})
        normalized_exits['profit_lock'] = {
            key: number(lock.get(key), 'profit_lock.' + key, inclusive=(key == 'profit'))
            for key in ('activation', 'profit', 'step', 'raise_by')}
        if normalized_exits['profit_lock']['profit'] > normalized_exits['profit_lock']['activation']:
            raise ValueError('locked profit cannot exceed activation profit')
        if normalized_exits['profit_lock']['raise_by'] > normalized_exits['profit_lock']['step']:
            raise ValueError('profit-lock raise_by cannot exceed step')

    until = value.get('valid_until')
    minutes = value.get('valid_minutes')
    if until is not None and minutes is not None:
        raise ValueError('choose valid_minutes or valid_until, not both')
    if until is not None:
        until = clock(until, 'valid_until')
        minutes = None
    else:
        minutes = number(30 if minutes is None else minutes, 'valid_minutes')
        if minutes > 1440:
            raise ValueError('valid_minutes cannot exceed one day')
    band = number(value.get('band_pct', 3), 'band_pct', inclusive=True)
    if band > 20:
        raise ValueError('band_pct cannot exceed 20')
    timeout = number(value.get('entry_timeout_seconds', 15), 'entry_timeout_seconds')
    stale = number(value.get('stale_seconds', 5), 'stale_seconds')
    retries = integer(value.get('max_reprices', 1), 'max_reprices', 0)
    if timeout > 300 or stale > 60 or retries > 5:
        raise ValueError('timeout <= 300s, stale <= 60s and max_reprices <= 5 required')
    mode = choice(value.get('mode', 'paper'), 'mode', ('paper', 'live'))
    max_premium = value.get('max_entry_premium')
    if max_premium is not None:
        max_premium = number(max_premium, 'max_entry_premium')
    if mode == 'live' and max_premium is None:
        raise ValueError('live orders require max_entry_premium to bound full premium exposure')
    return {
        'mode': mode,
        'underlying': choice(value.get('underlying'), 'underlying', ('NIFTY', 'BANKNIFTY', 'SENSEX')),
        'option_type': choice(value.get('option_type', 'CE'), 'option_type', ('CE', 'PE')),
        'expiry': expiry, 'strike': selected, 'lots': lots, 'band_pct': band,
        'spot_condition': spot, 'premium_condition': premium,
        'valid_minutes': minutes, 'valid_until': until, 'exits': normalized_exits,
        'entry_timeout_seconds': timeout, 'max_reprices': retries,
        'stale_seconds': stale, 'failsafe': choice(value.get('failsafe', 'flatten'), 'failsafe', ('flatten', 'alert')),
        'max_entry_premium': max_premium,
    }
