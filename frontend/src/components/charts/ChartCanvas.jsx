import { useEffect, useMemo, useRef, useState } from "react";
import { CandlestickSeries, createChart, CrosshairMode, HistogramSeries, LineSeries, LineStyle } from "lightweight-charts";
import { DrawingManager } from "lightweight-charts-drawing";
import { Crosshair, Minus, MoveVertical, ArrowUpRight, ArrowRight, ArrowUp, ArrowDown, Ruler, TrendingUp, Layers3, Trash2, Maximize, Minimize, ScanLine, Eye, EyeOff } from "lucide-react";
import { computeIndicators } from "../../lib/chartIndicators";
import { instrumentIdentity } from "../../lib/chartModel";
import { amountText, finiteValue, pluginDrawings, positionFresh, priceText, readDrawingEnvelope, saveDrawingEnvelope, timeText } from "./chartPresentation";
import ChartOrderLines from "./ChartOrderLines";
import ChartAnalysisOverlays from "./ChartAnalysisOverlays";
import { createRsiPaneLegend } from "./rsiPaneLegend";

import { candlePaint, chartInk, chartPaint } from "./chartTheme";
import { installDrawingTheme } from "./drawingTheme";

const TOOLS = [
  { id: null, label: "Select / crosshair", Icon: Crosshair },
  { id: "horizontal-line", label: "Horizontal line", Icon: Minus },
  { id: "vertical-line", label: "Vertical line", Icon: MoveVertical },
  { id: "trend-line", label: "Trend line", Icon: TrendingUp },
  { id: "ray", label: "Ray", Icon: ArrowUpRight },
  { id: "horizontal-ray", label: "Horizontal ray", Icon: ArrowRight },
  { id: "arrow", label: "Arrow", Icon: ArrowUpRight },
  { id: "arrow-mark-up", label: "Arrow up", Icon: ArrowUp },
  { id: "arrow-mark-down", label: "Arrow down", Icon: ArrowDown },
  { id: "date-and-price-range", label: "Ruler (price and time)", Icon: Ruler },
  { id: "fib-retracement", label: "Fibonacci retracement", Icon: Layers3 },
];
const ONE_CLICK_TOOLS = new Set(["horizontal-line", "horizontal-ray", "vertical-line", "arrow-mark-up", "arrow-mark-down"]);
const DRAWING_COLORS = ["#3b82f6", "#fbbf24", "#34d399", "#f472b6", "#f87171", "#ffffff"];
function restyleDrawing(drawing, patch) {
  return { ...drawing, style: { ...drawing.style, ...patch,
    ...(drawing.style?.levels ? { levels: drawing.style.levels.map(level => ({ ...level,
      ...(patch.color ? { color: patch.color } : {}), ...(patch.width ? { width: patch.width } : {}),
      ...(patch.lineStyle ? { style: patch.lineStyle } : {}) })) } : {}) } };
}
const chartTime = (time) => {
  const stamp = typeof time === "number" ? time : typeof time === "string" ? Date.parse(time) / 1000
    : Date.UTC(time.year, time.month - 1, time.day) / 1000;
  return timeText(stamp);
};

// Match a source time to the target candle interval without projecting beyond loaded data.
export function crosshairTarget(bars, time, timeframe) {
  if (typeof time !== "number") return bars.find(bar => bar.time === time) || null;
  const durations = { "1m": 60, "3m": 180, "5m": 300, "15m": 900, "30m": 1800, "1h": 3600, "1d": 86400, "1w": 604800 };
  const duration = durations[timeframe];
  if (!duration) return null;
  let lo = 0, hi = bars.length - 1, candidate = null;
  while (lo <= hi) { const mid = Math.floor((lo + hi) / 2); if (bars[mid].time <= time) { candidate = bars[mid]; lo = mid + 1; } else hi = mid - 1; }
  return candidate && time < candidate.time + duration ? candidate : null;
}

/** Owns the imperative chart. A keyed page instance separates every contract
 * and timeframe; durable drawings are shared only by full contract identity. */
