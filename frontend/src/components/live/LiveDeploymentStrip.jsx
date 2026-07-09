import { useEffect, useState } from "react";
import { Activity, ChevronDown, ChevronRight, Clock, Loader2, OctagonX, ShieldOff, Square } from "lucide-react";
import { toast } from "sonner";
import { api } from "@/lib/api";
import { fmtINR } from "@/lib/fmt";
import { getApiErrorMessage } from "@/lib/apiError";
import { Button } from "@/components/ui/button";
import DeployToLivePanel from "@/components/live/DeployToLivePanel";
import { useLiveData } from "@/components/live/LiveDataProvider";

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
      ? "text-warning"
      : "text-emerald-300";

  return (
    <span className={`inline-flex items-center gap-1 font-mono text-[11px] ${cls}`}>
      <Clock className="w-3 h-3" />
      {str}
    </span>
  );
}

// Map a backend live-entry refusal reason to a short human label. The full
// reason is always available in the chip's tooltip.
function entryErrorLabel(reason) {
  if (!reason) return null;
  const map = {
    live_entry_premium_unavailable_or_stale: "no fresh premium",
    signal_claimed_elsewhere: "claimed elsewhere",
    dry_run_failed: "pre-trade gate",
    not_within_lot_cap: "lot cap",
    cannot_trade: "engine halted",
  };
  return map[reason] || String(reason).replace(/[_:]/g, " ").trim();
}

