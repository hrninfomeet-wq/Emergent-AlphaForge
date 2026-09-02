/**
 * The two independent stops behind the backend's `can_trade()`.
 *
 * `LiveEngine.can_trade()` refuses a new live entry if EITHER is set:
 *   - `engine_halted`        — the engine halt (kill switch, reconcile mismatch, …)
 *   - `blocked_until_reset`  — the broker-stop-loss latch
 *
 * The UI used to render only the second. On 2026-09-02 that meant a kill-switch
 * halt sat completely invisible: the banner was hidden, `/arm-state` read SAFE,
 * and every attempt to enable a live deployment failed with "clear the
 * halt/latch" pointing at a latch the operator had already reset.
 *
 * Both are cleared by the same operator action (`POST
 * /live-broker/safety-config/reset-latch`), so they are presented as ONE state.
 *
 * Kept in plain .js — not inside the .jsx component — so it can be unit-tested
 * directly under node instead of asserted by grepping JSX.
 */

/** @returns {{stopped: boolean, latched?: boolean, halted?: boolean,
 *             reason?: string, at?: string|null, title?: string}} */
export function readStopState(cfg) {
  // Strict === true: a missing/unknown field must never read as "stopped=false"
  // by accident, but it must also never invent a stop the backend did not report.
  const latched = cfg?.blocked_until_reset === true;
  const halted = cfg?.engine_halted === true;
  if (!latched && !halted) return { stopped: false };

  // Prefer the halt's provenance. A kill switch sets BOTH gates, and the halt
  // carries the specific cause ("kill_switch") while the latch carries a generic
  // one — showing the latch's reason there would under-describe the incident.
  const reason = String(
    (halted ? cfg?.engine_halt_reason : null) || cfg?.latched_reason || "unspecified",
  );
  const at = (halted ? cfg?.engine_halted_at : null) || cfg?.latched_at || null;

  return {
    stopped: true,
    latched,
    halted,
    reason,
    at,
    title:
      halted && latched
        ? "Live trading stopped — kill switch used"
        : halted
          ? "Live trading stopped — engine halted"
          : "Live trading halted — safety latch tripped",
  };
}
