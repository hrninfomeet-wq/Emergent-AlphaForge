import { useEffect, useState } from "react";
import { api } from "@/lib/api";

/**
 * Where a strategy has actually got to: authored → backtested → optimized →
 * preset saved → paper → live.
 *
 * `GET /strategies/{id}/pipeline` was built for exactly this ("Powers the
 * StrategyCard stage chips", per its own docstring) and then had no frontend
 * caller, so the Library stayed a flat list with no sense of progress — the user
 * had to remember which strategies they had already tested or deployed.
 *
 * Reached stages are shown solid; unreached ones stay dim, so the card reads as a
 * path rather than a set of unrelated badges. Counts are surfaced in the tooltip
 * rather than the label to keep the row compact.
 */
const STAGES = [
  ["backtested", "backtested", (p) => p?.backtests?.count],
  ["optimized", "optimized", (p) => p?.optimizations?.count],
  ["preset_saved", "preset", (p) => p?.presets?.count],
  ["paper", "paper", (p) => p?.deployments?.count],
  ["live", "live", (p) => p?.live_ever_count],
];

export default function PipelineChips({ strategyId }) {
  const [pipe, setPipe] = useState(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let alive = true;
    api.getStrategyPipeline(strategyId)
      .then((d) => { if (alive) setPipe(d); })
      // A failed read must not render an all-dim row that looks like "never
      // tested" — say nothing instead of saying something false.
      .catch(() => { if (alive) setFailed(true); });
    return () => { alive = false; };
  }, [strategyId]);

  if (failed || !pipe) return null;

  const currentlyLive = Number(pipe?.live_armed_count || 0) > 0;

  return (
    <div className="flex items-center gap-1 flex-wrap mb-2" data-testid={`pipeline-chips-${strategyId}`}>
      <span className="text-[10px] uppercase tracking-wider text-dimmer mr-0.5">Progress</span>
      {STAGES.map(([key, label, countOf]) => {
        const reached = pipe?.stages?.[key] === true;
        const n = countOf(pipe);
        const isLive = key === "live";
        const cls = !reached
          ? "border-line bg-bg-2 text-dimmer"
          : isLive && currentlyLive
            ? "border-danger/50 bg-danger/15 text-danger font-semibold"
            : isLive
              ? "border-warning/40 bg-warning/10 text-warning"
              : "border-success/40 bg-success/10 text-success";
        return (
          <span
            key={key}
            className={`text-[10px] px-1.5 py-0.5 rounded border ${cls}`}
            title={reached
              ? `${label}: ${n ?? 0}${isLive && currentlyLive ? " · CURRENTLY LIVE" : ""}`
              : `not ${label} yet`}
            data-testid={`pipeline-chip-${key}`}
          >
            {label}
          </span>
        );
      })}
    </div>
  );
}
