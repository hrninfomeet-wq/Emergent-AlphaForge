import React, { act } from "react";
import { createRoot } from "react-dom/client";
import { createSeriesMarkers } from "lightweight-charts";
import ChartAnalysisOverlays, { analysisVisuals } from "./ChartAnalysisOverlays";
jest.mock("lightweight-charts", () => ({ createSeriesMarkers: jest.fn() }));
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
