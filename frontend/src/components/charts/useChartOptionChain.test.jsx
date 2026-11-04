import React, { act } from "react";
import { createRoot } from "react-dom/client";
import useChartOptionChain from "./useChartOptionChain";
import { chartOptionApi as api } from "../../lib/chartOptionApi";

jest.mock("../../lib/chartOptionApi", () => ({ chartOptionApi: { expiries: jest.fn(), chain: jest.fn(), stream: jest.fn() } }));
const a = { instrument_key: "NSE_INDEX|Nifty 50", symbol: "NIFTY" };
const b = { instrument_key: "BSE_INDEX|SENSEX", symbol: "SENSEX" };
const expiry = "2026-11-10", later = "2026-11-17";
const deferred = () => { let resolve; const promise = new Promise((yes) => { resolve = yes; }); return { promise, resolve }; };
const snapshot = (key = a, date = expiry) => ({ underlying: key, expiry: date, expiries: [expiry, later], spot: 25000, atm_strike: 25000,
  total_strikes: 80, rows: [{ strike: 24950, ce: null, pe: null }, { strike: 25000, atm: true, ce: { instrument: { instrument_key: "CE" }, ltp: 0, oi: null, volume: 0, iv: 12 }, pe: null }, { strike: 25050, ce: null, pe: null }], captured_at_ms: Date.now(), warnings: [] });
let container, root, state, streams;
function Harness({ instrument }) { state = useChartOptionChain(instrument); return null; }
const render = async (instrument = a) => act(async () => root.render(<Harness instrument={instrument} />));
beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true; jest.clearAllMocks(); jest.useFakeTimers(); streams = [];
  jest.setSystemTime(new Date("2026-11-04T06:00:00Z"));
  api.expiries.mockImplementation(async (key) => ({ underlying: key === a.instrument_key ? a : b, expiries: [later, expiry], warnings: [] }));
  api.chain.mockImplementation(async (key, params) => snapshot(key === a.instrument_key ? a : b, params.expiry));
  api.stream.mockImplementation(() => { const listeners = {}; const stream = { addEventListener: (type, fn) => { listeners[type] = fn; }, close: jest.fn(), emit: (type, value) => listeners[type]?.({ data: JSON.stringify(value) }) }; streams.push(stream); return stream; });
  container = document.createElement("div"); document.body.appendChild(container); root = createRoot(container);
});
afterEach(async () => { await act(async () => root.unmount()); container.remove(); jest.useRealTimers(); });

test("selects nearest returned expiry and opens one matching read-only stream after REST", async () => {
  await render(); expect(state.expiry).toBe(expiry); expect(api.chain).toHaveBeenCalledWith(a.instrument_key, { expiry, radius: 20 }, expect.any(Object));
  expect(api.stream).toHaveBeenCalledWith(a.instrument_key, { expiry, radius: 20 }); expect(state.data.rows).toHaveLength(3);
});

test("expiry changes close old stream, abort old requests and reject queued events", async () => {
  await render(); const old = streams[0]; const pending = deferred(); api.chain.mockReturnValueOnce(pending.promise);
  await act(async () => state.setExpiry(later)); const signal = api.chain.mock.calls[1][2].signal;
  expect(old.close).toHaveBeenCalledTimes(1); expect(state.data).toBeNull();
  await act(async () => state.setExpiry(expiry)); expect(signal.aborted).toBe(true);
  await act(async () => { pending.resolve(snapshot(a, later)); old.emit("snapshot", snapshot(a, later)); });
  expect(state.data.expiry).toBe(expiry); expect(state.expiry).toBe(expiry);
});

test("instrument replacement aborts pending metadata and cannot render the previous chain", async () => {
  const pending = deferred(); api.expiries.mockReturnValueOnce(pending.promise); await render(); const signal = api.expiries.mock.calls[0][1].signal;
  await render(b); expect(signal.aborted).toBe(true); expect(state.data.underlying.instrument_key).toBe(b.instrument_key);
  await act(async () => pending.resolve({ underlying: a, expiries: [later] })); expect(state.expiry).toBe(expiry); expect(api.chain).toHaveBeenCalledTimes(1);
});

