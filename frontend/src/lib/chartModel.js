// Presentation-only chart data. No broker state or strategy parameters live here.
const LAST_UTC_SECOND = 253402300799; // 9999-12-31T23:59:59Z; rejects present-day milliseconds.
const DEFAULT_MAX_BARS = 10000;
const DRAWING_VERSION = 1;
const DRAWING_ANCHORS = Object.freeze({
  "horizontal-line": 1, "horizontal-ray": 1, "vertical-line": 1, text: 1,
  "arrow-mark-up": 1, "arrow-mark-down": 1,
  "trend-line": 2, ray: 2, arrow: 2, rectangle: 2, "fib-retracement": 2,
  "date-and-price-range": 2,
});

function finiteNumber(value) {
  if (typeof value !== "number" && (typeof value !== "string" || !value.trim())) return null;
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}

function utcSecond(value) {
  const time = finiteNumber(value);
  return Number.isSafeInteger(time) && time >= 0 && time <= LAST_UTC_SECOND ? time : null;
}

/**
 * Accept the chart API's UTC-second OHLCV contract. Invalid candles are dropped,
 * not repaired; missing/invalid volume stays null, while genuine zero survives.
 * Last valid row wins at a duplicate timestamp. Neither input nor rows mutate.
 */
export function normalizeBars(rows) {
  if (!Array.isArray(rows)) return [];
  const byTime = new Map();
  for (const row of rows) {
    if (!row || typeof row !== "object" || Array.isArray(row)) continue;
    const time = utcSecond(row.time);
    const open = finiteNumber(row.open);
    const high = finiteNumber(row.high);
    const low = finiteNumber(row.low);
    const close = finiteNumber(row.close);
    if ([time, open, high, low, close].some(value => value === null)) continue;
    if (high < Math.max(open, close, low) || low > Math.min(open, close, high)) continue;
    const suppliedVolume = finiteNumber(row.volume);
    const volume = suppliedVolume !== null && suppliedVolume >= 0 ? suppliedVolume : null;
    byTime.set(time, { time, open, high, low, close, volume });
  }
  return [...byTime.values()].sort((left, right) => left.time - right.time);
}

/** Full-bar upserts: updates replace matching history rows, including volume. */
export function mergeBars(history, updates, maxBars = DEFAULT_MAX_BARS) {
  const count = finiteNumber(maxBars);
  const limit = count === null ? DEFAULT_MAX_BARS : Math.max(0, Math.floor(count));
  if (!limit) return [];
  const prior = Array.isArray(history) ? history : [];
  const incoming = Array.isArray(updates) ? updates : updates ? [updates] : [];
  return normalizeBars([...prior, ...incoming]).slice(-limit);
}

function text(value) {
  return typeof value === "string" && value.trim() ? value.trim() : null;
}

/**
 * Stable contract identity for drawings; a reused exchange token alone is not an
 * option/future identity. Missing derivative expiry deliberately fails closed.
 */
export function instrumentIdentity(instrument) {
  if (!instrument || typeof instrument !== "object") return null;
  const instrumentKey = text(instrument.instrument_key);
  const kind = text(instrument.kind)?.toLowerCase();
  if (!instrumentKey || !["index", "equity", "option", "future"].includes(kind)) return null;
  const expiry = text(instrument.expiry);
  if ((kind === "option" || kind === "future") && !expiry) return null;
  return {
    instrument_key: instrumentKey,
    exchange: text(instrument.exchange),
    segment: text(instrument.segment),
    kind,
    symbol: text(instrument.symbol),
    expiry,
    strike: finiteNumber(instrument.strike),
    option_type: text(instrument.option_type)?.toUpperCase() || null,
  };
}

export function drawingStorageKey(instrument) {
  const identity = instrumentIdentity(instrument);
  return identity ? `alphaforge:chart-drawings:v${DRAWING_VERSION}:${encodeURIComponent(JSON.stringify(identity))}` : null;
}

function normalizeStyle(value) {
  const input = value && typeof value === "object" ? value : {};
  const style = {};
  for (const key of ["color", "textColor", "backgroundColor"]) {
    if (typeof input[key] === "string" && input[key].length <= 128) style[key] = input[key];
  }
  const width = finiteNumber(input.width ?? input.lineWidth);
  if (width !== null && width > 0 && width <= 10) style.width = width;
  if (["solid", "dashed", "dotted"].includes(input.lineStyle)) style.lineStyle = input.lineStyle;
  if (typeof input.text === "string") style.text = input.text.slice(0, 1000);
  for (const key of ["extendLeft", "extendRight", "fillBackground", "reverse", "showText", "fibLevelsAsPercents",
    "showStats", "showPriceRange", "showPercentPriceRange", "showBarsRange", "showDateTimeRange", "showTime", "showPriceLabels"]) {
    if (typeof input[key] === "boolean") style[key] = input[key];
  }
  const transparency = finiteNumber(input.transparency);
  for (const key of ["leftEnd", "rightEnd"]) if ([0, 1].includes(input[key])) style[key] = input[key];
  if (transparency !== null && transparency >= 0 && transparency <= 100) style.transparency = transparency;
  if (Array.isArray(input.levels)) {
    style.levels = input.levels.slice(0, 32).flatMap(level => {
      if (!level || typeof level !== "object") return [];
      const coeff = finiteNumber(level.coeff);
      if (coeff === null || Math.abs(coeff) > 100 || typeof level.color !== "string") return [];
      const normalized = { coeff, color: level.color.slice(0, 128), visible: level.visible !== false };
      if (typeof level.label === "string") normalized.label = level.label.slice(0, 128);
      if (typeof level.text === "string") normalized.text = level.text.slice(0, 128);
      if (["solid", "dashed", "dotted"].includes(level.style)) normalized.style = level.style;
      const levelWidth = finiteNumber(level.width);
      if (levelWidth !== null && levelWidth > 0 && levelWidth <= 10) normalized.width = levelWidth;
      return [normalized];
    });
  }
  return style;
}

/**
 * App-owned schema: {schema_version,id,type,instrument,anchors:[{time,price}],style}.
 * Plugin {kind,points} is accepted when saving, but plugin internals/pixels are
 * never persisted. Raw plugin rows without identity are bound to the supplied
 * instrument; callers must validate the stored envelope identity before load.
 */
export function normalizeDrawings(rows, instrument) {
  const identity = instrumentIdentity(instrument);
  if (!identity || !Array.isArray(rows)) return [];
  const serializedIdentity = JSON.stringify(identity);
  const drawings = new Map();
  for (const row of rows.slice(0, 500)) {
    if (!row || typeof row !== "object") continue;
    if (row.schema_version !== undefined && row.schema_version !== DRAWING_VERSION) continue;
    const id = text(row.id);
    const type = row.type ?? row.kind;
    const required = DRAWING_ANCHORS[type];
    if (!id || id.length > 128 || !required) continue;
    if (row.instrument !== undefined && JSON.stringify(instrumentIdentity(row.instrument)) !== serializedIdentity) continue;
    const sourceAnchors = row.anchors ?? row.points;
    if (!Array.isArray(sourceAnchors) || sourceAnchors.length !== required) continue;
    const anchors = sourceAnchors.map(anchor => ({ time: utcSecond(anchor?.time), price: finiteNumber(anchor?.price) }));
    if (anchors.some(anchor => anchor.time === null || anchor.price === null)) continue;
    drawings.set(id, {
      schema_version: DRAWING_VERSION, id, type,
      instrument: { ...identity }, anchors, style: normalizeStyle(row.style),
    });
  }
  return [...drawings.values()];
}
