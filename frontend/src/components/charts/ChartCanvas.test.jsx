import React, { act } from "react";
import { createRoot } from "react-dom/client";
import ChartCanvas from "./ChartCanvas";
import { INDEX_SHORTCUTS, readDrawingEnvelope, saveDrawingEnvelope } from "./chartPresentation";

const mockCharts = [], mockManagers = [];
jest.mock("lightweight-charts", () => ({
  ColorType: { Solid: "solid" }, CrosshairMode: { Normal: 0 }, LineStyle: { Dashed: 2, Dotted: 1 },
  CandlestickSeries: "candle", HistogramSeries: "histogram", LineSeries: "line",
  createChart: () => {
    const pane = { setStretchFactor: jest.fn() };
    const scale = { subscribeVisibleLogicalRangeChange: jest.fn(), unsubscribeVisibleLogicalRangeChange: jest.fn(), setVisibleLogicalRange: jest.fn(), getVisibleRange: jest.fn(() => null), setVisibleRange: jest.fn(), fitContent: jest.fn() };
    const chart = { series: [], remove: jest.fn(), resize: jest.fn(), subscribeCrosshairMove: jest.fn(), unsubscribeCrosshairMove: jest.fn(), timeScale: () => scale, panes: () => [pane], removeSeries: jest.fn(),
      addSeries: jest.fn((kind, options, index = 0) => { const series = { kind, options, index, applyOptions: jest.fn(), setData: jest.fn(), createPriceLine: jest.fn((line) => line), removePriceLine: jest.fn(), getPane: () => ({ paneIndex: () => series.index }), moveToPane: jest.fn((next) => { series.index = next; }) }; chart.series.push(series); return series; }) };
    mockCharts.push(chart); return chart;
  },
}));
jest.mock("lightweight-charts-drawing", () => ({ DrawingManager: class {
  constructor() { this.listeners = {}; this.items = []; this.importJSON = jest.fn(rows => { this.items = rows; }); this.redraw = jest.fn(); this.destroy = jest.fn(); this.remove = jest.fn(); this.setTool = jest.fn((tool) => this.listeners.tool?.(tool)); this.setDrawingsVisible = jest.fn(); this.select = jest.fn(ids => this.listeners.selection?.(ids)); this.get = id => this.items.find(row => row.id === id); this.update = jest.fn(drawing => { this.items = this.items.map(row => row.id === drawing.id ? drawing : row); this.listeners.change?.(this.items); }); mockManagers.push(this); }
  on(name, callback) { this.listeners[name] = callback; return () => { delete this.listeners[name]; }; }
} }));
let container, root;
const bars = Array.from({ length: 40 }, (_, index) => ({ time: 100 + index * 60, open: 100 + index, high: 102 + index, low: 99 + index, close: 101 + index, volume: 10 }));
const initial = { instrument: INDEX_SHORTCUTS[0], timeframe: "1m", bars, configs: [], volumeAvailable: false, positions: [], positionsAt: 100000, now: 100000, loading: false, loadingOlder: false, hasMore: false, loadOlder: jest.fn(), error: "" };
beforeEach(() => { globalThis.IS_REACT_ACT_ENVIRONMENT = true; mockCharts.length = 0; mockManagers.length = 0; localStorage.clear(); container = document.createElement("div"); document.body.appendChild(container); root = createRoot(container); });
afterEach(async () => { await act(async () => root.unmount()); container.remove(); });
const render = async (overrides = {}) => act(async () => { root.render(<ChartCanvas {...initial} {...overrides} />); });

test("semantic drawing changes save to the selected contract and restore after remount", async () => {
  const drawing = { id: "test-line", kind: "horizontal-line", points: [{ time: 100, price: 120 }], style: { color: "#abc", width: 1 } };
  saveDrawingEnvelope(localStorage, initial.instrument, [drawing]); await render();
  expect(mockManagers[0].importJSON).toHaveBeenCalledWith([expect.objectContaining({ id: "test-line", kind: "horizontal-line", points: drawing.points })]);
  await act(async () => mockManagers[0].listeners.change([{ ...drawing, points: [{ time: 100, price: 130 }] }]));
  expect(readDrawingEnvelope(localStorage, initial.instrument).drawings[0].anchors[0].price).toBe(130);
  await render({ instrument: INDEX_SHORTCUTS[1] });
  expect(mockManagers[0].destroy).toHaveBeenCalledTimes(1); expect(mockCharts[0].remove).toHaveBeenCalledTimes(1);
  expect(mockManagers[1].importJSON).toHaveBeenCalledWith([]);
});

