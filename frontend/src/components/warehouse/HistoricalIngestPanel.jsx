import { useState } from "react";
import { api } from "@/lib/api";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { History, Loader2, ShieldCheck } from "lucide-react";
import { useJobs } from "@/lib/jobs";
import { INSTRUMENTS, AuditStat, dateInput } from "@/components/warehouse/shared";
import { dateToMs } from "@/lib/time";
import { fmtInt } from "@/lib/fmt";

/**
 * HistoricalIngestPanel — ingest an arbitrary past date range, safely.
 *
 * The flow is a strict state machine: Dry-run plan (always first — the execute
 * button does not exist until a plan for the CURRENT form values is shown) →
 * explicit confirm → background ingest chain (spot → expired contracts → band
 * option candles, upsert-only) → "Verify range" re-audits per-day counts +
 * integrity hashes and diffs them against the snapshot taken at plan time, so
 * "no existing day was degraded" is checked, not assumed. Changing any form
 * field invalidates the plan and forces a fresh dry-run.
 */
export function HistoricalIngestPanel({ status, onRefresh }) {
  const { startHygieneBatch, hygiene, isHygieneActive } = useJobs();
  const connected = Boolean(status?.connected && !status?.expired);
  const [form, setFormRaw] = useState({
    instrument: "NIFTY",
    from_date: dateInput(120),
    to_date: dateInput(90),
    include_options: true,
  });
  const [plan, setPlan] = useState(null);
  const [before, setBefore] = useState(null);
  const [after, setAfter] = useState(null);
  const [busy, setBusy] = useState(false);
  const [verifying, setVerifying] = useState(false);

  // Any form change invalidates the plan — execute must re-plan first.
  const set = (k, v) => {
    setFormRaw((prev) => ({ ...prev, [k]: v }));
    setPlan(null);
    setAfter(null);
  };

  const payload = () => ({
    instruments: [form.instrument],
    from_date: form.from_date,
    to_date: form.to_date,
    include_options: form.include_options,
  });

  const runPlan = async () => {
    setBusy(true);
    setAfter(null);
    try {
      const res = await api.dataHygieneCatchUp({ ...payload(), dry_run: true });
      setPlan(res.plan);
      try {
        const audit = await api.auditWarehouse(
          form.instrument, dateToMs(form.from_date, false), dateToMs(form.to_date, true));
        setBefore(audit);
      } catch {
        setBefore(null);
      }
      const s = res.plan?.summary || {};
      toast.info(`Plan: ${s.total_actions ?? 0} action(s) over ${s.trading_days ?? "?"} trading day(s).`);
    } catch (e) {
      setPlan(null);
      toast.error(`Dry-run plan failed: ${e.response?.data?.detail || e.message}`);
    } finally {
      setBusy(false);
    }
  };

  const execute = async () => {
    if (!plan) return;
    const s = plan.summary || {};
    if (!s.total_actions) {
      toast.info("Range already complete — nothing to ingest.");
      return;
    }
    const ok = window.confirm(
      `Ingest ${s.total_actions} action(s) for ${form.instrument} ` +
      `${form.from_date} → ${s.target_end}?\n\nUpsert-only: existing candles are ` +
      `never deleted; days can only gain data.`);
    if (!ok) return;
    setBusy(true);
    try {
      const res = await api.dataHygieneCatchUp({ ...payload(), dry_run: false, confirm: true });
      if (res.up_to_date) {
        toast.info("Range already complete — nothing submitted.");
        return;
      }
      const n = startHygieneBatch(res);
      toast.success(`Submitted ${n} background job(s). Watch progress below, then "Verify range".`);
    } catch (e) {
      toast.error(`Historical ingest failed: ${e.response?.data?.detail || e.message}`);
    } finally {
      setBusy(false);
    }
  };

  const verify = async () => {
    setVerifying(true);
    try {
      const audit = await api.auditWarehouse(
        form.instrument, dateToMs(form.from_date, false), dateToMs(form.to_date, true));
      setAfter(audit);
      onRefresh?.();
    } catch (e) {
      toast.error(`Range verify failed: ${e.response?.data?.detail || e.message}`);
    } finally {
      setVerifying(false);
    }
  };

  // Diff the plan-time snapshot against the post-ingest audit: additions are
  // intended; a shrunk or hash-changed previously-complete day is flagged loud.
  const diff = (() => {
    if (!before?.days || !after?.days) return null;
    const b = Object.fromEntries(before.days.map((d) => [d.date, d]));
    const added = [];
    const degraded = [];
    const corrected = [];
    for (const d of after.days) {
      const prev = b[d.date];
      if (!prev || prev.status === "missing") {
        if (d.status !== "missing") added.push(d.date);
        continue;
      }
      if ((d.stored_candles ?? 0) < (prev.stored_candles ?? 0)) {
        degraded.push(`${d.date} (${prev.stored_candles} → ${d.stored_candles} candles)`);
      } else if (prev.status === "ok" && prev.stored_hash && d.stored_hash &&
                 prev.stored_hash !== d.stored_hash) {
        corrected.push(d.date);
      } else if ((d.stored_candles ?? 0) > (prev.stored_candles ?? 0)) {
        added.push(d.date);
      }
    }
    return { added, degraded, corrected };
  })();

  const summaryBox = (label, audit) => (
    <div className="rounded-md border border-line bg-bg-2 p-2">
      <div className="text-[10px] uppercase tracking-wider text-dimmer mb-1">{label}</div>
      {audit?.summary ? (
        <div className="grid grid-cols-2 gap-1 text-[11px] font-mono">
          <span className="text-dim">Complete days</span>
          <span className="text-right">{audit.summary.complete_days}/{audit.summary.expected_days}</span>
          <span className="text-dim">Stored candles</span>
          <span className="text-right">{fmtInt(audit.summary.stored_candles)}</span>
          <span className="text-dim">Missing / incomplete</span>
          <span className="text-right">{audit.summary.missing_days} / {audit.summary.incomplete_days}</span>
          <span className="text-dim">Hash mismatches</span>
          <span className="text-right">{audit.summary.hash_mismatch_days}</span>
        </div>
      ) : (
        <div className="text-[11px] text-dimmer">no snapshot</div>
      )}
    </div>
  );

  const inst = plan?.instruments?.find(
    (i) => String(i.instrument).toUpperCase() === form.instrument) || plan?.instruments?.[0];

  return (
    <div className="rounded-lg border border-line bg-bg-1" data-testid="historical-ingest-panel">
      <div className="px-3 py-2 border-b border-line flex items-center gap-2">
        <History className="w-4 h-4 text-info" />
        <div className="text-xs font-semibold uppercase tracking-wider text-dim">
          Historical range ingestion
        </div>
        <span className="text-[10px] text-dimmer">
          always dry-runs first · upsert-only, never deletes existing candles
        </span>
      </div>
      <div className="p-3 space-y-3">
        <div className="flex flex-wrap items-end gap-2">
          <label className="text-[11px] text-dim">
            Instrument
            <select value={form.instrument} onChange={(e) => set("instrument", e.target.value)}
              className="mt-1 h-8 block rounded-md border border-input bg-bg-2 px-2 text-xs"
              data-testid="historical-instrument">
              {INSTRUMENTS.map((i) => <option key={i} value={i}>{i}</option>)}
            </select>
          </label>
          <label className="text-[11px] text-dim">
            From
            <Input type="date" value={form.from_date} onChange={(e) => set("from_date", e.target.value)}
              className="mt-1 bg-bg-2 border-line h-8" data-testid="historical-from" />
          </label>
          <label className="text-[11px] text-dim">
            To
            <Input type="date" value={form.to_date} onChange={(e) => set("to_date", e.target.value)}
              className="mt-1 bg-bg-2 border-line h-8" data-testid="historical-to" />
          </label>
          <label className="text-[11px] text-dim flex items-center gap-1.5 h-8">
            <input type="checkbox" checked={form.include_options}
              onChange={(e) => set("include_options", e.target.checked)}
              className="h-3.5 w-3.5 rounded border-line" data-testid="historical-include-options" />
            include options (expired contracts + band candles)
          </label>
          <Button size="sm" variant="outline" disabled={busy} onClick={runPlan}
            className="h-8 text-xs" data-testid="historical-plan-button">
            {busy && !plan ? <Loader2 className="w-3.5 h-3.5 mr-1 animate-spin" /> : null}
            Dry-run plan
          </Button>
          <Button size="sm" disabled={!plan || busy || isHygieneActive()} onClick={execute}
            className="h-8 text-xs bg-info text-bg-0 hover:bg-info/90"
            title={!plan ? "Run the dry-run plan first — execution is gated on a fresh plan" : undefined}
            data-testid="historical-execute-button">
            Ingest range…
          </Button>
        </div>

        {plan && inst && (
          <div className="rounded-md border border-line bg-bg-2 p-2 space-y-2" data-testid="historical-plan-box">
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
              <AuditStat label="Trading days" value={inst.trading_days} />
              <AuditStat label="Days with data" value={`${inst.stored_days}/${inst.trading_days}`} />
              <AuditStat label="Missing / incomplete" value={`${inst.missing_trading_days} / ${inst.incomplete_days?.length ?? 0}`} />
              <AuditStat label="Est. new spot candles" value={fmtInt(inst.expected_new_spot_candles ?? 0)} />
            </div>
            {(inst.actions || []).map((a) => (
              <div key={a.id} className="text-[11px] font-mono text-dim">
                <span className="uppercase text-info mr-1.5">{a.kind}</span>
                {a.from_date} → {a.to_date} — <span className="text-dimmer">{a.reason}</span>
              </div>
            ))}
            {inst.up_to_date && (
              <div className="text-[11px] text-emerald-300 font-mono">
                Range already complete — nothing to ingest.
              </div>
            )}
            {(plan.warnings || []).map((w, i) => (
              <div key={i} className="text-[10px] leading-snug text-warning">⚠ {w}</div>
            ))}
          </div>
        )}

        {hygiene && (
          <div className="text-[11px] font-mono text-dim">
            Ingest progress: {hygiene.completed}/{hygiene.total} job(s)
            {hygiene.failed ? <span className="text-danger"> · {hygiene.failed} failed</span> : null}
          </div>
        )}

        <div className="flex items-center gap-2">
          <Button size="sm" variant="outline" disabled={verifying || !before} onClick={verify}
            className="h-8 text-xs" data-testid="historical-verify-button"
            title="Re-audit the range (per-day counts + integrity hashes) and diff against the plan-time snapshot">
            {verifying ? <Loader2 className="w-3.5 h-3.5 mr-1 animate-spin" /> : <ShieldCheck className="w-3.5 h-3.5 mr-1" />}
            Verify range (before vs after)
          </Button>
          {!before && <span className="text-[10px] text-dimmer">run a dry-run plan first to snapshot the before state</span>}
        </div>

        {before && after && (
          <div className="space-y-2" data-testid="historical-verify-report">
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
              {summaryBox("Before (at plan time)", before)}
              {summaryBox("After (verified now)", after)}
            </div>
            {diff && (
              <div className="space-y-1 text-[11px] font-mono">
                <div className="text-emerald-300">
                  {diff.added.length} day(s) gained data{diff.added.length ? `: ${diff.added.slice(0, 10).join(", ")}${diff.added.length > 10 ? "…" : ""}` : ""}
                </div>
                {diff.corrected.length > 0 && (
                  <div className="text-warning">
                    {diff.corrected.length} previously-complete day(s) changed hash (broker value
                    correction — candle count did not shrink): {diff.corrected.slice(0, 10).join(", ")}
                  </div>
                )}
                {diff.degraded.length > 0 ? (
                  <div className="px-2 py-1 rounded border-2 border-danger/60 bg-danger/10 text-danger font-bold"
                    data-testid="historical-degraded-banner">
                    DEGRADED DAYS DETECTED (candle count shrank — this should be impossible on the
                    upsert-only path, investigate before trusting the warehouse): {diff.degraded.join("; ")}
                  </div>
                ) : (
                  <div className="text-emerald-300">No existing day lost candles. ✓</div>
                )}
              </div>
            )}
          </div>
        )}

        {!connected && (
          <div className="text-[10px] text-dimmer">
            Dry-run works without Upstox; executing the ingest requires a connected, non-expired token.
          </div>
        )}
      </div>
    </div>
  );
}
