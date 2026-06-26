import { useCallback, useEffect, useMemo, useState } from "react";
import { RefreshCw, Zap } from "lucide-react";
import { api } from "@/lib/api";
import LiveBlotter from "@/components/live/LiveBlotter";

/**
 * JournalLiveLane — the LIVE (deployment) lane of the Signal Journal.
 *
 * The journal's Paper lane joins each signal to its PAPER trade (/signals/enriched).
 * A live-auto-traded signal has NO paper trade, so it can't show premium/P&L
 * there. This lane instead surfaces the deployment's LIVE trades from
 * GET /live-broker/blotter — the same deployment-attributed, broker-truth feed
 * the Live Trading page uses (reusing <LiveBlotter/> verbatim).
 *
 * Honest by construction: live_trades has no close-loop, so P&L is the broker's
 * live MTM and squared/superseded rows read FLAT with no realized P&L (the
 * blotter already encodes this). Filtering is client-side by deployment so it
 * stays in sync with the page's shared deployment filter; no backend change.
 */
const POLL_MS = 30_000;

export default function JournalLiveLane({ deployments = [], deploymentId = "", onSelectDeployment }) {
  const [blotter, setBlotter] = useState(null); // { rows, count } | null
  const [refreshing, setRefreshing] = useState(false);

  const fetchBlotter = useCallback(async () => {
    setRefreshing(true);
    try {
      const d = await api.getLiveBlotter(200);
      setBlotter(d);
    } catch {
      setBlotter((prev) => prev ?? { rows: [], count: 0 });
    } finally {
      setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    fetchBlotter();
    const id = window.setInterval(fetchBlotter, POLL_MS);
    return () => window.clearInterval(id);
  }, [fetchBlotter]);

  // Client-side deployment filter (the feed has no server-side filters), kept in
  // sync with the page's shared deployment filter.
  const rows = useMemo(() => {
    const all = blotter?.rows;
    if (!Array.isArray(all)) return all; // null/undefined → LiveBlotter shows loading
    if (!deploymentId) return all;
    return all.filter((r) => String(r?.deployment_id || "") === String(deploymentId));
  }, [blotter, deploymentId]);

  return (
    <div className="rounded-lg border border-line bg-bg-1">
      {/* Lane header: deployment filter + refresh */}
      <div className="px-3 py-2 border-b border-line flex items-center gap-2 flex-wrap">
        <Zap className="w-4 h-4 text-danger" />
        <div className="text-xs font-semibold uppercase tracking-wider text-dim">Live Deployment Trades</div>

        <select
          value={deploymentId}
          onChange={(e) => onSelectDeployment?.(e.target.value)}
          className="ml-1 h-7 rounded-md border border-line bg-bg-2 text-xs px-2 text-dim"
          data-testid="journal-live-deployment-filter"
        >
          <option value="">All deployments</option>
          {deployments.map((d) => (
            <option key={d.id} value={d.id}>
              {d.name || d.strategy_id || d.id}
            </option>
          ))}
        </select>

        <button
          type="button"
          onClick={fetchBlotter}
          disabled={refreshing}
          className="ml-auto inline-flex items-center gap-1 h-7 px-2 rounded-md border border-line bg-bg-2 text-xs text-dim hover:bg-bg-3 disabled:opacity-60"
          data-testid="journal-live-refresh"
        >
          <RefreshCw className={`w-3 h-3 ${refreshing ? "animate-spin" : ""}`} /> Refresh
        </button>
      </div>

      <div className="p-3">
        <LiveBlotter rows={rows} />
      </div>

      <div className="px-3 pb-3 -mt-1 text-[10px] text-dimmer">
        LIVE rows show the broker&apos;s live MTM (urmtom + rpnl); CLOSED rows show the
        realized P&amp;L journaled by the software-guard / stop close-loop (exit price is
        the guard&apos;s last broker mark, an estimate — not a confirmed fill). Source: the
        deployment&apos;s auto-placed live orders, attributed by strategy.
      </div>
    </div>
  );
}
