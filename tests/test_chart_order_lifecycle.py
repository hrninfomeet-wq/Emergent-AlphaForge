"""No-network chart intent lifecycle and amendment tests."""
import asyncio
import socket
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from app.conditional.broker import PaperGateway, observation
from app.conditional.chart import chart_row
from app.conditional.engine import ConditionalEngine
from app.conditional.store import Conflict
from conditional_fakes import Clock, Market, MemoryStore, ForbiddenLiveGateway


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    loop = asyncio.new_event_loop()  # Windows asyncio initializes its private socket pair here.
    monkeypatch.setattr(asyncio, 'run', loop.run_until_complete)
    def forbidden(*args, **kwargs):
        raise AssertionError('network access forbidden')
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(socket.socket, 'connect_ex', forbidden)
    monkeypatch.setattr(socket, 'create_connection', forbidden)
    yield
    loop.close()


def setup(gateway=None):
    clock, store = Clock(), MemoryStore()
    market = Market(clock)
    engine = ConditionalEngine(store, market, {'paper': gateway or PaperGateway(), 'live': ForbiddenLiveGateway()},
                               now=clock, session_open=lambda _: True)
    return engine, store, market, clock


def payload(kind='LIMIT', **changes):
    result = {'instrument_key': 'NSE_FO|999', 'mode': 'paper', 'lots': 1, 'order_type': kind,
              'stop_price': 150, 'target_price': 250}
    if kind != 'MARKET':
        result['limit_price'] = 190 if kind == 'LIMIT' else 210
    if kind == 'STOP_LIMIT':
        result['trigger_price'] = 205
    result.update(changes)
    return result


async def submitted(engine, store, p):
    oid = (await engine.chart_preview(p))['preview_id']
    await engine.arm(oid)
    await engine.cycle()
    await engine.cycle()
    doc = await store.get(oid)
    assert len(doc['actions']) == 1, (doc, engine.last_error)
    return oid


async def change(engine, oid, field, price=None, version=0):
    return await engine.chart_change(oid, {'field': field, 'price': price, 'edit_version': version})


def test_market_fill_mark_and_cancel_closes():
    async def run():
        engine, store, market, clock = setup()
        oid = await submitted(engine, store, payload('MARKET'))
        await engine.cycle()
        doc = await store.get(oid)
        assert doc['entry_fill_price'] == 200 and doc['filled_quantity'] == 65
        market.premium = 210; clock.advance()
        await engine.cycle()
        doc = await store.get(oid)
        row = chart_row(doc, clock())
        assert row['mark_price'] == 210 and row['pnl'] == 650
        await change(engine, oid, 'cancel')
        await engine.cycle(); await engine.cycle()
        doc = await store.get(oid)
        assert doc['status'] == 'CLOSED' and doc['closed_quantity'] == 65
        assert doc['exit_reason'] == 'kill_or_cancel'
    asyncio.run(run())


def test_resting_limit_fills_only_at_or_below_limit():
    async def run():
        engine, store, market, clock = setup()
        oid = await submitted(engine, store, payload())
        assert (await store.get(oid))['actions'][0]['status'] == 'OPEN'
        market.premium = 191; clock.advance(); await engine.cycle()
        assert (await store.get(oid))['actions'][0]['filled_qty'] == 0
        market.premium = 190; clock.advance(); await engine.cycle()
        doc = await store.get(oid)
        assert doc['entry_fill_price'] == 190 and doc['filled_quantity'] == 65
    asyncio.run(run())


def test_stop_limit_latches_trigger_across_gap_and_retrace():
    async def run():
        engine, store, market, clock = setup()
        oid = await submitted(engine, store, payload('STOP_LIMIT'))
        market.premium = 215; clock.advance(); await engine.cycle()
        action = (await store.get(oid))['actions'][0]
        assert action['status'] == 'OPEN' and action['observation']['triggered'] is True
        market.premium = 204; clock.advance(); await engine.cycle()
        doc = await store.get(oid)
        assert doc['filled_quantity'] == 65 and doc['entry_fill_price'] == 204
    asyncio.run(run())


def test_pending_limit_amendment_keeps_action_identity():
    async def run():
        engine, store, market, clock = setup()
        oid = await submitted(engine, store, payload())
        identity = (await store.get(oid))['actions'][0]['id']
        await change(engine, oid, 'limit_price', 195)
        await engine.cycle()
        doc = await store.get(oid)
        assert doc['actions'][0]['id'] == identity and len(doc['actions']) == 1
        assert doc['actions'][0]['limit_price'] == 195 and not doc.get('pending_chart_change')
        market.premium = 195; clock.advance(); await engine.cycle()
        assert (await store.get(oid))['entry_fill_price'] == 195
    asyncio.run(run())


