import { Fragment, useMemo } from "react";
import { Link } from "react-router-dom";
import { Layers } from "lucide-react";
import { fmtINR, fmtINRSigned, colorPnL } from "@/lib/fmt";
import { useInteractiveColumns } from "@/components/common/useInteractiveColumns";
import { ResetLayoutButton } from "@/components/common/ResetLayoutButton";

/**
 * LiveBlotter — deployment-attributed live trades from GET /live-broker/blotter.
 *
 * The raw position/order tables show what the BROKER holds; this answers WHICH
 * deployed strategy opened each live trade and how it's doing. LIVE rows take P&L
 * from the broker position book (the live truth); CLOSED rows take the realized
 * P&L journaled by the close-loop when the guard/stop squared them; FLAT rows
 * (unfilled / superseded / externally closed) carry no fabricated P&L.
 *
 * Presentational: `rows` are passed from the dashboard poll — no own poller, so
 * the page stays on one cadence. P&L color uses the shared @/lib/fmt colorPnL.
 */
function istTime(iso) {
  if (!iso) return "–";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "–";
  return d.toLocaleTimeString("en-IN", { hour12: false, timeZone: "Asia/Kolkata" });
}

const SIDE_CLASS = { LONG: "text-success", B: "text-success", SHORT: "text-danger", S: "text-danger" };

// Default column set for the interactive layout hook (drag-resize / drag-reorder,
// persisted per-table). Purely presentational — read-only rendering below is
// unchanged; only column width/order becomes user-adjustable.
const DEFAULT_COLUMNS = [
  { key: "time", label: "Time", align: "left", defaultWidth: 80 },
  { key: "strategy", label: "Strategy / Deployment", align: "left", defaultWidth: 170 },
  { key: "symbol", label: "Symbol", align: "left", defaultWidth: 110 },
  { key: "side", label: "Side", align: "center", defaultWidth: 60 },
  { key: "lots", label: "Lots", align: "right", defaultWidth: 60 },
  { key: "entry", label: "Entry", align: "right", defaultWidth: 80 },
  { key: "ltp", label: "LTP", align: "right", defaultWidth: 80 },
  { key: "pnl", label: "P&L", align: "right", defaultWidth: 90 },
  { key: "status", label: "Status", align: "center", defaultWidth: 90 },
];

