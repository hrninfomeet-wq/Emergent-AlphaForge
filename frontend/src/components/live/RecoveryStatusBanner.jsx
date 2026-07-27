import { useCallback, useEffect, useState } from "react";
import { AlertTriangle, Loader2 } from "lucide-react";
import { api } from "@/lib/api";

/**
 * Overnight-position recovery visibility.
 *
 * After a restart the backend re-runs `live_startup_recovery` for the CURRENT
 * broker token: resume pending exits, rehydrate the software guard, reconcile the
 * reboot, and re-arm auto-square. `succeeded` only flips true once ALL of that
 * completes. Until then an open live position may be sitting there with no guard
 * and no resting OCO backstop.
 *
 * `GET /live-broker/recovery-status` was built specifically to drive this strip —
 * its own docstring says so — and then had no caller for its whole life, so the
 * operator had no way to know recovery had not finished. Boot-before-OAuth is the
 * common trigger: the backend starts before the daily broker login exists, so
 * recovery cannot run until the token appears.
 *
 * Severity follows exposure: unrecovered WITH open positions is the dangerous
 * case; unrecovered with a flat book is informational.
 */
const POLL_MS = 20000;

export default function RecoveryStatusBanner({ connected, openPositions = 0 }) {
  const [state, setState] = useState(null);

  const load = useCallback(async () => {
    try {
      setState(await api.getRecoveryStatus());
    } catch {
      // Keep the last-known value. Never fall back to a clean-looking state we
      // cannot vouch for — that would hide an unrecovered position.
    }
  }, []);

  useEffect(() => {
    if (!connected) return undefined;
    load();
    const id = window.setInterval(load, POLL_MS);
    return () => window.clearInterval(id);
  }, [connected, load]);

  // Nothing to say when disconnected (the broker banner already covers that) or
  // once recovery has genuinely completed for this token.
  if (!connected || state?.succeeded === true) return null;
  // Before the first poll returns we know nothing — say nothing.
  if (!state) return null;

  const exposed = Number(openPositions) > 0;
  const tone = exposed
    ? "border-danger/50 bg-danger/10 text-danger"
    : "border-warning/40 bg-warning/10 text-warning";

  return (
    <div className={`rounded-lg border px-3 py-2 ${tone}`} data-testid="recovery-status-banner" role="status">
      <div className="flex items-start gap-2 flex-wrap">
        {state?.last_result === "incomplete — will retry"
          ? <Loader2 className="w-4 h-4 shrink-0 mt-0.5 animate-spin" />
          : <AlertTriangle className="w-4 h-4 shrink-0 mt-0.5" />}
        <div className="min-w-0 flex-1">
          <div className="text-xs font-semibold uppercase tracking-wide">
            {exposed
              ? "Position recovery incomplete — open positions may be unguarded"
              : "Position recovery has not completed for this session"}
          </div>
          <div className="text-[11.5px] text-foreground mt-0.5">
            {exposed
              ? "The software guard, resting OCO backstop and auto-square re-arm may not be active for positions carried over. Verify exits manually before relying on them."
              : "The book is flat, so there is nothing at risk right now. Recovery re-runs automatically once a valid broker token is present."}
          </div>
          <div className="text-[10.5px] font-mono text-dim mt-0.5" data-testid="recovery-status-detail">
            {state?.last_result ? `status: ${state.last_result}` : "status: not attempted yet"}
            {state?.last_attempt_at
              ? ` · last attempt ${new Date(state.last_attempt_at).toLocaleString()}`
              : " · no attempt recorded"}
          </div>
        </div>
      </div>
    </div>
  );
}
