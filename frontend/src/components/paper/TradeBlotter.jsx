import { Fragment, useState } from "react";
import { fmtINRSigned, fmtNum, fmtPct, fmtDuration, colorPnL } from "@/lib/fmt";
import TradeSparkline from "./TradeSparkline";
import TradeDetailDrawer from "./TradeDetailDrawer";
import { classifyExitReason, EXIT_REASON_OPTIONS } from "@/lib/exitReason";
import { useMaximize, MaximizeButton } from "@/components/MaximizeButton";
import { useInteractiveColumns } from "@/components/common/useInteractiveColumns";
import { ResetLayoutButton } from "@/components/common/ResetLayoutButton";
import { Zap } from "lucide-react";

const IST_OFFSET_MS = 330 * 60 * 1000;
const pad = (n) => String(n).padStart(2, "0");
const istParts = (iso) => {
  if (!iso) return null;
  const d = new Date(new Date(iso).getTime() + IST_OFFSET_MS);
  return { day: `${d.getUTCFullYear()}-${pad(d.getUTCMonth() + 1)}-${pad(d.getUTCDate())}`,
           time: `${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}` };
};

const COLSPAN = 19;
// Default column set (key, sort col if sortable, alignment, default width in px —
// derived proportionally from the original 100%-wide `table-fixed` layout so the
// unsized default look is unchanged). `useInteractiveColumns` persists per-column
// width/order overrides (drag-to-resize / drag-to-reorder) keyed by tableId, so
// these are only the DEFAULTS a fresh browser (or a "reset layout" click) sees.
const DEFAULT_COLUMNS = [
  { key: "entry_dt", label: "Entry Date/Time", sortCol: "created_at", right: false, defaultWidth: 84 },
  { key: "strategy", label: "Strategy", sortCol: null, right: false, defaultWidth: 130 },
  { key: "contract", label: "Contract", sortCol: null, right: false, defaultWidth: 130 },
  { key: "side", label: "Side", sortCol: null, right: true, defaultWidth: 60 },
  { key: "entry_price", label: "Entry Price", sortCol: "entry_price", right: true, defaultWidth: 90 },
  { key: "exit_price", label: "Exit Price", sortCol: "exit_price", right: true, defaultWidth: 90 },
  { key: "exit_dt", label: "Exit Date/Time", sortCol: "closed_at", right: false, defaultWidth: 90 },
  { key: "duration", label: "Duration", sortCol: null, right: true, defaultWidth: 70 },
  { key: "qty", label: "Qty (lots × size)", sortCol: null, right: true, defaultWidth: 100 },
  { key: "sl_tp", label: "SL / TP", sortCol: null, right: true, defaultWidth: 90 },
  { key: "mfe", label: "Max P&L", sortCol: "mfe_value", right: true, defaultWidth: 90 },
  { key: "mae", label: "Min P&L", sortCol: "mae_value", right: true, defaultWidth: 90 },
  { key: "pnl_pct", label: "P&L%", sortCol: null, right: true, defaultWidth: 70 },
  { key: "charges", label: "Charges", sortCol: null, right: true, defaultWidth: 70 },
  { key: "net_pnl", label: "Net P&L", sortCol: "realized_pnl", right: true, defaultWidth: 90 },
  { key: "curve", label: "P&L curve", sortCol: null, right: true, defaultWidth: 80 },
  { key: "status", label: "Status", sortCol: null, right: true, defaultWidth: 70 },
  { key: "exit_reason", label: "Exit Reason", sortCol: null, right: true, defaultWidth: 90 },
];

