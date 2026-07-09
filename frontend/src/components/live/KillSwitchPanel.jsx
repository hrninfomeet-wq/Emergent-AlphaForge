import { useState } from "react";
import { Loader2, XOctagon } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { api } from "@/lib/api";
import { getApiErrorMessage } from "@/lib/apiError";
import { useLiveData } from "@/components/live/LiveDataProvider";

/**
 * KillSwitchPanel — the ONE button that flattens everything.
 *
 * Renders whenever the broker book shows open positions or working orders (or
 * a live test session is active) — not only inside an armed session, so a
 * deployment-placed or orphaned position always has a kill button.
 *
 * Fires only after a typed-KILL confirm, then renders the backend's per-leg
 * outcome report: every leg shows FILLED / PLACED_UNCONFIRMED / REJECTED with
 * the broker's reason string, plus cancel failures and the broker-truth
 * residual position check. A partial flatten is loudly visible — the report
 * lives in this always-mounted panel so it can't vanish with a session card.
 */

const TERMINAL_ORDER = new Set(["COMPLETE", "REJECTED", "REJECT", "CANCELED", "CANCELLED"]);

function asRows(raw) {
  if (Array.isArray(raw)) return raw;
  if (Array.isArray(raw?.positions)) return raw.positions;
  if (Array.isArray(raw?.orders)) return raw.orders;
  if (Array.isArray(raw?.data)) return raw.data;
  return [];
}

function isOpenPosition(p) {
  const n = parseFloat(p?.netqty ?? p?.quantity);
  return Number.isFinite(n) && n !== 0;
}

function isWorkingOrder(o) {
  return !TERMINAL_ORDER.has(String(o?.status ?? "").toUpperCase());
}

const OUTCOME_STYLE = {
  FILLED: "border-emerald-500/40 bg-emerald-500/10 text-emerald-300",
  PLACED_UNCONFIRMED: "border-amber-500/40 bg-amber-500/10 text-warning",
  REJECTED: "border-danger/40 bg-danger/10 text-danger",
  FAILED: "border-danger/40 bg-danger/10 text-danger",
  UNPRICED: "border-danger/40 bg-danger/10 text-danger",
};

function OutcomeChip({ outcome }) {
  const cls = OUTCOME_STYLE[outcome] || "border-line bg-bg-3 text-dimmer";
  return (
    <span className={`px-1.5 py-0.5 rounded border text-[10px] font-mono uppercase ${cls}`}>
      {outcome === "PLACED_UNCONFIRMED" ? "UNFILLED · WORKING" : outcome}
    </span>
  );
}

