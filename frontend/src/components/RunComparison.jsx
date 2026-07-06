import { useMemo } from "react";
import { Button } from "@/components/ui/button";
import { fmtInt, fmtNum, fmtPct, fmtPnL, colorPnL } from "@/lib/fmt";
import { X } from "lucide-react";

/**
 * Run comparison — side-by-side diff of two saved backtest runs.
 *
 * Pure client-side over two full run docs (GET /api/backtest/runs/{id}); no
 * backend changes. Shows:
 *   1. a params diff table (differing keys highlighted),
 *   2. a headline metric table, and
 *   3. overlaid equity curves normalized to trade index so two runs of
 *      different length still line up left→right.
 */

const METRICS = [
  { key: "trade_count", label: "Trades", fmt: (v) => fmtInt(v) },
  { key: "win_rate", label: "Win Rate", fmt: (v) => fmtPct(v) },
  { key: "profit_factor", label: "Profit Factor", fmt: (v) => fmtNum(v, 2) },
  { key: "total_pnl_pts", label: "Net P&L (pts)", fmt: (v) => fmtPnL(v), pnl: true },
  { key: "max_dd_pts", label: "Max DD (pts)", fmt: (v) => fmtPnL(v) },
  { key: "sharpe", label: "Sharpe", fmt: (v) => fmtNum(v, 2) },
];

const getParams = (run) => run?.params_applied || run?.config?.params || {};
const equitySeries = (run) =>
  (run?.equity_curve || []).map((p) => Number(p.equity_pts)).filter((v) => Number.isFinite(v));

// Build an SVG path for a series, mapping its own trade index to [0,W] (so two
// runs of different lengths overlay) and value to [0,H] against a shared scale.
function seriesPath(values, W, H, min, max) {
  if (values.length < 2) return "";
  const span = max - min || 1;
  return values
    .map((v, i) => {
      const x = (i / (values.length - 1)) * W;
      const y = H - ((v - min) / span) * H;
      return `${i === 0 ? "M" : "L"}${x.toFixed(2)},${y.toFixed(2)}`;
    })
    .join(" ");
}

function EquityOverlay({ a, b }) {
  const sa = equitySeries(a);
  const sb = equitySeries(b);
  const W = 100;
  const H = 40;
  const all = [...sa, ...sb, 0];
  const min = Math.min(...all);
  const max = Math.max(...all);
  const pathA = seriesPath(sa, W, H, min, max);
  const pathB = seriesPath(sb, W, H, min, max);
  const zeroY = H - ((0 - min) / ((max - min) || 1)) * H;

  if (!pathA && !pathB) {
    return <div className="text-[11px] text-dimmer">No equity curve stored for these runs.</div>;
  }
  return (
    <div data-testid="comparison-equity-overlay">
      <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" className="w-full h-28 bg-bg-2 rounded-md border border-line">
        <line x1="0" y1={zeroY} x2={W} y2={zeroY} stroke="currentColor" className="text-line" strokeWidth="0.3" />
        {pathA && <path d={pathA} fill="none" className="text-info" stroke="currentColor" strokeWidth="0.7" vectorEffect="non-scaling-stroke" />}
        {pathB && <path d={pathB} fill="none" className="text-warning" stroke="currentColor" strokeWidth="0.7" vectorEffect="non-scaling-stroke" />}
      </svg>
      <div className="flex items-center gap-4 mt-1 text-[10px] text-dimmer">
        <span className="flex items-center gap-1"><span className="inline-block w-3 h-0.5 bg-info" /> A · {a?.name || "Run A"}</span>
        <span className="flex items-center gap-1"><span className="inline-block w-3 h-0.5 bg-amber-300" /> B · {b?.name || "Run B"}</span>
        <span className="ml-auto font-mono">x = trade index (normalized), y = cumulative pts</span>
      </div>
    </div>
  );
}

