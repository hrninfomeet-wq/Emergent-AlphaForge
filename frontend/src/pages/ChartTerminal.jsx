import { useCallback, useEffect, useRef, useState } from 'react';
import { Activity, CandlestickChart, Maximize2, Minimize2, PanelLeftClose, PanelLeftOpen } from 'lucide-react';
import ChartTile from '../components/charts/ChartTile';
import ChartWatchlist from '../components/charts/ChartWatchlist';
import ChartOptionChain from '../components/charts/ChartOptionChain';
import ChartPositions from '../components/charts/ChartPositions';
import InstrumentSearch from '../components/charts/InstrumentSearch';
import useChartTrading from '../components/charts/useChartTrading';
import ChartTrading from '../components/charts/ChartTrading';
import { readIndicatorSettings } from '../lib/chartIndicatorConfig';
import { markChartOrders } from '../lib/chartOrderMarks';
import { INDEX_SHORTCUTS, matchingPositions, sessionQuote } from '../components/charts/chartPresentation';
import { LAYOUTS, gridTemplate, readLayout, saveLayout } from '../lib/chartLayouts';
import '../components/charts/chartTerminal.css';
import '../components/charts/chartLayouts.css';

function VisibleTile({ children, area }) {
  // The selected layout is bounded to six charts. Keeping these mounted avoids
  // scroll-height jumps and lost crosshair state as a row leaves the viewport.
  return <div className="chart-tile-slot" style={{ gridArea: area }}>{children}</div>;
}