export default function LiveBlotter({ rows, gtt }) {
  // al_id → { sl, tp } from the resting GTT/OCO book, so a backed position can
  // show its catastrophe band. oivariable legs: var_name "x" = SL, "y" = TP.
  const ocoByAlId = useMemo(() => {
    const m = {};
    for (const g of Array.isArray(gtt) ? gtt : []) {
      const id = g?.al_id ?? g?.Al_id ?? g?.AL_id;
      if (!id) continue;
      const legs = Array.isArray(g?.oivariable) ? g.oivariable : [];
      // Noren's GTT readback casing isn't guaranteed (GttBook lowercases + falls
      // back positionally) — mirror that so the SL/TP band still resolves: x=SL, y=TP.
      const byName = (n) => legs.find((l) => String(l?.var_name).toLowerCase() === n)?.d;
      const sl = byName("x") ?? legs[0]?.d;
      const tp = byName("y") ?? legs[1]?.d;
      m[String(id)] = { sl, tp };
    }
    return m;
  }, [gtt]);

  const { liveCount, closedCount, flatCount, livePnl, closedPnl } = useMemo(() => {
    const list = Array.isArray(rows) ? rows : [];
    let lc = 0, cc = 0, fc = 0, live = 0, closed = 0;
    for (const r of list) {
      const st = String(r?.status ?? (r?.at_broker ? "LIVE" : "FLAT"));
      const v = Number(r?.pnl);
      if (st === "LIVE") {
        lc += 1;
        if (Number.isFinite(v)) live += v;
      } else if (st === "CLOSED") {
        cc += 1;
        if (Number.isFinite(v)) closed += v;
      } else {
        fc += 1;
      }
    }
    return { liveCount: lc, closedCount: cc, flatCount: fc, livePnl: live, closedPnl: closed };
  }, [rows]);

  const { orderedColumns, getHeaderProps, getResizeHandleProps, resetLayout, isCustomized } = useInteractiveColumns({
    tableId: "live-blotter",
    columns: DEFAULT_COLUMNS,
    defaultWidth: 90,
  });

  if (rows == null) {
    return <div className="text-xs text-dimmer font-mono py-4 text-center">Loading live blotter&hellip;</div>;
  }

  if (rows.length === 0) {
    return (
      <div className="text-xs text-dimmer font-mono py-6 text-center">
        No live deployment trades yet. Armed deployments will record their auto-placed
        orders here, attributed to the strategy.
      </div>
    );
  }

  return (
    <div className="space-y-2">
      {/* Summary strip */}
      <div className="flex items-center gap-3 flex-wrap text-[11px] font-mono text-dimmer">
        <span className="inline-flex items-center gap-1 text-dim">
          <Layers className="w-3.5 h-3.5" /> {rows.length} trade{rows.length !== 1 ? "s" : ""}
        </span>
        <span>
          <b className="text-success">{liveCount}</b> live
        </span>
        {closedCount > 0 && (
          <span>
            <b className="text-sky-300">{closedCount}</b> closed
          </span>
        )}
        <span>
          <b className="text-dim">{flatCount}</b> flat
        </span>
        <span className="ml-auto flex items-center gap-3">
          {liveCount > 0 && (
            <span>
              live P&amp;L: <b className={colorPnL(livePnl)}>{fmtINRSigned(livePnl)}</b>
            </span>
          )}
          {closedCount > 0 && (
            <span>
              realized: <b className={colorPnL(closedPnl)}>{fmtINRSigned(closedPnl)}</b>
            </span>
          )}
        </span>
        <ResetLayoutButton onReset={resetLayout} isCustomized={isCustomized} label="live blotter" testid="live-blotter-reset-layout" />
      </div>

      <div className="overflow-x-auto">
        <table className="w-full text-xs font-mono tabular-nums" style={{ tableLayout: "fixed" }}>
          <colgroup>
            {orderedColumns.map((c) => <col key={c.key} style={{ width: `${c.width}px` }} />)}
          </colgroup>
          <thead>
            <tr className="border-b border-line text-dimmer uppercase tracking-wider text-[10px]">
              {orderedColumns.map((col, i) => {
                const headerProps = getHeaderProps(col.key);
                const alignCls = col.align === "right" ? "text-right" : col.align === "center" ? "text-center" : "text-left";
                const edgeCls = i === 0 ? "pl-0" : i === orderedColumns.length - 1 ? "pr-0" : "";
                return (
                  <th
                    key={col.key}
                    {...headerProps}
                    className={`relative py-2 px-3 ${alignCls} ${edgeCls} ${headerProps["data-drag-over"] ? "bg-bg-2" : ""}`}
                  >
                    {col.label}
                    <span
                      {...getResizeHandleProps(col.key)}
                      className="absolute top-0 right-0 h-full w-1.5 cursor-col-resize hover:bg-info/40"
                      onClick={(e) => e.stopPropagation()}
                    />
                  </th>
                );
              })}
            </tr>
          </thead>
          <tbody>
            {rows.map((r, i) => {
              const status = String(r?.status ?? (r?.at_broker ? "LIVE" : "FLAT"));
              const isFlat = status === "FLAT";   // truly empty rows dim; CLOSED keeps its P&L visible
              const side = String(r?.direction ?? "");

              // Cell content keyed to match DEFAULT_COLUMNS keys — identical markup
              // to the original fixed-order cells, just data-driven so drag-reorder
              // reorders body cells along with header cells.
              const cells = {
                time: <td className="py-2 px-3 text-dim">{istTime(r?.created_at)}</td>,
                strategy: (
                  <td className="py-2 px-3">
                    {r?.deployment_id ? (
                      <Link
                        to={`/journal?deployment=${encodeURIComponent(r.deployment_id)}`}
                        className="text-foreground font-semibold truncate max-w-[180px] block hover:text-info hover:underline"
                        title={`${r?.deployment_name ?? ""} — open in the Signal Journal`}
                      >
                        {r?.deployment_name ?? "–"}
                      </Link>
                    ) : (
                      <div className="text-foreground font-semibold truncate max-w-[180px]" title={r?.deployment_name}>
                        {r?.deployment_name ?? "–"}
                      </div>
                    )}
                    <div className="text-dimmer text-[10px]">{r?.strategy_id ?? ""}</div>
                  </td>
                ),
                symbol: <td className="py-2 px-3 text-foreground">{r?.trading_symbol || "–"}</td>,
                side: (
                  <td className={`py-2 px-3 text-center font-semibold ${SIDE_CLASS[side] ?? "text-dim"}`}>
                    {side || "–"}
                  </td>
                ),
                lots: <td className="py-2 px-3 text-right text-foreground">{r?.lots ?? "–"}</td>,
                entry: (
                  <td className="py-2 px-3 text-right text-dim">
                    {r?.entry_price != null ? fmtINR(r.entry_price, 2) : "–"}
                  </td>
                ),
                ltp: (
                  <td className="py-2 px-3 text-right text-foreground">
                    {r?.ltp != null ? fmtINR(r.ltp, 2) : "–"}
                  </td>
                ),
                pnl: (
                  <td className={`py-2 px-3 text-right font-semibold ${colorPnL(r?.pnl)}`}>
                    {r?.pnl != null ? fmtINRSigned(r.pnl) : "–"}
                  </td>
                ),
                status: (
                  <td className="py-2 px-3 text-center">
                    {status === "LIVE" ? (
                      <span className="inline-flex items-center gap-1 justify-center flex-wrap">
                        <span className="inline-block px-1.5 py-0.5 rounded text-[10px] border border-emerald-500/40 bg-emerald-500/10 text-emerald-300">
                          LIVE
                        </span>
                        {r?.oco_error ? (
                          <span
                            className="inline-block px-1.5 py-0.5 rounded text-[10px] border border-amber-500/40 bg-amber-500/10 text-warning"
                            title="The resting broker OCO failed to place — this position has NO PC-down broker backstop, only the software guard while the app is running."
                          >
                            no broker net
                          </span>
                        ) : r?.oco_al_id ? (
                          <span
                            className="inline-block px-1.5 py-0.5 rounded text-[10px] border border-emerald-500/50 bg-emerald-500/10 text-emerald-300"
                            title={
                              ocoByAlId[String(r.oco_al_id)]?.sl != null
                                ? `Resting broker OCO backstop — SL ₹${ocoByAlId[String(r.oco_al_id)].sl} · TP ₹${ocoByAlId[String(r.oco_al_id)].tp}`
                                : "Resting broker OCO backstop (PC-down protected)."
                            }
                          >
                            OCO &#10003;
                          </span>
                        ) : null}
                      </span>
                    ) : status === "CLOSED" ? (
                      <span
                        className="inline-block px-1.5 py-0.5 rounded text-[10px] border border-sky-500/40 bg-sky-500/10 text-sky-300"
                        title="Squared — realized P&L journaled by the software guard / stop close-loop. The exit price is the guard's last broker mark (an estimate, not a confirmed fill)."
                      >
                        CLOSED
                      </span>
                    ) : (
                      <span
                        className="inline-block px-1.5 py-0.5 rounded text-[10px] border border-line text-dimmer"
                        title="No live broker position and no journaled close — unfilled, superseded by a newer entry on the same symbol, or closed outside the app."
                      >
                        FLAT
                      </span>
                    )}
                  </td>
                ),
              };

              return (
                <tr
                  key={r?.id ?? r?.norenordno ?? i}
                  className={`border-b border-line/50 hover:bg-bg-2/40 transition-colors ${
                    isFlat ? "opacity-60" : ""
                  }`}
                >
                  {orderedColumns.map((col) => (
                    <Fragment key={col.key}>{cells[col.key]}</Fragment>
                  ))}
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
