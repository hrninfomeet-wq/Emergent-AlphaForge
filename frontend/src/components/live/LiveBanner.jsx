import { useState } from "react";
import { AlertTriangle, CheckCircle, Loader2, Zap } from "lucide-react";
import { api } from "@/lib/api";

/**
 * Bold top-of-page banner for the Live Trading page (L0).
 * Shows: LIVE / read-only notice / Flattrade connection chip / connect button.
 */
export default function LiveBanner({ status, onRefresh }) {
  const [connecting, setConnecting] = useState(false);

  const connected = status?.connected;
  const expired = status?.expired || status?.regenerate_after_6am;
  const uid = status?.uid;
  const hasStaticIp = status?.static_ip_primary;

  const handleConnect = async () => {
    if (connecting) return;
    setConnecting(true);
    try {
      const res = await api.flattradeAuthStart();
      const url = res?.login_url;
      if (url) window.open(url, "_blank", "noopener,noreferrer");
    } catch {
      /* ignore — user sees the chip state */
    } finally {
      setConnecting(false);
    }
  };

  return (
    <div
      className="rounded-lg border-2 border-danger bg-danger/10 px-4 py-3 space-y-2"
      data-testid="live-banner"
    >
      {/* Title row */}
      <div className="flex items-center gap-3 flex-wrap">
        <Zap className="w-5 h-5 text-danger shrink-0" />
        <span className="text-base font-bold tracking-widest uppercase text-danger">
          Live Trading &middot; Real Money &middot; Flattrade
        </span>
        <span className="ml-auto text-xs font-mono px-2 py-1 rounded border border-amber-500/40 bg-amber-500/10 text-amber-300">
          Read-only &mdash; order execution not enabled yet (L0)
        </span>
      </div>

      {/* Connection status row */}
      <div className="flex items-center gap-3 flex-wrap text-sm">
        {status === null ? (
          /* not loaded yet */
          <span className="text-dimmer font-mono text-xs">Loading broker status&hellip;</span>
        ) : connected && !expired ? (
          <span className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full border border-emerald-500/40 bg-emerald-500/10 text-emerald-300 text-xs font-mono">
            <span className="w-2 h-2 rounded-full bg-emerald-400 shrink-0" />
            Connected
            {uid && <>&nbsp;&middot;&nbsp;{uid}</>}
            {hasStaticIp && <>&nbsp;&middot;&nbsp;static IP &#10003;</>}
          </span>
        ) : (
          <>
            <span className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full border border-amber-500/40 bg-amber-500/10 text-amber-300 text-xs font-mono">
              <AlertTriangle className="w-3 h-3" />
              {expired ? "Daily token expired — re-login needed" : "Not connected — token missing or expired"}
            </span>
            <button
              type="button"
              onClick={handleConnect}
              disabled={connecting}
              className="inline-flex items-center gap-1 px-3 py-1 rounded-md border border-info/40 bg-info/10 text-info text-xs font-mono hover:bg-info/20 disabled:opacity-60 transition-colors"
              data-testid="live-connect-btn"
            >
              {connecting ? <Loader2 className="w-3 h-3 animate-spin" /> : <CheckCircle className="w-3 h-3" />}
              {connecting ? "Opening…" : "Connect / Re-login"}
            </button>
          </>
        )}

        {/* Regen hint */}
        {status?.regenerate_after_6am && (
          <span className="text-amber-400 text-xs font-mono">
            &#9888; Regenerate after 6 AM IST
          </span>
        )}
      </div>
    </div>
  );
}
