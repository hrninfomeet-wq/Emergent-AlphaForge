import { useEffect, useState } from "react";
import { Activity, ChevronDown, ChevronRight, Loader2, OctagonX, ShieldOff, Square } from "lucide-react";
import { toast } from "sonner";
import { api } from "@/lib/api";
import { fmtINR } from "@/lib/fmt";
import { getApiErrorMessage } from "@/lib/apiError";
import { Button } from "@/components/ui/button";
import DeployToLivePanel from "@/components/live/DeployToLivePanel";
import { useLiveData } from "@/components/live/LiveDataProvider";

/**
 * LiveDeploymentStrip — per-deployment live-execution controls for the Live
 * Trading page.
 *
 * Authorization is simply deployment.mode === "live" — there is no per-session
 * arm ceremony or expiry. For each deployment currently in live mode, shows:
 *   - today's orders / lots / realized ₹ from the /live/status payload
 *   - open positions count
 *   - Disable and Stop buttons
 *
 * Also exposes an "Enable Live Execution" entry for each non-archived
 * deployment that is NOT currently live (renders DeployToLivePanel per row).
 *
 * A master "Stop all live" button calls /deployments/stop-all.
 *
 * Props:
 *   deployments  – array of deployment objects from /deployments (non-archived)
 *   onRefresh    – called after any enable/disable/stop to let the parent re-fetch
 */

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
    premium_trigger_not_met: "premium fell back below the trigger before placement",
    strike_lock_failed: "could not lock the strike at the reference time",
    ref_premium_unavailable: "no fresh option tick to capture the reference premium",
    // Phase 5B B8: multi-leg/lazy gate + day-stop refusal reasons (A3/A4/deployment
    // day-stop gate). vix_unverifiable/vix_gate/day_stop are LIVE reasons today;
    // both_mode_live_pending_b6_b7 is the removed Cluster-A interim guard (B7,
    // d110a1e) — kept here only so a historical journaled signal from before that
    // removal still renders a readable label instead of the raw reason string.
    vix_gate: "VIX gate blocked the session",
    vix_unverifiable: "VIX unverifiable - session skipped",
    day_stop: "session day-stop hit",
    both_mode_live_pending_b6_b7: "multi-leg live was pending completion",
  };
  return map[reason] || String(reason).replace(/[_:]/g, " ").trim();
}

