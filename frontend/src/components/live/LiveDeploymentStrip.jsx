import { useMemo, useState } from "react";
import { Activity, ChevronDown, ChevronRight, Loader2, LogOut, OctagonX, Pause, Play, RefreshCw, ShieldOff, Square } from "lucide-react";
import { toast } from "sonner";
import { api } from "@/lib/api";
import { fmtINR } from "@/lib/fmt";
import { getApiErrorMessage } from "@/lib/apiError";
import { Button } from "@/components/ui/button";
import DeployToLivePanel from "@/components/live/DeployToLivePanel";
import ConfirmActionDialog from "@/components/live/ConfirmActionDialog";
import LiveAlertsToggle from "@/components/live/LiveAlertsToggle";
import SessionTimeline from "@/components/live/SessionTimeline";
import { isDriftPaused, driftTooltip } from "@/lib/deploymentState";
import { useLiveData } from "@/components/live/LiveDataProvider";
import { asPositionRows } from "@/components/live/liveHelpers";
import {
  bindingView, capHeadroom, describeIntended, entryRefusalView, openPositionRows,
  positionValue, readGovernor, sortDeploymentRows, stopAllLiveVerdict, summarizeExitReport,
} from "@/lib/liveDeploymentView";
import { deploymentEntryEnd } from "@/lib/sessionClock";

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

// ── Cap headroom — the governor's own numbers, never re-derived here ────────
const ratioClass = (r) =>
  r == null ? "text-dim" : r >= 1 ? "text-rose-300 font-semibold" : r >= 0.75 ? "text-warning" : "text-dim";

function Headroom({ gov }) {
  const rows = capHeadroom(gov);
  if (!rows.length) return null;
  const fmt = (key, v) => (v == null ? "—" : key === "loss" ? fmtINR(v) : String(v));
  return (
    <span
      className="text-[11px] font-mono whitespace-nowrap inline-flex items-center gap-1.5"
      data-testid="live-deploy-headroom"
      title="Consumed against this deployment's live caps, as the governor measures them. Loss is today's realized PLUS open unrealized P&L — what the daily loss cap actually gates on. — means unknown (a stale mark), never zero."
    >
      {rows.map((r) => (
        <span key={r.key} className={ratioClass(r.ratio)}>
          {r.label.split(" ")[0]} {fmt(r.key, r.used)}
          {r.configured ? `/${fmt(r.key, r.max)}` : <span className="text-dimmer"> (no cap)</span>}
        </span>
      ))}
    </span>
  );
}

// ── Tighten-only caps form ───────────────────────────────────────────────────
const CAP_FIELDS = [
  ["lots", "lots / entry"], ["max_lots_per_day", "max lots / day"],
  ["max_concurrent", "max open"], ["daily_loss_cap", "daily loss cap ₹"],
];

function TightenCaps({ caps, busy, onTighten }) {
  const [vals, setVals] = useState({});
  const changed = {};
  for (const [k] of CAP_FIELDS) {
    const raw = vals[k];
    if (raw === undefined || raw === "") continue;
    const n = Number(raw);
    if (Number.isFinite(n) && n !== Number(caps?.[k])) changed[k] = n;
  }
  return (
    <div className="flex items-center gap-2 flex-wrap" data-testid="live-deploy-tighten">
      <span className="text-dimmer">tighten caps:</span>
      {CAP_FIELDS.map(([k, label]) => (
        <label key={k} className="inline-flex items-center gap-1">
          <span className="text-dimmer">{label}</span>
          <input
            type="number"
            min="0"
            step={k === "daily_loss_cap" ? "100" : "1"}
            placeholder={caps?.[k] ?? "—"}
            value={vals[k] ?? ""}
            onChange={(e) => setVals((v) => ({ ...v, [k]: e.target.value }))}
            className="w-20 h-6 px-1 rounded border border-line bg-bg-2 text-foreground"
          />
        </label>
      ))}
      <Button
        variant="outline" size="sm"
        disabled={busy || Object.keys(changed).length === 0}
        onClick={() => onTighten(changed).then(() => setVals({}))}
        className="h-6 text-[11px]"
        title="Lower only. Any increase is refused — raising a cap needs Disable → re-Enable."
        data-testid="live-deploy-tighten-go"
      >
        Apply
      </Button>
      <span className="text-dimmer">lower only — raising needs Disable → re-Enable</span>
    </div>
  );
}