test("position protection slices create separate read-only price lines and replace summary stop/target", async () => {
  await render({ positions: [{ id: "position", origin: "broker", fresh: true, quantity: 30, pnl: 500, entry_price: 110, stop_price: 999, target_price: 888,
    protection_levels: [{ id: "one", kind: "stop", price: 100, quantity: 10 }, { id: "two", kind: "stop", price: 104, quantity: 20 }, { id: "target", kind: "target", price: 150 }] }] });
  const prices = mockCharts[0].series[0].createPriceLine.mock.calls.map(([line]) => line.price);
  expect(prices).toEqual([110, 100, 104, 150]);
  expect(container.querySelector('[aria-label="On-chart unrealized profit and loss"]').textContent).toContain("+₹500.00");
  expect(container.textContent).not.toMatch(/Place order|Buy|Sell/);
});

test("indicator panes render separately; daily VWAP remains unavailable", async () => {
  await render({ volumeAvailable: true, timeframe: "1d", configs: [{ id: "ema", enabled: true, params: { period: 20 } }, { id: "rsi", enabled: true, params: { period: 14 } }, { id: "vwap", enabled: true, params: {} }] });
  expect(mockCharts[0].series.filter((item) => item.kind === "line").map((item) => item.index)).toEqual([0, 1]);
  expect(container.textContent).toContain("Session VWAP is unavailable on daily or weekly candles");
  expect(container.querySelector('a[href="https://www.tradingview.com/lightweight-charts/"]')).not.toBeNull();
});

test("multiple EMA series retain their own values and update style/title without recreating the other", async () => {
  const configs = [{ id: "ema", instanceId: "fast", enabled: true, params: { period: 5 }, color: "#ff0000", lineWidth: 2 },
    { id: "ema", instanceId: "slow", enabled: true, params: { period: 20 }, color: "#00ff00", lineWidth: 3 }];
  await render({ configs });
  const [fast, slow] = mockCharts[0].series.filter(row => row.kind === "line");
  expect(fast.setData.mock.calls[0][0]).not.toEqual(slow.setData.mock.calls[0][0]);
  await render({ configs: [configs[0], { ...configs[1], params: { period: 30 }, color: "#0000ff", lineWidth: 4 }] });
  expect(mockCharts[0].addSeries).toHaveBeenCalledTimes(3);
  expect(slow.applyOptions).toHaveBeenLastCalledWith({ title: "EMA 30", color: "#0000ff", lineWidth: 4 });
  await render({ configs: [configs[1]] });
  expect(mockCharts[0].removeSeries).toHaveBeenCalledWith(fast);
  expect(mockCharts[0].removeSeries).not.toHaveBeenCalledWith(slow);
});

test("drawing style edits affect the selected drawing, save it, and leave other drawings intact", async () => {
  const rows = ["a", "b"].map((id, i) => ({ id, kind: "ray", points: [{ time: 100, price: 10 + i }, { time: 200, price: 20 + i }], style: { color: "#3b82f6", width: 2, extendRight: true } }));
  saveDrawingEnvelope(localStorage, initial.instrument, rows); await render();
  await act(async () => mockManagers[0].select(["a"]));
  await act(async () => container.querySelector('[aria-label="Drawing color #fbbf24"]').click());
  const select = container.querySelector('[aria-label="Drawing line width"]');
  await act(async () => { select.value = "4"; select.dispatchEvent(new Event("change", { bubbles: true })); });
  const saved = readDrawingEnvelope(localStorage, initial.instrument).drawings;
  expect(saved[0].style).toMatchObject({ color: "#fbbf24", width: 4, extendRight: true });
  expect(saved[1].style).toMatchObject({ color: "#3b82f6", width: 2 });
  expect(saved[0].anchors).toEqual(rows[0].points);
  for (const label of ["Arrow", "Ray", "Horizontal ray", "Vertical line", "Ruler (price and time)"]) expect(container.querySelector(`[aria-label="${label}"]`)).not.toBeNull();
});
