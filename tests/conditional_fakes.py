"""In-memory boundaries for conditional-engine behavioral tests."""
import copy
from datetime import datetime

from app.conditional.rules import IST
from app.conditional.store import Conflict, TERMINAL


class Clock:
    def __init__(self):
        self.value = datetime(2026, 10, 26, 10, 0, tzinfo=IST).timestamp()
    def __call__(self):
        return self.value
    def advance(self, seconds=1):
        self.value += seconds


class MemoryStore:
    def __init__(self):
        self.orders = {}
        self.controls = {mode: {'_id': mode, 'blocked': False, 'generation': 0} for mode in ('paper', 'live')}
        self.reservations = {}
        self.worker = {}
    async def initialize(self):
        pass
    async def insert(self, order):
        if order['_id'] in self.orders:
            raise Conflict('duplicate order')
        self.orders[order['_id']] = copy.deepcopy(order)
        return order
    async def get(self, oid):
        return copy.deepcopy(self.orders.get(oid))
    async def save(self, order):
        if self.orders[order['_id']]['version'] != order['version']:
            raise Conflict('Order changed concurrently')
        order['version'] += 1
        self.orders[order['_id']] = copy.deepcopy(order)
        return order
    async def list(self, *, active=False, limit=200):
        rows = [o for o in self.orders.values() if o['status'] != 'DRAFT' and (not active or o['status'] not in TERMINAL)]
        rows = sorted(rows, key=lambda o: o['created_at'], reverse=True)
        return copy.deepcopy(rows if active else rows[:limit])
    async def control(self, mode):
        return copy.deepcopy(self.controls[mode])
    async def latch(self, mode, blocked):
        if mode not in self.controls:
            raise ValueError('invalid mode')
        self.controls[mode]['blocked'] = bool(blocked)
        self.controls[mode]['generation'] += 1
        return copy.deepcopy(self.controls[mode])
    async def request_cancel(self, oid):
        order = self.orders.get(oid)
        if order is None or order['status'] in TERMINAL:
            return None
        order['cancel_requested'] = True
        order['version'] += 1
        return copy.deepcopy(order)
    async def reserve(self, mode, contract_key, oid):
        key = (mode, contract_key)
        old = self.reservations.get(key)
        if old and old != oid and self.orders[old]['status'] not in TERMINAL:
            return False
        self.reservations[key] = oid
        return True
    async def release(self, order):
        self.reservations = {k: v for k, v in self.reservations.items() if v != order['_id']}
    async def lease(self, owner, now):
        if self.worker and self.worker['owner'] != owner and self.worker['until'] >= now:
            return False
        self.worker = {'owner': owner, 'until': now + 90, 'heartbeat': now}
        return True
    async def heartbeat(self):
        return copy.deepcopy(self.worker)
    async def drop_lease(self, owner):
        if self.worker.get('owner') == owner:
            self.worker['until'] = 0


def contract():
    return dict(underlying='NIFTY', option_type='CE', expiry='2026-10-27', strike=25000,
                instrument_key='NSE_FO|999', underlying_key='NSE_INDEX|Nifty 50',
                lot_size=65, freeze_quantity=3510, tick_size=.05, segment='NSE_FO',
                exchange='NFO', tsym='NIFTY27OCT26C25000', token='999')


class Market:
    def __init__(self, clock):
        self.clock, self.spot, self.premium = clock, 25000, 200
        self.subscriptions = []
    async def contracts(self, underlying):
        return [contract()] if underlying == 'NIFTY' else []
    async def ensure_subscription(self, contracts):
        self.subscriptions.extend(copy.deepcopy(contracts))
    async def snapshot(self, spec, reference_contract=None, execution_contract=None):
        spot = {'price': self.spot, 'ts': self.clock()}
        option = {'price': self.premium, 'ts': self.clock()}
        return {'spot': spot, 'reference': option, 'execution': option}


class ForbiddenLiveGateway:
    protection_supported = True
    def __getattr__(self, name):
        raise AssertionError('live gateway accessed: ' + name)


def spec():
    return {'mode': 'paper', 'underlying': 'NIFTY', 'option_type': 'CE', 'expiry': '2026-10-27',
            'spot_condition': {'direction': 'above', 'mode': 'level', 'price': 25010},
            'exits': {'stop': {'unit': 'points', 'value': 20},
                      'target': {'unit': 'points', 'value': 30}, 'square_off': '15:00'}}