export default function ChartCanvas({ instrument, timeframe, chartBackground = "default", bars, configs, volumeAvailable, positions,
  positionsAt, now, loading, loadingOlder, hasMore, loadOlder, error, tradingRows = [], onOrderChange, tradingDisabled = false, onRuntime, onCrosshair, showFullscreenControl = true, analysisData, analysisSettings, analysisSignals }) {
  const themeRef = useRef(chartBackground);
  themeRef.current = chartBackground;
  const host = useRef(null);
  const surface = useRef(null);
  const runtime = useRef(null);
  const syncCallbacks = useRef({ onRuntime, onCrosshair });
  syncCallbacks.current = { onRuntime, onCrosshair };
  const syncing = useRef(false);
  const latest = useRef({ bars, loadOlder, hasMore, loadingOlder });
  latest.current = { bars, loadOlder, hasMore, loadingOlder };
  const [hover, setHover] = useState(null);
  const [tool, setTool] = useState(null);
  const [selection, setSelection] = useState([]);
  const [defaultDrawingStyle, setDefaultDrawingStyle] = useState({ color: "#3b82f6", width: 2, lineStyle: "solid" });
  const drawingStyleRef = useRef(defaultDrawingStyle);
  drawingStyleRef.current = defaultDrawingStyle;
  const [selectedStyle, setSelectedStyle] = useState(null);
  const [hidden, setHidden] = useState(false);
  const [drawingCount, setDrawingCount] = useState(0);
  const [warning, setWarning] = useState("");
  const [renderError, setRenderError] = useState("");
  const [fullscreen, setFullscreen] = useState(false);
  const [orderRuntime, setOrderRuntime] = useState(null);
  const calculated = useMemo(() => {
    const daily = ["1d", "1w"].includes(timeframe);
    const output = computeIndicators(bars, daily ? configs.map((config) => config.id === "vwap" ? { ...config, enabled: false } : config) : configs, volumeAvailable);
    if (daily && configs.some((config) => config.id === "vwap" && config.enabled)) output.warnings.push("Session VWAP is unavailable on daily or weekly candles; choose an intraday timeframe.");
    return output;
  }, [bars, configs, volumeAvailable, timeframe]);
  const identity = JSON.stringify(instrumentIdentity(instrument));

  useEffect(() => {
    let chart;
    let manager;
    let resize;
    let resizeFallback;
    let disposed = false;
    const disposers = [];
    setRenderError("");
    try {
      const paint = chartPaint(themeRef.current);
      chart = createChart(host.current, {
        ...paint,
        width: host.current.clientWidth || 800, height: host.current.clientHeight || 540,
        layout: { ...paint.layout, fontFamily: "Inter, ui-sans-serif, system-ui", fontSize: 11, attributionLogo: true,
          panes: { ...paint.layout.panes, enableResize: true } },
        rightPriceScale: { ...paint.rightPriceScale, scaleMargins: { top: 0.12, bottom: 0.1 } },
        timeScale: { ...paint.timeScale, timeVisible: !["1d", "1w"].includes(timeframe), secondsVisible: false, rightOffset: 5,
          tickMarkFormatter: (time) => {
            if (typeof time !== "number") return chartTime(time);
            return new Intl.DateTimeFormat("en-IN", { timeZone: "Asia/Kolkata", ...(["1d", "1w"].includes(timeframe)
              ? { day: "2-digit", month: "short" } : { hour: "2-digit", minute: "2-digit", hour12: false }) }).format(time * 1000);
          } },
        localization: { locale: "en-IN", timeFormatter: chartTime, priceFormatter: (price) => priceText(price) },
        crosshair: { ...paint.crosshair, mode: CrosshairMode.Normal },
      });
      const candles = chart.addSeries(CandlestickSeries, { ...candlePaint(themeRef.current), borderVisible: false,
        priceFormat: { type: "price", precision: 2, minMove: Number(instrument.tick_size) || 0.01 } });
      manager = new DrawingManager(chart, candles, { bars: () => latest.current.bars, interval: timeframe,
        timeInfo: () => ({ timeZone: "Asia/Kolkata", intraday: !["1d", "1w"].includes(timeframe) }) });
      installDrawingTheme(manager, () => themeRef.current);
      let stored;
      try { stored = readDrawingEnvelope(window.localStorage, instrument); }
      catch { stored = { drawings: [], writable: false, warning: "Browser storage unavailable. Drawings remain in this tab only." }; }
      manager.importJSON(pluginDrawings(stored.drawings));
      setWarning(stored.warning);
      setDrawingCount(stored.drawings.length);
      disposers.push(manager.on("add", drawing => manager.update(restyleDrawing(drawing, drawingStyleRef.current))), manager.on("change", (drawings) => {
        if (disposed) return;
        setDrawingCount(drawings.length);
        if (stored.writable) {
          let result;
          try { result = saveDrawingEnvelope(window.localStorage, instrument, drawings); }
          catch { result = { warning: "Browser storage unavailable. Drawings remain in this tab only." }; }
          setWarning(result.warning);
        }
      }), manager.on("tool", setTool), manager.on("selection", (ids) => {
        setSelection([...ids]);
        setSelectedStyle(ids.length ? manager.get(ids[0])?.style || null : null);
      }), manager.on("visibility", (visible) => setHidden(!visible)));
      const cursor = { logical: null };
      const crosshair = (event) => {
        cursor.logical = event.logical ?? null;
        setHover(event.time ? event.seriesData.get(candles) || null : null);
        // Mouse leave has no sourceEvent. Clear sibling cursors too; the runtime
        // guard below prevents programmatic clears from bouncing between tiles.
        if (!syncing.current && (event.sourceEvent || event.time == null)) syncCallbacks.current.onCrosshair?.(event.time ?? null);
      };
      chart.subscribeCrosshairMove(crosshair);
      disposers.push(() => chart.unsubscribeCrosshairMove(crosshair));
      const rangeChanged = (range) => {
        if (range && range.from < 12 && latest.current.hasMore && !latest.current.loadingOlder) latest.current.loadOlder();
      };
      chart.timeScale().subscribeVisibleLogicalRangeChange(rangeChanged);
      disposers.push(() => chart.timeScale().unsubscribeVisibleLogicalRangeChange(rangeChanged));
      const fitSize = () => {
        if (!disposed && host.current) chart.resize(host.current.clientWidth || 800, host.current.clientHeight || 540);
      };
      if (typeof ResizeObserver !== "undefined") { resize = new ResizeObserver(fitSize); resize.observe(host.current); }
      else { resizeFallback = fitSize; window.addEventListener("resize", fitSize); }
      runtime.current = { chart, candles, manager, cursor, theme: themeRef.current, indicators: new Map(), priceLines: [], initialized: false, firstTime: null };
      setOrderRuntime({ chart, series: candles });
      syncCallbacks.current.onRuntime?.({ setCrosshair(time) {
        syncing.current = true;
        try {
          const bar = crosshairTarget(latest.current.bars, time, timeframe);
          // The library omits crosshair events for programmatically positioned cursors.
          cursor.logical = bar ? chart.timeScale().timeToIndex(bar.time) : null;
          if (bar) chart.setCrosshairPosition(bar.close, bar.time, candles);
          else chart.clearCrosshairPosition();
        } finally { syncing.current = false; }
      } });
    } catch {
      setRenderError("The chart could not start in this browser. Reload the page or use a current desktop browser.");
    }
    return () => {
      disposed = true;
      syncCallbacks.current.onRuntime?.(null);
      resize?.disconnect();
      if (resizeFallback) window.removeEventListener("resize", resizeFallback);
      disposers.forEach((dispose) => dispose());
      manager?.destroy();
      chart?.remove();
      runtime.current = null;
    };
    // The page keys this component by full instrument identity and timeframe.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [identity, timeframe]);

  useEffect(() => {
    const r = runtime.current;
    if (!r || r.theme === chartBackground) return;
    r.chart.applyOptions(chartPaint(chartBackground));
    r.candles.applyOptions(candlePaint(chartBackground));
    r.manager.redraw();
    r.theme = chartBackground;
  }, [chartBackground]);

  useEffect(() => {
    const r = runtime.current;
    if (!r) return;
    try {
      const visible = r.initialized && r.firstTime !== bars[0]?.time ? r.chart.timeScale().getVisibleRange() : null;
      r.candles.setData(bars);
      if (bars.length && !r.initialized) {
        r.chart.timeScale().setVisibleLogicalRange({ from: Math.max(0, bars.length - 100), to: bars.length + 5 });
        r.initialized = true;
      } else if (visible) r.chart.timeScale().setVisibleRange(visible);
      r.firstTime = bars[0]?.time;
      r.manager.redraw();
    } catch { setRenderError("The chart rejected candle data. Refresh history before relying on this view."); }
  }, [bars]);

  useEffect(() => {
    const r = runtime.current;
    if (!r) return;
    try {
      const wanted = new Set(calculated.series.map((item) => item.id));
      for (const [id, item] of r.indicators) {
        if (!wanted.has(id)) { r.chart.removeSeries(item.series); r.indicators.delete(id); }
      }
      const panes = [...new Set(calculated.series.filter((item) => item.pane !== "price").map((item) => item.pane))];
      for (const item of calculated.series) {
        const pane = item.pane === "price" ? 0 : panes.indexOf(item.pane) + 1;
        const ink = chartInk(item.color, chartBackground);
        let existing = r.indicators.get(item.id);
        if (!existing) {
          const series = r.chart.addSeries(item.type === "histogram" ? HistogramSeries : LineSeries, {
            color: ink, title: item.name, lineWidth: item.lineWidth || 1, priceLineVisible: false,
            lastValueVisible: item.pane === "price", crosshairMarkerVisible: item.type !== "histogram",
            ...(item.indicatorId === "volume" ? { priceFormat: { type: "volume" } } : {}),
          }, pane);
          existing = { series, pane: item.pane };
          if (item.indicatorId === "rsi") {
            existing.legend = createRsiPaneLegend(series, r.cursor);
            series.attachPrimitive(existing.legend);
          }
          r.indicators.set(item.id, existing);
        } else if (existing.series.getPane().paneIndex() !== pane) existing.series.moveToPane(pane);
        existing.series.applyOptions({ color: ink, title: item.name, ...(item.type === "line" ? { lineWidth: item.lineWidth || 1 } : {}) });
        const points = chartBackground === "white"
          ? item.data.map(point => point.color ? { ...point, color: chartInk(point.color, chartBackground) } : point)
          : item.data;
        existing.series.setData(points);
        existing.legend?.update(item, chartBackground);
      }
      r.chart.panes().forEach((pane, index) => pane.setStretchFactor(index === 0 ? 4 : 1));
    } catch { setRenderError("An indicator could not be rendered. Disable the indicator and refresh history."); }
  }, [calculated, chartBackground]);

  useEffect(() => {
    const r = runtime.current;
    if (!r) return;
    r.priceLines.forEach((line) => r.candles.removePriceLine(line));
    r.priceLines = [];
    for (const position of positions) {
      const fresh = positionFresh(position, positionsAt, now);
      const prefix = `${position.origin === "paper" ? "Paper" : "Broker"}${fresh ? "" : " · stale"}`;
      const levels = Array.isArray(position.protection_levels) && position.protection_levels.length
        ? position.protection_levels.filter((level) => ["stop", "target"].includes(level.kind)).map((level) => ({ price: level.price, label: `${level.kind === "stop" ? "SL" : "Target"}${level.quantity ? ` ×${level.quantity}` : ""}${level.label ? ` · ${level.label}` : ""}`, color: level.kind === "stop" ? "#fb7185" : "#5ee0b5" }))
        : [{ price: position.stop_price, label: "SL", color: "#fb7185" }, { price: position.target_price, label: "Target", color: "#5ee0b5" }];
      for (const { price, label, color } of [{ price: position.entry_price, label: "Entry", color: "#a5b4fc" }, ...levels]) {
        if (!finiteValue(price)) continue;
        r.priceLines.push(r.candles.createPriceLine({ price: Number(price), color: chartInk(color, chartBackground), lineWidth: 1,
          lineStyle: fresh ? LineStyle.Dashed : LineStyle.Dotted, axisLabelVisible: true, title: `${prefix} ${label}` }));
      }
    }
  }, [positions, positionsAt, now, chartBackground]);

  useEffect(() => {
    const changed = () => setFullscreen(document.fullscreenElement === surface.current);
    document.addEventListener("fullscreenchange", changed);
    return () => document.removeEventListener("fullscreenchange", changed);
  }, []);

  const shownBar = hover || bars[bars.length - 1];
  const drawingStyle = selection.length && selectedStyle ? selectedStyle : defaultDrawingStyle;
  const changeDrawingStyle = patch => {
    const manager = runtime.current?.manager;
    if (selection.length && manager) {
      selection.forEach(id => { const drawing = manager.get(id); if (drawing) manager.update(restyleDrawing(drawing, patch)); });
      setSelectedStyle(manager.get(selection[0])?.style || null);
    } else setDefaultDrawingStyle(style => ({ ...style, ...patch }));
  };
  const toggleFullscreen = async () => {
    try {
      if (document.fullscreenElement === surface.current) await document.exitFullscreen();
      else if (surface.current.requestFullscreen) await surface.current.requestFullscreen();
      else setWarning("Fullscreen is unavailable in this browser.");
    } catch { setWarning("Fullscreen could not be opened in this browser."); }
  };
  return <section className={chartBackground === "white" ? "chart-surface chart-surface-white" : "chart-surface"} ref={surface} aria-label={`${instrument.symbol} ${timeframe} candlestick chart`}>
    <div className="chart-canvas-toolbar">
      <div className="chart-tools" role="toolbar" aria-label="Drawing tools">
        {TOOLS.map(({ id, label, Icon }) => <button type="button" key={id || "cursor"} title={label} aria-label={label} aria-pressed={tool === id}
          className={tool === id ? "active" : ""} onClick={() => { runtime.current?.manager.select([]); runtime.current?.manager.setTool(id); }}><Icon size={16} /></button>)}
        <span className="chart-tool-divider" />
        <button type="button" title={hidden ? "Show drawings" : "Hide drawings"} aria-label={hidden ? "Show drawings" : "Hide drawings"} onClick={() => runtime.current?.manager.setDrawingsVisible(hidden)}>{hidden ? <EyeOff size={16} /> : <Eye size={16} />}</button>
        <button type="button" title="Delete selected drawing" aria-label="Delete selected drawing" disabled={!selection.length} onClick={() => selection.forEach((id) => runtime.current?.manager.remove(id))}><Trash2 size={15} /></button>
      </div>
      <span className="chart-drawing-hint">{tool ? `${ONE_CLICK_TOOLS.has(tool) ? "Click to place" : "Click start, then end"} · Esc to cancel` : `${drawingCount} saved drawing${drawingCount === 1 ? "" : "s"}`}</span>
      <div className="chart-tools chart-tools-end">
        <button type="button" onClick={() => runtime.current?.chart.timeScale().fitContent()} aria-label="Fit all candles" title="Fit all candles"><ScanLine size={16} /></button>
        {showFullscreenControl && <button type="button" onClick={toggleFullscreen} aria-label={fullscreen ? "Exit fullscreen" : "Fullscreen chart"} title="Fullscreen chart">{fullscreen ? <Minimize size={16} /> : <Maximize size={16} />}</button>}
      </div>
    </div>
    <div className="chart-drawing-style" role="group" aria-label="Drawing style">
      <span>{selection.length ? "Selected drawing" : "New drawings"}</span>
      <label>Color<input type="color" aria-label="Drawing color" value={/^#[0-9a-f]{6}$/i.test(drawingStyle.color || "") ? drawingStyle.color : "#3b82f6"} onChange={event => changeDrawingStyle({ color: event.target.value })} /></label>
      <div className="chart-color-swatches">{DRAWING_COLORS.map(color => <button key={color} type="button" style={{ backgroundColor: color }} aria-label={`Drawing color ${color}`} title={color} onClick={() => changeDrawingStyle({ color })} />)}</div>
      <label>Width<select aria-label="Drawing line width" value={drawingStyle.width || 2} onChange={event => changeDrawingStyle({ width: Number(event.target.value) })}>{[1, 2, 3, 4, 5, 6].map(width => <option key={width} value={width}>{width} px</option>)}</select></label>
      <label>Line<select aria-label="Drawing line style" value={drawingStyle.lineStyle || "solid"} onChange={event => changeDrawingStyle({ lineStyle: event.target.value })}><option value="solid">Solid</option><option value="dashed">Dashed</option><option value="dotted">Dotted</option></select></label>
    </div>
    <div className="chart-ohlc" aria-live="off">
      <span className="chart-legend-symbol">{instrument.symbol} <small>· {timeframe}</small></span>
      {shownBar ? <><span>{timeText(shownBar.time)} <small>IST</small></span>{["open", "high", "low", "close"].map((field) => <span key={field}><small>{field[0].toUpperCase()}</small> <b className={field === "close" ? shownBar.close >= shownBar.open ? "chart-positive" : "chart-negative" : ""}>{priceText(shownBar[field])}</b></span>)}</> : <span>OHLC unavailable</span>}
    </div>
    <div className="chart-canvas-wrap">
      <div ref={host} className="chart-canvas" data-testid="chart-canvas" />
      {orderRuntime && analysisData && <ChartAnalysisOverlays theme={chartBackground} {...orderRuntime} data={analysisData} settings={analysisSettings} signals={analysisSignals} bars={bars} />}
      {orderRuntime && onOrderChange && <ChartOrderLines {...orderRuntime} rows={tradingRows} tickSize={Number(instrument.tick_size) || .05} onRequestChange={onOrderChange} disabled={tradingDisabled} />}
      {!!positions.length && <div className="chart-position-overlay" aria-label="On-chart unrealized profit and loss">{positions.map((position) => {
        const fresh = positionFresh(position, positionsAt, now);
        return <span key={position.id} title={position.label || position.symbol} className={`chart-position-chip ${fresh ? "" : "stale"}`}>
          <b>{position.origin === "paper" ? "Paper" : "Broker"} ×{position.quantity}</b>
          <strong className={finiteValue(position.pnl) ? Number(position.pnl) >= 0 ? "chart-positive" : "chart-negative" : ""}>{amountText(position.pnl)}</strong>
          <small>{fresh ? "Unrealized" : "Stale · unrealized"}</small>
        </span>;
      })}</div>}
      {(loading || renderError || (!bars.length && !loading)) && <div className="chart-empty" role="status"><div className="chart-empty-icon"><TrendingUp size={28} /></div><strong>{renderError || (loading ? "Loading candle history…" : error ? "History unavailable" : "No candles for this instrument")}</strong><span>{loading ? "Fetching history and connecting the market feed" : "Try another timeframe or refresh after connecting Upstox."}</span></div>}
      {loadingOlder && <div className="chart-loading-older">Loading earlier candles…</div>}
    </div>
    {!!calculated.series.length && <div className="chart-indicator-legend">{calculated.series.map((item) => <span key={item.id}><i style={{ background: chartInk(item.color, chartBackground) }} />{item.name} <b>{priceText(item.data.filter((point) => finiteValue(point.value)).at(-1)?.value)}</b></span>)}</div>}
    {(warning || calculated.warnings.length > 0) && <div className="chart-inline-warning" role="status">{[warning, ...calculated.warnings].filter(Boolean).join(" · ")}</div>}
    <footer className="chart-canvas-footer"><span>All times IST · Drag to pan · Scroll to zoom</span><a href="https://www.tradingview.com/lightweight-charts/" target="_blank" rel="noreferrer">Charts by TradingView Lightweight Charts™</a></footer>
  </section>;
}
