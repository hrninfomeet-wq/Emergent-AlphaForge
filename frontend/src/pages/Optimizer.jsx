import { Gauge } from "lucide-react";

export default function Optimizer() {
  return (
    <div className="space-y-3" data-testid="optimizer-page">
      <div className="rounded-lg border border-line bg-bg-1 p-8 text-center">
        <div className="w-12 h-12 mx-auto rounded-lg bg-bg-3 border border-line-strong flex items-center justify-center mb-3">
          <Gauge className="w-6 h-6 text-info" />
        </div>
        <div className="text-base font-semibold mb-1">Auto-Optimizer</div>
        <div className="text-[11px] font-mono text-info uppercase tracking-wider mb-3">Phase 3 · next up</div>
        <div className="text-sm text-dim max-w-2xl mx-auto leading-relaxed">
          One-click auto-optimization with <b className="text-foreground">Optuna Bayesian (TPE)</b>, <b className="text-foreground">Grid Search</b>, and <b className="text-foreground">Genetic (CMA-ES)</b> algorithms. Walk-forward by default. Per-instrument, per-mode, per-strategy isolation. Live progress bar, parameter importance, heatmaps for any 2 parameters, robustness scoring, top-N alternatives ranking. One-click “Apply best params as preset.” No manual tuning ever.
        </div>
      </div>
    </div>
  );
}
