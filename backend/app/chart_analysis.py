"""Read-only analysis of already closed chart candles. No data or execution I/O."""
from __future__ import annotations

import math

import pandas as pd

from app.context_signals import recent_sr_levels, reversal_candle
from app.features.registry import materialize_features
from app.indicators import atr, detect_fvg, detect_swing_points, precompute_all_indicators, cas_window_mask
from app.chart_data import TIMEFRAME_SECONDS
from app.strategies.base import build_live_eval_ctx, validate_signal
from app.strategies.plugins.fibonacci_pullback import FibonacciPullback
from app.strategies.plugins.smc_liquidity_sweep_fvg import SMCLiquiditySweepFVG

_OVERLAYS = {
    'support_resistance': 'Support / resistance', 'fvg': 'Fair value gaps',
    'order_blocks': 'Order blocks', 'structure_breaks': 'Structure breaks',
    'change_of_character': 'Change of character',
    'displacement': 'Displacement', 'patterns': 'Rejection candles',
    'premium_discount': 'Premium / discount', 'volume_imbalance': 'Volume imbalance',
}
# Explicitly reviewed implementations. These do not consume volume, external
# data or session-precomputed ranges. Their inherited scope is checked below.
_STRATEGIES = {s.id: s for s in (FibonacciPullback(), SMCLiquiditySweepFVG())}
_UNAVAILABLE = {
    'premium_momentum': ('Premium momentum', 'Requires the premium session engine and option data.'),
    'atm_premium_flow_scalp': ('ATM premium flow scalp', 'Requires warehouse option-flow data.'),
    'sensex_vwap_mean_reversion': ('SENSEX VWAP mean reversion', 'Requires warehouse option-flow data.'),
}
_KEYS = {'NSE_INDEX|Nifty 50': 'NIFTY', 'NSE_INDEX|Nifty Bank': 'BANKNIFTY', 'BSE_INDEX|SENSEX': 'SENSEX'}


def _scope(instrument, timeframe, strategy):
    key = instrument.get('instrument_key') if isinstance(instrument, dict) else None
    symbol = _KEYS.get(key)
    if symbol is None or instrument.get('kind') != 'index':
        return 'Strategy supports only its declared index instruments.'
    if symbol not in strategy.supported_instruments:
        return 'Instrument is not supported by this strategy.'
    if timeframe not in strategy.supported_timeframes or timeframe != '1m':
        return 'Preview currently supports reviewed 1-minute strategy inputs only.'
    return None


def analysis_catalog(instrument, timeframe):
    overlays = [dict(id=k, label=v, available=(k != 'volume_imbalance' and timeframe in TIMEFRAME_SECONDS),
                     reason=('Requires order-flow data.' if k == 'volume_imbalance' else None))
                for k, v in _OVERLAYS.items()]
    strategies = []
    for s in _STRATEGIES.values():
        reason = _scope(instrument, timeframe, s)
        strategies.append(dict(id=s.id, name=s.name, available=reason is None, reason=reason))
    strategies.extend(dict(id=k, name=name, available=False, reason=reason)
                      for k, (name, reason) in _UNAVAILABLE.items())
    return {'overlays': overlays, 'strategies': strategies}


def _frame(bars):
    if not isinstance(bars, list) or len(bars) > 2000:
        raise ValueError('Invalid candle list')
    result = []
    last = -1
    for b in bars:
        if not isinstance(b, dict): raise ValueError('Invalid candle')
        t = b.get('time')
        if isinstance(t, bool) or not isinstance(t, int) or t <= last or t <= 0:
            raise ValueError('Candle times must be strictly increasing integer seconds')
        values = []
        for name in ('open', 'high', 'low', 'close'):
            v = b.get(name)
            if isinstance(v, bool) or not isinstance(v, (float, int)) or not math.isfinite(v) or v <= 0:
                raise ValueError('Invalid OHLC')
            values.append(float(v))
        o, h, l, c = values
        if l > min(o, c) or h < max(o, c) or l > h: raise ValueError('Invalid OHLC bounds')
        volume = b.get('volume')
        if volume is not None and (isinstance(volume, bool) or not isinstance(volume, (int, float)) or not math.isfinite(volume) or volume < 0):
            raise ValueError('Invalid volume')
        result.append({'ts': t * 1000, 'open': o, 'high': h, 'low': l, 'close': c,
                       'volume': float(volume) if volume is not None else float('nan')})
        last = t
    return pd.DataFrame(result, columns=['ts', 'open', 'high', 'low', 'close', 'volume'])


