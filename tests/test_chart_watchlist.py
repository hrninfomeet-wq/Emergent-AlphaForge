import asyncio
import copy
import json
import sys
from pathlib import Path
from types import SimpleNamespace
import pytest
from pymongo.errors import DuplicateKeyError
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from app.chart_watchlist import WatchlistStore, RevisionConflict, watchlist_events
from app.upstox_stream import UpstoxMarketStreamManager

class Collection:
    def __init__(self): self.doc = None
    async def find_one(self, q): return copy.deepcopy(self.doc)
    async def insert_one(self, d):
        if self.doc is not None: raise DuplicateKeyError('duplicate')
        self.doc = copy.deepcopy(d)
    async def replace_one(self, q, d, upsert=False):
        if self.doc['revision'] != q['revision']: return SimpleNamespace(matched_count=0)
        self.doc = copy.deepcopy(d); return SimpleNamespace(matched_count=1)

def instrument(key, symbol='ABC'):
    return {'instrument_key': key, 'symbol': symbol, 'exchange': 'NSE', 'segment': 'NSE_EQ', 'kind': 'equity'}

def test_store_persists_cas_validation_and_retained_identity():
    async def run():
        collection = Collection(); catalog = {'NSE_EQ|1': instrument('NSE_EQ|1')}
        async def resolve(key):
            if key not in catalog: raise ValueError('Unknown')
            return copy.deepcopy(catalog[key])
        store = WatchlistStore(collection, resolve)
        assert (await store.get())['revision'] == 0
        saved = await store.put(0, 'Main', [{'instrument_key':'NSE_EQ|1'}])
        fresh = WatchlistStore(collection, resolve)
        assert await fresh.get() == saved
        with pytest.raises(RevisionConflict): await fresh.put(0, 'Old', [])
        with pytest.raises(ValueError): await fresh.put(1, 'Bad', [{'instrument_key':'unknown'}])
        with pytest.raises(ValueError): await fresh.put(1, 'Bad', [{'instrument_key':'NSE_EQ|1'}, {'instrument_key':'NSE_EQ|1'}])
        item = saved['items'][0]; catalog['NSE_EQ|1'] = instrument('NSE_EQ|1', 'REUSED')
        updated = await fresh.put(1, 'Main', [{'id':item['id'], 'instrument_key':'NSE_EQ|1', 'label':'Renamed'}])
        assert updated['items'][0]['instrument']['symbol'] == 'ABC'
        assert (await fresh.put(2, 'Main', []))['items'] == []
    asyncio.run(run())

def test_batch_owners_budget_overlap_and_send_failure():
    async def run():
        m = UpstoxMarketStreamManager(); m.configure_session(session_id='x', instrument_keys=['base'])
        chart = await m.acquire_chart_instrument('shared')
        a = await m.acquire_quote_batch(['shared', 'extra']); b = await m.acquire_quote_batch(['extra'])
        await m.release_quote_batch(a)
        assert m._owned_keys() == {'base', 'shared', 'extra'}
        await m.release_quote_batch(b)
        assert m._owned_keys() == {'base', 'shared'}
        assert [x['instrument_key'] for x in m._persistent_ticks([{'instrument_key':'extra'}, {'instrument_key':'base'}])] == ['base']
        await m.release_chart_instrument(chart)
        m.configure_session(session_id='x', instrument_keys=['base'], mode='full_d30')
        with pytest.raises(ValueError): await m.acquire_quote_batch([str(i) for i in range(50)])
        class Fail:
            async def send(self, message): raise RuntimeError('failed')
        m._active_websocket = Fail()
        with pytest.raises(RuntimeError): await m.acquire_quote_batch(['uncertain'])
        assert not m._quote_leases and 'uncertain' in m._chart_pending_removals
    asyncio.run(run())

def test_stream_identity_staleness_revision_and_cleanup():
    async def run():
        col=Collection(); catalog={'k':instrument('k'), 'old':instrument('old')}
        async def resolve(key): return copy.deepcopy(catalog[key])
        store=WatchlistStore(col, resolve)
        saved=await store.put(0, 'List', [{'instrument_key':'k'}, {'instrument_key':'old'}])
        catalog['old']['symbol']='REUSED'
        m=UpstoxMarketStreamManager(); m._latest_ticks['k']={'last_price':100,'ts':1000,'ingest_ts':19999}
        m._task=asyncio.current_task(); m._session['connected']=True
        async def connected(): return False
        stream=watchlist_events(store=store,manager=m,revision=1,is_disconnected=connected,now_ms=lambda:20000,interval=.01)
        first=await anext(stream); data=json.loads(first.split('data: ',1)[1])
        assert data['quotes']['k']['status']=='stale'
        assert data['quotes']['old']['status']=='unavailable' and 'old' not in m._owned_keys()
        await store.put(1,'List',[])
        event=await anext(stream); assert event.startswith('event: watchlist_changed')
        await stream.aclose(); assert not m._quote_leases and not m._tick_subscribers
    asyncio.run(run())

