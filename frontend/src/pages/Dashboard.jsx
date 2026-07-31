import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "@/lib/api";
import { fmtInt, fmtNum, fmtPct, fmtPnL, fmtINRSigned, colorPnL } from "@/lib/fmt";
import { MetricCard } from "@/components/MetricCard";
import { RegimeBadge } from "@/components/RegimeBadge";
import { SignificanceBadge } from "@/components/SignificanceBadge";
import WarehouseHealthBanner from "@/components/WarehouseHealthBanner";
import { Skeleton } from "@/components/ui/skeleton";
import { resultKpis } from "@/lib/backtestMetrics";
import { Database, FlaskConical, ListChecks, ArrowRight } from "lucide-react";

export default function Dashboard() {
  const [summary, setSummary] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api.summary().then((d) => {
      setSummary(d);
      setLoading(false);
    }).catch(() => setLoading(false));
  }, []);

  if (loading) {
    return (
      <div className="grid grid-cols-4 gap-3">
        {[1, 2, 3, 4].map((i) => <Skeleton key={i} className="h-24 bg-bg-1" />)}
      </div>
    );
  }

  const wh = summary?.warehouse || {};
  const latest = summary?.latest_backtest;
  const latestKpis = resultKpis(latest);
  const regimeDist = latest?.regime_distribution || {};
  const totalRegime = Object.values(regimeDist).reduce((s, v) => s + v, 0);
  const latestValue = (value) => latestKpis.currency ? fmtINRSigned(value) : fmtPnL(value);

  return (
    <div className="space-y-4" data-testid="dashboard-page">
      {/* Can I trust today's data? */}
      <WarehouseHealthBanner />

      {/* Top KPI cards */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
        <MetricCard
          label="Instruments Tracked"
          value={fmtInt(wh.instruments_tracked || 0)}
          sub={`${fmtInt(wh.total_candles || 0)} candles stored`}
          testid="kpi-instruments"
        />
        <MetricCard
          label="Strategies Loaded"
          value={fmtInt(summary?.strategies_loaded || 0)}
          sub={summary?.strategies_failed ? `${summary.strategies_failed} failed` : "all healthy"}
          testid="kpi-strategies"
        />
        <MetricCard
          label="Backtest Runs"
          value={fmtInt(summary?.backtest_runs || 0)}
          sub="all-time"
          testid="kpi-runs"
        />
        <MetricCard
          label="Deployment Policy"
          value="Operator-directed"
          sub="paper and live authorization are separate"
          accent="text-info"
          testid="kpi-deployment-policy"
        />
      </div>

      {/* Quick start grid */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-3">
        <QuickAction
          to="/warehouse"
          icon={Database}
          title="Manage Data Warehouse"
          desc="Ingest and audit local 1-minute candles for NIFTY / BANKNIFTY / SENSEX."
          cta="Open Warehouse"
          testid="quick-open-warehouse"
        />
        <QuickAction
          to="/backtest"
          icon={FlaskConical}
          title="Run a Backtest"
          desc="Configure strategy + instrument + mode + filters, then run with walk-forward validation."
          cta="Open Backtest Lab"
          testid="quick-open-backtest"
        />
        <QuickAction
          to="/checklist"
          icon={ListChecks}
          title="Tune Pre-Trade Checklist"
          desc="Conservative / Balanced / Aggressive profiles, configurable filters with anti-overfilter safeguards."
          cta="Open Checklist"
          testid="quick-open-checklist"
        />
      </div>

      {/* Latest backtest */}
      <div className="rounded-lg border border-line bg-bg-1" data-testid="latest-backtest-card">
        <div className="flex items-center px-3 py-2 border-b border-line">
          <div className="text-xs font-semibold uppercase tracking-wider text-dim">Latest Backtest</div>
          {latest && <SignificanceBadge significance={latest.significance} />}
          <Link to="/backtest" className="ml-auto text-xs text-info hover:underline flex items-center gap-1" data-testid="link-to-backtest">
            Open Lab <ArrowRight className="w-3 h-3" />
          </Link>
        </div>
        {!latest ? (
          <div className="p-6 text-sm text-dimmer text-center" data-testid="empty-latest-backtest">
            No backtest run yet — head to the Lab to run your first.
          </div>
        ) : (
          <div className="p-3 space-y-3">
            <div className="flex items-center gap-3 flex-wrap text-sm">
              <span className="text-foreground font-semibold">{latest.name}</span>
              <span className="text-dim font-mono text-xs">{latest.strategy_id} · {latest.instrument} · {latest.config?.mode}</span>
            </div>
            <div className="grid grid-cols-2 lg:grid-cols-6 gap-3">
              <MetricCard label="Trades" value={fmtInt(latestKpis.tradeCount)} testid="latest-trades" />
              <MetricCard label="Win Rate" value={fmtPct(latestKpis.winRate)} testid="latest-winrate" />
              <MetricCard label="Profit Factor" value={fmtNum(latestKpis.profitFactor, 2)} testid="latest-pf" />
              <MetricCard label={`Net P&L (${latestKpis.unit})`} value={latestValue(latestKpis.netPnl)} accent={colorPnL(latestKpis.netPnl)} testid="latest-pnl" />
              <MetricCard label={`Max DD (${latestKpis.unit})`} value={latestValue(latestKpis.maxDd)} accent="text-danger" testid="latest-dd" />
              <MetricCard label="Sharpe" value={fmtNum(latestKpis.sharpe, 2)} testid="latest-sharpe" />
            </div>
            {Object.keys(regimeDist).length > 0 && (
              <div className="flex items-center gap-1 flex-wrap pt-1">
                <span className="text-[11px] text-dimmer mr-1">REGIME DISTRIBUTION:</span>
                {Object.entries(regimeDist).sort((a, b) => b[1] - a[1]).map(([r, c]) => (
                  <RegimeBadge key={r} regime={r} count={c} total={totalRegime} />
                ))}
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

function QuickAction({ to, icon: Icon, title, desc, cta, testid }) {
  return (
    <Link
      to={to}
      className="block rounded-lg border border-line bg-bg-1 p-3 hover:bg-bg-2 transition-colors duration-150 group"
      data-testid={testid}
    >
      <div className="flex items-start gap-3">
        <div className="w-9 h-9 rounded-md bg-bg-3 border border-line-strong flex items-center justify-center shrink-0">
          <Icon className="w-4 h-4 text-info" />
        </div>
        <div className="flex-1 min-w-0">
          <div className="text-sm font-semibold">{title}</div>
          <div className="text-xs text-dim mt-0.5 leading-snug">{desc}</div>
          <div className="mt-3 text-xs text-info inline-flex items-center gap-1 group-hover:gap-1.5 transition-all">
            {cta}
            <ArrowRight className="w-3 h-3" />
          </div>
        </div>
      </div>
    </Link>
  );
}
