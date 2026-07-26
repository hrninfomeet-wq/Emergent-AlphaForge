import { useCallback, useEffect, useRef, useState } from "react";
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

function BrokerChip({ name, purpose, status, onReconnect, onDisconnect, openPositions = 0, open, onToggle, onClose }) {
  const [busy, setBusy] = useState(false);
  const [confirmDisconnect, setConfirmDisconnect] = useState(false);
  const ref = useRef(null);

  // `open` is owned by the parent so only ONE chip can be open at a time. The
  // old per-chip state + stopPropagation meant the other chip's outside-click
  // handler never fired, leaving both popovers open and overlapping.
  useEffect(() => {
    function onDoc(e) { if (ref.current && !ref.current.contains(e.target)) onClose(); }
    function onEsc(e) { if (e.key === "Escape") onClose(); }
    document.addEventListener("click", onDoc);
    document.addEventListener("keydown", onEsc);
    return () => { document.removeEventListener("click", onDoc); document.removeEventListener("keydown", onEsc); };
  }, [onClose]);

  // Reset the disconnect confirmation whenever the popover closes.
  useEffect(() => { if (!open) setConfirmDisconnect(false); }, [open]);

  const connected = !!status?.connected && !status?.expired;
  const expired = !!status?.expired;
  const dot = connected ? "bg-success" : expired ? "bg-warning" : "bg-danger";
  const stateLabel = connected ? "connected" : expired ? "token expired" : "disconnected";
  // Colour alone must not carry the state (colour-blind users, and the dot is 6px).
  const stateGlyph = connected ? "✓" : expired ? "!" : "×";
  const hint = tokenHint(status);

  const doReconnect = async () => {
    setBusy(true);
    try { await onReconnect(); }
    catch (e) { toast.error(`${name} reconnect failed: ${e?.response?.data?.detail || e?.message || "error"}`); }
    finally { setBusy(false); }
  };
  const doDisconnect = async () => {
    setBusy(true);
    try { await onDisconnect(); onClose(); }
    catch (e) { toast.error(`${name} disconnect failed: ${e?.response?.data?.detail || e?.message || "error"}`); }
    finally { setBusy(false); }
  };

  return (
    <div className="relative" ref={ref}>
      <button
        type="button"
        onClick={(e) => { e.stopPropagation(); onToggle(); }}
        className="inline-flex items-center gap-2 px-2.5 py-1.5 rounded-lg border border-line bg-bg-2 hover:border-dim text-xs font-semibold text-foreground"
        title={`${name} · ${stateLabel}`}
        aria-expanded={open}
        aria-haspopup="dialog"
      >
        <span className={`w-1.5 h-1.5 rounded-full ${dot}`} aria-hidden="true" />
        <span className="tracking-wide">{name}</span>
        <span className="font-mono text-[10px] text-dim">
          {purpose}{hint ? ` · ${hint}` : ""}
          <span className={connected ? "text-success" : expired ? "text-warning" : "text-danger"}> {stateGlyph}</span>
        </span>
        <span className="sr-only">{stateLabel}</span>
        <span className="text-dimmer text-[9px]" aria-hidden="true">▾</span>
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
                {/* Disconnect is destructive: for Flattrade it severs the session
                    the software guard, auto-exits and kill switch all depend on.
                    Two-step, and it names the exposure it would strand. */}
                {!confirmDisconnect ? (
                  <button
                    type="button" disabled={busy}
                    onClick={() => setConfirmDisconnect(true)}
                    className="flex-1 border border-line text-dim rounded-md px-2 py-1 text-[11px] hover:border-danger/50 hover:text-danger disabled:opacity-50"
                  >
                    Disconnect
                  </button>
                ) : (
                  <button
                    type="button" disabled={busy}
                    onClick={doDisconnect}
                    className="flex-1 border border-danger bg-danger/15 text-danger font-semibold rounded-md px-2 py-1 text-[11px] hover:bg-danger/25 disabled:opacity-50"
                  >
                    {busy ? "Disconnecting…" : "Confirm disconnect"}
                  </button>
                )}
              </>
            ) : (
              <button type="button" disabled={busy} onClick={doReconnect} className="flex-1 border border-success/50 bg-success/10 text-success rounded-md px-2 py-1 text-[11px] font-semibold hover:bg-success/20 disabled:opacity-50">
                Login to {name}
              </button>
            )}
          </div>
          {confirmDisconnect && (
            <div className="text-danger text-[10px] mt-2 leading-snug">
              {name === "Flattrade"
                ? `Ends the execution session${openPositions > 0 ? ` with ${openPositions} position${openPositions !== 1 ? "s" : ""} OPEN` : ""} — auto-exits and the kill switch stop working until you log in again.`
                : "Stops the market-data feed; live premiums and the option chain go stale."}
              {" "}
              <button type="button" onClick={() => setConfirmDisconnect(false)} className="underline hover:text-foreground">Cancel</button>
            </div>
          )}
          {name === "Flattrade" && !confirmDisconnect && (
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

export default function BrokerConnect({ flattradeStatus, onChanged, openPositions = 0 }) {
  const [upstox, setUpstox] = useState(null);
  const [openChip, setOpenChip] = useState(null);   // "upstox" | "flattrade" | null
  const closeChips = useCallback(() => setOpenChip(null), []);

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
        open={openChip === "upstox"}
        onToggle={() => setOpenChip((c) => (c === "upstox" ? null : "upstox"))}
        onClose={closeChips}
        onReconnect={() => redirectToAuth(api.upstoxAuthStart)}
        onDisconnect={() => api.disconnectUpstox().then(() => onChanged?.())}
      />
      <BrokerChip
        name="Flattrade" purpose="exec" status={flattradeStatus}
        openPositions={openPositions}
        open={openChip === "flattrade"}
        onToggle={() => setOpenChip((c) => (c === "flattrade" ? null : "flattrade"))}
        onClose={closeChips}
        onReconnect={() => redirectToAuth(api.flattradeAuthStart)}
        onDisconnect={() => api.disconnectFlattrade().then(() => onChanged?.())}
      />
    </div>
  );
}
