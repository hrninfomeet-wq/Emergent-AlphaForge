// Shared IST date<->epoch helpers.
//
// Previously dateToMs/msToDate were copy-pasted into BacktestLab, DataWarehouse
// and Optimizer with subtly different comments. Centralizing avoids drift: a
// fix here applies everywhere a date window is converted.
//
// IST = UTC+5:30. A trading "day" runs 09:15-15:30 IST. dateToMs maps a
// YYYY-MM-DD to the IST session start (09:15) or end (15:30) in epoch-ms UTC.

const IST_OFFSET_MIN = 5 * 60 + 30;

export function dateToMs(s, endOfDay = false) {
  if (!s) return null;
  const [y, m, d] = String(s).split("-").map(Number);
  if (!y || !m || !d) return null;
  const istHour = endOfDay ? 15 : 9;
  const istMin = endOfDay ? 30 : 15;
  // Build the wall-clock IST instant, then subtract the IST offset to get UTC.
  const baseUtc = Date.UTC(y, m - 1, d, istHour, istMin, 0);
  return baseUtc - IST_OFFSET_MIN * 60 * 1000;
}

export function msToDate(ms) {
  if (!ms) return "";
  const d = new Date(Number(ms) + IST_OFFSET_MIN * 60 * 1000);
  return d.toISOString().slice(0, 10);
}
