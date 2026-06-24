import { useCallback, useEffect, useRef, useState } from "react";
import { Loader2, RefreshCw, ShieldAlert, XCircle } from "lucide-react";
import { api } from "@/lib/api";
import { fmtNum } from "@/lib/fmt";

/**
 * GttBook — the OCO-GTT order book for the live page.
 *
 * OCO-GTT (One-Cancels-Other Good-Till-Triggered) is a PC-died catastrophe
 * backstop placed against NRML positions: if the software dies, the broker still
 * holds a resting stop/target pair so the position can't run unprotected. When the
 * normal software exit fills, that backstop is auto-cancelled — so a row here is
 * usually transient. This panel lists the resting GTT/OCO orders and lets a human
 * cancel one manually.
 *
 * It NEVER places a GTT itself — placement is owned by the exit-protection layer.
 *
 * Props: {} (none) — self-contained; polls the backend on its own.
 *
 * Backend contract (defensive — tolerate {gtt:[]} / empty / undefined):
 *   api.listGtt() → { gtt: [ {
 *     al_id,          string — the alert/GTT id (passed to cancelGtt)
 *     tsym,           string — trading symbol
 *     exch,           string — exchange (NFO/BFO/…)
 *     trantype,       "B" | "S"
 *     qty,            number
 *     trigger_price,  number — single-leg trigger (GTT)
 *     limit_price,    number — single-leg limit (GTT)
 *     status,         string — broker status text
 *     type,           "GTT" | "OCO"
 *     created_at,     ISO string | epoch ms
 *     // OCO two-leg fields (optional; present when type === "OCO"):
 *     stoploss_trigger_price, stoploss_limit_price,
 *     target_trigger_price,   target_limit_price,
 *   } ] }
 *
 *   api.cancelGtt(al_id) → { ok?: boolean, reason?: string, ... }
 *     ok === false (or a thrown error) surfaces `reason` on the row.
 */

const POLL_MS = 6_000;

const TRAN_LABEL = { B: "Buy", S: "Sell", b: "Buy", s: "Sell" };
const TRAN_CLASS = {
  B: "border-emerald-500/60 bg-emerald-500/15 text-emerald-300",
  b: "border-emerald-500/60 bg-emerald-500/15 text-emerald-300",
  S: "border-danger/60 bg-danger/15 text-danger",
  s: "border-danger/60 bg-danger/15 text-danger",
};