// ── Expanded detail: open positions, intended entry, diagnostics ────────────
function RowDetail({ depId, liveStatus, gov, markedRows, busy, onTighten }) {
  const rows = openPositionRows(liveStatus?.open_positions, markedRows);
  const intended = describeIntended(liveStatus?.last_entry?.intended);
  const c = gov?.available ? gov.consumed || {} : {};
  return (
    <div className="px-3 pb-2 pl-7 space-y-1.5 text-[11px] font-mono" data-testid="live-deploy-detail">
      <TightenCaps caps={liveStatus?.caps} busy={busy} onTighten={onTighten} />
      {rows.length === 0 ? (
        <div className="text-dimmer">No open positions registered with the guard.</div>
      ) : (
        <table className="w-full max-w-[640px]">
          <thead>
            <tr className="text-dimmer text-left">
              <th className="font-normal pr-3">contract</th>
              <th className="font-normal pr-3 text-right">qty</th>
              <th className="font-normal pr-3 text-right">entry</th>
              <th className="font-normal pr-3 text-right">last</th>
              <th className="font-normal pr-3 text-right">stop</th>
              <th className="font-normal pr-3 text-right">to stop</th>
              <th className="font-normal text-right">target</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((p) => (
              <tr key={p.tsym} className="text-dim">
                <td className="pr-3 truncate max-w-[180px]" title={p.tsym}>
                  {p.tsym}{!p.filled && <span className="text-warning"> (unfilled)</span>}
                </td>
                <td className="pr-3 text-right tabular-nums">{p.qty ?? "—"}</td>
                <td className="pr-3 text-right tabular-nums">{p.entry?.toFixed(2) ?? "—"}</td>
                <td className={`pr-3 text-right tabular-nums ${p.markStale ? "opacity-60" : ""}`}
                    title={p.markStale ? "no live tick — broker-book or no price" : "live tick"}>
                  {p.ltp?.toFixed(2) ?? "—"}
                </td>
                <td className="pr-3 text-right tabular-nums">{p.stop?.toFixed(2) ?? "—"}</td>
                <td className={`pr-3 text-right tabular-nums ${p.distToStopPct != null && p.distToStopPct < 10 ? "text-rose-300" : ""}`}>
                  {p.distToStopPts == null ? "—" : `${p.distToStopPts.toFixed(2)} (${p.distToStopPct.toFixed(1)}%)`}
                </td>
                <td className="text-right tabular-nums">{p.target?.toFixed(2) ?? "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {intended && (
        <div className="text-dim" data-testid="live-deploy-intended">last intended entry: {intended}</div>
      )}
      {c.open_rows_prior_days > 0 && (
        <div className="text-warning" data-testid="live-deploy-stale-rows">
          {c.open_rows_prior_days} OPEN journal row(s) from an earlier day hold a
          concurrency slot (outside the loss sum). The startup reconcile closes them once
          the broker can prove them flat.
        </div>
      )}
      {c.closed_today_null_realized > 0 && (
        <div className="text-warning">
          {c.closed_today_null_realized} trade(s) closed today with no journalled P&L —
          the loss figure counts them as zero.
        </div>
      )}
      <SessionTimeline depId={depId} />
    </div>
  );
}

// The contracts a confirmation is about to act on: symbol, quantity and approximate
// value (last price x qty). No price -> "value unknown", never a made-up number.
function PositionLines({ rows }) {
  if (rows.length === 0) {
    return <p>No open positions are registered with the guard.</p>;
  }
  return (
    <ul className="list-disc pl-4" data-testid="confirm-position-lines">
      {rows.map((p) => {
        const value = positionValue(p);
        return (
          <li key={p.tsym}>
            {p.tsym} · qty {p.qty ?? "—"}
            {value !== null ? ` · ≈ ${fmtINR(value)}` : " · value unknown"}
          </li>
        );
      })}
    </ul>
  );
}

// ── One live-mode deployment row ────────────────────────────────────────────
function LiveRow({ dep, liveStatus, busy, onDisable, onStop, onPause, onResume, onFlatten, onTighten, liveMtm, markedRows, nowMs, session }) {
  const [open, setOpen] = useState(false);
  const [flattenOpen, setFlattenOpen] = useState(false);
  const [disableOpen, setDisableOpen] = useState(false);
  const [stopOpen, setStopOpen] = useState(false);
  const [holdAfter, setHoldAfter] = useState(true);
  // Status payload shape: { today: {orders, lots, realized_pnl}, open_positions: [...] }
  const today = liveStatus?.today || {};
  const todayOrders = today.orders ?? 0;
  const todayLots = today.lots ?? 0;
  const todayRealised = today.realized_pnl ?? null;
  const openPositions = Array.isArray(liveStatus?.open_positions)
    ? liveStatus.open_positions.length
    : (liveStatus?.open_positions ?? 0);
  // The operator HOLD. Orthogonal to live/not-live: a held deployment is still
  // fully live-authorized and still guarding its open book — it just refuses new
  // entries. Rendering it as "not live" would be a lie in the dangerous direction.
  const paused = Boolean(liveStatus?.live_paused);
  // The governor's own answer to "can this place an entry right now?". A missing
  // governor never reads as yes.
  const gov = readGovernor(liveStatus);
  const binding = bindingView(gov);
  const refusal = entryRefusalView(liveStatus?.last_entry, nowMs);

  return (
    <div data-testid="live-deploy-row-wrap">
    <div className="px-3 py-2 flex items-center gap-2 flex-wrap" data-testid="live-deploy-row">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="shrink-0 text-dimmer hover:text-foreground"
        aria-expanded={open}
        title={open ? "Hide positions" : "Show open positions, intended entry and diagnostics"}
        data-testid="live-deploy-expand"
      >
        {open ? <ChevronDown className="w-3.5 h-3.5" /> : <ChevronRight className="w-3.5 h-3.5" />}
      </button>
      {/* Live indicator — amber (steady) while held, pulsing red ONLY when the
          governor says an entry could fire right now. The pulse is the "orders can
          fire right now" cue: mode alone kept it pulsing on an expired token, after
          the 15:00 cutoff and on market holidays. Blocked → steady, with the reason. */}
      <span
        className={`w-2 h-2 rounded-full shrink-0 ${
          paused ? "bg-amber" : binding.canTrade ? "bg-danger animate-pulse" : "bg-rose-400/60"
        }`}
        title={paused ? "LIVE — held (no new entries)" : binding.canTrade ? "LIVE — can place entries now" : `LIVE — blocked: ${binding.text}`}
        data-testid="live-deploy-dot"
      />
      <div className="min-w-0">
        <div className="font-medium text-xs truncate max-w-[180px] text-foreground" title={dep.name}>
          {dep.name || dep.id?.slice(0, 8) || "—"}
        </div>
        <div className="text-[10px] text-dimmer truncate max-w-[180px]" title={dep.strategy_id}>
          {dep.strategy_id || "—"}
        </div>
      </div>

      {/* Today's stats. The "today" prefix is load-bearing, not decoration: these
          are CUMULATIVE counters for the IST day (every entry placed, closed ones
          included), sitting one span away from a live open count. Unlabelled,
          "2 ord · 4 lots" next to "0 open" reads as two orders still working —
          which is exactly how it was misread on 2026-09-03, on a day whose two
          entries had both been squared hours earlier. */}
      <span
        className="text-[11px] font-mono text-dim whitespace-nowrap ml-2"
        title="Cumulative for the IST day: orders placed, lots traded, and realised P&L across ALL of this deployment's live trades today — closed ones included. Not a count of anything still working."
      >
        today {todayOrders} ord · {todayLots} lots
        {todayRealised != null && (
          <> · <span className={Number(todayRealised) >= 0 ? "text-success" : "text-danger"}>{fmtINR(todayRealised)}</span></>
        )}
      </span>

      {/* Open positions — a CURRENT-state number. */}
      <span
        className="text-[11px] font-mono text-dimmer whitespace-nowrap"
        title="Positions open RIGHT NOW and registered with the software exit guard."
      >
        · {openPositions} open
      </span>

      {/* LIVE MTM — the number this pane never had. Until 2026-09-16 the row
          showed only today's CUMULATIVE realised P&L, so a deployment sitting on
          an open position displayed no live P&L whatsoever. Sourced from the
          marks book, which re-marks urmtom on the Upstox tick. */}
      {liveMtm && liveMtm.count > 0 && (
        <span
          className={`text-[11px] font-mono whitespace-nowrap ${liveMtm.stale ? "opacity-60" : ""}`}
          title={liveMtm.stale
            ? `MTM on ${liveMtm.count} open position(s) — priced off the broker book, not a live tick`
            : `Live mark-to-market on ${liveMtm.count} open position(s), re-marked on the Upstox tick`}
          data-testid="live-deploy-mtm"
        >
          · MTM <span className={Number(liveMtm.value) >= 0 ? "text-success" : "text-danger"}>
            {fmtINR(liveMtm.value)}
          </span>
          {liveMtm.stale && <span className="text-warning"> (broker)</span>}
        </span>
      )}

      {/* Cap headroom — what is consumed against each live cap, from the governor. */}
      <Headroom gov={gov} />

      {/* This deployment's own last entry time, when earlier than the global
          15:00 cutoff (the default entry window ends 14:50). */}
      {deploymentEntryEnd(liveStatus?.entry_window, session) && (
        <span className="text-[10px] font-mono text-dimmer whitespace-nowrap"
              title="This deployment's entry window closes before the 15:00 IST live cutoff."
              data-testid="live-deploy-entry-end">
          {deploymentEntryEnd(liveStatus?.entry_window, session)}
        </span>
      )}

      {/* WHY it cannot place an entry right now — the first refusal in the entry
          path's own order (authorization → account → deployment). The held case
          has its own chip below. */}
      {!binding.canTrade && !(paused && gov.binding?.reason === "live_paused") && (
        <span
          className={`inline-flex items-center gap-1 text-[10px] font-medium rounded px-1.5 py-0.5 whitespace-nowrap border ${
            binding.tone === "danger"
              ? "text-rose-300 bg-rose-500/10 border-rose-500/30"
              : "text-warning bg-amber-500/10 border-amber-500/30"
          }`}
          title={binding.title}
          data-testid="live-deploy-binding"
        >
          <OctagonX className="w-3 h-3 shrink-0" />
          blocked: {binding.text}
        </span>
      )}

      {/* Entry-refused chip — WHY a live deployment isn't placing (stale
          premium / throttle / gate block). Surfaces the previously write-only
          signals.live_trade_error via the live-status payload's last_entry.
          Date-bounded: a refusal from an earlier session is dimmed and dated
          rather than read as current. */}
      {refusal && (
        <span
          className={`inline-flex items-center gap-1 text-[10px] font-medium text-rose-300 bg-rose-500/10 border border-rose-500/30 rounded px-1.5 py-0.5 whitespace-nowrap ${refusal.stale ? "opacity-50" : ""}`}
          title={`Last live entry refused: ${refusal.reason}${liveStatus.last_entry.at ? ` (at ${liveStatus.last_entry.at})` : ""}`}
          data-testid="live-entry-refused"
        >
          <OctagonX className="w-3 h-3 shrink-0" />
          {refusal.stale ? `entry refused ${refusal.dateLabel}` : "entry refused"}: {entryErrorLabel(refusal.reason)}
        </span>
      )}

      {/* HELD chip — states the one thing an operator could get wrong here:
          paused is NOT flat. Open positions stay open and stay guarded. */}
      {paused && (
        <span
          className="inline-flex items-center gap-1 text-[10px] font-medium text-warning bg-amber-500/10 border border-amber-500/30 rounded px-1.5 py-0.5 whitespace-nowrap"
          title="No new entries. Open positions stay OPEN and keep their stop/target/trailing and resting OCO — this is a hold, not a flatten. Use Stop to flatten."
          data-testid="live-deploy-held"
        >
          <Pause className="w-3 h-3 shrink-0" />
          held — no new entries
        </span>
      )}

      {/* Controls */}
      <div className="ml-auto flex items-center gap-1.5">
        {/* Flatten — exit THIS deployment's positions and stay live. */}
        <Button
          variant="ghost"
          size="sm"
          disabled={busy || openPositions === 0}
          onClick={() => { setHoldAfter(true); setFlattenOpen(true); }}
          className="h-7 text-xs text-rose-300"
          title={openPositions === 0 ? "No open positions" : "Exit this deployment's open positions — it stays live"}
          data-testid="live-deploy-flatten"
        >
          <LogOut className="w-3 h-3 mr-1" />
          Flatten
        </Button>
        {/* Pause/Resume is the REVERSIBLE control: it flips risk.live.paused only,
            so mode stays "live" and Resume needs no re-consent. Deliberately NOT
            api.pauseDeployment() — that routes through the status path, which
            demotes a live deployment back to paper and costs the full caps +
            consent ceremony to undo. */}
        {paused ? (
          <Button
            variant="ghost"
            size="sm"
            disabled={busy}
            onClick={() => onResume(dep)}
            className="h-7 text-xs text-success"
            data-testid="live-deploy-resume"
          >
            <Play className="w-3 h-3 mr-1" />
            Resume
          </Button>
        ) : (
          <Button
            variant="ghost"
            size="sm"
            disabled={busy}
            onClick={() => onPause(dep)}
            className="h-7 text-xs text-warning"
            data-testid="live-deploy-pause"
          >
            <Pause className="w-3 h-3 mr-1" />
            Pause
          </Button>
        )}
        <Button
          variant="ghost"
          size="sm"
          disabled={busy}
          onClick={() => setDisableOpen(true)}
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
          onClick={() => setStopOpen(true)}
          className="h-7 text-xs border-rose-500/40 text-rose-300 hover:text-rose-200"
          data-testid="live-deploy-stop"
        >
          <Square className="w-3 h-3 mr-1" />
          Stop
        </Button>
      </div>
    </div>
    {open && <RowDetail depId={dep.id} liveStatus={liveStatus} gov={gov} markedRows={markedRows}
                        busy={busy} onTighten={(body) => onTighten(dep, body)} />}
    <ConfirmActionDialog
      open={flattenOpen}
      onOpenChange={setFlattenOpen}
      title={`Flatten "${dep.name || dep.id}"?`}
      confirmLabel={holdAfter ? "Flatten & hold" : "Flatten, keep trading"}
      busy={busy}
      onConfirm={async () => { await onFlatten(dep, holdAfter); setFlattenOpen(false); }}
    >
      <p>Sends REAL exit orders (marketable limit, re-priced until filled) for:</p>
      <PositionLines rows={openPositionRows(liveStatus?.open_positions, markedRows)} />
      <p>The deployment stays LIVE. A contract shared with another position is NOT sent.
         Outside market hours nothing is sent.</p>
      <label className="flex items-center gap-2 text-foreground">
        <input type="checkbox" checked={holdAfter} onChange={(e) => setHoldAfter(e.target.checked)}
               data-testid="live-deploy-flatten-hold" />
        also HOLD new entries (recommended — otherwise the next signal can re-enter
        while this exit is still working)
      </label>
    </ConfirmActionDialog>
    {/* Disable — replaces a bare window.confirm. It names the one thing an operator
        could get wrong: disable is NOT flatten. */}
    <ConfirmActionDialog
      open={disableOpen}
      onOpenChange={setDisableOpen}
      title={`Disable live execution for "${dep.name || dep.id}"?`}
      confirmLabel="Disable live"
      danger={false}
      busy={busy}
      onConfirm={async () => { await onDisable(dep); setDisableOpen(false); }}
    >
      <p>No more live orders will be placed for this deployment; it goes back to PAPER.</p>
      <p>Its {openPositions} open position{openPositions === 1 ? "" : "s"} are NOT closed —
         they stay open and guarded (stop / target / trailing and the resting OCO) until they
         exit. Use Flatten or Stop to exit them.</p>
    </ConfirmActionDialog>
    {/* Stop — squares this deployment's positions, then demotes it to paper AND
        pauses it. Lists exactly what will be sent, like Flatten does. */}
    <ConfirmActionDialog
      open={stopOpen}
      onOpenChange={setStopOpen}
      title={`Stop "${dep.name || dep.id}"?`}
      confirmLabel="Stop & flatten"
      busy={busy}
      onConfirm={async () => { await onStop(dep); setStopOpen(false); }}
    >
      <p>Sends REAL exit orders (marketable limit, re-priced until filled) for:</p>
      <PositionLines rows={openPositionRows(liveStatus?.open_positions, markedRows)} />
      <p>Then the deployment is taken OUT of live (demoted to paper) and PAUSED. Going live
         again needs Enable Live Execution — caps and consent — from scratch. A contract
         shared with another position is NOT sent; outside market hours nothing is sent.</p>
    </ConfirmActionDialog>
    </div>
  );
}

// ── One non-live deployment row (shows Enable Live Execution trigger) ──────
function NotLiveRow({ dep, busy, onArmed, onRepin }) {
  const drift = isDriftPaused(dep);
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
      {drift && (
        <Button
          variant="ghost" size="sm" disabled={busy}
          onClick={() => onRepin(dep)}
          className="h-7 text-xs text-warning"
          title={`${driftTooltip(dep)} — re-pins to the current strategy code and resumes PAPER. Live must be re-enabled separately.`}
          data-testid="live-deploy-repin"
        >
          <RefreshCw className="w-3 h-3 mr-1" />
          Re-pin & resume paper
        </Button>
      )}
      <div className="ml-auto">
        {/* eslint-disable-next-line react/prop-types */}
        <DeployToLivePanel dep={dep} onArmed={onArmed} />
      </div>
    </div>
  );
}

const COLLAPSED_STORAGE_KEY = "af.liveDeploymentStrip.collapsed";
const NOTLIVE_STORAGE_KEY = "af.liveDeploymentStrip.notLiveOpen";

// ── Main strip ─────────────────────────────────────────────────────────────
export default function LiveDeploymentStrip() {
  // Deployments + the batched per-deployment live status come from the shared
  // LiveDataProvider (one 10s batched poll); this strip no longer self-polls.
  // `liveStatuses` is the provider's deployLive byId map (today's counters/open
  // positions/last-entry per deployment — its own `armed` field is dead, see
  // the partition comment below; live/not-live is read off `deployments[].mode`).
  // `positions` is the MARKED broker book: the same rows, with lp/urmtom
  // re-marked on the Upstox tick where one is available, plus mark_source and
  // mark_age_ms per row. Consuming it here is what puts live P&L on this pane.
  const { deployments, deployLive: liveStatuses, positions, refetch, armState } = useLiveData();
  const [busy, setBusy] = useState(false);
  const [stopAllOpen, setStopAllOpen] = useState(false);
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

  const [showNotLive, setShowNotLive] = useState(() => {
    try {
      return localStorage.getItem(NOTLIVE_STORAGE_KEY) === "1";
    } catch {
      return false;
    }
  });
  const toggleNotLive = () => {
    setShowNotLive((prev) => {
      const next = !prev;
      try {
        localStorage.setItem(NOTLIVE_STORAGE_KEY, next ? "1" : "0");
      } catch {
        // ignore storage errors (e.g. private mode)
      }
      return next;
    });
  };

  // After any enable/disable/stop, re-pull everything (statuses + roster + arm-state).
  const refreshAll = refetch.all;

  // Disable / Stop / Stop ALL confirm in a ConfirmActionDialog (rendered by the row /
  // the header), never a bare window.confirm — the dialog names what will happen.
  const doDisarm = async (dep) => {
    setBusy(true);
    try {
      await api.disableDeploymentLive(dep.id);
      toast.success(`Disabled live execution for "${dep.name || dep.id}"`);
      await refreshAll();
    } catch (e) {
      toast.error(getApiErrorMessage(e, e.message));
    } finally {
      setBusy(false);
    }
  };

  // HOLD — no confirm dialog. It is reversible in one click, takes nothing away
  // (caps, authorization and the guarded book all survive) and is the SAFE
  // direction; a confirm here would only train the operator to click through the
  // ones that do matter.
  const doPause = async (dep) => {
    setBusy(true);
    try {
      await api.pauseDeploymentLive(dep.id);
      toast.success(
        `Held "${dep.name || dep.id}" — no new entries. Open positions stay open and keep their exits.`,
      );
      await refreshAll();
    } catch (e) {
      toast.error(getApiErrorMessage(e, e.message));
    } finally {
      setBusy(false);
    }
  };

  const doResume = async (dep) => {
    setBusy(true);
    try {
      await api.resumeDeploymentLive(dep.id);
      toast.success(`Resumed "${dep.name || dep.id}" — live entries active again`);
      await refreshAll();
    } catch (e) {
      toast.error(getApiErrorMessage(e, e.message));
    } finally {
      setBusy(false);
    }
  };

  // The toast reports what the exit report says, not that a button was pressed: the
  // old "Stopped" arrived green whether or not a position was still open.
  const doStop = async (dep) => {
    setBusy(true);
    try {
      const res = await api.liveStop(dep.id);
      const s = summarizeExitReport(res);
      (s.ok ? toast.success : toast.error)(
        `Stopped "${dep.name || dep.id}" (paper + paused): ${s.message}`,
        { duration: s.ok ? 6000 : 15000 },
      );
      await refreshAll();
    } catch (e) {
      toast.error(getApiErrorMessage(e, e.message));
    } finally {
      setBusy(false);
    }
  };

  // Flatten WITHOUT demoting: this deployment's positions only; it stays live.
  // The toast reports what was SUBMITTED — never "flattened" (a broker
  // acceptance is not a fill) — and turns red for anything failed or skipped.
  const doFlatten = async (dep, hold) => {
    setBusy(true);
    try {
      const res = await api.liveFlatten(dep.id, { hold: Boolean(hold) });
      const s = summarizeExitReport(res);
      (s.ok ? toast.success : toast.error)(
        `${dep.name || dep.id}${res?.held ? " (held)" : ""}: ${s.message}`,
        { duration: s.ok ? 6000 : 15000 },
      );
      await refreshAll();
    } catch (e) {
      toast.error(getApiErrorMessage(e, e.message));
    } finally {
      setBusy(false);
    }
  };

  const doTighten = async (dep, body) => {
    setBusy(true);
    try {
      const res = await api.liveCaps(dep.id, body);
      const adv = (res?.advisories || []).map((a) => a.message).join(" ");
      toast.success(`Caps tightened for "${dep.name || dep.id}": ${(res?.changed || []).join(", ") || "no change"}${adv ? ` — ${adv}` : ""}`);
      await refreshAll();
    } catch (e) {
      toast.error(getApiErrorMessage(e, e.message));
    } finally {
      setBusy(false);
    }
  };

  const doRepin = async (dep) => {
    setBusy(true);
    try {
      await api.repinDeploymentSource(dep.id);
      toast.success(`Re-pinned "${dep.name || dep.id}" — resuming in PAPER. Re-enable live separately.`);
      await refreshAll();
    } catch (e) {
      toast.error(getApiErrorMessage(e, e.message));
    } finally {
      setBusy(false);
    }
  };

  // The confirmation (with the honest blast radius) is the requireText dialog
  // rendered in the header; this runs only after it is armed and confirmed.
  const doStopAll = async () => {
    setBusy(true);
    try {
      const res = await api.stopAllDeployments();
      const squared = res?.squared_off_count ?? (res?.squared_off?.length ?? 0);
      const paused = (res?.paused_deployment_ids || []).length;
      const disabledLive = (res?.disarmed_live_deployment_ids || []).length;
      const base = `Stopped ALL — ${squared} paper position(s) squared · ${paused} deployment(s) `
        + `paused · ${disabledLive} live deployment(s) disabled`;
      // The live half is judged by each deployment's own exit report. "N disabled"
      // says nothing about whether a position is still open on the broker.
      const names = {};
      for (const d of deployments || []) names[d.id] = d.name || d.id;
      const live = stopAllLiveVerdict(res, names);
      if (live.ok) {
        toast.success(
          live.submitted > 0
            ? `${base} · ${live.submitted} live exit(s) submitted — awaiting fill confirmation`
            : base,
          { duration: 8000 },
        );
      } else {
        toast.error(`${base} — but NOT every live exit went clean: ${live.message}`, { duration: 20000 });
      }
      await refreshAll();
    } catch (e) {
      toast.error(getApiErrorMessage(e, e.message));
    } finally {
      setBusy(false);
    }
  };

  // Partition on the deployment's own `mode` field (from /deployments) — mode IS
  // the live authorization. Sorted so a deployment holding positions renders
  // first, then held, then idle (it used to render in roster order, so the one
  // holding real money could be seventh); not-live rows collapse behind a count.
  const { live: liveDeps, notLive: notLiveDeps } = sortDeploymentRows(deployments, liveStatuses);
  const hasLive = liveDeps.length > 0;
  // What Stop ALL is about to touch, from the guard's own open-position lists.
  const openLiveCount = liveDeps.reduce((n, d) => {
    const open = liveStatuses?.[d.id]?.open_positions;
    return n + (Array.isArray(open) ? open.length : 0);
  }, 0);
  const markedRows = useMemo(() => asPositionRows(positions) || [], [positions]);
  const nowMs = Date.now();

  // Attribute the marked book to deployments. The broker book has no
  // deployment_id — the link is the trading symbol, which the guard records per
  // deployment in its live-status open_positions. A position the guard does not
  // own is deliberately NOT attributed to anyone: an unguarded broker position is
  // surfaced by the alert rail, and silently folding it into some deployment's
  // MTM would hide it.
  const mtmByDeployment = useMemo(() => {
    const out = {};
    const rows = asPositionRows(positions) || [];
    if (!rows.length) return out;
    const byTsym = new Map();
    for (const r of rows) {
      const t = r?.tsym;
      if (t) byTsym.set(String(t), r);
    }
    for (const dep of deployments || []) {
      const open = liveStatuses?.[dep.id]?.open_positions;
      if (!Array.isArray(open) || open.length === 0) continue;
      let value = 0, count = 0, stale = false;
      for (const op of open) {
        const row = byTsym.get(String(op?.tsym || ""));
        if (!row) continue;
        const v = Number(row.urmtom);
        if (!Number.isFinite(v)) continue;
        value += v; count += 1;
        // "broker" means no live tick backed this row — say so rather than
        // letting a 15s REST price render as a live mark.
        if (String(row.mark_source || "") !== "tick") stale = true;
      }
      if (count > 0) out[dep.id] = { value, count, stale };
    }
    return out;
  }, [positions, deployments, liveStatuses]);

  // Aggregate today's realized P&L across all live deployments, for the
  // always-visible header summary (matches LiveRow's own today-P&L coloring).
  let todayRealisedTotal = null;
  let heldCount = 0;
  let canTradeCount = 0;
  for (const dep of liveDeps) {
    const realised = liveStatuses[dep.id]?.today?.realized_pnl;
    if (realised != null) {
      todayRealisedTotal = (todayRealisedTotal ?? 0) + Number(realised);
    }
    if (liveStatuses[dep.id]?.live_paused) heldCount += 1;
    else if (bindingView(readGovernor(liveStatuses[dep.id])).canTrade) canTradeCount += 1;
  }
  // The header answers "how many can place an order RIGHT NOW?" — so it asks the
  // governor, not the mode. Counting mode alone said "2 live" on an expired token,
  // after the 15:00 cutoff and on a market holiday.
  const blockedCount = liveDeps.length - heldCount - canTradeCount;

  // The armed-summary lift to a parent is GONE (2026-09-09). Its only consumer was
  // LiveBanner, which is imported by nothing since the cockpit redesign, so the
  // effect short-circuited on every render. Nothing is lost: the dry-run condition
  // it carried is rendered persistently by ExecutionStateStrip, from the arm-state
  // verdict — arm_state.py emits "N deployment(s) armed but LIVE_AUTOPLACE_ARMED
  // off (dry-run)" and drops would_transmit_entry to false.

  if (!deployments || deployments.length === 0) return null;

  return (
    <div
      className="rounded-lg border border-line bg-bg-1"
      data-testid="live-deploy-strip"
    >
      {/* Header — always visible regardless of collapsed state.
          flex-wrap + shrink-0 are load-bearing: this strip also renders inside the
          ~460px config drawer, where an unwrapped row shrank each item below its
          content and the "Live Deployments" label overflowed its crushed box and
          painted ON TOP of the summary text. Items now keep their natural width
          and wrap to a second line instead of overlapping. */}
      <div className="px-3 py-2 border-b border-line flex flex-wrap items-center gap-x-2 gap-y-1.5">
        <button
          type="button"
          onClick={toggleCollapsed}
          className="flex items-center gap-2 min-w-0 shrink-0 hover:opacity-80"
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
        <span className="text-[11px] text-dimmer font-mono whitespace-nowrap shrink-0" data-testid="live-deploy-strip-summary">
          {canTradeCount} can trade
          {blockedCount > 0 && <span className="text-rose-300"> · {blockedCount} blocked</span>}
          {heldCount > 0 && <span className="text-warning"> · {heldCount} held</span>}
          {" · "}{notLiveDeps.length} not live
          {todayRealisedTotal != null && (
            <> · <span className={todayRealisedTotal >= 0 ? "text-success" : "text-danger"}>{fmtINR(todayRealisedTotal)}</span></>
          )}
        </span>

        {/* Decorative hint — the first thing to give up room in a narrow container. */}
        {!collapsed && (
          <span className="text-[11px] text-dimmer truncate hidden min-[420px]:inline">
            enable / disable / stop real orders
          </span>
        )}
        {busy && <Loader2 className="w-3.5 h-3.5 animate-spin text-dimmer ml-1 shrink-0" />}
        <LiveAlertsToggle />
        <Button
          variant="outline"
          size="sm"
          disabled={busy || !hasLive}
          onClick={() => setStopAllOpen(true)}
          // Theme tokens, not raw rose-*: rose-300 on the light theme's white
          // ground is washed out to near-illegible for a destructive control.
          className="ml-auto shrink-0 h-7 text-xs border-danger/40 text-danger hover:bg-danger/10"
          data-testid="live-deploy-stop-all"
          title="Disable live execution and square off every live deployment"
        >
          <OctagonX className="w-3.5 h-3.5 mr-1" />
          Stop ALL live
        </Button>
      </div>

      {/* Stop ALL — the gravest action on the page, so it must be TYPED. The body is
          the honest blast radius: /deployments/stop-all reaches paper as well as live. */}
      <ConfirmActionDialog
        open={stopAllOpen}
        onOpenChange={setStopAllOpen}
        title="Stop ALL trading?"
        confirmLabel="Stop ALL"
        requireText="STOP ALL"
        busy={busy}
        onConfirm={async () => { await doStopAll(); setStopAllOpen(false); }}
      >
        <ul className="list-disc pl-4">
          <li>squares off EVERY open PAPER trade</li>
          <li>pauses EVERY active deployment (paper included)</li>
          <li>disables live execution + flattens every LIVE deployment
              ({liveDeps.length} live now, {openLiveCount} open live position{openLiveCount === 1 ? "" : "s"})</li>
        </ul>
        <p>Exit orders for live positions are SUBMITTED, not confirmed filled — check the
           blotter afterwards. Outside market hours nothing is sent.</p>
      </ConfirmActionDialog>

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
                  onPause={doPause}
                  onResume={doResume}
                  onFlatten={doFlatten}
                  onTighten={doTighten}
                  liveMtm={mtmByDeployment[dep.id]}
                  markedRows={markedRows}
                  nowMs={nowMs}
                  session={armState?.session}
                />
              ))}
            </div>
          )}

          {/* Non-live deployments — collapsed behind a count so they never push
              the live rows off-screen. */}
          {notLiveDeps.length > 0 && (
            <button
              type="button"
              onClick={toggleNotLive}
              className="w-full px-3 py-1.5 border-t border-line flex items-center gap-1.5 text-[11px] text-dimmer hover:text-foreground"
              aria-expanded={showNotLive}
              data-testid="live-deploy-notlive-toggle"
            >
              {showNotLive ? <ChevronDown className="w-3.5 h-3.5" /> : <ChevronRight className="w-3.5 h-3.5" />}
              {notLiveDeps.length} not-live deployment{notLiveDeps.length !== 1 ? "s" : ""} — enable live execution
            </button>
          )}
          {notLiveDeps.length > 0 && showNotLive && (
            <div className="divide-y divide-line">
              {notLiveDeps.map((dep) => (
                <NotLiveRow
                  key={dep.id}
                  dep={dep}
                  busy={busy}
                  onArmed={refreshAll}
                  onRepin={doRepin}
                />
              ))}
            </div>
          )}
        </>
      )}
    </div>
  );
}
