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
