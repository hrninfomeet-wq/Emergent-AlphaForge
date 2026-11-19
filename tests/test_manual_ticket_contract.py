"""No-network checks: unsupported tickets stop before validation/execution.

Route functions are AST-loaded without importing runtime or starting services.
The forbidden-call stubs raise; they do not simulate any broker order.
"""
import ast
import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, Optional

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))


def ticket(**updates):
    return dict(dict(side='B', lots=1, order_type='LIMIT', product='MIS',
                     ref_ltp=200, band_pct=5, buffer_pct=0.5), **updates)


def load_route(name, namespace):
    source = (ROOT / 'backend/app/routers/live_broker.py').read_text(encoding='utf-8')
    node = next(n for n in ast.parse(source).body if isinstance(n, ast.AsyncFunctionDef) and n.name == name)
    node.decorator_list = []
    scope = dict(Any=Any, Dict=Dict, Optional=Optional, _ApproveBody=object,
                 _OrderTicketBody=object, **namespace)
    exec(compile(ast.Module(body=[node], type_ignores=[]), '<isolated-route>', 'exec'), scope)
    return scope[name]


@pytest.mark.parametrize('change', [dict(lots=2), dict(product='NRML'),
                                  dict(order_type='SL-LMT'), dict(order_type='MARKET'),
                                  dict(buffer_pct=2)])
def test_old_pending_unsupported_approval_never_reaches_executor(change):
    payload = ticket(); payload.update(change)
    store = SimpleNamespace(approve=lambda *a: {'ok': True, 'payload': {'ticket': payload}},
                            revert_to_pending=lambda *a: None)
    async def forbidden(*args):
        pytest.fail('unsupported approved ticket reached execution')
    route = load_route('live_order_approve', dict(
        _approval_store=lambda: store, _utcnow_iso=lambda: '2026-10-23T00:00:00+00:00',
        _PlaceBody=lambda **kw: SimpleNamespace(**kw), _place_manual_order=forbidden,
        HTTPException=RuntimeError))
    result = asyncio.run(route('isolated', SimpleNamespace(token='synthetic')))
    assert result['placed'] is False
    assert result['retryable'] is False


@pytest.mark.parametrize('change', [dict(lots=2), dict(product='NRML'),
                                  dict(order_type='SL-LMT'), dict(order_type='MARKET')])
def test_unsupported_ticket_cannot_create_approval_or_read_broker(change):
    payload = ticket(); payload.update(change)
    async def forbidden(*args):
        pytest.fail('unsupported ticket reached broker-backed validation')
    route = load_route('live_order_create_approval', {'_validate_order_ticket': forbidden})
    result = asyncio.run(route(SimpleNamespace(**payload, dict=lambda: payload)))
    assert result['ok'] is False
    assert result['verdicts'][0]['check'] == 'manual_execution'


def test_supported_instruction_and_default_buffer_are_not_blocked():
    from app.live.manual_ticket import manual_ticket_verdict
    assert manual_ticket_verdict(ticket())['ok']
    assert manual_ticket_verdict(ticket(buffer_pct=None))['ok']


@pytest.mark.parametrize('underlying', ['NIFTY', 'BANKNIFTY', 'SENSEX'])
def test_market_is_rejected_before_symbol_resolution(underlying):
    from app.live.flattrade_symbol import rules_for
    from app.live.order_builder import validate_and_build
    assert 'MARKET' not in rules_for(underlying)['price_types']
    def forbidden(*args):
        pytest.fail('unsupported MARKET ticket reached symbol lookup')
    children, verdicts = validate_and_build(dict(ticket(order_type='MARKET'),
                                                underlying=underlying, search_fn=forbidden))
    assert children is None
    assert verdicts[0]['check'] == 'exchange_order_type'


def test_frontend_rejects_changed_ticket_and_old_server_preview():
    import json
    import subprocess
    url = (ROOT / 'frontend/src/lib/manualTicket.js').as_uri()
    script = f"""import {{ manualTicketPlacementError as check }} from {json.dumps(url)};
    const t = {json.dumps(ticket())};
    const p = {{ok:true, placement_supported:true, ticketKey:JSON.stringify(t)}};
    const results = [check(t,p), check({{...t,lots:2}},p),
      check({{...t,ref_ltp:201}},p), check(t,{{...p,placement_supported:undefined}}),
      check(t,{{...p,ok:false}}), check({{...t,product:'NRML'}},p)];
    process.stdout.write(JSON.stringify(results));"""
    result = subprocess.run(['node','--input-type=module','-e',script],
                            capture_output=True,text=True,check=True)
    outcomes = json.loads(result.stdout)
    assert outcomes[0] is None
    assert all(isinstance(v,str) and v for v in outcomes[1:])


def entry(**changes):
    return dict(dict(exch='NFO', tsym='NIFTY26JUN26C25000', trantype='B',
                     prd='I', prctyp='LMT', ret='DAY', qty='65', prc='201'), **changes)


@pytest.mark.parametrize('change', [dict(qty='130'), dict(prc='201.05'), dict(tsym='different'),
                                  dict(exch='BFO'), dict(prd='M'), dict(trantype='S'),
                                  dict(prctyp='SL-LMT'), dict(ret='IOC'), dict(trgprc='180')])
def test_approved_entry_rejects_any_execution_field_drift(change):
    from app.live.manual_ticket import same_approved_entry
    assert same_approved_entry(entry(remarks='preview-id'), entry(remarks='execution-id'))
    assert not same_approved_entry(entry(), entry(**change))


def test_execution_build_drift_stops_before_any_broker_method():
    from app.live.executor import place_live_test_order
    from tests.test_live_validate_and_build import _fake_search
    async def mode():
        return {'mode': 'LIVE_TEST', 'single_shot_consumed': False}
    class NoBrokerCalls:
        def __getattr__(self, name):
            pytest.fail(f'drifted instruction reached broker method {name}')
    result = asyncio.run(place_live_test_order(
        {'underlying':'NIFTY','strike':25000,'side':'CE','expiry_date':'2026-06-26'},
        side='B', ref_ltp=200, band_pct=5, levels={}, client=NoBrokerCalls(),
        mode_store=SimpleNamespace(get=mode), intent_store=None, engine=None,
        search_fn=_fake_search, arm=None, approved_entry=entry(qty='130')))
    assert result['placed'] is False
    assert result['reason'].startswith('approved_order_changed')


@pytest.mark.parametrize('snapshot', [None, [], [None], [{}], [entry(qty='NaN')]])
def test_missing_or_malformed_approved_snapshot_cannot_disable_comparison(snapshot):
    store = SimpleNamespace(approve=lambda *a: {'ok':True, 'payload': {
        'ticket': ticket(), 'would_send': snapshot}}, revert_to_pending=lambda *a: None)
    async def forbidden(*args, **kwargs):
        pytest.fail('malformed approval reached execution')
    route = load_route('live_order_approve', dict(
        _approval_store=lambda:store, _utcnow_iso=lambda:'2026-10-23T00:00:00+00:00',
        _place_manual_order=forbidden))
    result = asyncio.run(route('isolated', SimpleNamespace(token='synthetic')))
    assert result['placed'] is False
    assert result['retryable'] is False
