import assert from "node:assert/strict";
import { test } from "node:test";
import { normalizeBars, mergeBars, instrumentIdentity, drawingStorageKey, normalizeDrawings } from "../../frontend/src/lib/chartModel.js";

const bar = (time, close = 10, volume = 100) => ({ time, open: close, high: close + 2, low: close - 2, close, volume });
const instrument = { instrument_key: "NSE_FO|12345", kind: "option", exchange: "NSE", segment: "NSE_FO", symbol: "NIFTY", expiry: "2026-11-03", strike: 25000, option_type: "CE" };
const pluginDrawing = { id: "first", kind: "trend-line", points: [{ time: 100, price: 250 }, { time: 160, price: 275 }], style: { color: "#f00", width: 2, lineStyle: "dashed" } };

test("bars become sorted unique finite UTC-second OHLCV; last valid duplicate wins without mutation", () => {
  const rows = [bar(120), bar(60), { ...bar(120, 11), close: "11", time: "120", volume: "0" }, { ...bar(120), high: 0 }];
  const before = structuredClone(rows);
  assert.deepEqual(normalizeBars(rows), [bar(60), bar(120, 11, 0)]);
  assert.deepEqual(rows, before);
  assert.notEqual(normalizeBars(rows)[0], rows[1]);
});

test("invalid candles and millisecond timestamps are dropped, missing/invalid volume stays null", () => {
  const invalid = [null, {}, [], bar(-1), bar(1.5), bar(1700000000000), bar(Infinity),
    { ...bar(1), open: "" }, { ...bar(2), high: Infinity }, { ...bar(3), close: null },
    { ...bar(4), low: 20 }, { ...bar(5), high: 8 }, { ...bar(6), open: true }];
  assert.deepEqual(normalizeBars(invalid), []);
  const volumes = [null, undefined, "", -1, NaN, Infinity, false];
  assert.ok(normalizeBars(volumes.map((volume, index) => ({ ...bar(index), volume }))).every(row => row.volume === null));
  assert.equal(normalizeBars([bar(0, 0, 0)])[0].volume, 0);
  for (const input of [null, {}, "bad"]) assert.deepEqual(normalizeBars(input), []);
});

test("full-bar merges replace timestamps, preserve gaps, and retain only the newest bounded history", () => {
  const prior = [bar(60), bar(120, 11), bar(240, 12)];
  const updates = [bar(120, 15, null), bar(300, 16)];
  const snapshot = structuredClone([prior, updates]);
  assert.deepEqual(mergeBars(prior, updates, 3), [bar(120, 15, null), bar(240, 12), bar(300, 16)]);
  assert.deepEqual(mergeBars(prior, bar(300), 1), [bar(300)]);
  assert.deepEqual(mergeBars(prior, updates, 0), []);
  assert.deepEqual(mergeBars(prior, updates, -1), []);
  assert.equal(mergeBars(prior, [], NaN).length, 3);
  assert.deepEqual([prior, updates], snapshot);
});

test("drawing identity includes expiry and contract fields; reused derivative token cannot cross expiry", () => {
  assert.equal(instrumentIdentity({ ...instrument, expiry: null }), null);
  assert.equal(instrumentIdentity({ ...instrument, kind: "future", expiry: "" }), null);
  assert.equal(instrumentIdentity({ instrument_key: "NSE_INDEX|Nifty 50", kind: "index" }).expiry, null);
  assert.equal(drawingStorageKey(null), null);
  assert.notEqual(drawingStorageKey(instrument), drawingStorageKey({ ...instrument, expiry: "2026-11-10" }));
  assert.notEqual(drawingStorageKey(instrument), drawingStorageKey({ ...instrument, strike: 25100 }));
  assert.notEqual(drawingStorageKey(instrument), drawingStorageKey({ ...instrument, option_type: "PE" }));
  assert.deepEqual(instrumentIdentity({ ...instrument, kind: "OPTION", strike: "25000", option_type: "ce" }), instrumentIdentity(instrument));
});

test("semantic drawings retain time/price anchors and allowlisted style, never plugin internals or pixels", () => {
  const raw = { ...pluginDrawing, pixelX: 123, canvas: "ignored", internal: { circular: true }, style: {
    ...pluginDrawing.style, onClick: "ignored", text: "label", reverse: false, transparency: 25,
    levels: [{ coeff: 0.618, color: "#fff", visible: true, unknown: 1 }, { coeff: Infinity, color: "red" }],
  } };
  const [saved] = normalizeDrawings([raw], instrument);
  assert.deepEqual(Object.keys(saved), ["schema_version", "id", "type", "instrument", "anchors", "style"]);
  assert.equal(saved.schema_version, 1);
  assert.deepEqual(saved.anchors, pluginDrawing.points);
  assert.notEqual(saved.anchors, pluginDrawing.points);
  assert.equal(saved.style.onClick, undefined);
  assert.deepEqual(saved.style.levels, [{ coeff: 0.618, color: "#fff", visible: true }]);
  assert.deepEqual(normalizeDrawings([saved], instrument), [saved]);
  assert.deepEqual(normalizeDrawings([saved], { ...instrument, expiry: "2026-11-10" }), []);
});

test("malformed/foreign drawings fail closed, valid supported tools and duplicate replacement survive", () => {
  const malformed = [
    { ...pluginDrawing, schema_version: 2 }, { ...pluginDrawing, id: "" }, { ...pluginDrawing, kind: "unknown" },
    { ...pluginDrawing, points: [{ time: 100, price: 1 }] },
    { ...pluginDrawing, points: [{ time: 100, price: Infinity }, { time: 160, price: 275 }] },
    { ...pluginDrawing, points: [{ time: 1700000000000, price: 1 }, { time: 160, price: 2 }] },
    { ...pluginDrawing, instrument: { ...instrument, instrument_key: "NSE_FO|OTHER" } },
  ];
  assert.deepEqual(normalizeDrawings(malformed, instrument), []);
  assert.deepEqual(normalizeDrawings([pluginDrawing], { ...instrument, expiry: null }), []);
  const tools = ["horizontal-line", "vertical-line", "text", "trend-line", "rectangle", "fib-retracement"];
  const input = tools.map((kind, index) => ({ ...pluginDrawing, id: String(index), kind, points: pluginDrawing.points.slice(0, index < 3 ? 1 : 2) }));
  assert.equal(normalizeDrawings(input, instrument).length, tools.length);
  const result = normalizeDrawings([pluginDrawing, { ...pluginDrawing, style: { width: 3 } }], instrument);
  assert.equal(result.length, 1);
  assert.equal(result[0].style.width, 3);
});

test("extended drawing tools round-trip semantic anchors and styling without changing legacy rows", () => {
  const types = ["horizontal-ray", "vertical-line", "arrow-mark-up", "arrow-mark-down", "ray", "arrow", "date-and-price-range"];
  const input = types.map((kind, i) => ({ ...pluginDrawing, id: kind, kind,
    points: pluginDrawing.points.slice(0, i < 4 ? 1 : 2),
    style: { color: "#123456", width: 4, lineStyle: "dashed", extendRight: true, rightEnd: 1, showStats: true, showBarsRange: true } }));
  const saved = normalizeDrawings([pluginDrawing, ...input], instrument);
  assert.equal(saved.length, 8);
  assert.deepEqual(normalizeDrawings(saved, instrument), saved);
  assert.deepEqual(saved[0], normalizeDrawings([pluginDrawing], instrument)[0]);
  assert.equal(saved.at(-1).style.showBarsRange, true);
  assert.equal(saved.at(-1).style.lineStyle, "dashed");
});
