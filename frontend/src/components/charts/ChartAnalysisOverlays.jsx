import { useLayoutEffect } from "react";
import { createSeriesMarkers } from "lightweight-charts";

const color = value => /^#[0-9a-f]{6}$/i.test(value || "") ? value : "#91a7e8";
/** Generated price/time regions. No hit testing, drawing persistence or actions. */
export class AnalysisZonePrimitive {
  constructor(zones) { this.zones = zones; this.view = { zOrder: () => "bottom", renderer: () => ({ draw: target => this.draw(target) }) }; }
  attached({ chart, series, requestUpdate }) { this.chart = chart; this.series = series; this.requestUpdate = requestUpdate; requestUpdate(); }
  detached() { this.chart = null; this.series = null; this.requestUpdate = null; }
  paneViews() { return [this.view]; }
  updateAllViews() {}
  draw(target) {
    if (!this.chart || !this.series) return;
    target.useBitmapCoordinateSpace(({ context, horizontalPixelRatio: hx, verticalPixelRatio: vy }) => {
      for (const zone of this.zones) {
        const x1 = this.chart.timeScale().timeToCoordinate(zone.start), x2 = this.chart.timeScale().timeToCoordinate(zone.end);
        const y1 = this.series.priceToCoordinate(zone.high), y2 = this.series.priceToCoordinate(zone.low);
        if ([x1, x2, y1, y2].some(value => value == null || !Number.isFinite(value))) continue;
        context.fillStyle = `${color(zone.color)}22`; context.strokeStyle = `${color(zone.color)}88`;
        const x = Math.min(x1, x2) * hx, y = Math.min(y1, y2) * vy;
        const width = Math.max(1, Math.abs(x2 - x1) * hx), height = Math.max(1, Math.abs(y2 - y1) * vy);
        context.fillRect(x, y, width, height); context.strokeRect(x, y, width, height);
      }
    });
  }
}

export function analysisVisuals(data, settings, signals, bars) {
  const groups = Object.entries(data?.overlays || {}).filter(([id, value]) => settings?.overlays?.[id] && value && typeof value === "object").map(([, value]) => value);
  const lines = groups.flatMap(group => group.lines || []).filter(line => Number.isFinite(line.price)).slice(0, 160);
  const zones = groups.flatMap(group => group.zones || []).filter(zone => [zone.start, zone.end, zone.low, zone.high].every(Number.isFinite) && zone.end >= zone.start && zone.high >= zone.low).slice(0, 240);
  const times = new Set(bars.map(bar => bar.time));
  const events = [...groups.flatMap(group => group.markers || []), ...(settings?.strategyId ? signals : [])];
  const unique = new Map();
  events.forEach(event => { if (typeof event.id === "string" && times.has(event.time)) unique.set(event.id, event); });
  const markers = [...unique.values()].sort((a, b) => a.time - b.time || a.id.localeCompare(b.id)).slice(-900).map(event => {
    const up = /up|bull|buy|long|ce/i.test(event.direction || "");
    return { time: event.time, position: up ? "belowBar" : "aboveBar", shape: up ? "arrowUp" : "arrowDown", color: up ? "#59d6ac" : "#f18fa4", text: String(event.text || "Research").slice(0, 70), id: event.id };
  });
  return { lines, zones, markers };
}

export default function ChartAnalysisOverlays({ chart, series, data, settings, signals = [], bars = [] }) {
  const barWindow = `${bars.length}|${bars[0]?.time}|${bars[bars.length - 1]?.time}`;
  useLayoutEffect(() => {
    if (!chart || !series || !data) return undefined;
    const visuals = analysisVisuals(data, settings, signals, bars);
    const primitive = new AnalysisZonePrimitive(visuals.zones);
    const priceLines = [];
    let markers;
    try {
      series.attachPrimitive(primitive);
      for (const line of visuals.lines) priceLines.push(series.createPriceLine({ price: line.price, title: String(line.label || "Research"), color: color(line.color), lineWidth: 1, lineStyle: 2, axisLabelVisible: false }));
      markers = createSeriesMarkers(series, visuals.markers);
    } catch { /* Failed research graphics must not stop candles or execution controls. */ }
    return () => { markers?.detach(); series.detachPrimitive(primitive); priceLines.forEach(line => series.removePriceLine(line)); };
    // Prices can tick without changing the loaded marker times. Keep this layer
    // attached across quote-only updates; layout cleanup precedes chart removal.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [chart, series, data, settings, signals, barWindow]);
  return null;
}
