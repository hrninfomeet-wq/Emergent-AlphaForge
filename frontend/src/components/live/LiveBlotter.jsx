import { useMemo } from "react";
import { Link } from "react-router-dom";
import { Layers } from "lucide-react";
import { fmtINR, fmtINRSigned, colorPnL } from "@/lib/fmt";

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

export default function LiveBlotter({ rows, gtt }) {
  // al_id → { sl, tp } from the resting GTT/OCO book, so a backed position can
  // show its catastrophe band. oivariable legs: var_name "x" = SL, "y" = TP.
  const ocoByAlId = useMemo(() => {
    const m = {};
    for (const g of Array.isArray(gtt) ? gtt : []) {
      const id = g?.al_id ?? g?.Al_id;
      if (!id) continue;
      const legs = Array.isArray(g?.oivariable) ? g.oivariable : [];
      const sl = legs.find((l) => l?.var_name === "x")?.d;
      const tp = legs.find((l) => l?.var_name === "y")?.d;
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
      </div>

      <div className="overflow-x-auto">
        <table className="w-full text-xs font-mono tabular-nums">
          <thead>
            <tr className="border-b border-line text-dimmer uppercase tracking-wider text-[10px]">
              <th className="text-left py-2 pr-3 pl-0">Time</th>
              <th className="text-left py-2 px-3">Strategy / Deployment</th>
              <th className="text-left py-2 px-3">Symbol</th>
              <th className="text-center py-2 px-3">Side</th>
              <th className="text-right py-2 px-3">Lots</th>
              <th className="text-right py-2 px-3">Entry</th>
              <th className="text-right py-2 px-3">LTP</th>
              <th className="text-right py-2 px-3">P&amp;L</th>
              <th className="text-center py-2 pl-3 pr-0">Status</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r, i) => {
              const status = String(r?.status ?? (r?.at_broker ? "LIVE" : "FLAT"));
              const isFlat = status === "FLAT";   // truly empty rows dim; CLOSED keeps its P&L visible
              const side = String(r?.direction ?? "");
              return (
                <tr
                  key={r?.id ?? r?.norenordno ?? i}
                  className={`border-b border-line/50 hover:bg-bg-2/40 transition-colors ${
                    isFlat ? "opacity-60" : ""
                  }`}
                >
                  <td className="py-2 pr-3 pl-0 text-dim">{istTime(r?.created_at)}</td>
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
                  <td className="py-2 px-3 text-foreground">{r?.trading_symbol || "–"}</td>
                  <td className={`py-2 px-3 text-center font-semibold ${SIDE_CLASS[side] ?? "text-dim"}`}>
                    {side || "–"}
                  </td>
                  <td className="py-2 px-3 text-right text-foreground">{r?.lots ?? "–"}</td>
                  <td className="py-2 px-3 text-right text-dim">
                    {r?.entry_price != null ? fmtINR(r.entry_price, 2) : "–"}
                  </td>
                  <td className="py-2 px-3 text-right text-foreground">
                    {r?.ltp != null ? fmtINR(r.ltp, 2) : "–"}
                  </td>
                  <td className={`py-2 px-3 text-right font-semibold ${colorPnL(r?.pnl)}`}>
                    {r?.pnl != null ? fmtINRSigned(r.pnl) : "–"}
                  </td>
                  <td className="py-2 pl-3 pr-0 text-center">
                    {status === "LIVE" ? (
                      <span className="inline-flex items-center gap-1 justify-center flex-wrap">
                        <span className="inline-block px-1.5 py-0.5 rounded text-[10px] border border-emerald-500/40 bg-emerald-500/10 text-emerald-300">
                          LIVE
                        </span>
                        {r?.oco_error ? (
                          <span
                            className="inline-block px-1.5 py-0.5 rounded text-[10px] border border-amber-500/40 bg-amber-500/10 text-amber-300"
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
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