def _empty():
    return {'lines': [], 'zones': [], 'markers': [], 'warnings': []}


def _marker(kind, t, price, direction, text, reasons=None):
    return {'id': f'{kind}-{t}', 'time': int(t), 'price': float(price), 'direction': direction,
            'text': text, 'reasons': reasons or []}


def analyze_closed_bars(bars, instrument, timeframe, strategy_id=None):
    frame = _frame(bars)
    overlays = {k: _empty() for k in _OVERLAYS}
    overlays['volume_imbalance']['warnings'].append('Unavailable: order-flow data is not supplied by chart candles.')
    strategy = {'id': strategy_id, 'status': 'not_selected', 'reason': None, 'signals': []}
    warnings = []
    if strategy_id:
        strategy.update(status='unavailable', reason='Strategy is not in the reviewed candle-only preview allowlist.')
        if strategy_id in _UNAVAILABLE: strategy['reason'] = _UNAVAILABLE[strategy_id][1]
        elif strategy_id in _STRATEGIES: strategy['reason'] = _scope(instrument, timeframe, _STRATEGIES[strategy_id])
    if frame.empty:
        warnings.append('No closed candles available.')
        if strategy_id and strategy['reason'] is None: strategy['reason'] = 'Insufficient closed candles.'
        return {'overlays': overlays, 'strategy': strategy, 'warnings': warnings}
    segment = 'options' if instrument.get('kind') in ('option', 'future') else 'spot'
    if timeframe == '1m':
        enriched = precompute_all_indicators(frame, segment=segment)
    else:
        # The strategy enrichment pipeline assumes one-minute gap spacing.
        # Overlays need only these three causal inputs on the selected bars.
        step = TIMEFRAME_SECONDS[timeframe]
        if step < 86400:
            dates = pd.to_datetime(frame.ts, unit='ms', utc=True).dt.tz_convert('Asia/Kolkata').dt.date
            holes = frame.index[(dates == dates.shift()) & (frame.ts.diff() > step * 1000)]
            if len(holes):
                frame = frame.loc[holes[-1]:].copy()
                warnings.append('Structure restarts after the latest intraday data gap.')
            if segment == 'spot':
                frame = frame.loc[~cas_window_mask(frame)].copy()
        if frame.empty:
            warnings.append('No closed continuous-session candles available.')
            return {'overlays': overlays, 'strategy': strategy, 'warnings': warnings}
        enriched = detect_swing_points(frame)
        enriched['atr'] = atr(frame)
        enriched['fvg'] = detect_fvg(frame)
    enriched = materialize_features(enriched, {}, ['swing_levels', 'displacement', 'choch', 'fvg_zones', 'order_block', 'premium_discount'], {})
    last = enriched.iloc[-1]
    levels = recent_sr_levels(enriched, len(enriched)-1)
    for kind, color in [('support', '#22a67a'), ('resistance', '#dd6b60')]:
        for n, price in enumerate(levels[kind][-10:]):
            overlays['support_resistance']['lines'].append({'id': f'{kind}-{n}', 'price': price, 'label': kind, 'color': color})
    # Only the current carried zone is emitted; its first appearance is the
    # known confirmation time, never the earlier candle that supplied its bound.
    for oid, prefix, active, label, color in [('fvg','fvg','fvg_state','FVG','#4c8dcb'), ('order_blocks','ob','ob_active','Order block','#b082dc')]:
        is_active = last[active] == 'active' if oid == 'fvg' else bool(last[active])
        top, bottom = last[f'{prefix}_top'], last[f'{prefix}_bottom']
        if is_active and pd.notna(top) and pd.notna(bottom):
            sig = (top, bottom, last[f'{prefix}_dir'])
            start = len(enriched)-1
            while start > 0:
                prev = enriched.iloc[start-1]
                if (prev[f'{prefix}_top'], prev[f'{prefix}_bottom'], prev[f'{prefix}_dir']) != sig: break
                start -= 1
            overlays[oid]['zones'].append({'id': f'{oid}-{int(enriched.iloc[start].ts / 1000)}',
                'start': int(enriched.iloc[start].ts / 1000), 'end': int(last.ts / 1000),
                'low': float(bottom), 'high': float(top), 'label': label, 'color': color})
    for _, row in enriched.tail(100).iterrows():
        t = int(row.ts / 1000)
        pattern = reversal_candle(row)
        if pattern:
            overlays['patterns']['markers'].append(_marker('pattern', t, row.close, pattern, 'Rejection candle'))
        if row['displacement']:
            overlays['displacement']['markers'].append(_marker('displacement', t, row.close, 'BULLISH' if row.close > row.open else 'BEARISH', 'Displacement'))
        for col, direction in [('bos_up','BULLISH'), ('bos_down','BEARISH'), ('choch_up','BULLISH'), ('choch_down','BEARISH')]:
            concept, label = ('structure_breaks', 'Break of structure') if col.startswith('bos') else ('change_of_character', 'Change of character')
            if row[col]: overlays[concept]['markers'].append(_marker(col, t, row.close, direction, label))
    overlays['structure_breaks']['markers'] = overlays['structure_breaks']['markers'][-100:]
    hi, lo = last['last_swing_high_level'], last['last_swing_low_level']
    if pd.notna(hi) and pd.notna(lo) and hi > lo:
        # The pair is available only after both prior swing levels have been
        # shifted into the current bar. Scan back only while that pair was
        # already known; do not draw a range over earlier unknown candles.
        start = len(enriched) - 1
        while start > 0:
            prev = enriched.iloc[start - 1]
            if prev['last_swing_high_level'] != hi or prev['last_swing_low_level'] != lo:
                break
            start -= 1
        start_time = int(enriched.iloc[start].ts / 1000)
        end_time = int(last.ts / 1000)
        mid = (float(hi) + float(lo)) / 2.0
        overlays['premium_discount']['lines'].append(
            {'id': 'equilibrium', 'price': mid, 'label': 'Current equilibrium', 'color': '#9c9fc1'})
        for name, low, high, color in [('discount', float(lo), mid, '#22a67a'),
                                       ('premium', mid, float(hi), '#dd6b60')]:
            overlays['premium_discount']['zones'].append(
                {'id': f'{name}-{start_time}', 'start': start_time, 'end': end_time,
                 'low': low, 'high': high, 'label': name.title(), 'color': color})
        overlays['premium_discount']['warnings'].append(f"Current swing range: {last['range_state']} ({last['premium_discount_pct']:.1f}).")
    else:
        overlays['premium_discount']['warnings'].append('Swing range unavailable.')
    if strategy_id in _STRATEGIES and strategy['reason'] is None:
        # Reject recent intra-session holes and auction bars instead of claiming
        # preview parity with a partial or frozen price series.
        tail = enriched.tail(60)
        if len(tail) < 60 or bool(tail['gap_before'].any()):
            strategy['reason'] = 'Requires 60 closed candles without an intraday gap.'
        elif bool(cas_window_mask(enriched).iloc[-1]):
            strategy['reason'] = 'Preview is unavailable during the closing auction.'
        else:
            s = _STRATEGIES[strategy_id]; params = s.default_params(); i = len(enriched)-1
            ctx = build_live_eval_ctx(s, enriched, i, _KEYS[instrument['instrument_key']], params)
            signal = validate_signal(s.evaluate(last, enriched.iloc[i-1], params, ctx))
            strategy.update(status='ready', reason=None)
            if signal.direction != 'NONE' and not signal.blockers:
                strategy['signals'].append(_marker(strategy_id, int(last.ts / 1000), last.close, signal.direction,
                    s.name, signal.reasons))
            elif signal.blockers:
                strategy['reason'] = '; '.join(signal.blockers)
    return {'overlays': overlays, 'strategy': strategy, 'warnings': warnings}
