import { useCallback, useState } from "react";
import {
  AlertTriangle,
  Loader2,
  RefreshCw,
  ShieldAlert,
  ShieldCheck,
} from "lucide-react";
import { fmtINR } from "@/lib/fmt";
import { useLiveData } from "@/components/live/LiveDataProvider";

/**
 * GuardPanel — read-only status of the SOFTWARE auto-exit guard.
 *
 * The guard is a server-side watcher that protects each filled entry WITHOUT a
 * resting stop-loss order on the exchange (margin-safe path): it tracks the live
 * premium and squares the position off in software when it crosses the stop or
 * target level. This panel just SHOWS what the guard is currently watching — it
 * never arms/disarms or squares anything off itself.
 *
 * Polls api.getGuardStatus() every 3s (+ on mount + manual refresh). Shape:
 * {
 *   armed: bool,            // true → guard transmits real auto-exits
 *   mode:  string,          // free-text mode label from the backend
 *   count: int,             // number of guarded positions
 *   guarded: [{
 *     tsym, qty,
 *     entry_price, stop_level, target_level, peak,
 *     seen_filled         // true once the entry fill is confirmed
 *   }]
 * }
 *
 * Defensive against undefined / partial payloads at every level.
 *
 * Props: none (reads guard status from the shared LiveDataProvider context).
 */

// ── Safe numeric coercion — non-finite → null (renders as "—") ───────────────
function fin(v) {
  if (v === null || v === undefined || v === "") return null;
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
}

/** Percent distance of `level` from `entry`, signed; null if not computable. */
function pctFromEntry(level, entry) {
  const l = fin(level);
  const e = fin(entry);
  if (l === null || e === null || e === 0) return null;
  return ((l - e) / Math.abs(e)) * 100;
}

function fmtSignedPct(p) {
  if (p === null || p === undefined || !Number.isFinite(p)) return "";
  const sign = p > 0 ? "+" : p < 0 ? "−" : "";
  return `${sign}${Math.abs(p).toFixed(1)}%`;
}

// ── Tiny presentational helpers ──────────────────────────────────────────────
const microLabel = "text-[10px] uppercase tracking-wider text-dimmer font-semibold";

function Metric({ label, value, valueClass = "text-foreground", sub }) {
  return (
    <div className="flex flex-col gap-0.5 min-w-0">
      <span className={microLabel}>{label}</span>
      <span className={`text-xs font-mono tabular-nums truncate ${valueClass}`}>
        {value}
      </span>
      {sub != null && sub !== "" && (
        <span className="text-[10px] font-mono tabular-nums text-dimmer">{sub}</span>
      )}
    </div>
  );
}

