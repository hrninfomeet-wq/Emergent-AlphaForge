import React, { act } from "react";
import { createRoot } from "react-dom/client";
import useChartAnalysis from "./useChartAnalysis";
import { chartAnalysisApi as api } from "../../lib/chartAnalysisApi";
import { defaultAnalysisSettings } from "../../lib/chartAnalysisSettings";
jest.mock("../../lib/chartAnalysisApi", () => ({ chartAnalysisApi: { catalog: jest.fn(), observe: jest.fn() } }));
const a = { instrument_key: "NSE_INDEX|Nifty 50", symbol: "NIFTY", kind: "index" }, b = { instrument_key: "BSE_INDEX|SENSEX", symbol: "SENSEX", kind: "index" };
const response = (instrument = a) => ({ instrument, timeframe: "1m", as_of_ms: Date.now(), closed_through: 60, overlays: {}, warnings: [], strategy: { id: "ema", signals: [] } });
const deferred = () => { let resolve; const promise = new Promise(done => { resolve = done; }); return { promise, resolve }; };
let root, host, state;
function Harness({ instrument, settings }) { state = useChartAnalysis(instrument, "1m", settings); return null; }
const render = async (settings, instrument = a) => act(async () => root.render(<Harness instrument={instrument} settings={settings} />));
beforeEach(() => { globalThis.IS_REACT_ACT_ENVIRONMENT = true; jest.useFakeTimers(); jest.clearAllMocks(); Object.defineProperty(document, "hidden", { configurable: true, value: false }); api.catalog.mockResolvedValue({ overlays: [], strategies: [] }); api.observe.mockImplementation(async key => response(key === a.instrument_key ? a : b)); host = document.createElement("div"); document.body.append(host); root = createRoot(host); });
afterEach(async () => { await act(async () => root.unmount()); host.remove(); jest.useRealTimers(); });
test("all-off default never observes or creates forecast work", async () => { await render(defaultAnalysisSettings()); await act(async () => jest.advanceTimersByTime(30000)); expect(api.observe).not.toHaveBeenCalled(); expect(state.enabled).toBe(false); });
test('future responses are refused and an in-flight refresh cannot keep old research fresh', async () => {
  const settings = { ...defaultAnalysisSettings(), forecast: true };
  api.observe.mockResolvedValueOnce({ ...response(), as_of_ms: Date.now() + 60000 });
  await render(settings); expect(state.data).toBeNull(); expect(state.error).toBeTruthy();
  await act(async () => jest.advanceTimersByTime(10000)); expect(state.data).not.toBeNull();
  api.observe.mockReturnValueOnce(deferred().promise);
  await act(async () => jest.advanceTimersByTime(16000)); expect(state.stale).toBe(true);
});
test("one in-flight request, context abort and late response isolation", async () => {
  const pending = deferred(); api.observe.mockReturnValueOnce(pending.promise); const settings = { ...defaultAnalysisSettings(), forecast: true };
  await render(settings); const signal = api.observe.mock.calls[0][3].signal;
  await act(async () => jest.advanceTimersByTime(50000)); expect(api.observe).toHaveBeenCalledTimes(1);
  await render(settings, b); expect(signal.aborted).toBe(true); expect(state.data.instrument.instrument_key).toBe(b.instrument_key);
  await act(async () => pending.resolve(response(a))); expect(state.data.instrument.instrument_key).toBe(b.instrument_key);
});
test("failed polling retains original capture with stale flag and ignores wrong identity", async () => {
  const settings = { ...defaultAnalysisSettings(), overlays: { fvg: true } }; await render(settings); const original = state.data;
  api.observe.mockRejectedValueOnce(new Error("offline")); await act(async () => jest.advanceTimersByTime(5000));
  expect(state.data).toBe(original); expect(state.stale).toBe(true); expect(state.error).toContain("stale");
  api.observe.mockResolvedValueOnce(response(b)); await act(async () => jest.advanceTimersByTime(10000));
  expect(state.data).toBe(original); expect(state.error).toContain("identity mismatch");
});
test("strategy signals accumulate uniquely and reset with strategy; hidden polling pauses", async () => {
  const settings = { ...defaultAnalysisSettings(), strategyId: "ema" };
  const one = { id: "one", time: 60, price: 100, direction: "UP", text: "Research" }, two = { ...one, id: "two", time: 120 };
  api.observe.mockResolvedValueOnce({ ...response(), strategy: { id: "ema", signals: [one] } }); await render(settings);
  api.observe.mockResolvedValueOnce({ ...response(), strategy: { id: "ema", signals: [one, two] } }); await act(async () => jest.advanceTimersByTime(5000)); expect(state.signals.map(row => row.id)).toEqual(["one", "two"]);
  Object.defineProperty(document, "hidden", { configurable: true, value: true }); await act(async () => jest.advanceTimersByTime(5000)); expect(api.observe).toHaveBeenCalledTimes(2);
  Object.defineProperty(document, "hidden", { configurable: true, value: false }); await act(async () => document.dispatchEvent(new Event("visibilitychange"))); expect(api.observe).toHaveBeenCalledTimes(3);
  await render({ ...settings, strategyId: "other" }); expect(state.signals).toEqual([]);
});
