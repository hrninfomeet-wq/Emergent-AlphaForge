import React, { act } from "react";
import { createRoot } from "react-dom/client";
import useChartData from "./useChartData";
import { chartApi } from "../../lib/chartApi";
import { INDEX_SHORTCUTS } from "./chartPresentation";

jest.mock("../../lib/chartApi", () => ({ chartApi: { history: jest.fn(), positions: jest.fn(), stream: jest.fn() } }));
let root, container, state, streams;
const a = INDEX_SHORTCUTS[0], b = INDEX_SHORTCUTS[1];
const bar = (time, close = 100, volume = 40) => ({ time, open: close, high: close + 1, low: close - 1, close, volume });
const history = (instrument = a, timeframe = "1m", bars = [bar(100)]) => ({ instrument, timeframe, bars, source: "fixture", volume_available: false, has_more: false, next_before: 100, warnings: [] });
const deferred = () => { let resolve; const promise = new Promise((done) => { resolve = done; }); return { promise, resolve }; };
function Probe({ instrument = a, timeframe = "1m" }) { state = useChartData(instrument, timeframe); return <span>{state.bars.map((row) => row.close).join(",")}</span>; }
beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  jest.useFakeTimers(); jest.clearAllMocks(); streams = [];
  chartApi.history.mockImplementation((key, timeframe) => Promise.resolve(history(key === b.instrument_key ? b : a, timeframe)));
  chartApi.positions.mockResolvedValue({ positions: [], warnings: [] });
  chartApi.stream.mockImplementation(() => { const listeners = {}; const stream = { addEventListener: jest.fn((name, callback) => { listeners[name] = callback; }), close: jest.fn(), emit: (name, data) => listeners[name]?.({ data: JSON.stringify(data) }) }; streams.push(stream); return stream; });
  container = document.createElement("div"); document.body.appendChild(container); root = createRoot(container);
});
afterEach(async () => { await act(async () => root.unmount()); container.remove(); jest.useRealTimers(); });
const render = async (props = {}) => act(async () => { root.render(<Probe {...props} />); });

test("switching identity aborts old requests and ignores late history", async () => {
  const pending = deferred(); chartApi.history.mockImplementationOnce(() => pending.promise);
  await render(); const firstSignal = chartApi.history.mock.calls[0][2].signal;
  await render({ instrument: b });
  expect(firstSignal.aborted).toBe(true); expect(state.instrument.instrument_key).toBe(b.instrument_key);
  await act(async () => pending.resolve(history(a, "1m", [bar(100, 999)])));
  expect(state.bars[0].close).toBe(100); expect(state.instrument.instrument_key).toBe(b.instrument_key);
  expect(streams).toHaveLength(1);
});

test("timeframe changes close the old stream; old and wrong-identity events cannot land", async () => {
  await render(); const old = streams[0];
  await render({ timeframe: "5m" }); expect(old.close).toHaveBeenCalledTimes(1);
  await act(async () => {
    old.emit("snapshot", { instrument_key: a.instrument_key, timeframe: "1m", bar: bar(200, 999) });
    streams[1].emit("snapshot", { instrument_key: b.instrument_key, timeframe: "5m", bar: bar(200, 888) });
  });
  expect(state.bars.map((row) => row.close)).toEqual([100]);
});

test("stream batches preserve both changed buckets and never invent authoritative volume", async () => {
  await render();
  await act(async () => streams[0].emit("snapshot", { instrument_key: a.instrument_key, timeframe: "1m", bars: [bar(100, 102, 500), bar(160, 103, 900)], connected: true, status: "live" }));
  expect(state.bars.map(({ time, close, volume }) => [time, close, volume])).toEqual([[100, 102, null], [160, 103, null]]);
});

test("REST reconciliation does not overwrite a tick received while its request was pending", async () => {
  await render(); const pending = deferred(); chartApi.history.mockImplementationOnce(() => pending.promise);
  await act(async () => state.refresh());
  await act(async () => streams[0].emit("snapshot", { instrument_key: a.instrument_key, timeframe: "1m", bar: bar(100, 110) }));
  await act(async () => pending.resolve(history(a, "1m", [bar(100, 105)])));
  expect(state.bars[0].close).toBe(110); expect(state.bars[0].volume).toBeNull();
  chartApi.history.mockResolvedValueOnce(history(a, "1m", [bar(100, 112, 1000)]));
  await act(async () => state.refresh());
  expect(state.bars[0].close).toBe(112); expect(state.bars[0].volume).toBe(1000);
});

