import { fmtINRSigned, fmtPct, fmtNum, fmtSigned, colorPnL } from "@/lib/fmt";
import ExitReasonBreakdown from "./ExitReasonBreakdown";

function DriftChip({ drift }) {
  const d = drift || {};
  if (d.state === "no_baseline") return <span className="text-[10px] text-dimmer">no baseline</span>;
  if (d.state === "insufficient_sample") return <span className="text-[10px] text-dimmer" title="Needs ≥10 complete forward sessions">insufficient sample</span>;
  if (d.state !== "ok") return <span className="text-[10px] text-dimmer">—</span>;
  const wrUp = (d.win_rate_delta ?? 0) >= 0;
  const avUp = (d.avg_delta ?? 0) >= 0;
  const cls = (wrUp && avUp) ? "bg-emerald-500/10 text-emerald-300 border-emerald-500/30"
    : (!wrUp && !avUp) ? "bg-rose-500/10 text-rose-300 border-rose-500/30"
    : "bg-amber-500/10 text-amber-300 border-amber-500/30";
  return (
    <span className={`text-[10px] font-mono px-1.5 py-0.5 rounded border ${cls}`}
      title="Live (session-gated) vs pinned backtest (option-₹)">
      WR {fmtNum(d.live_win_rate, 0)} vs {fmtNum(d.base_win_rate, 0)} {wrUp ? "▲" : "▼"} · ₹/t {fmtSigned(d.live_avg, 0)} vs {fmtSigned(d.base_avg, 0)} {avUp ? "▲" : "▼"}
    </span>
  );
}

export default function StrategyStatsTable({ stats, onFilterStrategy }) {
  if (!stats || stats.length === 0) {
    return <div className="rounded-lg border border-line bg-bg-1 p-3 text-[11px] text-dimmer">No strategy activity yet.</div>;
  }
  return (
    <div className="rounded-lg border border-line bg-bg-1 overflow-x-auto" data-testid="paper-strategy-stats">
      <table className="w-full text-xs">
        <thead className="bg-bg-2 text-dim">
          <tr className="border-b border-line">
            <th className="text-left p-2">Strategy</th>
            <th className="text-right p-2">Net P&amp;L</th>
            <th className="text-right p-2">Trades</th>
            <th className="text-right p-2">Win%</th>
            <th className="text-right p-2">PF</th>
            <th className="text-right p-2">Avg R</th>
            <th className="text-left p-2">vs backtest</th>
            <th className="text-left p-2">Exit mix</th>
            <th className="text-right p-2">Expectancy</th>
            <th className="text-right p-2">Open</th>
            <th className="text-right p-2">Contrib.</th>
          </tr>
        </thead>
        <tbody>
          {stats.map((s) => (
            <tr key={s.strategy_id + (s.deployment_id || "")} className="border-b border-line hover:bg-bg-2 cursor-pointer"
              onClick={() => onFilterStrategy?.(s.strategy_id)} data-testid="paper-strategy-row">
              <td className="p-2">
                <div className="font-medium truncate max-w-[200px]" title={s.deployment_name || s.strategy_id}>{s.deployment_name || s.strategy_id}</div>
                <div className="text-dimmer truncate max-w-[200px]">{s.strategy_id}</div>
              </td>
              <td className={`p-2 text-right font-mono tabular-nums ${colorPnL(s.net_pnl)}`}>{fmtINRSigned(s.net_pnl)}</td>
              <td className="p-2 text-right font-mono text-dim">{s.closed_trades}</td>
              <td className="p-2 text-right font-mono">{s.win_rate == null ? "—" : fmtPct(s.win_rate, 0)}</td>
              <td className="p-2 text-right font-mono">{s.profit_factor == null ? "—" : (s.profit_factor === "Infinity" || s.profit_factor === Infinity ? "∞" : fmtNum(s.profit_factor, 2))}</td>
              <td className={`p-2 text-right font-mono ${colorPnL(s.avg_r)}`}>{s.avg_r == null ? "—" : fmtSigned(s.avg_r, 2)}</td>
              <td className="p-2"><DriftChip drift={s.drift} /></td>
              <td className="p-2"><ExitReasonBreakdown breakdown={{ pct: s.exit_mix }} variant="compact" /></td>
              <td className={`p-2 text-right font-mono ${colorPnL(s.expectancy)}`}>{s.expectancy == null ? "—" : fmtINRSigned(s.expectancy)}</td>
              <td className="p-2 text-right font-mono text-dim">{s.open_count}{s.open_count ? ` · ${fmtINRSigned(s.open_mtm)}` : ""}</td>
              <td className="p-2 text-right font-mono text-dim">{s.contribution_pct == null ? "—" : `${fmtNum(s.contribution_pct, 0)}%`}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
