// Shared constants + tiny helpers for the Data Warehouse panels (split from
// pages/DataWarehouse.jsx — W4 of the warehouse-page review).
import { isoToFull, tsToFull } from "@/lib/fmt";

export const INSTRUMENTS = ["NIFTY", "BANKNIFTY", "SENSEX"];

export const MONEYNESS_OPTIONS = ["atm", "itm1", "itm2", "otm1", "otm2", "otm3"];

export const LEG_OPTIONS = ["CE", "PE"];

// Human labels for warehouse_runs.source (raw value stays in the tooltip).

// Human labels for warehouse_runs.source (raw value stays in the tooltip).
export const RUN_SOURCE_LABELS = {
  data_hygiene: "Hygiene / Sync",
  upstox: "Upstox spot ingest",
  upstox_background: "Upstox spot ingest (background)",
  upstox_options: "Option fetch",
  upstox_options_background: "Option fetch (background)",
  upstox_expired_option_contracts: "Expired contracts sync",
  upstox_vix: "India VIX ingest",
  yfinance: "Yahoo Finance ingest",
};

export function dateInput(daysAgo = 0) {
  // IST calendar date (UTC+5:30) regardless of the browser timezone, so date
  // defaults match the trading day the backend reasons about.
  const d = new Date(Date.now() + (5 * 60 + 30) * 60 * 1000 - daysAgo * 24 * 60 * 60 * 1000);
  return d.toISOString().slice(0, 10);
}

export function quoteTimeDisplay(quote) {
  const raw = quote?.timestamp || quote?.last_trade_time;
  if (!raw) return "n/a";
  if (/^\d+$/.test(String(raw))) return tsToFull(Number(raw));
  return isoToFull(raw);
}

export const HEATMAP_RANGES = [
  { key: "8w", label: "8 weeks", days: 56 },
  { key: "3m", label: "3 months", days: 92 },
  { key: "all", label: "All", days: null },
];

export function rangeCutoff(rangeKey) {
  const range = HEATMAP_RANGES.find((r) => r.key === rangeKey);
  if (!range || !range.days) return null;
  return new Date(Date.now() - range.days * 24 * 60 * 60 * 1000).toISOString().slice(0, 10);
}

export function RangeChips({ value, onChange, testid }) {
  return (
    <div className="flex items-center gap-1" data-testid={testid}>
      {HEATMAP_RANGES.map((r) => (
        <button
          key={r.key}
          onClick={() => onChange(r.key)}
          className={`text-[10px] px-1.5 py-0.5 rounded border font-mono ${value === r.key ? "border-info bg-bg-3 text-foreground" : "border-line bg-bg-1 text-dim hover:text-foreground"}`}
        >
          {r.label}
        </button>
      ))}
    </div>
  );
}

export function SectionHeader({ title, subtitle }) {
  return (
    <div className="flex items-baseline gap-2 pt-2 pb-0.5 px-1" data-testid={`section-${title.toLowerCase().replace(/\s+/g, "-")}`}>
      <div className="text-xs font-semibold uppercase tracking-wider text-foreground">{title}</div>
      {subtitle && <div className="text-[11px] text-dimmer">{subtitle}</div>}
    </div>
  );
}

export function AuditStat({ label, value }) {

  return (
    <div className="rounded-md border border-line bg-bg-2 p-2">
      <div className="text-[10px] uppercase tracking-wider text-dimmer">{label}</div>
      <div className="text-sm font-mono mt-0.5">{value}</div>
    </div>
  );
}
