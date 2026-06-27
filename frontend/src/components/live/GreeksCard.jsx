import { Activity } from "lucide-react";
import { useLiveData } from "@/components/live/LiveDataProvider";
import { fmtINRSigned, colorPnL } from "@/lib/fmt";

/**
 * GreeksCard — portfolio net delta + net theta across open live positions.
 *
 * Net Δ = ₹ P&L per 1 index point of underlying move; Net Θ = ₹/day time decay
 * (negative = the daily premium "rent" a buyer pays). Server-side Black-Scholes,
 * IV solved from the live GetQuotes premium. Informational only — the system
 * does not act on Greeks (exits are governed by premium stops + the OCO).
 */
export default function GreeksCard() {
  const { greeks } = useLiveData();
  const loading = greeks == null;
  const netDelta = Number(greeks?.net_delta_rupees_per_point);
  const netTheta = Number(greeks?.net_theta_rupees_per_day);
  const nComputed = greeks?.n_computed ?? 0;
  const nSkipped = greeks?.n_skipped ?? 0;
  const total = nComputed + nSkipped;

  return (
    <div className="rounded-lg border border-line bg-bg-2/40 px-4 py-3">
      <div className="flex items-center justify-between mb-2">
        <span className="inline-flex items-center gap-1.5 text-[11px] uppercase tracking-wider text-dimmer">
          <Activity className="w-3.5 h-3.5" /> Portfolio Greeks
        </span>
        {total > 0 && (
          <span className="text-[10px] font-mono text-dimmer/70">{nComputed} of {total} priced</span>
        )}
      </div>
      <div className="grid grid-cols-2 gap-3 font-mono">
        <div>
          <div className="text-[10px] uppercase tracking-wider text-dimmer">Net Δ (₹/point)</div>
          <div className={`text-lg font-semibold ${loading || !Number.isFinite(netDelta) ? "text-dimmer" : colorPnL(netDelta)}`}>
            {loading || !Number.isFinite(netDelta) ? "—" : fmtINRSigned(netDelta)}
          </div>
        </div>
        <div>
          <div className="text-[10px] uppercase tracking-wider text-dimmer">Net Θ (₹/day)</div>
          <div className={`text-lg font-semibold ${loading || !Number.isFinite(netTheta) ? "text-dimmer" : colorPnL(netTheta)}`}>
            {loading || !Number.isFinite(netTheta) ? "—" : fmtINRSigned(netTheta)}
          </div>
        </div>
      </div>
      {!loading && nComputed === 0 && total === 0 && (
        <div className="text-[10px] text-dimmer/70 mt-2">No open live positions.</div>
      )}
    </div>
  );
}
