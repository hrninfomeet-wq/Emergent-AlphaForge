import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "@/lib/api";
import { Skeleton } from "@/components/ui/skeleton";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { toast } from "sonner";
import { fmtNum, isoToFull } from "@/lib/fmt";
import { RefreshCw, BookOpen, Search, X, Activity } from "lucide-react";

const STATES = ["WATCHING", "FORMING", "CONFIRMED", "TRIGGERED", "ACTIVE", "EXITED", "SKIPPED", "AUDITED"];

const STATE_STYLE = {
  CONFIRMED: "border-amber-500/40 text-amber-300",
  TRIGGERED: "border-info/40 text-info",
  ACTIVE: "border-emerald-500/40 text-emerald-300",
  EXITED: "border-emerald-500/40 text-emerald-300",
  AUDITED: "border-line text-dim",
  SKIPPED: "border-line text-dimmer",
};

/**
 * Signal Journal — the audit trail of deployment-generated signals.
 *
 * (The backtest run history moved to the Backtest Lab, where runs are produced.)
 * This page surfaces the forward-testing signal lifecycle: every CONFIRMED /
 * approved / skipped / blocked signal a deployment produced, with its full
 * audit context (strategy hash, regime, option contract, blockers, tracked).
 */
export default function SignalJournal() {
  const navigate = useNavigate();
  const [signals, setSignals] = useState([]);
  const [deployments, setDeployments] = useState([]);
  const [loading, setLoading] = useState(true);
  const [filter, setFilter] = useState("");
  const [stateFilter, setStateFilter] = useState("");

  const refresh = async () => {
    try {
      const [sig, dep] = await Promise.all([
        api.listSignals({ ...(stateFilter ? { state: stateFilter } : {}), limit: 200 }),
        api.listDeployments({ limit: 200 }).catch(() => ({ items: [] })),
      ]);
      // Only deployment-generated signals belong in this audit trail.
      setSignals((sig.items || []).filter((s) => s.deployment_id));
      setDeployments(dep.items || []);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { refresh(); /* eslint-disable-next-line react-hooks/exhaustive-deps */ }, [stateFilter]);

  const deploymentName = useMemo(() => {
    const map = {};
    for (const d of deployments) map[d.id] = d.name || d.id?.slice(0, 8);
    return map;
  }, [deployments]);

  const visible = signals.filter((s) => {
    if (!filter) return true;
    const q = filter.toLowerCase();
    return (
      (s.instrument || "").toLowerCase().includes(q) ||
      (s.strategy_id || "").toLowerCase().includes(q) ||
      (s.direction || "").toLowerCase().includes(q) ||
      (deploymentName[s.deployment_id] || "").toLowerCase().includes(q)
    );
  });

  if (loading) return <Skeleton className="h-96 bg-bg-1" />;

  return (
    <div className="space-y-3" data-testid="signal-journal-page">
      <div className="rounded-lg border border-line bg-bg-1">
        <div className="px-3 py-2 border-b border-line flex items-center gap-2 flex-wrap">
          <Activity className="w-4 h-4 text-info" />
          <div className="text-xs font-semibold uppercase tracking-wider text-dim">Deployment Signal Journal</div>
          <div className="text-[11px] text-dimmer ml-1">{visible.length} of {signals.length} signals</div>

          <select
            value={stateFilter}
            onChange={(e) => setStateFilter(e.target.value)}
            className="ml-auto h-7 rounded-md border border-input bg-bg-2 px-2 text-xs text-foreground"
            data-testid="journal-state-filter"
          >
            <option value="">All states</option>
            {STATES.map((s) => <option key={s} value={s}>{s}</option>)}
          </select>

          <div className="relative">
            <Search className="absolute left-2 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-dimmer pointer-events-none" />
            <Input
              value={filter}
              onChange={(e) => setFilter(e.target.value)}
              placeholder="Filter by instrument, strategy, deployment…"
              className="bg-bg-2 border-line h-7 text-xs pl-7 w-64"
              data-testid="journal-filter-input"
            />
            {filter && (
              <button onClick={() => setFilter("")} className="absolute right-1.5 top-1/2 -translate-y-1/2 text-dimmer hover:text-foreground">
                <X className="w-3 h-3" />
              </button>
            )}
          </div>

          <Button variant="ghost" size="sm" onClick={() => navigate("/live")} className="h-7 text-xs" data-testid="journal-go-live">
            Live Signals
          </Button>
          <Button variant="ghost" size="sm" onClick={refresh} className="h-7 text-xs" data-testid="journal-refresh-button">
            <RefreshCw className="w-3 h-3 mr-1" /> Refresh
          </Button>
        </div>

        <div className="overflow-x-auto">
          <table className="w-full text-xs" data-testid="signal-journal-table">
            <thead className="sticky top-0 bg-bg-2 z-10">
              <tr className="text-dim border-b border-line">
                <th className="text-left p-2">Updated</th>
                <th className="text-left p-2">Deployment</th>
                <th className="text-left p-2">Instr.</th>
                <th className="text-left p-2">Side</th>
                <th className="text-left p-2">State</th>
                <th className="text-right p-2">Score</th>
                <th className="text-left p-2">Bar (IST)</th>
                <th className="text-left p-2">Strategy</th>
                <th className="text-left p-2">Regime</th>
                <th className="text-left p-2">Contract</th>
                <th className="text-left p-2">Tracked</th>
                <th className="text-left p-2">Blockers</th>
              </tr>
            </thead>
            <tbody>
              {visible.length === 0 && (
                <tr><td colSpan="12" className="p-6 text-center text-dimmer">
                  {signals.length === 0 ? "No deployment signals yet. Create a deployment in Live Signals to start forward testing." : "No signals match filter."}
                </td></tr>
              )}
              {visible.map((s) => {
                const ctx = s.context || {};
                const candle = ctx.candle || {};
                const contract = s.option_contract || {};
                const blockers = s.blockers || [];
                return (
                  <tr key={s.id} className="border-b border-line hover:bg-bg-2" data-testid="signal-journal-row">
                    <td className="p-2 font-mono text-dim">{isoToFull(s.updated_at)}</td>
                    <td className="p-2 font-medium truncate max-w-[140px]" title={deploymentName[s.deployment_id] || s.deployment_id}>
                      {deploymentName[s.deployment_id] || s.deployment_id?.slice(0, 8)}
                    </td>
                    <td className="p-2 font-mono">{s.instrument}</td>
                    <td className="p-2">
                      <span className={`font-mono ${s.direction === "CE" ? "text-emerald-400" : "text-red-400"}`}>{s.direction}</span>
                    </td>
                    <td className="p-2">
                      <span className={`text-[10px] px-1.5 py-0.5 rounded border font-mono ${STATE_STYLE[s.state] || "border-line text-dim"}`}>{s.state}</span>
                    </td>
                    <td className="p-2 font-mono text-right">{fmtNum(s.confidence)}</td>
                    <td className="p-2 font-mono text-dim">{candle.ist_time || "—"}</td>
                    <td className="p-2 font-mono text-dim truncate max-w-[150px]" title={`${s.strategy_id} ${ctx.strategy_hash || ""}`}>
                      {s.strategy_id}{ctx.strategy_hash ? ` · ${String(ctx.strategy_hash).slice(0, 8)}` : ""}
                    </td>
                    <td className="p-2 text-dim">{ctx.regime || "—"}</td>
                    <td className="p-2 font-mono text-dim">
                      {contract.strike ? `${contract.strike} ${contract.side || ""}` : "—"}
                    </td>
                    <td className="p-2">
                      <span className={`text-[10px] font-mono ${s.tracked_for_pnl ? "text-emerald-400" : "text-dimmer"}`}>
                        {s.tracked_for_pnl ? "yes" : "no"}
                      </span>
                    </td>
                    <td className="p-2 text-dimmer truncate max-w-[200px]" title={blockers.join("; ")}>
                      {blockers.length ? blockers.join("; ") : "—"}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
        <div className="px-3 py-1.5 border-t border-line text-[10px] text-dimmer">
          This is the forward-testing audit trail. Approve / skip signals in Live Signals. Backtest run history is in the Backtest Lab.
        </div>
      </div>
    </div>
  );
}
