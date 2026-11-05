"""Synthetic virtual-time chart research fixture (no production wiring).

Resume checkpoint: mount configure_research before the fixture catch-all; use the
same ResearchMarket for research history, chart history and stream bars. Mongo
is restricted to the existing allowlisted fixture database and chart_predictions.
"""
import copy
import math
import time
from datetime import datetime, timedelta

from app.chart_data import IST
from app.chart_research import ChartResearchService, PredictionJournal
from app.session_spec import session_spec
from app.routers import chart_research as router
from chart_watchlist_fixture import FIXTURE_DATABASE

NOTICE = 'SYNTHETIC RESEARCH — virtual future market time; no broker or real prices.'

class ResearchMarket:
    def __init__(self, instruments):
        self.instruments = {item['instrument_key']: dict(item) for item in instruments}
        day = datetime.now(IST).date() + timedelta(days=1)
        while not session_spec(day.isoformat(), 'spot').expected_candles:
            day += timedelta(days=1)
        start = datetime(day.year, day.month, day.day, 9, 15, tzinfo=IST).timestamp()
        self.start = int(start)
        self.now = self.start + 80 * 60 + 5
        self.rows = {}
        for key, instrument in self.instruments.items():
            base = 24000. if instrument['symbol'] == 'NIFTY' else 80000.
            bars = []
            for i in range(80):
                o = base + 5 * math.sin(i / 5)
                c = base + 5 * math.sin((i + 1) / 5)
                bars.append({'time': self.start + i * 60, 'open': o, 'high': max(o, c) + 1,
                             'low': min(o, c) - 1, 'close': c, 'volume': None})
            self.rows[key] = bars

    def supports(self, key, timeframe): return key in self.rows and timeframe == '1m'
    def forming(self, key):
        last = self.rows[key][-1]['close']
        return {'time': self.rows[key][-1]['time'] + 60, 'open': last, 'high': last + 1,
                'low': last - 1, 'close': last, 'volume': None}
    def advance(self):
        for key, rows in self.rows.items():
            prior_low = min(b['low'] for b in rows[-30:])
            previous = rows[-1]['close']
            close = max(previous, prior_low + 2)
            rows.append({'time': rows[-1]['time'] + 60, 'open': previous,
                         'high': max(previous, close) + 1, 'low': prior_low - 4,
                         'close': close, 'volume': None})
        self.now += 60
    async def resolve(self, key):
        if key not in self.instruments: raise ValueError('Unknown synthetic research instrument')
        return copy.deepcopy(self.instruments[key])
    async def history(self, key, timeframe, limit=600, before=None):
        if not self.supports(key, timeframe): raise ValueError('Synthetic research supports NIFTY and SENSEX 1m only')
        rows = self.rows[key] + [self.forming(key)]
        if before is not None: rows = [r for r in rows if r['time'] < before]
        return {'instrument': await self.resolve(key), 'timeframe': timeframe, 'bars': copy.deepcopy(rows[-limit:]),
                'source': 'synthetic_research_virtual_time', 'warnings': [NOTICE],
                'as_of_ms': int(self.now * 1000), 'requested_at_ms': int(self.now * 1000),
                'volume_available': False, 'has_more': False, 'next_before': None}

class Cursor:
    def __init__(self, docs): self.docs = docs
    def sort(self, key, order): self.docs.sort(key=lambda d: d.get(key, 0), reverse=order < 0); return self
    def limit(self, count): self.docs = self.docs[:count]; return self
    async def to_list(self, length): return copy.deepcopy(self.docs[:length])

class MemoryPredictions:
    """Minimal multi-document fixture adapter for the journal's exact Mongo calls."""
    def __init__(self): self.docs = {}
    async def create_index(self, fields): pass
    async def find_one_and_update(self, query, update, sort=None):
        cutoff = query['target_end']['$lte']
        for doc in sorted(self.docs.values(), key=lambda d: d['target_end']):
            if doc['status'] == 'pending' and doc['target_end'] <= cutoff and doc.get('next_score_check', 0) <= cutoff:
                previous = copy.deepcopy(doc); doc.update(update['$set']); return previous
        return None
    @staticmethod
    def matches(doc, query):
        for key, value in query.items():
            if isinstance(value, dict) and '$lte' in value:
                if doc.get(key, float('inf')) > value['$lte']: return False
            elif doc.get(key) != value: return False
        return True
    async def update_one(self, query, update, upsert=False):
        doc = self.docs.get(query.get('_id'))
        if isinstance(update, list):
            # Emulate the exact $replaceWith/$cond/$mergeObjects journal pipeline:
            # Mongo $$NOW uses real server wall-clock time, not the virtual clock.
            if doc is None and upsert:
                cond = update[0]['$replaceWith']['$cond']
                literal, dynamic = cond[1]['$mergeObjects']
                doc = copy.deepcopy(literal['$literal'])
                server_ms = int(time.time() * 1000)
                target_ms = dynamic['status']['$cond'][0]['$lt'][1]
                doc.update(persisted_at_ms=server_ms, status='pending' if server_ms < target_ms else 'late')
                self.docs[doc['_id']] = doc
        elif doc is not None and self.matches(doc, query):
            doc.update(copy.deepcopy(update.get('$set', {})))
    async def find_one(self, query):
        return next((copy.deepcopy(d) for d in self.docs.values() if self.matches(d, query)), None)
    def find(self, query): return Cursor([copy.deepcopy(d) for d in self.docs.values() if self.matches(d, query)])
    async def count_documents(self, query): return sum(self.matches(d, query) for d in self.docs.values())


class FixtureResearchService(ChartResearchService):
    async def observe(self, *args, **kwargs):
        value = await super().observe(*args, **kwargs)
        # Market bars and their horizon use the virtual exchange clock. Transport
        # freshness is measured in wall time, as for the other fixture streams.
        value['virtual_time_ms'] = value['as_of_ms']
        value['as_of_ms'] = int(time.time() * 1000)
        return value


def configure_research(app, instruments, database=None):
    market = ResearchMarket(instruments)
    if database is None:
        collection = MemoryPredictions()
    else:
        if database != FIXTURE_DATABASE: raise ValueError('Only the allowlisted fixture database is permitted')
        # Reuse the explicitly opened isolated client; never import app.db.
        collection = app.state.watchlist_client[FIXTURE_DATABASE].chart_predictions
    journal = PredictionJournal(collection, clock=lambda: market.now)
    research = FixtureResearchService(history=market, journal=journal, clock=lambda: market.now)
    app.state.research_market = market
    app.state.research_collection = collection
    app.state.research_service = research
    router.get_chart_research_service = lambda: research
    return market


def mount_research(app):
    app.include_router(router.api, prefix='/api/charts')
    @app.post('/api/fixture/research/advance')
    async def advance():
        market = app.state.research_market
        market.advance()
        return {'virtual_time': market.now, 'notice': NOTICE}
    @app.get('/api/fixture/research/state')
    async def state():
        market = app.state.research_market
        collection = app.state.research_collection
        return {'virtual_time': market.now, 'session_start': market.start, 'notice': NOTICE,
                'closed_bars': {k: len(v) for k, v in market.rows.items()},
                'pending': await collection.count_documents({'status': 'pending'}),
                'scored': await collection.count_documents({'status': 'scored'}),
                'storage': 'isolated_mongo' if app.state.watchlist_client else 'memory'}
