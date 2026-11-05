import copy
from datetime import datetime, timezone
import pytest
from app.chart_analysis import analyze_closed_bars, analysis_catalog

INST = {'instrument_key': 'NSE_INDEX|Nifty 50', 'kind': 'index'}
START = int(datetime(2026, 11, 4, 3, 45, tzinfo=timezone.utc).timestamp())

def candles(n=80):
    return [{'time': START + i*60, 'open': 100., 'high': 101., 'low': 99., 'close': 100., 'volume': None} for i in range(n)]

def test_catalog_scope_and_orderflow():
    catalog = analysis_catalog(INST, '1m')
    byid = {x['id']: x for x in catalog['strategies']}
    assert byid['smc_liquidity_sweep_fvg']['available']
    assert not byid['premium_momentum']['available'] and 'premium' in byid['premium_momentum']['reason']
    assert not next(x for x in catalog['overlays'] if x['id'] == 'volume_imbalance')['available']
    assert analysis_catalog(INST, '5m')['overlays'][0]['available']
    assert not analysis_catalog({'instrument_key':'NSE_EQ|ABC', 'kind':'equity'}, '1m')['strategies'][0]['available']

def test_deterministic_signal_closed_prefix_and_no_future_leak():
    bars = candles(); bars[-1].update(low=95., close=100.)
    result = analyze_closed_bars(bars, INST, '1m', 'smc_liquidity_sweep_fvg')
    sig = result['strategy']['signals'][0]
    assert sig['direction'] == 'CE' and sig['time'] == bars[-1]['time']
    assert 'sweep low+reclaim' in sig['reasons']
    extended = bars + [{'time': bars[-1]['time']+60, 'open':100., 'high':102., 'low':98., 'close':101., 'volume':None}]
    assert analyze_closed_bars(extended[:-1], INST, '1m', 'smc_liquidity_sweep_fvg') == result
    assert all(m['time'] <= bars[-1]['time'] for o in result['overlays'].values() for m in o['markers'])

def test_support_warmup_and_unsupported():
    for sid in ('premium_momentum', 'atm_premium_flow_scalp', 'arbitrary_plugin'):
        result = analyze_closed_bars(candles(), INST, '1m', sid)['strategy']
        assert result['status'] == 'unavailable' and result['reason'] and not result['signals']
    assert analyze_closed_bars(candles(10), INST, '1m', 'fibonacci_pullback')['strategy']['status'] == 'unavailable'
    assert analyze_closed_bars(candles(), {'instrument_key':'BSE_FO|1','kind':'option'}, '1m', 'fibonacci_pullback')['strategy']['status'] == 'unavailable'
    assert analyze_closed_bars(candles(), INST, '5m', 'fibonacci_pullback')['strategy']['status'] == 'unavailable'

def test_invalid_inputs_rejected_and_gap_prevents_signal():
    bars = candles(); bars[20]['time'] = bars[19]['time']
    with pytest.raises(ValueError): analyze_closed_bars(bars, INST, '1m')
    bars = candles(); bars[-1]['high'] = 90
    with pytest.raises(ValueError): analyze_closed_bars(bars, INST, '1m')
    bars = candles(); bars = bars[:50] + [{**b, 'time':b['time']+60} for b in bars[50:]]
    result = analyze_closed_bars(bars, INST, '1m', 'fibonacci_pullback')
    assert result['strategy']['status'] == 'unavailable' and 'gap' in result['strategy']['reason']

def test_fvg_zone_begins_at_detection_not_prior_bar():
    bars = candles(); bars[-1] = {**bars[-1], 'open':104., 'high':106., 'low':103., 'close':105.}
    zone = analyze_closed_bars(bars, INST, '1m')['overlays']['fvg']['zones'][0]
    assert zone['start'] == bars[-1]['time'] and zone['low'] == 101. and zone['high'] == 103.


def test_higher_timeframe_patterns_and_derivative_auction_are_not_suppressed():
    bars = candles(30)
    for i, b in enumerate(bars): b['time'] = START + 300 * i
    bars[-1].update(open=104., high=106., low=103., close=105.)
    result = analyze_closed_bars(bars, INST, '5m', 'smc_liquidity_sweep_fvg')
    assert result['overlays']['fvg']['zones'][0]['start'] == bars[-1]['time']
    assert result['strategy']['status'] == 'unavailable'
    bars = candles(30)
    for i, b in enumerate(bars): b['time'] = START + (335 + i) * 60
    bars[-1].update(open=104., high=106., low=103., close=105.)
    derivative = analyze_closed_bars(bars, {'kind':'option'}, '1m')
    assert derivative['overlays']['fvg']['zones'][0]['start'] == bars[-1]['time']

def test_historical_markers_are_prefix_invariant_and_imports_are_read_only():
    import ast
    from pathlib import Path
    bars = candles()
    bars[50].update(open=100., high=101., low=95., close=100.)
    prefix = analyze_closed_bars(bars[:60], INST, '1m')
    full = analyze_closed_bars(bars, INST, '1m')
    cutoff = bars[59]['time']
    for concept in ('patterns', 'displacement', 'structure_breaks'):
        assert prefix['overlays'][concept]['markers'] == [m for m in full['overlays'][concept]['markers'] if m['time'] <= cutoff]
    tree = ast.parse(Path('backend/app/chart_analysis.py').read_text())
    imports = [node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.module]
    assert not any(x.startswith(('app.runtime', 'app.live', 'app.backtest', 'app.strategy_deployments', 'app.upstox_client')) for x in imports)

def test_premium_discount_drawable_only_after_range_is_known():
    bars = candles()
    overlay = analyze_closed_bars(bars, INST, '1m')['overlays']['premium_discount']
    assert overlay['lines'][0]['price'] == 100
    discount, premium = overlay['zones']
    assert (discount['low'], discount['high'], premium['low'], premium['high']) == (99, 100, 100, 101)
    assert discount['start'] == bars[1]['time'] and discount['end'] == bars[-1]['time']
    assert premium['start'] == discount['start'] and all(z['color'].startswith('#') for z in overlay['zones'])
    assert analyze_closed_bars(bars[:1], INST, '1m')['overlays']['premium_discount']['zones'] == []

def test_transitive_analysis_run_blocks_execution_imports_and_discovery():
    import os
    import subprocess
    import sys
    source = r'''
import builtins
real_import = builtins.__import__
forbidden = ('app.runtime', 'app.live', 'app.backtest', 'app.strategy_deployments', 'app.upstox_client', 'app.optimizer', 'app.routers')
def guarded(name, *args, **kwargs):
    if any(name == prefix or name.startswith(prefix + '.') for prefix in forbidden):
        raise AssertionError('Forbidden import: ' + name)
    return real_import(name, *args, **kwargs)
builtins.__import__ = guarded
from app.strategies.base import StrategyRegistry
StrategyRegistry.auto_discover = lambda self: (_ for _ in ()).throw(AssertionError('Unexpected plugin discovery'))
from app.chart_analysis import analyze_closed_bars
bars = [dict(time=1793763900+i*60, open=100., high=101., low=99., close=100., volume=None) for i in range(80)]
bars[-1]['low'] = 95.
result = analyze_closed_bars(bars, {'instrument_key':'NSE_INDEX|Nifty 50','kind':'index'}, '1m', 'smc_liquidity_sweep_fvg')
assert result['strategy']['status'] == 'ready'
'''
    result = subprocess.run([sys.executable, '-c', source], env=os.environ.copy(), capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
