/**
 * "When was this?" in IST, for reasons and states that persist across sessions. No
 * imports, no JSX — loads under node (tests/test_ist_when.py executes it).
 *
 * A persisted reason ("auto-paused: max_consecutive_losses", "no live entries: broker
 * stop-loss latch", a CONFIRMED signal) reads as CURRENT unless it carries its date.
 * The label is computed by arithmetic on the epoch (never Intl / the viewer's zone),
 * so a PC set to another timezone cannot move a date.
 *
 *   today    -> "today 14:12 IST"
 *   earlier  -> "Fri 25 Sep 14:12 IST"   (the year is added when it is not this year)
 *   unknown  -> null   — the caller says "date not recorded"; it never invents one
 */

const IST_OFFSET_MS = (5 * 60 + 30) * 60 * 1000;
const DAY_NAMES = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
const MONTH_NAMES = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                     "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const pad2 = (n) => String(n).padStart(2, "0");

/**
 * An ISO string (with or without an offset; naive = UTC, the backend's convention) or
 * an epoch-ms number / digit string -> epoch ms; null when unusable.
 */
export function toEpochMs(value) {
  if (value === null || value === undefined || value === "") return null;
  if (typeof value === "number") return Number.isFinite(value) && value > 0 ? value : null;
  const s = String(value).trim();
  if (/^\d{10,}$/.test(s)) return Number(s);
  // A bare "YYYY-MM-DDTHH:MM:SS[.fff]" has no offset: read it as UTC.
  const hasZone = /(?:Z|[+-]\d{2}:?\d{2})$/i.test(s);
  const t = Date.parse(hasZone || !/T/.test(s) ? s : `${s}Z`);
  return Number.isFinite(t) && t > 0 ? t : null;
}

const istParts = (ms) => {
  const d = new Date(ms + IST_OFFSET_MS);          // UTC getters now read IST
  return { y: d.getUTCFullYear(), m: d.getUTCMonth(), day: d.getUTCDate(),
           dow: d.getUTCDay(), hh: d.getUTCHours(), mm: d.getUTCMinutes() };
};

/** The IST calendar date, "YYYY-MM-DD", of an epoch-ms instant. */
export function istDay(ms) {
  const p = istParts(ms);
  return `${p.y}-${pad2(p.m + 1)}-${pad2(p.day)}`;
}

/**
 * @param {*} value  ISO string / epoch ms
 * @param {number} [nowMs]
 * @returns {null | {label: string, today: boolean, ms: number}}
 */
export function formatIstWhen(value, nowMs = Date.now()) {
  const ms = toEpochMs(value);
  if (ms === null) return null;
  const then = istParts(ms);
  const hm = `${pad2(then.hh)}:${pad2(then.mm)}`;
  const nowOk = Number.isFinite(Number(nowMs));
  const now = nowOk ? istParts(Number(nowMs)) : null;
  const sameDay = now !== null && now.y === then.y && now.m === then.m && now.day === then.day;
  if (sameDay) return { label: `today ${hm} IST`, today: true, ms };
  const year = now !== null && now.y !== then.y ? ` ${then.y}` : "";
  return {
    label: `${DAY_NAMES[then.dow]} ${then.day} ${MONTH_NAMES[then.m]}${year} ${hm} IST`,
    today: false, ms,
  };
}