/** Short relative time for created_at; tolerates ISO strings + epoch ms. */
function shortAgo(ts) {
  if (ts == null || ts === "") return "";
  const t =
    typeof ts === "number" ? ts : Number.isFinite(Number(ts)) && String(ts).length >= 10 && !String(ts).includes("-")
      ? Number(ts)
      : new Date(ts).getTime();
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

/** Format a price defensively (null/blank → em dash). */
function price(p) {
  if (p == null || p === "" || Number.isNaN(Number(p))) return "–";
  return fmtNum(p, 2);
}

/** A trigger→limit leg line, e.g. "≥ 120.50 → 120.00". */
function Leg({ label, trigger, limit }) {
  return (
    <span className="inline-flex items-center gap-1 text-xs font-mono tabular-nums">
      {label && <span className="text-dimmer">{label}</span>}
      <span className="text-foreground">{price(trigger)}</span>
      <span className="text-dimmer">→</span>
      <span className="text-dim">{price(limit)}</span>
    </span>
  );
}

/**
 * One resting GTT/OCO row. Owns its own busy / error state so cancelling one row
 * never blocks another.
 */
function GttRow({ row, onCancelled }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  const alId = row?.al_id;
  const tranRaw = row?.trantype ?? "";
  const tranLabel = TRAN_LABEL[tranRaw] ?? (tranRaw || "–");
  const tranClass = TRAN_CLASS[tranRaw] ?? "border-line bg-bg-3 text-dimmer";
  const isOco = String(row?.type ?? "").toUpperCase() === "OCO";

  const handleCancel = async () => {
    if (!alId || busy) return;
    setBusy(true);
    setError(null);
    try {
      const res = await api.cancelGtt(alId);
      // Treat an explicit ok:false as a failure and surface the reason; otherwise
      // assume success and let the parent refetch reconcile with broker truth.
      if (res && res.ok === false) {
        setError(`Cancel failed: ${res.reason ?? res.detail ?? "rejected by broker"}`);
      } else {
        onCancelled?.();
      }
    } catch (e) {
      setError(e?.response?.data?.detail ?? e?.message ?? "Cancel failed");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="rounded-lg border border-line bg-bg-2/50 overflow-hidden">
      {/* Header: side chip + symbol + type/status + age */}
      <div className="px-4 py-2.5 border-b border-line bg-bg-2/40 flex items-center gap-2 flex-wrap">
        <span
          className={`inline-flex items-center px-2 py-0.5 rounded-full border text-[10px] font-mono font-bold uppercase tracking-wider ${tranClass}`}
        >
          {tranLabel}
        </span>
        <span className="text-sm font-semibold text-foreground font-mono truncate max-w-[220px]">
          {row?.tsym ?? "–"}
        </span>
        {row?.exch && (
          <span className="text-[10px] font-mono text-dimmer uppercase">{row.exch}</span>
        )}
        <span
          className={`inline-flex items-center px-2 py-0.5 rounded-full border text-[10px] font-mono font-bold uppercase tracking-wider ${
            isOco
              ? "border-info/50 bg-info/10 text-info"
              : "border-line bg-bg-3 text-dim"
          }`}
        >
          {isOco ? "OCO" : "GTT"}
        </span>
        {row?.status && (
          <span className="text-[10px] font-mono text-dimmer">[{row.status}]</span>
        )}
        <span
          className="ml-auto text-[10px] font-mono text-dimmer"
          title={row?.created_at != null ? String(row.created_at) : ""}
        >
          {shortAgo(row?.created_at)}
        </span>
      </div>

      <div className="px-4 py-3 space-y-3">
        {/* Qty + leg(s) */}
        <div className="flex items-start gap-x-6 gap-y-2 flex-wrap">
          <div className="flex flex-col gap-0.5">
            <span className="text-[10px] uppercase tracking-wider text-dimmer font-semibold">
              Qty
            </span>
            <span className="text-xs font-mono text-foreground tabular-nums">
              {row?.qty != null ? row.qty : "–"}
            </span>
          </div>

          {isOco ? (
            <>
              <div className="flex flex-col gap-0.5">
                <span className="text-[10px] uppercase tracking-wider text-dimmer font-semibold">
                  Stop leg
                </span>
                <Leg
                  trigger={row?.stoploss_trigger_price ?? row?.trigger_price}
                  limit={row?.stoploss_limit_price ?? row?.limit_price}
                />
              </div>
              <div className="flex flex-col gap-0.5">
                <span className="text-[10px] uppercase tracking-wider text-dimmer font-semibold">
                  Target leg
                </span>
                <Leg
                  trigger={row?.target_trigger_price}
                  limit={row?.target_limit_price}
                />
              </div>
            </>
          ) : (
            <div className="flex flex-col gap-0.5">
              <span className="text-[10px] uppercase tracking-wider text-dimmer font-semibold">
                Trigger → Limit
              </span>
              <Leg trigger={row?.trigger_price} limit={row?.limit_price} />
            </div>
          )}
        </div>

        {/* Cancel action */}
        <div className="flex items-center gap-2 flex-wrap">
          <button
            type="button"
            disabled={!alId || busy}
            onClick={handleCancel}
            title={alId ? "Cancel this resting GTT/OCO order" : "Missing al_id — cannot cancel"}
            className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-md border border-line bg-bg-2 text-dim text-xs font-mono hover:bg-bg-3 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
          >
            {busy ? (
              <Loader2 className="w-3.5 h-3.5 animate-spin" />
            ) : (
              <XCircle className="w-3.5 h-3.5" />
            )}
            {busy ? "Cancelling…" : "Cancel"}
          </button>
        </div>

        {/* Per-row error */}
        {error && (
          <div className="text-xs text-danger font-mono px-2 py-1 rounded border border-danger/30 bg-danger/10">
            {error}
          </div>
        )}
      </div>
    </div>
  );
}

export default function GttBook() {
  const [gtt, setGtt] = useState([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [fetchError, setFetchError] = useState(null);
  const timerRef = useRef(null);

  const fetchGtt = useCallback(async () => {
    setRefreshing(true);
    try {
      const res = await api.listGtt();
      const rows = Array.isArray(res?.gtt) ? res.gtt : [];
      setGtt(rows);
      setFetchError(null);
    } catch (e) {
      setFetchError(
        e?.response?.data?.detail ?? e?.message ?? "Failed to load GTT orders"
      );
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    fetchGtt();
    timerRef.current = setInterval(fetchGtt, POLL_MS);
    return () => {
      if (timerRef.current) clearInterval(timerRef.current);
    };
  }, [fetchGtt]);

  return (
    <div className="space-y-3">
      {/* Header row — count + manual refresh */}
      <div className="flex items-center gap-2">
        <span className="text-[10px] uppercase tracking-wider text-dimmer font-semibold">
          {gtt.length > 0 ? `${gtt.length} resting` : "GTT / OCO book"}
        </span>
        <button
          type="button"
          onClick={fetchGtt}
          disabled={refreshing}
          title="Refresh GTT / OCO orders"
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

      {/* Backstop note */}
      <div className="flex items-start gap-1.5 text-[11px] font-mono text-dimmer leading-snug">
        <ShieldAlert className="w-3.5 h-3.5 shrink-0 mt-0.5 text-amber-400" />
        <span>
          OCO-GTT is a PC-died catastrophe backstop for NRML positions only; it's
          auto-cancelled when the software exit fills.
        </span>
      </div>

      {/* Fetch error */}
      {fetchError && (
        <div className="text-xs text-danger font-mono px-2 py-1 rounded border border-danger/30 bg-danger/10">
          {fetchError}
        </div>
      )}

      {/* Loading */}
      {loading && gtt.length === 0 && !fetchError && (
        <div className="flex items-center gap-2 text-xs text-dimmer font-mono py-6 justify-center">
          <Loader2 className="w-3.5 h-3.5 animate-spin" />
          Loading GTT orders…
        </div>
      )}

      {/* Empty state */}
      {!loading && gtt.length === 0 && !fetchError && (
        <div className="text-xs text-dimmer font-mono py-6 text-center">
          No GTT / OCO orders
        </div>
      )}

      {/* Rows — key on a stable al_id where present; fall back to index so a row
          without an id still renders (it just can't be cancelled). */}
      {gtt.map((row, i) => (
        <GttRow
          key={row?.al_id ?? `gtt-${i}`}
          row={row}
          onCancelled={fetchGtt}
        />
      ))}
    </div>
  );
}
