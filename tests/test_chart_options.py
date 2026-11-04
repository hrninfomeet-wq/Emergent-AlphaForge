import asyncio
import json
from datetime import datetime, timezone
import pytest
from app.chart_options import ChartOptionsService, option_events

NOW = datetime(2026, 11, 4, 6, tzinfo=timezone.utc).timestamp()
class Catalog:
    async def resolve(self, key):
        if key == 'NIFTY': key = 'NSE_INDEX|Nifty 50'
        if key == 'SENSEX': key = 'BSE_INDEX|SENSEX'
        return {'instrument_key': key, 'symbol': key.split('|')[-1], 'kind':'index', 'exchange':key[:3], 'segment':key.split('|')[0], 'volume_available':False}

def contracts(key):
    exchange=key[:3]
    return [{'instrument_key':f'{exchange}_FO|{expiry}-{strike}{side}', 'trading_symbol':f'{strike}{side}', 'underlying_key':key,
             'expiry':expiry, 'strike_price':strike, 'instrument_type':side, 'lot_size':75}
            for expiry in ['2026-11-03','2026-11-10','2026-11-17'] for strike in [100,110] for side in ['CE','PE']]

def chain(key, expiry):
    return {'status':'success','data':[{'underlying_key':key,'expiry':expiry,'strike_price':strike,'underlying_spot_price':107,
        'call_options':{'instrument_key':f'{key[:3]}_FO|{expiry}-{strike}CE','market_data':{'ltp':0,'oi':7,'prev_oi':0,'volume':None},'option_greeks':{'iv':262.31}},
        'put_options':{'instrument_key':f'{key[:3]}_FO|{expiry}-{strike}PE','market_data':{},'option_greeks':{}}} for strike in [100,110]]}

@pytest.mark.asyncio
@pytest.mark.parametrize('symbol', ['NIFTY','SENSEX'])
async def test_nearest_expiry_and_raw_fields(symbol):
    async def get(url): return {'status':'success','data':contracts('NSE_INDEX|Nifty 50' if symbol=='NIFTY' else 'BSE_INDEX|SENSEX')}
    async def fetch(key, expiry): return chain(key, expiry)
    service=ChartOptionsService(catalog=Catalog(),get=get,chain=fetch,clock=lambda:NOW)
    result=await service.snapshot(symbol)
    assert result['expiries']==['2026-11-10','2026-11-17']
    assert result['expiry']=='2026-11-10' and result['atm_strike']==110
    leg=result['rows'][0]['ce']
    assert (leg['ltp'],leg['previous_oi'],leg['oi_change'],leg['iv'],leg['quote_time_ms']) == (0,0,7,262.31,None)
    assert result['rows'][0]['pe']['oi'] is None
    with pytest.raises(ValueError): await service.snapshot(symbol, expiry='2026-11-03')

@pytest.mark.asyncio
async def test_foreign_identity_and_stale_fallback():
    key='NSE_INDEX|Nifty 50'; now=[NOW]; fail=[False]
    async def get(url): return {'status':'success','data':contracts(key)}
    async def fetch(k,e):
        if fail[0]: raise RuntimeError('offline')
        return chain(k,e)
    s=ChartOptionsService(catalog=Catalog(),get=get,chain=fetch,clock=lambda:now[0])
    first=await s.snapshot(key); now[0]+=16; fail[0]=True
    stale=await s.snapshot(key)
    assert stale['stale'] and stale['captured_at_ms']==first['captured_at_ms'] and 'failed' in stale['warnings'][-1]
    bad=chain(key,'2026-11-10'); bad['data'][0]['call_options']['instrument_key']='NSE_FO|2026-11-10-110CE'
    with pytest.raises(ValueError, match='identity'): s._parse(bad, await s.underlying(key), '2026-11-10', await s._contracts(await s.underlying(key)))

