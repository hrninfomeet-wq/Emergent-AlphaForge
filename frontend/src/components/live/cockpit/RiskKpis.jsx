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
/**
 * Day-stop tile data: today's realised loss against the configured daily-loss
 * cap, aggregated across LIVE deployments only (a paper deployment's cap does
 * not gate real money). Returns nulls when nothing is live, so the tile says
 * "no live deployment" instead of rendering a permanently blank "—".
 */
function deriveDayStop(deployments, deployLive) {
  const live = (deployments || []).filter(
    (d) => String(d?.mode || "").toLowerCase() === "live",
  );
  if (!live.length) return { cap: null, used: null, any: false };
  let cap = 0;
  let used = 0;
  let sawCap = false;
  for (const d of live) {
    const st = (deployLive || {})[d.id];
    const c = Number(st?.caps?.daily_loss_cap);
    if (Number.isFinite(c) && c > 0) { cap += c; sawCap = true; }
    const r = Number(st?.today?.realized_pnl);
    if (Number.isFinite(r) && r < 0) used += Math.abs(r);
  }
  return { cap: sawCap ? cap : null, used: sawCap ? used : null, any: true };
}

export default function RiskKpis({ limits, positions, orders, guard, deployments, deployLive }) {
  const posRows = asPositionRows(positions);
  const ordRows = asOrderRows(orders);
  const openCount = posRows != null ? posRows.filter(isOpenPosition).length : null;
  const workCount = ordRows != null ? ordRows.filter(isWorkingOrder).length : null;
  const dayPnl = deriveDayPnl(positions);
  const cash = deriveCash(limits);
  // Match GuardPanel EXACTLY: default to armed when the field is absent. `!!undefined`
  // would render "DRY-RUN" over positions that are in fact being auto-exited for real —
  // the fail-DANGEROUS direction. Only an explicit `false` downgrades it.
  const guardArmed = guard?.armed !== false;
  const dayStop = deriveDayStop(deployments, deployLive);

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
      <MetricCard
        label="Day Stop"
        value={dayStop.cap != null ? fmtINR(dayStop.cap) : "—"}
        tone={dayStop.cap != null && dayStop.used >= dayStop.cap * 0.75 ? "danger" : "default"}
        icon={<OctagonAlert className="w-3.5 h-3.5" />}
        sub={
          dayStop.cap != null
            ? `${fmtINR(dayStop.used || 0)} used today`
            : dayStop.any
            ? "no cap configured"
            : "no live deployment"
        }
      />
    </div>
  );
}
