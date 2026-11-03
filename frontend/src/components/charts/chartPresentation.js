import { drawingStorageKey, instrumentIdentity, normalizeDrawings } from "../../lib/chartModel";

export const TIMEFRAMES = ["1m", "3m", "5m", "15m", "30m", "1h", "1d", "1w"];
export const INDEX_SHORTCUTS = [
  { instrument_key: "NSE_INDEX|Nifty 50", symbol: "NIFTY", name: "Nifty 50", exchange: "NSE", segment: "NSE_INDEX", kind: "index", volume_available: false, warehouse_symbol: "NIFTY" },
  { instrument_key: "NSE_INDEX|Nifty Bank", symbol: "BANKNIFTY", name: "Nifty Bank", exchange: "NSE", segment: "NSE_INDEX", kind: "index", volume_available: false, warehouse_symbol: "BANKNIFTY" },
  { instrument_key: "BSE_INDEX|SENSEX", symbol: "SENSEX", name: "SENSEX", exchange: "BSE", segment: "BSE_INDEX", kind: "index", volume_available: false, warehouse_symbol: "SENSEX" },
];

export const finiteValue = (value) => (typeof value === "number" || (typeof value === "string" && value.trim() !== "")) && Number.isFinite(Number(value));
export const priceText = (value) => finiteValue(value)
  ? Number(value).toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 }) : "Unknown";
export const amountText = (value) => finiteValue(value) ? `${Number(value) > 0 ? "+" : ""}₹${priceText(value)}` : "Unknown";
export const readable = (value) => String(value || "unknown").replace(/[_-]+/g, " ");
export const timeText = (time) => finiteValue(time) ? new Intl.DateTimeFormat("en-IN", {
  timeZone: "Asia/Kolkata", day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit", hour12: false,
}).format(new Date(Number(time) * 1000)) : "Time unknown";

const sessionDate = new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Kolkata", year: "numeric", month: "2-digit", day: "2-digit" });
const sessionLabel = new Intl.DateTimeFormat("en-IN", { timeZone: "Asia/Kolkata", day: "2-digit", month: "short", year: "numeric" });
const signedChange = (value) => {
  const rounded = Math.round((Math.abs(value) + Number.EPSILON) * 100) / 100;
  return `${rounded === 0 ? "" : value > 0 ? "+" : "−"}${priceText(rounded)}`;
};

/** Day return uses the LTP and previous close from the same quote, never a
 * chart candle's open. That keeps it independent of timeframe and history depth. */
export function sessionQuote(snapshot, instrumentKey, fallbackBar, now = Date.now()) {
  const tick = snapshot?.instrument_key === instrumentKey ? snapshot.tick : null;
  const stamp = finiteValue(tick?.ts) ? Number(tick.ts) : null;
  const validQuote = finiteValue(tick?.last_price) && Number(tick.last_price) > 0 &&
    stamp > 0 && stamp <= now + 5000;
  const price = validQuote ? Number(tick.last_price) :
    finiteValue(fallbackBar?.close) && Number(fallbackBar.close) > 0 ? Number(fallbackBar.close) : null;
  const previousClose = validQuote && finiteValue(tick?.close_price) && Number(tick.close_price) > 0
    ? Number(tick.close_price) : null;
  if (previousClose === null) return { price, change: null, percent: null, text: "Day change unavailable", title: "Waiting for a quote with the previous trading session's close." };
  const change = price - previousClose;
  const percent = change / previousClose * 100;
  if (!Number.isFinite(change) || !Number.isFinite(percent)) return { price, change: null, percent: null, text: "Day change unavailable", title: "Invalid previous-close reference." };
  const session = sessionDate.format(stamp) === sessionDate.format(now) ? "Today" : sessionLabel.format(stamp);
  return { price, previousClose, change, percent,
    text: `${signedChange(change)} pts (${signedChange(percent)}%) · ${session}`,
    title: `Change from previous trading session close: ${priceText(previousClose)} · Quote ${timeText(stamp / 1000)} IST` };
}

