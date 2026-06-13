import { useMemo } from "react";
import { MetricCard } from "@/components/MetricCard";
import { fmtInt, fmtNum, fmtPct, fmtPnL, colorPnL } from "@/lib/fmt";
import { buildPerformanceSeries, computeKeyMetrics } from "@/lib/backtestMetrics";
import { EquityUnderlyingChart } from "./EquityUnderlyingChart";

const money = (n) => (n == null ? "—" : `₹${fmtInt(n)}`);
const moneySigned = (n) => (n == null ? "—" : `${n < 0 ? "−" : "+"}₹${fmtInt(Math.abs(n))}`);

/**
 * The decision-first top of a backtest result: a rupee hero (when a capital was
 * set), the account-value + underlying chart with drawdown, and a tight block
 * of high-value metrics. Everything here answers "would I deploy this?" fast.
 */
export function PerformanceOverview({ result }) {
  const series = useMemo(() => buildPerformanceSeries(result), [result]);
  const k = useMemo(() => computeKeyMetrics(result), [result]);
  const cur = series.currency;

  // Hero — rupee-first when an account exists, else points.
  const hero = cur
    ? [
        { label: "Net P&L", value: moneySigned(k.netPnl), accent: colorPnL(k.netPnl), testid: "perf-net" },
        { label: "Return on capital", value: k.returnPct == null ? "—" : fmtPct(k.returnPct, 2), accent: colorPnL(k.returnPct), testid: "perf-return" },
        { label: "Ending equity", value: money(k.endingEquity), sub: `from ${money(k.capital)}`, testid: "perf-ending" },
        { label: "Max drawdown", value: moneySigned(k.maxDdValue), sub: k.maxDdPct == null ? null : fmtPct(k.maxDdPct, 2), accent: "text-danger", testid: "perf-maxdd" },
        { label: "Profit ÷ max DD", value: k.returnOverMaxDd == null ? "—" : `${fmtNum(k.returnOverMaxDd, 2)}×`, sub: "reward vs worst drop", accent: k.returnOverMaxDd != null && k.returnOverMaxDd < 1 ? "text-danger" : undefined, testid: "perf-ret-dd" },
        { label: "Sharpe (ann.)", value: k.sharpe == null ? "—" : fmtNum(k.sharpe, 2), testid: "perf-sharpe" },
      ]
    : [
        { label: "Net P&L (pts)", value: fmtPnL(k.netPnl), accent: colorPnL(k.netPnl), testid: "perf-net" },
        { label: "Max DD (pts)", value: fmtPnL(k.maxDdValue), accent: "text-danger", testid: "perf-maxdd" },
        { label: "Expectancy", value: `${fmtNum(k.expectancy, 2)} pts`, sub: "per trade", testid: "perf-exp" },
        { label: "Trades", value: fmtInt(k.tradeCount), testid: "perf-trades" },
        { label: "Win streak", value: fmtInt(k.maxWinStreak), testid: "perf-winstreak" },
        { label: "Loss streak", value: fmtInt(k.maxLossStreak), accent: "text-danger", testid: "perf-lossstreak" },
      ];

  return (
    <div className="space-y-3" data-testid="performance-overview">
      <div className="grid grid-cols-2 lg:grid-cols-6 gap-3">
        {hero.map((c) => <MetricCard key={c.testid} {...c} />)}
      </div>

      <EquityUnderlyingChart
        equity={series.equity}
        underlying={series.underlying}
        drawdown={series.drawdown}
        currency={cur}
        height={420}
      />

      {/* High-value, decision-critical metrics — kept tight on purpose. */}
      <div className="rounded-lg border border-line bg-bg-1 p-3" data-testid="perf-key-metrics">
        <div className="text-[11px] font-semibold uppercase tracking-wider text-dim mb-2">Trade quality</div>
        <div className="grid grid-cols-2 lg:grid-cols-4 gap-2">
          <Stat label={`Avg win`} value={cur ? moneySigned(k.avgWin) : `+${fmtNum(k.avgWin, 2)}`} accent="text-success" />
          <Stat label={`Avg loss`} value={cur ? moneySigned(k.avgLoss) : fmtNum(k.avgLoss, 2)} accent="text-danger" />
          <Stat label="Payoff (win/loss)" value={k.payoff == null ? "—" : `${fmtNum(k.payoff, 2)}×`} />
          <Stat label="Expectancy / trade" value={cur ? moneySigned(k.expectancy) : `${fmtNum(k.expectancy, 2)} pts`} accent={colorPnL(k.expectancy)} />
          <Stat label="Largest win" value={cur ? moneySigned(k.largestWin) : `+${fmtNum(k.largestWin, 2)}`} accent="text-success" />
          <Stat label="Largest loss" value={cur ? moneySigned(k.largestLoss) : fmtNum(k.largestLoss, 2)} accent="text-danger" />
          <Stat label="Max win / loss streak" value={`${fmtInt(k.maxWinStreak)} / ${fmtInt(k.maxLossStreak)}`} />
          <Stat
            label="Longest drawdown"
            value={k.ddDurationDays ? `${fmtInt(k.ddDurationDays)}d` : "—"}
            sub={k.recovered ? "recovered" : "still underwater"}
          />
          <Stat label="Trading days" value={fmtInt(k.tradingDays)} />
          <Stat label="Avg trades / day" value={k.avgTradesPerDay == null ? "—" : fmtNum(k.avgTradesPerDay, 1)} />
          {cur && (
            <Stat
              label="CAGR (≥1y only)"
              value={k.cagr == null ? "—" : fmtPct(k.cagr, 1)}
              sub={k.cagr == null ? "window < 1 year" : (k.calmar == null ? null : `Calmar ${fmtNum(k.calmar, 2)}`)}
            />
          )}
        </div>
      </div>
    </div>
  );
}

function Stat({ label, value, sub, accent }) {
  return (
    <div className="rounded-md border border-line bg-bg-2 p-2 min-w-0">
      <div className="text-[10px] uppercase tracking-wider text-dimmer">{label}</div>
      <div className={`text-sm font-mono mt-0.5 truncate ${accent || ""}`} title={String(value)}>{value}</div>
      {sub && <div className="text-[10px] text-dimmer mt-0.5">{sub}</div>}
    </div>
  );
}
