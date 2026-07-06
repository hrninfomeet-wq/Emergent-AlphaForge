import { useState } from "react";
import { AlertTriangle, Loader2, PlugZap, RefreshCw } from "lucide-react";
import { api } from "@/lib/api";

/**
 * FeedHealthBanner — surfaces when ACTIVE deployments exist but the live DATA feed
 * (Upstox stream -> candle roller -> fresh candles_1m) is NOT delivering, so the
 * trader is told immediately instead of waiting all day. Shown on Paper + Live pages.
 *
 * Renders only for feedHealth.state in {NEEDS_LOGIN, DEGRADED, WARMING_UP} with >=1
 * active deployment (LIVE / MARKET_CLOSED show nothing — the state already encodes hours).
 */
export default function FeedHealthBanner({ feedHealth, activeCount = 0 }) {
  const [busy, setBusy] = useState(false);
  const state = feedHealth?.state;
  if (!feedHealth || activeCount < 1) return null;
  if (state !== "NEEDS_LOGIN" && state !== "DEGRADED" && state !== "WARMING_UP") return null;

  const connect = async () => {
    if (busy) return;
    setBusy(true);
    try {
      const res = await api.upstoxAuthStart();
      const url = res?.login_url;
      if (url) { window.location.href = url; return; }
    } catch { /* fall through */ }
    setBusy(false);
  };
  const restart = async () => {
    if (busy) return;
    setBusy(true);
    try { await api.restartLiveFeed(); } catch { /* surfaced by next poll */ }
    setBusy(false);
  };

  const warming = state === "WARMING_UP";
  const tone = warming
    ? "border-amber-500/40 bg-amber-500/10 text-warning"
    : "border-2 border-danger bg-danger/10 text-danger";

  return (
    <div className={`rounded-lg px-4 py-3 flex items-center gap-3 flex-wrap ${tone}`} data-testid="feed-health-banner">
      {warming ? <Loader2 className="w-5 h-5 shrink-0 animate-spin" /> : <AlertTriangle className="w-5 h-5 shrink-0" />}
      <div className="min-w-0 flex-1">
        <div className="text-sm font-semibold">
          {state === "NEEDS_LOGIN" && `${activeCount} strateg${activeCount === 1 ? "y is" : "ies are"} active but the live data feed is offline`}
          {state === "DEGRADED" && "Active strategies, but no live candles are arriving"}
          {warming && "Live data feed is starting…"}
        </div>
        <div className="text-xs opacity-90">{feedHealth.reason || ""}{state === "NEEDS_LOGIN" ? " They will not trade until you connect." : ""}</div>
      </div>
      {state === "NEEDS_LOGIN" && feedHealth.cta === "connect_upstox" && (
        <button type="button" onClick={connect} disabled={busy}
          className="inline-flex items-center gap-1 px-3 py-1 rounded-md border border-info/40 bg-info/10 text-info text-xs font-mono hover:bg-info/20 disabled:opacity-60 transition-colors"
          data-testid="feed-connect-upstox">
          {busy ? <Loader2 className="w-3 h-3 animate-spin" /> : <PlugZap className="w-3 h-3" />}
          {busy ? "Opening…" : "Connect Upstox"}
        </button>
      )}
      {state === "DEGRADED" && (
        <button type="button" onClick={restart} disabled={busy}
          className="inline-flex items-center gap-1 px-3 py-1 rounded-md border border-danger/40 bg-danger/10 text-danger text-xs font-mono hover:bg-danger/20 disabled:opacity-60 transition-colors"
          data-testid="feed-restart">
          {busy ? <Loader2 className="w-3 h-3 animate-spin" /> : <RefreshCw className="w-3 h-3" />}
          Restart feed
        </button>
      )}
    </div>
  );
}