export function streamFreshness(snapshot, transport, receivedAt, now = Date.now()) {
  const age = finiteValue(snapshot?.last_tick_age_ms)
    ? Math.max(0, Number(snapshot.last_tick_age_ms)) + Math.max(0, now - receivedAt) : null;
  if (transport === "error" || transport === "reconnecting") return { label: "Reconnecting", tone: "warning", age };
  if (!snapshot) return { label: "Waiting for feed", tone: "neutral", age };
  if (snapshot.status === "market_closed" || snapshot.status === "off_hours") return { label: "Market closed", tone: "neutral", age };
  if (snapshot.connected !== true) return { label: "Feed disconnected", tone: "warning", age };
  if (snapshot.status === "stale") return { label: "Stale tick", tone: "warning", age };
  if (snapshot.status === "waiting") return { label: "Waiting for tick", tone: "neutral", age };
  if (age == null) return { label: "Tick freshness unknown", tone: "warning", age };
  if (age > 15000) return { label: "Stale tick", tone: "warning", age };
  if (transport !== "open") return { label: "Connecting", tone: "neutral", age };
  return { label: "Fresh tick", tone: "success", age };
}

export function matchingPositions(rows, instrument, origin = "all") {
  const seen = new Set();
  return (Array.isArray(rows) ? rows : []).filter((row) => {
    if (!row || row.instrument_key !== instrument?.instrument_key || !["broker", "paper"].includes(row.origin)) return false;
    if (origin !== "all" && row.origin !== origin) return false;
    if (row.expiry && instrument.expiry && String(row.expiry) !== String(instrument.expiry)) return false;
    // The same broker position must not be counted again through its journal.
    // Paper remains an independent origin even when its id happens to match.
    const identity = `${row.origin}:${row.id || `${row.instrument_key}:${row.symbol}`}`;
    if (seen.has(identity)) return false;
    seen.add(identity);
    return true;
  });
}

export function positionFresh(row, receivedAt, now = Date.now()) {
  if (row?.fresh !== true || !receivedAt) return false;
  const elapsed = Math.max(0, now - receivedAt);
  if (elapsed > 30000) return false;
  return [row.mark_age_ms, row.broker_age_ms].every((age) => !finiteValue(age) || Number(age) + elapsed <= 30000);
}

/** Application-owned v1 drawing envelope. Only semantic anchors are durable;
 * plugin/runtime objects and canvas pixels never become storage identity. */
export function readDrawingEnvelope(storage, instrument) {
  try {
    const raw = storage.getItem(drawingStorageKey(instrument));
    if (!raw) return { drawings: [], writable: true, warning: "" };
    const value = JSON.parse(raw);
    if (value.schema_version !== 1 || JSON.stringify(value.instrument) !== JSON.stringify(instrumentIdentity(instrument))) {
      return { drawings: [], writable: false, warning: "Saved drawings have a different identity or format. They were left untouched." };
    }
    if (!Array.isArray(value.drawings)) throw new Error("Invalid drawing envelope");
    const drawings = normalizeDrawings(value.drawings, instrument);
    if (drawings.length !== value.drawings.length) return { drawings, writable: false, warning: "Some saved drawings could not be read. Saved data was left untouched." };
    return { drawings, writable: true, warning: "" };
  } catch {
    return { drawings: [], writable: false, warning: "Saved drawings are unavailable. New drawings remain in this tab only." };
  }
}

export function saveDrawingEnvelope(storage, instrument, rows) {
  const drawings = normalizeDrawings(rows, instrument);
  if (drawings.length !== rows.length) return { ok: false, warning: "A drawing could not be saved safely. Saved data was left untouched." };
  try {
    storage.setItem(drawingStorageKey(instrument), JSON.stringify({ schema_version: 1,
      instrument: instrumentIdentity(instrument), drawings, saved_at: new Date().toISOString() }));
    return { ok: true, warning: "" };
  } catch {
    return { ok: false, warning: "Browser storage is full or unavailable. Drawings remain in this tab only." };
  }
}

export function pluginDrawings(rows) {
  return rows.map((drawing) => ({ id: drawing.id, kind: drawing.type, points: drawing.anchors, style: drawing.style }));
}
