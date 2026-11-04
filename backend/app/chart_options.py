"""Read-only, identity-checked chart option chains on the shared market feed."""
import asyncio
import copy
import time
from collections import OrderedDict
from datetime import date, datetime
from urllib.parse import quote

from app.chart_data import IST, _finite, _bounded_int, get_chart_data_service
from app.chart_stream import tick_age_ms
from app.tick_stream import sse_frame
from app.upstox_client import _authenticated_get, _base_url, fetch_option_chain

INDEX_KEYS = {"NIFTY": "NSE_INDEX|Nifty 50", "BANKNIFTY": "NSE_INDEX|Nifty Bank", "SENSEX": "BSE_INDEX|SENSEX"}


def _date(value):
    if not isinstance(value, str):
        raise ValueError('Invalid expiry')
    try:
        result = date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError('Invalid expiry') from exc
    if result.isoformat() != value:
        raise ValueError('Invalid expiry')
    return value


def _num(value):
    n = _finite(value)
    return n if n is not None and n >= 0 else None


class ChartOptionsService:
    def __init__(self, *, catalog=None, get=None, chain=None, clock=time.time):
        self.catalog = catalog or get_chart_data_service()
        self.get = get or _authenticated_get
        self.chain = chain or fetch_option_chain
        self.clock = clock
        self.contracts = OrderedDict()
        self.snapshots = OrderedDict()
        self.refresh_failures = {}
        self.lock = asyncio.Lock()

    async def underlying(self, key):
        item = await self.catalog.resolve(key)
        if item['kind'] in ('option', 'future'):
            key = item.get('underlying_key') or INDEX_KEYS.get(item.get('underlying', '').upper())
            if not key:
                raise ValueError('Underlying for this derivative is unknown')
            item = await self.catalog.resolve(key)
        if item['kind'] not in ('index', 'equity'):
            raise ValueError('Unsupported underlying')
        return item

    async def _contracts(self, underlying):
        key = underlying['instrument_key']
        now = self.clock()
        cached = self.contracts.get(key)
        if cached and now - cached[0] < 300:
            return cached[1]
        async with self.lock:
            cached = self.contracts.get(key)
            if cached and self.clock() - cached[0] < 300:
                return cached[1]
            payload = await self.get(f'{_base_url()}/v2/option/contract?instrument_key={quote(key, safe="")}')
            rows = payload.get('data') if isinstance(payload, dict) and payload.get('status') == 'success' else None
            if not isinstance(rows, list) or not rows or len(rows) > 20000:
                raise ValueError('Option contracts unavailable')
            today = datetime.fromtimestamp(self.clock(), IST).date().isoformat()
            result = {}
            for row in rows:
                try:
                    expiry = _date(row['expiry'])
                    strike = _num(row['strike_price'])
                    side = row['instrument_type']
                    k = row['instrument_key']
                    if (row.get('underlying_key') != key or expiry < today or strike is None or strike <= 0
                        or side not in ('CE', 'PE') or not isinstance(k, str) or len(k) > 200
                        or not k.startswith(underlying['exchange'] + '_FO|') or not row.get('trading_symbol')):
                        continue
                    item = {'instrument_key': k, 'symbol': row['trading_symbol'],
                            'name': row.get('name') or row['trading_symbol'], 'exchange': underlying['exchange'],
                            'segment': k.split('|')[0], 'kind': 'option', 'volume_available': True,
                            'expiry': expiry, 'strike': strike, 'option_type': side, 'underlying_key': key}
                    if row.get('exchange_token') is not None:
                        item['exchange_token'] = str(row['exchange_token'])
                    if row.get('underlying_symbol'):
                        item['underlying'] = row['underlying_symbol']
                    lot = _num(row.get('lot_size'))
                    if lot and lot.is_integer(): item['lot_size'] = int(lot)
                    if k in result and result[k] != item: raise ValueError('Conflicting option identity')
                    result[k] = item
                except (KeyError, TypeError):
                    continue
            if not result: raise ValueError('No valid current option contracts')
            self.contracts[key] = (self.clock(), result)
            self.contracts.move_to_end(key)
            while len(self.contracts) > 32: self.contracts.popitem(last=False)
            return result

    async def expiries(self, key):
        underlying = await self.underlying(key)
        contracts = await self._contracts(underlying)
        today = datetime.fromtimestamp(self.clock(), IST).date().isoformat()
        return {'underlying': underlying, 'expiries': sorted({v['expiry'] for v in contracts.values() if v['expiry'] >= today}),
                'source': 'upstox_rest_v2', 'warnings': []}

    async def snapshot(self, key, expiry=None, center=None, radius=20):
        radius = _bounded_int(radius, 'Radius', 5, 40)
        underlying = await self.underlying(key)
        contracts = await self._contracts(underlying)
        today = datetime.fromtimestamp(self.clock(), IST).date().isoformat()
        expiries = sorted({v['expiry'] for v in contracts.values() if v['expiry'] >= today})
        if not expiries: raise ValueError('No current option expiries')
        expiry = _date(expiry) if expiry else expiries[0]
        if expiry not in expiries: raise ValueError('Expiry unavailable or expired')
        if center is not None:
            center = _finite(center)
            if center is None or center <= 0: raise ValueError('Invalid center')
        cache_key = (underlying['instrument_key'], expiry)
        async with self.lock:
            entry = self.snapshots.get(cache_key)
            failed_at = self.refresh_failures.get(cache_key)
            if entry and failed_at is not None and self.clock() - failed_at < 15:
                data = copy.deepcopy(entry[1])
                data['warnings'].append('REST refresh failed; showing stale snapshot captured at captured_at_ms.')
                data['stale'] = True
            elif entry and self.clock() - entry[0] < 15:
                data = copy.deepcopy(entry[1])
            else:
                try:
                    payload = await self.chain(underlying['instrument_key'], expiry)
                except Exception:
                    if not entry: raise
                    self.refresh_failures[cache_key] = self.clock()
                    data = copy.deepcopy(entry[1])
                    data['warnings'].append('REST refresh failed; showing stale snapshot captured at captured_at_ms.')
                    data['stale'] = True
                else:
                    data = self._parse(payload, underlying, expiry, contracts)
                    self.refresh_failures.pop(cache_key, None)
                    self.snapshots[cache_key] = (self.clock(), copy.deepcopy(data))
                    self.snapshots.move_to_end(cache_key)
                    while len(self.snapshots) > 32:
                        evicted, _ = self.snapshots.popitem(last=False)
                        self.refresh_failures.pop(evicted, None)
        strikes = data['rows']
        target = center if center is not None else data['spot']
        if target is None: raise ValueError('Spot unavailable; provide a center strike')
        idx = min(range(len(strikes)), key=lambda i: abs(strikes[i]['strike'] - target))
        atm = min(strikes, key=lambda r: abs(r['strike'] - data['spot']))['strike'] if data['spot'] is not None else None
        data.update(underlying=underlying, expiry=expiry, expiries=expiries, atm_strike=atm,
                    total_strikes=len(strikes), rows=strikes[max(0, idx-radius):idx+radius+1])
        for row in data['rows']: row['atm'] = row['strike'] == atm
        return data

    def _parse(self, payload, underlying, expiry, contracts):
        rows = payload.get('data') if isinstance(payload, dict) and payload.get('status') == 'success' else None
        if not isinstance(rows, list) or not rows or len(rows) > 10000: raise ValueError('Invalid option chain')
        out, spots, keys = {}, [], set()
        for row in rows:
            if not isinstance(row, dict): raise ValueError('Invalid chain row')
            strike = _num(row.get('strike_price'))
            if (row.get('underlying_key') != underlying['instrument_key'] or row.get('expiry') != expiry
                or strike is None or strike <= 0 or strike in out): raise ValueError('Chain identity mismatch')
            spot = _num(row.get('underlying_spot_price'))
            if spot is not None and spot > 0: spots.append(spot)
            item = {'strike': strike}
            for name, field, side in [('ce','call_options','CE'), ('pe','put_options','PE')]:
                raw = row.get(field)
                if raw is None: item[name] = None; continue
                if not isinstance(raw, dict): raise ValueError('Invalid option leg')
                k = raw.get('instrument_key'); instrument = contracts.get(k)
                if (not instrument or k in keys or instrument['expiry'] != expiry or instrument['strike'] != strike
                    or instrument['option_type'] != side): raise ValueError('Option leg identity mismatch')
                keys.add(k)
                market = raw.get('market_data') if isinstance(raw.get('market_data'), dict) else {}
                greeks = raw.get('option_greeks') if isinstance(raw.get('option_greeks'), dict) else {}
                oi, prev = _num(market.get('oi')), _num(market.get('prev_oi'))
                item[name] = {'instrument': instrument, 'ltp': _num(market.get('ltp')), 'oi': oi,
                    'previous_oi': prev, 'oi_change': oi-prev if oi is not None and prev is not None else None,
                    'volume': _num(market.get('volume')), 'iv': _num(greeks.get('iv')), 'quote_time_ms': None,
                    'quote_source': 'upstox_rest_v2', 'iv_source': 'upstox_rest_v2'}
            if item['ce'] is None and item['pe'] is None: raise ValueError('Empty chain row')
            out[strike] = item
        if not out: raise ValueError('Empty option chain')
        return {'spot': spots[0] if spots else None, 'spot_source': 'upstox_rest_v2', 'spot_quote_time_ms': None, 'rows': [out[k] for k in sorted(out)],
                'captured_at_ms': int(self.clock()*1000), 'source': 'upstox_rest_v2', 'stale': False,
                'warnings': ['OI change is versus vendor previous OI; its baseline is unspecified.', 'REST IV is raw; units are unspecified.', 'REST capture time is not an exchange quote timestamp.']}


