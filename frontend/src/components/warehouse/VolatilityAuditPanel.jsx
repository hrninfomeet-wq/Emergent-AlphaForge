// Read-only realized-vol spike audit (split from pages/DataWarehouse.jsx).
import { useState } from "react";
import { api } from "@/lib/api";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { AlertTriangle } from "lucide-react";
import { fmtInt, fmtNum, tsToFull } from "@/lib/fmt";
import { INSTRUMENTS, dateInput } from "./shared";

export function VolatilityAuditPanel() {
  const [form, setForm] = useState({
    instrument: "NIFTY",
    from_date: dateInput(30),
    to_date: dateInput(0),
    spike_threshold: 2.5,
  });
  const [result, setResult] = useState(null);
  const [loading, setLoading] = useState(false);

  const set = (key, value) => setForm((prev) => ({ ...prev, [key]: value }));

  const run = async () => {
    setLoading(true);
    try {
      const res = await api.volatilityAudit({
        instrument: form.instrument,
        from_date: form.from_date,
        to_date: form.to_date,
        spike_threshold: Number(form.spike_threshold) || 2.5,
      });
      setResult(res);
      const s = res.summary || {};
      toast.success(`Volatility audit: ${fmtInt(s.spike_bars || 0)} spike bars (${fmtNum(s.spike_pct || 0, 2)}%)`);
    } catch (e) {
      const msg = e.response?.data?.detail || e.message;
      toast.error(`Volatility audit failed: ${msg}`);
    } finally {
      setLoading(false);
    }
  };

  const summary = result?.summary || {};
  const spikes = (result?.spikes || []).slice(0, 10);

  return (
    <div className="rounded-lg border border-line bg-bg-1" data-testid="volatility-audit-panel">
      <div className="px-3 py-2 border-b border-line flex items-center gap-2">
        <AlertTriangle className="w-4 h-4 text-info" />
        <div className="text-xs font-semibold uppercase tracking-wider text-dim">Volatility Audit</div>
        <div className="text-[11px] text-dimmer ml-1">Realized 5m vol vs 30-day baseline — find chaotic bars</div>
      </div>
      <div className="p-3 space-y-3">
        <div className="grid grid-cols-2 lg:grid-cols-[1fr_1fr_1fr_0.8fr_auto] gap-2 items-end">
          <label className="text-[11px] text-dim">
            Instrument
            <select
              value={form.instrument}
              onChange={(e) => set("instrument", e.target.value)}
              className="mt-1 h-9 w-full rounded-md border border-input bg-bg-1 px-3 py-1 text-sm text-foreground"
              data-testid="volatility-instrument-select"
            >
              {INSTRUMENTS.map((inst) => <option key={inst} value={inst}>{inst}</option>)}
            </select>
          </label>
          <label className="text-[11px] text-dim">
            From
            <Input type="date" value={form.from_date} onChange={(e) => set("from_date", e.target.value)}
              className="mt-1 bg-bg-1 border-line" data-testid="volatility-from-date" />
          </label>
          <label className="text-[11px] text-dim">
            To
            <Input type="date" value={form.to_date} onChange={(e) => set("to_date", e.target.value)}
              className="mt-1 bg-bg-1 border-line" data-testid="volatility-to-date" />
          </label>
          <label className="text-[11px] text-dim">
            Spike ≥
            <Input type="number" step="0.1" min="1" value={form.spike_threshold}
              onChange={(e) => set("spike_threshold", e.target.value)}
              className="mt-1 bg-bg-1 border-line text-right" data-testid="volatility-threshold" />
          </label>
          <Button size="sm" onClick={run} disabled={loading}
            className="h-9 text-xs bg-bg-3 border border-line hover:bg-bg-2" data-testid="volatility-run-button">
            <AlertTriangle className="w-3 h-3 mr-1" />
            {loading ? "Auditing..." : "Audit"}
          </Button>
        </div>

        {result && (
          <>
            <div className="grid grid-cols-2 lg:grid-cols-4 gap-2" data-testid="volatility-summary">
              <div className="rounded-md border border-line bg-bg-2 p-2">
                <div className="text-[10px] uppercase tracking-wider text-dimmer">Total Bars</div>
                <div className="text-sm font-mono mt-0.5">{fmtInt(summary.total_bars || 0)}</div>
              </div>
              <div className="rounded-md border border-line bg-bg-2 p-2">
                <div className="text-[10px] uppercase tracking-wider text-dimmer">Spike Bars</div>
                <div className="text-sm font-mono mt-0.5 text-warning">{fmtInt(summary.spike_bars || 0)}</div>
              </div>
              <div className="rounded-md border border-line bg-bg-2 p-2">
                <div className="text-[10px] uppercase tracking-wider text-dimmer">Spike Share</div>
                <div className="text-sm font-mono mt-0.5">{fmtNum(summary.spike_pct || 0, 2)}%</div>
              </div>
              <div className="rounded-md border border-line bg-bg-2 p-2">
                <div className="text-[10px] uppercase tracking-wider text-dimmer">Max Ratio</div>
                <div className="text-sm font-mono mt-0.5">{summary.max_ratio != null ? `${fmtNum(summary.max_ratio, 2)}×` : "—"}</div>
              </div>
            </div>

            <div className="overflow-x-auto">
              <table className="w-full text-xs" data-testid="volatility-spikes-table">
                <thead>
                  <tr className="text-dim border-b border-line">
                    <th className="text-left p-2">#</th>
                    <th className="text-left p-2">Bar (IST)</th>
                    <th className="text-right p-2">Close</th>
                    <th className="text-right p-2">Realized 5m</th>
                    <th className="text-right p-2">Baseline 30d</th>
                    <th className="text-right p-2">Ratio</th>
                  </tr>
                </thead>
                <tbody>
                  {spikes.length === 0 && (
                    <tr><td colSpan="6" className="p-4 text-center text-dimmer">No spike bars in this window at the chosen threshold.</td></tr>
                  )}
                  {spikes.map((s, i) => (
                    <tr key={`${s.ts}-${i}`} className="border-b border-line" data-testid="volatility-spike-row">
                      <td className="p-2 font-mono text-dim">{i + 1}</td>
                      <td className="p-2 font-mono">{s.datetime || tsToFull(Number(s.ts))}</td>
                      <td className="p-2 text-right font-mono">{fmtNum(s.close, 2)}</td>
                      <td className="p-2 text-right font-mono">{fmtNum(s.realized_vol_5m, 4)}</td>
                      <td className="p-2 text-right font-mono text-dim">{fmtNum(s.vol_baseline_30d, 4)}</td>
                      <td className="p-2 text-right font-mono text-warning">{fmtNum(s.vol_ratio, 2)}×</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
