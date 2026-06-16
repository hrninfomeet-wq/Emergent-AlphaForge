import { useMemo } from "react";
import { MetricCard } from "@/components/MetricCard";
import { fmtInt, fmtNum, fmtPct, fmtPnL, colorPnL } from "@/lib/fmt";
import { buildPerformanceSeries, computeKeyMetrics } from "@/lib/backtestMetrics";
import { DualAxisChart } from "./DualAxisChart";
import { MonthlyPnlCalendar } from "./MonthlyPnlCalendar";

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
  // Exit-control attribution lives in the OPTION metrics (option_backtest.metrics),
  // not the spot metrics. Spot-only / failed runs have no option_backtest -> {} ->
  // anyNonZero false -> the attribution block (below) stays hidden (correct).
  const m = result?.option_backtest?.metrics || {};
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

      {/* Two separate charts (split from one dual-pane chart per request). */}
      <DualAxisChart
        testid="chart-pnl-vs-value"
        title="Cumulative P&L vs trade value"
        left={{ data: series.cumPnl, kind: "area", color: "#2ED47A", label: "Cumulative P&L" }}
        right={{ data: series.buyValue, kind: "line", color: "#5AA9FF", label: cur ? "Trade value" : series.rightLabel }}
        currency={cur}
        height={300}
      />
      <DualAxisChart
        testid="chart-account-drawdown"
        title="Account value & drawdown"
        left={{ data: series.accountValue, kind: "line", color: "#C9A227", label: "Account value" }}
        right={{ data: series.drawdown, kind: "baseline", color: "#FF5D5D", label: "Drawdown" }}
        currency={cur}
        height={260}
      />

      <MonthlyPnlCalendar result={result} />

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
            value={k.ddDurationDays ? `${fmtInt(k.ddDurationDays)} days` : "—"}
            sub={k.recovered ? "recovered to new high" : "not yet recovered"}
            title={
              "The longest stretch the account stayed below a previous peak before "
              + (k.recovered
                ? "it climbed back to a new high (fully recovered by the end of the test)."
                : "the test ended — it had NOT returned to that peak (still underwater at the end).")
            }
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

      {/* Exit-control attribution — only rendered when any count is non-zero.
          Older runs without these keys simply show nothing (|| 0 fallback). */}
      {(() => {
        const trailExits = m.option_trail_exits || 0;
        const beExits = m.option_breakeven_exits || 0;
        const skippedCap = m.skipped_by_cap || 0;
        const skippedLoss = m.skipped_daily_loss || 0;
        const skippedTarget = m.skipped_daily_target || 0;
        const skippedMaxTrades = m.skipped_max_trades || 0;
        const anyNonZero = trailExits + beExits + skippedCap + skippedLoss + skippedTarget + skippedMaxTrades > 0;
        if (!anyNonZero) return null;
        return (
          <div className="rounded-lg border border-line bg-bg-1 p-3" data-testid="perf-exit-controls">
            <div className="text-[11px] font-semibold uppercase tracking-wider text-dim mb-2">Exit controls</div>
            <div className="grid grid-cols-2 lg:grid-cols-4 gap-2">
              {trailExits > 0 && (
                <Stat label="Trailing stop exits" value={fmtInt(trailExits)}
                  sub="trades closed by trail" title="Trades exited by the trailing stop control" />
              )}
              {beExits > 0 && (
                <Stat label="Breakeven exits" value={fmtInt(beExits)}
                  sub="locked to breakeven" title="Trades exited after the breakeven lock triggered" />
              )}
              {skippedCap > 0 && (
                <Stat label="Skipped (cap)" value={fmtInt(skippedCap)}
                  sub="signals blocked by cap" title="Signals skipped because a daily cap (loss/target/max-trades) was reached" />
              )}
              {skippedLoss > 0 && (
                <Stat label="Skipped (daily loss)" value={fmtInt(skippedLoss)}
                  sub="daily loss cap hit" title="Signals skipped because the daily loss cap was reached" />
              )}
              {skippedTarget > 0 && (
                <Stat label="Skipped (daily target)" value={fmtInt(skippedTarget)}
                  sub="daily target cap hit" title="Signals skipped because the daily profit target was reached" />
              )}
              {skippedMaxTrades > 0 && (
                <Stat label="Skipped (max trades)" value={fmtInt(skippedMaxTrades)}
                  sub="max-trades/day hit" title="Signals skipped because the max-trades-per-day cap was reached" />
              )}
            </div>
          </div>
        );
      })()}
    </div>
  );
}

function Stat({ label, value, sub, accent, title }) {
  return (
    <div className="rounded-md border border-line bg-bg-2 p-2 min-w-0" title={title || undefined}>
      <div className="text-[10px] uppercase tracking-wider text-dimmer">{label}</div>
      <div className={`text-sm font-mono mt-0.5 truncate ${accent || ""}`}>{value}</div>
      {sub && <div className="text-[10px] text-dimmer mt-0.5">{sub}</div>}
    </div>
  );
}
