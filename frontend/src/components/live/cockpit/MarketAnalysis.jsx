import { BarChart3 } from "lucide-react";

/**
 * Market Analysis — PCR, max-pain, IV rank, ATM straddle, net greeks + ATM option
 * chain.
 *
 * PHASE 1: "coming online" placeholder. PHASE 2 wires the analytics tiles + chain
 * to GET /market/analysis (options.*) and the existing option-chain source.
 */
export default function MarketAnalysis({ analysis }) {
  if (!analysis) {
    return (
      <div className="rounded-lg border border-line bg-bg-1 px-4 py-5 flex items-center gap-3 text-dim">
        <BarChart3 className="w-4 h-4 text-dimmer" />
        <div className="text-xs">
          <div className="font-semibold text-foreground">Market Analysis</div>
          <div className="text-dimmer">PCR, max-pain, IV rank, ATM straddle &amp; the option chain come online with the analysis engine.</div>
        </div>
      </div>
    );
  }
  return null; // Phase 2 fills this in.
}
