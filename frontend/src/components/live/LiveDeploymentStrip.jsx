import { useCallback, useEffect, useRef, useState } from "react";
import { Activity, Clock, Loader2, OctagonX, ShieldOff, Square } from "lucide-react";
import { toast } from "sonner";
import { api } from "@/lib/api";
import { fmtINR } from "@/lib/fmt";
import { Button } from "@/components/ui/button";
import DeployToLivePanel from "@/components/live/DeployToLivePanel";

/**
 * LiveDeploymentStrip — per-deployment live-arm controls for the Live Trading page.
 *
 * For each deployment that is currently armed (via /deployments/{id}/live/status),
 * shows:
 *   - armed_until countdown  (polling-derived, updated via a local clock)
 *   - today's orders / lots / realized ₹ from the status payload
 *   - open positions count
 *   - Disarm and Stop buttons
 *
 * Also exposes a "Deploy to Live" entry for each non-archived deployment that is
 * NOT currently armed (renders DeployToLivePanel per row).
 *
 * A master "Stop all live" button calls /deployments/stop-all.
 *
 * Props:
 *   deployments  – array of deployment objects from /deployments (non-archived)
 *   onRefresh    – called after any arm/disarm/stop to let the parent re-fetch
 */

const POLL_MS = 10_000;

// ── Simple countdown from an ISO datetime ──────────────────────────────────
function Countdown({ until }) {
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, []);

  if (!until) return <span className="text-dimmer font-mono text-[11px]">—</span>;

  const ms = Date.parse(until) - now;
  if (ms <= 0) return <span className="text-rose-300 font-mono text-[11px]">expired</span>;

  const totalSec = Math.floor(ms / 1000);
  const h = Math.floor(totalSec / 3600);
  const m = Math.floor((totalSec % 3600) / 60);
  const s = totalSec % 60;
  const str = h > 0
    ? `${h}h ${String(m).padStart(2, "0")}m`
    : `${String(m).padStart(2, "0")}m ${String(s).padStart(2, "0")}s`;

  const cls = ms < 5 * 60 * 1000
    ? "text-rose-300"
    : ms < 30 * 60 * 1000
      ? "text-amber-300"
      : "text-emerald-300";

  return (
    <span className={`inline-flex items-center gap-1 font-mono text-[11px] ${cls}`}>
      <Clock className="w-3 h-3" />
      {str}
    </span>
  );
}

// ── One armed-deployment row ───────────────────────────────────────────────
function ArmedRow({ dep, liveStatus, busy, onDisarm, onStop }) {
  const todayOrders = liveStatus?.today_orders ?? 0;
  const todayLots = liveStatus?.today_lots ?? 0;
  const todayRealised = liveStatus?.today_realised_pnl ?? null;
  const openPositions = liveStatus?.open_positions ?? 0;

  return (
    <div className="px-3 py-2 flex items-center gap-2 flex-wrap" data-testid="live-deploy-row">
      {/* Armed indicator */}
      <span className="w-2 h-2 rounded-full bg-danger shrink-0 animate-pulse" title="ARMED" />
      <div className="min-w-0">
        <div className="font-medium text-xs truncate max-w-[180px] text-foreground" title={dep.name}>
          {dep.name || dep.id?.slice(0, 8) || "—"}
        </div>
        <div className="text-[10px] text-dimmer truncate max-w-[180px]" title={dep.strategy_id}>
          {dep.strategy_id || "—"}
        </div>
      </div>

      {/* Armed until countdown */}
      <span className="ml-1">
        <Countdown until={liveStatus?.armed_until} />
      </span>

      {/* Today's stats */}
      <span className="text-[11px] font-mono text-dim whitespace-nowrap ml-2">
        {todayOrders} ord · {todayLots} lots
        {todayRealised != null && (
          <> · <span className={Number(todayRealised) >= 0 ? "text-success" : "text-danger"}>{fmtINR(todayRealised)}</span></>
        )}
      </span>

      {/* Open positions */}
      <span className="text-[11px] font-mono text-dimmer whitespace-nowrap">
        {openPositions} open
      </span>

      {/* Controls */}
      <div className="ml-auto flex items-center gap-1.5">
        <Button
          variant="ghost"
          size="sm"
          disabled={busy}
          onClick={() => onDisarm(dep)}
          className="h-7 text-xs text-amber-300 hover:text-amber-200"
          data-testid="live-deploy-disarm"
        >
          <ShieldOff className="w-3 h-3 mr-1" />
          Disarm
        </Button>
        <Button
          variant="outline"
          size="sm"
          disabled={busy}
          onClick={() => onStop(dep)}
          className="h-7 text-xs border-rose-500/40 text-rose-300 hover:text-rose-200"
          data-testid="live-deploy-stop"
        >
          <Square className="w-3 h-3 mr-1" />
          Stop
        </Button>
      </div>
    </div>
  );
}