test("polling uses two-second position snapshots and sixty-second history reconciliation", async () => {
  await render(); expect(chartApi.history).toHaveBeenCalledTimes(1); expect(chartApi.positions).toHaveBeenCalledTimes(1);
  await act(async () => jest.advanceTimersByTime(2000)); expect(chartApi.positions).toHaveBeenCalledTimes(2); expect(chartApi.history).toHaveBeenCalledTimes(1);
  await act(async () => jest.advanceTimersByTime(58000)); expect(chartApi.history).toHaveBeenCalledTimes(2);
});

test("a failed position refresh retains stale evidence rather than claiming a flat account", async () => {
  const position = { id: "p", origin: "broker", instrument_key: a.instrument_key, quantity: 10, fresh: true };
  chartApi.positions.mockResolvedValueOnce({ positions: [position] }); await render(); expect(state.positions).toEqual([position]);
  chartApi.positions.mockRejectedValueOnce(new Error("offline")); await act(async () => state.refresh());
  expect(state.positions).toEqual([position]); expect(state.positionsAt).toBe(0); expect(state.positionError).toContain("not current");
});

test("a history response for a reused derivative token cannot cross expiries", async () => {
  const option = { ...a, instrument_key: "NSE_FO|123", kind: "option", expiry: "2026-11-05", strike: 25000, option_type: "CE" };
  chartApi.history.mockResolvedValueOnce(history({ ...option, expiry: "2026-11-12" }));
  await render({ instrument: option }); expect(state.bars).toEqual([]); expect(state.error).toContain("did not match");
  expect(chartApi.stream).not.toHaveBeenCalled();
});

test("older history is prepended without replacing current candles", async () => {
  chartApi.history.mockResolvedValueOnce({ ...history(), has_more: true, next_before: 100 }); await render();
  chartApi.history.mockResolvedValueOnce({ ...history(a, "1m", [bar(40, 90), bar(100, 88)]), has_more: true, next_before: 40 });
  await act(async () => state.loadOlder()); expect(state.bars.map((row) => row.close)).toEqual([90, 100]);
  expect(chartApi.history.mock.calls[1][2].before).toBe(100);
});

test("a delayed stream accumulator cannot erase extrema from newer REST history", async () => {
  const corrected = { ...bar(100, 110, 1000), high: 130, low: 80 };
  chartApi.history.mockResolvedValueOnce({ ...history(a, "1m", [corrected]), as_of_ms: 2000 });
  await render();
  await act(async () => streams[0].emit("snapshot", { instrument_key: a.instrument_key, timeframe: "1m", history_as_of_ms: 1000,
    bar: bar(100, 115), connected: true, last_tick_age_ms: 20, status: "live" }));
  expect(state.bars).toEqual([corrected]); expect(state.snapshot.status).toBe("live");
  const reseeded = { ...corrected, close: 120, high: 131 };
  await act(async () => streams[0].emit("snapshot", { instrument_key: a.instrument_key, timeframe: "1m", history_as_of_ms: 2000, bar: reseeded }));
  expect(state.bars[0]).toEqual({ ...reseeded, volume: null });
});

test("an in-flight tick is replayed over REST only when its accumulator includes that REST snapshot", async () => {
  chartApi.history.mockResolvedValueOnce({ ...history(), as_of_ms: 1000 }); await render();
  const pending = deferred(); chartApi.history.mockImplementationOnce(() => pending.promise);
  await act(async () => state.refresh());
  await act(async () => streams[0].emit("snapshot", { instrument_key: a.instrument_key, timeframe: "1m", history_as_of_ms: 1000, bar: bar(100, 115) }));
  const corrected = { ...bar(100, 110, 1000), high: 130, low: 80 };
  await act(async () => pending.resolve({ ...history(a, "1m", [corrected]), as_of_ms: 2000 }));
  expect(state.bars).toEqual([corrected]);
  const next = deferred(); chartApi.history.mockImplementationOnce(() => next.promise); await act(async () => state.refresh());
  const reseeded = { ...corrected, close: 120, high: 131 };
  await act(async () => streams[0].emit("snapshot", { instrument_key: a.instrument_key, timeframe: "1m", history_as_of_ms: 3000, bar: reseeded }));
  await act(async () => next.resolve({ ...history(a, "1m", [corrected]), as_of_ms: 3000 }));
  expect(state.bars).toEqual([{ ...reseeded, volume: null }]);
});
