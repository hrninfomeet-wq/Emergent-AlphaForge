// Index + option-band coverage heatmaps (split from pages/DataWarehouse.jsx).
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Database, RefreshCw } from "lucide-react";
import { fmtInt, fmtNum, isoToFull } from "@/lib/fmt";
import { RangeChips, rangeCutoff } from "./shared";

export function CoverageHeatmap({ coverage }) {
  const [range, setRange] = useState("8w");
  if (!coverage || Object.keys(coverage).length === 0) {
    return (
      <div className="rounded-lg border border-line bg-bg-1 p-6 text-center text-dimmer text-sm" data-testid="coverage-heatmap-empty">
        Coverage heatmap will appear here after you ingest data.
      </div>
    );
  }
  // Build the trading-day date list. The backend annotates each day with
  // `is_trading_day` (calendar-aware: skips weekends/holidays, includes special
  // sessions). Non-trading days — including stray weekend ticks the roller may
  // have captured — are excluded so they are not flagged red.
  const DEFAULT_TARGET = 375;
  const tradingFlag = {};   // date -> is this a trading day (any instrument says so)
  const expectedByDate = {}; // date -> expected candle count for that session
  Object.values(coverage).forEach((c) =>
    (c.days || []).forEach((d) => {
      // Backward-compatible: if the flag is absent (older payloads), treat as trading.
      const isTrading = d.is_trading_day === undefined ? true : Boolean(d.is_trading_day);
      if (isTrading) tradingFlag[d.date] = true;
      const exp = Number(d.expected_candles);
      if (Number.isFinite(exp) && exp > 0) expectedByDate[d.date] = exp;
    })
  );
  const cutoff = rangeCutoff(range);
  const dates = Object.keys(tradingFlag).sort().filter((d) => !cutoff || d >= cutoff);

  return (
    <div className="rounded-lg border border-line bg-bg-1" data-testid="data-coverage-heatmap">
      <div className="px-3 py-2 border-b border-line flex items-center gap-2">
        <div className="text-xs font-semibold uppercase tracking-wider text-dim">Coverage Heatmap (per trading day)</div>
        <div className="ml-auto">
          <RangeChips value={range} onChange={setRange} testid="coverage-heatmap-range" />
        </div>
      </div>
      <div className="p-3 overflow-x-auto">
        <table className="text-[10px] font-mono">
          <thead>
            <tr>
              <th className="text-left text-dim pr-2"></th>
              {dates.map((d) => (
                <th key={d} className="px-0.5 text-dim text-[9px] writing-mode-vertical-rl transform -rotate-90 origin-bottom-left h-12" style={{ minWidth: 18 }}>{d.slice(5)}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {Object.entries(coverage).map(([inst, c]) => {
              const dayMap = Object.fromEntries((c.days || []).map((d) => [d.date, d]));
              return (
                <tr key={inst}>
                  <td className="text-dim text-xs pr-2 py-1">{inst}</td>
                  {dates.map((d) => {
                    const day = dayMap[d];
                    const target = expectedByDate[d] || DEFAULT_TARGET;
                    if (!day) return <td key={d} className="px-0.5"><div className="w-4 h-4 bg-bg-3 rounded-sm border border-line" title={`${d}: no data`}></div></td>;
                    const pct = Math.min(100, Math.round((day.candle_count / target) * 100));
                    const color = pct >= 90 ? "bg-emerald-600" : pct >= 50 ? "bg-amber-600" : "bg-rose-700";
                    const shortNote = target < DEFAULT_TARGET ? " (short session)" : "";
                    return (
                      <td key={d} className="px-0.5">
                        <div className={`w-4 h-4 ${color} rounded-sm border border-line cursor-help`} title={`${inst} · ${d}: ${day.candle_count}/${target} candles (${pct}%)${shortNote}\nhash ${day.hash}`}></div>
                      </td>
                    );
                  })}
                </tr>
              );
            })}
          </tbody>
        </table>
        <div className="mt-3 flex items-center gap-4 text-[10px] text-dimmer">
          <div className="flex items-center gap-1"><span className="w-3 h-3 bg-emerald-600 rounded-sm"></span>≥90% coverage</div>
          <div className="flex items-center gap-1"><span className="w-3 h-3 bg-amber-600 rounded-sm"></span>50–90%</div>
          <div className="flex items-center gap-1"><span className="w-3 h-3 bg-rose-700 rounded-sm"></span>&lt;50%</div>
          <div className="flex items-center gap-1"><span className="w-3 h-3 bg-bg-3 rounded-sm border border-line"></span>no data</div>
          <div className="ml-auto">Trading days only · weekends/holidays excluded · short sessions scaled to their expected length</div>
        </div>
      </div>
    </div>
  );
}

export function OptionCoverageHeatmap() {
  // Band truth per day, read from the persisted hygiene plan (instant). The
  // old version showed candle DENSITY over whatever contracts happened to be
  // stored — a self-referential denominator that could read 100% on a day
  // missing entire wick strikes. Cells now answer the question that matters:
  // "does this day have every strike its spot range demanded?"
  const [plan, setPlan] = useState(null);
  const [loaded, setLoaded] = useState(false);
  const [range, setRange] = useState("8w");

  const load = async () => {
    try {
      const res = await api.dataHygieneLatest();
      setPlan(res?.plan || null);
    } catch {
      setPlan(null);
    } finally {
      setLoaded(true);
    }
  };
  useEffect(() => {
    load();
  }, []);

  const instruments = plan?.instruments || [];
  if (!loaded) {
    return (
      <div className="rounded-lg border border-line bg-bg-1 p-6 flex items-center justify-center gap-2 text-dimmer text-sm" data-testid="option-coverage-heatmap-loading">
        <RefreshCw className="w-4 h-4 animate-spin" /> Loading option band coverage…
      </div>
    );
  }
  if (!instruments.length) {
    return (
      <div className="rounded-lg border border-line bg-bg-1 p-6 text-center text-dimmer text-sm" data-testid="option-coverage-heatmap-empty">
        Run “Check warehouse” in Data Hygiene once to see daily ATM-band coverage here.
      </div>
    );
  }

  const cutoff = rangeCutoff(range);
  const dateSet = new Set();
  instruments.forEach((i) => (i.option_candles?.per_day || []).forEach((d) => {
    if (!cutoff || d.date >= cutoff) dateSet.add(d.date);
  }));
  const dates = Array.from(dateSet).sort();

  return (
    <div className="rounded-lg border border-line bg-bg-1" data-testid="option-coverage-heatmap">
      <div className="px-3 py-2 border-b border-line flex items-center gap-2">
        <Database className="w-4 h-4 text-info" />
        <div className="text-xs font-semibold uppercase tracking-wider text-dim">Option Band Coverage</div>
        <span className="text-[10px] text-dimmer font-mono">every strike the day&apos;s spot range touched</span>
        <div className="ml-auto flex items-center gap-2">
          {plan?.computed_at && (
            <span className="text-[10px] text-dimmer font-mono">checked {isoToFull(plan.computed_at)}</span>
          )}
          <RangeChips value={range} onChange={setRange} testid="option-heatmap-range" />
          <Button variant="ghost" size="sm" onClick={load} className="h-6 w-6 p-0" title="Reload from the last check" data-testid="option-heatmap-refresh">
            <RefreshCw className="w-3 h-3" />
          </Button>
        </div>
      </div>
      <div className="p-3 overflow-x-auto">
        <table className="text-xs">
          <thead>
            <tr className="text-dim border-b border-line">
              <th className="text-left p-2 sticky left-0 bg-bg-1 z-10">Underlying</th>
              {dates.map((d) => (
                <th key={d} className="p-1 text-center font-mono whitespace-nowrap text-[9px]">{d.slice(5)}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {instruments.map((inst) => {
              const oc = inst.option_candles || {};
              const byDate = Object.fromEntries((oc.per_day || []).map((d) => [d.date, d]));
              return (
                <tr key={inst.instrument} className="border-b border-line">
                  <td className="p-2 sticky left-0 bg-bg-1 z-10">
                    <div className="font-semibold">{inst.instrument}</div>
                    <div className="text-[10px] text-dimmer font-mono">
                      band {fmtNum(oc.coverage_pct ?? 0, 1)}% · {fmtInt(oc.total_candles || 0)} candles
                    </div>
                    {(oc.broker_empty_pairs || 0) > 0 && (
                      <div className="text-[10px] text-dimmer font-mono">{fmtInt(oc.broker_empty_pairs)} broker-empty excluded</div>
                    )}
                  </td>
                  {dates.map((d) => {
                    const day = byDate[d];
                    if (!day) {
                      return (
                        <td key={d} className="p-0.5">
                          <div className="w-5 h-5 rounded-sm bg-bg-3 border border-line" title={`${inst.instrument} ${d}: not judged (no spot data or in-progress day)`} />
                        </td>
                      );
                    }
                    const pct = Number(day.coverage_pct || 0);
                    const cls = pct >= 100 ? "bg-emerald-600" : pct >= 95 ? "bg-amber-500" : "bg-rose-700";
                    const note = [
                      `${inst.instrument} ${d}: ${pct}% of band stored`,
                      `${fmtInt(day.expected)} pair(s) demanded`,
                      day.missing ? `${fmtInt(day.missing)} missing (fixable)` : null,
                      day.broker_empty ? `${fmtInt(day.broker_empty)} broker-empty (excluded)` : null,
                    ].filter(Boolean).join(" · ");
                    return (
                      <td key={d} className="p-0.5">
                        <div className={`w-5 h-5 rounded-sm ${cls} border border-line cursor-help`} title={note} />
                      </td>
                    );
                  })}
                </tr>
              );
            })}
          </tbody>
        </table>
        <div className="mt-3 flex flex-wrap gap-3 text-[11px] text-dim">
          <div className="flex items-center gap-1"><span className="w-3 h-3 bg-emerald-600 rounded-sm"></span>band complete (broker-empty excluded)</div>
          <div className="flex items-center gap-1"><span className="w-3 h-3 bg-amber-500 rounded-sm"></span>≥95% of band</div>
          <div className="flex items-center gap-1"><span className="w-3 h-3 bg-rose-700 rounded-sm"></span>&lt;95% — run Sync now</div>
          <div className="flex items-center gap-1"><span className="w-3 h-3 bg-bg-3 border border-line rounded-sm"></span>not judged</div>
        </div>
        <div className="mt-2 text-[11px] text-dimmer">
          A day is complete when candles exist for BOTH legs of every strike its spot low–high touched (±1 step pad) at the day&apos;s resolved expiry — the same rule backtests and the hygiene plan use.
        </div>
      </div>
    </div>
  );
}