async def option_events(*, service, manager, initial, key, expiry, center, radius, is_disconnected, interval=1):
    lease = None
    try:
        keys = [initial['underlying']['instrument_key']] + [leg['instrument']['instrument_key'] for row in initial['rows'] for leg in (row['ce'], row['pe']) if leg]
        lease = await manager.acquire_quote_batch(keys)
        snapshot = initial
        # Freeze the selected strike window for the lifetime of this lease.
        if center is None:
            center = initial['rows'][len(initial['rows']) // 2]['strike']
        while not await is_disconnected():
            snapshot = await service.snapshot(key, expiry, center, radius)
            current_keys = [snapshot['underlying']['instrument_key']] + [leg['instrument']['instrument_key'] for row in snapshot['rows'] for leg in (row['ce'], row['pe']) if leg]
            if set(current_keys) != set(keys):
                new_lease = await manager.acquire_quote_batch(current_keys)
                old_lease, lease, keys = lease, new_lease, current_keys
                await asyncio.shield(manager.release_quote_batch(old_lease))
            now = int(service.clock()*1000)
            ticks, connected = manager.latest_tick_map(), bool(manager.status().get('connected'))
            states = []
            for row in snapshot['rows']:
                for leg in (row['ce'], row['pe']):
                    if not leg: continue
                    tick = ticks.get(leg['instrument']['instrument_key'], {})
                    age = tick_age_ms(tick, now)
                    state = 'disconnected' if not connected else 'waiting' if not tick else 'live' if age is not None and age <= 10000 else 'stale'
                    field_sources = {field: 'upstox_rest_v2' for field in ('ltp', 'oi', 'volume')}
                    updated = False
                    if state == 'live':
                        for source, dest in [('last_price','ltp'), ('open_interest','oi'), ('volume_traded_today','volume')]:
                            value = _num(tick.get(source))
                            if value is not None:
                                leg[dest] = value
                                field_sources[dest] = 'upstox_ws_v3'
                                updated = True
                        if updated:
                            leg['oi_change'] = leg['oi'] - leg['previous_oi'] if leg['oi'] is not None and leg['previous_oi'] is not None else None
                            leg['quote_time_ms'] = tick.get('ts'); leg['quote_source'] = 'upstox_ws_v3'
                        else:
                            state = 'waiting'
                    leg['field_sources'] = field_sources
                    leg['quote_status'] = state; leg['live_update'] = updated; states.append(state)
            snapshot['as_of_ms'] = now
            snapshot['status'] = 'disconnected' if not connected else 'live' if 'live' in states else 'stale' if 'stale' in states else 'waiting'
            yield sse_frame('snapshot', snapshot)
            await asyncio.sleep(interval)
    except asyncio.CancelledError: raise
    except Exception:
        yield sse_frame('stream_error', {'warnings': ['Option chain stream unavailable.'], 'status': 'disconnected'})
    finally:
        if lease is not None: await asyncio.shield(manager.release_quote_batch(lease))

_service = None

def get_chart_options_service():
    global _service
    if _service is None: _service = ChartOptionsService()
    return _service
