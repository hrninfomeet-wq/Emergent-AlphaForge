import assert from "node:assert/strict";
import { test } from "node:test";
import { computeIndicators, INDICATOR_DEFINITIONS } from "../../frontend/src/lib/chartIndicators.js";

const bars = (closes) => closes.map((close, index) => ({ time: 1700000000 + index * 60, open: close, high: close + 1, low: close - 1, close, volume: index + 1 }));
const config = (id, params = {}) => ({ id, enabled: true, params });
const values = (result, id) => result.series.find(series => series.id === id).data.map(point => point.value);
function near(actual, expected, tolerance = 1e-10) {
  assert.equal(actual.length, expected.length);
  actual.forEach((value, index) => assert.ok(Math.abs(value - expected[index]) <= tolerance, `${index}: ${value} != ${expected[index]}`));
}

test("definitions pin agreed defaults; disabled/unknown/invalid/duplicate configs cannot create misleading series", () => {
  assert.deepEqual(Object.fromEntries(INDICATOR_DEFINITIONS.map(item => [item.id, item.defaults])), {
    volume: {}, vwap: {}, ema: { period: 20 }, sma: { period: 20 }, bb: { period: 20, multiplier: 2 },
    rsi: { period: 14 }, macd: { fast: 12, slow: 26, signal: 9 }, supertrend: { period: 10, multiplier: 3 }, atr: { period: 14 }, adx: { period: 14 },
  });
  const result = computeIndicators(bars([1, 2, 3]), [
    { id: "rsi", enabled: false }, config("unknown"), config("ema", { period: 0 }), config("sma", { period: 2 }),
    config("sma", { period: 3 }), config("macd", { fast: 26, slow: 12 }), config("bb", { multiplier: Infinity }),
  ], true);
  assert.deepEqual(result.series.map(series => series.id), ["sma"]);
  assert.equal(result.warnings.length, 5);
  for (const period of [null, "", false, 1.5, -1, NaN, 10001]) {
    assert.equal(computeIndicators([], [config("ema", { period })], true).series.length, 0);
  }
  assert.deepEqual(computeIndicators([], null), { series: [], warnings: [] });
});

test("EMA uses a complete SMA seed and SMA/BB use observed closes without premature warmup", () => {
  const input = bars([2, 4, 8, 10, 9, 5]);
  const result = computeIndicators(input, [config("ema", { period: 3 }), config("sma", { period: 3 }), config("bb", { period: 3, multiplier: 2 })]);
  // First seed (2+4+8)/3=14/3. EMA alpha=1/2: 22/3,49/6,79/12.
  near(values(result, "ema"), [14 / 3, 22 / 3, 49 / 6, 79 / 12]);
  near(values(result, "sma"), [14 / 3, 22 / 3, 9, 8]);
  // Population variances of the four windows: 56/9,56/9,2/3,14/3.
  const centers = [14 / 3, 22 / 3, 9, 8];
  const deviations = [Math.sqrt(56) / 3, Math.sqrt(56) / 3, Math.sqrt(2 / 3), Math.sqrt(14 / 3)];
  near(values(result, "bb:middle"), centers);
  near(values(result, "bb:upper"), centers.map((center, index) => center + 2 * deviations[index]));
  near(values(result, "bb:lower"), centers.map((center, index) => center - 2 * deviations[index]));
  result.series.forEach(series => assert.equal(series.data[0].time, input[2].time));
  const short = computeIndicators(input.slice(0, 2), [config("ema", { period: 3 }), config("bb", { period: 3 })]);
  assert.ok(short.series.every(series => series.data.length === 0));
});

test("Bollinger variance survives large price offsets and rolling-window replacement", () => {
  const input = bars(Array.from({ length: 400 }, (_, index) => 1000000000 + index % 5));
  const result = computeIndicators(input, [config("bb", { period: 5, multiplier: 2 })]);
  // Every five-element window is {B,B+1,B+2,B+3,B+4}: mean B+2; variance 2.
  near(values(result, "bb:middle"), Array(396).fill(1000000002), 1e-6);
  near(values(result, "bb:upper"), Array(396).fill(1000000002 + 2 * Math.sqrt(2)), 1e-6);
});

