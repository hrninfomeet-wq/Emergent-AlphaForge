import { useMemo } from "react";
import { fmtInt, fmtNum } from "@/lib/fmt";
import { monthlyPnl } from "@/lib/backtestMetrics";

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/**
 * Year × month P&L grid — the consistency-at-a-glance view popular platforms
 * lead with. Net P&L per calendar month (₹ when option execution ran, else
 * index points), green/red shaded by magnitude, with a per-year total column.
 */
export function MonthlyPnlCalendar({ result }) {
  const { currency, unit, byMonth } = useMemo(() => monthlyPnl(result), [result]);

  const { years, cells, yearTotals, maxAbs } = useMemo(() => {
    const yset = new Set();
    const cmap = {}; // `${y}-${m}` -> pnl
    for (const [key, pnl] of byMonth.entries()) {
      const [y, m] = key.split("-").map(Number);
      yset.add(y);
      cmap[`${y}-${m}`] = pnl;
    }
    const ys = [...yset].sort();
    const totals = {};
    let mx = 1;
    for (const y of ys) {
      let t = 0;
      for (let m = 1; m <= 12; m++) {
        const v = cmap[`${y}-${m}`];
        if (v != null) { t += v; mx = Math.max(mx, Math.abs(v)); }
      }
      totals[y] = t;
    }
    return { years: ys, cells: cmap, yearTotals: totals, maxAbs: mx };
  }, [byMonth]);

  if (!years.length) return null;

  const fmtVal = (v) => (currency ? `₹${fmtInt(v)}` : fmtNum(v, 0));
  const cellStyle = (v) => {
    if (v == null || v === 0) return {};
    const intensity = 0.18 + 0.72 * Math.min(1, Math.abs(v) / maxAbs);
    return { backgroundColor: v > 0 ? "var(--color-success)" : "var(--color-danger)", opacity: intensity };
  };

  return (
    <div className="rounded-lg border border-line bg-bg-1" data-testid="monthly-pnl-calendar">
      <div className="px-3 py-2 border-b border-line flex items-center gap-2">
        <div className="text-[11px] font-semibold uppercase tracking-wider text-dim">Monthly P&amp;L</div>
        <div className="text-[11px] text-dimmer">net per calendar month ({unit})</div>
      </div>
      <div className="p-3 overflow-x-auto">
        <table className="text-[11px] font-mono">
          <thead>
            <tr className="text-dimmer">
              <th className="text-left p-1 pr-2">Year</th>
              {MONTHS.map((m) => <th key={m} className="p-1 text-center min-w-[52px]">{m}</th>)}
              <th className="p-1 text-center min-w-[64px] border-l border-line">Total</th>
            </tr>
          </thead>
          <tbody>
            {years.map((y) => (
              <tr key={y} className="border-t border-line">
                <td className="p-1 pr-2 text-dim font-semibold">{y}</td>
                {MONTHS.map((mLabel, i) => {
                  const v = cells[`${y}-${i + 1}`];
                  return (
                    <td key={mLabel} className="p-0.5">
                      <div
                        className="rounded-sm px-1 py-1 text-center text-[10px] cursor-default"
                        style={cellStyle(v)}
                        title={v == null ? `${mLabel} ${y}: no trades` : `${mLabel} ${y}: ${fmtVal(v)}`}
                      >
                        {v == null ? <span className="text-dimmer">·</span> : (currency ? `${v < 0 ? "−" : ""}${fmtInt(Math.abs(v) / 1000)}k` : fmtNum(v, 0))}
                      </div>
                    </td>
                  );
                })}
                <td className={`p-1 text-right border-l border-line ${yearTotals[y] >= 0 ? "text-success" : "text-danger"}`}>
                  {yearTotals[y] < 0 ? "−" : ""}{fmtVal(Math.abs(yearTotals[y]))}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        <div className="mt-2 flex items-center gap-3 text-[10px] text-dimmer">
          <span className="inline-flex items-center gap-1"><span className="w-3 h-3 rounded-sm" style={{ backgroundColor: "var(--color-success)", opacity: 0.7 }} /> profit</span>
          <span className="inline-flex items-center gap-1"><span className="w-3 h-3 rounded-sm" style={{ backgroundColor: "var(--color-danger)", opacity: 0.7 }} /> loss</span>
          {currency && <span>cells in ₹ thousands · hover for exact</span>}
        </div>
      </div>
    </div>
  );
}