export default function RunComparison({ a, b, onClose }) {
  const paramKeys = useMemo(() => {
    const keys = new Set([...Object.keys(getParams(a)), ...Object.keys(getParams(b))]);
    return Array.from(keys).sort();
  }, [a, b]);

  const pa = getParams(a);
  const pb = getParams(b);

  return (
    <div className="border-b border-line bg-bg-1" data-testid="run-comparison-panel">
      <div className="px-3 py-2 border-b border-line flex items-center gap-2">
        <div className="text-xs font-semibold uppercase tracking-wider text-dim">Run Comparison</div>
        <div className="text-[11px] text-dimmer truncate">
          <span className="text-info font-mono">A</span> {a?.name} · {a?.instrument} {a?.strategy_id}
          <span className="mx-2 text-dimmer">vs</span>
          <span className="text-warning font-mono">B</span> {b?.name} · {b?.instrument} {b?.strategy_id}
        </div>
        <Button size="sm" variant="ghost" onClick={onClose} className="ml-auto h-6 w-6 p-0" data-testid="run-comparison-close">
          <X className="w-3.5 h-3.5" />
        </Button>
      </div>

      <div className="p-3 grid grid-cols-1 lg:grid-cols-2 gap-4">
        {/* Metrics + equity */}
        <div className="space-y-3">
          <div>
            <div className="text-[10px] uppercase tracking-wider text-dimmer mb-1">Metrics</div>
            <table className="w-full text-xs" data-testid="comparison-metric-table">
              <thead>
                <tr className="text-dim border-b border-line">
                  <th className="text-left p-1.5">Metric</th>
                  <th className="text-right p-1.5 text-info">A</th>
                  <th className="text-right p-1.5 text-warning">B</th>
                </tr>
              </thead>
              <tbody>
                {METRICS.map((m) => {
                  const va = a?.metrics?.[m.key];
                  const vb = b?.metrics?.[m.key];
                  return (
                    <tr key={m.key} className="border-b border-line">
                      <td className="p-1.5 text-dim">{m.label}</td>
                      <td className={`p-1.5 text-right font-mono ${m.pnl ? colorPnL(va) : ""}`}>{m.fmt(va)}</td>
                      <td className={`p-1.5 text-right font-mono ${m.pnl ? colorPnL(vb) : ""}`}>{m.fmt(vb)}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          <div>
            <div className="text-[10px] uppercase tracking-wider text-dimmer mb-1">Equity (overlaid, normalized)</div>
            <EquityOverlay a={a} b={b} />
          </div>
        </div>

        {/* Params diff */}
        <div>
          <div className="text-[10px] uppercase tracking-wider text-dimmer mb-1">Parameters (differing keys highlighted)</div>
          <table className="w-full text-xs" data-testid="comparison-params-table">
            <thead>
              <tr className="text-dim border-b border-line">
                <th className="text-left p-1.5">Param</th>
                <th className="text-right p-1.5 text-info">A</th>
                <th className="text-right p-1.5 text-warning">B</th>
              </tr>
            </thead>
            <tbody>
              {paramKeys.length === 0 && (
                <tr><td colSpan="3" className="p-3 text-center text-dimmer">No parameters recorded.</td></tr>
              )}
              {paramKeys.map((k) => {
                const va = pa[k];
                const vb = pb[k];
                const differ = JSON.stringify(va) !== JSON.stringify(vb);
                const show = (v) => (v === undefined ? "—" : typeof v === "object" ? JSON.stringify(v) : String(v));
                return (
                  <tr key={k} className={`border-b border-line ${differ ? "bg-amber-950/30" : ""}`} data-testid={differ ? "comparison-param-diff" : "comparison-param-same"}>
                    <td className={`p-1.5 ${differ ? "text-warning font-medium" : "text-dim"}`}>{k}</td>
                    <td className="p-1.5 text-right font-mono">{show(va)}</td>
                    <td className="p-1.5 text-right font-mono">{show(vb)}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