def test_tighten_stop_on_same_order_then_close():
    async def run():
        engine, store, market, clock = setup()
        oid = await submitted(engine, store, payload('MARKET'))
        await engine.cycle()
        entry_id = (await store.get(oid))['actions'][0]['id']
        await change(engine, oid, 'stop_price', 190)
        await engine.cycle()
        doc = await store.get(oid)
        assert doc['_id'] == oid and doc['exit_state']['stop'] == 190
        market.premium = 190; clock.advance()
        await engine.cycle(); await engine.cycle()
        doc = await store.get(oid)
        assert doc['status'] == 'CLOSED' and doc['exit_reason'] == 'stop'
        assert doc['actions'][0]['id'] == entry_id and doc['actions'][-1]['fill_price'] == 190
        assert (doc['actions'][-1]['fill_price'] - doc['entry_fill_price']) * doc['closed_quantity'] == -650
    asyncio.run(run())


def test_target_exit():
    async def run():
        engine, store, market, clock = setup()
        oid = await submitted(engine, store, payload('MARKET'))
        await engine.cycle()
        market.premium = 250; clock.advance()
        await engine.cycle(); await engine.cycle()
        doc = await store.get(oid)
        assert doc['status'] == 'CLOSED' and doc['exit_reason'] == 'target'
        assert doc['closed_quantity'] == 65
    asyncio.run(run())


def test_stale_edit_and_kill_fence_pending_amendment():
    async def run():
        engine, store, _, _ = setup()
        oid = await submitted(engine, store, payload())
        await change(engine, oid, 'limit_price', 195)
        with pytest.raises(Conflict):
            await change(engine, oid, 'limit_price', 196)
        await engine.kill('paper'); await engine.reset('paper')
        await engine.cycle()
        doc = await store.get(oid)
        assert doc['actions'][0]['limit_price'] == 190
        assert doc['actions'][0]['status'] == 'CANCELED'
        assert len(doc['actions']) == 1
    asyncio.run(run())


class RaceGateway(PaperGateway):
    def __init__(self, status):
        self.status, self.amend_calls = status, 0
    async def observe(self, action):
        if self.status == 'UNKNOWN':
            return observation('UNKNOWN', broker_id=action.get('broker_id'))
        if self.status == 'PARTIAL':
            return observation('PARTIAL', broker_id=action.get('broker_id'), filled_qty=10, fill_price=190)
        if self.status == 'COMPLETE':
            return observation('COMPLETE', broker_id=action.get('broker_id'), filled_qty=65, fill_price=190)
        return await super().observe(action)
    async def amend_entry(self, action):
        self.amend_calls += 1
        return await super().amend_entry(action)


@pytest.mark.parametrize('status', ['UNKNOWN', 'PARTIAL', 'COMPLETE'])
def test_amendment_does_not_transmit_on_unknown_partial_or_fill_race(status):
    async def run():
        gateway = RaceGateway(status)
        engine, store, _, _ = setup(gateway)
        oid = await submitted(engine, store, payload())
        await change(engine, oid, 'limit_price', 195)
        await engine.cycle()
        doc = await store.get(oid)
        assert gateway.amend_calls == 0
        assert doc['actions'][0]['limit_price'] == 190
        if status == 'UNKNOWN':
            assert doc.get('pending_chart_change')
        else:
            assert not doc.get('pending_chart_change')
            assert any(e['event'] == 'CHART_CHANGE_REFUSED' for e in doc['events'])
    asyncio.run(run())


def test_live_market_rejected_before_live_gateway():
    async def run():
        engine, _, _, _ = setup()
        with pytest.raises(ValueError, match='limit and stop-limit'):
            await engine.chart_preview(payload('MARKET', mode='live'))
    asyncio.run(run())

class FillDuringAmendGateway(PaperGateway):
    def __init__(self):
        self.amend_calls = 0
    async def amend_entry(self, action):
        self.amend_calls += 1
        return dict(observation('COMPLETE', broker_id=action.get('broker_id'),
                                filled_qty=65, fill_price=190), amended=False)


