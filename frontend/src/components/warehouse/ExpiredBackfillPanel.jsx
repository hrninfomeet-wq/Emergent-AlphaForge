// Expired option-contract metadata backfill — advanced tool (split from pages/DataWarehouse.jsx).
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Database, Download } from "lucide-react";
import { fmtInt } from "@/lib/fmt";
import { INSTRUMENTS } from "./shared";

export function ExpiredContractBackfillPanel({ status, form, setForm, result, running, onBackfill }) {
  const connected = status?.connected && !status?.expired;
  const set = (key, value) => setForm((prev) => ({ ...prev, [key]: value }));
  const blocked = result?.status === "blocked";

  return (
    <div className="rounded-lg border border-line bg-bg-1" data-testid="expired-contract-backfill-panel">
      <div className="px-3 py-2 border-b border-line flex items-center gap-2">
        <Database className="w-4 h-4 text-info" />
        <div className="text-xs font-semibold uppercase tracking-wider text-dim">Backfill expired option contracts</div>
        {result && (
          <span className={`ml-auto text-[10px] px-1.5 py-0.5 rounded border font-mono ${blocked ? "bg-amber-950 text-amber-200 border-amber-900" : result.status === "ok" ? "bg-emerald-950 text-emerald-200 border-emerald-900" : "bg-bg-3 text-dim border-line"}`}>
            {result.status}
          </span>
        )}
      </div>
      <div className="p-3 grid grid-cols-1 xl:grid-cols-[minmax(0,2fr)_minmax(260px,1fr)] gap-3">
        <div className="rounded-md border border-line bg-bg-2 p-3">
          <div className="grid grid-cols-2 lg:grid-cols-[1fr_1fr_1fr_0.8fr_auto] gap-2 items-end">
            <label className="text-[11px] text-dim">
              Instrument
              <select
                value={form.instrument}
                onChange={(e) => set("instrument", e.target.value)}
                className="mt-1 h-9 w-full rounded-md border border-input bg-bg-1 px-3 py-1 text-sm text-foreground"
                data-testid="expired-contract-instrument"
              >
                {INSTRUMENTS.map((inst) => <option key={inst} value={inst}>{inst}</option>)}
              </select>
            </label>
            <label className="text-[11px] text-dim">
              From
              <Input
                type="date"
                value={form.from_date}
                onChange={(e) => set("from_date", e.target.value)}
                className="mt-1 bg-bg-1 border-line"
                data-testid="expired-contract-from-date"
              />
            </label>
            <label className="text-[11px] text-dim">
              To
              <Input
                type="date"
                value={form.to_date}
                onChange={(e) => set("to_date", e.target.value)}
                className="mt-1 bg-bg-1 border-line"
                data-testid="expired-contract-to-date"
              />
            </label>
            <label className="text-[11px] text-dim">
              Max expiries
              <Input
                type="number"
                min="1"
                max="120"
                value={form.max_expiries}
                onChange={(e) => set("max_expiries", e.target.value)}
                className="mt-1 bg-bg-1 border-line text-right"
                data-testid="expired-contract-max-expiries"
              />
            </label>
            <Button
              size="sm"
              onClick={onBackfill}
              disabled={running || !connected}
              className="h-9 text-xs bg-bg-3 border border-line hover:bg-bg-2"
              data-testid="expired-contract-backfill-button"
            >
              <Download className="w-3 h-3 mr-1" />
              {running ? "Backfilling..." : "Backfill"}
            </Button>
          </div>
          <label className="mt-3 text-[11px] text-dim flex items-center gap-2">
            <input
              type="checkbox"
              checked={!!form.confirm_large_fetch}
              onChange={(e) => set("confirm_large_fetch", e.target.checked)}
              className="h-4 w-4"
              data-testid="expired-contract-confirm-large"
            />
            Allow more expiries than the max guard for this request
          </label>
          <div className="mt-2 text-[11px] text-dim">
            This is metadata only: it stores expired contracts by expiry, strike, side, and instrument key. It is required before old option candles can be planned reliably.
          </div>
        </div>

        <div className="rounded-md border border-line bg-bg-2 p-3" data-testid="expired-contract-backfill-summary">
          <div className="text-[10px] uppercase tracking-wider text-dimmer">Backfill result</div>
          {result ? (
            <div className="mt-2 grid grid-cols-2 gap-2 text-xs">
              <div>
                <div className="text-dimmer">Expiries</div>
                <div className="font-mono">{fmtInt(result.expiry_count || 0)}</div>
              </div>
              <div>
                <div className="text-dimmer">Contracts</div>
                <div className="font-mono">{fmtInt(result.fetched_contracts || 0)}</div>
              </div>
              <div>
                <div className="text-dimmer">Stored</div>
                <div className="font-mono">{fmtInt(result.upserted || 0)}</div>
              </div>
              <div>
                <div className="text-dimmer">Skipped</div>
                <div className="font-mono">{fmtInt(result.skipped || 0)}</div>
              </div>
              {result.reason && (
                <div className="col-span-2 rounded-md border border-amber-900 bg-amber-950/40 p-2 text-amber-100">
                  {result.reason}
                </div>
              )}
              {result.failed?.length > 0 && (
                <div className="col-span-2 text-rose-200">
                  Failed expiries: {result.failed.slice(0, 5).map((item) => item.expiry).join(", ")}
                </div>
              )}
            </div>
          ) : (
            <div className="mt-2 text-[11px] text-dim">
              Use this before historical option planning when old expiries are missing from local contract metadata. Requires Upstox expired-instruments access.
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
