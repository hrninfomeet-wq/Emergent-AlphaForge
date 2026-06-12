// Manual option data planner — advanced tool (split from pages/DataWarehouse.jsx).
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Database, Download, RefreshCw } from "lucide-react";
import { fmtInt, fmtNum } from "@/lib/fmt";
import { INSTRUMENTS, MONEYNESS_OPTIONS, LEG_OPTIONS, AuditStat } from "./shared";

export function OptionWarehousePanel({
  status,
  form,
  setForm,
  plan,
  fetchResult,
  fetchJob,
  planning,
  fetching,
  onPreview,
  onFetch,
}) {
  const connected = status?.connected && !status?.expired;
  const summary = plan?.summary || {};
  const guidance = plan?.chunk_guidance;
  const items = plan?.items || [];
  const missingCount = summary.missing_data_contracts || 0;
  const missingMetaCount = summary.missing_contract_count || plan?.missing_count || 0;
  const plannedCoverage = Number(summary.planned_coverage_pct ?? 0);
  const fetchDateCount = summary.fetch_date_count || 0;
  const fetchContractCount = form.fetch_missing_only ? missingCount : items.length;
  const fetchBlockedByMax = !!plan && fetchContractCount > Number(form.max_contracts || 0);
  const fetchNothingToDo = !!plan && form.fetch_missing_only && missingCount === 0;
  const fixedExpiryMissing = form.expiry_policy === "fixed" && !form.fixed_expiry_date;
  const set = (key, value) => setForm((prev) => ({ ...prev, [key]: value }));
  const toggleListValue = (key, value, orderedValues) => {
    setForm((prev) => {
      const current = prev[key] || [];
      const nextRaw = current.includes(value)
        ? current.filter((item) => item !== value)
        : [...current, value];
      const next = orderedValues.filter((item) => nextRaw.includes(item));
      return { ...prev, [key]: next.length ? next : [value] };
    });
  };

  return (
    <div className="rounded-lg border border-line bg-bg-1" data-testid="option-warehouse-panel">
      <div className="px-3 py-2 border-b border-line flex items-center gap-2">
        <Database className="w-4 h-4 text-info" />
        <div className="text-xs font-semibold uppercase tracking-wider text-dim">Option Data Planner</div>
        {plan && (
          <span className="ml-auto text-[10px] px-1.5 py-0.5 rounded border font-mono bg-bg-3 text-dim border-line">
            {fmtInt(summary.planned_contracts || 0)} contracts
          </span>
        )}
      </div>
      <div className="p-3 space-y-3">
        <div className="rounded-md border border-line bg-bg-2 p-3">
          <div className="grid grid-cols-2 xl:grid-cols-[1fr_1fr_1fr_1fr_0.8fr_0.8fr_auto] gap-2 items-end">
            <label className="text-[11px] text-dim">
              Instrument
              <select
                value={form.underlying}
                onChange={(e) => set("underlying", e.target.value)}
                className="mt-1 h-9 w-full rounded-md border border-input bg-bg-1 px-3 py-1 text-sm text-foreground"
                data-testid="option-warehouse-instrument"
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
                data-testid="option-warehouse-from-date"
              />
            </label>
            <label className="text-[11px] text-dim">
              To
              <Input
                type="date"
                value={form.to_date}
                onChange={(e) => set("to_date", e.target.value)}
                className="mt-1 bg-bg-1 border-line"
                data-testid="option-warehouse-to-date"
              />
            </label>
            <label className="text-[11px] text-dim">
              Expiry
              <select
                value={form.expiry_policy}
                onChange={(e) => set("expiry_policy", e.target.value)}
                className="mt-1 h-9 w-full rounded-md border border-input bg-bg-1 px-3 py-1 text-sm text-foreground"
                data-testid="option-warehouse-expiry"
              >
                <option value="next_available">Next available</option>
                <option value="fixed">Fixed date</option>
              </select>
            </label>
            <label className="text-[11px] text-dim">
              Sample
              <Input
                type="number"
                min="1"
                max="375"
                value={form.sample_interval_minutes}
                onChange={(e) => set("sample_interval_minutes", e.target.value)}
                className="mt-1 bg-bg-1 border-line text-right"
                data-testid="option-warehouse-sample-minutes"
              />
            </label>
            <label className="text-[11px] text-dim">
              Chunk
              <Input
                type="number"
                min="1"
                max="30"
                placeholder="Auto"
                value={form.chunk_days}
                onChange={(e) => set("chunk_days", e.target.value)}
                className="mt-1 bg-bg-1 border-line text-right"
                data-testid="option-warehouse-chunk-days"
              />
            </label>
            <Button
              size="sm"
              onClick={onPreview}
              disabled={planning || fixedExpiryMissing}
              className="h-9 text-xs bg-bg-3 border border-line hover:bg-bg-2"
              data-testid="option-warehouse-preview-button"
            >
              <RefreshCw className="w-3 h-3 mr-1" />
              {planning ? "Planning..." : fixedExpiryMissing ? "Set Expiry" : "Preview"}
            </Button>
          </div>

          {form.expiry_policy === "fixed" && (
            <div className="mt-2 max-w-xs">
              <label className="text-[11px] text-dim">
                Fixed expiry date
                <Input
                  type="date"
                  value={form.fixed_expiry_date}
                  onChange={(e) => set("fixed_expiry_date", e.target.value)}
                  className="mt-1 bg-bg-1 border-line"
                  data-testid="option-warehouse-fixed-expiry-date"
                />
              </label>
            </div>
          )}
          <div className="mt-3 grid grid-cols-1 lg:grid-cols-3 gap-2 text-[11px] text-dim" data-testid="option-warehouse-help">
            <div className="rounded-md border border-line bg-bg-1 p-2">
              <span className="font-semibold text-foreground">Expiry:</span> Next available uses stored contract expiries on or after each sampled spot date. Fixed date forces one expiry across the whole window.
            </div>
            <div className="rounded-md border border-line bg-bg-1 p-2">
              <span className="font-semibold text-foreground">Sample:</span> Sample every N minutes to choose strikes. Use 15 for quick planning; Use 1 for final strategy prep when accuracy matters.
            </div>
            <div className="rounded-md border border-line bg-bg-1 p-2">
              <span className="font-semibold text-foreground">History:</span> Historical expiry changes are handled only when those old contracts are stored locally; missing contract metadata means sync/backfill first.
            </div>
          </div>
          <div className="mt-2 rounded-md border border-line bg-bg-1 p-2 text-[11px] text-dim" data-testid="option-warehouse-background-help">
            Preview is the trust check for planner-selected moneyness. Fetch runs in background and only requests missing selected dates; the Option Coverage Heatmap below shows what is stored.
          </div>
        </div>

        <div className="grid grid-cols-1 xl:grid-cols-[minmax(0,1.5fr)_minmax(0,1fr)] gap-3">
          <div className="rounded-md border border-line bg-bg-2 p-3 space-y-3">
            <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
              <div>
                <div className="text-[10px] uppercase tracking-wider text-dimmer mb-1">Moneyness</div>
                <div className="flex flex-wrap gap-1.5">
                  {MONEYNESS_OPTIONS.map((label) => {
                    const active = form.moneyness.includes(label);
                    return (
                      <Button
                        key={label}
                        type="button"
                        size="sm"
                        variant="secondary"
                        onClick={() => toggleListValue("moneyness", label, MONEYNESS_OPTIONS)}
                        className={`h-7 px-2 text-xs border ${active ? "border-info bg-bg-3 text-foreground" : "border-line bg-bg-1 text-dim"}`}
                        data-testid={`option-warehouse-moneyness-${label}`}
                      >
                        {label.toUpperCase()}
                      </Button>
                    );
                  })}
                </div>
              </div>
              <div>
                <div className="text-[10px] uppercase tracking-wider text-dimmer mb-1">Legs</div>
                <div className="flex flex-wrap gap-1.5">
                  {LEG_OPTIONS.map((label) => {
                    const active = form.legs.includes(label);
                    return (
                      <Button
                        key={label}
                        type="button"
                        size="sm"
                        variant="secondary"
                        onClick={() => toggleListValue("legs", label, LEG_OPTIONS)}
                        className={`h-7 px-3 text-xs border ${active ? "border-info bg-bg-3 text-foreground" : "border-line bg-bg-1 text-dim"}`}
                        data-testid={`option-warehouse-leg-${label.toLowerCase()}`}
                      >
                        {label}
                      </Button>
                    );
                  })}
                </div>
              </div>
            </div>

            <div className="grid grid-cols-[1fr_0.8fr_auto] gap-2 items-end">
              <label className="text-[11px] text-dim flex items-center gap-2 h-9">
                <input
                  type="checkbox"
                  checked={form.fetch_missing_only}
                  onChange={(e) => set("fetch_missing_only", e.target.checked)}
                  className="h-4 w-4"
                  data-testid="option-warehouse-missing-only"
                />
                Missing only
              </label>
              <label className="text-[11px] text-dim">
                Max contracts
                <Input
                  type="number"
                  min="1"
                  max="500"
                  value={form.max_contracts}
                  onChange={(e) => set("max_contracts", e.target.value)}
                  className="mt-1 bg-bg-1 border-line text-right"
                  data-testid="option-warehouse-max-contracts"
                />
              </label>
              <Button
                size="sm"
                onClick={onFetch}
                disabled={fetching || !connected || !plan || items.length === 0 || fetchBlockedByMax || fetchNothingToDo || fixedExpiryMissing}
                className="h-9 text-xs bg-bg-3 border border-line hover:bg-bg-2"
                data-testid="option-warehouse-fetch-button"
              >
                <Download className="w-3 h-3 mr-1" />
                {fetching ? "Fetching..." : fixedExpiryMissing ? "Set Expiry" : fetchBlockedByMax ? "Raise Max" : fetchNothingToDo ? "Complete" : "Fetch Missing"}
              </Button>
            </div>
          </div>

          <div className="rounded-md border border-line bg-bg-2 p-3" data-testid="option-warehouse-chunk-guidance">
            <div className="text-[10px] uppercase tracking-wider text-dimmer">Chunk guidance</div>
            {guidance ? (
              <div className="mt-2 grid grid-cols-2 gap-2 text-xs">
                <div>
                  <div className="text-dimmer">Mode</div>
                  <div className="font-mono">{guidance.mode}</div>
                </div>
                <div>
                  <div className="text-dimmer">Days/call</div>
                  <div className="font-mono">{guidance.chunk_days}</div>
                </div>
                <div>
                  <div className="text-dimmer">API calls</div>
                  <div className="font-mono">{fmtInt(guidance.estimated_api_calls || 0)}</div>
                </div>
                <div>
                  <div className="text-dimmer">Contracts</div>
                  <div className="font-mono">{fmtInt(guidance.contracts || 0)}</div>
                </div>
              </div>
            ) : (
              <div className="mt-2 text-[11px] text-dim">
                Auto picks smaller date chunks as the contract count grows. Use manual 1-3 for very large runs; 7 is fine for small previews.
              </div>
            )}
          </div>
        </div>

        {fetchJob && (
          <div className="rounded-md border border-line bg-bg-2 p-3" data-testid="option-warehouse-fetch-progress">
            <div className="flex items-center justify-between gap-2 text-xs">
              <span className="font-mono uppercase">{fetchJob.status}</span>
              <span className="font-mono text-dimmer">{fmtNum(fetchJob.progress_pct || 0, 1)}%</span>
            </div>
            <div className="mt-2 h-2 rounded bg-bg-3 overflow-hidden">
              <div
                className="h-full bg-info transition-all"
                style={{ width: `${Math.min(100, Math.max(0, Number(fetchJob.progress_pct || 0)))}%` }}
              />
            </div>
            <div className="mt-2 grid grid-cols-2 lg:grid-cols-5 gap-2 text-[11px] text-dim">
              <div>
                <span className="text-dimmer">Tasks</span>
                <span className="ml-1 font-mono">{fmtInt(fetchJob.completed_tasks || 0)}/{fmtInt(fetchJob.total_tasks || 0)}</span>
              </div>
              <div>
                <span className="text-dimmer">Contracts</span>
                <span className="ml-1 font-mono">{fmtInt(fetchJob.fetch_contracts || 0)}</span>
              </div>
              <div>
                <span className="text-dimmer">Fetched</span>
                <span className="ml-1 font-mono">{fmtInt(fetchJob.total_fetched || 0)}</span>
              </div>
              <div>
                <span className="text-dimmer">Added</span>
                <span className="ml-1 font-mono">{fmtInt(fetchJob.candles_added || 0)}</span>
              </div>
              <div>
                <span className="text-dimmer">Matched</span>
                <span className="ml-1 font-mono">{fmtInt(fetchJob.matched_existing || 0)}</span>
              </div>
            </div>
            {Boolean(fetchJob.failed?.length) && (
              <div className="mt-2 text-[11px] text-danger">
                Failed tasks: {fmtInt(fetchJob.failed.length)}. Lower Chunk to 1-2 or retry the same preview.
              </div>
            )}
          </div>
        )}

        {plan && (
          <div className="space-y-3" data-testid="option-warehouse-plan-summary">
            <div className="grid grid-cols-2 lg:grid-cols-6 gap-2">
              <AuditStat label="Spot used" value={fmtInt(summary.spot_candles_used || 0)} />
              <AuditStat label="Contracts" value={fmtInt(summary.planned_contracts || 0)} />
              <AuditStat label="Need fetch" value={fmtInt(summary.missing_data_contracts || 0)} />
              <AuditStat label="Coverage" value={`${plannedCoverage}%`} />
              <AuditStat label="Missing meta" value={fmtInt(missingMetaCount)} />
              <AuditStat label="Selections" value={fmtInt(summary.selection_count || 0)} />
            </div>

            <div className={`rounded-md border p-3 text-xs ${missingCount === 0 && missingMetaCount === 0 && items.length > 0 ? "border-emerald-900 bg-emerald-950/30 text-emerald-100" : "border-amber-900 bg-amber-950/30 text-amber-100"}`} data-testid="option-warehouse-planned-coverage">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <span className="font-semibold">Planned coverage</span>
                <span className="font-mono">{plannedCoverage}% · {fmtInt(summary.stored_selected_date_candles || 0)}/{fmtInt(summary.expected_candles_per_selected_dates || 0)} candles</span>
              </div>
              <div className="mt-1 text-[11px] opacity-90">
                {missingCount === 0 && missingMetaCount === 0 && items.length > 0
                  ? "This planner-selected moneyness window is locally covered."
                  : `${fmtInt(missingCount)} contracts still need option candles across ${fmtInt(fetchDateCount)} selected dates; ${fmtInt(missingMetaCount)} selections need contract metadata.`}
              </div>
            </div>

            {plan.warning && (
              <div className="rounded-md border border-amber-900 bg-amber-950/40 p-2 text-xs text-amber-100">
                {plan.warning}
              </div>
            )}

            {fetchResult && (
              <div className="rounded-md border border-line bg-bg-2 p-3 text-xs" data-testid="option-warehouse-fetch-summary">
                <span className="font-mono">{fetchResult.status}</span>
                <span className="text-dim ml-2">
                  {fmtInt(fetchResult.fetch_contracts || 0)} contracts · {fmtInt(fetchResult.total_fetched || 0)} candles · +{fmtInt(fetchResult.candles_added || 0)} / ~{fmtInt(fetchResult.candles_updated || 0)}
                </span>
              </div>
            )}

            <div className="overflow-x-auto rounded-md border border-line">
              <table className="w-full text-xs" data-testid="option-warehouse-plan-table">
                <thead>
                  <tr className="text-dim border-b border-line bg-bg-2">
                    <th className="text-left p-2">Symbol</th>
                    <th className="text-left p-2">Expiry</th>
                    <th className="text-right p-2">Strike</th>
                    <th className="text-left p-2">Side</th>
                    <th className="text-left p-2">Selected</th>
                    <th className="text-right p-2">Dates</th>
                    <th className="text-right p-2">Need</th>
                    <th className="text-right p-2">Coverage</th>
                  </tr>
                </thead>
                <tbody>
                  {items.slice(0, 80).map((item) => (
                    <tr key={item.instrument_key} className="border-b border-line" data-testid="option-warehouse-plan-row">
                      <td className="p-2 font-mono">{item.trading_symbol || item.instrument_key}</td>
                      <td className="p-2 font-mono text-dim">{item.expiry_date}</td>
                      <td className="p-2 font-mono text-right">{item.strike}</td>
                      <td className="p-2 font-mono">{item.side}</td>
                      <td className="p-2 text-dim">{item.selected_as}</td>
                      <td className="p-2 font-mono text-right">{fmtInt(item.selected_date_count || 0)}</td>
                      <td className="p-2 font-mono text-right">{item.needs_fetch ? fmtInt(item.fetch_date_count || 0) : "no"}</td>
                      <td className="p-2 font-mono text-right">{item.coverage_pct || 0}%</td>
                    </tr>
                  ))}
                  {items.length === 0 && (
                    <tr><td colSpan="8" className="p-4 text-center text-dimmer">No option contracts selected</td></tr>
                  )}
                </tbody>
              </table>
            </div>

            {items.length > 80 && (
              <div className="text-[11px] text-dimmer">Showing first 80 of {fmtInt(items.length)} planned contracts.</div>
            )}
            {missingCount === 0 && items.length > 0 && (
              <div className="text-[11px] text-dim">Stored option data covers the planner-selected window.</div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
