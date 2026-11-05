import asyncio
import copy
from datetime import datetime
from types import SimpleNamespace

import pytest

from app.chart_data import IST
from app.chart_research import (ChartResearchService, PredictionJournal, candle_end,
                                closed_bars, forecast_candidate, subject_key)

BASE = int(datetime(2026, 11, 5, 9, 15, tzinfo=IST).timestamp())
INSTRUMENT = {'instrument_key': 'NSE_INDEX|Nifty 50', 'symbol': 'NIFTY', 'kind': 'index'}


def bars(count=60):
    return [{'time': BASE + i * 60, 'open': 100 + i * .1, 'close': 100.08 + i * .1,
             'high': 100.2 + i * .1, 'low': 99.9 + i * .1, 'volume': None} for i in range(count)]


def matches(row, query):
    return all(row.get(k) <= v['$lte'] if isinstance(v, dict) else row.get(k) == v for k, v in query.items())


class Cursor:
    def __init__(self, rows): self.rows = copy.deepcopy(rows)
    def sort(self, field, direction): self.rows.sort(key=lambda r: r[field], reverse=direction < 0); return self
    def limit(self, amount): self.rows = self.rows[:amount]; return self
    async def to_list(self, length): return self.rows[:length]


class Collection:
    def __init__(self): self.rows = {}; self.on_write = None; self.server_now = (BASE + 3610) * 1000
    async def update_one(self, query, update, upsert=False):
        row = next((r for r in self.rows.values() if matches(r, query)), None)
        if isinstance(update, list):
            if row is None:
                doc = copy.deepcopy(update[0]['$replaceWith']['$cond'][1]['$mergeObjects'][0]['$literal'])
                doc['persisted_at_ms'] = self.server_now
                doc['status'] = 'pending' if self.server_now < doc['target_end'] * 1000 else 'late'
                self.rows[doc['_id']] = doc
            if self.on_write: self.on_write()
            return SimpleNamespace(upserted_id=None)
        inserted = None
        if row is None and upsert:
            row = copy.deepcopy(update['$setOnInsert']); self.rows[row['_id']] = row; inserted = row['_id']
        if row is not None: row.update(copy.deepcopy(update.get('$set', {})))
        if self.on_write: self.on_write()
        return SimpleNamespace(upserted_id=inserted)
    async def find_one(self, query): return copy.deepcopy(next((r for r in self.rows.values() if matches(r, query)), None))
    def find(self, query): return Cursor([r for r in self.rows.values() if matches(r, query)])
    async def count_documents(self, query): return sum(matches(r, query) for r in self.rows.values())


def test_closed_candles_reject_bad_input_and_ignore_forming_values():
    history = {'bars': bars(61), 'requested_at_ms': (BASE + 3610) * 1000}
    now = BASE + 60 * 60 + 10
    closed = closed_bars(history, '1m', now)
    assert len(closed) == 60
    history['bars'][-1]['close'] = 999999  # unfinished candle cannot leak into model
    assert closed_bars(history, '1m', now) == closed
    history['bars'][0]['low'] = 500
    with pytest.raises(ValueError, match='prices'): closed_bars(history, '1m', now)
    with pytest.raises(ValueError, match='chronological'): closed_bars({'bars': bars()[::-1], 'requested_at_ms': now * 1000}, '1m', now)
    with pytest.raises(ValueError, match='time'): closed_bars({'bars': [{'time': True}], 'requested_at_ms': now * 1000}, '1m', now)


def test_model_horizon_probability_and_causal_boundary():
    series = bars(); now = BASE + 3600 + 10
    result = forecast_candidate(series, '1m', now)
    assert result['status'] == 'candidate'
    assert result['source_time'] == BASE + 59 * 60
    assert result['target_time'] == BASE + 60 * 60 and result['target_end'] == BASE + 61 * 60
    assert sum(result['probabilities'].values()) == pytest.approx(1)
    assert result['zone_low'] <= result['predicted_close'] <= result['zone_high']
    assert result['confidence'] < 1 and result['sample_size'] == 56
    assert forecast_candidate(series, '1m', result['target_end'])['status'] == 'waiting'
    assert forecast_candidate(series, '1d', now)['status'] == 'unavailable'
    gap_series = [{**b, 'time': b['time'] + i * 60} for i, b in enumerate(series)]
    assert forecast_candidate(gap_series, '1m', gap_series[-1]['time'] + 65)['status'] == 'unavailable'
    after_close = [{**b, 'time': b['time'] + 6 * 3600} for b in series]
    assert forecast_candidate(after_close, '1m', after_close[-1]['time'] + 65)['status'] == 'waiting'


@pytest.mark.asyncio
async def test_immutable_dedup_and_scoring_match_independent_arithmetic():
    series = bars(); now = [BASE + 3610]; store = Collection()
    journal = PredictionJournal(store, clock=lambda: now[0])
    candidate = forecast_candidate(series, '1m', now[0]); subject = subject_key(INSTRUMENT, '1m')
    first = await journal.log(subject, candidate, INSTRUMENT, '1m')
    mutated = {**candidate, 'predicted_close': 1, 'baseline_close': 1}
    again = await journal.log(subject, mutated, INSTRUMENT, '1m')
    assert len(store.rows) == 1 and first == again
    target = {**series[-1], 'time': candidate['target_time'], 'close': candidate['baseline_close'] + 2}
    now[0] = candidate['target_end'] - 1
    await journal.score(subject, [target], '1m', now[0])
    assert (await journal.summary(subject))['evaluated'] == 0
    now[0] += 1
    await journal.score(subject, [target], '1m', now[0])
    scored = (await journal.summary(subject))
    assert scored['evaluated'] == 1 and scored['pending'] == 0
    assert scored['baseline_mae'] == 2
    assert scored['model_mae'] == pytest.approx(abs(target['close'] - first['predicted_close']))
    assert scored['baseline_brier'] == 2
    p = first['probabilities']
    assert scored['model_brier'] == pytest.approx((p['up'] - 1)**2 + p['flat']**2 + p['down']**2)
    await journal.score(subject, [{**target, 'close': 1}], '1m', now[0])
    assert await journal.summary(subject) == scored  # no retrospective score rewriting


