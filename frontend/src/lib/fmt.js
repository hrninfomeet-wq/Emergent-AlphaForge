// Locale-safe formatters (avoid toLocaleString which can fail on POSIX runtime)

const formatThousands = (n, decimals = 2) => {
  const parts = Math.abs(Number(n)).toFixed(decimals).split(".");
  parts[0] = parts[0].replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  const out = parts.join(".");
  return Number(n) < 0 ? "-" + out : out;
};

export const fmtNum = (n, decimals = 2) => {
  if (n === null || n === undefined || Number.isNaN(Number(n))) return "–";
  return formatThousands(n, decimals);
};

export const fmtInt = (n) => {
  if (n === null || n === undefined || Number.isNaN(Number(n))) return "–";
  return formatThousands(n, 0);
};

export const fmtPct = (n, decimals = 2) => {
  if (n === null || n === undefined || Number.isNaN(Number(n))) return "–";
  return `${Number(n).toFixed(decimals)}%`;
};

export const fmtSigned = (n, decimals = 2) => {
  if (n === null || n === undefined || Number.isNaN(Number(n))) return "–";
  const v = Number(n);
  return `${v >= 0 ? "+" : ""}${v.toFixed(decimals)}`;
};

export const fmtPnL = (n) => {
  if (n === null || n === undefined || Number.isNaN(Number(n))) return "–";
  const v = Number(n);
  return `${v >= 0 ? "+" : "−"}${formatThousands(Math.abs(v), 2)}`;
};

export const colorPnL = (n) => {
  if (n === null || n === undefined || Number.isNaN(Number(n))) return "text-dim";
  return Number(n) > 0 ? "text-success" : Number(n) < 0 ? "text-danger" : "text-dim";
};

// IST formatters using UTC offset arithmetic (avoid locale dependencies)
const IST_OFFSET_MIN = 330; // UTC+5:30
const pad = (n) => String(n).padStart(2, "0");
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

export const tsToTime = (ts) => {
  if (!ts) return "";
  const d = new Date(Number(ts) + IST_OFFSET_MIN * 60 * 1000);
  return `${pad(d.getUTCDate())} ${MONTHS[d.getUTCMonth()]} ${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}`;
};

export const tsToFull = (ts) => {
  if (!ts) return "";
  const d = new Date(Number(ts) + IST_OFFSET_MIN * 60 * 1000);
  return `${d.getUTCFullYear()}-${pad(d.getUTCMonth() + 1)}-${pad(d.getUTCDate())} ${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())} IST`;
};

export const isoToFull = (iso) => {
  if (!iso) return "";
  const d = new Date(iso);
  return tsToFull(d.getTime());
};

// Indian lakh/crore grouping (last 3 digits, then groups of 2). No locale API.
const groupIndian = (intStr) => {
  if (intStr.length <= 3) return intStr;
  const head = intStr.slice(0, intStr.length - 3);
  const tail = intStr.slice(-3);
  return head.replace(/\B(?=(\d{2})+(?!\d))/g, ",") + "," + tail;
};

export const fmtINR = (n, decimals = 0) => {
  if (n === null || n === undefined || Number.isNaN(Number(n))) return "–";
  const v = Number(n);
  const fixed = Math.abs(v).toFixed(decimals);
  const [int, dec] = fixed.split(".");
  const body = groupIndian(int) + (dec ? "." + dec : "");
  return `${v < 0 ? "−" : ""}₹${body}`;
};

export const fmtINRSigned = (n, decimals = 0) => {
  if (n === null || n === undefined || Number.isNaN(Number(n))) return "–";
  const v = Number(n);
  const fixed = Math.abs(v).toFixed(decimals);
  const [int, dec] = fixed.split(".");
  const body = groupIndian(int) + (dec ? "." + dec : "");
  return `${v < 0 ? "−" : "+"}₹${body}`;
};

export const fmtDuration = (seconds) => {
  const s = Math.max(0, Math.round(Number(seconds) || 0));
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m`;
  const h = Math.floor(m / 60);
  return `${h}h ${String(m % 60).padStart(2, "0")}m`;
};
