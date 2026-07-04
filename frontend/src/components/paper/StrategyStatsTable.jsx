import { Fragment, useEffect, useState } from "react";
import { fmtINRSigned, fmtINR, fmtPct, fmtNum, fmtSigned, colorPnL } from "@/lib/fmt";
import { api } from "@/lib/api";
import ExitReasonBreakdown from "./ExitReasonBreakdown";
import { ChevronDown, ChevronRight } from "lucide-react";

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

const PERIOD_TABS = [
  { key: "day", label: "Daily" },
  { key: "week", label: "Weekly" },
  { key: "month", label: "Monthly" },
  { key: "year", label: "Yearly" },
];

function DeploymentStatsDrawer({ deploymentId }) {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [period, setPeriod] = useState("day");

  useEffect(() => {
    let alive = true;
    setData(null);
    setError(null);
    api.paperDeploymentStats(deploymentId)
      .then((d) => { if (alive) setData(d); })
      .catch((e) => { if (alive) setError(e.response?.data?.detail || e.message); });
    return () => { alive = false; };
  }, [deploymentId]);

  if (error) return <div className="p-3 text-[11px] text-danger">Failed to load stats: {error}</div>;
  if (!data) return <div className="p-3 text-[11px] text-dimmer">Loading deployment statistics…</div>;
  const rows = data.periods?.[period] || [];
  return (
    <div className="p-3 bg-bg-0 space-y-2" data-testid="deployment-stats-drawer">
      <div className="flex items-center gap-1">
        {PERIOD_TABS.map((t) => (
          <button key={t.key} onClick={() => setPeriod(t.key)}
            className={`h-6 px-2 rounded text-[11px] border ${period === t.key ? "border-info text-info bg-info/10" : "border-line text-dim hover:text-foreground"}`}
            data-testid={`deployment-stats-tab-${t.key}`}>
            {t.label}
          </button>
        ))}
        <span className="ml-auto text-[10px] text-dimmer">
          Capital = starting capital {fmtINR(data.starting_capital)} + this deployment's own realized P&L
        </span>
      </div>
      {rows.length === 0 ? (
        <div className="text-[11px] text-dimmer">No closed trades yet for this deployment.</div>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-[11px]">
            <thead className="text-dim">
              <tr className="border-b border-line">
                <th className="text-left p-1.5">Period</th>
                <th className="text-right p-1.5">Trades</th>
                <th className="text-right p-1.5">Net P&L</th>
                <th className="text-right p-1.5">Min P&L</th>
                <th className="text-right p-1.5">Max P&L</th>
                <th className="text-right p-1.5">Min Capital</th>
                <th className="text-right p-1.5">Max Capital</th>
                <th className="text-right p-1.5">Max Drawdown</th>
                <th className="text-right p-1.5">Max Deployed</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.bucket} className="border-b border-line/60" data-testid="deployment-stats-row">
                  <td className="p-1.5 font-mono">{r.bucket}</td>
                  <td className="p-1.5 text-right font-mono text-dim">{r.trades}</td>
                  <td className={`p-1.5 text-right font-mono ${colorPnL(r.net_pnl)}`}>{fmtINRSigned(r.net_pnl)}</td>
                  <td className={`p-1.5 text-right font-mono ${colorPnL(r.pnl_min)}`}>{fmtINRSigned(r.pnl_min)}</td>
                  <td className={`p-1.5 text-right font-mono ${colorPnL(r.pnl_max)}`}>{fmtINRSigned(r.pnl_max)}</td>
                  <td className="p-1.5 text-right font-mono">{r.capital_min == null ? "—" : fmtINR(r.capital_min)}</td>
                  <td className="p-1.5 text-right font-mono">{r.capital_max == null ? "—" : fmtINR(r.capital_max)}</td>
                  <td className={`p-1.5 text-right font-mono ${colorPnL(r.max_drawdown_value)}`}>{fmtINRSigned(r.max_drawdown_value)}</td>
                  <td className="p-1.5 text-right font-mono text-dim">{fmtINR(r.max_deployed_value)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

export default function StrategyStatsTable({ stats, onFilterStrategy }) {
  const [expanded, setExpanded] = useState(null); // deployment_id or null
  if (!stats || stats.length === 0) {
    return <div className="rounded-lg border border-line bg-bg-1 p-3 text-[11px] text-dimmer">No strategy activity yet.</div>;
  }
  return (
    <div className="rounded-lg border border-line bg-bg-1 overflow-x-auto" data-testid="paper-strategy-stats">
      <table className="w-full text-xs">
        <thead className="bg-bg-2 text-dim">
          <tr className="border-b border-line">
            <th className="w-6 p-2" />
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
          {stats.map((s) => {
            const depId = s.deployment_id || null;
            const isOpen = depId && expanded === depId;
            return (
              <Fragment key={s.strategy_id + (depId || "")}>
                <tr className="border-b border-line hover:bg-bg-2 cursor-pointer"
                  onClick={() => onFilterStrategy?.(s.strategy_id)} data-testid="paper-strategy-row">
                  <td className="p-2" onClick={(e) => e.stopPropagation()}>
                    {depId && (
                      <button onClick={() => setExpanded(isOpen ? null : depId)}
                        className="text-dim hover:text-foreground" title="Day / week / month / year statistics"
                        data-testid="deployment-stats-toggle">
                        {isOpen ? <ChevronDown className="w-3.5 h-3.5" /> : <ChevronRight className="w-3.5 h-3.5" />}
                      </button>
                    )}
                  </td>
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
                {isOpen && (
                  <tr>
                    <td colSpan={12} className="p-0 border-b border-line">
                      <DeploymentStatsDrawer deploymentId={depId} />
                    </td>
                  </tr>
                )}
              </Fragment>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
