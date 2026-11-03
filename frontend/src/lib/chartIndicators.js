import { normalizeBars } from "./chartModel.js";

// Display calculations only: these never feed the strategy or order engines.
// Seeds/conventions are explicit because libraries differ at the left edge.
// EMA and Wilder RMA use an SMA seed; BB uses population standard deviation.
// ATR includes first-bar H-L; RSI/DM start with the first observed price change.
export const INDICATOR_DEFINITIONS = Object.freeze([
  { id: "volume", name: "Volume", defaults: {} },
  { id: "vwap", name: "Session VWAP", defaults: {} },
  { id: "ema", name: "EMA", defaults: { period: 20 } },
  { id: "sma", name: "SMA", defaults: { period: 20 } },
  { id: "bb", name: "Bollinger Bands", defaults: { period: 20, multiplier: 2 } },
  { id: "rsi", name: "RSI", defaults: { period: 14 } },
  { id: "macd", name: "MACD", defaults: { fast: 12, slow: 26, signal: 9 } },
  { id: "supertrend", name: "Supertrend", defaults: { period: 10, multiplier: 3 } },
  { id: "atr", name: "ATR", defaults: { period: 14 } },
  { id: "adx", name: "ADX", defaults: { period: 14 } },
].map(item => Object.freeze({ ...item, defaults: Object.freeze(item.defaults) })));

const GREEN = "#22c55e";
const RED = "#f87171";
const COLORS = { ema: "#fbbf24", sma: "#38bdf8", bb: "#a78bfa", rsi: "#c084fc",
  macd: "#38bdf8", signal: "#fb923c", atr: "#fbbf24", adx: "#e879f9", vwap: "#f472b6" };
const IST_SECONDS = 5.5 * 60 * 60;
const definitions = new Map(INDICATOR_DEFINITIONS.map(item => [item.id, item]));

function parameters(definition, supplied) {
  const params = { ...definition.defaults };
  for (const key of Object.keys(params)) {
    const value = supplied?.[key] === undefined ? params[key] : supplied[key];
    if ((typeof value !== "number" && typeof value !== "string") || String(value).trim() === "") return null;
    const number = Number(value);
    if (!Number.isFinite(number) || number <= 0) return null;
    if (key === "multiplier" ? number > 1000 : !Number.isInteger(number) || number > 10000) return null;
    params[key] = number;
  }
  if (definition.id === "macd" && params.fast >= params.slow) return null;
  return params;
}

function mean(values) {
  // Divide before summing to avoid overflowing on otherwise finite prices.
  return values.reduce((sum, value) => sum + value / values.length, 0);
}

function smooth(values, period, alpha) {
  const result = Array(values.length).fill(null);
  let seed = [];
  let prior = null;
  for (let index = 0; index < values.length; index += 1) {
    const value = values[index];
    if (!Number.isFinite(value)) { seed = []; prior = null; continue; }
    if (prior === null) {
      seed.push(value);
      if (seed.length < period) continue;
      prior = mean(seed);
      seed = [];
    } else {
      prior = prior * (1 - alpha) + value * alpha;
    }
    result[index] = prior;
  }
  return result;
}

const ema = (values, period) => smooth(values, period, 2 / (period + 1));
const rma = (values, period) => smooth(values, period, 1 / period);

function rollingMoments(values, period) {
  const averages = Array(values.length).fill(null);
  const deviations = Array(values.length).fill(null);
  let average = 0;
  let m2 = 0;
  for (let index = period - 1; index < values.length; index += 1) {
    // Rebase regularly to bound floating-point drift, using centered differences
    // instead of E[x²]-E[x]² (which loses small spreads on large price levels).
    if ((index - period + 1) % 128 === 0 || period === 1) {
      const window = values.slice(index - period + 1, index + 1);
      average = mean(window);
      m2 = window.reduce((sum, value) => sum + (value - average) ** 2, 0);
    } else {
      const removed = values[index - period];
      const added = values[index];
      const priorAverage = average;
      average += (added - removed) / period;
      m2 += (added - removed) * (added - average + removed - priorAverage);
    }
    averages[index] = average;
    deviations[index] = Math.sqrt(Math.max(0, m2) / period);
  }
  return { averages, deviations };
}

function trueRanges(bars) {
  return bars.map((bar, index) => index === 0 ? bar.high - bar.low : Math.max(
    bar.high - bar.low, Math.abs(bar.high - bars[index - 1].close), Math.abs(bar.low - bars[index - 1].close),
  ));
}

