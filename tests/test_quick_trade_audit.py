"""Audit reproductions, not safety certification. No network or production DB.

Runs the actual manual executor, router arm closure, software guard and exit
builder against MockNoren. Fixture contract dates/metadata are synthetic.
"""
import ast
import asyncio
import json
import logging
import socket
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

import pytest

from tests.test_live_executor import (
    FakeAsyncCollection, FakeEngine, _live_test_mode_store, _paper_mode_store,
    _fresh_intent_store,
)
from app.live.executor import place_live_test_order
from app.live.mock_noren import MockNoren, make_om
from app.live.session_store import SessionStore
from app.live.live_sl_monitor import build_monitor_state
from app.live.live_position_guard import LiveMonitorRegistry, LivePositionGuard
from app.live.auto_square import square_position

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 10, 23, 5, 0, tzinfo=timezone.utc)
CASES = [('NIFTY', 'NFO', 'NIFTY', 65),
         ('BANKNIFTY', 'NFO', 'BANKNIFTY', 30),
         ('SENSEX', 'BFO', 'BSXOPT', 20)]


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    # Windows creates a loopback socketpair for the loop's own wakeup pipe.
    # Initialize that pipe before forbidding every task-side connection.
    loop = asyncio.new_event_loop()
    def forbidden(*args, **kwargs):
        raise AssertionError('Audit must never connect to network or production DB')
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(socket.socket, 'connect_ex', forbidden)
    yield loop
    loop.close()


def make_arm(registry, session):
    # Execute the real closure without importing the router/runtime singletons.
    tree = ast.parse((ROOT / 'backend/app/routers/live_broker.py').read_text(encoding='utf-8'))
    node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == '_make_arm')
    scope = dict(Any=Any, Dict=Dict, Optional=Optional, SessionStore=SessionStore,
                 build_monitor_state=build_monitor_state, _get_live_registry=lambda: registry,
                 _GUARD_DEFAULT_STOP_PCT=50, _utcnow_iso=lambda: NOW.isoformat(),
                 log=logging.getLogger(__name__))
    exec(compile(ast.Module(body=[node], type_ignores=[]), '<actual-router-arm>', 'exec'), scope)
    return scope['_make_arm'](ref_ltp=200, session_store=session,
                              levels={'stop_pct': 10, 'target_pct': 20})


async def scenario(case, *, paper=False, fill=200):
    underlying, exch, symname, lot = case
    tsym = f'{underlying}27OCT26C25000'  # synthetic broker identity
    scrip = dict(tsym=tsym, token='999', ls=str(lot), ti='0.05', symname=symname,
                 optt='CE', exd='27-OCT-2026', dname=f'{underlying} 27OCT26 25000 CE')
    contract = dict(underlying=underlying, strike=25000, side='CE', expiry_date='2026-10-27')
    client = MockNoren(limits_data={'cash': '1000000'})
    registry = LiveMonitorRegistry()
    session = SessionStore(FakeAsyncCollection())
    result = await place_live_test_order(
        contract, side='B', ref_ltp=200, band_pct=3, levels={'stop_pct': 10},
        client=client, mode_store=_paper_mode_store() if paper else _live_test_mode_store(),
        intent_store=_fresh_intent_store(), engine=FakeEngine(),
        search_fn=lambda *args: [scrip], arm=make_arm(registry, session))
    if paper:
        assert not result['placed']
        assert await client.order_book() == []
        print(json.dumps({'instrument': underlying, 'mode': 'PAPER', 'result': result}))
        return
    assert result['placed'] and result['protected']
    entry_order = (await client.order_book())[0]
    assert entry_order['prctyp'] == 'LMT' and entry_order['prc'] == 201
    assert entry_order['qty'] == lot and entry_order['exch'] == exch
    assert entry_order['trgprc'] is None
    assert (await session.get())['sl_norenordno'] is None
    oid = result['norenordno']
    client.emit_om(make_om(oid, 'COMPLETE', 'Fill', str(lot), str(fill)))
    # MockNoren emits events but does not apply them to its order book.
    # Supply the independent broker-book observation explicitly.
    entry_order.update(status='COMPLETE', fillshares=str(lot), avgprc=str(fill))
    position = dict(tsym=tsym, exch=exch, netqty=str(lot), lp=str(fill), prd='I',
                    token='999', daybuyavgprc=str(fill), urmtom='0')
    client.set_position_book([position])
    ticks = {}
    async def get_client():
        return client
    async def on_mark(marks):
        pass
    guard = LivePositionGuard(registry=registry, client_factory=get_client,
                              square_fn=square_position, premium_tick_fn=lambda: ticks,
                              now_fn=lambda: NOW, on_mark=on_mark)
    await guard._cycle()
    state = registry.get(oid)['state']
    assert state['entry'] == fill
    assert state['stop_level'] == 180 and state['target_level'] == 240
    key = ('NSE_FO' if exch == 'NFO' else 'BSE_FO') + '|999'
    events = []
    for price in [190, 180]:
        ticks[key] = dict(last_price=price, ingest_ts=int(NOW.timestamp() * 1000))
        await guard._fast_premium_pass(NOW)
        events.append(dict(tick=price, stop=registry.get(oid)['state']['stop_level'],
                           orders=len(await client.order_book())))
    orders = await client.order_book()
    assert len(orders) == 2, orders
    assert orders[1]['trantype'] == 'S' and orders[1]['qty'] == lot
    exit_submitted = dict(orders[1])
    # Acceptance is not a fill: retain protection until repeated flat reads.
    assert len(registry) == 1
    client.emit_om(make_om(orders[1]['norenordno'], 'COMPLETE', 'Fill', str(lot), '180'))
    orders[1].update(status='COMPLETE', fillshares=str(lot), avgprc='180')
    client.set_position_book([dict(position, netqty='0', lp='180')])
    await guard._cycle()
    await guard._cycle()
    assert len(registry) == 0
    print(json.dumps(dict(instrument=underlying, simulation='MockNoren only',
                          entry=dict(exchange=exch, quantity=lot, limit=201, fill=fill),
                          broker_stop=None, stop=180, target=240, ticks=events,
                          exit_submitted=exit_submitted, simulated_exit_fill=180,
                          final_registry_count=len(registry))))


@pytest.mark.parametrize('case', CASES, ids=[c[0] for c in CASES])
def test_manual_entry_tick_stop_exit_simulation(case, no_network):
    no_network.run_until_complete(scenario(case))


@pytest.mark.parametrize('case', CASES, ids=[c[0] for c in CASES])
def test_paper_mode_does_not_simulate_quick_trade_entry(case, no_network):
    no_network.run_until_complete(scenario(case, paper=True))


@pytest.mark.parametrize('case', CASES, ids=[c[0] for c in CASES])
def test_fill_basis_mismatch_is_reproduced(case, no_network):
    # A BUY limit of 201 can legitimately fill at 201. Desired 10% stop=180.90,
    # actual stop=180; desired 20% target=241.20, actual=240.
    no_network.run_until_complete(scenario(case, fill=201))