def test_conflict_at_write_and_no_silent_replacement():
    async def run():
        col=Collection()
        async def resolve(key): return instrument(key)
        store=WatchlistStore(col,resolve)
        saved=await store.put(0,'List',[{'instrument_key':'k'}])
        with pytest.raises(ValueError): await store.put(1,'List',[{'instrument_key':'k'}])
        original=col.replace_one
        async def racing(q,d,upsert=False):
            col.doc['revision'] += 1
            return await original(q,d,upsert=upsert)
        col.replace_one=racing
        with pytest.raises(RevisionConflict): await store.put(1,'Other',[])
        assert col.doc['name']=='List' and col.doc['items']==saved['items']
    asyncio.run(run())

def test_cancelled_stream_releases_lease_and_subscriber():
    async def run():
        col=Collection()
        async def resolve(key): return instrument(key)
        store=WatchlistStore(col,resolve); await store.put(0,'List',[{'instrument_key':'k'}])
        m=UpstoxMarketStreamManager()
        async def connected(): return False
        stream=watchlist_events(store=store,manager=m,revision=1,is_disconnected=connected,interval=60)
        await anext(stream)
        task=asyncio.create_task(anext(stream)); await asyncio.sleep(.01); task.cancel()
        with pytest.raises(asyncio.CancelledError): await task
        assert not m._quote_leases and not m._tick_subscribers and not m._owned_keys()
    asyncio.run(run())

@pytest.mark.parametrize('healthy', [True, False])
def test_periodic_retry_recovers_unavailable_without_churning(healthy):
    async def run():
        col = Collection(); catalog = {'old': instrument('old'), 'healthy': instrument('healthy')}
        unavailable = set()
        async def resolve(key):
            if key in unavailable: raise ValueError('catalogue unavailable')
            return copy.deepcopy(catalog[key])
        store = WatchlistStore(col, resolve)
        keys = ['healthy', 'old'] if healthy else ['old']
        await store.put(0, 'List', [{'instrument_key': k} for k in keys])
        unavailable.add('old')
        manager = UpstoxMarketStreamManager(); clock = [0]
        async def connected(): return False
        stream = watchlist_events(store=store, manager=manager, revision=1,
            is_disconnected=connected, interval=0, monotonic=lambda: clock[0])
        first = json.loads((await anext(stream)).split('data: ', 1)[1])
        assert first['quotes']['old']['status'] == 'unavailable'
        clock[0] = 61
        assert (await anext(stream)).startswith('event: snapshot')  # Still unavailable: no reconnect churn.
        unavailable.remove('old'); clock[0] = 122
        assert (await anext(stream)).startswith('event: stream_error')
        with pytest.raises(StopAsyncIteration): await anext(stream)
        assert not manager._quote_leases
        reopened = watchlist_events(store=store, manager=manager, revision=1,
            is_disconnected=connected, interval=0, monotonic=lambda: clock[0])
        recovered = json.loads((await anext(reopened)).split('data: ', 1)[1])
        assert recovered['quotes']['old']['status'] != 'unavailable'
        assert 'old' in manager._owned_keys()
        await reopened.aclose()
    asyncio.run(run())

def test_periodic_identity_loss_releases_batch_before_error():
    async def run():
        col = Collection(); changed = [False]; clock = [0]
        async def resolve(key): return instrument(key, 'REUSED' if changed[0] else 'ABC')
        store = WatchlistStore(col, resolve)
        await store.put(0, 'List', [{'instrument_key': 'k'}])
        manager = UpstoxMarketStreamManager()
        async def connected(): return False
        stream = watchlist_events(store=store, manager=manager, revision=1,
            is_disconnected=connected, interval=0, monotonic=lambda: clock[0])
        await anext(stream)
        assert 'k' in manager._owned_keys()
        changed[0] = True; clock[0] = 61
        assert (await anext(stream)).startswith('event: stream_error')
        assert not manager._quote_leases and 'k' not in manager._owned_keys()
        await stream.aclose()
    asyncio.run(run())
