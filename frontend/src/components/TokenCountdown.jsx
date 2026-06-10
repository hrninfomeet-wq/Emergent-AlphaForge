import { useEffect, useState } from "react";
import { Clock, AlertTriangle, Loader2 } from "lucide-react";
import { api } from "@/lib/api";

/**
 * Shared Upstox token countdown.
 *
 * Two visual variants of the same logic (previously duplicated in Layout's
 * TokenExpiryIndicator and DataWarehouse's TokenExpiryBadge):
 *   - variant="button" (top bar): clickable to start OAuth when expired/disconnected.
 *   - variant="badge"  (warehouse panel): read-only status pill.
 *
 * Pass `status` to render from a parent-owned status object, or omit it and the
 * component will poll /upstox/status itself (used by the top bar).
 */

function computeState(status, now) {
  if (!status) return null;
  const expMs = status.expires_at ? Date.parse(status.expires_at) : NaN;
  const mins = Number.isNaN(expMs) ? -1 : Math.floor((expMs - now) / 60000);
  const expired = !status.connected || status.expired || mins <= 0;
  const hrs = Math.floor(Math.max(0, mins) / 60);
  const remMins = Math.max(0, mins) % 60;
  const label = hrs > 0 ? `${hrs}h ${remMins}m` : `${remMins}m`;
  return { mins, expired, label, connected: !!status.connected, expiresAt: status.expires_at };
}

export default function TokenCountdown({ status: externalStatus, variant = "badge", className = "" }) {
  const [polledStatus, setPolledStatus] = useState(null);
  const [now, setNow] = useState(Date.now());
  const [connecting, setConnecting] = useState(false);
  const selfPoll = externalStatus === undefined;

  useEffect(() => {
    let alive = true;
    let poll;
    if (selfPoll) {
      const load = () => api.upstoxStatus().then((s) => { if (alive) setPolledStatus(s); }).catch(() => {});
      load();
      poll = setInterval(load, 60000);
    }
    const tick = setInterval(() => setNow(Date.now()), 30000);
    return () => { alive = false; if (poll) clearInterval(poll); clearInterval(tick); };
  }, [selfPoll]);

  const status = selfPoll ? polledStatus : externalStatus;
  const st = computeState(status, now);
  if (!st) return null;

  const connectUpstox = async () => {
    if (connecting) return;
    setConnecting(true);
    try {
      const res = await api.startUpstoxAuth();
      if (!res?.login_url) throw new Error("no login url");
      window.location.href = res.login_url;
    } catch {
      setConnecting(false);
    }
  };

  // Color escalation shared by both variants.
  const cls = st.expired || st.mins < 30
    ? "border-rose-900 bg-rose-950/40 text-rose-200"
    : st.mins < 120
      ? "border-amber-900 bg-amber-950/40 text-amber-200"
      : "border-line bg-bg-2 text-dim";
  const Icon = st.expired || st.mins < 30 ? AlertTriangle : Clock;

  // Button variant (top bar): clickable to re-auth when needed.
  if (variant === "button" && st.expired) {
    return (
      <button
        type="button"
        onClick={connectUpstox}
        disabled={connecting}
        className={`flex items-center gap-1 px-2 py-1 rounded-md border border-rose-900 bg-rose-950/40 text-[11px] font-mono text-rose-200 hover:bg-rose-900/50 transition-colors disabled:opacity-60 ${className}`}
        data-testid="topbar-token-indicator"
        title={st.connected ? "Upstox token expired. Click to re-authorize." : "Upstox not connected. Click to authorize."}
      >
        {connecting ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <AlertTriangle className="w-3.5 h-3.5" />}
        {connecting ? "Connecting…" : st.connected ? "Reconnect Upstox" : "Connect Upstox"}
      </button>
    );
  }

  // Badge variant only renders when connected with a known expiry.
  if (variant === "badge" && (!st.connected || !st.expiresAt)) return null;

  const text = st.expired ? "token expired" : variant === "button" ? `Token ${st.label}` : `${st.label} left`;
  return (
    <span
      className={`flex items-center gap-1 px-2 py-1 rounded-md border text-[11px] font-mono ${cls} ${className}`}
      data-testid={variant === "button" ? "topbar-token-indicator" : "upstox-token-expiry-badge"}
      title={`Upstox token ${st.expired ? "expired" : "expires"} at ${st.expiresAt}`}
    >
      <Icon className="w-3.5 h-3.5" /> {text}
    </span>
  );
}
