import { Activity } from "lucide-react";

/**
 * Market Pulse — structure + multi-timeframe trend + S/R range bar.
 *
 * PHASE 1: renders a "coming online" placeholder. PHASE 2 wires this to
 * GET /market/analysis (structure/regime bucket + confidence + why, per-timeframe
 * trend, and the S/R range bar from levels.position_in_range). Kept as its own
 * component so the Phase-2 change is isolated.
 */
export default function MarketPulse({ analysis }) {
  if (!analysis) {
    return (
      <div className="rounded-lg border border-line bg-bg-1 px-4 py-5 flex items-center gap-3 text-dim">
        <Activity className="w-4 h-4 text-dimmer" />
        <div className="text-xs">
          <div className="font-semibold text-foreground">Market Pulse</div>
          <div className="text-dimmer">Regime, multi-timeframe trend &amp; S/R come online with the analysis engine.</div>
        </div>
      </div>
    );
  }
  return null; // Phase 2 fills this in.
}
