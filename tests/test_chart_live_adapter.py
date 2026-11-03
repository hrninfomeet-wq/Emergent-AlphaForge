"""No-network tests for chart stop-limit live admission and readback."""
import asyncio
import socket
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from app.conditional.broker import FlattradeGateway
from app.live.broker_protocol import OrderResult
from conditional_fakes import contract


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    loop = asyncio.new_event_loop()
    monkeypatch.setattr(asyncio, 'run', loop.run_until_complete)
    def forbidden(*args, **kwargs):
        raise AssertionError('network access forbidden')
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(socket.socket, 'connect_ex', forbidden)
    monkeypatch.setattr(socket, 'create_connection', forbidden)
    yield
    loop.close()


def action():
    return dict(id='chart123', kind='entry', side='B', contract=contract(), quantity=65,
                order_type='STOP_LIMIT', limit_price=210, trigger_price=205,
                broker_id='broker1', pending_amendment={'limit_price': 215, 'trigger_price': 210})


def row(limit, trigger, status='OPEN', filled=0):
    return dict(norenordno='broker1', remarks='chart123', tsym=contract()['tsym'], exch='NFO',
                trantype='B', prd='I', prctyp='SL-LMT', prc=limit, trgprc=trigger,
                qty=65, fillshares=filled, avgprc=215 if filled else 0, status=status)


class FakeClient:
    def __init__(self):
        self.rows = [row(210, 205)]
        self.modifications = []
    async def order_book(self):
        return self.rows
    async def modify_order(self, broker_id, **kwargs):
        self.modifications.append((broker_id, kwargs))
        self.rows = [row(kwargs['prc'], kwargs['trgprc'])]


class FakeEngine:
    async def can_trade(self):
        return True, ''


def test_stop_limit_intent_and_modify_readback():
    async def run():
        client = FakeClient()
        async def factory():
            return client
        gateway = FlattradeGateway(client_factory=factory, engine_factory=FakeEngine)
        intent = gateway._intent(action())
        assert (intent.prctyp, intent.prc, intent.trgprc, intent.qty) == ('SL-LMT', 210, 205, 65)
        result = await gateway.amend_entry(action())
        assert client.modifications == [('broker1', {'prc': 215, 'trgprc': 210})]
        assert result['status'] == 'OPEN' and result['amended'] is True
        assert (result['limit_price'], result['trigger_price']) == (215, 210)
        # A subsequent confirmed readback must not submit the same modification again.
        result = await gateway.amend_entry(action())
        assert result['amended'] is True and len(client.modifications) == 1
    asyncio.run(run())


def test_stop_limit_readback_rejects_unrecognized_prices():
    result = FlattradeGateway._match(action(), [row(220, 210)])
    assert result['status'] == 'UNKNOWN'


def test_executor_admits_valid_stop_limit_and_rejects_invalid(monkeypatch):
    from app.live import executor
    monkeypatch.setattr(executor, '_autoplace_armed', lambda: True)
    monkeypatch.setattr(executor, 'margin_verdict', lambda *args, **kwargs: {'ok': True})
    class Client:
        def __init__(self):
            self.placed = []
        async def limits(self):
            return {}
        async def place_order(self, intent):
            self.placed.append(intent)
            return OrderResult(ok=True, norenordno='fake1')
    async def run():
        client = Client()
        intent = FlattradeGateway._intent(action())
        result = await executor.submit_conditional_intent(client, intent, engine=FakeEngine(), entry=True)
        assert result.ok and client.placed == [intent]
        intent.trgprc = 211
        result = await executor.submit_conditional_intent(client, intent, engine=FakeEngine(), entry=True)
        assert not result.ok and len(client.placed) == 1
    asyncio.run(run())
