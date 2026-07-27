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

/** Why the deployment is paused, or null. Kill-switch wins over drift. */
export function pauseReasonOf(dep) {
  return dep?.kill_switch_reason || dep?.drift_reason || null;
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
