import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import { fmtINRSigned, fmtNum, fmtPct, colorPnL } from "@/lib/fmt";
import { BarChart3, RefreshCw } from "lucide-react";

/**
 * LiveTradeStats — analysis over the journaled live_trades (close-loop realized
 * P&L): period P&L, win rate, profit factor + per-strategy breakdown.
 *
 * Fetches on mount + manual refresh (no interval: history changes only when a
 * live trade closes, and the page's live cadence belongs to LiveDataProvider).
 */
function Stat({ label, value, tone }) {
  return (
    <div>
      <div className="text-[10px] uppercase tracking-wider text-dimmer">{label}</div>
      <div className={`text-sm font-mono font-semibold ${tone != null ? colorPnL(tone) : "text-foreground"}`}>{value}</div>
    </div>
  );
}

export default function LiveTradeStats() {
  const [data, setData] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  const load = useCallback(async () => {
    setBusy(true);
    try {
      setData(await api.liveTradeStats());
      setError(null);
    } catch (e) {
      setError(e.response?.data?.detail || e.message);
    } finally {
      setBusy(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const p = data?.period_pnl || {};
  const strategies = data?.per_strategy || [];
  return (
    <div className="rounded-lg border border-line bg-bg-1" data-testid="live-trade-stats">
      <div className="px-3 py-2 border-b border-line flex items-center gap-2">
        <BarChart3 className="w-4 h-4 text-info" />
        <div className="text-[11px] font-semibold uppercase tracking-wider text-dim">Live Trade Statistics</div>
        <span className="text-[10px] text-dimmer">
          journaled close-loop history · {data ? `${data.closed_count} closed of ${data.trade_count}` : "…"}
        </span>
        <button onClick={load} disabled={busy} className="ml-auto text-dim hover:text-foreground"
          title="Refresh statistics" data-testid="live-trade-stats-refresh">
          <RefreshCw className={`w-3.5 h-3.5 ${busy ? "animate-spin" : ""}`} />
        </button>
      </div>
      {error ? (
        <div className="p-3 text-[11px] text-danger">Failed to load: {error}</div>
      ) : !data ? (
        <div className="p-3 text-[11px] text-dimmer">Loading…</div>
      ) : (
        <div className="p-3 space-y-3">
          <div className="grid grid-cols-3 sm:grid-cols-6 gap-x-5 gap-y-2">
            <Stat label="Today" value={fmtINRSigned(p.today)} tone={p.today} />
            <Stat label="Week" value={fmtINRSigned(p.week)} tone={p.week} />
            <Stat label="Month" value={fmtINRSigned(p.month)} tone={p.month} />
            <Stat label="Lifetime" value={fmtINRSigned(p.lifetime)} tone={p.lifetime} />
            <Stat label="Win rate" value={p.win_rate == null ? "—" : fmtPct(p.win_rate, 0)} />
            <Stat label="Profit factor" value={p.profit_factor == null ? "—" : fmtNum(p.profit_factor, 2)} />
          </div>
          {strategies.length > 0 && (
            <div className="overflow-x-auto">
              <table className="w-full text-[11px]">
                <thead className="text-dim">
                  <tr className="border-b border-line">
                    <th className="text-left p-1.5">Strategy</th>
                    <th className="text-right p-1.5">Net P&L</th>
                    <th className="text-right p-1.5">Closed</th>
                    <th className="text-right p-1.5">Win%</th>
                    <th className="text-right p-1.5">Expectancy</th>
                    <th className="text-right p-1.5">Open</th>
                  </tr>
                </thead>
                <tbody>
                  {strategies.map((s) => (
                    <tr key={s.strategy_id + (s.deployment_id || "")} className="border-b border-line/60"
                      data-testid="live-trade-stats-row">
                      <td className="p-1.5">
                        <div className="font-medium truncate max-w-[220px]" title={s.deployment_name || s.strategy_id}>
                          {s.deployment_name || s.strategy_id}
                        </div>
                      </td>
                      <td className={`p-1.5 text-right font-mono ${colorPnL(s.net_pnl)}`}>{fmtINRSigned(s.net_pnl)}</td>
                      <td className="p-1.5 text-right font-mono text-dim">{s.closed_trades}</td>
                      <td className="p-1.5 text-right font-mono">{s.win_rate == null ? "—" : fmtPct(s.win_rate, 0)}</td>
                      <td className={`p-1.5 text-right font-mono ${colorPnL(s.expectancy)}`}>{s.expectancy == null ? "—" : fmtINRSigned(s.expectancy)}</td>
                      <td className="p-1.5 text-right font-mono text-dim">{s.open_count}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
