import { useMemo } from "react";
import {
  Area,
  AreaChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { fmtINR } from "@/lib/fmt";

/**
 * PayoffChart — option P&L-at-expiry curve for the order ticket.
 *
 * Props: { underlying, strike, premium, optionType("CE"|"PE"), side("B"|"S"),
 *          lotSize, lots }.
 * Long CE per-unit = max(0, spot-strike) - premium; long PE = max(0, strike-spot) - premium.
 * Short negates. Total = perUnit × lotSize × lots. Emerald above breakeven, red below.
 */
export default function PayoffChart({
  underlying,
  strike,
  premium,
  optionType = "CE",
  side = "B",
  lotSize = 1,
  lots = 1,
}) {
  const k = parseFloat(strike);
  const prem = parseFloat(premium);
  const qty = (parseInt(lotSize, 10) || 1) * (parseInt(lots, 10) || 1);
  const valid =
    Number.isFinite(k) && k > 0 && Number.isFinite(prem) && prem > 0 && qty > 0;
  const isCE = String(optionType).toUpperCase() === "CE";
  const long = String(side).toUpperCase() === "B";

  const { data, breakeven, maxLoss, maxLossLabel, zeroOffset } = useMemo(() => {
    if (!valid) {
      return { data: [], breakeven: null, maxLoss: null, maxLossLabel: "", zeroOffset: 0.5 };
    }
    const lo = k * 0.92;
    const hi = k * 1.08;
    const n = 56;
    const pts = [];
    let minP = Infinity;
    let maxP = -Infinity;
    for (let i = 0; i <= n; i++) {
      const s = lo + (hi - lo) * (i / n);
      const intrinsic = isCE ? Math.max(0, s - k) : Math.max(0, k - s);
      let perUnit = intrinsic - prem;
      if (!long) perUnit = -perUnit;
      const pnl = perUnit * qty;
      pts.push({ spot: Math.round(s), pnl: Math.round(pnl) });
      if (pnl < minP) minP = pnl;
      if (pnl > maxP) maxP = pnl;
    }
    const be = isCE ? k + prem : k - prem; // long breakeven
    // Long buyer: max loss = premium paid. Seller: theoretically large; show premium received as max profit.
    const ml = long ? prem * qty : prem * qty;
    const off = maxP === minP ? 0.5 : maxP / (maxP - minP);
    return {
      data: pts,
      breakeven: Math.round(be),
      maxLoss: ml,
      maxLossLabel: long ? "Max loss" : "Max profit",
      zeroOffset: Math.max(0, Math.min(1, off)),
    };
  }, [k, prem, qty, isCE, long, valid]);

  if (!valid) {
    return (
      <div className="text-[10px] text-dimmer font-mono py-6 text-center">
        Enter strike + premium to see the payoff
      </div>
    );
  }

  return (
    <div className="space-y-1.5">
      <div className="flex items-center justify-between text-[10px] font-mono text-dimmer">
        <span className="uppercase tracking-wider font-semibold">Payoff at expiry</span>
        <span>
          BE ≈ <span className="text-foreground">{breakeven}</span>
          <span className="mx-1.5">·</span>
          {maxLossLabel} <span className={long ? "text-danger" : "text-success"}>{fmtINR(maxLoss)}</span>
          <span className="mx-1.5">·</span>
          {qty} qty
        </span>
      </div>
      <div style={{ width: "100%", height: 150 }}>
        <ResponsiveContainer>
          <AreaChart data={data} margin={{ top: 6, right: 6, bottom: 0, left: 0 }}>
            <defs>
              <linearGradient id="pfFill" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor="#34d399" stopOpacity={0.35} />
                <stop offset={`${Math.round(zeroOffset * 100)}%`} stopColor="#34d399" stopOpacity={0.05} />
                <stop offset={`${Math.round(zeroOffset * 100)}%`} stopColor="#f87171" stopOpacity={0.05} />
                <stop offset="100%" stopColor="#f87171" stopOpacity={0.35} />
              </linearGradient>
              <linearGradient id="pfStroke" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor="#34d399" />
                <stop offset={`${Math.round(zeroOffset * 100)}%`} stopColor="#34d399" />
                <stop offset={`${Math.round(zeroOffset * 100)}%`} stopColor="#f87171" />
                <stop offset="100%" stopColor="#f87171" />
              </linearGradient>
            </defs>
            <XAxis
              dataKey="spot"
              tick={{ fontSize: 9, fill: "#7a7a7a", fontFamily: "monospace" }}
              tickLine={false}
              axisLine={{ stroke: "#333" }}
              interval="preserveStartEnd"
              minTickGap={40}
            />
            <YAxis
              tick={{ fontSize: 9, fill: "#7a7a7a", fontFamily: "monospace" }}
              tickLine={false}
              axisLine={false}
              width={42}
              tickFormatter={(v) => (Math.abs(v) >= 1000 ? `${Math.round(v / 1000)}k` : `${v}`)}
            />
            <ReferenceLine y={0} stroke="#555" strokeWidth={1} />
            <ReferenceLine x={Math.round(k)} stroke="#555" strokeDasharray="3 3" />
            <Tooltip
              contentStyle={{
                background: "#111", border: "1px solid #333", borderRadius: 6,
                fontSize: 11, fontFamily: "monospace", padding: "4px 8px",
              }}
              labelStyle={{ color: "#999" }}
              formatter={(v) => [fmtINR(v), "P&L"]}
              labelFormatter={(l) => `${underlying || ""} ${l}`}
            />
            <Area
              type="monotone"
              dataKey="pnl"
              stroke="url(#pfStroke)"
              strokeWidth={1.5}
              fill="url(#pfFill)"
              isAnimationActive={false}
              dot={false}
            />
          </AreaChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}