export default function TradeBlotter({
  rows, sort, onToggleSort, onCloseAtMarket, busy,
  selected, onToggleRow, onToggleAll, allClosedSelected,
  filters = {}, onSetFilter, strategyOptions = [],
}) {
  const { panelRef, maximized, toggleMaximize } = useMaximize();
  const [open, setOpen] = useState(() => new Set());
  const toggle = (id) => setOpen((s) => { const n = new Set(s); n.has(id) ? n.delete(id) : n.add(id); return n; });
  const mark = (col) => (sort === col ? " ▲" : sort === `-${col}` ? " ▼" : null);

  const { orderedColumns, getHeaderProps, getResizeHandleProps, resetLayout, isCustomized } = useInteractiveColumns({
    tableId: "paper-blotter",
    columns: DEFAULT_COLUMNS,
    defaultWidth: 90,
  });

  const H = ({ col }) => {
    const { key, label, sortCol, right, width } = col;
    const headerProps = getHeaderProps(key);
    return (
      <th
        {...headerProps}
        className={`relative px-1.5 py-1 align-bottom ${right ? "text-right" : "text-left"} ${sortCol ? "cursor-pointer hover:text-foreground" : ""} ${headerProps["data-drag-over"] ? "bg-bg-3" : ""}`}
        style={{ width: `${width}px` }}
        onClick={sortCol ? () => onToggleSort(sortCol) : undefined}
      >
        {label}{sortCol ? mark(sortCol) : null}
        <span
          {...getResizeHandleProps(key)}
          className="absolute top-0 right-0 h-full w-1.5 cursor-col-resize hover:bg-info/40"
          onClick={(e) => e.stopPropagation()}
        />
      </th>
    );
  };
  const FilterSelect = ({ k, title, testid, children }) => (
    <select value={filters[k] || ""} onChange={(e) => onSetFilter?.(k, e.target.value)} onClick={(e) => e.stopPropagation()}
      className="w-full h-6 rounded border border-line bg-bg-2 px-1 text-[10px] text-foreground" title={title} data-testid={testid}>
      {children}
    </select>
  );

  // Body-cell renderers keyed the same as DEFAULT_COLUMNS, so reordering the
  // headers reorders the matching body cells identically. Content/formatting
  // is byte-identical to the original fixed-order JSX — only WHICH cell to
  // emit at a given position is now data-driven.
  const cellRenderers = {
    entry_dt: (t, ctx) => (
      <td className="px-1.5 py-1 font-mono">{ctx.entry ? ctx.entry.day : "—"}<div className="text-dimmer">{ctx.entry ? ctx.entry.time : ""}</div></td>
    ),
    strategy: (t) => (
      <td className="px-1.5 py-1 overflow-hidden"><div className="font-medium truncate" title={t.deployment_name || t.strategy_id}>{t.deployment_name || t.strategy_id}</div></td>
    ),
    contract: (t) => (
      <td className="px-1.5 py-1 overflow-hidden"><div className="text-dim font-mono truncate" title={t.trading_symbol || t.instrument}>{t.trading_symbol || t.instrument}</div></td>
    ),
    side: (t) => (
      <td className="px-1.5 py-1 text-right"><span className={`font-mono ${t.direction === "CE" ? "text-emerald-400" : t.direction === "PE" ? "text-red-400" : "text-dim"}`}>{t.direction || "—"}</span></td>
    ),
    entry_price: (t) => (
      <td className="px-1.5 py-1 text-right font-mono">{fmtNum(t.entry_price)}</td>
    ),
    exit_price: (t, ctx) => (
      <td className="px-1.5 py-1 text-right font-mono">{t.exit_price != null ? fmtNum(t.exit_price) : (ctx.isOpen ? "live" : "—")}</td>
    ),
    exit_dt: (t, ctx) => (
      <td className="px-1.5 py-1 font-mono">{ctx.exit ? ctx.exit.day : "—"}<div className="text-dimmer">{ctx.exit ? ctx.exit.time : ""}</div></td>
    ),
    duration: (t, ctx) => (
      <td className="px-1.5 py-1 text-right font-mono text-dim">{fmtDuration(ctx.a.duration_s)}</td>
    ),
    qty: (t) => (
      <td className="px-1.5 py-1 text-right font-mono">{t.quantity != null ? fmtNum(t.quantity) : "—"}<div className="text-dimmer">{t.lots != null && t.lot_size != null ? `${t.lots} × ${t.lot_size}` : ""}</div></td>
    ),
    sl_tp: (t, ctx) => (
      <td className="px-1.5 py-1 text-right font-mono text-dimmer">{ctx.a.sl ?? "—"} / {ctx.a.tp ?? "—"}</td>
    ),
    mfe: (t, ctx) => (
      <td className="px-1.5 py-1 text-right font-mono text-success">{fmtINRSigned(ctx.a.mfe_value)}</td>
    ),
    mae: (t, ctx) => (
      <td className="px-1.5 py-1 text-right font-mono text-danger">{fmtINRSigned(ctx.a.mae_value)}</td>
    ),
    pnl_pct: (t, ctx) => (
      <td className={`px-1.5 py-1 text-right font-mono ${colorPnL(ctx.pct)}`}>{ctx.pct == null ? "—" : fmtPct(ctx.pct, 1)}</td>
    ),
    charges: (t, ctx) => (
      <td className="px-1.5 py-1 text-right font-mono text-dimmer"
        title={t.charges ? `Brokerage ${t.charges.brokerage} · STT ${t.charges.stt} · Exch ${t.charges.exchange_txn} · GST ${t.charges.gst} · SEBI ${t.charges.sebi} · Stamp ${t.charges.stamp_duty}${t.net_realized_pnl != null ? ` → net after charges ₹${t.net_realized_pnl}` : ""}` : "Charges are computed when the trade closes"}>
        {ctx.isOpen || t.total_charges == null ? "—" : `₹${fmtNum(t.total_charges, 0)}`}
      </td>
    ),
    net_pnl: (t, ctx) => (
      <td className={`px-1.5 py-1 text-right font-mono ${colorPnL(ctx.net)}`}>{fmtINRSigned(ctx.net)}</td>
    ),
    curve: (t, ctx) => (
      <td className="px-1.5 py-1 text-right"><div className="flex justify-end"><TradeSparkline points={ctx.a.spark} /></div></td>
    ),
    status: (t, ctx) => (
      <td className="px-1.5 py-1 text-right"><span className={`text-[10px] px-1.5 py-0.5 rounded border font-mono ${ctx.isOpen ? "border-emerald-500/40 text-emerald-300" : "border-line text-dim"}`}>{t.status}</span></td>
    ),
    exit_reason: (t, ctx) => (
      <td className="px-1.5 py-1 text-right" onClick={(e) => e.stopPropagation()}>
        {ctx.isOpen ? (
          <button disabled={busy} onClick={() => onCloseAtMarket(t)} className="h-6 text-[11px] bg-bg-3 border border-line hover:bg-bg-2 px-2 rounded inline-flex items-center" data-testid="close-paper-trade" title="Close at last live mark">
            <Zap className="w-3 h-3 mr-1" /> @ market
          </button>
        ) : (
          <span className="inline-block max-w-full truncate text-[10px] px-1.5 py-0.5 rounded border border-line text-dim" title={t.exit_reason || ""} data-testid="paper-exit-reason">{ctx.reason ? ctx.reason.label : "—"}</span>
        )}
      </td>
    ),
  };

  // Filter-row cells, keyed the same way — anything without a filter renders
  // an empty <td> as before.
  const filterRenderers = {
    strategy: () => (
      <td className="p-1">
        <FilterSelect k="strategy_id" title="Filter by strategy" testid="paper-strategy-filter">
          <option value="">All strategies</option>
          {strategyOptions.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
        </FilterSelect>
      </td>
    ),
    side: () => (
      <td className="p-1">
        <FilterSelect k="direction" title="Filter by side" testid="paper-side-filter">
          <option value="">All</option>
          <option value="CE">CE</option>
          <option value="PE">PE</option>
        </FilterSelect>
      </td>
    ),
    status: () => (
      <td className="p-1">
        <FilterSelect k="status" title="Filter by status" testid="paper-status-filter">
          <option value="">All</option>
          <option value="OPEN">Open</option>
          <option value="CLOSED">Closed</option>
        </FilterSelect>
      </td>
    ),
    exit_reason: () => (
      <td className="p-1">
        <FilterSelect k="exit_reason" title="Filter by exit reason" testid="paper-exit-reason-filter">
          <option value="">All</option>
          {EXIT_REASON_OPTIONS.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
        </FilterSelect>
      </td>
    ),
  };

  return (
    <div ref={panelRef} className={`rounded-lg border border-line bg-bg-1 ${maximized ? "flex flex-col overflow-hidden" : ""}`} data-testid="paper-trade-blotter">
      <div className="px-3 py-1.5 border-b border-line flex items-center shrink-0">
        <div className="text-[11px] font-semibold uppercase tracking-wider text-dim">Trades</div>
        <div className="ml-auto flex items-center gap-1.5">
          <ResetLayoutButton onReset={resetLayout} isCustomized={isCustomized} label="trades table" testid="paper-trades-reset-layout" />
          <MaximizeButton maximized={maximized} onToggle={toggleMaximize} label="trades" testid="paper-trades-maximize" />
        </div>
      </div>
      <div className={maximized ? "overflow-auto flex-1" : "overflow-x-auto"} style={maximized ? { minHeight: 0 } : undefined}>
        <table className="w-full table-fixed text-xs" data-testid="paper-trade-table">
          <colgroup>
            <col style={{ width: "28px" }} />
            {orderedColumns.map((c) => <col key={c.key} style={{ width: `${c.width}px` }} />)}
          </colgroup>
          <thead className="sticky top-0 bg-bg-2 z-10">
            <tr className="text-dim border-b border-line">
              <th className="px-1.5 py-1 text-center">
                <input type="checkbox" checked={!!allClosedSelected} onChange={onToggleAll} data-testid="paper-select-all" title="Select closed trades on this page" />
              </th>
              {orderedColumns.map((col) => <H key={col.key} col={col} />)}
            </tr>
            <tr className="text-dim border-b border-line bg-bg-1" data-testid="paper-filter-row">
              <td className="p-1" />
              {orderedColumns.map((col) => (
                <Fragment key={col.key}>
                  {filterRenderers[col.key] ? filterRenderers[col.key]() : <td className="p-1" />}
                </Fragment>
              ))}
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
              const ctx = { isOpen, a, entry, exit, net, pct, reason };
              return (
                <Fragment key={t.id}>
                  <tr className="border-b border-line hover:bg-bg-2 cursor-pointer" onClick={() => toggle(t.id)} data-testid="paper-trade-row">
                    <td className="px-1.5 py-1 text-center" onClick={(e) => e.stopPropagation()}>
                      {!isOpen && (
                        <input type="checkbox" checked={selected?.has(t.id) || false} onChange={() => onToggleRow?.(t.id)} data-testid="paper-row-select" />
                      )}
                    </td>
                    {orderedColumns.map((col) => (
                      <Fragment key={col.key}>{cellRenderers[col.key](t, ctx)}</Fragment>
                    ))}
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
