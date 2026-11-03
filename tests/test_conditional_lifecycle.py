"""Paper lifecycle tests: isolated clocks, store, market and forbidden network."""
import asyncio
import socket
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from app.conditional.broker import PaperGateway
from app.conditional.engine import ConditionalEngine
from app.conditional.store import Conflict
from conditional_fakes import Clock, Market, MemoryStore, ForbiddenLiveGateway, spec


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    loop = asyncio.new_event_loop()  # Windows creates its private socket pair here.
    monkeypatch.setattr(asyncio, 'run', loop.run_until_complete)
    def forbidden(*args, **kwargs):
        raise AssertionError('network is forbidden in conditional lifecycle tests')
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(socket.socket, 'connect_ex', forbidden)
    monkeypatch.setattr(socket, 'create_connection', forbidden)
    yield
    loop.close()


def setup():
    clock = Clock()
    store = MemoryStore()
    market = Market(clock)
    engine = ConditionalEngine(store, market, {'paper': PaperGateway(), 'live': ForbiddenLiveGateway()},
                               now=clock, session_open=lambda _: True)
    return engine, store, market, clock


async def armed(engine):
    preview = await engine.preview(spec())
    order = await engine.arm(preview['preview_id'])
    assert order['status'] == 'ARMED'
    return order['_id']


async def entered(engine, store, market):
    oid = await armed(engine)
    market.spot = 25020
    await engine.cycle()  # trigger and resolve
    assert (await store.get(oid))['status'] == 'TRIGGERED'
    await engine.cycle()  # submit paper entry
    await engine.cycle()  # account for entry fill and mark
    order = await store.get(oid)
    assert order['filled_quantity'] == 65
    assert order['entry_fill_price'] == 200
    assert order['protection'] == 'software_only'
    return oid


@pytest.mark.parametrize('premium,reason', [(180, 'stop'), (230, 'target')])
def test_paper_entry_mark_and_software_exit(premium, reason):
    async def run():
        engine, store, market, clock = setup()
        oid = await entered(engine, store, market)
        market.premium = 210
        clock.advance()
        await engine.cycle()
        marked = await store.get(oid)
        assert marked['last_premium'] == 210
        assert (marked['last_premium'] - marked['entry_fill_price']) * (marked['filled_quantity'] - marked['closed_quantity']) == 650
        market.premium = premium
        clock.advance()
        await engine.cycle()
        await engine.cycle()
        order = await store.get(oid)
        assert order['status'] == 'CLOSED'
        assert order['exit_reason'] == reason
        assert order['closed_quantity'] == 65
        assert [a['fill_price'] for a in order['actions'] if a['kind'] == 'exit'] == [premium]
    asyncio.run(run())


def test_cancel_pending_before_trigger():
    async def run():
        engine, store, market, _ = setup()
        oid = await armed(engine)
        await store.request_cancel(oid)
        market.spot = 25020
        await engine.cycle()
        order = await store.get(oid)
        assert order['status'] == 'CANCELED'
        assert order['actions'] == []
    asyncio.run(run())


def test_kill_generation_fences_draft_and_restart_reconciles_open_position():
    async def run():
        engine, store, market, clock = setup()
        oid = await entered(engine, store, market)
        draft = (await engine.preview(spec()))['preview_id']
        await engine.kill('paper')
        await engine.reset('paper')
        with pytest.raises(ValueError, match='Kill/reset state changed'):
            await engine.arm(draft)
        await store.drop_lease(engine.owner)
        restarted = ConditionalEngine(store, market, {'paper': PaperGateway(), 'live': ForbiddenLiveGateway()},
                                      now=clock, session_open=lambda _: True)
        await restarted.cycle()
        await restarted.cycle()
        order = await store.get(oid)
        assert order['status'] == 'CLOSED'
        assert order['exit_reason'] == 'kill_or_cancel'
        assert len([a for a in order['actions'] if a['kind'] == 'entry']) == 1
        assert order['closed_quantity'] == 65
    asyncio.run(run())


def test_store_rejects_stale_revision():
    async def run():
        engine, store, _, _ = setup()
        oid = (await engine.preview(spec()))['preview_id']
        a, b = await store.get(oid), await store.get(oid)
        await store.save(a)
        with pytest.raises(Conflict):
            await store.save(b)
    asyncio.run(run())


