import { useEffect, useState } from "react";
import { CheckCircle2, Loader2, ShieldOff, Square, XCircle } from "lucide-react";
import { api } from "@/lib/api";
import { fmtINR } from "@/lib/fmt";
import { useLiveData } from "@/components/live/LiveDataProvider";

/**
 * PositionMonitor — polls getLiveTestSession every 3s.
 *
 * Status-aware rendering:
 *   active (armed/filled/open)  → amber guarded-position card with a Square button
 *   rejected / canceled         → compact RED card + reject_reason + Dismiss
 *   squared / kill_switch       → compact GREEN card + Dismiss
 *   none                        → nothing rendered
 *
 * The 10-minute auto-square countdown was removed (see docs/superpowers/specs/
 * 2026-07-09-remove-manual-livetest-10min-timer-design.md); the position is
 * protected by the software guard's stop + the 15:00 IST EOD square, and can be
 * closed any time with Square (or the account-wide Kill switch).
 */

const HEARTBEAT_STALE_MS = 10_000; // amber if heartbeat older than this

/** Statuses that represent a live position that still needs watching. */
const ACTIVE_STATUSES = ["armed", "filled", "open"];
/** Statuses that represent a position that was rejected/canceled before ever filling. */
const REJECT_STATUSES = ["rejected", "canceled"];
/** Statuses that represent a position that was squared/killed. */
const CLOSED_STATUSES = ["squared", "kill_switch"];

function HeartbeatDot({ heartbeat }) {
  const isRecent =
    heartbeat != null &&
    Date.now() - new Date(heartbeat).getTime() < HEARTBEAT_STALE_MS;

  return (
    <span
      className={`inline-flex items-center gap-1 text-[10px] font-mono ${
        isRecent ? "text-emerald-300" : "text-warning"
      }`}
      title={heartbeat ? `Last heartbeat: ${heartbeat}` : "No heartbeat"}
    >
      <span
        className={`w-2 h-2 rounded-full shrink-0 ${
          isRecent ? "bg-emerald-400 animate-pulse" : "bg-amber-400"
        }`}
      />
      {isRecent ? "backend alive" : "heartbeat stale"}
    </span>
  );
}