def test_fill_during_amend_is_not_retried_or_reported_as_confirmed():
    async def run():
        gateway = FillDuringAmendGateway()
        engine, store, _, _ = setup(gateway)
        oid = await submitted(engine, store, payload())
        await change(engine, oid, 'limit_price', 195)
        await engine.cycle()
        await engine.cycle()
        doc = await store.get(oid)
        assert gateway.amend_calls == 1
        assert doc['filled_quantity'] == 65
        assert doc['actions'][0]['limit_price'] == 190
        assert doc['spec']['chart']['limit_price'] == 190
        assert not doc.get('pending_chart_change')
        assert any(e['event'] == 'CHART_CHANGE_REFUSED' for e in doc['events'])
    asyncio.run(run())


def test_stop_limit_trigger_latch_survives_missing_quote():
    async def run():
        engine, store, market, clock = setup()
        oid = await submitted(engine, store, payload('STOP_LIMIT'))
        market.premium = 215; clock.advance(); await engine.cycle()
        assert (await store.get(oid))['actions'][0]['observation']['triggered'] is True
        market.premium = None; clock.advance(); await engine.cycle()
        assert (await store.get(oid))['actions'][0]['observation']['triggered'] is True
        market.premium = 204; clock.advance(); await engine.cycle()
        assert (await store.get(oid))['entry_fill_price'] == 204
    asyncio.run(run())


def test_unfilled_stop_cannot_tighten_above_resting_limit():
    async def run():
        engine, store, _, _ = setup()
        oid = await submitted(engine, store, payload())
        with pytest.raises(ValueError, match='below'):
            await change(engine, oid, 'stop_price', 195)
        assert (await store.get(oid))['spec']['chart']['stop_price'] == 150
    asyncio.run(run())


class AmbiguousAmendGateway(PaperGateway):
    def __init__(self, readback):
        self.calls, self.readback = 0, readback
    async def amend_entry(self, action):
        self.calls += 1
        return observation('UNKNOWN', broker_id=action.get('broker_id'))
    async def observe(self, action):
        if self.calls:
            if self.readback == 'COMPLETE':
                return dict(observation('COMPLETE', broker_id=action.get('broker_id'),
                                        filled_qty=65, fill_price=195), amended=True)
            return dict(observation('OPEN', broker_id=action.get('broker_id')), amended=False)
        return await super().observe(action)


def test_ambiguous_amend_then_confirmed_fill_commits_new_spec_once():
    async def run():
        gateway = AmbiguousAmendGateway('COMPLETE')
        engine, store, _, _ = setup(gateway)
        oid = await submitted(engine, store, payload())
        await change(engine, oid, 'limit_price', 195)
        await engine.cycle(); await engine.cycle(); await engine.cycle()
        doc = await store.get(oid)
        assert gateway.calls == 1
        assert doc['filled_quantity'] == 65
        assert doc['actions'][0]['limit_price'] == 195
        assert doc['spec']['chart']['limit_price'] == 195
        assert not doc.get('pending_chart_change')
    asyncio.run(run())


def test_ambiguous_amend_original_open_never_retries_and_allows_cancel():
    async def run():
        gateway = AmbiguousAmendGateway('OPEN')
        engine, store, _, _ = setup(gateway)
        oid = await submitted(engine, store, payload())
        await change(engine, oid, 'limit_price', 195)
        await engine.cycle(); await engine.cycle(); await engine.cycle()
        assert gateway.calls == 1
        assert (await store.get(oid))['pending_chart_change']
        await change(engine, oid, 'cancel', version=1)
        await engine.cycle(); await engine.cycle()
        doc = await store.get(oid)
        assert gateway.calls == 1 and doc['actions'][0]['status'] == 'CANCELED'
    asyncio.run(run())


def test_live_cancel_explicit_confirmation_ignores_failed_readiness():
    async def run():
        engine, store, _, _ = setup()
        oid = await submitted(engine, store, payload())
        doc = await store.get(oid)
        doc['mode'] = 'live'
        await store.save(doc)
        async def unavailable():
            return False, 'unavailable'
        engine.live_ready = unavailable
        with pytest.raises(ValueError, match='Explicit live confirmation'):
            await engine.chart_change(oid, {'field': 'cancel', 'edit_version': 0})
        result = await engine.chart_change(oid, {'field': 'cancel', 'edit_version': 0, 'confirm_live': True})
        assert result['cancel_requested'] is True
    asyncio.run(run())