// ── One live-mode deployment row ────────────────────────────────────────────
function LiveRow({ dep, liveStatus, busy, onDisable, onStop }) {
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
      {/* Live indicator */}
      <span className="w-2 h-2 rounded-full bg-danger shrink-0 animate-pulse" title="LIVE" />
      <div className="min-w-0">
        <div className="font-medium text-xs truncate max-w-[180px] text-foreground" title={dep.name}>
          {dep.name || dep.id?.slice(0, 8) || "—"}
        </div>
        <div className="text-[10px] text-dimmer truncate max-w-[180px]" title={dep.strategy_id}>
          {dep.strategy_id || "—"}
        </div>
      </div>

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

      {/* Entry-refused chip — WHY a live deployment isn't placing (stale
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
          onClick={() => onDisable(dep)}
          className="h-7 text-xs text-warning"
          data-testid="live-deploy-disarm"
        >
          <ShieldOff className="w-3 h-3 mr-1" />
          Disable
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

// ── One non-live deployment row (shows Enable Live Execution trigger) ──────
function NotLiveRow({ dep, busy, onArmed }) {
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
      <span className="text-[11px] text-dimmer uppercase tracking-wider ml-1">Not live</span>
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
  // `liveStatuses` is the provider's deployLive byId map (today's counters/open
  // positions/last-entry per deployment — its own `armed` field is dead, see
  // the partition comment below; live/not-live is read off `deployments[].mode`).
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

  // After any enable/disable/stop, re-pull everything (statuses + roster + arm-state).
  const refreshAll = refetch.all;

  const doDisarm = async (dep) => {
    if (!window.confirm(`Disable live execution for "${dep.name || dep.id}"? No more live orders will be placed.`)) return;
    setBusy(true);
    try {
      await api.disableDeploymentLive(dep.id);
      toast.success(`Disabled live execution for "${dep.name || dep.id}"`);
      await refreshAll();
    } catch (e) {
      toast.error(e.response?.data?.detail || e.message);
    } finally {
      setBusy(false);
    }
  };

  const doStop = async (dep) => {
    if (!window.confirm(`Stop live trading for "${dep.name || dep.id}"? This disables live execution and squares off any open live positions.`)) return;
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
    // pauses EVERY active deployment (paper included), AND disables every live
    // deployment — not just "live" as the button label implies.
    if (!window.confirm(
      "Stop ALL trading?\n\n"
      + "• squares off EVERY open PAPER trade\n"
      + "• pauses EVERY active deployment (paper included)\n"
      + "• disables live execution + flattens every LIVE deployment\n\n"
      + "Continue?"
    )) return;
    setBusy(true);
    try {
      const res = await api.stopAllDeployments();
      const squared = res?.squared_off_count ?? (res?.squared_off?.length ?? 0);
      const paused = (res?.paused_deployment_ids || []).length;
      const disabledLive = (res?.disarmed_live_deployment_ids || []).length;
      toast.success(
        `Stopped ALL — ${squared} paper position(s) squared · ${paused} deployment(s) `
        + `paused · ${disabledLive} live deployment(s) disabled`,
      );
      await refreshAll();
    } catch (e) {
      toast.error(getApiErrorMessage(e, e.message));
    } finally {
      setBusy(false);
    }
  };

  // Partition on the deployment's own `mode` field (from /deployments), NOT on
  // the /live/status payload's `armed` flag — that field is a dead holdover
  // (risk.live no longer carries an `armed` sub-field, so it always reads
  // false) from before the per-session arm ceremony was removed. Authorization
  // is simply deployment.mode === "live", so that's what partitions the strip.
  const liveDeps = (deployments || []).filter((d) => d?.mode === "live");
  const notLiveDeps = (deployments || []).filter((d) => d?.mode !== "live");
  const hasLive = liveDeps.length > 0;

  // Aggregate today's realized P&L across all live deployments, for the
  // always-visible header summary (matches LiveRow's own today-P&L coloring).
  let todayRealisedTotal = null;
  for (const dep of liveDeps) {
    const realised = liveStatuses[dep.id]?.today?.realized_pnl;
    if (realised != null) {
      todayRealisedTotal = (todayRealisedTotal ?? 0) + Number(realised);
    }
  }

  // Lift the live-deployment summary to parent (for LiveBanner).
  // autoplace_armed is a backend env flag shared across all deployments —
  // take it from any live status that has the field set.
  useEffect(() => {
    if (!onArmedSummaryChange) return;
    const armedCount = liveDeps.length;
    let autoplaceArmed = null;
    for (const dep of liveDeps) {
      const st = liveStatuses[dep.id];
      if (st && "autoplace_armed" in st) {
        autoplaceArmed = st.autoplace_armed;
        break;
      }
    }
    onArmedSummaryChange({ armedCount, autoplaceArmed });
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [liveDeps.length, liveStatuses, onArmedSummaryChange]);

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
          {liveDeps.length} live · {notLiveDeps.length} not live
          {todayRealisedTotal != null && (
            <> · <span className={todayRealisedTotal >= 0 ? "text-success" : "text-danger"}>{fmtINR(todayRealisedTotal)}</span></>
          )}
        </span>

        {!collapsed && (
          <span className="text-[11px] text-dimmer">enable / disable / stop real orders</span>
        )}
        {busy && <Loader2 className="w-3.5 h-3.5 animate-spin text-dimmer ml-1" />}
        <Button
          variant="outline"
          size="sm"
          disabled={busy || !hasLive}
          onClick={doStopAll}
          className="ml-auto h-7 text-xs border-rose-500/40 text-rose-300 hover:text-rose-200"
          data-testid="live-deploy-stop-all"
          title="Disable live execution and square off every live deployment"
        >
          <OctagonX className="w-3.5 h-3.5 mr-1" />
          Stop ALL live
        </Button>
      </div>

      {!collapsed && (
        <>
          {/* Live-mode deployments */}
          {hasLive && (
            <div className="divide-y divide-line">
              {liveDeps.map((dep) => (
                <LiveRow
                  key={dep.id}
                  dep={dep}
                  liveStatus={liveStatuses[dep.id]}
                  busy={busy}
                  onDisable={doDisarm}
                  onStop={doStop}
                />
              ))}
            </div>
          )}

          {/* Non-live deployments — show Enable Live Execution */}
          {notLiveDeps.length > 0 && (
            <div className="divide-y divide-line">
              {notLiveDeps.map((dep) => (
                <NotLiveRow
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
