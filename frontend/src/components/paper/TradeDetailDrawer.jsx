import { Area, AreaChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { fmtINR, fmtINRSigned, fmtNum } from "@/lib/fmt";

export default function TradeDetailDrawer({ trade }) {
  const a = trade.analytics || {};
  const qty = Number(trade.quantity || 0);
  // P&L curve in ₹; SL/TP are premium levels -> convert to ₹ vs entry for the reference lines.
  const entry = Number(trade.entry_price || 0);
  const slPnl = a.sl != null && qty ? (Number(a.sl) - entry) * qty : null;
  const tpPnl = a.tp != null && qty ? (Number(a.tp) - entry) * qty : null;
  const data = (a.spark || []).map((p) => ({ t: p.t, pnl: p.pnl }));
  return (
    <div className="bg-bg-0 border-t border-line p-3" data-testid="paper-trade-detail">
      <div className="grid lg:grid-cols-[2fr_1fr] gap-4">
        <div className="h-[160px]">
          {data.length >= 2 ? (
            <ResponsiveContainer width="100%" height="100%">
              <AreaChart data={data} margin={{ top: 6, right: 8, bottom: 0, left: 0 }}>
                <defs>
                  <linearGradient id={`pnl-${trade.id}`} x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stopColor="var(--color-info)" stopOpacity={0.25} />
                    <stop offset="100%" stopColor="var(--color-info)" stopOpacity={0} />
                  </linearGradient>
                </defs>
                <XAxis dataKey="t" hide />
                <YAxis tick={{ fontSize: 10 }} width={52} tickFormatter={(v) => fmtINR(v)} />
                <Tooltip formatter={(v) => fmtINRSigned(v)} labelFormatter={() => ""} />
                <ReferenceLine y={0} stroke="var(--border-1)" strokeDasharray="2,2" />
                {slPnl != null && <ReferenceLine y={slPnl} stroke="var(--color-danger)" strokeDasharray="3,3" label={{ value: "SL", fontSize: 10, fill: "var(--color-danger)" }} />}
                {tpPnl != null && <ReferenceLine y={tpPnl} stroke="var(--color-success)" strokeDasharray="3,3" label={{ value: "TP", fontSize: 10, fill: "var(--color-success)" }} />}
                <Area type="monotone" dataKey="pnl" stroke="var(--color-info)" strokeWidth={1.5} fill={`url(#pnl-${trade.id})`} />
              </AreaChart>
            </ResponsiveContainer>
          ) : (
            <div className="text-[11px] text-dimmer font-mono pt-6">No intra-trade marks recorded.</div>
          )}
        </div>
        <div className="text-[11px] font-mono space-y-1">
          <Row k="Max P&amp;L (MFE)" v={fmtINRSigned(a.mfe_value)} cls="text-success" />
          <Row k="Min P&amp;L (MAE)" v={fmtINRSigned(a.mae_value)} cls="text-danger" />
          <Row k="Running P&amp;L" v={fmtINRSigned(a.running_pnl)} />
          <Row k="Last SL / TP" v={`${a.sl ?? "—"} / ${a.tp ?? "—"}`} />
          {trade.friction_cost != null && Number(trade.friction_cost) !== 0 && (
            <>
              <Row k="Gross" v={fmtINR(trade.gross_realized_pnl)} />
              <Row k="Friction" v={`−${fmtINR(Math.abs(Number(trade.friction_cost)))}`} />
            </>
          )}
          {trade.total_charges != null && Number(trade.total_charges) > 0 && (
            <>
              <Row k="Charges (total)" v={`−${fmtINR(trade.total_charges)}`} />
              {trade.charges && (
                <Row k="↳ breakdown"
                  v={`STT ${fmtINR(trade.charges.stt)} · Exch ${fmtINR(trade.charges.exchange_txn)} · GST ${fmtINR(trade.charges.gst)} · Stamp ${fmtINR(trade.charges.stamp_duty)} · SEBI ${fmtINR(trade.charges.sebi)} · Brkg ${fmtINR(trade.charges.brokerage)}`}
                  cls="text-dimmer" />
              )}
              {trade.net_realized_pnl != null && (
                <Row k="Net after charges" v={fmtINRSigned(trade.net_realized_pnl)}
                  cls={Number(trade.net_realized_pnl) >= 0 ? "text-success" : "text-danger"} />
              )}
            </>
          )}
        </div>
      </div>
    </div>
  );
}

function Row({ k, v, cls = "" }) {
  return (
    <div className="flex justify-between gap-3">
      <span className="text-dimmer">{k}</span>
      <span className={cls}>{v}</span>
    </div>
  );
}
