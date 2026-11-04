import React, { act } from "react";
import { createRoot } from "react-dom/client";
import useChartWatchlist, { watchlistQuote } from "./useChartWatchlist";
import { chartWatchlistApi as api } from "../../lib/chartWatchlistApi";

jest.mock("../../lib/chartWatchlistApi", () => ({ chartWatchlistApi: { get: jest.fn(), save: jest.fn(), stream: jest.fn() } }));
const a = { instrument_key: "NSE_INDEX|Nifty 50", symbol: "NIFTY", name: "Nifty 50", kind: "index", exchange: "NSE" };
const b = { instrument_key: "BSE_INDEX|SENSEX", symbol: "SENSEX", name: "SENSEX", kind: "index", exchange: "BSE" };
const initial = () => ({ id: "default", name: "My list", revision: 1, capacity: 200, items: [{ id: "a", instrument: a, label: "" }, { id: "b", instrument: b, label: "" }] });
const deferred = () => { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; };
let container, root, state, server, streams;
function Probe() { state = useChartWatchlist(); return <span>{state.watchlist?.name}</span>; }
const render = () => act(async () => root.render(<Probe />));
const quote = (extra = {}) => ({ tick: { last_price: 110, close_price: 100, ts: Date.now(), ingest_ts: Date.now() }, status: "live", connected: true, last_tick_age_ms: 0, emitted_at_ms: Date.now(), ...extra });
beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  jest.useFakeTimers(); jest.setSystemTime(new Date("2026-11-04T06:00:00Z")); jest.clearAllMocks(); server = initial(); streams = [];
  api.get.mockImplementation(async () => server);
  api.save.mockImplementation(async (payload) => {
    server = { ...server, name: payload.name, revision: server.revision + 1, items: payload.items.map((item, index) => ({
      id: item.id || `new-${index}`, instrument: [a, b].find((candidate) => candidate.instrument_key === item.instrument_key), label: item.label,
    })) };
    return server;
  });
  api.stream.mockImplementation(() => {
    const listeners = {}; const stream = { addEventListener: jest.fn((type, listener) => { listeners[type] = listener; }), close: jest.fn(),
      emit: (type, value) => listeners[type]?.({ data: typeof value === "string" ? value : JSON.stringify(value) }) };
    streams.push(stream); return stream;
  });
  container = document.createElement("div"); document.body.appendChild(container); root = createRoot(container);
});
afterEach(async () => { await act(async () => root.unmount()); container.remove(); jest.useRealTimers(); });

test("server-confirmed name, symbol label, order, delete and add survive a new hook instance", async () => {
  await render();
  await act(async () => state.rename("Session list"));
  await act(async () => state.relabel("a", "Core index"));
  await act(async () => state.move("b", -1));
  expect(state.watchlist.items.map((item) => item.id)).toEqual(["b", "a"]);
  await act(async () => state.remove("b"));
  await act(async () => state.add(b));
  expect(api.save.mock.calls[4][0]).toEqual({ revision: 5, name: "Session list", items: [
    { id: "a", instrument_key: a.instrument_key, label: "Core index" }, { instrument_key: b.instrument_key, label: "" },
  ] });
  await act(async () => root.unmount()); root = createRoot(container); await render();
  expect(state.watchlist).toEqual(server); expect(state.watchlist.items[0].label).toBe("Core index"); expect(state.watchlist.name).toBe("Session list");
});

test("mutations serialize before rerender and do not optimistically claim saved state", async () => {
  await render(); const pending = deferred(); api.save.mockReturnValueOnce(pending.promise);
  let first, second;
  await act(async () => { first = state.rename("Pending"); second = state.remove("b"); });
  expect(api.save).toHaveBeenCalledTimes(1); expect(state.watchlist.name).toBe("My list"); expect(state.saving).toBe(true);
  expect(await second).toBe(false);
  await act(async () => { pending.resolve({ ...server, name: "Pending", revision: 2 }); await first; });
  expect(state.watchlist.name).toBe("Pending"); expect(state.saving).toBe(false);
});

test("duplicate, over-capacity, empty name and invalid reorder never write", async () => {
  await render(); await act(async () => state.add(a)); expect(state.error).toContain("already");
  await act(async () => state.rename(" ")); expect(state.error).toContain("1–80");
  await act(async () => state.move("a", -1)); expect(api.save).not.toHaveBeenCalled();
  server = { ...server, revision: 2, items: Array.from({ length: 200 }, (_, index) => ({ id: String(index), instrument: { ...a, instrument_key: `key-${index}` }, label: "" })) };
  await act(async () => state.refresh()); await act(async () => state.add(b)); expect(state.error).toContain("full"); expect(api.save).not.toHaveBeenCalled();
});

test("conflict reloads authoritative data, explains lost edit and never auto-retries it", async () => {
  await render(); server = { ...server, name: "Other window", revision: 2 };
  api.save.mockRejectedValueOnce({ response: { status: 409 } });
  let result; await act(async () => { result = await state.rename("My unsaved edit"); });
  expect(result).toBe(false); expect(state.watchlist.name).toBe("Other window"); expect(state.error).toContain("Your edit was not saved"); expect(api.save).toHaveBeenCalledTimes(1); expect(api.get).toHaveBeenCalledTimes(2);
});

