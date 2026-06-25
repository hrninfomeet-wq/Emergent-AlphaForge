import { Zap, Shield, ShieldCheck } from "lucide-react";

/**
 * ExecutionStateStrip — the SINGLE "will a signal place a REAL order right now?"
 * verdict, from GET /live-broker/arm-state. Replaces the old hardcoded
 * "L3 — Live-Test execution enabled" chip that always lied regardless of state.
 *
 * Presentational only: `armState` is passed down from the dashboard's existing
 * poll (no extra poller — keeps the page on one cadence).
 */
export default function ExecutionStateStrip({ armState }) {
  if (!armState) return null;
  const {
    verdict,
    label,
    would_transmit_entry: entryTx,
    would_transmit_exit: exitTx,
    reasons,
  } = armState;

  const tone =
    verdict === "LIVE"
      ? { box: "border-2 border-danger bg-danger/15 text-danger", Icon: Zap }
      : verdict === "DRY_RUN"
        ? { box: "border border-amber-500/50 bg-amber-500/10 text-amber-300", Icon: Shield }
        : { box: "border border-emerald-500/40 bg-emerald-500/10 text-emerald-300", Icon: ShieldCheck };
  const Icon = tone.Icon;

  return (
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
      {Array.isArray(reasons) && reasons.length > 0 && (
        <span
          className="text-dimmer ml-auto truncate max-w-[55%]"
          title={reasons.join(" · ")}
        >
          {reasons.join(" · ")}
        </span>
      )}
    </div>
  );
}
