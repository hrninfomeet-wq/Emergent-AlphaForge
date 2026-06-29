import { Button } from "@/components/ui/button";
import { fmtNum, colorPnL } from "@/lib/fmt";
import { Pause, Play, Square, OctagonX, Activity } from "lucide-react";
import { deploymentLiveness } from "@/lib/deploymentLiveness";

const inr = (v) => (v == null ? "—" : `₹${fmtNum(v, 0)}`);

// One row of the Live Deployments strip: status dot + strategy/name, live open
// count + MTM, and the Pause/Resume + Stop controls.
function DeploymentControlRow({ dep, open, busy, feedHealth, onPause, onResume, onStop }) {
  const status = String(dep.status || "").toUpperCase();
  const isActive = status === "ACTIVE";
  const isPaused = status === "PAUSED";
  const live = deploymentLiveness(dep, feedHealth);
  const openCount = open?.openCount || 0;
  const openMtm = open?.openMtm || 0;
  return (
    <div className="px-3 py-2 flex items-center gap-2 flex-wrap" data-testid="paper-deploy-row">
      <span className={`w-2 h-2 rounded-full shrink-0 ${live.dot}`} title={live.tooltip} />
      <div className="min-w-0">
        <div className="font-medium text-xs truncate max-w-[200px]" title={dep.name}>{dep.name || dep.id?.slice(0, 8) || "—"}</div>
        <div className="text-[10px] text-dimmer truncate max-w-[200px]" title={dep.strategy_id}>{dep.strategy_id || "—"}</div>
      </div>
      <span className={`ml-2 text-[11px] uppercase tracking-wider ${live.text}`} title={live.tooltip}>{live.label}</span>
      <span className="ml-3 text-[11px] font-mono text-dim whitespace-nowrap">
        {openCount} open · MTM <span className={colorPnL(openMtm)}>{inr(openMtm)}</span>
      </span>
      <div className="ml-auto flex items-center gap-1.5">
        {isActive && (
          <Button variant="ghost" size="sm" disabled={busy} onClick={() => onPause(dep)}
            className="h-7 text-xs text-amber-300 hover:text-amber-200" data-testid="paper-deploy-pause">
            <Pause className="w-3 h-3 mr-1" /> Pause
          </Button>
        )}
        {isPaused && (
          <Button variant="ghost" size="sm" disabled={busy} onClick={() => onResume(dep)}
            className="h-7 text-xs text-emerald-300 hover:text-emerald-200" data-testid="paper-deploy-resume">
            <Play className="w-3 h-3 mr-1" /> Resume
          </Button>
        )}
        <Button variant="outline" size="sm" disabled={busy} onClick={() => onStop(dep)}
          className="h-7 text-xs border-rose-500/40 text-rose-300 hover:text-rose-200" data-testid="paper-deploy-stop">
          <Square className="w-3 h-3 mr-1" /> Stop
        </Button>
      </div>
    </div>
  );
}

// Live Deployments control strip: master Stop-all + per-deployment Pause/Resume/Stop.
// Presentational only — the page owns the doPause/doResume/doStop/doStopAll handlers.
export default function DeploymentControlStrip({ liveDeployments, perDeployOpen, busy, feedHealth, onPause, onResume, onStop, onStopAll }) {
  return (
    <div className="rounded-lg border border-line bg-bg-1" data-testid="paper-deploy-strip">
      <div className="px-3 py-2 border-b border-line flex items-center gap-2">
        <Activity className="w-4 h-4 text-info" />
        <div className="text-xs font-semibold uppercase tracking-wider text-dim">Live Deployments</div>
        <span className="text-[11px] text-dimmer">pause / resume / stop · squares off open positions</span>
        <Button variant="outline" size="sm" disabled={busy || liveDeployments.length === 0} onClick={onStopAll}
          className="ml-auto h-7 text-xs border-rose-500/40 text-rose-300 hover:text-rose-200" data-testid="paper-stop-all"
          title="Square off every open position and pause all active strategies">
          <OctagonX className="w-3.5 h-3.5 mr-1" /> Stop ALL paper trading
        </Button>
      </div>
      {liveDeployments.length === 0 ? (
        <div className="px-3 py-3 text-[11px] text-dimmer">No active deployments.</div>
      ) : (
        <div className="divide-y divide-line">
          {liveDeployments.map((dep) => (
            <DeploymentControlRow
              key={dep.id}
              dep={dep}
              open={perDeployOpen[dep.id]}
              busy={busy}
              feedHealth={feedHealth}
              onPause={onPause}
              onResume={onResume}
              onStop={onStop}
            />
          ))}
        </div>
      )}
    </div>
  );
}