test("ambiguous save acknowledgement reconciles from GET and still reports unconfirmed save", async () => {
  await render(); server = { ...server, revision: 2, name: "Committed but lost reply" }; api.save.mockRejectedValueOnce(new Error("network"));
  let result; await act(async () => { result = await state.rename("Committed but lost reply"); });
  expect(result).toBe(false); expect(state.watchlist.name).toBe(server.name); expect(state.error).toContain("not confirmed");
});

test("cached age grows independently of heartbeat and silent transport becomes offline", async () => {
  await render();
  await act(async () => streams[0].emit("snapshot", { revision: 1, quotes: { [a.instrument_key]: quote() }, warnings: [] }));
  let view = watchlistQuote(state.quotes[a.instrument_key], state.streamHealthy, state.now);
  expect(view.fresh).toBe(true); expect(view.percent).toBe(10);
  await act(async () => jest.advanceTimersByTime(11000));
  view = watchlistQuote(state.quotes[a.instrument_key], state.streamHealthy, state.now); expect(view.status).toBe("Stale"); expect(view.price).toBe(110);
  await act(async () => jest.advanceTimersByTime(2000));
  view = watchlistQuote(state.quotes[a.instrument_key], state.streamHealthy, state.now); expect(view.status).toBe("Offline"); expect(view.timestamp).toContain("IST");
});

test("transport errors and explicit stream_error mark retained quote stale immediately", async () => {
  await render();
  const snapshot = { revision: 1, quotes: { [a.instrument_key]: quote() }, warnings: [] };
  await act(async () => streams[0].emit("snapshot", snapshot));
  await act(async () => streams[0].onerror()); expect(state.streamHealthy).toBe(false); expect(state.quotes[a.instrument_key].tick.last_price).toBe(110);
  await act(async () => streams[0].emit("snapshot", snapshot)); expect(state.streamHealthy).toBe(true);
  await act(async () => streams[0].emit("stream_error", { warnings: ["Feed offline"] })); expect(state.streamHealthy).toBe(false); expect(state.warnings).toEqual(["Feed offline"]);
});

test("revision replacement closes stream, drops old quotes and ignores queued old events", async () => {
  await render(); const old = streams[0];
  await act(async () => old.emit("snapshot", { revision: 1, quotes: { [a.instrument_key]: quote() } }));
  server = { ...server, revision: 2 };
  await act(async () => old.emit("watchlist_changed", { revision: 2 }));
  expect(old.close).toHaveBeenCalledTimes(1); expect(streams).toHaveLength(2); expect(state.quotes).toEqual({});
  await act(async () => old.emit("snapshot", { revision: 1, quotes: { [a.instrument_key]: quote() } }));
  expect(state.quotes).toEqual({}); expect(state.streamHealthy).toBe(false);
});

test("refresh events during a pending write reconcile after the write without stale reads", async () => {
  await render(); const pending = deferred(); api.save.mockReturnValueOnce(pending.promise); let write;
  await act(async () => { write = state.rename("Mine"); streams[0].emit("watchlist_changed", { revision: 2 }); });
  expect(api.get).toHaveBeenCalledTimes(1);
  server = { ...server, name: "Newest", revision: 3 };
  await act(async () => { pending.resolve({ ...server, name: "Mine", revision: 2 }); await write; });
  expect(api.get).toHaveBeenCalledTimes(2); expect(state.watchlist.name).toBe("Newest");
});

test("unmount aborts reads and writes, closes streams, and rejects late acknowledgements", async () => {
  await render(); const pending = deferred(); api.save.mockReturnValueOnce(pending.promise); let write;
  await act(async () => { write = state.rename("Late"); }); const signal = api.save.mock.calls[0][1].signal;
  await act(async () => root.unmount()); expect(signal.aborted).toBe(true); expect(streams[0].close).toHaveBeenCalledTimes(1);
  let result; await act(async () => { pending.resolve({ ...server, name: "Late", revision: 2 }); result = await write; }); expect(result).toBe(false);
  root = createRoot(container); const read = deferred(); api.get.mockReturnValueOnce(read.promise); await render(); const readSignal = api.get.mock.calls[1][0].signal;
  await act(async () => root.unmount()); expect(readSignal.aborted).toBe(true);
  await act(async () => read.resolve(server)); root = createRoot(container);
});

test("quote percentages reject missing, zero and boolean close and future timestamp cannot be fresh", () => {
  for (const close_price of [null, undefined, 0, -1, true, "", "bad"]) {
    const value = quote(); value.tick.close_price = close_price; value.receivedAtMs = Date.now();
    expect(watchlistQuote(value, true, Date.now()).percent).toBeNull();
  }
  const future = quote(); future.receivedAtMs = Date.now(); future.tick.ts = Date.now() + 6000;
  expect(watchlistQuote(future, true, Date.now()).fresh).toBe(false);
  for (const ts of [-1, NaN, 1e100, Infinity, true]) {
    const invalid = quote(); invalid.tick.ts = ts; invalid.receivedAtMs = Date.now();
    expect(watchlistQuote(invalid, true, Date.now()).timestamp).toBe("Quote time unavailable");
    expect(watchlistQuote(invalid, true, Date.now()).fresh).toBe(false);
  }
});