test("stream snapshots replace quotes including nulls, deduplicate REST identity and become stale on silence", async () => {
  await render(); const rest = state.data;
  await act(async () => streams[0].emit("snapshot", rest)); expect(state.data).toBe(rest);
  const update = snapshot(); update.as_of_ms = Date.now() + 1; update.status = "live"; update.rows[1].ce = { ...update.rows[1].ce, ltp: 125, oi: 0, iv: null };
  await act(async () => streams[0].emit("snapshot", update)); expect(state.data.rows[1].ce.ltp).toBe(125); expect(state.data.rows[1].ce.iv).toBeNull(); expect(state.streamHealthy).toBe(true);
  await act(async () => jest.advanceTimersByTime(13000)); expect(state.streamHealthy).toBe(false); expect(state.status).toBe("Stale");
});

test("strike-window changes keep identity isolated and Back to ATM removes center", async () => {
  await render(); await act(async () => state.moveWindow(1));
  expect(api.chain.mock.calls[1][1].center).toBeGreaterThan(25000); expect(streams[0].close).toHaveBeenCalledTimes(1);
  await act(async () => state.backToAtm()); expect(api.chain.mock.calls[2][1]).toEqual({ expiry, radius: 20 });
});

test("empty expiry, REST errors and stream errors are explicit and cleanup closes owned resources", async () => {
  api.expiries.mockResolvedValueOnce({ underlying: a, expiries: [] }); await render(); expect(state.empty).toBe(true); expect(api.chain).not.toHaveBeenCalled();
  await act(async () => state.refresh()); expect(state.data).not.toBeNull();
  await act(async () => streams[0].emit("stream_error", { warnings: ["feed offline"] })); expect(state.warnings).toContain("feed offline"); expect(state.streamHealthy).toBe(false);
  const signal = api.chain.mock.calls[0][2].signal; await act(async () => root.unmount()); expect(signal.aborted).toBe(true); expect(streams[0].close).toHaveBeenCalledTimes(1); root = createRoot(container);
});

test("failed refresh retains original values and capture time with stale status", async () => {
  await render(); const original = state.data;
  api.chain.mockRejectedValueOnce(new Error("offline"));
  await act(async () => state.refresh());
  expect(state.data).toBe(original); expect(state.data.captured_at_ms).toBe(original.captured_at_ms);
  expect(state.error).toContain("could not be loaded"); expect(state.status).toBe("Stale");
  expect(streams[0].close).toHaveBeenCalledTimes(1);
});

test("REST stale and disconnected or waiting streams never imply live quotes", async () => {
  api.chain.mockResolvedValueOnce({ ...snapshot(), stale: true }); await render(); expect(state.status).toBe("Stale");
  await act(async () => streams[0].emit("snapshot", { ...snapshot(), status: "disconnected" })); expect(state.status).toBe("Disconnected");
  await act(async () => streams[0].emit("snapshot", { ...snapshot(), status: "waiting" })); expect(state.status).toBe("Waiting for quotes");
});

test("IST rollover drops expired selection and discovers nearest remaining expiry", async () => {
  jest.setSystemTime(new Date("2026-11-10T18:29:59Z")); await render(); expect(state.expiry).toBe(expiry);
  await act(async () => jest.advanceTimersByTime(2000));
  expect(state.expiry).toBe(later); expect(state.expiries).toEqual([later]);
  expect(streams[0].close).toHaveBeenCalledTimes(1);
});

test("out-of-order stream snapshots cannot replace a newer emission", async () => {
  await render(); const update = { ...snapshot(), as_of_ms: Date.now() + 2000, status: "live" };
  await act(async () => streams[0].emit("snapshot", update));
  await act(async () => streams[0].emit("snapshot", { ...snapshot(), as_of_ms: Date.now() + 1000, spot: 1 }));
  expect(state.data.spot).toBe(25000); expect(state.data.as_of_ms).toBe(update.as_of_ms);
});
