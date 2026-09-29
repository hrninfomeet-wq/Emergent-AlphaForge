import { Activity } from "lucide-react";
import { useLiveData } from "@/components/live/LiveDataProvider";
import { fmtINRSigned, colorPnL } from "@/lib/fmt";
import { greeksView, noteToneClass } from "@/lib/greeksView";

/**
 * GreeksCard — portfolio net delta + net theta across open live positions.
 *
 * Net Δ = ₹ P&L per 1 index point of underlying move; Net Θ = ₹/day time decay
 * (negative = the daily premium "rent" a buyer pays). Server-side Black-Scholes,
 * IV solved from the live GetQuotes premium. Informational only — the system
 * does not act on Greeks (exits are governed by premium stops + the OCO).
 *
 * What it may CLAIM is decided by lib/greeksView.js from the BROKER's position book:
 * "no positions" only when that book was read and is empty; an unreadable book is
 * "unknown" (—), and open broker positions the software guard is not watching are
 * called out. It never says "no positions" from the guard registry or a failed poll.
 */
export default function GreeksCard() {
  const { greeks, errors } = useLiveData();
  const view = greeksView(greeks, errors?.greeks);
  const { netDelta, netTheta } = view;

  return (
    <div className="rounded-lg border border-line bg-bg-2/40 px-4 py-3" data-testid="greeks-card" data-greeks-state={view.state}>
      <div className="flex items-center justify-between mb-2">
        <span className="inline-flex items-center gap-1.5 text-[11px] uppercase tracking-wider text-dimmer">
          <Activity className="w-3.5 h-3.5" /> Portfolio Greeks
        </span>
        {view.priced && (
          <span className="text-[10px] font-mono text-dimmer/70">{view.priced}</span>
        )}
      </div>
      <div className="grid grid-cols-2 gap-3 font-mono">
        <div>
          <div className="text-[10px] uppercase tracking-wider text-dimmer">Net Δ (₹/point)</div>
          <div className={`text-lg font-semibold ${netDelta === null ? "text-dimmer" : colorPnL(netDelta)}`}>
            {netDelta === null ? "—" : fmtINRSigned(netDelta)}
          </div>
        </div>
        <div>
          <div className="text-[10px] uppercase tracking-wider text-dimmer">Net Θ (₹/day)</div>
          <div className={`text-lg font-semibold ${netTheta === null ? "text-dimmer" : colorPnL(netTheta)}`}>
            {netTheta === null ? "—" : fmtINRSigned(netTheta)}
          </div>
        </div>
      </div>
      {view.notes.map((note) => (
        <div
          key={note.text}
          className={`text-[10px] mt-2 ${noteToneClass(note.tone)}`}
          data-testid="greeks-note"
          data-tone={note.tone}
        >
          {note.text}
        </div>
      ))}
    </div>
  );
}