/** One guarded position row. */
function GuardRow({ pos }) {
  const tsym = pos?.tsym ?? "–";
  const qty = pos?.qty ?? "–";
  const entry = fin(pos?.entry_price);
  const stop = fin(pos?.stop_level);
  const target = fin(pos?.target_level);
  const peak = fin(pos?.peak);
  const filled = !!pos?.seen_filled;

  const stopPct = pctFromEntry(stop, entry);
  const targetPct = pctFromEntry(target, entry);

  return (
    <div className="rounded-lg border border-line bg-bg-2/50 px-3 py-2.5">
      {/* Header: symbol + qty + fill chip */}
      <div className="flex items-center gap-2 flex-wrap mb-2.5">
        <span className="text-sm font-semibold text-foreground font-mono truncate">
          {tsym}
        </span>
        <span className="text-[11px] font-mono text-dimmer tabular-nums">
          ×{qty}
        </span>
        <span className="ml-auto">
          {filled ? (
            <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded-full border border-emerald-500/50 bg-emerald-500/10 text-emerald-300 text-[10px] font-mono font-bold uppercase tracking-wider">
              <ShieldCheck className="w-3 h-3 shrink-0" />
              Filled
            </span>
          ) : (
            <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded-full border border-amber-500/50 bg-amber-500/10 text-amber-300 text-[10px] font-mono font-bold uppercase tracking-wider">
              <Loader2 className="w-3 h-3 shrink-0 animate-spin" />
              Pending fill
            </span>
          )}
        </span>
      </div>

      {/* Levels grid */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-x-4 gap-y-2">
        <Metric
          label="Entry"
          value={entry != null ? fmtINR(entry, 2) : "—"}
        />
        <Metric
          label="Stop"
          value={stop != null ? fmtINR(stop, 2) : "—"}
          valueClass="text-danger"
          sub={stopPct != null ? fmtSignedPct(stopPct) : ""}
        />
        <Metric
          label="Target"
          value={target != null ? fmtINR(target, 2) : "—"}
          valueClass={target != null ? "text-emerald-300" : "text-dimmer"}
          sub={target != null && targetPct != null ? fmtSignedPct(targetPct) : ""}
        />
        <Metric
          label="Peak"
          value={peak != null ? fmtINR(peak, 2) : "—"}
          valueClass={peak != null ? "text-info" : "text-dimmer"}
        />
      </div>
    </div>
  );
}

export default function GuardPanel() {
  // Guard status comes from the shared LiveDataProvider (single 3s poll); this
  // panel no longer self-polls (it previously double-polled with the dashboard).
  const { guard, errors, refetch } = useLiveData();
  const [refreshing, setRefreshing] = useState(false);

  const status = guard;
  const loading = status == null && !errors.guard;
  const error = errors.guard
    ? (errors.guard?.response?.data?.detail ?? errors.guard?.message ?? "Failed to load guard status")
    : null;

  const fetchStatus = useCallback(async () => {
    setRefreshing(true);
    try {
      await refetch.guard();
    } finally {
      setRefreshing(false);
    }
  }, [refetch]);

  // ── Defensive derivation ──────────────────────────────────────────────────
  const armed = !!status?.armed;
  const mode = status?.mode;
  const guarded = Array.isArray(status?.guarded) ? status.guarded : [];
  // Trust the server count when sane; otherwise fall back to the list length.
  const rawCount = Number(status?.count);
  const count = Number.isFinite(rawCount) ? rawCount : guarded.length;

  return (
    <div className="rounded-lg border border-line bg-bg-1 overflow-hidden">
      {/* Header: status pill + count + manual refresh */}
      <div className="px-4 py-2.5 border-b border-line bg-bg-2/40 flex items-center gap-2 flex-wrap">
        <span className="text-sm font-semibold text-foreground">Software Guard</span>

        {/* ARMED / DRY-RUN pill */}
        {armed ? (
          <span className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full border border-danger/60 bg-danger/15 text-danger text-[10px] font-mono font-bold uppercase tracking-wider">
            <ShieldAlert className="w-3 h-3 shrink-0" />
            Armed · live auto-exit
          </span>
        ) : (
          <span className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full border border-amber-500/60 bg-amber-500/15 text-amber-300 text-[10px] font-mono font-bold uppercase tracking-wider">
            <ShieldCheck className="w-3 h-3 shrink-0" />
            Dry-run · logs only
          </span>
        )}

        {mode && (
          <span className="text-[10px] font-mono text-dimmer uppercase tracking-wider">
            {mode}
          </span>
        )}

        <span className="text-[10px] font-mono text-dim tabular-nums">
          {count} guarded
        </span>

        <button
          type="button"
          onClick={fetchStatus}
          disabled={refreshing}
          title="Refresh guard status"
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

      <div className="px-4 py-3 space-y-3">
        {/* Fetch error */}
        {error && (
          <div className="text-xs text-danger font-mono px-2 py-1 rounded border border-danger/30 bg-danger/10">
            {error}
          </div>
        )}

        {/* Loading (first load only) */}
        {loading && !status && !error && (
          <div className="flex items-center gap-2 text-xs text-dimmer font-mono py-6 justify-center">
            <Loader2 className="w-3.5 h-3.5 animate-spin" />
            Loading guard status…
          </div>
        )}

        {/* Empty state */}
        {!loading && count === 0 && guarded.length === 0 && !error && (
          <div className="py-6 text-center space-y-1.5">
            <div className="text-xs text-dimmer font-mono">
              No positions under software guard
            </div>
            <div className="text-[11px] text-dimmer font-mono leading-relaxed max-w-md mx-auto">
              Positions are auto-registered on a filled entry; the guard squares on
              stop/target via the margin-safe path (no resting SL).
            </div>
          </div>
        )}

        {/* Guarded rows */}
        {guarded.length > 0 && (
          <div className="space-y-2">
            {guarded.map((pos, i) => (
              <GuardRow key={pos?.tsym ?? i} pos={pos} />
            ))}
          </div>
        )}

        {/* Dry-run footer hint */}
        {!armed && (guarded.length > 0 || count > 0) && (
          <div className="flex items-start gap-1.5 text-[11px] font-mono text-dimmer pt-1 border-t border-line/60">
            <AlertTriangle className="w-3.5 h-3.5 shrink-0 text-amber-400 mt-0.5" />
            <span>
              Set <span className="text-dim font-semibold">LIVE_GUARD_ARMED=1</span> (env)
              + rebuild to transmit real auto-exits.
            </span>
          </div>
        )}
      </div>
    </div>
  );
}
