"""Isolated synthetic chart fixture. Never connects to a broker.

Build with REACT_APP_BACKEND_URL=http://localhost:8002 into a separate build
folder, then run: python tests/manual/chart_terminal_fixture.py --build <folder>
Open http://localhost:8002/charts. Orders exist only in this process's memory.
Watchlists use memory unless --watchlist-db explicitly selects the allowlisted
isolated Mongo database; production database configuration is never imported.
"""
import argparse
import asyncio
import json
import math
import os
import sys
import time
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'backend'), str(ROOT / 'tests'), str(Path(__file__).resolve().parent)]

# The production router imports app.db and app.conditional.service. Install only
# the interfaces it uses before importing it, preventing production wiring imports.
service = types.ModuleType('app.conditional.service')
db_stub = types.ModuleType('app.db')
def serialize_doc(value):
    if isinstance(value, dict):
        return {k: serialize_doc(v) for k, v in value.items() if k != '_id'}
    if isinstance(value, list):
        return [serialize_doc(v) for v in value]
    return value
db_stub.serialize_doc = serialize_doc
def forbidden_production_db():
    raise RuntimeError('Production database wiring is forbidden in the fixture')
db_stub.get_db = forbidden_production_db
sys.modules['app.db'] = db_stub
sys.modules['app.conditional.service'] = service
# PaperGateway imports only this validator from production market wiring. Keep
# the identical numeric check locally so ProductionMarket is never imported.
market_stub = types.ModuleType('app.conditional.market')
def positive(value, field, *, integer=False):
    if isinstance(value, bool):
        raise ValueError(f'{field} must be a positive number')
    try:
        number = float(value)
    except (ValueError, TypeError, OverflowError):
        raise ValueError(f'{field} missing or invalid') from None
    if not math.isfinite(number) or number <= 0 or (integer and not number.is_integer()):
        raise ValueError(f"{field} must be positive{' integer' if integer else ''}")
    return int(number) if integer else number
market_stub.positive = positive
sys.modules['app.conditional.market'] = market_stub

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from conditional_fakes import Clock, Market, MemoryStore, ForbiddenLiveGateway
from app.conditional.broker import PaperGateway
from app.conditional.engine import ConditionalEngine
from app.conditional.chart import chart_row
from app.routers.conditional_orders import api as conditional_router
from app.chart_stream import parse_chart_channels
from app.chart_watchlist import WatchlistStore
from app import chart_watchlist as watchlist_model
from app.routers import chart_watchlist as watchlist_router
from chart_watchlist_fixture import FIXTURE_DATABASE, open_collection, SyntheticQuoteManager

NOTICE = 'SYNTHETIC PAPER TEST — isolated fixture; no broker or database.'
_real_watchlist_state = watchlist_model.stream_state

def synthetic_watchlist_state(*args, **kwargs):
    return {**_real_watchlist_state(*args, **kwargs),
            'source': 'synthetic_ui_fixture', 'warnings': [NOTICE]}

watchlist_model.stream_state = synthetic_watchlist_state
OPTION = {'instrument_key': 'NSE_FO|999', 'symbol': 'SYNTHETIC PAPER TEST NIFTY 25000 CE',
          'name': NOTICE, 'kind': 'option', 'exchange': 'NSE', 'segment': 'NSE_FO',
          'expiry': '2026-10-27', 'strike': 25000, 'option_type': 'CE', 'volume_available': True}
INDEX = {'instrument_key': 'NSE_INDEX|Nifty 50', 'symbol': 'NIFTY', 'name': NOTICE,
         'kind': 'index', 'exchange': 'NSE', 'segment': 'NSE_INDEX', 'volume_available': False}
BANK_INDEX = {**INDEX, 'instrument_key': 'NSE_INDEX|Nifty Bank', 'symbol': 'BANKNIFTY'}
SENSEX_INDEX = {**INDEX, 'instrument_key': 'BSE_INDEX|SENSEX', 'symbol': 'SENSEX',
                'exchange': 'BSE', 'segment': 'BSE_INDEX'}
EQUITY = {**INDEX, 'instrument_key': 'NSE_EQ|SYNTHETIC_RELIANCE', 'symbol': 'RELIANCE',
          'kind': 'equity', 'segment': 'NSE_EQ', 'volume_available': True}
INSTRUMENTS = [INDEX, BANK_INDEX, SENSEX_INDEX, OPTION, EQUITY]
from chart_option_fixture import FixtureOptionMarket, OptionQuoteManager, configure as configure_options
option_market = FixtureOptionMarket([INDEX, SENSEX_INDEX])
INSTRUMENTS.extend(option_market.instruments.values())

class FixtureClock(Clock):
    # Advance between cycles only: snapshot timestamps cannot run microscopically
    # ahead of the engine's cycle-start timestamp and appear future-dated.
    pass