function LegReport({ panic }) {
  const legs = panic?.legs || [];
  const cancelFailures = panic?.cancel_failures || [];
  const residual = panic?.residual || [];
  return (
    <div className="space-y-2" data-testid="kill-switch-report">
      {legs.length > 0 && (
        <table className="w-full text-[11px] font-mono">
          <thead>
            <tr className="text-dimmer text-left uppercase tracking-wider text-[10px]">
              <th className="py-1 pr-2 font-normal">Leg</th>
              <th className="py-1 pr-2 font-normal text-right">Qty</th>
              <th className="py-1 pr-2 font-normal">Outcome</th>
              <th className="py-1 font-normal">Reason / detail</th>
            </tr>
          </thead>
          <tbody>
            {legs.map((l, i) => (
              <tr key={i} className="border-t border-line/60 align-top">
                <td className="py-1 pr-2 whitespace-nowrap">
                  {l.tsym}{l.slice ? <span className="text-dimmer"> ({l.slice})</span> : null}
                </td>
                <td className="py-1 pr-2 text-right">{l.qty}</td>
                <td className="py-1 pr-2"><OutcomeChip outcome={l.outcome} /></td>
                <td className="py-1 text-dim break-words">
                  {l.reason || (l.outcome === "FILLED"
                    ? `filled in ${(l.attempts || []).length} attempt(s)` : "—")}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {cancelFailures.length > 0 && (
        <div className="text-[11px] font-mono text-danger">
          Cancel failures: {cancelFailures.map((c) => `${c.norenordno}: ${c.reason}`).join("; ")}
        </div>
      )}
      {panic?.all_flat === true ? (
        <div className="text-[11px] font-mono px-2 py-1 rounded border border-emerald-500/40 bg-emerald-500/10 text-emerald-300">
          Broker position book re-checked: ALL FLAT.
        </div>
      ) : (
        <div className="text-[11px] font-mono px-2 py-1 rounded border-2 border-danger/60 bg-danger/10 text-danger font-bold"
          data-testid="kill-switch-residual">
          {panic?.all_flat === false
            ? `POSITIONS REMAIN: ${residual.map((r) => `${r.tsym} (${r.netqty})`).join(", ")} — handle manually or fire again.`
            : "Could not re-check the position book — verify positions manually before walking away."}
        </div>
      )}
    </div>
  );
}

export default function KillSwitchPanel() {
  const { positions, orders, errors, refetch } = useLiveData();
  const openPositions = asRows(positions).filter(isOpenPosition);
  const workingOrders = asRows(orders).filter(isWorkingOrder);

  // Broker state is UNKNOWN when a book poll has never loaded or is erroring. The
  // kill switch must be reachable EXACTLY then (no token, restart-before-OAuth,
  // failed poll) — the flatten endpoint reads its OWN books, so it works even when
  // the dashboard can't. This panel therefore ALWAYS renders (never self-unmounts).
  const brokerUnknown =
    positions == null || orders == null || !!errors?.positions || !!errors?.orders;

  const [confirming, setConfirming] = useState(false);
  const [confirmText, setConfirmText] = useState("");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState(null);

  const fire = async () => {
    if (confirmText !== "KILL" || busy) return;
    setBusy(true);
    try {
      const res = await api.liveKillSwitch();
      setResult(res);
    } catch (e) {
      setResult({ error: getApiErrorMessage(e, "Kill switch failed.") });
    } finally {
      setBusy(false);
      setConfirming(false);
      setConfirmText("");
      // Positions/orders live on the 15s slow poll — refresh everything now.
      (refetch?.all ?? refetch?.slow ?? (() => {}))();
    }
  };

  return (
    <div className="rounded-lg border-2 border-danger/60 bg-danger/5 overflow-hidden"
      data-testid="kill-switch-panel">
      <div className="px-4 py-2.5 border-b border-danger/30 bg-danger/10 flex items-center gap-2 flex-wrap">
        <XOctagon className="w-4 h-4 text-danger" />
        <span className="text-sm font-bold text-danger uppercase tracking-wider">Kill switch</span>
        <span className="text-[10px] text-dimmer font-mono">
          cancels all working orders · flattens every position (marketable LIMIT, re-priced until filled) · sweeps GTT/OCO · reverts to OFFLINE
        </span>
        <span className="ml-auto text-[10px] font-mono text-dim" data-testid="kill-switch-counts">
          {brokerUnknown
            ? "broker state UNKNOWN"
            : `${openPositions.length} open · ${workingOrders.length} working`}
        </span>
      </div>

      <div className="px-4 py-3 space-y-3">
        {brokerUnknown && (
          <div className="text-[11px] font-mono text-warning" data-testid="kill-switch-degraded">
            Broker state UNKNOWN (no/failed book read) — the kill will still attempt to
            flatten whatever the broker holds (it reads the broker directly).
          </div>
        )}
        {!confirming ? (
          <Button
            variant="outline"
            disabled={busy}
            onClick={() => { setResult(null); setConfirming(true); }}
            className="h-8 text-xs border-danger/60 text-danger hover:bg-danger/20 font-bold"
            data-testid="kill-switch-open-confirm"
          >
            {busy ? <Loader2 className="w-3.5 h-3.5 mr-1 animate-spin" /> : <XOctagon className="w-3.5 h-3.5 mr-1" />}
            FLATTEN EVERYTHING…
          </Button>
        ) : (
          <div className="rounded-md border-2 border-danger bg-danger/10 p-2 space-y-2">
            <div className="text-xs font-semibold text-danger">
              {brokerUnknown
                ? "Final confirm — broker state is UNKNOWN; this will attempt to cancel every working order and flatten every position the broker holds, with REAL exit orders."
                : `Final confirm — this cancels ${workingOrders.length} working order(s) and flattens ${openPositions.length} position(s) with REAL exit orders.`}
            </div>
            <div className="flex items-center gap-2">
              <Input
                autoFocus
                value={confirmText}
                onChange={(e) => setConfirmText(e.target.value.toUpperCase())}
                onKeyDown={(e) => { if (e.key === "Enter") fire(); }}
                placeholder='Type KILL to confirm'
                className="h-8 w-40 bg-bg-2 border-danger/50 text-xs font-mono"
                data-testid="kill-switch-confirm-input"
              />
              <Button
                disabled={confirmText !== "KILL" || busy}
                onClick={fire}
                className="h-8 text-xs bg-danger text-white hover:bg-danger/90 font-bold"
                data-testid="kill-switch-fire"
              >
                {busy ? <Loader2 className="w-3.5 h-3.5 mr-1 animate-spin" /> : null}
                KILL
              </Button>
              <Button variant="ghost" className="h-8 text-xs" disabled={busy}
                onClick={() => { setConfirming(false); setConfirmText(""); }}>
                Cancel
              </Button>
            </div>
          </div>
        )}

        {result?.error && (
          <div className="text-xs font-mono px-2 py-1 rounded border border-danger/40 bg-danger/10 text-danger">
            {String(result.error)}
          </div>
        )}
        {result?.already_running && (
          <div className="text-xs font-mono px-2 py-1 rounded border border-amber-500/40 bg-amber-500/10 text-warning">
            {String(result.message || "Kill switch already in progress.")}
          </div>
        )}
        {result && !result.error && !result.already_running && (
          <div className="space-y-2">
            <div className={`text-xs font-mono px-2 py-1 rounded border ${
              result?.panic?.all_flat === true
                ? "border-emerald-500/40 bg-emerald-500/10 text-emerald-300"
                : "border-amber-500/40 bg-amber-500/10 text-warning"
            }`}>
              {String(result.message || "Kill switch executed.")}
            </div>
            {result.connected === false ? (
              // A read error (expired token) is UNKNOWN, not a benign "not connected".
              <div className="text-[11px] font-mono text-danger">
                {result.read_error
                  ? "Broker read FAILED — positions UNKNOWN. New entries are blocked; reconnect Flattrade and re-fire to flatten."
                  : "Broker NOT connected — nothing was transmitted (plan only)."}
              </div>
            ) : (
              <LegReport panic={result.panic} />
            )}
          </div>
        )}
      </div>
    </div>
  );
}
