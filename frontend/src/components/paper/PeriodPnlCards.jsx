import { fmtINRSigned, fmtPct, fmtNum } from "@/lib/fmt";

function Card({ label, value, tone = null }) {
  const cls = tone == null ? "" : Number(tone) > 0 ? "text-success" : Number(tone) < 0 ? "text-danger" : "";
  return (
    <div className="rounded-md border border-line bg-bg-2 p-2">
      <div className="text-[10px] uppercase tracking-wider text-dimmer">{label}</div>
      <div className={`text-base font-mono tabular-nums mt-0.5 ${cls}`}>{value}</div>
    </div>
  );
}

export default function PeriodPnlCards({ period }) {
  if (!period) return null;
  const p = period;
  const pf = p.profit_factor;
  return (
    <div className="grid grid-cols-2 lg:grid-cols-6 gap-2" data-testid="paper-period-cards">
      <Card label="Today" value={fmtINRSigned(p.today)} tone={p.today} />
      <Card label="This week" value={fmtINRSigned(p.week)} tone={p.week} />
      <Card label="This month" value={fmtINRSigned(p.month)} tone={p.month} />
      <Card label="Lifetime" value={fmtINRSigned(p.lifetime)} tone={p.lifetime} />
      <Card label="Win rate" value={p.win_rate == null ? "—" : fmtPct(p.win_rate, 1)} />
      <Card label="Profit factor" value={pf == null ? "—" : (pf === Infinity || pf === "Infinity" ? "∞" : fmtNum(pf, 2))} />
    </div>
  );
}