class DisabledLive(ForbiddenLiveGateway):
    protection_supported = False
    async def ready(self):
        return False, 'Synthetic fixture: live trading is permanently disabled'

clock = FixtureClock()
market = Market(clock)
store = MemoryStore()
engine = ConditionalEngine(store, market, {'paper': PaperGateway(), 'live': DisabledLive()},
                           now=clock, session_open=lambda _: True)
service.get_service = lambda: engine
async def fixture_status():
    return {'orders': await store.list(), 'live_available': False, 'live_reason': NOTICE}
service.status = fixture_status

app = FastAPI(title=NOTICE)
app.add_middleware(CORSMiddleware, allow_origins=['http://localhost:8002', 'http://127.0.0.1:8002'],
                   allow_methods=['GET', 'POST', 'PUT', 'OPTIONS'], allow_headers=['*'])

# Router's as_of_ms uses the simulated market clock. Translate only this public
# timestamp to wall time so the unchanged UI can evaluate snapshot freshness.
@app.get('/api/conditional-orders/chart/orders')
async def chart_orders():
    return {'rows': [chart_row(doc, clock()) for doc in await store.list()],
            'as_of_ms': time.time() * 1000,
            'controls': {mode: await store.control(mode) for mode in ('paper', 'live')}}

app.include_router(conditional_router, prefix='/api')

@app.on_event('startup')
async def start_worker():
    # Fail startup clearly if explicitly selected isolated Mongo is unavailable.
    await app.state.watchlist_store.get()
    async def work():
        while True:
            clock.advance(.5)
            await engine.cycle()
            await asyncio.sleep(0.5)
    app.state.worker = asyncio.create_task(work())

@app.on_event('shutdown')
async def stop_worker():
    app.state.worker.cancel()
    try:
        await app.state.worker
    except asyncio.CancelledError:
        pass
    if app.state.watchlist_client is not None:
        app.state.watchlist_client.close()

class Premium(BaseModel):
    premium: float = Field(gt=0, le=1000000, allow_inf_nan=False)

@app.post('/api/fixture/premium')
async def set_premium(body: Premium):
    market.premium = body.premium
    return {'premium': market.premium, 'notice': NOTICE}

class OptionMarketUpdate(BaseModel):
    instrument_key: str
    ltp: float | None = Field(default=None, ge=0, le=1000000, allow_inf_nan=False)
    oi: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    volume: float | None = Field(default=None, ge=0, allow_inf_nan=False)

@app.post('/api/fixture/option-market')
async def set_option_market(body: OptionMarketUpdate):
    if body.instrument_key not in option_market.instruments:
        raise HTTPException(400, 'Unknown synthetic option instrument')
    changes = {key: value for key, value in body.model_dump(exclude_none=True).items() if key != 'instrument_key'}
    if not changes:
        raise HTTPException(400, 'Supply at least one synthetic ltp, oi or volume')
    option_market.overrides.setdefault(body.instrument_key, {}).update(changes)
    return {'instrument_key': body.instrument_key, 'values': option_market.metrics(body.instrument_key),
            'source': 'synthetic_ui_fixture', 'warnings': [NOTICE]}

@app.get('/api/fixture/option-state')
async def option_state():
    return {'source': 'synthetic_ui_fixture', 'warnings': [NOTICE], 'expiries': option_market.expiries,
            'active_leases': len(app.state.quote_manager.leases),
            'leased_keys': [list(keys) for keys in app.state.quote_manager.leases.values()],
            'overrides': option_market.overrides}

@app.get('/api/charts/instruments')
async def instruments(q: str = ''):
    query = q.upper()
    return {'items': [i for i in INSTRUMENTS if query in (i['symbol'] + ' ' + i['instrument_key']).upper()],
            'warnings': [NOTICE]}

def get_instrument(key):
    result = next((i for i in INSTRUMENTS if i['instrument_key'] == key), None)
    if result is None:
        raise HTTPException(404, 'Unknown synthetic instrument')
    return result

def frame_seconds(frame):
    return {'1m': 60, '3m': 180, '5m': 300, '15m': 900, '30m': 1800,
            '1h': 3600, '1d': 86400, '1w': 604800}.get(frame, 60)

def fixture_price(key):
    if key == OPTION['instrument_key']:
        return market.premium
    if key in option_market.instruments:
        return option_market.metrics(key)['ltp']
    return market.spot * {'NSE_INDEX|Nifty 50': 1, 'NSE_INDEX|Nifty Bank': 2, 'BSE_INDEX|SENSEX': 3}.get(key, 1)


async def resolve_watchlist_instrument(key):
    return dict(get_instrument(key))