export default function PositionMonitor() {
  // Session comes from the shared LiveDataProvider (single 3s poll); imperative
  // refetch after a Square/Kill action via refetch.session().
  const { session, refetch } = useLiveData();
  const [squareBusy, setSquareBusy] = useState(false);
  const [actionMsg, setActionMsg] = useState(null);
  const [dismissed, setDismissed] = useState(false);

  // Re-show a previously-dismissed reject/closed card when a NEW active session
  // appears (preserves the old per-poll setDismissed(false) side-effect).
  useEffect(() => {
    if (session && ACTIVE_STATUSES.includes(session.status)) setDismissed(false);
  }, [session]);

  if (session == null) return null;

  const { status } = session;

  // --- Rejected/canceled card ---
  if (REJECT_STATUSES.includes(status)) {
    if (dismissed) return null;
    return (
      <div className="rounded-lg border-2 border-danger/50 bg-danger/5 overflow-hidden">
        <div className="px-4 py-2.5 border-b border-danger/30 bg-danger/10 flex items-center gap-2 flex-wrap">
          <XCircle className="w-4 h-4 text-danger shrink-0" />
          <span className="text-sm font-bold text-danger uppercase tracking-wider">
            Order Rejected
          </span>
          <button
            type="button"
            onClick={() => setDismissed(true)}
            className="ml-auto text-xs font-mono text-dimmer hover:text-foreground transition-colors"
          >
            Dismiss
          </button>
        </div>
        <div className="px-4 py-3 text-xs font-mono text-dimmer space-y-1">
          {session.position && (
            <div>
              <span className="text-dimmer">Order: </span>
              <span className="text-foreground">{session.position}</span>
            </div>
          )}
          {session.reject_reason && (
            <div>
              <span className="text-dimmer">Reason: </span>
              <span className="text-danger">{session.reject_reason}</span>
            </div>
          )}
          <div className="text-[10px] text-dimmer pt-1">
            The order was rejected by the broker. No position was opened.
          </div>
        </div>
      </div>
    );
  }

  // --- Squared/kill_switch card ---
  if (CLOSED_STATUSES.includes(status)) {
    if (dismissed) return null;
    const label = status === "kill_switch" ? "Kill switch executed" : "Position squared / closed";
    return (
      <div className="rounded-lg border-2 border-emerald-500/50 bg-emerald-500/5 overflow-hidden">
        <div className="px-4 py-2.5 border-b border-emerald-500/30 bg-emerald-500/10 flex items-center gap-2 flex-wrap">
          <CheckCircle2 className="w-4 h-4 text-emerald-400 shrink-0" />
          <span className="text-sm font-bold text-emerald-300 uppercase tracking-wider">
            {label}
          </span>
          <button
            type="button"
            onClick={() => setDismissed(true)}
            className="ml-auto text-xs font-mono text-dimmer hover:text-foreground transition-colors"
          >
            Dismiss
          </button>
        </div>
        {session.position && (
          <div className="px-4 py-3 text-xs font-mono text-dimmer">
            <span className="text-dimmer">Order: </span>
            <span className="text-foreground">{session.position}</span>
          </div>
        )}
      </div>
    );
  }

  // --- Active countdown card (armed/filled/open) ---
  const hasActivePosition =
    ACTIVE_STATUSES.includes(status) && session.position != null;

  if (!hasActivePosition) return null;

  const pos = session.position;
  const heartbeat = session.heartbeat ?? null;

  const handleSquare = async () => {
    if (squareBusy) return;
    setSquareBusy(true);
    setActionMsg(null);
    try {
      const res = await api.squareLivePosition();
      setActionMsg({ ok: true, text: res?.message ?? "Square-off sent." });
      refetch.session();
    } catch (e) {
      setActionMsg({ ok: false, text: e?.response?.data?.detail ?? "Square-off failed." });
    } finally {
      setSquareBusy(false);
    }
  };

  // Kill switch moved to KillSwitchPanel (always visible whenever open
  // positions/working orders exist — not only during a test session — with a
  // typed confirm + per-leg outcome report).

  return (
    <div className="rounded-lg border-2 border-amber-500/50 bg-amber-500/5 overflow-hidden">
      {/* Header */}
      <div className="px-4 py-2.5 border-b border-amber-500/30 bg-amber-500/10 flex items-center gap-2 flex-wrap">
        <span className="text-sm font-bold text-warning uppercase tracking-wider">
          Live Position Active
        </span>
        <HeartbeatDot heartbeat={heartbeat} />
      </div>

      <div className="px-4 py-3 space-y-4">
        {/* Position summary */}
        <div className="text-xs font-mono space-y-1">
          {typeof pos === "string" ? (
            <div className="flex items-center gap-2">
              <span className="text-dimmer uppercase tracking-wider text-[10px]">Order</span>
              <span className="font-semibold text-foreground">{pos}</span>
            </div>
          ) : pos && typeof pos === "object" ? (
            <>
              {pos.symbol && (
                <div className="flex items-center gap-2">
                  <span className="text-dimmer uppercase tracking-wider text-[10px]">Symbol</span>
                  <span className="font-semibold text-foreground">{pos.symbol}</span>
                </div>
              )}
              <div className="flex flex-wrap gap-4">
                {pos.qty != null && (
                  <div>
                    <span className="text-dimmer">Qty: </span>
                    <span className="text-foreground">{pos.qty}</span>
                  </div>
                )}
                {pos.avg_price != null && (
                  <div>
                    <span className="text-dimmer">Avg: </span>
                    <span className="text-foreground">{fmtINR(parseFloat(pos.avg_price))}</span>
                  </div>
                )}
                {pos.ltp != null && (
                  <div>
                    <span className="text-dimmer">LTP: </span>
                    <span className="text-foreground">{fmtINR(parseFloat(pos.ltp))}</span>
                  </div>
                )}
                {pos.pnl != null && (
                  <div>
                    <span className="text-dimmer">P&amp;L: </span>
                    <span
                      className={
                        parseFloat(pos.pnl) >= 0 ? "text-success font-semibold" : "text-danger font-semibold"
                      }
                    >
                      {parseFloat(pos.pnl) >= 0 ? "+" : ""}
                      {fmtINR(parseFloat(pos.pnl))}
                    </span>
                  </div>
                )}
                {pos.sl_norenordno && (
                  <div>
                    <span className="text-dimmer">SL order: </span>
                    <span className="text-foreground">{pos.sl_norenordno}</span>
                  </div>
                )}
              </div>
            </>
          ) : null}
        </div>

        {/* Protection note — no timer; the software guard + EOD square protect it */}
        <div className="text-[11px] font-mono text-dimmer flex items-start gap-1.5">
          <ShieldOff className="w-3.5 h-3.5 text-warning shrink-0 mt-px" />
          <span>
            Guarded by the software stop &amp; the 15:00 IST end-of-day square — no
            auto-timer. Close it any time with Square (or the Kill switch).
          </span>
        </div>

        {/* Action buttons — the account-wide Kill switch lives in KillSwitchPanel */}
        <div className="flex items-center gap-2 flex-wrap">
          <button
            type="button"
            disabled={squareBusy}
            onClick={handleSquare}
            className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-md border border-amber-500/50 bg-amber-500/10 text-warning text-xs font-mono font-semibold hover:bg-amber-500/20 disabled:opacity-60 transition-colors"
          >
            {squareBusy ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Square className="w-3.5 h-3.5" />}
            Square now
          </button>
        </div>

        {/* Action result message */}
        {actionMsg && (
          <div
            className={`text-xs font-mono px-2 py-1 rounded border ${
              actionMsg.ok
                ? "border-emerald-500/30 bg-emerald-500/10 text-emerald-300"
                : "border-danger/30 bg-danger/10 text-danger"
            }`}
          >
            {actionMsg.text}
          </div>
        )}
      </div>
    </div>
  );
}
