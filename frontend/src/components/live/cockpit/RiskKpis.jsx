import { TrendingUp, Layers, Shield, Wallet, ClipboardList, OctagonAlert } from "lucide-react";
import MetricCard from "@/components/live/MetricCard";
import { fmtINR } from "@/lib/fmt";
import {
  asPositionRows, asOrderRows, isOpenPosition, isWorkingOrder,
  deriveDayPnl, deriveCash, signedINR,
} from "@/components/live/liveHelpers";

/**
 * Compact live-risk KPI grid for the cockpit's right column — reuses MetricCard
 * and the exact derivations from liveHelpers so the numbers match the broker
 * blotters below.
 */
export default function RiskKpis({ limits, positions, orders, guard }) {
  const posRows = asPositionRows(positions);
  const ordRows = asOrderRows(orders);
  const openCount = posRows != null ? posRows.filter(isOpenPosition).length : null;
  const workCount = ordRows != null ? ordRows.filter(isWorkingOrder).length : null;
  const dayPnl = deriveDayPnl(positions);
  const cash = deriveCash(limits);
  const guardArmed = !!guard?.armed;

  return (
    <div className="grid grid-cols-3 gap-2">
      <MetricCard label="Day P&L" value={dayPnl != null ? signedINR(dayPnl) : null}
        tone={dayPnl == null ? "default" : dayPnl >= 0 ? "success" : "danger"}
        loading={positions == null} icon={<TrendingUp className="w-3.5 h-3.5" />} sub="MTM + realised" />
      <MetricCard label="Open Pos" value={openCount != null ? String(openCount) : null}
        loading={positions == null} icon={<Layers className="w-3.5 h-3.5" />} sub="from broker" />
      <MetricCard label="Guard" value={guard == null ? null : guardArmed ? "ARMED" : "DRY-RUN"}
        tone={guard == null ? "default" : guardArmed ? "danger" : "warn"}
        loading={guard == null} icon={<Shield className="w-3.5 h-3.5" />} sub="auto-exit" />
      <MetricCard label="Avail Margin" value={cash != null ? fmtINR(cash) : null}
        loading={limits == null} icon={<Wallet className="w-3.5 h-3.5" />} sub="broker net" />
      <MetricCard label="Working Ord" value={workCount != null ? String(workCount) : null}
        loading={orders == null} icon={<ClipboardList className="w-3.5 h-3.5" />} sub="from broker" />
      <MetricCard label="Day Stop" value="—" icon={<OctagonAlert className="w-3.5 h-3.5" />} sub="per-deployment" />
    </div>
  );
}