function relativeStrength(closes, period) {
  const gains = [null];
  const losses = [null];
  for (let index = 1; index < closes.length; index += 1) {
    const change = closes[index] - closes[index - 1];
    gains.push(Math.max(0, change));
    losses.push(Math.max(0, -change));
  }
  const up = rma(gains, period);
  const down = rma(losses, period);
  return closes.map((_, index) => {
    if (up[index] === null || down[index] === null) return null;
    // A completely flat seed is neutral, rather than NaN or a false extreme.
    if (up[index] === 0 && down[index] === 0) return 50;
    if (down[index] === 0) return 100;
    return 100 - 100 / (1 + up[index] / down[index]);
  });
}

function directionalMovement(bars, period) {
  const positive = [null];
  const negative = [null];
  const ranges = trueRanges(bars);
  ranges[0] = null; // Directional movement needs a previous bar.
  for (let index = 1; index < bars.length; index += 1) {
    const up = bars[index].high - bars[index - 1].high;
    const down = bars[index - 1].low - bars[index].low;
    positive.push(up > down && up > 0 ? up : 0);
    negative.push(down > up && down > 0 ? down : 0);
  }
  const tr = rma(ranges, period);
  const plus = rma(positive, period);
  const minus = rma(negative, period);
  const plusDI = tr.map((value, index) => value === null ? null : value === 0 ? 0 : 100 * (plus[index] / value));
  const minusDI = tr.map((value, index) => value === null ? null : value === 0 ? 0 : 100 * (minus[index] / value));
  const dx = plusDI.map((value, index) => {
    if (value === null) return null;
    const total = value + minusDI[index];
    return total === 0 ? 0 : 100 * Math.abs(value - minusDI[index]) / total;
  });
  return { plusDI, minusDI, adx: rma(dx, period) };
}

// Recurrence and initial down direction follow TradingView's published bands:
// https://www.tradingview.com/support/solutions/43000634738-supertrend/
function supertrend(bars, period, multiplier) {
  const atr = rma(trueRanges(bars), period);
  const values = Array(bars.length).fill(null);
  const colors = Array(bars.length).fill(RED);
  let upper = null;
  let lower = null;
  let uptrend = false;
  for (let index = 0; index < bars.length; index += 1) {
    if (atr[index] === null) continue;
    const middle = bars[index].high / 2 + bars[index].low / 2;
    const basicUpper = middle + multiplier * atr[index];
    const basicLower = middle - multiplier * atr[index];
    if (upper === null) {
      upper = basicUpper;
      lower = basicLower;
    } else {
      upper = basicUpper < upper || bars[index - 1].close > upper ? basicUpper : upper;
      lower = basicLower > lower || bars[index - 1].close < lower ? basicLower : lower;
      uptrend = uptrend ? bars[index].close >= lower : bars[index].close > upper;
    }
    values[index] = uptrend ? lower : upper;
    colors[index] = uptrend ? GREEN : RED;
  }
  return { values, colors };
}

function sessionVwap(bars) {
  const values = [];
  let session = null;
  let volume = 0;
  let average = null;
  let incomplete = false;
  for (const bar of bars) {
    const day = Math.floor((bar.time + IST_SECONDS) / 86400);
    if (day !== session) { session = day; volume = 0; average = null; incomplete = false; }
    if (bar.volume === null) incomplete = true;
    if (!incomplete && bar.volume > 0) {
      const typical = bar.high / 3 + bar.low / 3 + bar.close / 3;
      const nextVolume = volume + bar.volume;
      average = average === null ? typical : average * (volume / nextVolume) + typical * (bar.volume / nextVolume);
      volume = nextVolume;
    }
    values.push(incomplete ? null : average);
  }
  return values;
}

/**
 * Configs: [{ id: type, instanceId?, enabled, params, color?, lineWidth? }].
 * Legacy rows use their type as identity; new rows have independent identities.
 * Every series has a stable id, name, pane, type, data and color. Warmup emits
 * no point; missing interior data uses chart whitespace to avoid joining gaps.
 * VWAP is HLC3 weighted by observed volume and resets at midnight Asia/Kolkata.
 * Missing volume invalidates that session's remaining VWAP; it is never an SMA.
 */
