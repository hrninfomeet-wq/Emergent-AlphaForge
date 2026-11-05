"""Chart-only research orchestration and immutable forward predictions.

No execution, deployment or broker imports. The only writes are prediction logs.
The model is an experimental conditional empirical distribution, not calibrated AI.
"""
import asyncio
import copy
import hashlib
import json
import math
import time
from collections import OrderedDict
from datetime import datetime
from functools import lru_cache

import numpy as np

from app.chart_data import IST, TIMEFRAME_SECONDS, get_chart_data_service
from app.session_spec import session_spec

MODEL = 'candle-analogues-v1'
INTRADAY = {'1m', '3m', '5m', '15m', '30m', '1h'}
_cached_spec = lru_cache(maxsize=2048)(session_spec)


def number(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        value = float(value)
    except (ValueError, TypeError, OverflowError):
        return None
    return value if math.isfinite(value) else None


def _session_bounds(stamp, instrument=None, continuous=False):
    day = datetime.fromtimestamp(stamp, IST)
    segment = 'options' if (instrument or {}).get('kind') in ('option', 'future') else 'spot'
    spec = _cached_spec(day.date().isoformat(), segment)
    midnight = day.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    close_min = spec.cas_start_min if continuous and spec.has_cas else spec.close_min
    return midnight + spec.open_min * 60, midnight + close_min * 60, spec.expected_candles > 0


def candle_end(stamp, timeframe, instrument=None):
    day = datetime.fromtimestamp(stamp, IST)
    _, close, _ = _session_bounds(stamp, instrument)
    if timeframe == '1w':
        return int(close + (4 - day.weekday()) * 86400)
    if timeframe == '1d':
        return int(close)
    return min(int(stamp + TIMEFRAME_SECONDS[timeframe]), int(close))


def closed_bars(history, timeframe, now):
    """Accept authoritative history only; discard unfinished/future bars before use."""
    if timeframe not in TIMEFRAME_SECONDS:
        raise ValueError('Unsupported analysis timeframe')
    requested_at = number(history.get('requested_at_ms'))
    if requested_at is None or requested_at <= 0 or requested_at > now * 1000 + 5000:
        raise ValueError('History request timestamp unavailable for closed-candle research')
    eligible_at = min(now, requested_at / 1000)
    result = []
    for raw in history.get('bars', [])[-600:]:
        stamp = number(raw.get('time')) if isinstance(raw, dict) else None
        if stamp is None or stamp <= 0 or not stamp.is_integer():
            raise ValueError('Invalid analysis candle time')
        instrument = history.get('instrument')
        if timeframe in INTRADAY:
            opened, closed, trading_day = _session_bounds(stamp, instrument)
            if not trading_day or not opened <= stamp < closed:
                continue
        end = candle_end(stamp, timeframe, instrument)
        if stamp > eligible_at or end > eligible_at:
            continue
        if end <= stamp:
            continue  # unusable out-of-session artifact, never a completed candle
        values = {key: number(raw.get(key)) for key in ('open', 'high', 'low', 'close')}
        if (any(v is None or v <= 0 for v in values.values()) or
                values['low'] > min(values['open'], values['close']) or
                values['high'] < max(values['open'], values['close'])):
            raise ValueError('Invalid analysis candle prices')
        volume = number(raw.get('volume'))
        result.append({'time': int(stamp), **values, 'volume': volume if volume is not None and volume >= 0 else None})
    if any(a['time'] >= b['time'] for a, b in zip(result, result[1:])):
        raise ValueError('Analysis candles must be unique and chronological')
    return result


def _state(bars, index):
    row = bars[index]
    body = np.sign(row['close'] - row['open'])
    momentum = np.sign(row['close'] - bars[max(0, index - 3)]['close'])
    return int(body), int(momentum)


def forecast_candidate(bars, timeframe, now, instrument=None):
    """Predict the next close using only already-observed adjacent-bar outcomes."""
    if timeframe not in INTRADAY or len(bars) < 45:
        return {'status': 'unavailable', 'reason': 'Forecast needs at least 45 closed intraday candles.'}
    last = bars[-1]
    step = TIMEFRAME_SECONDS[timeframe]
    target = last['time'] + step
    end = target + step
    session_open, session_close, trading_day = _session_bounds(target, instrument, continuous=True)
    if not trading_day or target < session_open or end > session_close or not target <= now < end:
        return {'status': 'waiting', 'reason': 'Waiting for a recent closed candle with an open next-candle horizon.'}
    if bars[-4]['time'] < session_open or any(b['time'] - a['time'] != step for a, b in zip(bars[-4:], bars[-3:])):
        return {'status': 'unavailable', 'reason': 'Recent momentum context crosses a session boundary or data gap.'}
    samples = []
    wanted = _state(bars, len(bars) - 1)
    for i in range(3, len(bars) - 1):
        a, b = bars[i:i + 2]
        opened, closed, trading_day = _session_bounds(a['time'], instrument, continuous=True)
        if not trading_day or bars[i - 3]['time'] < opened or b['time'] + step > closed:
            continue
        if b['time'] != a['time'] + step:
            continue
        # Do not train on overnight transitions or momentum crossing data gaps.
        if any(bars[j + 1]['time'] != bars[j]['time'] + step for j in range(i - 3, i)):
            continue
        if datetime.fromtimestamp(a['time'], IST).date() != datetime.fromtimestamp(b['time'], IST).date():
            continue
        samples.append((_state(bars, i), b['close'] / a['close'] - 1))
    matched = [value for state, value in samples if state == wanted]
    conditional = len(matched) >= 40
    values = np.asarray((matched if conditional else [value for _, value in samples])[-400:], dtype=float)
    if len(values) < 40:
        return {'status': 'unavailable', 'reason': 'Fewer than 40 usable adjacent-candle observations.'}
    recent_ranges = [b['high'] - b['low'] for b in bars[-14:]]
    flat_points = max(last['close'] * .0002, float(np.median(recent_ranges)) * .1)
    flat_return = flat_points / last['close']
    counts = {'up': int(np.sum(values > flat_return)), 'flat': int(np.sum(np.abs(values) <= flat_return)),
              'down': int(np.sum(values < -flat_return))}
    # Laplace smoothing avoids zero/one probability from a small sample.
    probabilities = {key: (count + 1) / (len(values) + 3) for key, count in counts.items()}
    low, median, high = np.quantile(values, [.1, .5, .9])
    return {'status': 'candidate', 'model': MODEL, 'source_time': last['time'], 'target_time': target,
            'target_end': end, 'created_at_ms': int(now * 1000), 'baseline_close': last['close'],
            'predicted_close': last['close'] * (1 + float(median)),
            'zone_low': last['close'] * (1 + float(low)), 'zone_high': last['close'] * (1 + float(high)),
            'probabilities': probabilities, 'confidence': max(probabilities.values()),
            'sample_size': len(values), 'flat_threshold_points': flat_points,
            'reasons': [f"{len(values)} completed transitions {'with matching candle direction and momentum' if conditional else 'pooled because matching observations were sparse'}.",
                        'Zone is the empirical 10th–90th percentile for the next close, not its high/low.',
                        'Experimental statistical model; probabilities are not calibrated accuracy.']}


def subject_key(instrument, timeframe):
    identity = {key: instrument.get(key) for key in ('instrument_key', 'expiry', 'strike', 'option_type')}
    return hashlib.sha256(json.dumps([identity, timeframe, MODEL], sort_keys=True).encode()).hexdigest()


class PredictionJournal:
    def __init__(self, collection, clock=time.time):
        self.collection, self.clock = collection, clock
        self.indexed = False
        self.index_lock = asyncio.Lock()

    async def ensure_indexes(self):
        if self.indexed: return
        async with self.index_lock:
            if not self.indexed:
                await self.collection.create_index([('subject', 1), ('status', 1), ('target_time', -1)])
                await self.collection.create_index([('status', 1), ('model', 1), ('target_end', 1)])
                self.indexed = True

    async def log(self, subject, candidate, instrument, timeframe):
        await self.ensure_indexes()
        if candidate.get('status') != 'candidate' or self.clock() >= candidate['target_end']:
            return {'status': 'waiting', 'reason': 'Prediction horizon closed before logging.'}
        identity = f"{subject}:{candidate['source_time']}"
        doc = {**copy.deepcopy(candidate), '_id': identity, 'subject': subject,
               'instrument_key': instrument['instrument_key'], 'timeframe': timeframe, 'status': 'pending'}
        # Mongo's server timestamp certifies persistence atomically. Duplicate
        # requests preserve the original record, even if acknowledgements race.
        await self.collection.update_one({'_id': identity}, [{'$replaceWith': {'$cond': [
            {'$eq': [{'$type': '$subject'}, 'missing']},
            {'$mergeObjects': [{'$literal': doc}, {
                'persisted_at_ms': {'$toLong': '$$NOW'},
                'status': {'$cond': [{'$lt': [{'$toLong': '$$NOW'}, candidate['target_end'] * 1000]}, 'pending', 'late']}
            }]}, '$$ROOT'
        ]}}], upsert=True)
        saved = await self.collection.find_one({'_id': identity})
        if (self.clock() >= candidate['target_end'] or not saved or saved['status'] != 'pending' or
                saved.get('persisted_at_ms', math.inf) >= candidate['target_end'] * 1000):
            return {'status': 'waiting', 'reason': 'Prediction was not durably logged before the horizon closed.'}
        return {key: value for key, value in {**saved, 'id': identity, 'status': 'logged'}.items() if key not in ('_id', 'subject')}

    async def score(self, subject, bars, timeframe, now):
        by_time = {bar['time']: bar for bar in bars}
        pending = await self.collection.find({'subject': subject, 'status': 'pending', 'target_end': {'$lte': now}}).sort('target_time', -1).limit(500).to_list(length=500)
        for prediction in pending:
            actual = by_time.get(prediction['target_time'])
            if actual is None or candle_end(actual['time'], timeframe) > now:
                continue
            delta = actual['close'] - prediction['baseline_close']
            outcome = 'flat' if abs(delta) <= prediction['flat_threshold_points'] else 'up' if delta > 0 else 'down'
            model_brier = sum((prediction['probabilities'][label] - int(label == outcome)) ** 2 for label in ('up', 'flat', 'down'))
            baseline_brier = sum((int(label == 'flat') - int(label == outcome)) ** 2 for label in ('up', 'flat', 'down'))
            score = {'actual_close': actual['close'], 'outcome': outcome,
                     'model_error': abs(actual['close'] - prediction['predicted_close']),
                     'baseline_error': abs(delta), 'model_brier': model_brier, 'baseline_brier': baseline_brier,
                     'covered': prediction['zone_low'] <= actual['close'] <= prediction['zone_high'],
                     'scored_at_ms': int(now * 1000)}
            await self.collection.update_one({'_id': prediction['_id'], 'status': 'pending'}, {'$set': {'status': 'scored', 'score': score}})

    async def summary(self, subject):
        docs = await self.collection.find({'subject': subject, 'status': 'scored'}).sort('target_time', -1).limit(500).to_list(length=500)
        count = len(docs)
        pending = await self.collection.count_documents({'subject': subject, 'status': 'pending'})
        mean = lambda field: sum(row['score'][field] for row in docs) / count if count else None
        return {'evaluated': count, 'pending': pending, 'model_mae': mean('model_error'),
                'baseline_mae': mean('baseline_error'), 'model_brier': mean('model_brier'),
                'baseline_brier': mean('baseline_brier'), 'coverage': mean('covered'), 'window': 'latest 500 scored predictions'}

    async def claim_due(self, now):
        # Short leases recover on restart and bound retries across workers.
        return await self.collection.find_one_and_update({
            'model': MODEL, 'status': 'pending', 'target_end': {'$lte': now},
            '$or': [{'next_score_check': {'$exists': False}}, {'next_score_check': {'$lte': now}}],
        }, {'$set': {'next_score_check': now + 300}}, sort=[('target_end', 1)])


class ChartResearchService:
    def __init__(self, *, history=None, journal=None, clock=time.time, analyzer=None):
        self.history = history or get_chart_data_service()
        self.journal = journal
        self.clock = clock
        self.analyzer = analyzer
        self.cache = OrderedDict()
        self.lock = asyncio.Lock()

    async def catalog(self, key, timeframe):
        from app.chart_analysis import analysis_catalog
        if timeframe not in TIMEFRAME_SECONDS:
            raise ValueError('Unsupported analysis timeframe')
        instrument = await self.history.resolve(key)
        return {'instrument': instrument, 'timeframe': timeframe, **analysis_catalog(instrument, timeframe)}

    async def settle_due(self, limit=2):
        """Score logged predictions even after a tab closes or the server restarts."""
        for _ in range(limit):
            now = self.clock()
            doc = await self.journal.claim_due(now)
            if not doc: break
            try:
                tf = doc['timeframe']
                history = await self.history.history(doc['instrument_key'], tf, before=doc['target_end'], limit=3)
                if subject_key(history['instrument'], tf) != doc['subject']:
                    continue  # never settle on a recycled contract key
                await self.journal.score(doc['subject'], closed_bars(history, tf, self.clock()), tf, self.clock())
            except (ValueError, RuntimeError):
                continue  # stale/absent data stays pending; no fabricated outcome

    async def observe(self, key, timeframe, strategy_id=None, forecast=False):
        from app.chart_analysis import analyze_closed_bars
        history = await self.history.history(key, timeframe, limit=600)
        instrument = history['instrument']
        now = self.clock()
        bars = closed_bars(history, timeframe, now)
        # Includes candle values so a REST revision does not masquerade as new analysis.
        fingerprint = hashlib.sha256(json.dumps([instrument, timeframe, bars, strategy_id], sort_keys=True).encode()).hexdigest()
        async with self.lock:
            result = self.cache.get(fingerprint)
            if result is None:
                result = await asyncio.to_thread(self.analyzer or analyze_closed_bars, bars, instrument, timeframe, strategy_id)
                self.cache[fingerprint] = result
                while len(self.cache) > 48:
                    self.cache.popitem(last=False)
            else:
                self.cache.move_to_end(fingerprint)
        response = {**copy.deepcopy(result), 'instrument': instrument, 'timeframe': timeframe,
                    'closed_through': bars[-1]['time'] if bars else None, 'as_of_ms': int(now * 1000),
                    'prediction': None, 'scores': None}
        response['warnings'] = list(dict.fromkeys([*history.get('warnings', []), *response.get('warnings', [])]))
        if forecast:
            if self.journal is None:
                response['prediction'] = {'status': 'unavailable', 'reason': 'Prediction logging is unavailable.'}
            else:
                subject = subject_key(instrument, timeframe)
                try:
                    await self.journal.score(subject, bars, timeframe, now)
                    candidate = forecast_candidate(bars, timeframe, self.clock(), instrument)
                    response['prediction'] = await self.journal.log(subject, candidate, instrument, timeframe) if candidate['status'] == 'candidate' else candidate
                    response['scores'] = await self.journal.summary(subject)
                except Exception:
                    response['prediction'] = {'status': 'unavailable', 'reason': 'Prediction log or scoring is unavailable; no forecast is certified as logged.'}
                    response['warnings'].append('Research logging failed. Existing predictions are retained.')
        return response


_service = None


def get_chart_research_service():
    global _service
    if _service is None:
        from app.db import get_db
        _service = ChartResearchService(journal=PredictionJournal(get_db().chart_predictions))
    return _service
