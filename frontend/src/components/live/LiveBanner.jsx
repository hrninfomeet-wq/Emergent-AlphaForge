import { useState } from "react";
import { AlertTriangle, Loader2, LogIn, LogOut, Shield, Zap } from "lucide-react";
import { api } from "@/lib/api";

/**
 * Bold top-of-page banner for the Live Trading page (L0).
 * Shows: LIVE / read-only notice / Flattrade connection chip / Login + Logout.
 * Also reflects the LIVE-mode deployment count and the LIVE_AUTOPLACE_ARMED master
 * switch. "Live" here means mode=="live" — the per-deployment arm ceremony is gone,
 * so a live deployment stays live across sessions until explicitly disabled.
 */
export default function LiveBanner({ status, onRefresh, armedCount = 0, autoplaceArmed = null }) {
  const [busy, setBusy] = useState(false);

  const connected = status?.connected;
  const expired = status?.expired || status?.regenerate_after_6am;
  const uid = status?.uid;
  const hasStaticIp = status?.static_ip_primary;

  // Login: full-page (same-tab) OAuth redirect to Flattrade. After login the
  // broker bounces to our /auth/callback which saves the token and redirects
  // back to /live-trading?flattrade_connected=1 — so the user lands back here.
  const handleLogin = async () => {
    if (busy) return;
    setBusy(true);
    try {
      const res = await api.flattradeAuthStart();
      const url = res?.login_url;
      if (url) {
        window.location.href = url;
        return; // navigating away
      }
    } catch {
      /* fall through */
    }
    setBusy(false);
  };

  const handleLogout = async () => {
    if (busy) return;
    setBusy(true);
    try {
      await api.disconnectFlattrade();
      onRefresh?.();
    } catch {
      /* ignore — user sees the chip state */
    } finally {
      setBusy(false);
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
        {armedCount > 0 && (
          <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full border border-danger bg-danger/20 text-danger text-xs font-mono font-bold animate-pulse">
            <Zap className="w-3 h-3" />
            {armedCount} deployment{armedCount !== 1 ? "s" : ""} LIVE
          </span>
        )}
        {armedCount > 0 && autoplaceArmed === false && (
          <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full border border-amber-500/60 bg-amber-500/10 text-warning text-xs font-mono">
            <Shield className="w-3 h-3" />
            DRY-RUN — set LIVE_AUTOPLACE_ARMED=1 for real orders
          </span>
        )}
        {/* The real execution-state verdict lives in <ExecutionStateStrip/> below,
            data-bound to /live-broker/arm-state — replacing the old hardcoded chip. */}
      </div>

      {/* Connection status row */}
      <div className="flex items-center gap-3 flex-wrap text-sm">
        {status === null ? (
          /* not loaded yet */
          <span className="text-dimmer font-mono text-xs">Loading broker status&hellip;</span>
        ) : connected && !expired ? (
          <>
            <span className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full border border-emerald-500/40 bg-emerald-500/10 text-emerald-300 text-xs font-mono">
              <span className="w-2 h-2 rounded-full bg-emerald-400 shrink-0" />
              Connected
              {uid && <>&nbsp;&middot;&nbsp;{uid}</>}
              {hasStaticIp && <>&nbsp;&middot;&nbsp;static IP &#10003;</>}
            </span>
            <button
              type="button"
              onClick={handleLogout}
              disabled={busy}
              className="inline-flex items-center gap-1 px-3 py-1 rounded-md border border-danger/40 bg-danger/10 text-danger text-xs font-mono hover:bg-danger/20 disabled:opacity-60 transition-colors"
              data-testid="live-logout-btn"
            >
              {busy ? <Loader2 className="w-3 h-3 animate-spin" /> : <LogOut className="w-3 h-3" />}
              Logout
            </button>
          </>
        ) : (
          <>
            <span className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full border border-amber-500/40 bg-amber-500/10 text-warning text-xs font-mono">
              <AlertTriangle className="w-3 h-3" />
              {expired ? "Daily token expired — login needed" : "Not connected — token missing or expired"}
            </span>
            <button
              type="button"
              onClick={handleLogin}
              disabled={busy}
              className="inline-flex items-center gap-1 px-3 py-1 rounded-md border border-info/40 bg-info/10 text-info text-xs font-mono hover:bg-info/20 disabled:opacity-60 transition-colors"
              data-testid="live-login-btn"
            >
              {busy ? <Loader2 className="w-3 h-3 animate-spin" /> : <LogIn className="w-3 h-3" />}
              {busy ? "Opening…" : "Login to Flattrade"}
            </button>
          </>
        )}

        {/* Regen hint */}
        {status?.regenerate_after_6am && (
          <span className="text-warning text-xs font-mono">
            &#9888; Regenerate after 6 AM IST
          </span>
        )}
      </div>
    </div>
  );
}