// ── One unarmed-deployment row (shows Deploy to Live panel trigger) ─────────
function UnarmedRow({ dep, busy, onArmed }) {
  return (
    <div className="px-3 py-2 flex items-center gap-2 flex-wrap">
      <span className="w-2 h-2 rounded-full bg-dimmer shrink-0" />
      <div className="min-w-0">
        <div className="font-medium text-xs truncate max-w-[180px] text-foreground" title={dep.name}>
          {dep.name || dep.id?.slice(0, 8) || "—"}
        </div>
        <div className="text-[10px] text-dimmer truncate max-w-[180px]" title={dep.strategy_id}>
          {dep.strategy_id || "—"}
        </div>
      </div>
      <span className="text-[11px] text-dimmer uppercase tracking-wider ml-1">Not armed</span>
      <div className="ml-auto">
        {/* eslint-disable-next-line react/prop-types */}
        <DeployToLivePanel dep={dep} onArmed={onArmed} />
      </div>
    </div>
  );
}

// ── Main strip ─────────────────────────────────────────────────────────────
export default function LiveDeploymentStrip({ deployments, onRefresh }) {
  // Map of deployment_id -> live status (null = not armed / error)
  const [liveStatuses, setLiveStatuses] = useState({});
  const [busy, setBusy] = useState(false);
  const timerRef = useRef(null);

  // Poll live/status for all deployments
  const pollStatuses = useCallback(() => {
    if (!deployments || deployments.length === 0) return;
    deployments.forEach((dep) => {
      api.liveStatus(dep.id)
        .then((d) => setLiveStatuses((prev) => ({ ...prev, [dep.id]: d })))
        .catch(() => {
          // 404 / not-armed → null status
          setLiveStatuses((prev) => ({ ...prev, [dep.id]: null }));
        });
    });
  }, [deployments]);

  useEffect(() => {
    pollStatuses();
    timerRef.current = setInterval(pollStatuses, POLL_MS);
    return () => { if (timerRef.current) clearInterval(timerRef.current); };
  }, [pollStatuses]);

  const refreshAll = useCallback(async () => {
    pollStatuses();
    onRefresh?.();
  }, [pollStatuses, onRefresh]);

  const doDisarm = async (dep) => {
    if (!window.confirm(`Disarm "${dep.name || dep.id}"? No more live orders will be placed.`)) return;
    setBusy(true);
    try {
      await api.liveDisarm(dep.id);
      toast.success(`Disarmed "${dep.name || dep.id}"`);
      await refreshAll();
    } catch (e) {
      toast.error(e.response?.data?.detail || e.message);
    } finally {
      setBusy(false);
    }
  };

  const doStop = async (dep) => {
    if (!window.confirm(`Stop live trading for "${dep.name || dep.id}"? This disarms and squares off any open live positions.`)) return;
    setBusy(true);
    try {
      await api.liveStop(dep.id);
      toast.success(`Stopped "${dep.name || dep.id}"`);
      await refreshAll();
    } catch (e) {
      toast.error(e.response?.data?.detail || e.message);
    } finally {
      setBusy(false);
    }
  };

  const doStopAll = async () => {
    if (!window.confirm("Stop ALL live trading? This disarms and squares off every live deployment.")) return;
    setBusy(true);
    try {
      await api.stopAllPaper(); // reuses the existing stop-all endpoint
      toast.success("Stopped all live deployments");
      await refreshAll();
    } catch (e) {
      toast.error(e.response?.data?.detail || e.message);
    } finally {
      setBusy(false);
    }
  };

  // Partition: armed = liveStatus exists + has armed_until; unarmed = rest.
  const armedDeps = (deployments || []).filter(
    (d) => liveStatuses[d.id] && liveStatuses[d.id].armed_until,
  );
  const unarmedDeps = (deployments || []).filter(
    (d) => !(liveStatuses[d.id] && liveStatuses[d.id].armed_until),
  );
  const hasArmed = armedDeps.length > 0;

  if (!deployments || deployments.length === 0) return null;

  return (
    <div
      className="rounded-lg border border-line bg-bg-1"
      data-testid="live-deploy-strip"
    >
      {/* Header */}
      <div className="px-3 py-2 border-b border-line flex items-center gap-2">
        <Activity className="w-4 h-4 text-danger" />
        <div className="text-xs font-semibold uppercase tracking-wider text-dim">
          Live Deployments
        </div>
        <span className="text-[11px] text-dimmer">arm / disarm / stop real orders</span>
        {busy && <Loader2 className="w-3.5 h-3.5 animate-spin text-dimmer ml-1" />}
        <Button
          variant="outline"
          size="sm"
          disabled={busy || !hasArmed}
          onClick={doStopAll}
          className="ml-auto h-7 text-xs border-rose-500/40 text-rose-300 hover:text-rose-200"
          data-testid="live-deploy-stop-all"
          title="Disarm and square off every live deployment"
        >
          <OctagonX className="w-3.5 h-3.5 mr-1" />
          Stop ALL live
        </Button>
      </div>

      {/* Armed deployments */}
      {hasArmed && (
        <div className="divide-y divide-line">
          {armedDeps.map((dep) => (
            <ArmedRow
              key={dep.id}
              dep={dep}
              liveStatus={liveStatuses[dep.id]}
              busy={busy}
              onDisarm={doDisarm}
              onStop={doStop}
            />
          ))}
        </div>
      )}

      {/* Unarmed deployments — show Deploy to Live */}
      {unarmedDeps.length > 0 && (
        <div className="divide-y divide-line">
          {unarmedDeps.map((dep) => (
            <UnarmedRow
              key={dep.id}
              dep={dep}
              busy={busy}
              onArmed={refreshAll}
            />
          ))}
        </div>
      )}
    </div>
  );
}
