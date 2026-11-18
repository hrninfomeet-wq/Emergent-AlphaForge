import { chartInk, chartPalette } from "./chartTheme";
import { useLayoutEffect } from "react";
import { createSeriesMarkers } from "lightweight-charts";

const color = value => /^#[0-9a-f]{6}$/i.test(value || "") ? value : "#91a7e8";
/** Generated price/time regions. No hit testing, drawing persistence or actions. */
export class AnalysisZonePrimitive {
  constructor(zones, theme) {
    this.theme = theme;
    this.zones = zones;
    this.range = zones.filter(zone => zone.fullWidth);
    this.views = [
      { zOrder: () => "bottom", renderer: () => ({ draw: target => this.draw(target) }) },
      { zOrder: () => "top", renderer: () => ({ draw: target => this.drawLabels(target) }) },
    ];
  }
  attached({ chart, series, requestUpdate }) { this.chart = chart; this.series = series; this.requestUpdate = requestUpdate; requestUpdate(); }
  detached() { this.chart = null; this.series = null; this.requestUpdate = null; }
  paneViews() { return this.views; }
  autoscaleInfo() {
    if (!this.range.length) return null;
    return { priceRange: {
      minValue: Math.min(...this.range.map(zone => zone.low)),
      maxValue: Math.max(...this.range.map(zone => zone.high)),
    } };
  }
  updateAllViews() {}
  draw(target) {
    if (!this.chart || !this.series) return;
    target.useBitmapCoordinateSpace(({ context, bitmapSize, horizontalPixelRatio: hx, verticalPixelRatio: vy }) => {
      context.save();
      for (const zone of this.zones) {
        const x1 = zone.fullWidth ? 0 : this.chart.timeScale().timeToCoordinate(zone.start);
        const x2 = zone.fullWidth ? bitmapSize.width / hx : this.chart.timeScale().timeToCoordinate(zone.end);
        const y1 = this.series.priceToCoordinate(zone.high), y2 = this.series.priceToCoordinate(zone.low);
        if ([x1, x2, y1, y2].some(value => value == null || !Number.isFinite(value))) continue;
        const ink = chartInk(color(zone.color), this.theme);
        const alpha = zone.fullWidth ? "40" : this.theme === "white" ? "33" : "22";
        context.fillStyle = `${ink}${alpha}`; context.strokeStyle = `${ink}88`;
        const x = Math.min(x1, x2) * hx, y = Math.min(y1, y2) * vy;
        const width = Math.max(1, Math.abs(x2 - x1) * hx), height = Math.max(1, Math.abs(y2 - y1) * vy);
        context.fillRect(x, y, width, height); context.strokeRect(x, y, width, height);
      }
      context.restore();
    });
  }
  drawLabels(target) {
    if (!this.series || !this.range.length) return;
    const premium = this.range.find(zone => zone.label === "Premium");
    const discount = this.range.find(zone => zone.label === "Discount");
    if (!premium || !discount) return;
    const equilibrium = discount.high;
    const at = price => this.series.priceToCoordinate(price);
    const top = at(premium.high), middle = at(equilibrium), bottom = at(discount.low);
    if ([top, middle, bottom].some(value => value == null || !Number.isFinite(value))) return;
    target.useMediaCoordinateSpace(({ context, mediaSize }) => {
      const palette = chartPalette(this.theme);
      context.save();
      context.font = "600 11px Inter, system-ui, sans-serif";
      context.textBaseline = "middle";
      context.strokeStyle = this.theme === "white" ? "#475569" : "#bcc8dc";
      context.lineWidth = 1;
      context.setLineDash([5, 4]);
      context.beginPath();
      context.moveTo(0, middle);
      context.lineTo(mediaSize.width, middle);
      context.stroke();
      context.setLineDash([]);
      // Keep the labels apart when the range is small relative to the visible
      // candles. Bands and the equilibrium line always retain the true prices.
      const center = Math.max(32, Math.min(mediaSize.height - 32, middle));
      const labels = [
        ["Premium", Math.min(center - 21, (top + middle) / 2), premium.color],
        ["Equilibrium · 50%", center, palette.legendText],
        ["Discount", Math.max(center + 21, (middle + bottom) / 2), discount.color],
      ];
      for (const [text, coordinate, ink] of labels) {
        const y = Math.max(11, Math.min(mediaSize.height - 11, coordinate));
        const width = context.measureText(text).width + 14;
        // Price-axis titles extend into the right edge (for example EMA 20).
        const x = 12;
        context.fillStyle = palette.background;
        context.fillRect(x, y - 9, width, 18);
        context.fillStyle = chartInk(ink, this.theme);
        context.fillText(text, x + 7, y);
      }
      context.restore();
    });
  }
}

export function analysisVisuals(data, settings, signals, bars) {
  const groups = Object.entries(data?.overlays || {}).filter(([id, value]) => settings?.overlays?.[id] && value && typeof value === "object").map(([id, value]) => ({ ...value, id }));
  const lines = groups.flatMap(group => group.lines || []).filter(line => Number.isFinite(line.price)).slice(0, 160);
  const zones = groups.flatMap(group => (group.zones || []).map(zone => group.id === "premium_discount" ? { ...zone, fullWidth: true } : zone)).filter(zone => [zone.start, zone.end, zone.low, zone.high].every(Number.isFinite) && zone.end >= zone.start && zone.high >= zone.low).slice(0, 240);
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

export default function ChartAnalysisOverlays({ chart, series, data, settings, signals = [], bars = [], theme = "default" }) {
  const barWindow = `${bars.length}|${bars[0]?.time}|${bars[bars.length - 1]?.time}`;
  useLayoutEffect(() => {
    if (!chart || !series || !data) return undefined;
    const visuals = analysisVisuals(data, settings, signals, bars);
    const primitive = new AnalysisZonePrimitive(visuals.zones, theme);
    const priceLines = [];
    let markers;
    try {
      series.attachPrimitive(primitive);
      for (const line of visuals.lines) priceLines.push(series.createPriceLine({ price: line.price, title: String(line.label || "Research"), color: chartInk(color(line.color), theme), lineWidth: 1, lineStyle: 2, axisLabelVisible: false }));
      markers = createSeriesMarkers(series, visuals.markers.map(marker => ({ ...marker, color: chartInk(marker.color, theme) })));
    } catch { /* Failed research graphics must not stop candles or execution controls. */ }
    return () => { markers?.detach(); series.detachPrimitive(primitive); priceLines.forEach(line => series.removePriceLine(line)); };
    // Prices can tick without changing the loaded marker times. Keep this layer
    // attached across quote-only updates; layout cleanup precedes chart removal.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [chart, series, data, settings, signals, barWindow, theme]);
  return null;
}