test("RSI uses Wilder's first period price changes and recursive gain/loss means", () => {
  const input = bars([10, 12, 11, 15, 14, 14, 18]);
  const result = computeIndicators(input, [config("rsi", { period: 3 })]);
  // Gain/loss seed=2,1/3. Next=4/3,5/9; then 8/9,10/27; then 52/27,20/81.
  near(values(result, "rsi"), [600 / 7, 1200 / 17, 1200 / 17, 975 / 11]);
  assert.equal(result.series[0].data[0].time, input[3].time);
  for (const [closes, expected] of [[[2, 3, 4, 5], 100], [[5, 4, 3, 2], 0], [[5, 5, 5, 5], 50]]) {
    assert.deepEqual(values(computeIndicators(bars(closes), [config("rsi", { period: 3 })]), "rsi"), [expected]);
  }
});

const directionalFixture = () => [
  [10, 8, 9], [12, 9, 11], [11, 7, 8], [14, 10, 13], [13, 9, 10], [15, 11, 14],
].map(([high, low, close], index) => ({ time: 1700000000 + index * 60, open: close, high, low, close, volume: 10 }));

test("ATR true range includes gaps and uses an arithmetic Wilder seed, then alpha=1/period", () => {
  const result = computeIndicators(directionalFixture(), [config("atr", { period: 3 })]);
  // TR is [2,3,4,6,4,5]; seed=(2+3+4)/3=3; recurrence gives 4,4,13/3.
  near(values(result, "atr"), [3, 4, 4, 13 / 3]);
  assert.equal(result.series[0].data[0].time, directionalFixture()[2].time);
});

test("ADX seeds from a full period of DX after DM warmup, with independent fractional fixture", () => {
  const input = directionalFixture();
  const result = computeIndicators(input, [config("adx", { period: 2 })]);
  // First two transitions: TR=3,4; +DM=2,0; -DM=0,2. DI=200/7 each.
  // Subsequent DX values: 60,100/7,60. Seed=(0+60)/2=30.
  // Follow-on ADX=(30+100/7)/2=155/7; then (155/7+60)/2=575/14.
  near(values(result, "adx:plus"), [200 / 7, 800 / 19, 160 / 7, 32]);
  near(values(result, "adx:minus"), [200 / 7, 200 / 19, 120 / 7, 8]);
  near(values(result, "adx"), [30, 155 / 7, 575 / 14]);
  assert.equal(result.series.find(series => series.id === "adx").data[0].time, input[3].time);
  const tied = [
    { time: 1, open: 5, high: 6, low: 4, close: 5 },
    { time: 2, open: 5, high: 7, low: 3, close: 5 },
    { time: 3, open: 5, high: 7, low: 3, close: 5 },
    { time: 4, open: 5, high: 7, low: 3, close: 5 },
  ];
  const flat = computeIndicators(tied, [config("adx", { period: 2 })]);
  assert.deepEqual(values(flat, "adx"), [0]);
  assert.deepEqual(values(flat, "adx:plus"), [0, 0]);
  assert.deepEqual(values(flat, "adx:minus"), [0, 0]);
});

test("MACD uses separate EMA seeds and a signal EMA seeded only from available MACD observations", () => {
  const input = bars([2, 4, 8, 10, 9, 5, 7]);
  const result = computeIndicators(input, [config("macd", { fast: 2, slow: 3, signal: 2 })]);
  // Fractions independently derived with Python Fraction, no production helpers.
  near(values(result, "macd:line"), [5 / 3, 13 / 9, 41 / 54, -89 / 324, -43 / 1944]);
  near(values(result, "macd:signal"), [14 / 9, 83 / 81, 77 / 486, 37 / 972]);
  near(values(result, "macd:histogram"), [-1 / 9, -43 / 162, -421 / 972, -13 / 216]);
  assert.equal(result.series.find(series => series.id === "macd:signal").data[0].time, input[3].time);
  assert.ok(result.series.every(series => series.pane === "macd"));
});

