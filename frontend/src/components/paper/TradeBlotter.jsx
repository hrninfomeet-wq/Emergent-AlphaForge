import { Fragment, useState } from "react";
import { fmtINR, fmtINRSigned, fmtNum, fmtPct, fmtDuration, colorPnL } from "@/lib/fmt";
import TradeSparkline from "./TradeSparkline";
import TradeDetailDrawer from "./TradeDetailDrawer";
import { Zap } from "lucide-react";

const IST_OFFSET_MS = 330 * 60 * 1000;
const pad = (n) => String(n).padStart(2, "0");
const istParts = (iso) => {
  if (!iso) return null;
  const d = new Date(new Date(iso).getTime() + IST_OFFSET_MS);
  return { day: `${d.getUTCFullYear()}-${pad(d.getUTCMonth() + 1)}-${pad(d.getUTCDate())}`,
           time: `${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}` };
};

export default function TradeBlotter({ rows, sort, onToggleSort, onCloseAtMarket, busy }) {
  const [open, setOpen] = useState(() => new Set());
  const toggle = (id) => setOpen((s) => { const n = new Set(s); n.has(id) ? n.delete(id) : n.add(id); return n; });
  const mark = (col) => (sort === col ? " ▲" : sort === `-${col}` ? " ▼" : null);
  const H = ({ col, children, right }) => (
    <th className={`p-2 ${right ? "text-right" : "text-left"} ${col ? "cursor-pointer hover:text-foreground" : ""}`}
      onClick={col ? () => onToggleSort(col) : undefined}>{children}{col ? mark(col) : null}</th>
  );
  return (
    <div className="rounded-lg border border-line bg-bg-1 overflow-x-auto" data-testid="paper-trade-blotter">
      <table className="w-full text-xs" data-testid="paper-trade-table">
        <thead className="sticky top-0 bg-bg-2 z-10">
          <tr className="text-dim border-b border-line">
            <H col="created_at">Date / time</H>
            <H>Strategy / contract</H>
            <H right>Side</H>
            <H col="entry_price" right>Entry→Exit</H>
            <H right>Dur</H>
            <H right>SL / TP</H>
            <H col="mfe_value" right>Max</H>
            <H col="mae_value" right>Min</H>
            <H right>Now</H>
            <H right>P&amp;L curve</H>
            <H col="realized_pnl" right>Net P&amp;L</H>
            <H right>P&amp;L%</H>
            <H right>Status</H>
            <H right>Actions</H>
          </tr>
        </thead>
        <tbody>
          {rows.length === 0 && (
            <tr><td colSpan="14" className="p-6 text-center text-dimmer">No paper trades match these filters.</td></tr>
          )}
          {rows.map((t) => {
            const isOpen = String(t.status || "").toUpperCase() === "OPEN";
            const a = t.analytics || {};
            const entry = istParts(t.created_at);
            const exit = istParts(t.closed_at);
            const net = isOpen ? a.running_pnl : Number(t.realized_pnl || 0);
            const notional = Number(t.entry_price || 0) * Number(t.quantity || 0);
            const pct = notional ? (Number(net || 0) / notional) * 100 : null;
            return (
              <Fragment key={t.id}>
                <tr className="border-b border-line hover:bg-bg-2 cursor-pointer" onClick={() => toggle(t.id)} data-testid="paper-trade-row">
                  <td className="p-2 font-mono whitespace-nowrap">{entry ? entry.day : "—"}<div className="text-dimmer">{entry ? entry.time : ""}</div></td>
                  <td className="p-2"><div className="font-medium truncate max-w-[150px]" title={t.deployment_name}>{t.deployment_name || t.strategy_id}</div><div className="text-dimmer font-mono truncate max-w-[150px]">{t.trading_symbol || t.instrument}</div></td>
                  <td className="p-2 text-right"><span className={`font-mono ${t.direction === "CE" ? "text-emerald-400" : t.direction === "PE" ? "text-red-400" : "text-dim"}`}>{t.direction || "—"}</span></td>
                  <td className="p-2 text-right font-mono whitespace-nowrap">{fmtNum(t.entry_price)}→{t.exit_price != null ? fmtNum(t.exit_price) : (isOpen ? "live" : "—")}</td>
                  <td className="p-2 text-right font-mono text-dim">{fmtDuration(a.duration_s)}</td>
                  <td className="p-2 text-right font-mono text-dimmer whitespace-nowrap">{a.sl ?? "—"} / {a.tp ?? "—"}</td>
                  <td className="p-2 text-right font-mono text-success">{fmtINRSigned(a.mfe_value)}</td>
                  <td className="p-2 text-right font-mono text-danger">{fmtINRSigned(a.mae_value)}</td>
                  <td className={`p-2 text-right font-mono ${colorPnL(a.running_pnl)}`}>{fmtINRSigned(a.running_pnl)}</td>
                  <td className="p-2 text-right"><div className="flex justify-end"><TradeSparkline points={a.spark} /></div></td>
                  <td className={`p-2 text-right font-mono ${colorPnL(net)}`}>{fmtINRSigned(net)}</td>
                  <td className={`p-2 text-right font-mono ${colorPnL(pct)}`}>{pct == null ? "—" : fmtPct(pct, 1)}</td>
                  <td className="p-2 text-right"><span className={`text-[10px] px-1.5 py-0.5 rounded border font-mono ${isOpen ? "border-emerald-500/40 text-emerald-300" : "border-line text-dim"}`}>{t.status}</span></td>
                  <td className="p-2 text-right" onClick={(e) => e.stopPropagation()}>
                    {isOpen && (
                      <button disabled={busy} onClick={() => onCloseAtMarket(t)} className="h-7 text-[11px] bg-bg-3 border border-line hover:bg-bg-2 px-2 rounded inline-flex items-center" data-testid="close-paper-trade" title="Close at last live mark">
                        <Zap className="w-3 h-3 mr-1" /> @ market
                      </button>
                    )}
                  </td>
                </tr>
                {open.has(t.id) && (
                  <tr><td colSpan="14" className="p-0"><TradeDetailDrawer trade={t} /></td></tr>
                )}
              </Fragment>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
