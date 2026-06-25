import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { toast } from "sonner";
import { fmtInt, fmtNum, fmtPct, fmtPnL, colorPnL, isoToFull } from "@/lib/fmt";
import { SignificanceBadge } from "@/components/SignificanceBadge";
import RunComparison from "@/components/RunComparison";
import { Trash2, RefreshCw, BookOpen, Play, Search, X, ChevronDown, ChevronRight, GitCompare } from "lucide-react";
import { useTableSort, SortHeader } from "@/components/SortHeader";

/**
 * Backtest Run Journal — the saved-run history table.
 *
 * Lives in the Backtest Lab (where runs are produced). Clicking a row loads
 * that run's config + result back into the lab via `onLoadRun`. Collapsible so
 * it does not dominate the page.
 */
export default function BacktestRunJournal({ onLoadRun, refreshKey = 0, defaultOpen = true }) {
  const [runs, setRuns] = useState([]);
  const [loading, setLoading] = useState(true);
  const [filter, setFilter] = useState("");
  const [selected, setSelected] = useState(new Set());
  const [open, setOpen] = useState(defaultOpen);
  const [comparison, setComparison] = useState(null); // { a, b } full run docs
  const [comparing, setComparing] = useState(false);
  const { sort, onSort, sortRows } = useTableSort();

  const refresh = async () => {
    setLoading(true);
    try {
      const d = await api.listBacktestRuns(200);
      setRuns(d.items || []);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { refresh(); }, [refreshKey]);

  const remove = async (id, e) => {
    if (e) e.stopPropagation();
    if (!confirm("Delete this backtest run permanently?")) return;
    try {
      await api.deleteBacktestRun(id);
      toast.success("Run deleted");
      setSelected((s) => { const n = new Set(s); n.delete(id); return n; });
      refresh();
    } catch (e) {
      toast.error("Delete failed");
    }
  };

  const bulkDelete = async () => {
    if (selected.size === 0) return;
    if (!confirm(`Delete ${selected.size} selected backtest run(s)?`)) return;
    try {
      await Promise.all([...selected].map((id) => api.deleteBacktestRun(id)));
      toast.success(`Deleted ${selected.size} runs`);
      setSelected(new Set());
      refresh();
    } catch (e) {
      toast.error("Bulk delete failed");
    }
  };

  const toggleSelected = (id, e) => {
    e.stopPropagation();
    setSelected((s) => {
      const n = new Set(s);
      if (n.has(id)) n.delete(id); else n.add(id);
      return n;
    });
  };

  // Fetch both selected runs in full (the list payload is trimmed; params and
  // equity_curve come from GET /backtest/runs/{id}) and open the comparison.
  const compareSelected = async () => {
    if (selected.size !== 2) return;
    const [idA, idB] = [...selected];
    setComparing(true);
    try {
      const [a, b] = await Promise.all([api.getBacktestRun(idA), api.getBacktestRun(idB)]);
      setComparison({ a, b });
    } catch (e) {
      toast.error("Failed to load runs for comparison");
    } finally {
      setComparing(false);
    }
  };

  const visible = runs.filter((r) => {
    if (!filter) return true;
    const q = filter.toLowerCase();
    return (
      (r.name || "").toLowerCase().includes(q) ||
      (r.strategy_id || "").toLowerCase().includes(q) ||
      (r.instrument || "").toLowerCase().includes(q) ||
      (r.config?.mode || "").toLowerCase().includes(q)
    );
  });

  const sortValue = (r, key) => ({
    created: r.created_at, name: r.name, instrument: r.instrument,
    strategy: r.strategy_id, mode: r.config?.mode,
    trades: r.metrics?.trade_count, winrate: r.metrics?.win_rate,
    pf: r.metrics?.profit_factor, netpts: r.metrics?.total_pnl_pts,
    maxdd: r.metrics?.max_dd_pts,
  }[key]);
  const sortedVisible = sortRows(visible, sortValue);

  return (
    <div className="rounded-lg border border-line bg-bg-1" data-testid="backtest-run-journal">
      <div className="px-3 py-2 border-b border-line flex items-center gap-2 flex-wrap">
        <button onClick={() => setOpen((o) => !o)} className="text-dim hover:text-foreground" data-testid="run-journal-toggle">
          {open ? <ChevronDown className="w-4 h-4" /> : <ChevronRight className="w-4 h-4" />}
        </button>
        <BookOpen className="w-4 h-4 text-info" />
        <div className="text-xs font-semibold uppercase tracking-wider text-dim">Backtest Run Journal</div>
        <div className="text-[11px] text-dimmer ml-1">{visible.length} of {runs.length} runs</div>

        {open && (
          <>
            <div className="relative ml-auto">
              <Search className="absolute left-2 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-dimmer pointer-events-none" />
              <Input
                value={filter}
                onChange={(e) => setFilter(e.target.value)}
                placeholder="Filter by name, strategy, instrument, mode…"
                className="bg-bg-2 border-line h-7 text-xs pl-7 w-72"
                data-testid="journal-filter-input"
              />
              {filter && (
                <button onClick={() => setFilter("")} className="absolute right-1.5 top-1/2 -translate-y-1/2 text-dimmer hover:text-foreground">
                  <X className="w-3 h-3" />
                </button>
              )}
            </div>

            {selected.size === 2 && (
              <Button onClick={compareSelected} size="sm" variant="secondary" className="h-7 text-xs" disabled={comparing} data-testid="journal-compare-button">
                <GitCompare className="w-3 h-3 mr-1" /> {comparing ? "Loading…" : "Compare 2"}
              </Button>
            )}

            {selected.size > 0 && (
              <Button onClick={bulkDelete} size="sm" variant="destructive" className="h-7 text-xs" data-testid="journal-bulk-delete">
                <Trash2 className="w-3 h-3 mr-1" /> Delete {selected.size}
              </Button>
            )}

            <Button variant="ghost" size="sm" onClick={refresh} className="h-7 text-xs" data-testid="journal-refresh-button">
              <RefreshCw className="w-3 h-3 mr-1" /> Refresh
            </Button>
          </>
        )}
      </div>

      {open && (
        <>
          {comparison && (
            <RunComparison a={comparison.a} b={comparison.b} onClose={() => setComparison(null)} />
          )}
          <div className="overflow-x-auto max-h-[420px] overflow-y-auto">
            <table className="w-full text-xs" data-testid="signal-journal-table">
              <thead className="sticky top-0 bg-bg-2 z-10">
                <tr className="text-dim border-b border-line">
                  <th className="p-2 w-8"></th>
                  <th className="text-right p-2 w-10">#</th>
                  <SortHeader col={{ key: "created", label: "Created", align: "left" }} sort={sort} onSort={onSort} testidPrefix="journal-sort" />
                  <SortHeader col={{ key: "name", label: "Name", align: "left" }} sort={sort} onSort={onSort} testidPrefix="journal-sort" />
                  <SortHeader col={{ key: "instrument", label: "Instr.", align: "left" }} sort={sort} onSort={onSort} testidPrefix="journal-sort" />
                  <SortHeader col={{ key: "strategy", label: "Strategy", align: "left" }} sort={sort} onSort={onSort} testidPrefix="journal-sort" />
                  <SortHeader col={{ key: "mode", label: "Mode", align: "left" }} sort={sort} onSort={onSort} testidPrefix="journal-sort" />
                  <SortHeader col={{ key: "trades", label: "Trades", align: "right" }} sort={sort} onSort={onSort} testidPrefix="journal-sort" />
                  <SortHeader col={{ key: "winrate", label: "WinRate", align: "right" }} sort={sort} onSort={onSort} testidPrefix="journal-sort" />
                  <SortHeader col={{ key: "pf", label: "PF", align: "right" }} sort={sort} onSort={onSort} testidPrefix="journal-sort" />
                  <SortHeader col={{ key: "netpts", label: "Net Pts", align: "right" }} sort={sort} onSort={onSort} testidPrefix="journal-sort" />
                  <SortHeader col={{ key: "maxdd", label: "MaxDD", align: "right" }} sort={sort} onSort={onSort} testidPrefix="journal-sort" />
                  <th className="text-left p-2">Significance</th>
                  <th className="p-2 w-24"></th>
                </tr>
              </thead>
              <tbody>
                {loading && (
                  <tr><td colSpan="14" className="p-6 text-center text-dimmer">Loading…</td></tr>
                )}
                {!loading && visible.length === 0 && (
                  <tr><td colSpan="14" className="p-6 text-center text-dimmer">
                    {runs.length === 0 ? "No backtest runs yet. Run one above." : "No runs match filter."}
                  </td></tr>
                )}
                {!loading && sortedVisible.map((r, idx) => (
                  <tr
                    key={r.id}
                    className={`border-b border-line hover:bg-bg-2 cursor-pointer ${selected.has(r.id) ? "bg-bg-2" : ""}`}
                    onClick={() => onLoadRun?.(r.id)}
                    data-testid="signal-journal-row"
                  >
                    <td className="p-2" onClick={(e) => e.stopPropagation()}>
                      <input
                        type="checkbox"
                        checked={selected.has(r.id)}
                        onChange={(e) => toggleSelected(r.id, e)}
                        className="w-3.5 h-3.5 accent-info cursor-pointer"
                        data-testid={`journal-select-${r.id.slice(0, 8)}`}
                      />
                    </td>
                    <td className="p-2 font-mono text-dimmer text-right">{idx + 1}</td>
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
                    <td className="p-2" onClick={(e) => e.stopPropagation()}>
                      <div className="flex gap-1 justify-end">
                        <Button
                          size="sm" variant="ghost"
                          onClick={() => onLoadRun?.(r.id)}
                          className="h-6 w-6 p-0 hover:bg-bg-3"
                          title="Load in Backtest Lab"
                          data-testid={`journal-load-${r.id.slice(0, 8)}`}
                        >
                          <Play className="w-3 h-3 text-info" />
                        </Button>
                        <Button
                          size="sm" variant="ghost"
                          onClick={(e) => remove(r.id, e)}
                          className="h-6 w-6 p-0 hover:bg-bg-3"
                          title="Delete run"
                          data-testid={`journal-delete-${r.id.slice(0, 8)}`}
                        >
                          <Trash2 className="w-3 h-3 text-rose-400" />
                        </Button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="px-3 py-1.5 border-t border-line text-[10px] text-dimmer">
            Tip: Click any row to load that run's config + result into the lab above. Select exactly two rows to compare them.
          </div>
        </>
      )}
    </div>
  );
}
