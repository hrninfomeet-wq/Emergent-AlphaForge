import { Activity } from "lucide-react";

export default function LiveSignals() {
  return (
    <div className="space-y-3" data-testid="live-signals-page">
      <div className="rounded-lg border border-line bg-bg-1 p-8 text-center">
        <div className="w-12 h-12 mx-auto rounded-lg bg-bg-3 border border-line-strong flex items-center justify-center mb-3">
          <Activity className="w-6 h-6 text-info" />
        </div>
        <div className="text-base font-semibold mb-1">Live Signal Console</div>
        <div className="text-[11px] font-mono text-info uppercase tracking-wider mb-3">Phase 4 · Upstox WebSocket</div>
        <div className="text-sm text-dim max-w-2xl mx-auto leading-relaxed">
          Sub-second tick stream from Upstox WS. Per-tick strategy evaluation with discipline filters (cooldown, bar-close gate, daily caps). Live signal cards with full context: entry, target, stop, time stop, probability distribution, regime, India VIX, news risk, invalidation. One-click “Take / Skip / Deploy to Paper”. Full audit trail of every signal.
        </div>
      </div>
    </div>
  );
}
