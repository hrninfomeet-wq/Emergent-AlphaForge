"""No-network adapter contract tests. Live clients are injected fakes."""
import asyncio
import copy
import json
import socket
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from app.conditional.market import normalize_contract, ProductionMarket
from app.conditional.broker import PaperGateway, FlattradeGateway
from app.upstox_stream import UpstoxMarketStreamManager


@pytest.fixture
def loop(monkeypatch):
    value = asyncio.new_event_loop()
    def forbidden(*args, **kwargs):
        raise AssertionError('adapter tests may not connect to broker/network/DB')
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(socket.socket, 'connect_ex', forbidden)
    yield value
    value.close()


def contract():
    return dict(underlying='NIFTY', option_type='CE', expiry='2026-10-27', strike=25000,
                instrument_key='NSE_FO|999', underlying_key='NSE_INDEX|Nifty 50',
                lot_size=65, freeze_quantity=3510, tick_size=.05, segment='NSE_FO',
                exchange='NFO', tsym='NIFTY27OCT26C25000', token='999')


def action(**kwargs):
    return dict(dict(id='af123', kind='entry', side='B', contract=contract(),
                     quantity=65, limit_price=201, reference_price=200), **kwargs)


def broker_row(a=None, **kwargs):
    a = a or action()
    return dict(dict(norenordno='ORDER1', remarks=a['id'], tsym=a['contract']['tsym'],
                     exch=a['contract']['exchange'], trantype=a['side'], prd='I',
                     prctyp='SL-LMT' if a['kind'] == 'stop' else 'LMT',
                     prc=str(a['limit_price']), trgprc=str(a.get('trigger_price', 0)),
                     qty=str(a['quantity']), fillshares='0', avgprc='0', status='OPEN'), **kwargs)


class Client:
    def __init__(self):
        self.rows = []
        self.positions = []
        self.cancelled = []
    async def order_book(self):
        return copy.deepcopy(self.rows)
    async def position_book(self):
        return copy.deepcopy(self.positions)
    async def cancel_order(self, order_id):
        self.cancelled.append(order_id)
        return SimpleNamespace(ok=True)  # Deliberately does NOT prove canceled.
    async def search_scrip(self, exch, query):
        return [dict(dname='NIFTY 27OCT26 25000 CE', optt='CE', exd='27-OCT-2026',
                     ls='65', ti='.05', tsym='NIFTY27OCT26C25000', token='999')]


def gateway(client, submitter=None):
    async def factory():
        return client
    return FlattradeGateway(client_factory=factory, engine_factory=lambda: object(),
                             submitter=submitter)


def test_paper_limit_and_restart_observation(loop):
    async def run():
        g = PaperGateway()
        a = action(reference_price=202)
        a['observation'] = await g.submit(a)
        assert a['observation']['status'] == 'OPEN'
        a['reference_price'] = 200
        obs = await PaperGateway().observe(a)
        assert obs['status'] == 'COMPLETE' and obs['fill_price'] == 200
        a['observation'] = obs
        assert (await PaperGateway().cancel(a))['status'] == 'COMPLETE'
    loop.run_until_complete(run())


@pytest.mark.parametrize('field,value', [('lot_size', 0), ('tick_size', None), ('freeze_quantity', 0)])
def test_paper_metadata_never_falls_back(loop, field, value):
    a = action(); a['contract'][field] = value
    with pytest.raises(ValueError):
        loop.run_until_complete(PaperGateway().prepare(a))


def test_cancel_ack_does_not_claim_cancellation(loop):
    client = Client(); client.rows = [broker_row()]
    result = loop.run_until_complete(gateway(client).cancel(action()))
    assert result['status'] == 'OPEN' and client.cancelled == ['ORDER1']


@pytest.mark.parametrize('change', [dict(tsym='OTHER'), dict(exch='BFO'), dict(remarks='foreign'),
                                  dict(fillshares='66'), dict(avgprc='nan', fillshares='1')])
