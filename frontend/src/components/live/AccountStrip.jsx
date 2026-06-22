import { fmtINR } from "@/lib/fmt";

/**
 * Account metric cards — cash, pay-in, pay-out, open positions, working orders.
 * All read-only (L0).
 */
function MetricCard({ label, value, sub }) {
  return (
    <div className="rounded-lg border border-line bg-bg-1 px-4 py-3 flex flex-col gap-1 min-w-[120px]">
      <div className="text-[10px] uppercase tracking-wider text-dimmer font-semibold">{label}</div>
      <div className="text-lg font-mono tabular-nums font-semibold text-foreground">{value}</div>
      {sub && <div className="text-[10px] text-dimmer">{sub}</div>}
    </div>
  );
}

export default function AccountStrip({ limits, positionCount, orderCount }) {
  const cash = limits?.cash ?? limits?.marginused != null ? limits?.net : null;
  // Noren limits fields — try common names defensively
  const cashVal = limits?.cash ?? limits?.net ?? limits?.marginusedtoday ?? null;
  const payIn = limits?.payin ?? null;
  const payOut = limits?.payout ?? null;

  return (
    <div className="flex flex-wrap gap-3" data-testid="live-account-strip">
      <MetricCard label="Cash / Net" value={cashVal !== null ? fmtINR(cashVal) : "–"} />
      <MetricCard label="Pay-in" value={payIn !== null ? fmtINR(payIn) : "–"} />
      <MetricCard label="Pay-out" value={payOut !== null ? fmtINR(payOut) : "–"} />
      <MetricCard
        label="Open Positions"
        value={positionCount !== null ? String(positionCount) : "–"}
        sub="from broker"
      />
      <MetricCard
        label="Working Orders"
        value={orderCount !== null ? String(orderCount) : "–"}
        sub="from broker"
      />
    </div>
  );
}
