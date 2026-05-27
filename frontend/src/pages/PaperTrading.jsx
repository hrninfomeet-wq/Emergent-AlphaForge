import { useCallback, useEffect, useState } from "react";
import { Briefcase, RefreshCw } from "lucide-react";
import { toast } from "sonner";
import { api } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { fmtNum, isoToFull } from "@/lib/fmt";

export default function PaperTrading() {
  const [trades, setTrades] = useState([]);
  const [status, setStatus] = useState("");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [prices, setPrices] = useState({});

  const refresh = useCallback(async () => {
    try {
      const res = await api.listPaperTrades({ ...(status ? { status } : {}), limit: 100 });
      setTrades(res.items || []);
    } catch (e) {
      toast.error(`Paper trades load failed: ${e.response?.data?.detail || e.message}`);
    } finally {
      setLoading(false);
    }
  }, [status]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const setPrice = (id, value) => setPrices((prev) => ({ ...prev, [id]: value }));

  const mark = async (trade) => {
    const price = Number(prices[trade.id] || trade.last_price || trade.entry_price || 0);
    setBusy(true);
    try {
      await api.markPaperTrade(trade.id, { last_price: price });
      toast.success("Paper trade marked");
      await refresh();
    } catch (e) {
      toast.error(`Mark failed: ${e.response?.data?.detail || e.message}`);
    } finally {
      setBusy(false);
    }
  };

  const close = async (trade) => {
    const price = Number(prices[trade.id] || trade.last_price || trade.entry_price || 0);
    setBusy(true);
    try {
      await api.closePaperTrade(trade.id, { exit_price: price, reason: "manual close" });
      toast.success("Paper trade closed");
      await refresh();
    } catch (e) {
      toast.error(`Close failed: ${e.response?.data?.detail || e.message}`);
    } finally {
      setBusy(false);
    }
  };

  const openTrades = trades.filter((trade) => trade.status === "OPEN");
  const realized = trades.reduce((sum, trade) => sum + Number(trade.realized_pnl || 0), 0);
  const unrealized = openTrades.reduce((sum, trade) => sum + Number(trade.unrealized_pnl || 0), 0);

  return (
    <div className="space-y-3" data-testid="paper-trading-page">
      <section className="rounded-lg border border-line bg-bg-1" data-testid="paper-trading-journal">
        <div className="px-3 py-2 border-b border-line flex items-center gap-2">
          <Briefcase className="w-4 h-4 text-info" />
          <div className="text-xs font-semibold uppercase tracking-wider text-dim">Paper Trading Journal</div>
          <Button size="sm" variant="ghost" onClick={refresh} className="ml-auto h-7 text-xs">
            <RefreshCw className="w-3 h-3 mr-1" />
            Refresh
          </Button>
        </div>

        <div className="p-3 space-y-3">
          <div className="grid grid-cols-2 lg:grid-cols-5 gap-2">
            <PaperStat label="Open" value={openTrades.length} />
            <PaperStat label="Trades" value={trades.length} />
            <PaperStat label="Unrealized" value={fmtNum(unrealized)} tone={unrealized} />
            <PaperStat label="Realized" value={fmtNum(realized)} tone={realized} />
            <div className="rounded-md border border-line bg-bg-2 p-2">
              <div className="text-[10px] uppercase tracking-wider text-dimmer">Filter</div>
              <select value={status} onChange={(e) => setStatus(e.target.value)} className="mt-1 h-8 w-full rounded-md border border-input bg-bg-1 px-2 text-xs">
                <option value="">All</option>
                <option value="OPEN">OPEN</option>
                <option value="CLOSED">CLOSED</option>
              </select>
            </div>
          </div>

          <div className="overflow-x-auto rounded-md border border-line" data-testid="paper-trade-table">
            <table className="w-full text-xs">
              <thead className="bg-bg-2 text-dim">
                <tr>
                  <th className="p-2 text-left">Symbol</th>
                  <th className="p-2 text-left">Status</th>
                  <th className="p-2 text-right">Qty</th>
                  <th className="p-2 text-right">Entry</th>
                  <th className="p-2 text-right">Last</th>
                  <th className="p-2 text-left">Risk</th>
                  <th className="p-2 text-right">P&L</th>
                  <th className="p-2 text-left">Updated</th>
                  <th className="p-2 text-right">Actions</th>
                </tr>
              </thead>
              <tbody>
                {loading ? (
                  <tr><td colSpan="9" className="p-4 text-center text-dim">Loading trades...</td></tr>
                ) : trades.length === 0 ? (
                  <tr><td colSpan="9" className="p-4 text-center text-dim">No paper trades yet. Deploy a signal from Live Signals.</td></tr>
                ) : trades.map((trade) => {
                  const pnl = trade.status === "OPEN" ? trade.unrealized_pnl : trade.realized_pnl;
                  const risk = trade.risk || {};
                  return (
                    <tr key={trade.id} className="border-b border-line">
                      <td className="p-2 font-mono">{trade.trading_symbol || trade.instrument || trade.id}</td>
                      <td className="p-2 font-mono">{trade.status}</td>
                      <td className="p-2 text-right font-mono">{trade.quantity}</td>
                      <td className="p-2 text-right font-mono">{fmtNum(trade.entry_price)}</td>
                      <td className="p-2 text-right font-mono">{fmtNum(trade.last_price)}</td>
                      <td className="p-2">
                        <span className="text-[10px] px-1.5 py-0.5 rounded border border-line bg-bg-3 font-mono" data-testid="risk-badge">
                          S {risk.stop_price ?? "--"} / T {risk.target_price ?? "--"}
                        </span>
                      </td>
                      <td className={`p-2 text-right font-mono ${Number(pnl || 0) >= 0 ? "text-emerald-400" : "text-red-400"}`}>{fmtNum(pnl || 0)}</td>
                      <td className="p-2 text-dim">{isoToFull(trade.updated_at)}</td>
                      <td className="p-2">
                        {trade.status === "OPEN" && (
                          <div className="flex justify-end gap-1.5">
                            <Input
                              type="number"
                              value={prices[trade.id] ?? trade.last_price ?? trade.entry_price}
                              onChange={(e) => setPrice(trade.id, e.target.value)}
                              className="h-7 w-24 bg-bg-1 border-line text-right"
                            />
                            <Button size="sm" variant="secondary" disabled={busy} onClick={() => mark(trade)} className="h-7 text-xs border border-line" data-testid="mark-paper-trade">
                              Mark
                            </Button>
                            <Button size="sm" disabled={busy} onClick={() => close(trade)} className="h-7 text-xs bg-bg-3 border border-line hover:bg-bg-2" data-testid="close-paper-trade">
                              Close
                            </Button>
                          </div>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      </section>
    </div>
  );
}

function PaperStat({ label, value, tone = 0 }) {
  const toneClass = Number(tone || 0) > 0 ? "text-emerald-400" : Number(tone || 0) < 0 ? "text-red-400" : "";
  return (
    <div className="rounded-md border border-line bg-bg-2 p-2">
      <div className="text-[10px] uppercase tracking-wider text-dimmer">{label}</div>
      <div className={`text-sm font-mono mt-0.5 ${toneClass}`}>{value}</div>
    </div>
  );
}