export function computeIndicators(rows, configs = [], volumeAvailable = false) {
  const bars = normalizeBars(rows);
  const closes = bars.map(bar => bar.close);
  const series = [];
  const warnings = new Set();
  const seen = new Set();
  let currentConfig;
  function add(id, name, pane, values, color, type = "line", colors = []) {
    const data = [];
    let started = false;
    values.forEach((value, index) => {
      if (!bars[index]) return;
      if (Number.isFinite(value)) {
        started = true;
        data.push({ time: bars[index].time, value, ...(colors[index] ? { color: colors[index] } : {}) });
      } else if (value !== null && value !== undefined) {
        warnings.add(`${name}: a non-finite calculation was omitted.`);
        if (started) data.push({ time: bars[index].time });
      } else if (started) data.push({ time: bars[index].time });
    });
    const instance = currentConfig.instanceId || currentConfig.id;
    const seriesId = `${instance}${id.slice(currentConfig.id.length)}`;
    const primary = id === currentConfig.id || id.endsWith(":line") || currentConfig.id === "bb";
    const customColor = /^#[0-9a-f]{6}$/i.test(currentConfig.color || "") ? currentConfig.color : null;
    const lineWidth = [1, 2, 3, 4].includes(currentConfig.lineWidth) ? currentConfig.lineWidth : 1;
    series.push({ id: seriesId, name, pane: pane === "price" ? pane : instance, type, data,
      color: primary && customColor ? customColor : color, lineWidth, indicatorId: currentConfig.id });
  }
  for (const config of Array.isArray(configs) ? configs : []) {
    if (!config?.enabled) continue;
    const definition = definitions.get(config.id);
    if (!definition) { warnings.add(`Unknown indicator: ${String(config.id)}.`); continue; }
    const instance = config.instanceId || config.id;
    if (seen.has(instance)) { warnings.add(`${definition.name}: duplicate configuration ignored.`); continue; }
    seen.add(instance);
    currentConfig = config;
    const params = parameters(definition, config.params);
    if (!params) { warnings.add(`${definition.name}: invalid parameters; indicator omitted.`); continue; }
    const { period, multiplier } = params;
    const id = config.id;
    if (id === "volume" || id === "vwap") {
      if (volumeAvailable !== true) { warnings.add(`${definition.name} unavailable: this instrument has no traded volume.`); continue; }
      if (bars.some(bar => bar.volume === null)) warnings.add(`${definition.name}: some bars have missing volume.${id === "vwap" ? " VWAP is unavailable from each missing bar until the next IST session." : ""}`);
      if (id === "volume") add(id, "Volume", id, bars.map(bar => bar.volume), GREEN, "histogram", bars.map(bar => bar.close >= bar.open ? GREEN : RED));
      else {
        warnings.add("Session VWAP uses loaded bars within each IST date; full-session VWAP requires history from the session start and intraday bars.");
        add(id, "Session VWAP (loaded bars)", "price", sessionVwap(bars), COLORS.vwap);
      }
    } else if (id === "ema") add(id, `EMA ${period}`, "price", ema(closes, period), COLORS.ema);
    else if (id === "sma") add(id, `SMA ${period}`, "price", rollingMoments(closes, period).averages, COLORS.sma);
    else if (id === "bb") {
      const { averages, deviations } = rollingMoments(closes, period);
      add("bb:middle", `BB ${period} middle`, "price", averages, COLORS.bb);
      add("bb:upper", `BB ${period}/${multiplier} upper`, "price", averages.map((value, index) => value === null ? null : value + multiplier * deviations[index]), COLORS.bb);
      add("bb:lower", `BB ${period}/${multiplier} lower`, "price", averages.map((value, index) => value === null ? null : value - multiplier * deviations[index]), COLORS.bb);
    } else if (id === "rsi") add(id, `RSI ${period}`, id, relativeStrength(closes, period), COLORS.rsi);
    else if (id === "atr") add(id, `ATR ${period}`, id, rma(trueRanges(bars), period), COLORS.atr);
    else if (id === "macd") {
      const fast = ema(closes, params.fast);
      const slow = ema(closes, params.slow);
      const line = closes.map((_, index) => fast[index] === null || slow[index] === null ? null : fast[index] - slow[index]);
      const signal = ema(line, params.signal);
      const histogram = line.map((value, index) => value === null || signal[index] === null ? null : value - signal[index]);
      add("macd:line", `MACD ${params.fast}/${params.slow}`, id, line, COLORS.macd);
      add("macd:signal", `MACD signal ${params.signal}`, id, signal, COLORS.signal);
      add("macd:histogram", "MACD histogram", id, histogram, GREEN, "histogram", histogram.map(value => value >= 0 ? GREEN : RED));
    } else if (id === "adx") {
      const values = directionalMovement(bars, period);
      add(id, `ADX ${period}`, id, values.adx, COLORS.adx);
      add("adx:plus", `+DI ${period}`, id, values.plusDI, GREEN);
      add("adx:minus", `−DI ${period}`, id, values.minusDI, RED);
    } else if (id === "supertrend") {
      const { values, colors } = supertrend(bars, period, multiplier);
      add(id, `Supertrend ${period}/${multiplier}`, "price", values, GREEN, "line", colors);
    }
  }
  return { series, warnings: [...warnings] };
}