// ── One armed-deployment row ───────────────────────────────────────────────
function ArmedRow({ dep, liveStatus, busy, onDisarm, onStop }) {
  // Status payload shape: { today: {orders, lots, realized_pnl}, open_positions: [...] }
  const today = liveStatus?.today || {};
  const todayOrders = today.orders ?? 0;
  const todayLots = today.lots ?? 0;
  const todayRealised = today.realized_pnl ?? null;
  const openPositions = Array.isArray(liveStatus?.open_positions)
    ? liveStatus.open_positions.length
    : (liveStatus?.open_positions ?? 0);

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

      {/* Entry-refused chip — WHY an armed deployment isn't placing (stale
          premium / throttle / gate block). Surfaces the previously write-only
          signals.live_trade_error via the live-status payload's last_entry. */}
      {liveStatus?.last_entry?.error && (
        <span
          className="inline-flex items-center gap-1 text-[10px] font-medium text-rose-300 bg-rose-500/10 border border-rose-500/30 rounded px-1.5 py-0.5 whitespace-nowrap"
          title={`Last live entry refused: ${liveStatus.last_entry.error}${liveStatus.last_entry.at ? ` (at ${liveStatus.last_entry.at})` : ""}`}
          data-testid="live-entry-refused"
        >
          <OctagonX className="w-3 h-3 shrink-0" />
          entry refused: {entryErrorLabel(liveStatus.last_entry.error)}
        </span>
      )}

      {/* Controls */}
      <div className="ml-auto flex items-center gap-1.5">
        <Button
          variant="ghost"
          size="sm"
          disabled={busy}
          onClick={() => onDisarm(dep)}
          className="h-7 text-xs text-warning"
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
      <span className="w-2 h-2 rounded-full bg-slate-500 shrink-0" />
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

const COLLAPSED_STORAGE_KEY = "af.liveDeploymentStrip.collapsed";

// ── Main strip ─────────────────────────────────────────────────────────────
export default function LiveDeploymentStrip({ onArmedSummaryChange }) {
  // Deployments + the batched per-deployment live status come from the shared
  // LiveDataProvider (one 10s batched poll); this strip no longer self-polls.
  // `liveStatuses` is the provider's deployLive byId map (missing id = not armed).
  const { deployments, deployLive: liveStatuses, refetch } = useLiveData();
  const [busy, setBusy] = useState(false);
  const [collapsed, setCollapsed] = useState(() => {
    try {
      return localStorage.getItem(COLLAPSED_STORAGE_KEY) === "1";
    } catch {
      return false;
    }
  });

  const toggleCollapsed = () => {
    setCollapsed((prev) => {
      const next = !prev;
      try {
        localStorage.setItem(COLLAPSED_STORAGE_KEY, next ? "1" : "0");
      } catch {
        // ignore storage errors (e.g. private mode)
      }
      return next;
    });
  };

  // After any arm/disarm/stop, re-pull everything (statuses + roster + arm-state).
  const refreshAll = refetch.all;

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
    // Honest blast radius: /deployments/stop-all squares EVERY open paper trade,
    // pauses EVERY active deployment (paper included), AND disarms every armed live
    // deployment — not just "live" as the button label implies.
    if (!window.confirm(
      "Stop ALL trading?\n\n"
      + "• squares off EVERY open PAPER trade\n"
      + "• pauses EVERY active deployment (paper included)\n"
      + "• disarms + flattens every armed LIVE deployment\n\n"
      + "Continue?"
    )) return;
    setBusy(true);
    try {
      const res = await api.stopAllDeployments();
      const squared = res?.squared_off_count ?? (res?.squared_off?.length ?? 0);
      const paused = (res?.paused_deployment_ids || []).length;
      const disarmed = (res?.disarmed_live_deployment_ids || []).length;
      toast.success(
        `Stopped ALL — ${squared} paper position(s) squared · ${paused} deployment(s) `
        + `paused · ${disarmed} live disarmed`,
      );
      await refreshAll();
    } catch (e) {
      toast.error(getApiErrorMessage(e, e.message));
    } finally {
      setBusy(false);
    }
  };

  // Partition on the LIVE `armed` flag from the status payload — NOT `armed_until`.
  // armed_until persists in the doc after a disarm/stop (it's the last cutoff), so
  // keying off it left disarmed rows stuck in the armed section (and the banner kept
  // counting them). `armed` flips to false on disarm / stop / next-day, so the row
  // moves to "unarmed" and the banner updates as soon as the next poll lands.
  const armedDeps = (deployments || []).filter(
    (d) => liveStatuses[d.id] && liveStatuses[d.id].armed === true,
  );
  const unarmedDeps = (deployments || []).filter(
    (d) => !(liveStatuses[d.id] && liveStatuses[d.id].armed === true),
  );
  const hasArmed = armedDeps.length > 0;

  // Aggregate today's realized P&L across all armed deployments, for the
  // always-visible header summary (matches ArmedRow's own today-P&L coloring).
  let todayRealisedTotal = null;
  for (const dep of armedDeps) {
    const realised = liveStatuses[dep.id]?.today?.realized_pnl;
    if (realised != null) {
      todayRealisedTotal = (todayRealisedTotal ?? 0) + Number(realised);
    }
  }

  // Lift armed summary to parent (for LiveBanner).
  // autoplace_armed is a backend env flag shared across all deployments —
  // take it from any armed status that has the field set.
  useEffect(() => {
    if (!onArmedSummaryChange) return;
    const armedCount = armedDeps.length;
    let autoplaceArmed = null;
    for (const dep of armedDeps) {
      const st = liveStatuses[dep.id];
      if (st && "autoplace_armed" in st) {
        autoplaceArmed = st.autoplace_armed;
        break;
      }
    }
    onArmedSummaryChange({ armedCount, autoplaceArmed });
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [armedDeps.length, liveStatuses, onArmedSummaryChange]);

  if (!deployments || deployments.length === 0) return null;

  return (
    <div
      className="rounded-lg border border-line bg-bg-1"
      data-testid="live-deploy-strip"
    >
      {/* Header — always visible regardless of collapsed state */}
      <div className="px-3 py-2 border-b border-line flex items-center gap-2">
        <button
          type="button"
          onClick={toggleCollapsed}
          className="flex items-center gap-2 min-w-0 hover:opacity-80"
          data-testid="live-deploy-strip-toggle"
          title={collapsed ? "Expand" : "Collapse"}
          aria-expanded={!collapsed}
        >
          <Activity className="w-4 h-4 text-danger shrink-0" />
          <span className="text-xs font-semibold uppercase tracking-wider text-dim">
            Live Deployments
          </span>
          {collapsed ? (
            <ChevronRight className="w-3.5 h-3.5 text-dimmer shrink-0" />
          ) : (
            <ChevronDown className="w-3.5 h-3.5 text-dimmer shrink-0" />
          )}
        </button>

        {/* Compact summary — visible whether expanded or collapsed */}
        <span className="text-[11px] text-dimmer font-mono whitespace-nowrap" data-testid="live-deploy-strip-summary">
          {armedDeps.length} armed · {unarmedDeps.length} unarmed
          {todayRealisedTotal != null && (
            <> · <span className={todayRealisedTotal >= 0 ? "text-success" : "text-danger"}>{fmtINR(todayRealisedTotal)}</span></>
          )}
        </span>

        {!collapsed && (
          <span className="text-[11px] text-dimmer">arm / disarm / stop real orders</span>
        )}
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

      {!collapsed && (
        <>
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
        </>
      )}
    </div>
  );
}
