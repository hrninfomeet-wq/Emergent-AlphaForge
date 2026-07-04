import { Fragment, useEffect, useState } from "react";
import {
  CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";
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

function DeploymentBucketChart({ series, period, bucket, startingCapital }) {
  const points = [];
  let cum = 0;
  for (const p of series || []) {
    if (p[period] !== bucket) continue;
    cum += Number(p.pnl || 0);
    points.push({
      n: points.length + 1,
      cumPnl: Math.round(cum * 100) / 100,
      account: Math.round((startingCapital + cum) * 100) / 100,
    });
  }
  if (points.length < 2) {
    return <div className="text-[11px] text-dimmer py-3">Not enough closed trades in {bucket} to chart.</div>;
  }
  return (
    <div className="h-[180px]" data-testid="deployment-stats-chart">
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={points} margin={{ top: 6, right: 8, bottom: 2, left: 8 }}>
          <CartesianGrid stroke="var(--color-line)" strokeDasharray="3 3" vertical={false} />
          <XAxis dataKey="n" tick={{ fontSize: 10 }} stroke="var(--color-dimmer)"
            label={{ value: "Trade # (chronological)", position: "insideBottom", offset: -1, fontSize: 9 }} />
          <YAxis yAxisId="pnl" tick={{ fontSize: 10 }} stroke="var(--color-info)" width={62}
            tickFormatter={(v) => `${v >= 0 ? "" : "-"}₹${Math.abs(v) >= 1000 ? `${(Math.abs(v) / 1000).toFixed(1)}k` : Math.abs(v)}`} />
          <YAxis yAxisId="acct" orientation="right" tick={{ fontSize: 10 }} stroke="var(--color-success)" width={62}
            domain={["auto", "auto"]}
            tickFormatter={(v) => `₹${(v / 1000).toFixed(0)}k`} />
          <Tooltip
            contentStyle={{ background: "var(--color-bg-2)", border: "1px solid var(--color-line)", fontSize: 11 }}
            formatter={(v, name) => [fmtINRSigned(v), name === "cumPnl" ? "Cumulative P&L" : "Account value"]}
            labelFormatter={(n) => `Trade #${n}`} />
          <Line yAxisId="pnl" type="monotone" dataKey="cumPnl" stroke="var(--color-info)" dot={false} strokeWidth={1.5} />
          <Line yAxisId="acct" type="monotone" dataKey="account" stroke="var(--color-success)" dot={false} strokeWidth={1.5} />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}

function DeploymentStatsDrawer({ deploymentId }) {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [period, setPeriod] = useState("day");
  const [bucket, setBucket] = useState(null); // selected bucket for the chart

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
  const activeBucket = bucket && rows.some((r) => r.bucket === bucket)
    ? bucket
    : rows[0]?.bucket || null;
  return (
    <div className="p-3 bg-bg-0 space-y-2" data-testid="deployment-stats-drawer">
      <div className="flex items-center gap-1">
        {PERIOD_TABS.map((t) => (
          <button key={t.key} onClick={() => { setPeriod(t.key); setBucket(null); }}
            className={`h-6 px-2 rounded text-[11px] border ${period === t.key ? "border-info text-info bg-info/10" : "border-line text-dim hover:text-foreground"}`}
            data-testid={`deployment-stats-tab-${t.key}`}>
            {t.label}
          </button>
        ))}
        <span className="ml-auto text-[10px] text-dimmer">
          Capital = starting capital {fmtINR(data.starting_capital)} (editable on the account card) + this deployment's own realized P&L
        </span>
      </div>
      {rows.length === 0 ? (
        <div className="text-[11px] text-dimmer">No closed trades yet for this deployment.</div>
      ) : (
        <>
          {activeBucket && (
            <div className="rounded border border-line bg-bg-1 p-2">
              <div className="text-[10px] text-dimmer mb-1">
                <span className="text-info">Cumulative P&L</span> (left) · <span className="text-success">Account value</span> (right) — per trade in <span className="font-mono text-dim">{activeBucket}</span>. Click a row below to chart another period.
              </div>
              <DeploymentBucketChart series={data.trade_series} period={period}
                bucket={activeBucket} startingCapital={Number(data.starting_capital || 0)} />
            </div>
          )}
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
                  <th className="text-right p-1.5" title="Peak concurrent entry premium at risk">Max Deployed</th>
                  <th className="text-right p-1.5" title="Minimum balance the demat account needed at the period's start to fund every trade in it (concurrent premium + losses already taken)">Required Capital</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => (
                  <tr key={r.bucket}
                    onClick={() => setBucket(r.bucket)}
                    className={`border-b border-line/60 cursor-pointer hover:bg-bg-2 ${r.bucket === activeBucket ? "bg-info/5" : ""}`}
                    data-testid="deployment-stats-row">
                    <td className="p-1.5 font-mono">{r.bucket}</td>
                    <td className="p-1.5 text-right font-mono text-dim">{r.trades}</td>
                    <td className={`p-1.5 text-right font-mono ${colorPnL(r.net_pnl)}`}>{fmtINRSigned(r.net_pnl)}</td>
                    <td className={`p-1.5 text-right font-mono ${colorPnL(r.pnl_min)}`}>{fmtINRSigned(r.pnl_min)}</td>
                    <td className={`p-1.5 text-right font-mono ${colorPnL(r.pnl_max)}`}>{fmtINRSigned(r.pnl_max)}</td>
                    <td className="p-1.5 text-right font-mono">{r.capital_min == null ? "—" : fmtINR(r.capital_min)}</td>
                    <td className="p-1.5 text-right font-mono">{r.capital_max == null ? "—" : fmtINR(r.capital_max)}</td>
                    <td className={`p-1.5 text-right font-mono ${colorPnL(r.max_drawdown_value)}`}>{fmtINRSigned(r.max_drawdown_value)}</td>
                    <td className="p-1.5 text-right font-mono text-dim">{fmtINR(r.max_deployed_value)}</td>
                    <td className="p-1.5 text-right font-mono font-semibold">{fmtINR(r.required_capital)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
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
            <th className="text-right p-2" title="Statutory charges (brokerage/STT/exchange/GST/SEBI/stamp) across closed trades">Charges</th>
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
                  <td className="p-2 text-right font-mono text-dimmer">{s.total_charges ? `₹${fmtNum(s.total_charges, 0)}` : "—"}</td>
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
                    <td colSpan={13} className="p-0 border-b border-line">
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
