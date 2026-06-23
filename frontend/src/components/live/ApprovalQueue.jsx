import { useCallback, useEffect, useRef, useState } from "react";
import {
  AlertTriangle,
  CheckCircle,
  KeyRound,
  Loader2,
  RefreshCw,
  XCircle,
} from "lucide-react";
import { api } from "@/lib/api";
import { fmtINR } from "@/lib/fmt";

/**
 * ApprovalQueue — the human-in-the-loop gate for real-money orders.
 *
 * An order is created elsewhere (the order ticket) via POST .../approvals, which
 * returns a ONE-SHOT token known only to the creating client. The pending queue
 * (GET .../approvals) NEVER returns that token, so the parent holds it in memory
 * (`tokens` prop, keyed by approval_id) and hands it here for approval.
 *
 * Props:
 *   tokens     — { [approval_id]: token }  one-shot tokens held by the parent.
 *   mode       — current execution mode string ("LIVE_TEST" enables real placement).
 *   onConsumed — (approval_id) => void  called after a successful approve OR reject
 *                (or a terminal failure) so the parent can drop the stale token.
 */

const POLL_MS = 4_000;

/** Reasons after which the approval is gone for good → drop the token + refetch. */
const TERMINAL_REASON_PREFIXES = ["bad_token", "expired", "not pending"];

function isTerminalReason(reason) {
  if (!reason) return false;
  const r = String(reason).toLowerCase();
  return TERMINAL_REASON_PREFIXES.some((p) => r.startsWith(p));
}

