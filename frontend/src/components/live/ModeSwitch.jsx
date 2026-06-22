import { useState } from "react";
import { AlertTriangle, CheckCircle, Loader2, ShieldAlert, Zap } from "lucide-react";
import { api } from "@/lib/api";

/**
 * ModeSwitch — shows the current live-broker mode; lets the user enter
 * LIVE_TEST (with explicit confirm checkbox) or revert to LIVE_OFFLINE/PAPER.
 *
 * LIVE_TEST implies a real Flattrade order may be sent.
 */

const MODE_LABELS = {
  PAPER: "Paper (no broker)",
  LIVE_OFFLINE: "Live Offline (read-only)",
  LIVE_TEST: "Live Test — REAL MONEY",
};

const MODE_BADGE_CLASS = {
  PAPER: "border-emerald-500/40 bg-emerald-500/10 text-emerald-300",
  LIVE_OFFLINE: "border-blue-500/40 bg-blue-500/10 text-blue-300",
  LIVE_TEST: "border-danger/60 bg-danger/15 text-danger",
};

export default function ModeSwitch({ mode, onModeChange }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  // "Enter LIVE_TEST" flow
  const [showEnterConfirm, setShowEnterConfirm] = useState(false);
  const [confirmed, setConfirmed] = useState(false);

  // "Leave LIVE_TEST" target
  const [leaveTarget, setLeaveTarget] = useState("LIVE_OFFLINE");

  const handleEnterLiveTest = async () => {
    if (!confirmed) return;
    setBusy(true);
    setError(null);
    try {
      const res = await api.setLiveMode("LIVE_TEST", true);
      onModeChange(res.mode ?? "LIVE_TEST");
      setShowEnterConfirm(false);
      setConfirmed(false);
    } catch (e) {
      setError(e?.response?.data?.detail ?? e?.message ?? "Mode switch failed");
    } finally {
      setBusy(false);
    }
  };

  const handleLeave = async (targetMode) => {
    setBusy(true);
    setError(null);
    try {
      const res = await api.setLiveMode(targetMode, true);
      onModeChange(res.mode ?? targetMode);
    } catch (e) {
      setError(e?.response?.data?.detail ?? e?.message ?? "Mode switch failed");
    } finally {
      setBusy(false);
    }
  };

  const badgeClass = MODE_BADGE_CLASS[mode] ?? "border-line bg-bg-2 text-dim";
  const isLiveTest = mode === "LIVE_TEST";

  return (
    <div className="space-y-3">
      {/* Current mode chip */}
      <div className="flex items-center gap-3 flex-wrap">
        <span className="text-xs text-dimmer font-semibold uppercase tracking-wider">
          Broker Mode
        </span>
        <span
          className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full border text-xs font-mono font-semibold ${badgeClass}`}
        >
          {isLiveTest && <Zap className="w-3 h-3 shrink-0" />}
          {MODE_LABELS[mode] ?? mode ?? "Unknown"}
        </span>
        {mode === null && (
          <span className="text-xs text-dimmer font-mono">Loading…</span>
        )}
      </div>

      {/* LIVE_TEST active — show revert controls */}
      {isLiveTest && (
        <div className="rounded-lg border-2 border-danger bg-danger/10 px-4 py-3 space-y-3">
          <div className="flex items-center gap-2 text-danger font-bold text-sm">
            <AlertTriangle className="w-4 h-4 shrink-0" />
            LIVE TEST ACTIVE — Real orders may be placed on this account.
          </div>
          <div className="flex items-center gap-2 flex-wrap">
            <span className="text-xs text-dimmer">Revert to:</span>
            <button
              type="button"
              disabled={busy}
              onClick={() => handleLeave("LIVE_OFFLINE")}
              className="inline-flex items-center gap-1 px-3 py-1 rounded-md border border-blue-500/40 bg-blue-500/10 text-blue-300 text-xs font-mono hover:bg-blue-500/20 disabled:opacity-60 transition-colors"
            >
              {busy ? <Loader2 className="w-3 h-3 animate-spin" /> : null}
              Live Offline
            </button>
            <button
              type="button"
              disabled={busy}
              onClick={() => handleLeave("PAPER")}
              className="inline-flex items-center gap-1 px-3 py-1 rounded-md border border-emerald-500/40 bg-emerald-500/10 text-emerald-300 text-xs font-mono hover:bg-emerald-500/20 disabled:opacity-60 transition-colors"
            >
              {busy ? <Loader2 className="w-3 h-3 animate-spin" /> : null}
              Paper
            </button>
          </div>
        </div>
      )}

      {/* Not LIVE_TEST — show enter button */}
      {!isLiveTest && !showEnterConfirm && (
        <button
          type="button"
          disabled={busy || mode === null}
          onClick={() => { setShowEnterConfirm(true); setConfirmed(false); setError(null); }}
          className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-md border border-danger/50 bg-danger/10 text-danger text-xs font-mono font-semibold hover:bg-danger/20 disabled:opacity-60 transition-colors"
        >
          <ShieldAlert className="w-3.5 h-3.5" />
          Enter LIVE_TEST mode…
        </button>
      )}

      {/* Enter LIVE_TEST confirm panel */}
      {!isLiveTest && showEnterConfirm && (
        <div className="rounded-lg border-2 border-danger bg-danger/10 px-4 py-3 space-y-3">
          <div className="text-sm font-bold text-danger flex items-center gap-2">
            <AlertTriangle className="w-4 h-4 shrink-0" />
            You are about to enter LIVE TEST mode
          </div>
          <ul className="text-xs text-danger/80 space-y-1 list-disc pl-4">
            <li>An order ticket will appear below to place a real Flattrade order.</li>
            <li>Funds will be debited from your live trading account.</li>
            <li>All backend safeguards still apply, but this is real money.</li>
          </ul>
          <label className="flex items-center gap-2 cursor-pointer select-none">
            <input
              type="checkbox"
              className="w-4 h-4 rounded border-danger accent-danger"
              checked={confirmed}
              onChange={(e) => setConfirmed(e.target.checked)}
            />
            <span className="text-xs text-danger font-semibold">
              I understand this will use real money — proceed.
            </span>
          </label>
          <div className="flex items-center gap-2">
            <button
              type="button"
              disabled={!confirmed || busy}
              onClick={handleEnterLiveTest}
              className="inline-flex items-center gap-1 px-4 py-1.5 rounded-md border border-danger/60 bg-danger text-white text-xs font-mono font-bold hover:bg-danger/90 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
            >
              {busy ? <Loader2 className="w-3 h-3 animate-spin" /> : <CheckCircle className="w-3 h-3" />}
              Confirm — Enter LIVE_TEST
            </button>
            <button
              type="button"
              disabled={busy}
              onClick={() => { setShowEnterConfirm(false); setConfirmed(false); setError(null); }}
              className="px-3 py-1.5 rounded-md border border-line bg-bg-2 text-dim text-xs font-mono hover:bg-bg-3 transition-colors"
            >
              Cancel
            </button>
          </div>
        </div>
      )}

      {error && (
        <div className="text-xs text-danger font-mono px-2 py-1 rounded border border-danger/30 bg-danger/10">
          {error}
        </div>
      )}
    </div>
  );
}
