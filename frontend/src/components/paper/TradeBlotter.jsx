import { Fragment, useState } from "react";
import { fmtINRSigned, fmtNum, fmtPct, fmtDuration, colorPnL } from "@/lib/fmt";
import TradeSparkline from "./TradeSparkline";
import TradeDetailDrawer from "./TradeDetailDrawer";
import { classifyExitReason, EXIT_REASON_OPTIONS } from "@/lib/exitReason";
import { useMaximize, MaximizeButton } from "@/components/MaximizeButton";
import { Zap } from "lucide-react";

const IST_OFFSET_MS = 330 * 60 * 1000;
const pad = (n) => String(n).padStart(2, "0");
const istParts = (iso) => {
  if (!iso) return null;
  const d = new Date(new Date(iso).getTime() + IST_OFFSET_MS);
  return { day: `${d.getUTCFullYear()}-${pad(d.getUTCMonth() + 1)}-${pad(d.getUTCDate())}`,
           time: `${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}` };
};

const COLSPAN = 17;
// Proportional column widths (%) so the table auto-fits the pane via `table-fixed`
// — no horizontal scroll. Sum = 100. Numeric columns are sized for real ₹ values;
// only the long Strategy/Contract text truncates (with a hover tooltip).
const COLW = [3, 7, 8, 3, 5, 5, 7, 4, 7, 8, 6, 6, 5, 6, 5, 5, 10];