def configure_watchlist(database=None):
    global NOTICE
    collection, client = open_collection(database)
    app.state.watchlist_client = client
    app.state.watchlist_store = WatchlistStore(collection, resolve_watchlist_instrument)
    app.state.quote_manager = OptionQuoteManager(INSTRUMENTS, fixture_price, option_market)
    watchlist_router.get_watchlist_store = lambda: app.state.watchlist_store
    watchlist_router.get_quote_manager = lambda: app.state.quote_manager
    NOTICE = ('SYNTHETIC PAPER TEST — no broker; isolated test storage.' if database else
              'SYNTHETIC PAPER TEST — isolated fixture; no broker or database.')
    app.title = NOTICE
    for instrument in INSTRUMENTS:
        instrument['name'] = NOTICE


configure_watchlist()
app.include_router(watchlist_router.api, prefix='/api/charts')
configure_options(app, option_market)

def bar(key, frame, stamp, n=0):
    price = fixture_price(key)
    opened = price + 2 * math.sin(n / 12)
    close = price + 2 * math.sin((n + 1) / 12)
    return {'time': int(stamp), 'open': opened, 'high': max(opened, close) + 1,
            'low': min(opened, close) - 1, 'close': close,
            'volume': 1000 if key == OPTION['instrument_key'] else None}

@app.get('/api/charts/history')
async def history(instrument_key: str, timeframe: str = '1m', before: int = None, limit: int = 600):
    instrument = get_instrument(instrument_key)
    step = frame_seconds(timeframe)
    end = (int(time.time()) // step) * step if before is None else ((before - 1) // step) * step
    count = min(max(limit, 1), 600)
    rows = [bar(instrument_key, timeframe, end - (count - 1 - n) * step, n) for n in range(count)]
    return {'instrument': instrument, 'timeframe': timeframe, 'bars': rows,
            'source': 'synthetic_ui_fixture', 'warnings': [NOTICE], 'as_of_ms': int(time.time() * 1000),
            'volume_available': instrument['volume_available'], 'has_more': False, 'next_before': None}

@app.get('/api/charts/positions')
async def positions(instrument_key: str):
    get_instrument(instrument_key)
    return {'instrument_key': instrument_key, 'positions': [], 'warnings': [NOTICE],
            'as_of_ms': int(time.time() * 1000)}

@app.get('/api/charts/stream')
async def stream(instrument_key: str, timeframe: str = '1m'):
    get_instrument(instrument_key)
    return fixture_stream([{'instrument_key': instrument_key, 'timeframe': timeframe}])

@app.get('/api/charts/streams')
async def streams(channels: str):
    try:
        selected = parse_chart_channels(channels)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    for channel in selected:
        get_instrument(channel['instrument_key'])
    return fixture_stream(selected)

def fixture_stream(channels):
    async def events():
        while True:
            now = time.time()
            for channel in channels:
                instrument_key, timeframe = channel['instrument_key'], channel['timeframe']
                price = fixture_price(instrument_key)
                payload = {'instrument_key': instrument_key, 'timeframe': timeframe, 'connected': True,
                           'status': 'live', 'source': 'synthetic_ui_fixture', 'warnings': [NOTICE],
                           'emitted_at_ms': int(now * 1000), 'last_tick_age_ms': 0,
                           'tick': {'last_price': price, 'close_price': price - 1,
                                    'ts': int(now * 1000), 'ingest_ts': int(now * 1000)},
                           'bar': bar(instrument_key, timeframe, int(now) // frame_seconds(timeframe) * frame_seconds(timeframe), 0)}
                yield 'event: snapshot\ndata: ' + json.dumps(payload) + '\n\n'
            await asyncio.sleep(1)
    return StreamingResponse(events(), media_type='text/event-stream', headers={'Cache-Control': 'no-store'})

# Every other API route is explicitly unavailable rather than reaching production.
@app.api_route('/api/{path:path}', methods=['GET', 'POST', 'PUT', 'PATCH', 'DELETE'])
async def unavailable(path: str):
    raise HTTPException(404, 'Unavailable in synthetic paper fixture')

def configure_static(directory):
    directory = Path(directory).resolve()
    if not (directory / 'index.html').is_file():
        raise SystemExit(f'Build index.html missing: {directory}')
    @app.get('/charts')
    @app.get('/charts/')
    async def chart_page():
        return FileResponse(directory / 'index.html')
    app.mount('/', StaticFiles(directory=str(directory)), name='frontend')

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=NOTICE)
    parser.add_argument('--build', default=os.environ.get('CHART_FIXTURE_BUILD', str(ROOT / 'frontend' / 'build')))
    parser.add_argument('--watchlist-db', choices=[FIXTURE_DATABASE], default=None,
                        help='Persist watchlists only in the isolated allowlisted localhost Mongo database')
    args = parser.parse_args()
    configure_watchlist(args.watchlist_db)
    configure_static(args.build)
    import uvicorn
    print(NOTICE, flush=True)
    uvicorn.run(app, host='127.0.0.1', port=8002, log_level='warning')