test("Supertrend trails final bands and flips both ways, using Wilder ATR", () => {
  const result = computeIndicators(bars([10, 11, 15, 14, 8, 9, 14]), [config("supertrend", { period: 2, multiplier: 1 })]);
  // Seed ATR=2, initial down band=11+2=13. Next ATR=3.5 and up band=15-3.5.
  // Following ATR=2.75,4.875,3.4375,4.71875. Final bands ratchet through flips.
  near(values(result, "supertrend"), [13, 11.5, 11.5, 12.875, 12.4375, 9.28125]);
  assert.deepEqual(result.series[0].data.map(point => point.color), ["#f87171", "#22c55e", "#22c55e", "#f87171", "#f87171", "#22c55e"]);
});

test("index/unknown volume never fabricates volume or turns VWAP into an arithmetic mean", () => {
  const configs = [config("volume"), config("vwap")];
  for (const available of [false, undefined, null, "true"]) {
    const result = computeIndicators(bars([10, 30, 50]), configs, available);
    assert.deepEqual(result.series, []);
    assert.equal(result.warnings.length, 2);
    assert.ok(result.warnings.every(warning => warning.includes("unavailable")));
  }
});

test("VWAP uses HLC3 and volume, resets on IST date, and preserves genuine zero volume", () => {
  const time = Date.parse("2026-10-31T18:27:00Z") / 1000;
  const input = [
    { time, open: 10, high: 14, low: 8, close: 14, volume: 2 }, // typical 12
    { time: time + 60, open: 18, high: 22, low: 16, close: 22, volume: 6 }, // typical 20
    { time: time + 120, open: 100, high: 100, low: 100, close: 100, volume: 0 },
    { time: time + 180, open: 40, high: 40, low: 40, close: 40, volume: 3 }, // next IST day, same UTC date
  ];
  const result = computeIndicators(input, [config("vwap"), config("volume")], true);
  near(values(result, "vwap"), [12, 18, 18, 40]); // (12*2+20*6)/(2+6)=18, not mean 16.
  assert.deepEqual(values(result, "volume"), [2, 6, 0, 3]);
  assert.ok(result.warnings.some(warning => warning.includes("session start")));
});

test("missing volume invalidates the remaining session VWAP and zero-start session stays empty", () => {
  const input = bars([10, 20, 30, 40, 50, 60]);
  const first = Date.parse("2026-10-31T03:45:00Z") / 1000;
  input.forEach((bar, index) => { bar.time = first + index * 60; });
  input[0].volume = 0;
  input[1].volume = 2;
  input[2].volume = null;
  input[3].volume = 3;
  input[4].time = first + 86400;
  input[4].volume = 0;
  input[5].time = first + 86460;
  input[5].volume = 4;
  const result = computeIndicators(input, [config("vwap")], true);
  const data = result.series[0].data;
  assert.deepEqual(data, [{ time: input[1].time, value: 20 }, { time: input[2].time }, { time: input[3].time }, { time: input[4].time }, { time: input[5].time, value: 60 }]);
  assert.ok(result.warnings.some(warning => warning.includes("missing volume")));
  const empty = computeIndicators(input.map(bar => ({ ...bar, volume: 0 })), [config("vwap")], true);
  assert.deepEqual(empty.series[0].data, []);
});

test("empty, one-bar, flat and invalid input remain immutable and produce only sorted finite plotted values", () => {
  const configs = INDICATOR_DEFINITIONS.map(definition => config(definition.id));
  for (const input of [[], bars([10]), bars(Array(80).fill(5)), [...bars([10, 11, 12]).reverse(), { time: 1 }]]) {
    const before = structuredClone(input);
    const result = computeIndicators(input, configs, true);
    assert.deepEqual(input, before);
    for (const series of result.series) {
      assert.ok(["price", ...configs.map(item => item.id)].includes(series.pane));
      assert.ok(["line", "histogram"].includes(series.type));
      series.data.forEach((point, index) => {
        assert.ok(point.value === undefined || Number.isFinite(point.value));
        assert.ok(index === 0 || point.time > series.data[index - 1].time);
      });
    }
  }
  const zeroRange = bars(Array(35).fill(0)).map(bar => ({ ...bar, high: 0, low: 0 }));
  const result = computeIndicators(zeroRange, [config("atr"), config("adx"), config("rsi")]);
  assert.ok(values(result, "atr").every(value => value === 0));
  assert.ok(values(result, "adx").every(value => value === 0));
  assert.ok(values(result, "rsi").every(value => value === 50));
});