export default function TradeBlotter({
  rows, sort, onToggleSort, onCloseAtMarket, busy,
  selected, onToggleRow, onToggleAll, allClosedSelected,
  filters = {}, onSetFilter, strategyOptions = [],
}) {
  const { panelRef, maximized, toggleMaximize } = useMaximize();
  const [open, setOpen] = useState(() => new Set());
  const toggle = (id) => setOpen((s) => { const n = new Set(s); n.has(id) ? n.delete(id) : n.add(id); return n; });
  const mark = (col) => (sort === col ? " ▲" : sort === `-${col}` ? " ▼" : null);
  const H = ({ col, children, right }) => (
    <th className={`px-1.5 py-1 align-bottom ${right ? "text-right" : "text-left"} ${col ? "cursor-pointer hover:text-foreground" : ""}`}
      onClick={col ? () => onToggleSort(col) : undefined}>{children}{col ? mark(col) : null}</th>
  );
  const FilterSelect = ({ k, title, testid, children }) => (
    <select value={filters[k] || ""} onChange={(e) => onSetFilter?.(k, e.target.value)} onClick={(e) => e.stopPropagation()}
      className="w-full h-6 rounded border border-line bg-bg-2 px-1 text-[10px] text-foreground" title={title} data-testid={testid}>
      {children}
    </select>
  );
  return (
    <div ref={panelRef} className={`rounded-lg border border-line bg-bg-1 ${maximized ? "flex flex-col overflow-hidden" : ""}`} data-testid="paper-trade-blotter">
      <div className="px-3 py-1.5 border-b border-line flex items-center shrink-0">
        <div className="text-[11px] font-semibold uppercase tracking-wider text-dim">Trades</div>
        <div className="ml-auto">
          <MaximizeButton maximized={maximized} onToggle={toggleMaximize} label="trades" testid="paper-trades-maximize" />
        </div>
      </div>
      <div className={maximized ? "overflow-auto flex-1" : "overflow-x-auto"} style={maximized ? { minHeight: 0 } : undefined}>
        <table className="w-full table-fixed text-xs" data-testid="paper-trade-table">
          <colgroup>
            {COLW.map((w, i) => <col key={i} style={{ width: `${w}%` }} />)}
          </colgroup>
          <thead className="sticky top-0 bg-bg-2 z-10">
            <tr className="text-dim border-b border-line">
              <th className="px-1.5 py-1 text-center">
                <input type="checkbox" checked={!!allClosedSelected} onChange={onToggleAll} data-testid="paper-select-all" title="Select closed trades on this page" />
              </th>
              <H col="created_at">Entry Date/Time</H>
              <H>Strategy / Contract</H>
              <H right>Side</H>
              <H col="entry_price" right>Entry Price</H>
              <H col="exit_price" right>Exit Price</H>
              <H col="closed_at">Exit Date/Time</H>
              <H right>Duration</H>
              <H right>Qty (lots × size)</H>
              <H right>SL / TP</H>
              <H col="mfe_value" right>Max P&amp;L</H>
              <H col="mae_value" right>Min P&amp;L</H>
              <H right>P&amp;L%</H>
              <H col="realized_pnl" right>Net P&amp;L</H>
              <H right>P&amp;L curve</H>
              <H right>Status</H>
              <H right>Exit Reason</H>
            </tr>
            <tr className="text-dim border-b border-line bg-bg-1" data-testid="paper-filter-row">
              <td className="p-1" />
              <td className="p-1" />
              <td className="p-1">
                <FilterSelect k="strategy_id" title="Filter by strategy" testid="paper-strategy-filter">
                  <option value="">All strategies</option>
                  {strategyOptions.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
                </FilterSelect>
              </td>
              <td className="p-1">
                <FilterSelect k="direction" title="Filter by side" testid="paper-side-filter">
                  <option value="">All</option>
                  <option value="CE">CE</option>
                  <option value="PE">PE</option>
                </FilterSelect>
              </td>
              <td className="p-1" /><td className="p-1" /><td className="p-1" /><td className="p-1" />
              <td className="p-1" /><td className="p-1" /><td className="p-1" /><td className="p-1" />
              <td className="p-1" /><td className="p-1" /><td className="p-1" />
              <td className="p-1">
                <FilterSelect k="status" title="Filter by status" testid="paper-status-filter">
                  <option value="">All</option>
                  <option value="OPEN">Open</option>
                  <option value="CLOSED">Closed</option>
                </FilterSelect>
              </td>
              <td className="p-1">
                <FilterSelect k="exit_reason" title="Filter by exit reason" testid="paper-exit-reason-filter">
                  <option value="">All</option>
                  {EXIT_REASON_OPTIONS.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
                </FilterSelect>
              </td>
            </tr>
          </thead>
          <tbody>
            {rows.length === 0 && (
              <tr><td colSpan={COLSPAN} className="p-6 text-center text-dimmer">No paper trades match these filters.</td></tr>
            )}
            {rows.map((t) => {
              const isOpen = String(t.status || "").toUpperCase() === "OPEN";
              const a = t.analytics || {};
              const entry = istParts(t.created_at);
              const exit = istParts(t.closed_at);
              const net = isOpen ? a.running_pnl : Number(t.realized_pnl || 0);
              const notional = Number(t.entry_price || 0) * Number(t.quantity || 0);
              const pct = notional ? (Number(net || 0) / notional) * 100 : null;
              const reason = isOpen ? null : classifyExitReason(t.exit_reason);
              return (
                <Fragment key={t.id}>
                  <tr className="border-b border-line hover:bg-bg-2 cursor-pointer" onClick={() => toggle(t.id)} data-testid="paper-trade-row">
                    <td className="px-1.5 py-1 text-center" onClick={(e) => e.stopPropagation()}>
                      {!isOpen && (
                        <input type="checkbox" checked={selected?.has(t.id) || false} onChange={() => onToggleRow?.(t.id)} data-testid="paper-row-select" />
                      )}
                    </td>
                    <td className="px-1.5 py-1 font-mono">{entry ? entry.day : "—"}<div className="text-dimmer">{entry ? entry.time : ""}</div></td>
                    <td className="px-1.5 py-1 overflow-hidden"><div className="font-medium truncate" title={t.deployment_name}>{t.deployment_name || t.strategy_id}</div><div className="text-dimmer font-mono truncate" title={t.trading_symbol || t.instrument}>{t.trading_symbol || t.instrument}</div></td>
                    <td className="px-1.5 py-1 text-right"><span className={`font-mono ${t.direction === "CE" ? "text-emerald-400" : t.direction === "PE" ? "text-red-400" : "text-dim"}`}>{t.direction || "—"}</span></td>
                    <td className="px-1.5 py-1 text-right font-mono">{fmtNum(t.entry_price)}</td>
                    <td className="px-1.5 py-1 text-right font-mono">{t.exit_price != null ? fmtNum(t.exit_price) : (isOpen ? "live" : "—")}</td>
                    <td className="px-1.5 py-1 font-mono">{exit ? exit.day : "—"}<div className="text-dimmer">{exit ? exit.time : ""}</div></td>
                    <td className="px-1.5 py-1 text-right font-mono text-dim">{fmtDuration(a.duration_s)}</td>
                    <td className="px-1.5 py-1 text-right font-mono">{t.quantity != null ? fmtNum(t.quantity) : "—"}<div className="text-dimmer">{t.lots != null && t.lot_size != null ? `${t.lots} × ${t.lot_size}` : ""}</div></td>
                    <td className="px-1.5 py-1 text-right font-mono text-dimmer">{a.sl ?? "—"} / {a.tp ?? "—"}</td>
                    <td className="px-1.5 py-1 text-right font-mono text-success">{fmtINRSigned(a.mfe_value)}</td>
                    <td className="px-1.5 py-1 text-right font-mono text-danger">{fmtINRSigned(a.mae_value)}</td>
                    <td className={`px-1.5 py-1 text-right font-mono ${colorPnL(pct)}`}>{pct == null ? "—" : fmtPct(pct, 1)}</td>
                    <td className={`px-1.5 py-1 text-right font-mono ${colorPnL(net)}`}>{fmtINRSigned(net)}</td>
                    <td className="px-1.5 py-1 text-right"><div className="flex justify-end"><TradeSparkline points={a.spark} /></div></td>
                    <td className="px-1.5 py-1 text-right"><span className={`text-[10px] px-1.5 py-0.5 rounded border font-mono ${isOpen ? "border-emerald-500/40 text-emerald-300" : "border-line text-dim"}`}>{t.status}</span></td>
                    <td className="px-1.5 py-1 text-right" onClick={(e) => e.stopPropagation()}>
                      {isOpen ? (
                        <button disabled={busy} onClick={() => onCloseAtMarket(t)} className="h-6 text-[11px] bg-bg-3 border border-line hover:bg-bg-2 px-2 rounded inline-flex items-center" data-testid="close-paper-trade" title="Close at last live mark">
                          <Zap className="w-3 h-3 mr-1" /> @ market
                        </button>
                      ) : (
                        <span className="inline-block max-w-full truncate text-[10px] px-1.5 py-0.5 rounded border border-line text-dim" title={t.exit_reason || ""} data-testid="paper-exit-reason">{reason ? reason.label : "—"}</span>
                      )}
                    </td>
                  </tr>
                  {open.has(t.id) && (
                    <tr><td colSpan={COLSPAN} className="p-0"><TradeDetailDrawer trade={t} /></td></tr>
                  )}
                </Fragment>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
