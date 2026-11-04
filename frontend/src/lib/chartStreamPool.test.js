import { createChartStreamPool } from "./chartStreamPool";

class SyntheticEventSource {
  static instances = [];
  constructor(url) { this.url = url; this.listeners = new Map(); this.closed = false; this.readyState = 0; SyntheticEventSource.instances.push(this); }
  addEventListener(type, fn) { this.listeners.set(type, fn); }
  close() { this.closed = true; }
  emit(type, payload) {
    if (type === "open") this.readyState = 1;
    if (type === "error") this.readyState = 0;
    const event = { type, data: JSON.stringify(payload) };
    this.listeners.get(type)?.(event);
    this[`on${type}`]?.(event);
  }
}
const flush = () => Promise.resolve();
let pool;
beforeEach(() => { SyntheticEventSource.instances = []; pool = createChartStreamPool("/api", SyntheticEventSource); });

test("duplicate consumers share one channel and closing one preserves its sibling", async () => {
  const a = pool.stream("NSE_INDEX|Nifty 50", "1m");
  const b = pool.stream("NSE_INDEX|Nifty 50", "1m");
  const receiveA = jest.fn(), receiveB = jest.fn();
  a.addEventListener("snapshot", receiveA); b.addEventListener("snapshot", receiveB);
  await flush();
  expect(SyntheticEventSource.instances).toHaveLength(1);
  const source = SyntheticEventSource.instances[0];
  expect(JSON.parse(new URL(source.url, "http://fixture").searchParams.get("channels"))).toEqual([
    { instrument_key: "NSE_INDEX|Nifty 50", timeframe: "1m" },
  ]);
  a.close(); await flush();
  source.emit("snapshot", { instrument_key: "NSE_INDEX|Nifty 50", timeframe: "1m" });
  expect(receiveA).not.toHaveBeenCalled(); expect(receiveB).toHaveBeenCalledTimes(1);
  expect(source.closed).toBe(false);
  b.close(); expect(source.closed).toBe(true);
});

test("channel changes replace the connection and ignore stale events including open and errors", async () => {
  const a = pool.stream("key", "1m");
  const snapshots = jest.fn(), resync = jest.fn(), opened = jest.fn(), error = jest.fn();
  a.addEventListener("snapshot", snapshots); a.addEventListener("resync", resync);
  a.onopen = opened; a.onerror = error;
  await flush();
  const old = SyntheticEventSource.instances[0];
  const b = pool.stream("key", "5m");
  const other = jest.fn(); b.addEventListener("snapshot", other);
  await flush();
  const active = SyntheticEventSource.instances[1];
  expect(old.closed).toBe(true);
  old.emit("snapshot", { instrument_key: "key", timeframe: "1m" }); old.emit("open"); old.emit("error");
  expect(snapshots).not.toHaveBeenCalled(); expect(opened).not.toHaveBeenCalled(); expect(error).not.toHaveBeenCalled();
  active.emit("snapshot", { instrument_key: "key", timeframe: "5m" });
  active.emit("resync", { instrument_key: "key", timeframe: "1m", reason: "tick_queue_overflow" });
  active.emit("open"); active.emit("error");
  expect(snapshots).not.toHaveBeenCalled(); expect(other).toHaveBeenCalledTimes(1);
  expect(resync).toHaveBeenCalledTimes(1); expect(opened).toHaveBeenCalledTimes(1); expect(error).toHaveBeenCalledTimes(1);
  a.close(); b.close();
  expect(active.closed).toBe(true);
});

test("batch registration supports six unique channels and unlimited duplicate consumers", async () => {
  const viewers = ["1m", "3m", "5m", "15m", "30m", "1h"].map(tf => pool.stream("key", tf));
  viewers.push(pool.stream("key", "1m"));
  expect(() => pool.stream("key", "1d")).toThrow(/six|6/i);
  await flush();
  expect(SyntheticEventSource.instances).toHaveLength(1);
  viewers.forEach(v => v.close());
  await flush();
  expect(SyntheticEventSource.instances).toHaveLength(1);
  expect(SyntheticEventSource.instances[0].closed).toBe(true);
});

test("closing before the registration flush never opens a transport", async () => {
  pool.stream("key", "1m").close();
  await flush();
  expect(SyntheticEventSource.instances).toHaveLength(0);
});

test("joining an already open channel reports open asynchronously without a reconnect", async () => {
  const first = pool.stream("key", "1m");
  await flush();
  SyntheticEventSource.instances[0].emit("open");
  const second = pool.stream("key", "1m");
  const opened = jest.fn(); second.onopen = opened;
  expect(opened).not.toHaveBeenCalled();
  await flush();
  expect(opened).toHaveBeenCalledTimes(1);
  expect(second.readyState).toBe(1);
  expect(SyntheticEventSource.instances).toHaveLength(1);
  first.close(); second.close();
});

test("constructor failure notifies consumers and a later registration retries", async () => {
  let fails = true;
  class FlakySource extends SyntheticEventSource {
    constructor(url) { if (fails) throw new Error("transport unavailable"); super(url); }
  }
  pool = createChartStreamPool("/api", FlakySource);
  const first = pool.stream("key", "1m");
  const failed = jest.fn(); first.onerror = failed;
  await flush();
  expect(failed).toHaveBeenCalledTimes(1);
  fails = false;
  const second = pool.stream("key", "1m");
  await flush();
  expect(SyntheticEventSource.instances).toHaveLength(1);
  first.close(); second.close();
});
