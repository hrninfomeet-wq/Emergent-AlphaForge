import { useState } from "react";
import { Pin, Play, Pause, Loader2 } from "lucide-react";
import { toast } from "sonner";
import { api } from "@/lib/api";
import { getApiErrorMessage } from "@/lib/apiError";
import { SectionCard } from "@/components/live/liveHelpers";
import { useLiveData } from "@/components/live/LiveDataProvider";

/**
 * Deployments panel for the cockpit core.
 *
 * Carries the PAPER lifecycle controls inline (resume / pause / re-pin), because
 * paper testing is the everyday loop and should never require hunting across
 * pages. Real-money controls deliberately stay OUT of here — a live deployment
 * shows its state and sends the operator to the ⚙ drawer, where the consent flow
 * lives. Enabling live is a decision, not a quick action.
 */
function statusPill(dep) {
  const mode = String(dep?.mode || "").toLowerCase();
  const status = String(dep?.status || "").toUpperCase();
  if (mode === "live") return { label: "Live", cls: "bg-danger/10 text-danger border-danger/40" };
  if (status === "PAUSED") return { label: "Paused", cls: "bg-amber-500/10 text-warning border-amber-500/40" };
  if (status === "ACTIVE") return { label: "Paper · running", cls: "bg-success/10 text-success border-success/40" };
  return { label: status || "—", cls: "bg-bg-3 text-dim border-line" };
}

function DeploymentRow({ dep, onDone }) {
  const [busy, setBusy] = useState(false);
  const isLive = String(dep?.mode || "").toLowerCase() === "live";
  const paused = String(dep?.status || "").toUpperCase() === "PAUSED";
  const active = String(dep?.status || "").toUpperCase() === "ACTIVE";
  // WHY it paused is the actionable part: a drift pause needs a RE-PIN, not a
  // plain resume — resuming alone just gets auto-paused again on the next bar.
  const pausedReason = dep?.kill_switch_reason || dep?.drift_reason;
  const isDriftPaused = paused && dep?.drift_reason === "strategy_source_drift";

  const run = async (fn, okMsg) => {
    setBusy(true);
    try {
      await fn();
      toast.success(okMsg);
      onDone?.();
    } catch (e) {
      toast.error(getApiErrorMessage(e, "Action failed"));
    } finally {
      setBusy(false);
    }
  };

  const p = statusPill(dep);
  const btn = "inline-flex items-center gap-1 px-2 py-1 rounded-md border text-[11px] font-medium disabled:opacity-50";

  return (
    <div className="px-3 py-2 rounded-md bg-bg-2 border border-line/60 space-y-1.5">
      <div className="flex items-center justify-between gap-2">
        <span className="text-xs text-foreground truncate">
          {dep.name || dep.strategy_id}
          {dep.instrument ? <span className="text-dimmer"> · {dep.instrument}</span> : null}
        </span>
        <span className={`text-[9px] uppercase tracking-wide px-2 py-0.5 rounded-full border shrink-0 ${p.cls}`}>
          {p.label}
        </span>
      </div>

      {pausedReason && (
        <div className="text-[10.5px] text-warning font-mono truncate" title={pausedReason}>
          auto-paused: {pausedReason}
        </div>
      )}

      {/* Paper lifecycle — inline. Live deployments get no quick actions. */}
      {isLive ? (
        <div className="text-[10.5px] text-dimmer">Real-money controls are in ⚙ Configure.</div>
      ) : (
        <div className="flex items-center gap-1.5 flex-wrap">
          {busy && <Loader2 className="w-3 h-3 animate-spin text-dimmer" />}
          {isDriftPaused && (
            <button
              type="button" disabled={busy}
              onClick={() => run(() => api.repinDeploymentSource(dep.id), "Re-pinned to the current strategy code — resuming")}
              title={`The strategy file changed since this was deployed (pinned ${dep.drift_pinned_sha || "?"} → current ${dep.drift_current_sha || "?"}). Re-pin to the current code, then it can run again.`}
              className={`${btn} border-info/50 bg-info/10 text-info hover:bg-info/20`}
              data-testid="cockpit-repin"
            >
              <Pin className="w-3 h-3" /> Re-pin &amp; resume
            </button>
          )}
          {paused && !isDriftPaused && (
            <button
              type="button" disabled={busy}
              onClick={() => run(() => api.resumeDeployment(dep.id), "Resumed — paper trading again")}
              className={`${btn} border-success/50 bg-success/10 text-success hover:bg-success/20`}
              data-testid="cockpit-resume"
            >
              <Play className="w-3 h-3" /> Resume paper
            </button>
          )}
          {active && (
            <button
              type="button" disabled={busy}
              onClick={() => run(() => api.pauseDeployment(dep.id), "Paused")}
              className={`${btn} border-line bg-bg-3 text-dim hover:text-foreground`}
              data-testid="cockpit-pause"
            >
              <Pause className="w-3 h-3" /> Pause
            </button>
          )}
        </div>
      )}
    </div>
  );
}

export default function DeploymentSummary({ deployments, onManage }) {
  const { refetch } = useLiveData();
  const rows = (deployments || []).filter((d) => String(d?.status || "").toUpperCase() !== "ARCHIVED");
  const liveCount = rows.filter((d) => String(d?.mode || "").toLowerCase() === "live").length;
  const runningPaper = rows.filter(
    (d) => String(d?.mode || "").toLowerCase() !== "live" && String(d?.status || "").toUpperCase() === "ACTIVE",
  ).length;

  return (
    <SectionCard
      title="Deployments"
      badge={
        <span className="text-[10px] font-mono text-dimmer px-2 py-0.5 rounded-full border border-line bg-bg-3 uppercase tracking-wider">
          {liveCount} live · {runningPaper} paper running
        </span>
      }
    >
      <div className="space-y-1.5">
        {rows.length === 0 && (
          <div className="text-xs text-dimmer font-mono py-3 text-center">No deployments</div>
        )}
        {rows.slice(0, 5).map((d) => (
          <DeploymentRow key={d.id} dep={d} onDone={refetch.deployments} />
        ))}
        <button
          type="button"
          onClick={onManage}
          className="w-full mt-1 border border-line bg-bg-2 rounded-md px-2 py-1.5 text-xs font-medium hover:border-dim"
        >
          Manage deployments →
        </button>
      </div>
    </SectionCard>
  );
}
