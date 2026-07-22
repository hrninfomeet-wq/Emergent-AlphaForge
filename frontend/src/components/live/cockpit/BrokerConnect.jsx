import { useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import { toast } from "sonner";

/**
 * Compact Upstox + Flattrade connection module for the cockpit command bar.
 * Upstox = market-data feed, Flattrade = order execution. Each chip shows a
 * connection dot + a short state, and a click-out popover with Reconnect /
 * Disconnect (or "Login" when disconnected/expired). All actions route through
 * the EXISTING OAuth/disconnect endpoints — no new mutating routes, and never
 * the Flattrade MCP.
 */

function tokenHint(s) {
  // Defensive across status shapes: show a countdown-ish hint when present.
  if (!s) return "";
  const raw = s.token_valid_for || s.valid_for || s.expires_in_label || s.token_ttl || "";
  return typeof raw === "string" ? raw : "";
}

function BrokerChip({ name, purpose, status, onReconnect, onDisconnect }) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const ref = useRef(null);

  useEffect(() => {
    function onDoc(e) { if (ref.current && !ref.current.contains(e.target)) setOpen(false); }
    function onEsc(e) { if (e.key === "Escape") setOpen(false); }
    document.addEventListener("click", onDoc);
    document.addEventListener("keydown", onEsc);
    return () => { document.removeEventListener("click", onDoc); document.removeEventListener("keydown", onEsc); };
  }, []);

  const connected = !!status?.connected && !status?.expired;
  const expired = !!status?.expired;
  const dot = connected ? "bg-success" : expired ? "bg-warning" : "bg-danger";
  const stateLabel = connected ? "connected" : expired ? "token expired" : "disconnected";
  const hint = tokenHint(status);

  const doReconnect = async () => {
    setBusy(true);
    try { await onReconnect(); }
    catch (e) { toast.error(`${name} reconnect failed: ${e?.response?.data?.detail || e?.message || "error"}`); }
    finally { setBusy(false); }
  };
  const doDisconnect = async () => {
    setBusy(true);
    try { await onDisconnect(); setOpen(false); }
    catch (e) { toast.error(`${name} disconnect failed: ${e?.response?.data?.detail || e?.message || "error"}`); }
    finally { setBusy(false); }
  };

  return (
    <div className="relative" ref={ref}>
      <button
        type="button"
        onClick={(e) => { e.stopPropagation(); setOpen((o) => !o); }}
        className="inline-flex items-center gap-2 px-2.5 py-1.5 rounded-lg border border-line bg-bg-2 hover:border-dim text-xs font-semibold text-foreground"
        title={`${name} · ${stateLabel}`}
      >
        <span className={`w-1.5 h-1.5 rounded-full ${dot}`} />
        <span className="tracking-wide">{name}</span>
        <span className="font-mono text-[10px] text-dim">{purpose}{hint ? ` · ${hint}` : ""}</span>
        <span className="text-dimmer text-[9px]">▾</span>
      </button>
      {open && (
        <div className="absolute top-full right-0 mt-1.5 w-56 rounded-lg border border-line bg-bg-1 shadow-xl p-3 z-30">
          <div className="flex justify-between text-[11px] mb-1"><span className="text-dimmer">Purpose</span><span>{purpose === "data" ? "Market data feed" : "Order execution"}</span></div>
          <div className="flex justify-between text-[11px] mb-1"><span className="text-dimmer">State</span><span className={connected ? "text-success" : expired ? "text-warning" : "text-danger"}>{stateLabel}</span></div>
          {hint && <div className="flex justify-between text-[11px] mb-1"><span className="text-dimmer">Token</span><span className="font-mono">{hint}</span></div>}
          <div className="flex gap-1.5 mt-2.5">
            {connected ? (
              <>
                <button type="button" disabled={busy} onClick={doReconnect} className="flex-1 border border-line bg-bg-2 rounded-md px-2 py-1 text-[11px] hover:border-dim disabled:opacity-50">Reconnect</button>
                <button type="button" disabled={busy} onClick={doDisconnect} className="flex-1 border border-danger/50 bg-danger/10 text-danger rounded-md px-2 py-1 text-[11px] hover:bg-danger/20 disabled:opacity-50">Disconnect</button>
              </>
            ) : (
              <button type="button" disabled={busy} onClick={doReconnect} className="flex-1 border border-success/50 bg-success/10 text-success rounded-md px-2 py-1 text-[11px] font-semibold hover:bg-success/20 disabled:opacity-50">
                Login to {name}
              </button>
            )}
          </div>
          {name === "Flattrade" && (
            <div className="text-dimmer text-[9.5px] mt-2">Token clears ~06:00 IST daily; log in via AlphaForge (never the shared MCP).</div>
          )}
        </div>
      )}
    </div>
  );
}

// Redirect the browser to an OAuth authorize URL returned by an auth-start call.
async function redirectToAuth(startFn) {
  const data = await startFn();
  const url = data?.authorize_url || data?.url || data?.login_url || (typeof data === "string" ? data : null);
  if (url) window.location.href = url;
  else toast.error("Could not start login — no authorize URL returned.");
}

export default function BrokerConnect({ flattradeStatus, onChanged }) {
  const [upstox, setUpstox] = useState(null);

  useEffect(() => {
    let cancelled = false;
    const load = () => api.upstoxStatus().then((d) => { if (!cancelled) setUpstox(d); }).catch(() => {});
    load();
    const t = window.setInterval(load, 15000);
    return () => { cancelled = true; window.clearInterval(t); };
  }, []);

  return (
    <div className="flex gap-1.5">
      <BrokerChip
        name="Upstox" purpose="data" status={upstox}
        onReconnect={() => redirectToAuth(api.upstoxAuthStart)}
        onDisconnect={() => api.disconnectUpstox().then(() => onChanged?.())}
      />
      <BrokerChip
        name="Flattrade" purpose="exec" status={flattradeStatus}
        onReconnect={() => redirectToAuth(api.flattradeAuthStart)}
        onDisconnect={() => api.disconnectFlattrade().then(() => onChanged?.())}
      />
    </div>
  );
}
