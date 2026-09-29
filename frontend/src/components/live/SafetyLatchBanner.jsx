import { useCallback, useEffect, useState } from "react";
import { ShieldAlert, Loader2, RotateCcw } from "lucide-react";
import { toast } from "sonner";
import { api } from "@/lib/api";
import { getApiErrorMessage } from "@/lib/apiError";
import { readStopState, stopWhen } from "@/lib/liveStopState";

/**
 * The way back from a tripped broker-stop-loss latch.
 *
 * `blocked_until_reset` halts EVERY new live entry and never self-clears — that
 * is correct, deliberate safety behaviour and nothing here weakens it. What was
 * missing was the exit: `POST /live-broker/safety-config/reset-latch` existed but
 * had no caller anywhere in the frontend, so a halted operator's only route back
 * was a raw API call they had to know about.
 *
 * That became reachable for the first time when the account-caps governor gave
 * `engine.guardrail_tick()` its first production caller — before that the latch
 * could effectively never trip, so the missing UI never bit.
 *
 * Resetting is deliberately two-step. A latch means a loss limit was breached;
 * clearing it re-authorises real-money entries and should never be one stray
 * click away.
 */
const POLL_MS = 20000;

// Machine codes come from the engine's own guardrail verdict — render them in
// plain language, but never invent a cause the backend did not report.
const REASON_TEXT = {
  broker_stop_loss: "the account daily-loss limit was breached",
  max_open_block: "the account hit its maximum open-position count",
  profit_lock: "the account profit-lock target was reached",
  unspecified: "the cause was not recorded",
  // Engine-halt codes. These never trip the latch, so before the halt was
  // surfaced here the banner stayed hidden while entries were in fact blocked.
  kill_switch: "the kill switch was used to flatten everything",
  reconcile_mismatch: "the broker's positions did not match our own record",
  order_sm_flagged: "an order came back in an inconsistent state",
  om_for_unknown_order: "the broker reported an order we have no record of",
  post_place_protection_failed: "a stop could not be attached after a fill",
};

export default function SafetyLatchBanner({ onChanged }) {
  const [cfg, setCfg] = useState(null);
  const [busy, setBusy] = useState(false);
  const [confirming, setConfirming] = useState(false);

  const load = useCallback(async () => {
    try {
      setCfg(await api.getSafetyConfig());
    } catch {
      // Stay silent and keep the last-known value: a failed poll must never
      // render a reassuring "not halted" state we cannot actually vouch for.
    }
  }, []);

  useEffect(() => {
    load();
    const id = window.setInterval(load, POLL_MS);
    return () => window.clearInterval(id);
  }, [load]);

  const stop = readStopState(cfg);
  const stopped = stop.stopped;
  useEffect(() => {
    if (!stopped) setConfirming(false);
  }, [stopped]);

  if (!stopped) return null;

  const reason = stop.reason;
  // The provenance line is ALWAYS rendered: a latch never self-clears, so it can be
  // days old, and a stop with no recorded time says so rather than showing nothing.
  const when = stopWhen(stop.at);

  const doReset = async () => {
    setBusy(true);
    try {
      const next = await api.resetSafetyLatch();
      setCfg(next);
      setConfirming(false);
      toast.success("Halt and latch cleared — live entries are permitted again");
      onChanged?.();
    } catch (e) {
      toast.error(getApiErrorMessage(e, "Could not clear the safety latch"));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div
      className="rounded-lg border border-danger/50 bg-danger/10 px-3 py-2.5"
      data-testid="safety-latch-banner"
      role="alert"
    >
      <div className="flex items-start gap-2.5 flex-wrap">
        <ShieldAlert className="w-4 h-4 text-danger shrink-0 mt-0.5" />
        <div className="min-w-0 flex-1">
          <div className="text-xs font-semibold text-danger uppercase tracking-wide">
            {stop.title}
          </div>
          <div className="text-[11.5px] text-foreground mt-0.5">
            No new live entries are being placed because{" "}
            {REASON_TEXT[reason] || `the guardrail reported “${reason}”`}.
          </div>
          <div
            className={`text-[10.5px] font-mono mt-0.5 ${when.known ? "text-dim" : "text-warning"}`}
            data-testid="safety-latch-when"
          >
            {when.known ? `tripped ${when.text}` : when.text}
          </div>
          <div className="text-[10.5px] text-dimmer mt-1">
            Open positions are unaffected and existing exits still run. Clearing this
            re-authorises real-money entries — check today’s P&amp;L first.
          </div>
        </div>

        <div className="flex items-center gap-1.5 shrink-0">
          {busy && <Loader2 className="w-3.5 h-3.5 animate-spin text-dimmer" />}
          {!confirming ? (
            <button
              type="button"
              onClick={() => setConfirming(true)}
              disabled={busy}
              className="inline-flex items-center gap-1 px-2.5 py-1 rounded-md border border-danger/50 bg-bg-2 text-[11px] font-medium text-danger hover:bg-danger/15 disabled:opacity-50"
              data-testid="safety-latch-reset"
            >
              <RotateCcw className="w-3 h-3" /> Reset latch
            </button>
          ) : (
            <>
              <span className="text-[10.5px] text-dim">Re-authorise entries?</span>
              <button
                type="button"
                onClick={doReset}
                disabled={busy}
                className="px-2.5 py-1 rounded-md border border-danger bg-danger/20 text-[11px] font-semibold text-danger hover:bg-danger/30 disabled:opacity-50"
                data-testid="safety-latch-reset-confirm"
              >
                Yes, clear it
              </button>
              <button
                type="button"
                onClick={() => setConfirming(false)}
                disabled={busy}
                className="px-2 py-1 rounded-md border border-line bg-bg-3 text-[11px] text-dim hover:text-foreground disabled:opacity-50"
                data-testid="safety-latch-reset-cancel"
              >
                Cancel
              </button>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
