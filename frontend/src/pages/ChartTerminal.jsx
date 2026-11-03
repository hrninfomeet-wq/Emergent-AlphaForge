import { useEffect, useMemo, useState } from "react";
import { Activity, ChevronLeft, RefreshCw, SlidersHorizontal, CandlestickChart } from "lucide-react";
import ChartCanvas from "../components/charts/ChartCanvas";
import ChartPositions from "../components/charts/ChartPositions";
import IndicatorSettings from "../components/charts/IndicatorSettings";
import { readIndicatorSettings, saveIndicatorSettings } from "../lib/chartIndicatorConfig";
import InstrumentSearch from "../components/charts/InstrumentSearch";
import useChartData from "../components/charts/useChartData";
import useChartTrading from "../components/charts/useChartTrading";
import ChartTrading from "../components/charts/ChartTrading";
import { markChartOrders } from "../lib/chartOrderMarks";
import { INDEX_SHORTCUTS, matchingPositions, priceText, readable, sessionQuote, streamFreshness, TIMEFRAMES, timeText } from "../components/charts/chartPresentation";
import { instrumentIdentity } from "../lib/chartModel";
import "../components/charts/chartTerminal.css";

export default function ChartTerminal() {
  const [instrument, setInstrument] = useState(INDEX_SHORTCUTS[0]);
  const [timeframe, setTimeframe] = useState("1m");
  const [storedIndicators] = useState(() => {
    try { return readIndicatorSettings(window.localStorage); }
    catch { return readIndicatorSettings(null); }
  });
  const [configs, setConfigs] = useState(storedIndicators.configs);
  const [indicatorWarning, setIndicatorWarning] = useState(storedIndicators.warning);
  useEffect(() => {
    if (!storedIndicators.writable) return;
    try { setIndicatorWarning(saveIndicatorSettings(window.localStorage, configs).warning); }
    catch { setIndicatorWarning("Indicator settings could not be saved. Changes stay in this tab."); }
  }, [configs, storedIndicators]);
  const [settings, setSettings] = useState(false);
  const [origin, setOrigin] = useState("all");
  const [showPositions, setShowPositions] = useState(true);
  const [now, setNow] = useState(Date.now);
  const data = useChartData(instrument, timeframe);
  const selected = data.instrument || instrument;
  const trading = useChartTrading(selected);
  const tradingRows = markChartOrders(trading.rows, data.snapshot, trading.asOf, now, trading.stale);
  const chartOrders = tradingRows.filter(row => row.instrument_key === selected.instrument_key && row.mode === trading.mode);
  const identity = `${JSON.stringify(instrumentIdentity(selected))}|${timeframe}`;
  useEffect(() => { const timer = setInterval(() => setNow(Date.now()), 1000); return () => clearInterval(timer); }, []);
  const positions = useMemo(() => matchingPositions(data.positions, selected, origin), [data.positions, selected, origin]);
  const freshness = streamFreshness(data.snapshot, data.transport, data.snapshotAt, now);
  const last = data.bars[data.bars.length - 1];
  const quote = sessionQuote(data.snapshot, selected.instrument_key, last, now);
  const activeIndicators = configs.filter((row) => row.enabled).length;
  const allWarnings = [...new Set([indicatorWarning, ...(data.warnings || []), ...(data.snapshot?.warnings || [])].filter(Boolean))];
  return <div className="chart-terminal" data-testid="chart-terminal">
    <header className="chart-page-heading"><div><div className="chart-eyebrow"><CandlestickChart size={14} /> MARKET WORKSPACE</div><h1>Charts</h1><p>Live context. Your analysis, in one place.</p></div><div className="chart-header-note"><Activity size={15} /><span>Upstox market data<br /><small>Broker positions stay separate</small></span></div></header>
    <div className="chart-command-bar"><InstrumentSearch onSelect={setInstrument} /><div className="chart-shortcuts" aria-label="Saved index shortcuts">{INDEX_SHORTCUTS.map((item) => <button type="button" key={item.symbol} className={instrument.instrument_key === item.instrument_key ? "active" : ""} onClick={() => setInstrument(item)}>{item.symbol}</button>)}</div></div>
    <section className="chart-workspace">
      <div className="chart-instrument-header"><div className="chart-instrument-title"><span className="chart-instrument-badge">{selected.exchange}</span><div><h2>{selected.symbol}</h2><p>{selected.name} · {readable(selected.kind)}{selected.expiry ? ` · ${selected.expiry}` : ""}</p></div></div>
        <div className="chart-instrument-price"><strong>{quote.price === null ? "—" : priceText(quote.price)}</strong><span aria-label="Session change" title={quote.title} className={quote.change > 0 ? "chart-positive" : quote.change < 0 ? "chart-negative" : ""}>{quote.text}</span></div>
        <div className={`chart-status ${freshness.tone}`}><i />{freshness.label}{freshness.age != null && <small>· {Math.floor(freshness.age / 1000)}s</small>}</div>
      </div>
      <div className="chart-timeframe-bar"><div className="chart-timeframes" role="group" aria-label="Candle timeframe">{TIMEFRAMES.map((value) => <button type="button" key={value} aria-pressed={timeframe === value} className={timeframe === value ? "active" : ""} onClick={() => setTimeframe(value)}>{value}</button>)}</div>
        <div className="chart-data-actions"><button type="button" className={settings ? "active" : ""} aria-expanded={settings} onClick={() => setSettings((value) => !value)}><SlidersHorizontal size={14} />Indicators <span className="chart-count">{activeIndicators}</span></button><button type="button" aria-label="Refresh chart history and positions" title="Refresh history and positions" disabled={data.loading || data.refreshing} onClick={data.refresh}><RefreshCw size={14} className={data.refreshing ? "chart-spin" : ""} /></button></div>
      </div>
      {settings && <IndicatorSettings configs={configs} setConfigs={setConfigs} volumeAvailable={data.volumeAvailable} timeframe={timeframe} onClose={() => setSettings(false)} />}
      {(data.error || allWarnings.length > 0) && <div className="chart-inline-warning" role="status">{[data.error, ...allWarnings].filter(Boolean).join(" · ")}</div>}
      <ChartCanvas key={identity} instrument={selected} timeframe={timeframe} bars={data.bars} configs={configs} volumeAvailable={data.volumeAvailable} positions={showPositions ? positions : []}
        tradingRows={chartOrders} onOrderChange={trading.requestChange} tradingDisabled={trading.stale || trading.busy}
        positionsAt={data.positionsAt} now={now} loading={data.loading} loadingOlder={data.loadingOlder} hasMore={data.hasMore} loadOlder={data.loadOlder} error={data.error} />
      <div className="chart-history-status"><button type="button" onClick={data.loadOlder} disabled={!data.hasMore || data.loadingOlder || data.loading}><ChevronLeft size={14} />{data.loadingOlder ? "Loading…" : "Load earlier"}</button><span>{data.bars.length.toLocaleString("en-IN")} candles · {readable(data.source)}{data.receivedAt ? ` · Reconciled ${timeText(data.receivedAt / 1000)} IST` : ""}</span><span className="chart-provisional">Tick candles provisional · REST reconciliation every 60s</span></div>
    </section>
    <ChartTrading instrument={selected} lastPrice={quote.price} trading={{ ...trading, rows: tradingRows }} />
    <ChartPositions positions={positions} positionsAt={data.positionsAt} now={now} warnings={data.positionWarnings} error={data.positionError} origin={origin} setOrigin={setOrigin} visible={showPositions} setVisible={setShowPositions} />
  </div>;
}
