import { useEffect, useMemo, useState } from 'react';
import { ChevronLeft, RefreshCw, SlidersHorizontal } from 'lucide-react';
import ChartCanvas from './ChartCanvas';
import IndicatorSettings from './IndicatorSettings';
import useChartData from './useChartData';
import { matchingPositions, priceText, readable, sessionQuote, streamFreshness, TIMEFRAMES, timeText } from './chartPresentation';
import { instrumentIdentity } from '../../lib/chartModel';

export default function ChartTile({ tile, index, active, onActivate, onChange, onData, onRuntime, onCrosshair, now, origin, showPositions, trading, tradingRows }) {
  const data = useChartData(tile.instrument, tile.timeframe, { positionsEnabled: active });
  const [settings, setSettings] = useState(false);
  const [tools, setTools] = useState(false);
  const selected = data.instrument || tile.instrument;
  const identity = `${JSON.stringify(instrumentIdentity(selected))}|${tile.timeframe}`;
  const { instrument: dataInstrument, snapshot, bars, positions: dataPositions, positionsAt, positionWarnings, positionError } = data;
  const activePayload = useMemo(() => ({ instrument: dataInstrument, snapshot, bars, positions: dataPositions, positionsAt, positionWarnings, positionError }), [dataInstrument, snapshot, bars, dataPositions, positionsAt, positionWarnings, positionError]);
  useEffect(() => { if (active) onData(index, tile.instrument.instrument_key, tile.timeframe, activePayload); }, [active, index, tile.instrument.instrument_key, tile.timeframe, activePayload, onData]);
  const positions = useMemo(() => matchingPositions(data.positions, selected, origin), [data.positions, selected, origin]);
  const freshness = streamFreshness(data.snapshot, data.transport, data.snapshotAt, now);
  const quote = sessionQuote(data.snapshot, selected.instrument_key, data.bars[data.bars.length - 1], now);
  const warnings = [...new Set([...(data.warnings || []), ...(data.snapshot?.warnings || [])].filter(Boolean))];
  return <section className={`chart-workspace chart-tile ${active ? 'chart-tile-active' : ''} ${tools ? 'chart-tile-tools-open' : ''}`} onPointerDownCapture={onActivate} onFocusCapture={onActivate} aria-label={`Chart ${index + 1}: ${selected.symbol}`} data-testid={`chart-tile-${index}`}>
    <div className="chart-instrument-header"><div className="chart-instrument-title"><span className="chart-instrument-badge">{selected.exchange}</span><div><h2>{selected.symbol}</h2><p>{selected.name} · {readable(selected.kind)}{selected.expiry ? ` · ${selected.expiry}` : ''}</p></div></div>
      <div className="chart-instrument-price"><strong>{quote.price === null ? '—' : priceText(quote.price)}</strong><span aria-label="Session change" title={quote.title} className={quote.change > 0 ? 'chart-positive' : quote.change < 0 ? 'chart-negative' : ''}>{quote.text}</span></div>
      <div className={`chart-status ${freshness.tone}`}><i />{freshness.label}{freshness.age != null && <small>· {Math.floor(freshness.age / 1000)}s</small>}</div>
    </div>
    <div className="chart-timeframe-bar"><div className="chart-timeframes" role="group" aria-label={`Chart ${index + 1} timeframe`}>{TIMEFRAMES.map(value => <button type="button" key={value} aria-pressed={tile.timeframe === value} className={tile.timeframe === value ? 'active' : ''} onClick={() => onChange({ timeframe: value })}>{value}</button>)}</div>
      <div className="chart-data-actions"><button type="button" className="chart-compact-tools-toggle" aria-expanded={tools} onClick={() => setTools(value => !value)}>Tools</button><button type="button" className={settings ? 'active' : ''} aria-expanded={settings} onClick={() => setSettings(value => !value)}><SlidersHorizontal size={14} />Indicators <span className="chart-count">{tile.configs.filter(row => row.enabled).length}</span></button><button type="button" aria-label="Refresh chart history and positions" disabled={data.loading || data.refreshing} onClick={data.refresh}><RefreshCw size={14} className={data.refreshing ? 'chart-spin' : ''} /></button></div>
    </div>
    {settings && <IndicatorSettings configs={tile.configs} setConfigs={value => onChange({ configs: typeof value === 'function' ? value(tile.configs) : value })} volumeAvailable={data.volumeAvailable} timeframe={tile.timeframe} onClose={() => setSettings(false)} />}
    {(data.error || warnings.length > 0) && <div className="chart-inline-warning" role="status">{[data.error, ...warnings].filter(Boolean).join(' · ')}</div>}
    <ChartCanvas showFullscreenControl={false} key={identity} instrument={selected} timeframe={tile.timeframe} bars={data.bars} configs={tile.configs} volumeAvailable={data.volumeAvailable} positions={active && showPositions ? positions : []}
      tradingRows={active ? tradingRows.filter(row => row.instrument_key === selected.instrument_key && row.mode === trading.mode) : []} onOrderChange={active ? trading.requestChange : undefined} tradingDisabled={trading.stale || trading.busy}
      positionsAt={data.positionsAt} now={now} loading={data.loading} loadingOlder={data.loadingOlder} hasMore={data.hasMore} loadOlder={data.loadOlder} error={data.error} onRuntime={onRuntime} onCrosshair={onCrosshair} />
    <div className="chart-history-status"><button type="button" onClick={data.loadOlder} disabled={!data.hasMore || data.loadingOlder || data.loading}><ChevronLeft size={14} />{data.loadingOlder ? 'Loading…' : 'Load earlier'}</button><span>{data.bars.length.toLocaleString('en-IN')} candles · {readable(data.source)}{data.receivedAt ? ` · Reconciled ${timeText(data.receivedAt / 1000)} IST` : ''}</span><span className="chart-provisional">Tick candles provisional · REST reconciliation every 60s</span></div>
  </section>;
}