@pytest.mark.asyncio
async def test_slow_storage_and_missing_target_do_not_certify_prediction():
    now = [BASE + 3610]; store = Collection(); journal = PredictionJournal(store, clock=lambda: now[0])
    candidate = forecast_candidate(bars(), '1m', now[0]); subject = subject_key(INSTRUMENT, '1m')
    store.on_write = lambda: now.__setitem__(0, candidate['target_end'])
    assert (await journal.log(subject, candidate, INSTRUMENT, '1m'))['status'] == 'waiting'
    assert list(store.rows.values())[0]['status'] == 'pending'  # server persisted in time; delayed acknowledgement isn't displayed
    assert (await journal.summary(subject))['evaluated'] == 0
    store.on_write = None; store.rows.clear(); now[0] = BASE + 3610
    await journal.log(subject, candidate, INSTRUMENT, '1m'); now[0] = BASE + 4000
    await journal.score(subject, [], '1m', now[0])
    assert (await journal.summary(subject))['pending'] == 1


@pytest.mark.asyncio
async def test_duplicate_ack_after_horizon_and_server_late_insert_are_withheld():
    now = [BASE + 3610]; store = Collection(); journal = PredictionJournal(store, clock=lambda: now[0])
    candidate = forecast_candidate(bars(), '1m', now[0]); subject = subject_key(INSTRUMENT, '1m')
    first = await journal.log(subject, candidate, INSTRUMENT, '1m')
    assert first['status'] == 'logged'
    store.on_write = lambda: now.__setitem__(0, candidate['target_end'] + 1)
    assert (await journal.log(subject, candidate, INSTRUMENT, '1m'))['status'] == 'waiting'
    assert list(store.rows.values())[0]['status'] == 'pending'
    store.rows.clear(); store.on_write = None; now[0] = BASE + 3610
    store.server_now = candidate['target_end'] * 1000
    assert (await journal.log(subject, candidate, INSTRUMENT, '1m'))['status'] == 'waiting'
    assert list(store.rows.values())[0]['status'] == 'late'


@pytest.mark.asyncio
async def test_service_uses_server_history_and_never_exposes_unlogged_forecast():
    now = BASE + 3610
    class History:
        async def history(self, *args, **kwargs): return {'instrument': INSTRUMENT, 'bars': bars(61), 'requested_at_ms': now * 1000}
    calls = []
    def analyze(closed, instrument, timeframe, strategy):
        calls.append(closed)
        return {'overlays': {}, 'strategy': None, 'warnings': []}
    class BrokenJournal:
        async def score(self, *args): raise RuntimeError('database offline')
    service = ChartResearchService(history=History(), journal=BrokenJournal(), clock=lambda: now, analyzer=analyze)
    result = await service.observe('ignored-client-key', '1m', forecast=True)
    assert len(calls[0]) == 60 and result['closed_through'] == bars()[-1]['time']
    assert result['prediction']['status'] == 'unavailable' and 'probabilities' not in result['prediction']
    await service.observe('ignored-client-key', '1m', forecast=False)
    assert len(calls) == 1  # immutable analysis cache reused


def test_expiry_separates_journals_and_daily_close_is_session_end():
    assert subject_key({**INSTRUMENT, 'expiry': '2026-11-05'}, '1m') != subject_key({**INSTRUMENT, 'expiry': '2026-11-12'}, '1m')
    assert candle_end(BASE, '1d') == int(datetime(2026, 11, 5, 15, 30, tzinfo=IST).timestamp())


def test_cached_forming_bar_cannot_mature_by_wall_clock_or_fetch_completion():
    history = {'bars': bars(61), 'requested_at_ms': (BASE + 3598) * 1000,
               'as_of_ms': (BASE + 3602) * 1000}
    # Fetch started before 10:14 closed, even though it completed after 10:15.
    assert len(closed_bars(history, '1m', BASE + 3610)) == 59
    history['requested_at_ms'] = (BASE + 3602) * 1000
    assert len(closed_bars(history, '1m', BASE + 3610)) == 60
    del history['requested_at_ms']
    with pytest.raises(ValueError, match='timestamp'): closed_bars(history, '1m', BASE + 3610)


def test_cas_and_segment_forecast_boundaries_follow_existing_session_spec():
    series = [{**b, 'time': b['time'] + 5 * 3600 + 60} for b in bars()]
    now = series[-1]['time'] + 65  # next spot candle is inside closing auction
    assert forecast_candidate(series, '1m', now, INSTRUMENT)['status'] == 'waiting'
    assert forecast_candidate(series, '1m', now, {'kind': 'option'})['status'] == 'candidate'
    option_stamp = int(datetime(2026, 11, 5, 15, 35, tzinfo=IST).timestamp())
    assert candle_end(option_stamp, '1m', {'kind': 'option'}) == option_stamp + 60
