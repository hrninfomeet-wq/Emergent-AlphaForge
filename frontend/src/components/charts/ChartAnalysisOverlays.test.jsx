import React, { act } from "react";
import { createRoot } from "react-dom/client";
import { createSeriesMarkers } from "lightweight-charts";
import ChartAnalysisOverlays, { analysisVisuals, AnalysisZonePrimitive } from "./ChartAnalysisOverlays";
jest.mock("lightweight-charts", () => ({ createSeriesMarkers: jest.fn() }));

test("current range spans the viewport even with same-bar or unloaded time anchors, while FVG stays bounded", () => {
  const zones = [
    { label: "Discount", low: 90, high: 100, start: 60, end: 60, color: "#22a67a" },
    { label: "Premium", low: 100, high: 110, start: 60, end: 60, color: "#dd6b60" },
  ];
  const fvg = { low: 80, high: 85, start: 1, end: 2, color: "#abcdef" };
  const visual = analysisVisuals({ overlays: { premium_discount: { zones }, fvg: { zones: [fvg] } } },
    { overlays: { premium_discount: true, fvg: true } }, [], []);
  const timeToCoordinate = jest.fn(time => time === 60 ? null : time * 10);
  const series = { priceToCoordinate: price => 200 - price };
  const primitive = new AnalysisZonePrimitive(visual.zones, "white");
  primitive.attached({ chart: { timeScale: () => ({ timeToCoordinate }) }, series, requestUpdate() {} });
  const context = Object.fromEntries(["save", "restore", "fillRect", "strokeRect", "beginPath", "moveTo", "lineTo", "stroke", "setLineDash", "fillText"].map(name => [name, jest.fn()]));
  context.measureText = text => ({ width: text.length * 6 });
  primitive.draw({ useBitmapCoordinateSpace: draw => draw({ context, bitmapSize: { width: 1000, height: 400 }, horizontalPixelRatio: 2, verticalPixelRatio: 2 }) });
  expect(context.fillRect.mock.calls).toEqual([[0, 200, 1000, 20], [0, 180, 1000, 20], [20, 230, 20, 10]]);
  expect(timeToCoordinate.mock.calls.flat()).toEqual([1, 2]);
  expect(primitive.autoscaleInfo()).toEqual({ priceRange: { minValue: 90, maxValue: 110 } });
  primitive.drawLabels({ useMediaCoordinateSpace: draw => draw({ context, mediaSize: { width: 500, height: 200 } }) });
  expect(context.moveTo).toHaveBeenCalledWith(0, 100);
  expect(context.lineTo).toHaveBeenCalledWith(500, 100);
  expect(context.fillText.mock.calls.map(call => call[0])).toEqual(["Premium", "Equilibrium · 50%", "Discount"]);
  // A newly confirmed range changes scale and geometry using the new prices.
  const next = new AnalysisZonePrimitive(visual.zones.slice(0, 2).map(zone => ({ ...zone, high: zone.high + 20, low: zone.low + 20 })), "default");
  next.attached({ chart: {}, series, requestUpdate() {} });
  expect(next.autoscaleInfo()).toEqual({ priceRange: { minValue: 110, maxValue: 130 } });
  next.drawLabels({ useMediaCoordinateSpace: draw => draw({ context, mediaSize: { width: 500, height: 200 } }) });
  expect(context.moveTo).toHaveBeenLastCalledWith(0, 80);
  expect(new AnalysisZonePrimitive([fvg]).autoscaleInfo()).toBeNull();
});
test("separate read-only renderer filters disabled groups and deduplicates strategy markers", () => {
  const event = { id: "e", time: 60, direction: "UP", text: "Research" };
  const visuals = analysisVisuals({ overlays: { fvg: { zones: [], lines: [{ price: 123 }], markers: [event] }, order_blocks: { lines: [{ price: 999 }] } } }, { overlays: { fvg: true }, strategyId: "ema" }, [event, { ...event, id: "outside", time: 1 }], [{ time: 60 }]);
  expect(visuals.lines.map(row => row.price)).toEqual([123]); expect(visuals.markers).toHaveLength(1); expect(visuals.markers[0].shape).toBe("arrowUp");
});

test('premium/discount bands and equilibrium hide together without hiding S/R', () => {
  const zone = { start: 60, end: 120, low: 99, high: 100 };
  const data = { overlays: { premium_discount: { lines: [{ price: 100 }], zones: [zone, { ...zone, low: 100, high: 101 }] }, support_resistance: { lines: [{ price: 98 }] } } };
  const draw = enabled => analysisVisuals(data, { overlays: { premium_discount: enabled, support_resistance: true } }, [], []);
  expect(draw(true).zones).toHaveLength(2); expect(draw(true).lines).toHaveLength(2);
  expect(draw(false).zones).toEqual([]); expect(draw(false).lines).toEqual([{ price: 98 }]);
});
test("generated layers detach without touching user drawing or execution managers", async () => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true; const host = document.createElement("div"), root = createRoot(host), detach = jest.fn(); createSeriesMarkers.mockReturnValue({ detach });
  const series = { attachPrimitive: jest.fn(), detachPrimitive: jest.fn(), createPriceLine: jest.fn(() => "researchLine"), removePriceLine: jest.fn() }, chart = {};
  await act(async () => root.render(<ChartAnalysisOverlays chart={chart} series={series} data={{ overlays: { fvg: { lines: [{ price: 123 }], zones: [] } } }} settings={{ overlays: { fvg: true } }} bars={[]} />));
  expect(series.attachPrimitive).toHaveBeenCalledTimes(1); expect(createSeriesMarkers).toHaveBeenCalledWith(series, []);
  await act(async () => root.unmount()); expect(detach).toHaveBeenCalledTimes(1); expect(series.detachPrimitive).toHaveBeenCalledTimes(1); expect(series.removePriceLine).toHaveBeenCalledWith("researchLine");
});
