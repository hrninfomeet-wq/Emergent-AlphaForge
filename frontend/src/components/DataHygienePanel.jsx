import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { useJobs } from "@/lib/jobs";
import { fmtInt } from "@/lib/fmt";
import { ShieldCheck, RefreshCw, Play, CheckCircle2, AlertTriangle, AlertCircle } from "lucide-react";

const STATUS_STYLES = {
  verified: "bg-emerald-950 text-emerald-200 border-emerald-900",
  warning: "bg-amber-950 text-amber-200 border-amber-900",
  degraded: "bg-rose-950 text-rose-200 border-rose-900",
};

const STATUS_ICON = {
  verified: CheckCircle2,
  warning: AlertTriangle,
  degraded: AlertCircle,
};

const KIND_LABEL = {
  spot: "Spot candles",
  contracts: "Option contracts",
  option_candles: "Option candles",
};

function StatusPill({ status }) {
  const Icon = STATUS_ICON[status] || AlertCircle;
  return (
    <span className={`text-[10px] px-1.5 py-0.5 rounded border font-mono inline-flex items-center gap-1 ${STATUS_STYLES[status] || STATUS_STYLES.degraded}`}>
      <Icon className="w-3 h-3" />
      {status}
    </span>
  );
}

/**
 * Data Hygiene panel — the one-click warehouse health + fill control.
 *
 * Plan: diff the desired warehouse (default scope 2024-11-27 -> today,
 * NIFTY/BANKNIFTY/SENSEX, ATM CE+PE) against what is actually stored.
 * Execute: submit the suggested fetches in dependency order (spot -> contracts
 * -> option_candles). Execute jobs are tracked by the global JobsProvider so
 * progress survives navigation.
 */
