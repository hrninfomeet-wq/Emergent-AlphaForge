/**
 * ONE source of truth for "what state is this deployment in, and what can the
 * operator do about it".
 *
 * These questions were previously answered by three different pages with three
 * slightly different expressions (the /live deployment card, the /paper control
 * strip, and the live cockpit), which is how a drift-paused deployment could show
 * a bare "Paused" pill on one page and an actionable "Re-pin & resume" on
 * another. Any surface that lists deployments should import these instead of
 * re-deriving them.
 */

import { formatIstWhen } from "./istWhen.js";

/** Why the deployment is paused, or null. Kill-switch wins over drift. */
export function pauseReasonOf(dep) {
  return dep?.kill_switch_reason || dep?.drift_reason || null;
}

/**
 * The pause reason WITH ITS DATE, for the line that says "auto-paused: …".
 *
 * `kill_switch_reason` / `drift_reason` are persisted on the deployment and are NOT
 * cleared by a manual pause or resume — so a bare "auto-paused: max_consecutive_losses"
 * can be a fortnight old and read exactly like this morning's. The timestamps the
 * evaluator writes with them (`kill_switch_paused_at`, `drift_detected_at`) say when.
 *
 * When both a kill-switch and a drift reason are stored the NEWEST describes the pause
 * we are in (a reason with no timestamp counts as oldest — unknown is never promoted).
 * `stale` is true when the reason is not from today (IST) or its date is unknown: the
 * caller shows it in the warning colour with `title`, because a hand-pause since may
 * mean it is not why the deployment is paused now.
 *
 * @returns {null | {kind: "kill_switch"|"drift", reason: string, whenLabel: string,
 *                   dated: boolean, stale: boolean, text: string, title: string}}
 */
export function pauseReasonView(dep, nowMs = Date.now()) {
  const cands = [];
  if (dep?.kill_switch_reason) {
    cands.push({ kind: "kill_switch", reason: String(dep.kill_switch_reason),
                 when: formatIstWhen(dep.kill_switch_paused_at, nowMs) });
  }
  if (dep?.drift_reason) {
    cands.push({ kind: "drift", reason: String(dep.drift_reason),
                 when: formatIstWhen(dep.drift_detected_at, nowMs) });
  }
  if (cands.length === 0) return null;
  // Newest first; an undated reason sorts last. Ties keep kill-switch ahead of drift.
  cands.sort((a, b) => (b.when ? b.when.ms : -Infinity) - (a.when ? a.when.ms : -Infinity));
  const c = cands[0];
  const dated = c.when !== null;
  const whenLabel = dated ? c.when.label : "date not recorded";
  const stale = !dated || !c.when.today;
  return {
    kind: c.kind,
    reason: c.reason,
    whenLabel,
    dated,
    stale,
    text: `auto-paused (${whenLabel}): ${c.reason}`,
    title: stale
      ? `${c.reason} — recorded ${whenLabel}. If this deployment was paused by hand since, `
        + "that may not be why it is paused now."
      : `${c.reason} — recorded ${whenLabel}.`,
  };
}

/**
 * True when the strategy's source file changed after this deployment pinned it.
 * This one matters operationally: a plain Resume gets auto-paused again on the
 * next bar, so the ONLY way out is to re-pin to the current code.
 */
export function isDriftPaused(dep) {
  return (
    String(dep?.status || "").toUpperCase() === "PAUSED" &&
    dep?.drift_reason === "strategy_source_drift"
  );
}

/** Human explanation of the drift, with the two SHAs, for a tooltip. */
export function driftTooltip(dep) {
  return (
    `The strategy file changed since this was deployed ` +
    `(pinned ${dep?.drift_pinned_sha || "?"} → current ${dep?.drift_current_sha || "?"}). ` +
    `Re-pin to the current code so it can run again — plain Resume would be ` +
    `auto-paused on the next bar.`
  );
}

/** Status pill descriptor shared by the listing surfaces. */
export function statusPillOf(dep) {
  const mode = String(dep?.mode || "").toLowerCase();
  const status = String(dep?.status || "").toUpperCase();
  if (mode === "live") return { label: "Live", cls: "bg-danger/10 text-danger border-danger/40" };
  if (status === "PAUSED") return { label: "Paused", cls: "bg-amber-500/10 text-warning border-amber-500/40" };
  if (status === "ACTIVE") return { label: "Paper · running", cls: "bg-success/10 text-success border-success/40" };
  return { label: status || "—", cls: "bg-bg-3 text-dim border-line" };
}
