import { SectionCard } from "@/components/live/liveHelpers";

/**
 * Compact deployments summary for the cockpit core. Shows a glanceable list of
 * deployments with mode/status pills; the full enable/disable/stop controls live
 * in the config drawer (opened via onManage) — this panel never mutates.
 */
function statusPill(dep) {
  const mode = String(dep?.mode || "").toLowerCase();
  const status = String(dep?.status || "").toUpperCase();
  if (mode === "live") return { label: "Live", cls: "bg-danger/10 text-danger border-danger/40" };
  if (status === "PAUSED") return { label: "Paused", cls: "bg-amber-500/10 text-warning border-amber-500/40" };
  return { label: "Paper", cls: "bg-bg-3 text-dim border-line" };
}

export default function DeploymentSummary({ deployments, onManage }) {
  const rows = (deployments || []).filter((d) => String(d?.status || "").toUpperCase() !== "ARCHIVED");
  const liveCount = rows.filter((d) => String(d?.mode || "").toLowerCase() === "live").length;

  return (
    <SectionCard
      title="Deployments"
      badge={
        <span className="text-[10px] font-mono text-dimmer px-2 py-0.5 rounded-full border border-line bg-bg-3 uppercase tracking-wider">
          {liveCount} live · {Math.max(0, rows.length - liveCount)} paper
        </span>
      }
    >
      <div className="space-y-1.5">
        {rows.length === 0 && (
          <div className="text-xs text-dimmer font-mono py-3 text-center">No deployments</div>
        )}
        {rows.slice(0, 4).map((d) => {
          const p = statusPill(d);
          return (
            <div key={d.id} className="flex items-center justify-between px-3 py-2 rounded-md bg-bg-2 border border-line/60">
              <span className="text-xs text-foreground truncate mr-2">
                {d.name || d.strategy_id}
                {d.instrument ? <span className="text-dimmer"> · {d.instrument}</span> : null}
              </span>
              <span className={`text-[9px] uppercase tracking-wide px-2 py-0.5 rounded-full border ${p.cls}`}>{p.label}</span>
            </div>
          );
        })}
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
