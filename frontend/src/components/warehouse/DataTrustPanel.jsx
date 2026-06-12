// Calendar-aware data trust audit + danger zone (split from pages/DataWarehouse.jsx).
import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { ShieldCheck, Trash2 } from "lucide-react";
import { fmtInt } from "@/lib/fmt";
import { INSTRUMENTS, AuditStat } from "./shared";

export function DataTrustPanel({
  auditForm,
  setAuditForm,
  auditResult,
  auditLoading,
  onAudit,
  clearInstrument,
  setClearInstrument,
  clearing,
  onClear,
  optionClearing,
  onClearOptions,
}) {
  const set = (key, value) => setAuditForm((prev) => ({ ...prev, [key]: value }));
  const [dangerOpen, setDangerOpen] = useState(false);
  const summary = auditResult?.summary;
  const days = auditResult?.days || [];
  const statusClass = (status) => {
    if (status === "ok") return "bg-emerald-950 text-emerald-200 border-emerald-900";
    if (status === "missing" || status === "hash_mismatch") return "bg-rose-950 text-rose-200 border-rose-900";
    if (status === "incomplete" || status === "unverified") return "bg-amber-950 text-amber-200 border-amber-900";
    return "bg-bg-3 text-dim border-line";
  };

  return (
    <div className="rounded-lg border border-line bg-bg-1" data-testid="warehouse-audit-panel">
      <div className="px-3 py-2 border-b border-line flex items-center gap-2">
        <ShieldCheck className="w-4 h-4 text-info" />
        <div className="text-xs font-semibold uppercase tracking-wider text-dim">Data Trust Audit</div>
        {summary && (
          <span className={`ml-auto text-[10px] px-1.5 py-0.5 rounded border font-mono ${summary.complete ? "bg-emerald-950 text-emerald-200 border-emerald-900" : "bg-amber-950 text-amber-200 border-amber-900"}`}>
            {summary.complete ? "trusted" : "needs review"}
          </span>
        )}
      </div>
      <div className="p-3 space-y-3">
        <div className="grid grid-cols-1 xl:grid-cols-[minmax(0,2fr)_minmax(260px,1fr)] gap-3">
          <div className="rounded-md border border-line bg-bg-2 p-3">
            <div className="grid grid-cols-2 lg:grid-cols-[1fr_1fr_1fr_auto] gap-2 items-end">
              <label className="text-[11px] text-dim">
                Instrument
                <select
                  value={auditForm.instrument}
                  onChange={(e) => set("instrument", e.target.value)}
                  className="mt-1 h-9 w-full rounded-md border border-input bg-bg-1 px-3 py-1 text-sm text-foreground"
                  data-testid="warehouse-audit-instrument"
                >
                  {INSTRUMENTS.map((inst) => <option key={inst} value={inst}>{inst}</option>)}
                </select>
              </label>
              <label className="text-[11px] text-dim">
                From
                <Input
                  type="date"
                  value={auditForm.from_date}
                  onChange={(e) => set("from_date", e.target.value)}
                  className="mt-1 bg-bg-1 border-line"
                  data-testid="warehouse-audit-from-date"
                />
              </label>
              <label className="text-[11px] text-dim">
                To
                <Input
                  type="date"
                  value={auditForm.to_date}
                  onChange={(e) => set("to_date", e.target.value)}
                  className="mt-1 bg-bg-1 border-line"
                  data-testid="warehouse-audit-to-date"
                />
              </label>
              <Button
                size="sm"
                onClick={onAudit}
                disabled={auditLoading}
                className="h-9 text-xs bg-bg-3 border border-line hover:bg-bg-2"
                data-testid="warehouse-audit-button"
              >
                <ShieldCheck className="w-3 h-3 mr-1" />
                {auditLoading ? "Auditing..." : "Audit"}
              </Button>
            </div>
          </div>

          <div className="rounded-md border border-rose-900/50 bg-bg-2 p-3" data-testid="warehouse-danger-zone">
            <button
              onClick={() => setDangerOpen((v) => !v)}
              className="w-full flex items-center gap-2 text-left"
              data-testid="warehouse-danger-toggle"
            >
              <Trash2 className="w-3.5 h-3.5 text-rose-300" />
              <span className="text-[11px] font-semibold uppercase tracking-wider text-rose-200">Danger zone — delete stored data</span>
              <span className="text-[10px] text-dimmer ml-auto">{dangerOpen ? "hide" : "show"}</span>
            </button>
            {dangerOpen && (
              <div className="mt-3 space-y-3">
                <div className="text-[10px] text-dimmer">
                  Deletions are permanent and require typing the instrument name to confirm.
                </div>
                <div className="grid grid-cols-[1fr_auto] gap-2 items-end">
                  <label className="text-[11px] text-dim">
                    Index candles + hashes + runs
                    <select
                      value={clearInstrument}
                      onChange={(e) => setClearInstrument(e.target.value)}
                      className="mt-1 h-9 w-full rounded-md border border-input bg-bg-1 px-3 py-1 text-sm text-foreground"
                      data-testid="warehouse-clear-instrument"
                    >
                      <option value="ALL">ALL</option>
                      {INSTRUMENTS.map((inst) => <option key={inst} value={inst}>{inst}</option>)}
                    </select>
                  </label>
                  <Button
                    size="sm"
                    variant="secondary"
                    onClick={onClear}
                    disabled={clearing}
                    className="h-9 text-xs border border-rose-900 bg-rose-950/60 text-rose-100 hover:bg-rose-950"
                    data-testid="warehouse-clear-button"
                  >
                    <Trash2 className="w-3 h-3 mr-1" />
                    {clearing ? "Clearing..." : "Clear index"}
                  </Button>
                </div>
                <div className="flex items-center justify-between gap-2">
                  <span className="text-[10px] text-dimmer">Option candles for {auditForm.instrument} (keeps contract metadata + index candles).</span>
                  <Button
                    size="sm"
                    variant="secondary"
                    onClick={onClearOptions}
                    disabled={optionClearing}
                    className="h-8 text-xs border border-rose-900 bg-rose-950/60 text-rose-100 hover:bg-rose-950 shrink-0"
                    data-testid="option-clear-button"
                  >
                    <Trash2 className="w-3 h-3 mr-1" />
                    {optionClearing ? "Clearing..." : "Clear options"}
                  </Button>
                </div>
              </div>
            )}
          </div>
        </div>

        {summary && (
          <div className="grid grid-cols-2 lg:grid-cols-6 gap-2">
            <AuditStat label="Complete" value={`${summary.complete_days}/${summary.expected_days}`} />
            <AuditStat label="Candles" value={`${fmtInt(summary.stored_candles)}/${fmtInt(summary.expected_candles)}`} />
            <AuditStat label="Missing" value={fmtInt(summary.missing_days)} />
            <AuditStat label="Incomplete" value={fmtInt(summary.incomplete_days)} />
            <AuditStat label="Hash mismatch" value={fmtInt(summary.hash_mismatch_days)} />
            <AuditStat label="Unverified" value={fmtInt(summary.unverified_days)} />
          </div>
        )}

        {days.length > 0 && (
          <div className="flex flex-wrap gap-1.5" data-testid="warehouse-audit-days">
            {days.slice(0, 60).map((day) => (
              <span
                key={day.date}
                className={`text-[10px] px-1.5 py-0.5 rounded border font-mono ${statusClass(day.status)}`}
                title={`${day.date}: ${day.stored_candles}/${day.expected_candles} candles · ${day.status}`}
              >
                {day.date.slice(5)} {day.status}
              </span>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
