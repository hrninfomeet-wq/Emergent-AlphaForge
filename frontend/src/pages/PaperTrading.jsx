import { Briefcase } from "lucide-react";

export default function PaperTrading() {
  return (
    <div className="space-y-3" data-testid="paper-trading-page">
      <ComingSoon
        title="Paper Trading Journal"
        phase="Phase 4"
        desc="Auto-deploy live signals as paper trades, track P&L in real-time, replay any historical day in fast-forward, and audit every triggered/skipped signal. Fully wired to the Upstox WebSocket tick stream."
      />
    </div>
  );
}

function ComingSoon({ title, phase, desc }) {
  return (
    <div className="rounded-lg border border-line bg-bg-1 p-8 text-center" data-testid="coming-soon">
      <div className="w-12 h-12 mx-auto rounded-lg bg-bg-3 border border-line-strong flex items-center justify-center mb-3">
        <Briefcase className="w-6 h-6 text-info" />
      </div>
      <div className="text-base font-semibold mb-1">{title}</div>
      <div className="text-[11px] font-mono text-info uppercase tracking-wider mb-3">{phase}</div>
      <div className="text-sm text-dim max-w-xl mx-auto leading-relaxed">{desc}</div>
    </div>
  );
}
