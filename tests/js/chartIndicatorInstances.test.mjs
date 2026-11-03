import test from "node:test";
import assert from "node:assert/strict";
import { computeIndicators } from "../../frontend/src/lib/chartIndicators.js";
import { defaultIndicators, newIndicator, readIndicatorSettings, saveIndicatorSettings, INDICATOR_STORAGE_KEY } from "../../frontend/src/lib/chartIndicatorConfig.js";
const bars = Array.from({ length: 90 }, (_, i) => ({ time: 1700000000 + i * 60, open: i + 100, high: i + 102, low: i + 99, close: i + 101, volume: 10 }));

test("EMA20 and EMA50 compute separately; removal and disable preserve the remaining identity", () => {
  const first = defaultIndicators()[0];
  const second = { ...newIndicator("ema", [first]), params: { period: 50 }, lineWidth: 3 };
  const output = computeIndicators(bars, [first, second]);
  assert.equal(output.series.length, 2);
  assert.equal(output.series[0].name, "EMA 20"); assert.equal(output.series[1].name, "EMA 50");
  assert.notEqual(output.series[0].id, output.series[1].id);
  // Linear closes 101..190: SMA-seeded EMA equals last close minus (period-1)/2.
  assert.ok(Math.abs(output.series[0].data.at(-1).value - 180.5) < 1e-10);
  assert.ok(Math.abs(output.series[1].data.at(-1).value - 165.5) < 1e-10);
  assert.notEqual(output.series[0].color, output.series[1].color);
  assert.deepEqual(computeIndicators(bars, [second]).series[0], output.series[1]);
  assert.deepEqual(computeIndicators(bars, [{ ...first, enabled: false }, second]).series, [output.series[1]]);
});

test("repeated oscillator instances own distinct panes and child series ids", () => {
  for (const type of ["rsi", "macd", "adx"]) {
    const one = newIndicator(type), two = newIndicator(type, [one]);
    const output = computeIndicators(bars, [one, two]);
    assert.equal(new Set(output.series.map(row => row.pane)).size, 2);
    assert.equal(new Set(output.series.map(row => row.id)).size, output.series.length);
  }
});

test("indicator settings persist exact instances; malformed data is preserved and invalid writes refused", () => {
  const data = new Map(); const storage = { getItem: key => data.get(key) || null, setItem: (key, value) => data.set(key, value) };
  const first = defaultIndicators()[0], second = { ...newIndicator("sma", [first]), params: { period: 50 }, color: "#123456" };
  assert.equal(saveIndicatorSettings(storage, [first, second]).ok, true);
  assert.deepEqual(readIndicatorSettings(storage).configs, [first, second]);
  const before = data.get(INDICATOR_STORAGE_KEY);
  assert.equal(saveIndicatorSettings(storage, [first, first]).ok, false);
  assert.equal(data.get(INDICATOR_STORAGE_KEY), before);
  data.set(INDICATOR_STORAGE_KEY, "broken");
  assert.equal(readIndicatorSettings(storage).writable, false);
  assert.equal(data.get(INDICATOR_STORAGE_KEY), "broken");
});
