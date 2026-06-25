import { useMemo } from "react";
import { Link } from "react-router-dom";
import { Layers } from "lucide-react";
import { fmtINR, fmtINRSigned, colorPnL } from "@/lib/fmt";

/**
 * LiveBlotter — deployment-attributed live trades from GET /live-broker/blotter.
 *
 * The raw position/order tables show what the BROKER holds; this answers WHICH
 * deployed strategy opened each live trade and how it's doing. P&L comes from the
 * live broker position book (the source of truth), joined to the live_trades
 * journal for attribution. Rows whose symbol is no longer held at the broker show
 * as FLAT with no fabricated P&L (the journal has no close-loop yet).
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

export default function LiveBlotter({ rows }) {
  const { liveCount, flatCount, livePnl } = useMemo(() => {
    const list = Array.isArray(rows) ? rows : [];
    let lc = 0;
    let fc = 0;
    let pnl = 0;
    for (const r of list) {
      if (r?.at_broker) {
        lc += 1;
        const v = Number(r?.pnl);
        if (Number.isFinite(v)) pnl += v;
      } else {
        fc += 1;
      }
    }
    return { liveCount: lc, flatCount: fc, livePnl: pnl };
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
        <span>
          <b className="text-dim">{flatCount}</b> flat
        </span>
        {liveCount > 0 && (
          <span className="ml-auto">
            live P&amp;L:{" "}
            <b className={colorPnL(livePnl)}>{fmtINRSigned(livePnl)}</b>
          </span>
        )}
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
              const flat = !r?.at_broker;
              const side = String(r?.direction ?? "");
              return (
                <tr
                  key={r?.id ?? r?.norenordno ?? i}
                  className={`border-b border-line/50 hover:bg-bg-2/40 transition-colors ${
                    flat ? "opacity-60" : ""
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
                    {flat ? (
                      <span
                        className="inline-block px-1.5 py-0.5 rounded text-[10px] border border-line text-dimmer"
                        title="No live broker position for this row — squared, unfilled, or superseded by a newer entry on the same symbol. Realized P&L is not journaled yet (no close-loop)."
                      >
                        FLAT
                      </span>
                    ) : (
                      <span className="inline-block px-1.5 py-0.5 rounded text-[10px] border border-emerald-500/40 bg-emerald-500/10 text-emerald-300">
                        LIVE
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
