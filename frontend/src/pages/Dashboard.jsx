import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "@/lib/api";
import { fmtInt, fmtNum, fmtPct, fmtPnL, colorPnL } from "@/lib/fmt";
import { MetricCard } from "@/components/MetricCard";
import { RegimeBadge } from "@/components/RegimeBadge";
import { SignificanceBadge } from "@/components/SignificanceBadge";
import WarehouseHealthBanner from "@/components/WarehouseHealthBanner";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { Database, FlaskConical, Library, ListChecks, ArrowRight, Activity } from "lucide-react";

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
  const regimeDist = latest?.regime_distribution || {};
  const totalRegime = Object.values(regimeDist).reduce((s, v) => s + v, 0);

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
          label="Build Phase"
          value="P4a"
          sub="local stack + Upstox scaffold"
          accent="text-info"
          testid="kpi-phase"
        />
      </div>

      {/* Quick start grid */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-3">
        <QuickAction
          to="/warehouse"
          icon={Database}
          title="Manage Data Warehouse"
          desc="Ingest 1m candles for NIFTY / BANKNIFTY / SENSEX from yfinance with integrity checks."
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
              <MetricCard label="Trades" value={fmtInt(latest.metrics?.trade_count)} testid="latest-trades" />
              <MetricCard label="Win Rate" value={fmtPct(latest.metrics?.win_rate)} testid="latest-winrate" />
              <MetricCard label="Profit Factor" value={fmtNum(latest.metrics?.profit_factor, 2)} testid="latest-pf" />
              <MetricCard label="Net P&L (pts)" value={fmtPnL(latest.metrics?.total_pnl_pts)} accent={colorPnL(latest.metrics?.total_pnl_pts)} testid="latest-pnl" />
              <MetricCard label="Max DD (pts)" value={fmtPnL(latest.metrics?.max_dd_pts)} accent="text-danger" testid="latest-dd" />
              <MetricCard label="Sharpe" value={fmtNum(latest.metrics?.sharpe, 2)} testid="latest-sharpe" />
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

      {/* Build progress */}
      <BuildProgress />
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

function BuildProgress() {
  const phases = [
    { name: "Phase 1 — Core POC", status: "done", desc: "Vectorized backtest + walk-forward + costs validated" },
    { name: "Phase 2 — V1 Lab", status: "done", desc: "6 strategies + warehouse v2 + multi-pane charts" },
    { name: "Phase 3 — Auto-Optimizer", status: "done", desc: "Optuna + Grid + CMA-ES + heatmaps" },
    { name: "Phase 3.5 — Workflow Fixes", status: "done", desc: "Presets + stop button + exports + journal deep links" },
    { name: "Phase 4 — Upstox Live", status: "current", desc: "OAuth + historical ingest scaffold; WS/live/options remain" },
    { name: "Phase 5 — Profitability Engine", status: "pending", desc: "Kaplan-Meier + meta-model + What-If + Telegram" },
    { name: "Phase 6 — Swing Extension", status: "pending", desc: "Daily/weekly TF + overnight risk" },
    { name: "Phase 7 — Local Deploy", status: "done", desc: "Docker Compose + Windows .bat + setup guide" },
  ];
  return (
    <div className="rounded-lg border border-line bg-bg-1" data-testid="build-progress-card">
      <div className="px-3 py-2 border-b border-line flex items-center">
        <div className="text-xs font-semibold uppercase tracking-wider text-dim">Build Roadmap</div>
        <div className="ml-auto text-[11px] text-dimmer">live status</div>
      </div>
      <div className="p-2">
        {phases.map((p, i) => {
          const dotColor = p.status === "done" ? "bg-emerald-500" : p.status === "current" ? "bg-info animate-pulse" : "bg-slate-700";
          return (
            <div key={p.name} className="flex items-start gap-3 px-2 py-2 border-b border-line last:border-b-0">
              <div className="flex flex-col items-center pt-1.5">
                <span className={`w-2 h-2 rounded-full ${dotColor}`}></span>
                {i < phases.length - 1 && <span className="w-px h-6 bg-line mt-1"></span>}
              </div>
              <div className="flex-1 min-w-0">
                <div className="text-sm font-medium">{p.name}</div>
                <div className="text-xs text-dim">{p.desc}</div>
              </div>
              <div className={`text-[10px] font-mono uppercase tracking-wider ${p.status === "done" ? "text-success" : p.status === "current" ? "text-info" : "text-dimmer"}`}>
                {p.status}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
