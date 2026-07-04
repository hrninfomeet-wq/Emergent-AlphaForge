import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import { fmtINRSigned, fmtNum, fmtPct, colorPnL } from "@/lib/fmt";
import { BarChart3, RefreshCw } from "lucide-react";

const IST_OFFSET_MS = 330 * 60 * 1000;
const pad = (n) => String(n).padStart(2, "0");
function istStamp(iso) {
  if (!iso) return null;
  const d = new Date(new Date(iso).getTime() + IST_OFFSET_MS);
  if (Number.isNaN(d.getTime())) return null;
  return `${d.getUTCFullYear()}-${pad(d.getUTCMonth() + 1)}-${pad(d.getUTCDate())} ${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}`;
}

/**
 * LiveTradeStats — analysis over the journaled live_trades (close-loop realized
 * P&L): period P&L, win rate, profit factor + per-strategy breakdown.
 *
 * Fetches on mount + manual refresh (no interval: history changes only when a
 * live trade closes, and the page's live cadence belongs to LiveDataProvider).
 */
function Stat({ label, value, tone }) {
  return (
    <div>
      <div className="text-[10px] uppercase tracking-wider text-dimmer">{label}</div>
      <div className={`text-sm font-mono font-semibold ${tone != null ? colorPnL(tone) : "text-foreground"}`}>{value}</div>
    </div>
  );
}

