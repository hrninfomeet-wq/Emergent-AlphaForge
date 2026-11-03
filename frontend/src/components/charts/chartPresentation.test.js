import { drawingStorageKey } from "../../lib/chartModel";
import { INDEX_SHORTCUTS, matchingPositions, positionFresh, readDrawingEnvelope, saveDrawingEnvelope, sessionQuote, streamFreshness } from "./chartPresentation";
const instrument = INDEX_SHORTCUTS[0];
const now = 100000;
beforeEach(() => localStorage.clear());

const quoteNow = Date.parse("2026-11-02T05:00:00Z");
const quoteSnapshot = (last_price = 21231.10, close_price = 21000, ts = quoteNow) => ({
  instrument_key: instrument.instrument_key, tick: { last_price, close_price, ts },
});
const dayQuote = (snapshot = quoteSnapshot(), bar = { open: 21233.10, close: 21231.10 }, at = quoteNow) =>
  sessionQuote(snapshot, instrument.instrument_key, bar, at);

test("session change is previous-close based, independent of candle open and timeframe", () => {
  const result = dayQuote();
  // Independently: 21231.10 - 21000 = 231.10; 231.10 / 21000 * 100 = 1.100476...%.
  expect(result.change).toBeCloseTo(231.10, 8);
  expect(result.percent).toBeCloseTo(1.10047619047619, 10);
  expect(result.text).toBe("+231.10 pts (+1.10%) · Today");
  expect(result.title).toContain("21,000.00");
  for (const timeframe of ["1m", "1d", "1w"]) {
    expect(dayQuote({ ...quoteSnapshot(), timeframe }, { open: 19000, close: 20000 })).toEqual(result);
  }
  expect(dayQuote(quoteSnapshot(21252)).change).toBe(252);
  expect(dayQuote(quoteSnapshot(21252)).percent).toBeCloseTo(1.2, 10);
});

test("losses, flat sessions and rounded zero have explicit, consistent formatting", () => {
  expect(dayQuote(quoteSnapshot(20769, 21000)).text).toBe("−231.00 pts (−1.10%) · Today");
  expect(dayQuote(quoteSnapshot(21000, 21000)).text).toBe("0.00 pts (0.00%) · Today");
  expect(dayQuote(quoteSnapshot(20999.999, 21000)).text).toBe("0.00 pts (0.00%) · Today");
});

test("missing or invalid previous close never becomes zero or a candle return", () => {
  for (const reference of [null, undefined, 0, -1, NaN, Infinity, "", false]) {
    const snapshot = quoteSnapshot();
    snapshot.tick.close_price = reference;
    const result = dayQuote(snapshot);
    expect(result.text).toBe("Day change unavailable");
    expect(result.change).toBeNull();
    expect(result.price).toBe(21231.1);
  }
});

test("foreign quotes and invalid trade timestamps cannot supply a daily return", () => {
  for (const snapshot of [null, { ...quoteSnapshot(), instrument_key: "NSE_INDEX|Nifty Bank" },
    quoteSnapshot(22000, 21000, null), quoteSnapshot(22000, 21000, quoteNow + 6000),
    quoteSnapshot(null), quoteSnapshot(0)]) {
    const result = dayQuote(snapshot, { close: 21500 });
    expect(result.price).toBe(21500);
    expect(result.change).toBeNull();
  }
  expect(dayQuote(null, null).price).toBeNull();
});

test("old quotes show their session date, with day boundaries evaluated in India", () => {
  const beforeMidnight = Date.parse("2026-11-01T18:29:59Z");
  const afterMidnight = Date.parse("2026-11-01T18:30:00Z");
  expect(dayQuote(quoteSnapshot(21231.1, 21000, beforeMidnight), null, afterMidnight).text).toContain("01 Nov 2026");
  expect(dayQuote(quoteSnapshot(21231.1, 21000, afterMidnight), null, afterMidnight).text).toContain("Today");
});

test("freshness degrades with wall time and respects server stale status", () => {
  const snapshot = { connected: true, status: "live", last_tick_age_ms: 500 };
  expect(streamFreshness(snapshot, "open", now, now).label).toBe("Fresh tick");
  expect(streamFreshness(snapshot, "open", now, now + 16000).label).toBe("Stale tick");
  expect(streamFreshness({ ...snapshot, status: "stale" }, "open", now, now).label).toBe("Stale tick");
  expect(streamFreshness(snapshot, "reconnecting", now, now).label).toBe("Reconnecting");
  expect(streamFreshness({ connected: true }, "open", now, now).label).toBe("Tick freshness unknown");
});

test("positions match exact contract and keep broker/paper independent without journal duplication", () => {
  const row = { id: "one", instrument_key: instrument.instrument_key, origin: "broker" };
  const rows = [row, { ...row }, { ...row, origin: "paper" }, { ...row, instrument_key: "NSE_FO|other" }];
  expect(matchingPositions(rows, instrument)).toHaveLength(2);
  expect(matchingPositions(rows, instrument, "broker")).toEqual([row]);
  expect(matchingPositions([{ ...row, expiry: "2026-11-12" }], { ...instrument, expiry: "2026-11-05" })).toEqual([]);
});

test("stale broker, stale mark or failed snapshot prevents a fresh position label", () => {
  expect(positionFresh({ fresh: true, mark_age_ms: 500, broker_age_ms: 15000 }, now, now + 5000)).toBe(true);
  expect(positionFresh({ fresh: true, mark_age_ms: 500, broker_age_ms: 29000 }, now, now + 2000)).toBe(false);
  expect(positionFresh({ fresh: true }, 0, now)).toBe(false);
  expect(positionFresh({ fresh: false }, now, now)).toBe(false);
});

test("drawings persist semantic anchors with full identity across reload", () => {
  const rows = [{ id: "line-one", kind: "trend-line", points: [{ time: 100, price: 20 }, { time: 200, price: 30 }], style: { color: "#fff", width: 2 } }];
  expect(saveDrawingEnvelope(localStorage, instrument, rows)).toEqual({ ok: true, warning: "" });
  const loaded = readDrawingEnvelope(localStorage, instrument);
  expect(loaded.writable).toBe(true); expect(loaded.drawings[0].anchors).toEqual(rows[0].points); expect(loaded.drawings[0].type).toBe("trend-line");
  expect(loaded.drawings[0].instrument.instrument_key).toBe(instrument.instrument_key);
});

test("corrupt or foreign drawing envelopes are retained and disable overwrite", () => {
  const key = drawingStorageKey(instrument); localStorage.setItem(key, "broken");
  expect(readDrawingEnvelope(localStorage, instrument).writable).toBe(false); expect(localStorage.getItem(key)).toBe("broken");
  localStorage.setItem(key, JSON.stringify({ schema_version: 1, instrument: INDEX_SHORTCUTS[1], drawings: [] }));
  expect(readDrawingEnvelope(localStorage, instrument).writable).toBe(false);
});

test("quota errors and invalid anchors are surfaced without claiming a save", () => {
  const storage = { setItem: () => { throw new Error("QuotaExceededError"); } };
  expect(saveDrawingEnvelope(storage, instrument, []).ok).toBe(false);
  expect(saveDrawingEnvelope(localStorage, instrument, [{ id: "bad", kind: "horizontal-line", points: [{ x: 10, y: 20 }] }]).ok).toBe(false);
  expect(localStorage.getItem(drawingStorageKey(instrument))).toBeNull();
});