export default function DataHygienePanel({ upstoxConnected }) {
  const { startHygieneBatch, hygiene, isHygieneActive, onJobComplete } = useJobs();
  const [plan, setPlan] = useState(null);
  const [planning, setPlanning] = useState(false);
  const [executing, setExecuting] = useState(false);

  const runPlan = async () => {
    setPlanning(true);
    try {
      const res = await api.dataHygienePlan();
      setPlan(res);
      const s = res.summary || {};
      toast.success(`Hygiene plan: ${s.total_actions || 0} action(s) across ${s.instruments_count || 0} instruments`);
    } catch (e) {
      const msg = e.response?.data?.detail || e.message;
      toast.error(`Hygiene plan failed: ${msg}`);
    } finally {
      setPlanning(false);
    }
  };

  const execute = async () => {
    if (!plan) return;
    const totalActions = plan.summary?.total_actions || 0;
    if (totalActions === 0) {
      toast.info("Nothing to fetch — the warehouse already matches the desired scope.");
      return;
    }
    const ok = window.confirm(
      `Submit ${totalActions} background fetch job(s) to fill warehouse gaps?\n\n` +
        `They run in dependency order (spot → contracts → option candles) and may take a while. ` +
        `Progress is shown here and in the top bar.`,
    );
    if (!ok) return;
    setExecuting(true);
    try {
      const res = await api.dataHygieneExecute(plan);
      const submitted = startHygieneBatch(res);
      const errors = (res.errors || []).length;
      if (submitted > 0) {
        toast.success(`Submitted ${submitted} hygiene job(s) in dependency order`);
      } else {
        toast.warning("No jobs were submitted. Re-run the plan and check Upstox connection.");
      }
      if (errors) toast.error(`${errors} action(s) failed to submit`);
    } catch (e) {
      const msg = e.response?.data?.detail || e.message;
      toast.error(`Hygiene execute failed: ${msg}`);
    } finally {
      setExecuting(false);
    }
  };

  // Re-run the plan automatically when a hygiene batch completes, so the diff
  // reflects the freshly fetched data.
  useEffect(() => {
    const off = onJobComplete("data_hygiene", () => {
      runPlan();
    });
    return off;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const summary = plan?.summary;
  const overall = summary?.overall_status;
  const hygieneActive = isHygieneActive();

  return (
    <div className="rounded-lg border border-line bg-bg-1" data-testid="data-hygiene-panel">
      <div className="px-3 py-2 border-b border-line flex items-center gap-2">
        <ShieldCheck className="w-4 h-4 text-info" />
        <div className="text-xs font-semibold uppercase tracking-wider text-dim">Data Hygiene</div>
        {overall && (
          <span className="ml-2"><StatusPill status={overall} /></span>
        )}
        <div className="ml-auto flex items-center gap-2">
          <Button
            size="sm"
            variant="secondary"
            onClick={runPlan}
            disabled={planning}
            className="h-7 text-xs"
            data-testid="hygiene-plan-button"
          >
            <RefreshCw className={`w-3 h-3 mr-1 ${planning ? "animate-spin" : ""}`} />
            {planning ? "Checking…" : "Check warehouse"}
          </Button>
          <Button
            size="sm"
            onClick={execute}
            disabled={!plan || executing || hygieneActive || (summary?.total_actions || 0) === 0 || !upstoxConnected}
            className="h-7 text-xs bg-bg-3 border border-line hover:bg-bg-2"
            data-testid="hygiene-execute-button"
          >
            <Play className="w-3 h-3 mr-1" />
            {hygieneActive
              ? "Filling…"
              : !upstoxConnected
                ? "Connect Upstox"
                : (summary?.total_actions || 0) === 0
                  ? plan ? "Up to date" : "Fill gaps"
                  : `Fill gaps (${summary.total_actions})`}
          </Button>
        </div>
      </div>

      <div className="p-3 space-y-3">
        <div className="text-[11px] text-dim">
          Diffs the warehouse against the desired scope (2024-11-27 → today · NIFTY + BANKNIFTY + SENSEX · ATM CE/PE) and
          fills gaps in dependency order: spot → contracts → option candles. Re-running is safe; only missing data is fetched.
        </div>

        {/* Active hygiene batch progress */}
        {hygiene && (
          <div className="rounded-md border border-line bg-bg-2 p-3" data-testid="hygiene-progress">
            <div className="flex items-center justify-between gap-2 text-xs">
              <span className="font-mono uppercase">{hygiene.done ? "complete" : "running"}</span>
              <span className="font-mono text-dimmer">
                {hygiene.completed}/{hygiene.total} jobs · {Math.round(hygiene.progress_pct || 0)}%
              </span>
            </div>
            <div className="mt-2 h-2 rounded bg-bg-3 overflow-hidden">
              <div
                className="h-full bg-info transition-all"
                style={{ width: `${Math.min(100, Math.max(0, Number(hygiene.progress_pct || 0)))}%` }}
              />
            </div>
            {hygiene.failed > 0 && (
              <div className="mt-2 text-[11px] text-danger">{hygiene.failed} job(s) failed — check Recent Ingest Runs.</div>
            )}
          </div>
        )}

        {/* Plan results per instrument */}
        {plan && (
          <div className="space-y-2" data-testid="hygiene-plan-result">
            <div className="text-[11px] text-dimmer font-mono">
              Window {plan.window?.start} → {plan.window?.end} · {summary?.total_actions || 0} action(s)
            </div>
            <div className="grid grid-cols-1 lg:grid-cols-3 gap-2">
              {(plan.instruments || []).map((inst) => (
                <div key={inst.instrument} className="rounded-md border border-line bg-bg-2 p-3" data-testid={`hygiene-instrument-${inst.instrument.toLowerCase()}`}>
                  <div className="text-sm font-semibold mb-2">{inst.instrument}</div>
                  <div className="space-y-1.5 text-[11px]">
                    <div className="flex items-center justify-between gap-2">
                      <span className="text-dim">Spot</span>
                      <span className="flex items-center gap-1.5">
                        <span className="font-mono text-dimmer">{inst.spot?.coverage_pct}%</span>
                        <StatusPill status={inst.spot?.status} />
                      </span>
                    </div>
                    <div className="flex items-center justify-between gap-2">
                      <span className="text-dim">Contracts</span>
                      <span className="flex items-center gap-1.5">
                        <span className="font-mono text-dimmer">{fmtInt(inst.contracts?.expiries_in_window || 0)} exp</span>
                        <StatusPill status={inst.contracts?.status} />
                      </span>
                    </div>
                    <div className="flex items-center justify-between gap-2">
                      <span className="text-dim">Option candles</span>
                      <span className="flex items-center gap-1.5">
                        <span className="font-mono text-dimmer">{fmtInt(inst.option_candles?.expiries_with_data || 0)} exp</span>
                        <StatusPill status={inst.option_candles?.status} />
                      </span>
                    </div>
                  </div>
                  {(inst.actions || []).length > 0 ? (
                    <div className="mt-2 pt-2 border-t border-line flex flex-wrap gap-1">
                      {inst.actions.map((a) => (
                        <span key={a.id} className="text-[10px] px-1.5 py-0.5 rounded bg-amber-950 text-amber-200 border border-amber-900" title={a.reason}>
                          {KIND_LABEL[a.kind] || a.kind}
                        </span>
                      ))}
                    </div>
                  ) : (
                    <div className="mt-2 pt-2 border-t border-line text-[10px] text-emerald-300 flex items-center gap-1">
                      <CheckCircle2 className="w-3 h-3" /> Up to date
                    </div>
                  )}
                </div>
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