@pytest.mark.asyncio
async def test_stream_updates_and_releases():
    key='NSE_INDEX|Nifty 50'
    async def get(url): return {'status':'success','data':contracts(key)}
    async def fetch(k,e): return chain(k,e)
    s=ChartOptionsService(catalog=Catalog(),get=get,chain=fetch,clock=lambda:NOW)
    initial=await s.snapshot(key)
    class Manager:
        released=[]
        async def acquire_quote_batch(self, keys): self.keys=keys; return 'lease'
        async def release_quote_batch(self, lease): self.released.append(lease)
        def status(self): return {'connected':True}
        def latest_tick_map(self): return {'NSE_FO|2026-11-10-100CE':{'last_price':9,'open_interest':10,'volume_traded_today':5,'implied_volatility':0.1,'ts':int(NOW*1000),'ingest_ts':int(NOW*1000)}}
    m=Manager()
    async def disconnected(): return False
    gen=option_events(service=s,manager=m,initial=initial,key=key,expiry=initial['expiry'],center=110,radius=20,is_disconnected=disconnected)
    frame=await anext(gen); payload=json.loads(frame.split('data: ',1)[1]); leg=payload['rows'][0]['ce']
    assert (leg['ltp'],leg['oi'],leg['volume'],leg['iv'],leg['quote_status']) == (9,10,5,262.31,'live')
    await gen.aclose()
    assert m.released==['lease']

@pytest.mark.asyncio
async def test_cache_singleflight_and_failure_backoff():
    key = 'NSE_INDEX|Nifty 50'; now = [NOW]; calls = {'catalog': 0, 'chain': 0}; fail = [False]
    async def get(url):
        calls['catalog'] += 1
        await asyncio.sleep(0)
        return {'status': 'success', 'data': contracts(key)}
    async def fetch(k, expiry):
        calls['chain'] += 1
        await asyncio.sleep(0)
        if fail[0]: raise RuntimeError('offline')
        return chain(k, expiry)
    service = ChartOptionsService(catalog=Catalog(), get=get, chain=fetch, clock=lambda: now[0])
    results = await asyncio.gather(*(service.snapshot(key) for _ in range(8)))
    assert calls == {'catalog': 1, 'chain': 1}
    now[0] += 16; fail[0] = True
    stale = await asyncio.gather(*(service.snapshot(key) for _ in range(8)))
    assert calls['chain'] == 2
    assert all(item['stale'] and item['captured_at_ms'] == results[0]['captured_at_ms'] for item in stale)
    now[0] += 16; fail[0] = False
    assert not (await service.snapshot(key))['stale']
    assert calls['chain'] == 3

@pytest.mark.asyncio
async def test_expiry_rolls_at_ist_midnight_inside_catalog_cache():
    key = 'NSE_INDEX|Nifty 50'
    now = [datetime(2026, 11, 10, 18, 29, tzinfo=timezone.utc).timestamp()]
    async def get(url): return {'status': 'success', 'data': contracts(key)}
    async def fetch(k, expiry): return chain(k, expiry)
    service = ChartOptionsService(catalog=Catalog(), get=get, chain=fetch, clock=lambda: now[0])
    assert (await service.snapshot(key))['expiry'] == '2026-11-10'
    now[0] += 120
    assert (await service.snapshot(key))['expiry'] == '2026-11-17'
    with pytest.raises(ValueError, match='Expiry unavailable'):
        await service.snapshot(key, expiry='2026-11-10')

@pytest.mark.asyncio
async def test_stream_requires_timestamps_and_recovers_connection():
    key = 'NSE_INDEX|Nifty 50'
    async def get(url): return {'status': 'success', 'data': contracts(key)}
    async def fetch(k, expiry): return chain(k, expiry)
    service = ChartOptionsService(catalog=Catalog(), get=get, chain=fetch, clock=lambda: NOW)
    initial = await service.snapshot(key)
    class Manager:
        connected = False
        tick = {'last_price': 9, 'implied_volatility': 0.1}
        released = []
        async def acquire_quote_batch(self, keys):
            assert len(keys) <= 163
            return 'lease'
        async def release_quote_batch(self, lease): self.released.append(lease)
        def status(self): return {'connected': self.connected}
        def latest_tick_map(self): return {'NSE_FO|2026-11-10-100CE': self.tick}
    manager = Manager()
    async def disconnected(): return False
    gen = option_events(service=service, manager=manager, initial=initial, key=key,
                        expiry=initial['expiry'], center=110, radius=20, is_disconnected=disconnected, interval=0)
    async def next_leg():
        return json.loads((await anext(gen)).split('data: ', 1)[1])['rows'][0]['ce']
    assert (await next_leg())['quote_status'] == 'disconnected'
    manager.connected = True
    leg = await next_leg()
    assert leg['quote_status'] == 'stale' and leg['ltp'] == 0 and leg['quote_time_ms'] is None
    manager.tick.update(ts=int(NOW * 1000), ingest_ts=int(NOW * 1000))
    leg = await next_leg()
    assert leg['quote_status'] == 'live' and leg['ltp'] == 9 and leg['iv'] == 262.31
    assert leg['field_sources'] == {'ltp': 'upstox_ws_v3', 'oi': 'upstox_rest_v2', 'volume': 'upstox_rest_v2'}
    await gen.aclose()
    assert manager.released == ['lease']

