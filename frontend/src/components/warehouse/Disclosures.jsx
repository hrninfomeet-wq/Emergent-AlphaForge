// Collapsible page guidance + advanced-tools wrapper (split from pages/DataWarehouse.jsx).
import { useState } from "react";
import { AlertCircle, Database } from "lucide-react";

export function AdvancedTools({ children }) {
  // Pre-band manual tools. Routine maintenance no longer needs them — they
  // exist for research pulls outside the rolling scope (extra moneyness,
  // fixed-expiry studies, deep contract history). Collapsed by default so the
  // page reads status-first.
  const [open, setOpen] = useState(false);
  return (
    <div className="rounded-lg border border-line bg-bg-1" data-testid="warehouse-advanced-tools">
      <button
        onClick={() => setOpen((v) => !v)}
        className="w-full px-3 py-2 flex items-center gap-2 text-left"
        data-testid="warehouse-advanced-toggle"
      >
        <Database className="w-4 h-4 text-info" />
        <span className="text-xs font-semibold uppercase tracking-wider text-dim">Advanced tools — manual option planner &amp; contract backfill</span>
        <span className="text-[11px] text-dimmer">{open ? "hide" : "show"}</span>
      </button>
      {open && (
        <div className="px-3 pb-3 space-y-3">
          <div className="rounded-md border border-line bg-bg-2 p-2 text-[11px] text-dim">
            Routine warehouse maintenance is automatic (Sync / auto-update, driven by the daily ATM band).
            Use these tools only for pulls the band does not cover: extra moneyness (OTM2/OTM3), fixed-expiry
            studies, or contract metadata beyond the rolling 9-month scope.
          </div>
          {children}
        </div>
      )}
    </div>
  );
}

export function HowThisPageWorks() {
  const [open, setOpen] = useState(false);
  return (
    <div className="rounded-lg border border-line bg-bg-1" data-testid="warehouse-help">
      <button
        onClick={() => setOpen((v) => !v)}
        className="w-full px-3 py-2 flex items-center gap-2 text-left"
        data-testid="warehouse-help-toggle"
      >
        <AlertCircle className="w-4 h-4 text-info" />
        <span className="text-xs font-semibold uppercase tracking-wider text-dim">How this page works</span>
        <span className="text-[11px] text-dimmer">{open ? "hide" : "show"}</span>
      </button>
      {open && (
        <div className="px-3 pb-3 text-[11px] text-dim space-y-1.5">
          <div>1. <span className="text-foreground font-semibold">Data flows in automatically.</span> While Upstox is connected, the warehouse catches up on startup, after connecting, and daily at 18:00 IST — spot candles, option contracts, and every strike the day's spot range touched (the ATM band).</div>
          <div>2. <span className="text-foreground font-semibold">Sync now forces it.</span> One click in Data Hygiene catches up new sessions and band-fills any wick-edge gaps. Re-running is always safe — only missing data is requested.</div>
          <div>3. <span className="text-foreground font-semibold">Verified means trustworthy.</span> Strike-days the broker has proven it has no candles for are excluded automatically (shown as "broker-empty"), so amber always means something is actually fixable.</div>
          <div>4. <span className="text-foreground font-semibold">Explore</span> with the candlestick chart and the Spot &amp; ATM Option Lookup; <span className="text-foreground font-semibold">verify</span> with the audits below them.</div>
          <div>5. <span className="text-foreground font-semibold">Manual tools</span> (date-range spot ingest, the option planner, expired-contract backfill) are only needed for research pulls beyond the rolling 9-month scope. Red buttons delete data — read their confirmations carefully.</div>
        </div>
      )}
    </div>
  );
}
