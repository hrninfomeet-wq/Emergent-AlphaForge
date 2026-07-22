import { AlertTriangle } from "lucide-react";
import FeedHealthBanner from "@/components/live/FeedHealthBanner";
import { SLICE_LABEL } from "@/components/live/liveHelpers";

/**
 * The cockpit's alert rail — the safety banners relocated VERBATIM from the old
 * LiveDashboard (degraded-data, unguarded-positions, no-broker-backstop, auth
 * message, feed health). data-testids are preserved. Each banner renders only
 * when its condition fires, so a healthy cockpit shows an empty rail.
 */
export default function AlertRail({
  health, unguardedPositions = [], noBackstopPositions = [],
  feedHealth, activeCount, authMsg,
}) {
  return (
    <div className="space-y-3">
      {health?.degraded && (
        <div className="text-sm font-mono px-3 py-2.5 rounded-lg border-2 border-amber-500 bg-amber-500/15 text-warning flex items-start gap-2" data-testid="live-degraded-banner">
          <AlertTriangle className="w-4 h-4 shrink-0 mt-0.5" />
          <span>
            <span className="font-bold">Broker data may be STALE</span> — the last poll failed for{" "}
            {(health.errorSlices || []).map((s) => SLICE_LABEL[s] || s).join(", ")}. The values below are the
            LAST-KNOWN reading, not live. The kill switch still works; reconnect / reload if this persists.
          </span>
        </div>
      )}

      {authMsg && (
        <div className={`text-sm font-mono px-3 py-2 rounded border ${authMsg.ok ? "border-emerald-500/40 bg-emerald-500/10 text-emerald-300" : "border-danger/40 bg-danger/10 text-danger"}`}>
          {authMsg.text}
        </div>
      )}

      {unguardedPositions.length > 0 && (
        <div className="text-sm font-mono px-3 py-2.5 rounded-lg border-2 border-danger bg-danger/15 text-danger flex items-start gap-2" data-testid="unguarded-positions-banner">
          <AlertTriangle className="w-4 h-4 shrink-0 mt-0.5" />
          <span>
            <span className="font-bold">{unguardedPositions.length} broker position{unguardedPositions.length !== 1 ? "s" : ""} NOT under the software guard</span>{" "}
            — no software stop / target / 15:00 square is watching{" "}
            {unguardedPositions.map((p) => p.tsym ?? p.tradingsymbol).filter(Boolean).slice(0, 4).join(", ")}
            {unguardedPositions.length > 4 ? "…" : ""}. The guard re-attaches open positions on startup; if this persists, square manually or check the broker connection.
          </span>
        </div>
      )}

      {noBackstopPositions.length > 0 && (
        <div className="text-sm font-mono px-3 py-2.5 rounded-lg border-2 border-amber-500 bg-amber-500/15 text-warning flex items-start gap-2" data-testid="no-broker-backstop-banner">
          <AlertTriangle className="w-4 h-4 shrink-0 mt-0.5" />
          <span>
            <span className="font-bold">{noBackstopPositions.length} live position{noBackstopPositions.length !== 1 ? "s" : ""} have no broker backstop (software-guard-only)</span>{" "}
            — the resting broker OCO failed to place for{" "}
            {noBackstopPositions.map((p) => p.tsym).filter(Boolean).slice(0, 4).join(", ")}
            {noBackstopPositions.length > 4 ? "…" : ""}. The software guard protects
            {noBackstopPositions.length !== 1 ? " these" : " this"} while the app is running, but there is NO PC-down net. Square manually or re-place the OCO if the app may go offline.
          </span>
        </div>
      )}

      <FeedHealthBanner feedHealth={feedHealth} activeCount={activeCount} />
    </div>
  );
}
