import { Zap, Shield, ShieldCheck, Loader2, PowerOff, AlertTriangle } from "lucide-react";

/**
 * ExecutionStateStrip — the SINGLE "will a signal place a REAL order right now?"
 * verdict, from GET /live-broker/arm-state. Replaces the old hardcoded
 * "L3 — Live-Test execution enabled" chip that always lied regardless of state.
 *
 * Presentational: `armState` is passed down from the dashboard's existing poll
 * (no extra poller — keeps the page on one cadence). The one action it offers is
 * a STAND DOWN button (wired via `onStandDown`) that reverts the MANUAL ticket
 * mode to LIVE_OFFLINE — shown only when the manual path is in LIVE_TEST (armed
 * or with a consumed single-shot latch), since that's the only state there is
 * anything to neutralise/reset. The AUTO (deployment) path is governed by the
 * Live Deployment strip, not here.
 */
export default function ExecutionStateStrip({ armState, onStandDown, standingDown = false }) {
  if (!armState) return null;
  const {
    verdict,
    label,
    mode,
    single_shot_consumed: latchConsumed,
    would_transmit_entry: entryTx,
    // Absent must NOT read as dry-run: the software guard always transmits now, so
    // a missing field means "payload older than v0.56.0", not "exits are logs only".
    // Rendering "auto-squares: dry-run" over live positions is the dangerous direction.
    would_transmit_exit: exitTx = true,
    exit_gap: exitGap,
    warning,
    reasons,
  } = armState;

  const tone =
    verdict === "LIVE"
      ? { box: "border-2 border-danger bg-danger/15 text-danger", Icon: Zap }
      : verdict === "DRY_RUN"
        ? { box: "border border-amber-500/50 bg-amber-500/10 text-warning", Icon: Shield }
        : { box: "border border-emerald-500/40 bg-emerald-500/10 text-emerald-300", Icon: ShieldCheck };
  const Icon = tone.Icon;

  // The manual ticket path lives in the mode singleton: LIVE_TEST means it is
  // either armed (unconsumed) or just fired (consumed). In both cases STAND DOWN
  // is meaningful — revert to LIVE_OFFLINE neutralises an armed shot and resets a
  // consumed latch in one call. Hide it once already safe (PAPER / LIVE_OFFLINE).
  const manualLive = mode === "LIVE_TEST";

  return (
    <div className="space-y-1.5">
    {(exitGap || warning) && (
      <div
        className="rounded-lg px-3 py-2 flex items-start gap-2 text-xs font-mono border-2 border-amber-500 bg-amber-500/15 text-warning"
        data-testid="execution-gate-gap-warning"
        role="alert"
      >
        <AlertTriangle className="w-4 h-4 shrink-0 mt-0.5" />
        <span>
          <b className="uppercase tracking-wider">Exit gate gap:</b>{" "}
          {warning ||
            "Real entries armed but guard auto-squares are dry-run — only the broker OCO protects open positions."}
        </span>
      </div>
    )}
    <div
      className={`rounded-lg px-3 py-2 flex items-center gap-2 flex-wrap text-xs font-mono ${tone.box}`}
      data-testid="execution-state-strip"
    >
      <Icon className="w-4 h-4 shrink-0" />
      <span className="font-bold uppercase tracking-wider">{label}</span>
      <span className="ml-1">
        entries:{" "}
        <b className={entryTx ? "text-danger" : "text-dim"}>{entryTx ? "TRANSMIT" : "dry-run"}</b>
      </span>
      <span>
        &middot; auto-squares:{" "}
        <b className={exitTx ? "text-danger" : "text-dim"}>{exitTx ? "TRANSMIT" : "dry-run"}</b>
      </span>
      {manualLive && (
        <span className="text-dimmer">
          &middot; manual: <b className="text-current">LIVE_TEST</b>
          {latchConsumed ? " (latch consumed)" : " (armed)"}
        </span>
      )}

      {manualLive && typeof onStandDown === "function" && (
        <button
          type="button"
          onClick={onStandDown}
          disabled={standingDown}
          title="Revert the manual ticket to LIVE_OFFLINE (neutralises an armed single-shot and resets a consumed latch)"
          className="inline-flex items-center gap-1 px-2 py-0.5 rounded-md border border-current bg-black/25 text-xs font-mono hover:bg-black/40 disabled:opacity-60 transition-colors"
          data-testid="execution-stand-down-btn"
        >
          {standingDown ? <Loader2 className="w-3 h-3 animate-spin" /> : <PowerOff className="w-3 h-3" />}
          {standingDown ? "Standing down…" : "Stand down"}
        </button>
      )}

      {Array.isArray(reasons) && reasons.length > 0 && (
        <span
          className="text-dimmer ml-auto truncate max-w-[45%]"
          title={reasons.join(" · ")}
        >
          {reasons.join(" · ")}
        </span>
      )}
    </div>
    </div>
  );
}
