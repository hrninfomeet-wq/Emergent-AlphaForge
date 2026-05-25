export function MetricCard({ label, value, sub, accent, testid }) {
  return (
    <div className="rounded-lg border border-line bg-bg-1 p-3" data-testid={testid}>
      <div className="text-[10px] uppercase tracking-wider text-dimmer mb-1">{label}</div>
      <div className={`text-lg font-mono tabular-nums ${accent || ""}`}>
        {value}
      </div>
      {sub && <div className="text-[11px] text-dim mt-0.5">{sub}</div>}
    </div>
  );
}
