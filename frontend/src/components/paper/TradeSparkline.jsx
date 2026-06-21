// Compact P&L sparkline from analytics.spark = [{t, pnl}]. Pure SVG, no deps.
export default function TradeSparkline({ points, width = 72, height = 24 }) {
  if (!points || points.length < 2) {
    return <span className="text-dimmer text-[10px] font-mono">—</span>;
  }
  const xs = points.map((p) => p.t);
  const ys = points.map((p) => p.pnl);
  const minX = Math.min(...xs), maxX = Math.max(...xs);
  const minY = Math.min(...ys, 0), maxY = Math.max(...ys, 0);
  const spanX = maxX - minX || 1;
  const spanY = maxY - minY || 1;
  const px = (x) => ((x - minX) / spanX) * width;
  const py = (y) => height - ((y - minY) / spanY) * height;
  const path = points.map((p, i) => `${i ? "L" : "M"}${px(p.t).toFixed(1)},${py(p.pnl).toFixed(1)}`).join(" ");
  const last = ys[ys.length - 1];
  const stroke = last >= 0 ? "var(--color-success)" : "var(--color-danger)";
  const zeroY = py(0).toFixed(1);
  return (
    <svg width={width} height={height} className="overflow-visible" preserveAspectRatio="none" data-testid="trade-sparkline" aria-hidden="true">
      <line x1="0" y1={zeroY} x2={width} y2={zeroY} stroke="var(--border-1)" strokeWidth="0.5" strokeDasharray="2,2" />
      <path d={path} fill="none" stroke={stroke} strokeWidth="1.5" />
    </svg>
  );
}
