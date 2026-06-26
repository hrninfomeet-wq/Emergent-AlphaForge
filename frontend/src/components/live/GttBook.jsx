import { useCallback, useState } from "react";
import { Loader2, RefreshCw, ShieldAlert, XCircle } from "lucide-react";
import { api } from "@/lib/api";
import { fmtNum } from "@/lib/fmt";
import { useLiveData } from "@/components/live/LiveDataProvider";

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
 * Backend contract = the Flattrade GetPendingGTTOrder row (schema confirmed
 * against the PiConnect PDF ch.1.16). Field casing varies (Al_id/al_id, Qty/qty,
 * Prc/prc) so every accessor below is tolerant:
 *   api.listGtt() → { gtt: [ {
 *     al_id | Al_id,  string — the alert id (passed to cancelGtt)
 *     ai_t,           string — alert type, e.g. "LTP_A"/"LTP_B"/"LMT_BOS_O".
 *                             This is the real-money DIRECTION field — read it
 *                             back to confirm a stop fires the right way.
 *     tsym,           string — trading symbol
 *     exch,           string — exchange (NFO/BFO/…)
 *     trantype,       "B" | "S"
 *     qty | Qty,      number
 *     d,              string — single-leg trigger (price compared with LTP)
 *     prc | Prc,      string — single-leg limit price of the resulting order
 *     validity,       "GTT" | "DAY"
 *     prd,            "C" | "M" | "H"
 *     remarks | Remarks,
 *     oivariable,     [ {var_name:"x"|"y", d:string} ] — length ≥ 2 ⇒ OCO; the
 *                             two d's are the SL (x) and TP (y) triggers.
 *   } ] }
 *
 *   api.cancelGtt(al_id, kind) → { canceled?: boolean, result?: {...} }
 *     canceled === false (or a thrown error) surfaces a reason on the row.
 */

const TRAN_LABEL = { B: "Buy", S: "Sell", b: "Buy", s: "Sell" };
const TRAN_CLASS = {
  B: "border-emerald-500/60 bg-emerald-500/15 text-emerald-300",
  b: "border-emerald-500/60 bg-emerald-500/15 text-emerald-300",
  S: "border-danger/60 bg-danger/15 text-danger",
  s: "border-danger/60 bg-danger/15 text-danger",
};

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

  // Tolerate the Noren response casing (Al_id/al_id, Qty/qty, Prc/prc, …).
  const alId = row?.al_id ?? row?.Al_id ?? row?.AL_id;
  const qty = row?.qty ?? row?.Qty;
  const prc = row?.prc ?? row?.Prc;
  const remarks = row?.remarks ?? row?.Remarks;
  const aiT = row?.ai_t ?? "";
  const tranRaw = row?.trantype ?? "";
  const tranLabel = TRAN_LABEL[tranRaw] ?? (tranRaw || "–");
  const tranClass = TRAN_CLASS[tranRaw] ?? "border-line bg-bg-3 text-dimmer";

  // OCO = two oivariable legs (x = SL trigger, y = TP trigger). Single GTT has
  // its trigger in `d`. Fall back to ai_t containing "BOS" (OCO bracket type).
  const oiv = Array.isArray(row?.oivariable) ? row.oivariable : [];
  const isOco = oiv.length >= 2 || /BOS/i.test(aiT);
  const oivByName = (n) => oiv.find((v) => String(v?.var_name).toLowerCase() === n)?.d;
  const slTrigger = oivByName("x") ?? oiv[0]?.d;
  const tpTrigger = oivByName("y") ?? oiv[1]?.d;

  const handleCancel = async () => {
    if (!alId || busy) return;
    setBusy(true);
    setError(null);
    try {
      const res = await api.cancelGtt(alId, isOco ? "oco" : "gtt");
      // Treat an explicit canceled:false as a failure; otherwise assume success
      // and let the parent refetch reconcile with broker truth.
      if (res && res.canceled === false) {
        const reason =
          res?.result?.emsg ?? res?.reason ?? res?.detail ?? "rejected by broker";
        setError(`Cancel failed: ${reason}`);
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
        {/* ai_t — the alert-type/direction string the broker recorded. Surfaced
            so the user can read back the EXACT value (e.g. LTP_B vs LTP_A). */}
        {aiT && (
          <span
            className="text-[10px] font-mono text-info/90 px-1.5 py-0.5 rounded border border-info/30 bg-info/10"
            title="Alert type (direction) recorded by the broker"
          >
            {aiT}
          </span>
        )}
        {remarks && (
          <span className="text-[10px] font-mono text-dimmer truncate max-w-[120px]">
            {remarks}
          </span>
        )}
        <span
          className="ml-auto text-[10px] font-mono text-dimmer"
          title={row?.validity != null ? String(row.validity) : ""}
        >
          {row?.validity ?? ""}
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
              {qty != null ? qty : "–"}
            </span>
          </div>

          {isOco ? (
            <>
              <div className="flex flex-col gap-0.5">
                <span className="text-[10px] uppercase tracking-wider text-dimmer font-semibold">
                  Stop trigger (x)
                </span>
                <Leg trigger={slTrigger} limit={prc} />
              </div>
              <div className="flex flex-col gap-0.5">
                <span className="text-[10px] uppercase tracking-wider text-dimmer font-semibold">
                  Target trigger (y)
                </span>
                <Leg trigger={tpTrigger} limit={prc} />
              </div>
            </>
          ) : (
            <div className="flex flex-col gap-0.5">
              <span className="text-[10px] uppercase tracking-wider text-dimmer font-semibold">
                Trigger → Limit
              </span>
              <Leg trigger={row?.d} limit={prc} />
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
  // GTT/OCO rows come from the shared LiveDataProvider (single 6s poll).
  const { gtt: gttResp, errors, refetch } = useLiveData();
  const [refreshing, setRefreshing] = useState(false);

  const gtt = Array.isArray(gttResp?.gtt) ? gttResp.gtt : [];
  const loading = gttResp == null && !errors.gtt;
  const fetchError = errors.gtt
    ? (errors.gtt?.response?.data?.detail ?? errors.gtt?.message ?? "Failed to load GTT orders")
    : null;

  // Manual refresh + post-cancel re-pull go through the shared poller.
  const fetchGtt = useCallback(async () => {
    setRefreshing(true);
    try {
      await refetch.gtt();
    } finally {
      setRefreshing(false);
    }
  }, [refetch]);

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
          key={row?.al_id ?? row?.Al_id ?? `gtt-${i}`}
          row={row}
          onCancelled={fetchGtt}
        />
      ))}
    </div>
  );
}
