import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { Skeleton } from "@/components/ui/skeleton";
import { Button } from "@/components/ui/button";
import { toast } from "sonner";
import { fmtInt, fmtNum, fmtPct, fmtPnL, colorPnL, isoToFull } from "@/lib/fmt";
import { SignificanceBadge } from "@/components/SignificanceBadge";
import { Trash2, RefreshCw, BookOpen } from "lucide-react";

export default function SignalJournal() {
  const [runs, setRuns] = useState([]);
  const [loading, setLoading] = useState(true);

  const refresh = async () => {
    try {
      const d = await api.listBacktestRuns(100);
      setRuns(d.items || []);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { refresh(); }, []);

  const remove = async (id) => {
    try {
      await api.deleteBacktestRun(id);
      toast.success("Run deleted");
      refresh();
    } catch (e) {
      toast.error("Delete failed");
    }
  };

  if (loading) return <Skeleton className="h-96 bg-bg-1" />;

  return (
    <div className="space-y-3" data-testid="signal-journal-page">
      <div className="rounded-lg border border-line bg-bg-1">
        <div className="px-3 py-2 border-b border-line flex items-center">
          <BookOpen className="w-4 h-4 mr-2 text-info" />
          <div className="text-xs font-semibold uppercase tracking-wider text-dim">Backtest Run Journal</div>
          <div className="ml-auto text-[11px] text-dimmer mr-2">{runs.length} runs</div>
          <Button variant="ghost" size="sm" onClick={refresh} className="h-7 text-xs" data-testid="journal-refresh-button">
            <RefreshCw className="w-3 h-3 mr-1" /> Refresh
          </Button>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-xs" data-testid="signal-journal-table">
            <thead className="sticky top-0 bg-bg-2">
              <tr className="text-dim border-b border-line">
                <th className="text-left p-2">Created</th>
                <th className="text-left p-2">Name</th>
                <th className="text-left p-2">Instr.</th>
                <th className="text-left p-2">Strategy</th>
                <th className="text-left p-2">Mode</th>
                <th className="text-right p-2">Trades</th>
                <th className="text-right p-2">WinRate</th>
                <th className="text-right p-2">PF</th>
                <th className="text-right p-2">Net Pts</th>
                <th className="text-right p-2">MaxDD</th>
                <th className="text-left p-2">Significance</th>
                <th className="p-2"></th>
              </tr>
            </thead>
            <tbody>
              {runs.length === 0 && (
                <tr><td colSpan="12" className="p-6 text-center text-dimmer">No backtest runs yet. Run one from the Backtest Lab.</td></tr>
              )}
              {runs.map((r) => (
                <tr key={r.id} className="border-b border-line hover:bg-bg-2" data-testid="signal-journal-row">
                  <td className="p-2 font-mono text-dim">{isoToFull(r.created_at)}</td>
                  <td className="p-2 font-medium">{r.name}</td>
                  <td className="p-2 font-mono">{r.instrument}</td>
                  <td className="p-2 font-mono text-dim">{r.strategy_id}</td>
                  <td className="p-2 font-mono">{r.config?.mode}</td>
                  <td className="p-2 font-mono text-right">{fmtInt(r.metrics?.trade_count)}</td>
                  <td className="p-2 font-mono text-right">{fmtPct(r.metrics?.win_rate)}</td>
                  <td className="p-2 font-mono text-right">{fmtNum(r.metrics?.profit_factor, 2)}</td>
                  <td className={`p-2 font-mono text-right ${colorPnL(r.metrics?.total_pnl_pts)}`}>{fmtPnL(r.metrics?.total_pnl_pts)}</td>
                  <td className="p-2 font-mono text-right text-danger">{fmtPnL(r.metrics?.max_dd_pts)}</td>
                  <td className="p-2"><SignificanceBadge significance={r.significance} /></td>
                  <td className="p-2">
                    <Button size="sm" variant="ghost" className="h-6 w-6 p-0" onClick={() => remove(r.id)} data-testid="journal-delete-button">
                      <Trash2 className="w-3 h-3 text-rose-400" />
                    </Button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