export default function LiveTradeStats() {
  const [data, setData] = useState(null);
  const [history, setHistory] = useState(null);
  const [histLimit, setHistLimit] = useState(50);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  const load = useCallback(async () => {
    setBusy(true);
    try {
      const [stats, hist] = await Promise.all([
        api.liveTradeStats(),
        api.liveTradeHistory(histLimit),
      ]);
      setData(stats);
      setHistory(hist);
      setError(null);
    } catch (e) {
      setError(e.response?.data?.detail || e.message);
    } finally {
      setBusy(false);
    }
  }, [histLimit]);

  useEffect(() => { load(); }, [load]);

  const p = data?.period_pnl || {};
  const strategies = data?.per_strategy || [];
  return (
    <div className="rounded-lg border border-line bg-bg-1" data-testid="live-trade-stats">
      <div className="px-3 py-2 border-b border-line flex items-center gap-2">
        <BarChart3 className="w-4 h-4 text-info" />
        <div className="text-[11px] font-semibold uppercase tracking-wider text-dim">Live Trade Statistics</div>
        <span className="text-[10px] text-dimmer">
          journaled close-loop history · {data ? `${data.closed_count} closed of ${data.trade_count}` : "…"}
        </span>
        <button onClick={load} disabled={busy} className="ml-auto text-dim hover:text-foreground"
          title="Refresh statistics" data-testid="live-trade-stats-refresh">
          <RefreshCw className={`w-3.5 h-3.5 ${busy ? "animate-spin" : ""}`} />
        </button>
      </div>
      {error ? (
        <div className="p-3 text-[11px] text-danger">Failed to load: {error}</div>
      ) : !data ? (
        <div className="p-3 text-[11px] text-dimmer">Loading…</div>
      ) : (
        <div className="p-3 space-y-3">
          <div className="grid grid-cols-3 sm:grid-cols-6 gap-x-5 gap-y-2">
            <Stat label="Today" value={fmtINRSigned(p.today)} tone={p.today} />
            <Stat label="Week" value={fmtINRSigned(p.week)} tone={p.week} />
            <Stat label="Month" value={fmtINRSigned(p.month)} tone={p.month} />
            <Stat label="Lifetime" value={fmtINRSigned(p.lifetime)} tone={p.lifetime} />
            <Stat label="Win rate" value={p.win_rate == null ? "—" : fmtPct(p.win_rate, 0)} />
            <Stat label="Profit factor" value={p.profit_factor == null ? "—" : fmtNum(p.profit_factor, 2)} />
          </div>
          {strategies.length > 0 && (
            <div className="overflow-x-auto">
              <table className="w-full text-[11px]">
                <thead className="text-dim">
                  <tr className="border-b border-line">
                    <th className="text-left p-1.5">Strategy</th>
                    <th className="text-right p-1.5">Net P&L</th>
                    <th className="text-right p-1.5">Closed</th>
                    <th className="text-right p-1.5">Win%</th>
                    <th className="text-right p-1.5">Expectancy</th>
                    <th className="text-right p-1.5">Open</th>
                  </tr>
                </thead>
                <tbody>
                  {strategies.map((s) => (
                    <tr key={s.strategy_id + (s.deployment_id || "")} className="border-b border-line/60"
                      data-testid="live-trade-stats-row">
                      <td className="p-1.5">
                        <div className="font-medium truncate max-w-[220px]" title={s.deployment_name || s.strategy_id}>
                          {s.deployment_name || s.strategy_id}
                        </div>
                      </td>
                      <td className={`p-1.5 text-right font-mono ${colorPnL(s.net_pnl)}`}>{fmtINRSigned(s.net_pnl)}</td>
                      <td className="p-1.5 text-right font-mono text-dim">{s.closed_trades}</td>
                      <td className="p-1.5 text-right font-mono">{s.win_rate == null ? "—" : fmtPct(s.win_rate, 0)}</td>
                      <td className={`p-1.5 text-right font-mono ${colorPnL(s.expectancy)}`}>{s.expectancy == null ? "—" : fmtINRSigned(s.expectancy)}</td>
                      <td className="p-1.5 text-right font-mono text-dim">{s.open_count}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          <div className="pt-1">
            <div className="text-[10px] uppercase tracking-wider text-dimmer mb-1">
              Trade history — journaled by the app ({history ? `${history.count} of ${history.total}` : "…"})
            </div>
            {!history || history.items.length === 0 ? (
              <div className="text-[11px] text-dimmer">No journaled live trades yet.</div>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full text-[11px]" data-testid="live-trade-history">
                  <thead className="text-dim">
                    <tr className="border-b border-line">
                      <th className="text-left p-1.5">Entry (IST)</th>
                      <th className="text-left p-1.5">Strategy</th>
                      <th className="text-left p-1.5">Contract</th>
                      <th className="text-right p-1.5">Side</th>
                      <th className="text-right p-1.5">Lots (Qty)</th>
                      <th className="text-right p-1.5">Entry</th>
                      <th className="text-right p-1.5">Exit</th>
                      <th className="text-left p-1.5">Exit (IST)</th>
                      <th className="text-right p-1.5">Realized P&L</th>
                      <th className="text-left p-1.5">Exit reason</th>
                      <th className="text-right p-1.5">Status</th>
                    </tr>
                  </thead>
                  <tbody>
                    {history.items.map((t) => (
                      <tr key={t.id} className="border-b border-line/60" data-testid="live-trade-history-row">
                        <td className="p-1.5 font-mono text-dim">{istStamp(t.created_at) || "—"}</td>
                        <td className="p-1.5"><div className="truncate max-w-[160px]" title={t.deployment_name || t.strategy_id}>{t.deployment_name || t.strategy_id || "—"}</div></td>
                        <td className="p-1.5 font-mono"><div className="truncate max-w-[160px]" title={t.trading_symbol || t.instrument_key}>{t.trading_symbol || t.instrument_key || "—"}</div></td>
                        <td className={`p-1.5 text-right font-mono ${t.direction === "CE" ? "text-emerald-300" : t.direction === "PE" ? "text-rose-300" : "text-dim"}`}>{t.direction || "—"}</td>
                        <td className="p-1.5 text-right font-mono text-dim">{t.lots != null ? `${t.lots} (${t.quantity ?? "—"})` : "—"}</td>
                        <td className="p-1.5 text-right font-mono">{t.entry_price != null ? fmtNum(t.entry_price) : "—"}</td>
                        <td className="p-1.5 text-right font-mono">{t.exit_price != null ? fmtNum(t.exit_price) : "—"}</td>
                        <td className="p-1.5 font-mono text-dim">{istStamp(t.closed_at) || "—"}</td>
                        <td className={`p-1.5 text-right font-mono ${colorPnL(t.realized_pnl)}`}>{t.realized_pnl != null ? fmtINRSigned(t.realized_pnl) : "—"}</td>
                        <td className="p-1.5 text-dim"><div className="truncate max-w-[140px]" title={t.exit_reason || ""}>{t.exit_reason || "—"}</div></td>
                        <td className="p-1.5 text-right"><span className={`text-[10px] px-1.5 py-0.5 rounded border font-mono ${String(t.status).toUpperCase() === "OPEN" ? "border-emerald-500/40 text-emerald-300" : "border-line text-dim"}`}>{t.status}</span></td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                {history.total > history.count && (
                  <button onClick={() => setHistLimit((n) => Math.min(500, n + 100))}
                    className="mt-1 h-6 px-2 rounded border border-line text-[11px] text-dim hover:text-foreground"
                    data-testid="live-trade-history-more">
                    Load more ({history.total - history.count} older)
                  </button>
                )}
              </div>
            )}
            <div className="mt-1 text-[10px] text-dimmer">
              Covers trades placed by this app (the close-loop journal). Manual trades made directly at Flattrade are not journaled here — use the broker tradebook for those.
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
