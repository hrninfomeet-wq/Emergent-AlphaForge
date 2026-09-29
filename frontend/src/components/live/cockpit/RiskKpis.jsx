import { TrendingUp, Layers, Shield, Wallet, ClipboardList, OctagonAlert } from "lucide-react";
import MetricCard from "@/components/live/MetricCard";
import { fmtINR } from "@/lib/fmt";
import {
  asPositionRows, asOrderRows, isOpenPosition, isWorkingOrder,
  deriveDayPnl, deriveCash, signedINR,
} from "@/components/live/liveHelpers";
import { guardHealthView, worstDayStop } from "@/lib/liveDeploymentView";

/**
 * Compact live-risk KPI grid for the cockpit's right column — reuses MetricCard
 * and the exact derivations from liveHelpers so the numbers match the broker
 * blotters below.
 *
 * Day Stop shows the WORST live deployment (worstDayStop), not a pooled sum: caps
 * are enforced per deployment, and pooling one at 95% of its cap with one at 0%
 * read ~48% and calm. Its "used" is the governor's own day P&L (realized + open),
 * not realized alone.
 */

function dayStopSub(ds) {
  if (!ds.any) return "no live deployment";
  if (!ds.anyCap) return "no cap configured";
  if (!ds.worst) return "loss unknown — a mark is stale";
  const pct = Math.round(ds.worst.ratio * 100);
  return `${ds.worst.name}: ${pct}% (${fmtINR(ds.worst.used)} used)`
    + (ds.anyUnknown ? " · some unknown" : "");
}

export default function RiskKpis({ limits, positions, orders, guard, deployments, deployLive }) {
  const posRows = asPositionRows(positions);
  const ordRows = asOrderRows(orders);
  const openCount = posRows != null ? posRows.filter(isOpenPosition).length : null;
  const workCount = ordRows != null ? ordRows.filter(isWorkingOrder).length : null;
  const dayPnl = deriveDayPnl(positions);
  const cash = deriveCash(limits);
  // The guard card reports what the guard is DOING (its own health: watching /
  // idle / blind / stalled / not running), never the constant `armed` — which read
  // "ARMED" while an expired token left the guard unable to read a single price.
  const guardView = guardHealthView(guard);
  const dayStop = worstDayStop(deployments, deployLive);

  return (
    <div className="grid grid-cols-3 gap-2">
      <MetricCard label="Day P&L" value={dayPnl != null ? signedINR(dayPnl) : null}
        tone={dayPnl == null ? "default" : dayPnl >= 0 ? "success" : "danger"}
        loading={positions == null} icon={<TrendingUp className="w-3.5 h-3.5" />} sub="MTM + realised" />
      <MetricCard label="Open Pos" value={openCount != null ? String(openCount) : null}
        loading={positions == null} icon={<Layers className="w-3.5 h-3.5" />} sub="from broker" />
      <MetricCard label="Guard" value={guardView.label}
        tone={guard == null ? "default" : guardView.tone}
        loading={guard == null} icon={<Shield className="w-3.5 h-3.5" />}
        sub={guardView.title || "auto-exit"} />
      <MetricCard label="Avail Margin" value={cash != null ? fmtINR(cash) : null}
        loading={limits == null} icon={<Wallet className="w-3.5 h-3.5" />} sub="broker net" />
      <MetricCard label="Working Ord" value={workCount != null ? String(workCount) : null}
        loading={orders == null} icon={<ClipboardList className="w-3.5 h-3.5" />} sub="from broker" />
      <MetricCard
        label="Day Stop"
        value={dayStop.worst ? fmtINR(dayStop.worst.cap) : "—"}
        tone={dayStop.tone === "danger" ? "danger" : dayStop.tone === "warn" ? "warn" : "default"}
        icon={<OctagonAlert className="w-3.5 h-3.5" />}
        sub={dayStopSub(dayStop)}
      />
    </div>
  );
}