@pytest.mark.asyncio
async def test_fresh_tick_without_quote_fields_does_not_claim_live():
    key = 'NSE_INDEX|Nifty 50'
    async def get(url): return {'status': 'success', 'data': contracts(key)}
    async def fetch(k, expiry): return chain(k, expiry)
    service = ChartOptionsService(catalog=Catalog(), get=get, chain=fetch, clock=lambda: NOW)
    initial = await service.snapshot(key)
    class Manager:
        async def acquire_quote_batch(self, keys): return 'lease'
        async def release_quote_batch(self, lease): pass
        def status(self): return {'connected': True}
        def latest_tick_map(self):
            return {'NSE_FO|2026-11-10-100CE': {'ts': int(NOW * 1000), 'ingest_ts': int(NOW * 1000), 'implied_volatility': 0.2}}
    async def disconnected(): return False
    gen = option_events(service=service, manager=Manager(), initial=initial, key=key, expiry=initial['expiry'], center=110, radius=20, is_disconnected=disconnected)
    payload = json.loads((await anext(gen)).split('data: ', 1)[1]); leg = payload['rows'][0]['ce']
    assert not leg['live_update'] and leg['quote_source'] == 'upstox_rest_v2'
    assert leg['quote_status'] == 'waiting' and leg['quote_time_ms'] is None
    assert set(leg['field_sources'].values()) == {'upstox_rest_v2'}
    assert payload['spot_source'] == 'upstox_rest_v2' and payload['spot_quote_time_ms'] is None
    await gen.aclose()

@pytest.mark.asyncio
async def test_max_radius_bounded_lease_and_cancel_cleanup():
    key = 'NSE_INDEX|Nifty 50'; expiry = '2026-11-10'
    rows = [{'instrument_key': f'NSE_FO|{strike}{side}', 'trading_symbol': f'{strike}{side}', 'underlying_key': key,
             'expiry': expiry, 'strike_price': strike, 'instrument_type': side} for strike in range(1, 102) for side in ('CE', 'PE')]
    data = [{'underlying_key': key, 'expiry': expiry, 'strike_price': strike, 'underlying_spot_price': 51,
             'call_options': {'instrument_key': f'NSE_FO|{strike}CE'}, 'put_options': {'instrument_key': f'NSE_FO|{strike}PE'}} for strike in range(1, 102)]
    async def get(url): return {'status': 'success', 'data': rows}
    async def fetch(k, e): return {'status': 'success', 'data': data}
    service = ChartOptionsService(catalog=Catalog(), get=get, chain=fetch, clock=lambda: NOW)
    initial = await service.snapshot(key, radius=40)
    class Manager:
        released = []
        async def acquire_quote_batch(self, keys): self.keys = keys; return 'lease'
        async def release_quote_batch(self, lease): self.released.append(lease)
        def status(self): return {'connected': False}
        def latest_tick_map(self): return {}
    manager = Manager()
    async def disconnected(): return False
    gen = option_events(service=service, manager=manager, initial=initial, key=key, expiry=expiry, center=51, radius=40, is_disconnected=disconnected, interval=60)
    await anext(gen)
    assert len(manager.keys) == 163 and len(set(manager.keys)) == 163
    task = asyncio.create_task(anext(gen))
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError): await task
    assert manager.released == ['lease']

@pytest.mark.asyncio
async def test_malformed_chain_envelope_and_row_are_rejected():
    key = 'NSE_INDEX|Nifty 50'
    async def get(url): return {'status': 'success', 'data': contracts(key)}
    service = ChartOptionsService(catalog=Catalog(), get=get, clock=lambda: NOW)
    underlying = await service.underlying(key); valid = await service._contracts(underlying)
    for payload in (None, {'status': 'error', 'data': []}, {'status': 'success', 'data': [None]}):
        with pytest.raises(ValueError): service._parse(payload, underlying, '2026-11-10', valid)