def test_contradictory_book_fails_closed(loop, change):
    client = Client(); client.rows = [broker_row(**change)]
    a = action(broker_id='ORDER1')
    result = loop.run_until_complete(gateway(client).observe(a))
    assert result['status'] == 'UNKNOWN'


def test_lost_ack_never_retried_by_adapter(loop):
    calls = []
    async def submit(*args, **kwargs):
        calls.append(1)
        raise TimeoutError('lost acknowledgement')
    result = loop.run_until_complete(gateway(Client(), submit).submit(action()))
    assert result['status'] == 'UNKNOWN' and len(calls) == 1


def test_prepared_broker_tick_must_match(loop):
    a = action(); a['contract']['tick_size'] = .1
    with pytest.raises(ValueError, match='lot or tick mismatch'):
        loop.run_until_complete(gateway(Client()).prepare(a))


def test_sell_cannot_follow_external_flat(loop):
    client = Client()
    client.rows = [broker_row(status='COMPLETE', fillshares='65', avgprc='200')]
    calls = []
    async def submit(*args, **kwargs):
        calls.append(1)
        return SimpleNamespace(ok=True, norenordno='EXIT')
    sell = action(id='exit123', side='S', kind='exit', limit_price=190, owned_actions=[action()])
    result = loop.run_until_complete(gateway(client, submit).submit(sell))
    assert result['status'] == 'REJECTED' and not calls


def test_master_tick_join_checks_identity():
    row = dict(underlying_symbol='NIFTY', instrument_type='CE', instrument_key='NSE_FO|999',
               segment='NSE_FO', exchange='NSE', expiry=1793125799000, strike_price=25000,
               lot_size=65, freeze_quantity=3510, tick_size=5,
               underlying_key='NSE_INDEX|Nifty 50')
    csv_row = dict(instrument_key='NSE_FO|999', exchange='NSE_FO', option_type='CE',
                   expiry='2026-10-27', strike='25000', lot_size='65', tick_size='.05')
    assert normalize_contract(row, csv_row, asof=1)['tick_size'] == .05
    with pytest.raises(ValueError, match='identity mismatch'):
        normalize_contract(row, dict(csv_row, expiry='2026-10-28'), asof=1)


def test_hot_subscription_does_not_restart_stream(loop):
    class Socket:
        def __init__(self):
            self.messages = []
        async def send(self, message):
            self.messages.append(json.loads(message))
    async def run():
        manager = UpstoxMarketStreamManager(db_factory=lambda: None)
        ws = Socket(); manager._active_websocket = ws
        await manager.ensure_instruments(['NSE_FO|999'])
        await manager.ensure_instruments(['NSE_FO|999'])
        assert len(ws.messages) == 1
        assert ws.messages[0]['data']['instrumentKeys'] == ['NSE_FO|999']
        manager.configure_session(session_id='fixture', instrument_keys=['INDEX'])
        assert 'NSE_FO|999' in manager.status()['instrument_keys']
        assert manager._task is None
    loop.run_until_complete(run())


def test_snapshot_requires_local_ingest_clock(loop):
    class Stream:
        def subscribe(self, *, max_queue):
            return asyncio.Queue(maxsize=max_queue)
        def unsubscribe(self, queue):
            pass
        async def ensure_instruments(self, keys):
            pass
        def latest_tick_map(self):
            return {'NSE_FO|999': {'last_price': 200, 'ts': 100000},
                    'NSE_INDEX|Nifty 50': {'last_price': 25000, 'ingest_ts': 100000}}
    market = ProductionMarket(stream=Stream(), clock=lambda: 100)
    result = loop.run_until_complete(market.snapshot({'underlying': 'NIFTY'}, contract(), contract()))
    assert result['reference']['ts'] is None
    assert result['spot'] == {'price': 25000, 'ts': 100}
