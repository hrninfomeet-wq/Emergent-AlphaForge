// Shared IST date<->epoch helpers.
//
// Previously dateToMs/msToDate were copy-pasted into BacktestLab, DataWarehouse
// and Optimizer with subtly different comments. Centralizing avoids drift: a
// fix here applies everywhere a date window is converted.
//
// IST = UTC+5:30. A trading "day" opens at 09:15 IST. dateToMs maps a
// YYYY-MM-DD to the IST session start (09:15) or end in epoch-ms UTC.
//
// The end bound is 15:40, not 15:30: from 2026-08-03 the equity-derivatives
// segment trades ten minutes past the cash close (see backend/app/session_spec.py),
// so a 15:30 cap would silently truncate the last ten minutes of every option
// series from any date-range query built on this helper. Widening it is safe for
// spot too — the index feed stops at 15:29 regardless, and the server filters
// off-session rows on its own.

const IST_OFFSET_MIN = 5 * 60 + 30;
const SESSION_END_HOUR = 15;
const SESSION_END_MIN = 40;

export function dateToMs(s, endOfDay = false) {
  if (!s) return null;
  const [y, m, d] = String(s).split("-").map(Number);
  if (!y || !m || !d) return null;
  const istHour = endOfDay ? SESSION_END_HOUR : 9;
  const istMin = endOfDay ? SESSION_END_MIN : 15;
  // Build the wall-clock IST instant, then subtract the IST offset to get UTC.
  const baseUtc = Date.UTC(y, m - 1, d, istHour, istMin, 0);
  return baseUtc - IST_OFFSET_MIN * 60 * 1000;
}

export function msToDate(ms) {
  if (!ms) return "";
  const d = new Date(Number(ms) + IST_OFFSET_MIN * 60 * 1000);
  return d.toISOString().slice(0, 10);
}