/** Short relative time for created_at; falls back to the raw string. */
function shortAgo(ts) {
  if (!ts) return "";
  const t = new Date(ts).getTime();
  if (!Number.isFinite(t)) return String(ts);
  const diffMs = Date.now() - t;
  if (diffMs < 0) return "just now";
  const secs = Math.round(diffMs / 1000);
  if (secs < 60) return `${secs}s ago`;
  const mins = Math.round(secs / 60);
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.round(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  const days = Math.round(hrs / 24);
  return `${days}d ago`;
}

const SIDE_LABEL = { B: "Buy", S: "Sell", b: "Buy", s: "Sell" };
const SIDE_CLASS = {
  B: "border-emerald-500/60 bg-emerald-500/15 text-emerald-300",
  b: "border-emerald-500/60 bg-emerald-500/15 text-emerald-300",
  S: "border-danger/60 bg-danger/15 text-danger",
  s: "border-danger/60 bg-danger/15 text-danger",
};

function SummaryField({ label, value }) {
  return (
    <div className="flex flex-col gap-0.5">
      <span className="text-[10px] uppercase tracking-wider text-dimmer font-semibold">
        {label}
      </span>
      <span className="text-xs font-mono text-foreground tabular-nums">{value}</span>
    </div>
  );
}

/**
 * One pending approval card. Owns its own busy / confirm / result state so that
 * acting on one card never blocks another.
 */
function ApprovalCard({ appr, token, mode, onConsumed, onChanged }) {
  const [showConfirm, setShowConfirm] = useState(false);
  const [approveBusy, setApproveBusy] = useState(false);
  const [rejectBusy, setRejectBusy] = useState(false);
  const [result, setResult] = useState(null); // approve API result
  const [error, setError] = useState(null);

  const approvalId = appr?.approval_id;
  const summary = appr?.summary ?? {};
  const hasToken = token != null && token !== "";
  const isLiveTest = mode === "LIVE_TEST";
  const isBuy = String(summary.side ?? "B").toUpperCase() === "B";
  const busy = approveBusy || rejectBusy;
  // The backend only auto-places a BUY entry, and only in LIVE_TEST. Don't offer a
  // doomed Approve click — a wrong click is now safely REVERTED to pending by the
  // backend, but disabling it is clearer. SELL/non-armed → Reject (and re-create).
  const canApprove = hasToken && isBuy && isLiveTest && !busy;

  const sideRaw = summary.side ?? "";
  const sideLabel = SIDE_LABEL[sideRaw] ?? (sideRaw || "–");
  const sideClass = SIDE_CLASS[sideRaw] ?? "border-line bg-bg-3 text-dimmer";

  const refLtp =
    summary.ref_ltp != null && Number.isFinite(Number(summary.ref_ltp))
      ? fmtINR(Number(summary.ref_ltp), 2)
      : "—";

  const handleApproveConfirmed = async () => {
    if (!approvalId || !hasToken || approveBusy) return;
    setApproveBusy(true);
    setError(null);
    setResult(null);
    try {
      const res = await api.approveOrder(approvalId, token);
      setResult(res ?? {});
      setShowConfirm(false);
      // Drop the token + refetch when placed, OR when the approval is terminally gone.
      if (res?.placed || isTerminalReason(res?.reason)) {
        onConsumed?.(approvalId);
        onChanged?.();
      }
    } catch (e) {
      setError(e?.response?.data?.detail ?? e?.message ?? "Approve failed");
      setShowConfirm(false);
    } finally {
      setApproveBusy(false);
    }
  };

  const handleReject = async () => {
    if (!approvalId || rejectBusy) return;
    setRejectBusy(true);
    setError(null);
    try {
      const res = await api.rejectOrder(approvalId);
      if (res?.ok) {
        onConsumed?.(approvalId);
      } else {
        // Reject didn't take (e.g. not pending / not found) — surface it, keep token.
        setError(`Reject failed: ${res?.reason ?? "not pending"}`);
      }
      onChanged?.(); // always resync the queue with server truth
    } catch (e) {
      setError(e?.response?.data?.detail ?? e?.message ?? "Reject failed");
    } finally {
      setRejectBusy(false);
    }
  };

  const placed = result?.placed === true;
  // A result exists and was not placed → show the reason in a danger box.
  const showFailBox = result != null && !placed;

  return (
    <div className="rounded-lg border border-line bg-bg-2/50 overflow-hidden">
      {/* Header: side chip + contract + created_at */}
      <div className="px-4 py-2.5 border-b border-line bg-bg-2/40 flex items-center gap-2 flex-wrap">
        <span
          className={`inline-flex items-center px-2 py-0.5 rounded-full border text-[10px] font-mono font-bold uppercase tracking-wider ${sideClass}`}
        >
          {sideLabel}
        </span>
        <span className="text-sm font-semibold text-foreground font-mono">
          {summary.underlying ?? "–"} {summary.strike ?? "–"} {summary.option_type ?? ""}
        </span>
        <span className="text-[10px] font-mono text-dimmer">
          {appr?.status ? `[${appr.status}]` : ""}
        </span>
        <span className="ml-auto text-[10px] font-mono text-dimmer" title={appr?.created_at ?? ""}>
          {shortAgo(appr?.created_at)}
        </span>
      </div>

      <div className="px-4 py-3 space-y-3">
        {/* Summary grid */}
        <div className="grid grid-cols-3 gap-x-4 gap-y-2 sm:grid-cols-4">
          <SummaryField label="Order Type" value={summary.order_type ?? "–"} />
          <SummaryField label="Product" value={summary.product ?? "–"} />
          <SummaryField label="Lots" value={summary.lots ?? "–"} />
          <SummaryField label="Ref LTP" value={refLtp} />
          <SummaryField label="Children" value={summary.child_count ?? "–"} />
        </div>

        {/* Mode hint — approving in a non-LIVE_TEST mode will be rejected by the backend */}
        {isBuy && !isLiveTest && (
          <div className="flex items-center gap-1.5 text-[11px] font-mono text-amber-400">
            <AlertTriangle className="w-3.5 h-3.5 shrink-0" />
            Arm LIVE_TEST above to place on approval
          </div>
        )}

        {/* Sell hint — automated placement is BUY-only; sells are placed manually */}
        {!isBuy && (
          <div className="flex items-center gap-1.5 text-[11px] font-mono text-amber-400">
            <AlertTriangle className="w-3.5 h-3.5 shrink-0" />
            Automated placement is BUY-only — Reject this and place the SELL manually
          </div>
        )}

        {/* Token-lost notice */}
        {!hasToken && (
          <div className="flex items-center gap-1.5 text-[11px] font-mono text-dimmer">
            <KeyRound className="w-3.5 h-3.5 shrink-0 text-amber-400" />
            token lost on reload — Reject and re-create
          </div>
        )}

        {/* Actions */}
        {!showConfirm && (
          <div className="flex items-center gap-2 flex-wrap">
            <button
              type="button"
              disabled={!canApprove}
              onClick={() => {
                setShowConfirm(true);
                setError(null);
                setResult(null);
              }}
              title={
                !hasToken
                  ? "Token lost on reload — reject and re-create the approval"
                  : !isBuy
                  ? "Automated placement is BUY-only — reject and place the SELL manually"
                  : !isLiveTest
                  ? "Arm LIVE_TEST above to place on approval"
                  : "Approve — places a REAL order"
              }
              className="inline-flex items-center gap-1.5 px-5 py-2 rounded-md border-2 border-danger/70 bg-danger text-white text-sm font-mono font-bold hover:bg-danger/90 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
            >
              {approveBusy ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : null}
              Approve — REAL MONEY
            </button>
            <button
              type="button"
              disabled={busy}
              onClick={handleReject}
              className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-md border border-line bg-bg-2 text-dim text-xs font-mono hover:bg-bg-3 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
            >
              {rejectBusy ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <XCircle className="w-3.5 h-3.5" />}
              Reject
            </button>
          </div>
        )}

        {/* Second inline confirm — danger box */}
        {showConfirm && (
          <div className="rounded-lg border-2 border-danger bg-danger/10 px-4 py-3 space-y-3">
            <div className="text-sm font-bold text-danger flex items-center gap-2">
              <AlertTriangle className="w-4 h-4 shrink-0" />
              Final confirm — placing a REAL order
            </div>
            <div className="text-xs text-danger/80 font-mono space-y-0.5">
              <div>
                <span className="font-bold">{summary.underlying ?? "–"}</span>{" "}
                <span className="font-bold">{summary.strike ?? "–"}</span>{" "}
                {summary.option_type ?? ""} {sideLabel}
              </div>
              <div>
                {summary.lots ?? "–"} lot(s) · {summary.order_type ?? "–"} · {summary.product ?? "–"}
                {" · "}~{refLtp} LTP
              </div>
            </div>
            <div className="flex items-center gap-2">
              <button
                type="button"
                disabled={!canApprove}
                onClick={handleApproveConfirmed}
                className="inline-flex items-center gap-1.5 px-4 py-1.5 rounded-md border border-danger/60 bg-danger text-white text-xs font-mono font-bold hover:bg-danger/90 disabled:opacity-50 transition-colors"
              >
                {approveBusy ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : null}
                {approveBusy ? "Placing…" : "Confirm — Place Order"}
              </button>
              <button
                type="button"
                disabled={approveBusy}
                onClick={() => setShowConfirm(false)}
                className="px-3 py-1.5 rounded-md border border-line bg-bg-2 text-dim text-xs font-mono hover:bg-bg-3 transition-colors"
              >
                Cancel
              </button>
            </div>
          </div>
        )}

        {/* Approve result — placed (green) or not-placed (danger) */}
        {placed && (
          <div className="rounded-lg border border-emerald-500/40 bg-emerald-500/10 px-3 py-2.5 text-xs font-mono text-emerald-300 space-y-1">
            <div className="font-bold flex items-center gap-1.5">
              <CheckCircle className="w-3.5 h-3.5 shrink-0" />
              Order placed
            </div>
            {result?.norenordno && <div>Order ID: {result.norenordno}</div>}
            {result?.protected && <div className="text-emerald-300/80">Protected (SL attached)</div>}
          </div>
        )}

        {showFailBox && (
          <div className="rounded-lg border border-danger/40 bg-danger/10 px-3 py-2.5 text-xs font-mono text-danger space-y-1">
            <div className="font-bold flex items-center gap-1.5">
              <XCircle className="w-3.5 h-3.5 shrink-0" />
              {result?.halted ? "Halted" : "Not placed"}
            </div>
            {result?.reason && <div className="text-danger/90">Reason: {result.reason}</div>}
            {Array.isArray(result?.verdicts) &&
              result.verdicts.map((v, i) => (
                <div
                  key={i}
                  className={`flex items-start gap-1.5 ${v.ok ? "text-emerald-300" : "text-danger"}`}
                >
                  {v.ok ? (
                    <CheckCircle className="w-3 h-3 shrink-0 mt-0.5" />
                  ) : (
                    <XCircle className="w-3 h-3 shrink-0 mt-0.5" />
                  )}
                  <span>
                    {v.check}
                    {v.detail ? ` — ${v.detail}` : ""}
                  </span>
                </div>
              ))}
          </div>
        )}

        {/* Network / unexpected error */}
        {error && (
          <div className="text-xs text-danger font-mono px-2 py-1 rounded border border-danger/30 bg-danger/10">
            {error}
          </div>
        )}
      </div>
    </div>
  );
}

export default function ApprovalQueue({ tokens, mode, onConsumed }) {
  const [pending, setPending] = useState([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [fetchError, setFetchError] = useState(null);
  const timerRef = useRef(null);

  const fetchPending = useCallback(async () => {
    setRefreshing(true);
    try {
      const res = await api.listOrderApprovals();
      const rows = Array.isArray(res?.pending) ? res.pending : [];
      setPending(rows);
      setFetchError(null);
    } catch (e) {
      setFetchError(e?.response?.data?.detail ?? e?.message ?? "Failed to load approvals");
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    fetchPending();
    timerRef.current = setInterval(fetchPending, POLL_MS);
    return () => {
      if (timerRef.current) clearInterval(timerRef.current);
    };
  }, [fetchPending]);

  const tokenMap = tokens ?? {};

  return (
    <div className="space-y-3">
      {/* Header row — manual refresh */}
      <div className="flex items-center gap-2">
        <span className="text-[10px] uppercase tracking-wider text-dimmer font-semibold">
          {pending.length > 0 ? `${pending.length} pending` : "Pending approvals"}
        </span>
        <button
          type="button"
          onClick={fetchPending}
          disabled={refreshing}
          title="Refresh pending approvals"
          className="ml-auto inline-flex items-center gap-1.5 px-2.5 py-1 rounded-md border border-line bg-bg-2 text-dim text-[11px] font-mono hover:bg-bg-3 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
        >
          {refreshing ? (
            <Loader2 className="w-3 h-3 animate-spin" />
          ) : (
            <RefreshCw className="w-3 h-3" />
          )}
          Refresh
        </button>
      </div>

      {/* Fetch error */}
      {fetchError && (
        <div className="text-xs text-danger font-mono px-2 py-1 rounded border border-danger/30 bg-danger/10">
          {fetchError}
        </div>
      )}

      {/* Loading */}
      {loading && pending.length === 0 && !fetchError && (
        <div className="flex items-center gap-2 text-xs text-dimmer font-mono py-6 justify-center">
          <Loader2 className="w-3.5 h-3.5 animate-spin" />
          Loading approvals…
        </div>
      )}

      {/* Empty state */}
      {!loading && pending.length === 0 && !fetchError && (
        <div className="text-xs text-dimmer font-mono py-6 text-center">
          No pending approvals
        </div>
      )}

      {/* Cards — only rows with a stable approval_id (never key on Math.random,
          which would remount a stateful confirm/approve card every 4s poll). */}
      {pending
        .filter((appr) => appr?.approval_id)
        .map((appr) => (
          <ApprovalCard
            key={appr.approval_id}
            appr={appr}
            token={tokenMap[appr.approval_id]}
            mode={mode}
            onConsumed={onConsumed}
            onChanged={fetchPending}
          />
        ))}
    </div>
  );
}