export default function ChartTerminal() {
  const terminal = useRef(null);
  const [fullscreen, setFullscreen] = useState(false);
  const [windowMaximized, setWindowMaximized] = useState(false);
  const [fullscreenError, setFullscreenError] = useState('');
  const [initial] = useState(() => { let indicators; try { indicators = readIndicatorSettings(window.localStorage).configs; } catch { indicators = undefined; } try { return readLayout(window.localStorage, indicators); } catch { return readLayout(null, indicators); } });
  const [state, setState] = useState(initial.state);
  const [warning, setWarning] = useState(initial.warning);
  const [pickerOpen, setPickerOpen] = useState(false);
  const [activeData, setActiveData] = useState(null);
  const [origin, setOrigin] = useState('all');
  const [showPositions, setShowPositions] = useState(true);
  const [chainInstrument, setChainInstrument] = useState(null);
  const [now, setNow] = useState(Date.now);
  const runtimes = useRef(new Map());
  useEffect(() => {
    const changed = () => setFullscreen(document.fullscreenElement === terminal.current);
    document.addEventListener('fullscreenchange', changed);
    return () => document.removeEventListener('fullscreenchange', changed);
  }, []);
  useEffect(() => {
    if (!windowMaximized) return undefined;
    const overflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    const escape = event => { if (event.key === 'Escape') setWindowMaximized(false); };
    document.addEventListener('keydown', escape);
    return () => { document.body.style.overflow = overflow; document.removeEventListener('keydown', escape); };
  }, [windowMaximized]);
  const maximized = fullscreen || windowMaximized;
  const normalWatchlistVisible = state.watchlistVisible !== false;
  const watchlistVisible = maximized ? (state.maximizedWatchlistVisible ?? normalWatchlistVisible) : normalWatchlistVisible;
  const drawerLabel = watchlistVisible ? 'Hide watchlist drawer' : 'Show watchlist drawer';
  const maximize = async () => {
    setFullscreenError('');
    if (windowMaximized) { setWindowMaximized(false); return; }
    try {
      if (document.fullscreenElement === terminal.current) await document.exitFullscreen();
      else if (terminal.current?.requestFullscreen) await terminal.current.requestFullscreen();
      else setWindowMaximized(true);
    } catch {
      if (document.fullscreenElement === terminal.current) setFullscreenError('Use Esc to leave full screen.');
      else setWindowMaximized(true);
    }
  };
  const layout = LAYOUTS.find(item => item.id === state.layout) || LAYOUTS[0];
  const activeTile = state.tiles[state.active];
  const validData = activeData && activeData.index === state.active && activeData.key === activeTile.instrument.instrument_key && activeData.timeframe === activeTile.timeframe ? activeData.data : null;
  const selected = validData?.instrument || activeTile.instrument;
  const trading = useChartTrading(selected);
  const tradingRows = markChartOrders(trading.rows, validData?.snapshot, trading.asOf, now, trading.stale);
  const positions = matchingPositions(validData?.positions, selected, origin);
  const quote = sessionQuote(validData?.snapshot, selected.instrument_key, validData?.bars?.[validData.bars.length - 1], now);
  useEffect(() => { const timer = setInterval(() => setNow(Date.now()), 1000); return () => clearInterval(timer); }, []);
  useEffect(() => { if (initial.writable) { try { setWarning(saveLayout(window.localStorage, state)); } catch { setWarning('Chart layout could not be saved. Changes stay in this tab.'); } } }, [state, initial.writable]);
  const receiveData = useCallback((index, key, timeframe, data) => setActiveData({ index, key, timeframe, data }), []);
  const changeTile = (index, patch) => setState(previous => ({ ...previous, tiles: previous.tiles.map((tile, i) => {
    if (i === index) return { ...tile, ...patch };
    if (patch.instrument && previous.syncSymbol) return { ...tile, instrument: patch.instrument };
    return tile;
  }) }));
  const selectLayout = id => setState(previous => { const next = LAYOUTS.find(item => item.id === id); return { ...previous, layout: id, active: Math.min(previous.active, next.count - 1) }; });
  const syncCrosshair = (index, time) => { if (state.syncCrosshair) runtimes.current.forEach((runtime, i) => { if (i !== index) runtime.setCrosshair(time); }); };
  return <div ref={terminal} className={`chart-terminal${maximized ? ' chart-terminal-maximized' : ''}${windowMaximized ? ' chart-terminal-window-maximized' : ''}`} data-testid="chart-terminal">
    <header className="chart-page-heading"><div><div className="chart-eyebrow"><CandlestickChart size={14} /> MARKET WORKSPACE</div><h1>Charts</h1><p>Live context. Your analysis, in one place.</p></div><div className="chart-header-note"><Activity size={15} /><span>Upstox market data<br /><small>Broker positions stay separate</small></span></div></header>
    <div className="chart-command-bar"><InstrumentSearch onSelect={instrument => changeTile(state.active, { instrument })} /><div className="chart-shortcuts" aria-label="Saved index shortcuts">{INDEX_SHORTCUTS.map(item => <button type="button" key={item.symbol} className={activeTile.instrument.instrument_key === item.instrument_key ? 'active' : ''} onClick={() => changeTile(state.active, { instrument: item })}>{item.symbol}</button>)}</div><button type="button" className="chart-open-chain" onClick={() => setChainInstrument(selected)}>Option chain</button><button type="button" className="chart-maximize-layout" aria-label={maximized ? 'Exit full screen' : 'Maximize chart layout'} title={maximized ? 'Exit full screen (Esc)' : 'Maximize selected layout — search stays available'} onClick={maximize}>{maximized ? <Minimize2 size={16} /> : <Maximize2 size={16} />}<span>{maximized ? 'Restore' : 'Maximize'}</span></button></div>
    <div className="chart-layout-controls">{maximized ? <button type="button" className="chart-watchlist-toggle chart-drawer-toggle" aria-label={drawerLabel} title={drawerLabel} aria-expanded={watchlistVisible}
      onClick={() => setState(previous => ({ ...previous, maximizedWatchlistVisible: !(previous.maximizedWatchlistVisible ?? (previous.watchlistVisible !== false)) }))}>
      {watchlistVisible ? <PanelLeftClose size={18} /> : <PanelLeftOpen size={18} />}
    </button> : <button type="button" className="chart-watchlist-toggle" aria-pressed={normalWatchlistVisible} onClick={() => setState(previous => ({...previous, watchlistVisible: previous.watchlistVisible === false}))}>Watchlist</button>}<div className="chart-layout-picker"><button type="button" aria-expanded={pickerOpen} aria-controls="chart-layout-menu" onClick={() => setPickerOpen(open => !open)}>Layout · {layout.count} {layout.count === 1 ? 'chart' : 'charts'} · {layout.label}</button>{pickerOpen && <div id="chart-layout-menu" className="chart-layout-menu" role="group" aria-label="Chart layouts">{[1, 2, 3, 4, 6].map(count => <div className="chart-layout-group" key={count}><strong>{count} {count === 1 ? 'chart' : 'charts'}</strong><div>{LAYOUTS.filter(item => item.count === count).map(item => <button type="button" key={item.id} aria-label={`${count} ${count === 1 ? 'chart' : 'charts'}: ${item.label}`} aria-pressed={item.id === state.layout} onClick={() => { selectLayout(item.id); setPickerOpen(false); }}><span className="chart-layout-preview" style={{ gridTemplateAreas: gridTemplate(item), gridTemplateColumns: `repeat(${item.areas[0].length}, 1fr)` }}>{Array.from({ length: item.count }, (_, i) => <i key={i} style={{ gridArea: `tile${i}` }} />)}</span><small>{item.label}</small></button>)}</div></div>)}</div>}</div><label><input type="checkbox" checked={state.syncSymbol} onChange={event => { const enabled = event.target.checked; setState(previous => ({ ...previous, syncSymbol: enabled, tiles: enabled ? previous.tiles.map(tile => ({ ...tile, instrument: previous.tiles[previous.active].instrument })) : previous.tiles })); }} /> Sync symbol</label><label><input type="checkbox" checked={state.syncCrosshair} onChange={event => setState(previous => ({ ...previous, syncCrosshair: event.target.checked }))} /> Sync crosshair</label><span>Active chart {state.active + 1} · search and trading apply here</span></div>
    {(warning || fullscreenError) && <div className="chart-inline-warning" role="status">{[warning, fullscreenError].filter(Boolean).join(' · ')}</div>}
    <div className="chart-terminal-body">{watchlistVisible && <ChartWatchlist activeInstrument={selected} onOptionChain={setChainInstrument} onSelect={instrument => changeTile(state.active, {instrument})} />}<div className="chart-terminal-main"><div className="chart-grid-scroll"><div className={`chart-layout-grid ${layout.count > 1 ? 'chart-layout-multi' : 'chart-layout-single'}`} style={{ gridTemplateAreas: gridTemplate(layout), gridTemplateColumns: `repeat(${layout.areas[0].length}, minmax(0, 1fr))`, minWidth: layout.count > 1 ? `max(100%, ${layout.areas[0].length * 380}px)` : undefined }}>
      {state.tiles.slice(0, layout.count).map((tile, index) => <VisibleTile key={index} active={state.active === index} area={`tile${index}`}><ChartTile tile={tile} index={index} active={state.active === index} onActivate={() => setState(previous => previous.active === index ? previous : { ...previous, active: index })} onChange={patch => changeTile(index, patch)} onData={receiveData} onRuntime={runtime => runtime ? runtimes.current.set(index, runtime) : runtimes.current.delete(index)} onCrosshair={time => syncCrosshair(index, time)} now={now} origin={origin} showPositions={showPositions} trading={trading} tradingRows={tradingRows} /></VisibleTile>)}
    </div></div>
    <ChartTrading instrument={selected} lastPrice={quote.price} trading={{ ...trading, rows: tradingRows }} />
    <ChartPositions positions={positions} positionsAt={validData?.positionsAt} now={now} warnings={validData?.positionWarnings || []} error={validData?.positionError || ''} origin={origin} setOrigin={setOrigin} visible={showPositions} setVisible={setShowPositions} />
    </div></div>
    {chainInstrument && <ChartOptionChain instrument={chainInstrument} onSelect={instrument => changeTile(state.active, {instrument})} onClose={() => setChainInstrument(null)} />}
  </div>;
}
