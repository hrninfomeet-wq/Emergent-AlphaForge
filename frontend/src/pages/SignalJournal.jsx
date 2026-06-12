import { Fragment, useCallback, useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { api, API } from "@/lib/api";
import { Skeleton } from "@/components/ui/skeleton";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { toast } from "sonner";
import { fmtNum, tsToFull, tsToTime, colorPnL } from "@/lib/fmt";
import {
  RefreshCw, Download, Trash2, ChevronDown, ChevronRight,
  ChevronLeft, BookOpen,
} from "lucide-react";

/**
 * Signals ledger (route /journal, rebuilt 2026-06-12, forward-surfaces R3).
 *
 * The trade-recommendation record: one row per deployment signal joined with
 * its paper trade — IST time, deployment, strategy, instrument, CE/PE, the
 * option contract + expiry, spot at entry, entry premium, the entry-trigger
 * reasons (expandable), exit time/premium/reason, P&L in ₹ and premium points,
 * score, state, blockers and paper_trade_error. All filter / sort / paginate /
 * CSV is server-side via GET /api/signals/enriched. Deletion is via
 * POST /api/signals/purge (row-select, older-than-N-days, per-deployment).
 *
 * Manual research-signal creation / approval flow was retired 2026-06-12;
 * deployments journal and auto-trade their own signals.
 */

const INSTRUMENTS = ["NIFTY", "BANKNIFTY", "SENSEX"];
// States a deployment signal can be in (post-overhaul lifecycle).
const STATES = ["CONFIRMED", "TRIGGERED", "ACTIVE", "EXITED", "AUDITED"];
const PAGE_SIZE = 100;

const STATE_STYLE = {
  CONFIRMED: "border-amber-500/40 text-amber-300",
  TRIGGERED: "border-info/40 text-info",
  ACTIVE: "border-emerald-500/40 text-emerald-300",
  EXITED: "border-emerald-500/40 text-emerald-300",
  AUDITED: "border-line text-dim",
};

// Server-sortable columns (must match _ENRICHED_SORT_FIELDS in server.py).
const SORTABLE = {
  bar_ts: "bar_ts",
  instrument: "instrument",
  state: "state",
  score: "confidence",
  updated: "updated_at",
};

const inr = (v) =>
  v == null ? "—" : `₹${Number(v).toLocaleString("en-IN", { maximumFractionDigits: 0 })}`;

export default function SignalJournal() {
  const [searchParams, setSearchParams] = useSearchParams();

  const [data, setData] = useState({ items: [], total: 0 });
  const [deployments, setDeployments] = useState([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);

  const [filters, setFilters] = useState({
    deployment_id: searchParams.get("deployment") || "",
    instrument: "",
    state: "",
    clean: "", // "" | "true" | "false"
    date_from: "",
    date_to: "",
  });
  const [sort, setSort] = useState("-bar_ts");
  const [skip, setSkip] = useState(0);
  const [selected, setSelected] = useState(() => new Set());
  const [expanded, setExpanded] = useState(null);
  const [olderDays, setOlderDays] = useState("30");

  // Build the server param object from the current filters (empty values dropped).
  const params = useMemo(() => {
    const p = { sort, skip, limit: PAGE_SIZE };
    if (filters.deployment_id) p.deployment_id = filters.deployment_id;
    if (filters.instrument) p.instrument = filters.instrument;
    if (filters.state) p.state = filters.state;
    if (filters.clean === "true") p.clean = true;
    else if (filters.clean === "false") p.clean = false;
    if (filters.date_from) p.date_from = filters.date_from;
    if (filters.date_to) p.date_to = filters.date_to;
    return p;
  }, [filters, sort, skip]);

  const fetchRows = useCallback(async () => {
    try {
      const res = await api.listSignalsEnriched(params);
      setData({ items: res.items || [], total: res.total || 0 });
    } catch (e) {
      toast.error(`Load failed: ${e.response?.data?.detail || e.message}`);
    } finally {
      setLoading(false);
    }
  }, [params]);

  useEffect(() => {
    api.listDeployments({ limit: 200 }).then((d) => setDeployments(d.items || [])).catch(() => {});
  }, []);

  useEffect(() => { fetchRows(); }, [fetchRows]);

  // Auto-refresh ≤60s (the evaluator fires each market minute).
  useEffect(() => {
    const id = window.setInterval(fetchRows, 45000);
    return () => window.clearInterval(id);
  }, [fetchRows]);

  const setFilter = (k, v) => { setSkip(0); setSelected(new Set()); setFilters((f) => ({ ...f, [k]: v })); };

  // Keep the ?deployment= URL param in sync so links + reloads preselect.
  useEffect(() => {
    const cur = searchParams.get("deployment") || "";
    if (filters.deployment_id !== cur) {
      const next = new URLSearchParams(searchParams);
      if (filters.deployment_id) next.set("deployment", filters.deployment_id);
      else next.delete("deployment");
      setSearchParams(next, { replace: true });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filters.deployment_id]);

  const toggleSort = (col) => {
    const field = SORTABLE[col];
    if (!field) return;
    setSkip(0);
    setSort((cur) => (cur === field ? `-${field}` : cur === `-${field}` ? field : `-${field}`));
  };
  const sortMark = (col) => {
    const field = SORTABLE[col];
    if (!field) return null;
    if (sort === field) return " ▲";
    if (sort === `-${field}`) return " ▼";
    return null;
  };

  const exportCsv = () => {
    const qs = new URLSearchParams();
    Object.entries({ ...params, limit: 500, skip: 0, format: "csv" }).forEach(([k, v]) => qs.set(k, String(v)));
    window.open(`${API}/signals/enriched?${qs.toString()}`, "_blank");
  };

  const toggleSelect = (id) => setSelected((s) => {
    const n = new Set(s);
    if (n.has(id)) n.delete(id); else n.add(id);
    return n;
  });
  const allVisibleSelected = data.items.length > 0 && data.items.every((r) => selected.has(r.id));
  const toggleSelectAll = () => setSelected((s) => {
    if (allVisibleSelected) return new Set();
    return new Set(data.items.map((r) => r.id));
  });

  const purge = async (payload, confirmMsg) => {
    if (!window.confirm(confirmMsg)) return;
    setBusy(true);
    try {
      const res = await api.purgeSignals(payload);
      toast.success(`Deleted ${res.deleted} signal${res.deleted === 1 ? "" : "s"}.`);
      setSelected(new Set());
      setSkip(0);
      await fetchRows();
    } catch (e) {
      toast.error(`Delete failed: ${e.response?.data?.detail || e.message}`);
    } finally {
      setBusy(false);
    }
  };

  const deleteSelected = () => {
    if (selected.size === 0) return;
    purge({ ids: [...selected] }, `Delete ${selected.size} selected signal${selected.size === 1 ? "" : "s"}? This cannot be undone.`);
  };
  const deleteOlder = () => {
    const n = parseInt(olderDays, 10);
    if (!n || n < 1) { toast.error("Enter a valid number of days."); return; }
    purge({ older_than_days: n }, `Delete all journaled signals older than ${n} days? This cannot be undone.`);
  };
  const purgeDeployment = () => {
    if (!filters.deployment_id) { toast.error("Select a deployment filter first."); return; }
    const name = deployments.find((d) => d.id === filters.deployment_id)?.name || filters.deployment_id;
    purge({ deployment_id: filters.deployment_id }, `Delete ALL journaled signals for deployment "${name}"? This cannot be undone.`);
  };

  const total = data.total;
  const pageEnd = Math.min(skip + data.items.length, total);

  if (loading) return <Skeleton className="h-96 bg-bg-1" data-testid="signals-ledger-page" />;

  return (
    <div className="space-y-3" data-testid="signals-ledger-page">
      {/* Filters + actions */}
      <div className="rounded-lg border border-line bg-bg-1">
        <div className="px-3 py-2 border-b border-line flex items-center gap-2 flex-wrap">
          <BookOpen className="w-4 h-4 text-info" />
          <div className="text-xs font-semibold uppercase tracking-wider text-dim">Signals Ledger</div>
          <div className="text-[11px] text-dimmer ml-1">
            {total === 0 ? "no signals" : `${skip + 1}–${pageEnd} of ${total}`}
          </div>

          <select
            value={filters.deployment_id}
            onChange={(e) => setFilter("deployment_id", e.target.value)}
            className="ml-auto h-7 rounded-md border border-input bg-bg-2 px-2 text-xs text-foreground max-w-[180px]"
            data-testid="ledger-deployment-filter"
          >
            <option value="">All deployments</option>
            {deployments.map((d) => <option key={d.id} value={d.id}>{d.name || d.id?.slice(0, 8)}</option>)}
          </select>

          <select value={filters.instrument} onChange={(e) => setFilter("instrument", e.target.value)}
            className="h-7 rounded-md border border-input bg-bg-2 px-2 text-xs text-foreground" data-testid="ledger-instrument-filter">
            <option value="">All instruments</option>
            {INSTRUMENTS.map((i) => <option key={i} value={i}>{i}</option>)}
          </select>

          <select value={filters.state} onChange={(e) => setFilter("state", e.target.value)}
            className="h-7 rounded-md border border-input bg-bg-2 px-2 text-xs text-foreground" data-testid="ledger-state-filter">
            <option value="">All states</option>
            {STATES.map((s) => <option key={s} value={s}>{s}</option>)}
          </select>

          <select value={filters.clean} onChange={(e) => setFilter("clean", e.target.value)}
            className="h-7 rounded-md border border-input bg-bg-2 px-2 text-xs text-foreground" data-testid="ledger-clean-filter">
            <option value="">Clean + blocked</option>
            <option value="true">Clean only</option>
            <option value="false">Blocked only</option>
          </select>

          <Input type="date" value={filters.date_from} onChange={(e) => setFilter("date_from", e.target.value)}
            className="bg-bg-2 border-line h-7 text-xs w-[150px] pr-1" data-testid="ledger-date-from" title="From (IST)" />
          <Input type="date" value={filters.date_to} onChange={(e) => setFilter("date_to", e.target.value)}
            className="bg-bg-2 border-line h-7 text-xs w-[150px] pr-1" data-testid="ledger-date-to" title="To (IST)" />

          <Button variant="ghost" size="sm" onClick={exportCsv} className="h-7 text-xs" data-testid="ledger-export-csv">
            <Download className="w-3 h-3 mr-1" /> CSV
          </Button>
          <Button variant="ghost" size="sm" onClick={fetchRows} className="h-7 text-xs" data-testid="ledger-refresh-button">
            <RefreshCw className="w-3 h-3 mr-1" /> Refresh
          </Button>
        </div>

        {/* Deletion toolkit */}
        <div className="px-3 py-1.5 flex items-center gap-2 flex-wrap text-[11px] text-dimmer">
          <Trash2 className="w-3.5 h-3.5" />
          <span>Cleanup:</span>
          <Button variant="outline" size="sm" disabled={busy || selected.size === 0} onClick={deleteSelected}
            className="h-6 text-[11px] border-rose-500/40 text-rose-300 hover:text-rose-200" data-testid="ledger-delete-selected">
            Delete selected ({selected.size})
          </Button>
          <span className="ml-2">Older than</span>
          <Input value={olderDays} onChange={(e) => setOlderDays(e.target.value)} type="number" min={1}
            className="bg-bg-2 border-line h-6 text-[11px] w-16" data-testid="ledger-delete-older-days" />
          <span>days</span>
          <Button variant="outline" size="sm" disabled={busy} onClick={deleteOlder}
            className="h-6 text-[11px] border-rose-500/40 text-rose-300 hover:text-rose-200" data-testid="ledger-delete-older">
            Purge old
          </Button>
          <Button variant="outline" size="sm" disabled={busy || !filters.deployment_id} onClick={purgeDeployment}
            className="h-6 text-[11px] border-rose-500/40 text-rose-300 hover:text-rose-200 ml-2" data-testid="ledger-purge-deployment"
            title={filters.deployment_id ? "" : "Select a deployment filter first"}>
            Purge this deployment
          </Button>
        </div>
      </div>

      {/* Table */}
      <div className="rounded-lg border border-line bg-bg-1 overflow-x-auto">
        <table className="w-full text-xs" data-testid="signals-ledger-table">
          <thead className="sticky top-0 bg-bg-2 z-10">
            <tr className="text-dim border-b border-line">
              <th className="p-2 w-8">
                <input type="checkbox" checked={allVisibleSelected} onChange={toggleSelectAll} data-testid="ledger-select-all" />
              </th>
              <th className="p-2 w-6"></th>
              <th className="text-left p-2 cursor-pointer hover:text-foreground" onClick={() => toggleSort("bar_ts")}>Time (IST){sortMark("bar_ts")}</th>
              <th className="text-left p-2">Deployment</th>
              <th className="text-left p-2">Strategy</th>
              <th className="text-left p-2 cursor-pointer hover:text-foreground" onClick={() => toggleSort("instrument")}>Instr.{sortMark("instrument")}</th>
              <th className="text-left p-2">Side</th>
              <th className="text-left p-2">Contract</th>
              <th className="text-right p-2">Spot</th>
              <th className="text-right p-2">Entry ₹</th>
              <th className="text-left p-2">Exit</th>
              <th className="text-right p-2">P&L ₹</th>
              <th className="text-right p-2">P&L pts</th>
              <th className="text-right p-2 cursor-pointer hover:text-foreground" onClick={() => toggleSort("score")}>Score{sortMark("score")}</th>
              <th className="text-left p-2 cursor-pointer hover:text-foreground" onClick={() => toggleSort("state")}>State{sortMark("state")}</th>
              <th className="text-left p-2">Notes</th>
            </tr>
          </thead>
          <tbody>
            {data.items.length === 0 && (
              <tr><td colSpan="16" className="p-6 text-center text-dimmer">
                No signals match these filters. Deploy a preset in the Deployments command center to start.
              </td></tr>
            )}
            {data.items.map((s) => {
              const isOpen = expanded === s.id;
              const reasons = s.reasons || [];
              const blockers = s.blockers || [];
              const exitBits = [];
              if (s.exit_premium != null) exitBits.push(`₹${fmtNum(s.exit_premium)}`);
              if (s.exit_reason) exitBits.push(s.exit_reason);
              return (
                <Fragment key={s.id}>
                  <tr className="border-b border-line hover:bg-bg-2" data-testid="signals-ledger-row">
                    <td className="p-2">
                      <input type="checkbox" checked={selected.has(s.id)} onChange={() => toggleSelect(s.id)} data-testid="ledger-row-select" />
                    </td>
                    <td className="p-2">
                      <button onClick={() => setExpanded(isOpen ? null : s.id)} className="text-dimmer hover:text-foreground" data-testid="ledger-expand-row" title="Entry triggers / details">
                        {isOpen ? <ChevronDown className="w-3.5 h-3.5" /> : <ChevronRight className="w-3.5 h-3.5" />}
                      </button>
                    </td>
                    <td className="p-2 font-mono text-dim whitespace-nowrap">{tsToFull(s.bar_ts) || s.bar_ist || "—"}</td>
                    <td className="p-2 font-medium truncate max-w-[130px]" title={s.deployment_name}>{s.deployment_name || s.deployment_id?.slice(0, 8)}</td>
                    <td className="p-2 text-dim truncate max-w-[120px]" title={s.strategy_id}>{s.strategy_id}</td>
                    <td className="p-2 font-mono">{s.instrument}</td>
                    <td className="p-2"><span className={`font-mono ${s.direction === "CE" ? "text-emerald-400" : "text-red-400"}`}>{s.direction}</span></td>
                    <td className="p-2 font-mono text-dim whitespace-nowrap" title={s.contract_expiry ? `expiry ${s.contract_expiry}` : ""}>
                      {s.contract || "—"}{s.contract_expiry ? <span className="text-dimmer"> · {s.contract_expiry}</span> : null}
                    </td>
                    <td className="p-2 font-mono text-right text-dim">{s.spot_entry != null ? fmtNum(s.spot_entry) : "—"}</td>
                    <td className="p-2 font-mono text-right">{s.entry_premium != null ? fmtNum(s.entry_premium) : "—"}</td>
                    <td className="p-2 font-mono text-dim whitespace-nowrap" title={s.closed_at ? `closed ${s.closed_at}` : ""}>
                      {exitBits.length ? exitBits.join(" · ") : (s.trade_status === "OPEN" ? "open" : "—")}
                    </td>
                    <td className={`p-2 font-mono text-right ${colorPnL(s.pnl_value)}`}>{s.pnl_value != null ? inr(s.pnl_value) : "—"}</td>
                    <td className={`p-2 font-mono text-right ${colorPnL(s.pnl_premium_pts)}`}>{s.pnl_premium_pts != null ? fmtNum(s.pnl_premium_pts) : "—"}</td>
                    <td className="p-2 font-mono text-right">{fmtNum(s.score, 0)}</td>
                    <td className="p-2"><span className={`text-[10px] px-1.5 py-0.5 rounded border font-mono ${STATE_STYLE[s.state] || "border-line text-dim"}`}>{s.state}</span></td>
                    <td className="p-2 text-dimmer truncate max-w-[180px]" title={[...(s.paper_trade_error ? [s.paper_trade_error] : []), ...blockers].join("; ")}>
                      {s.paper_trade_error ? <span className="text-rose-300">{s.paper_trade_error}</span> : (blockers.length ? blockers.join("; ") : "—")}
                    </td>
                  </tr>
                  {isOpen && (
                    <tr className="bg-bg-2/50 border-b border-line" data-testid="ledger-detail-row">
                      <td></td><td></td>
                      <td colSpan="14" className="p-3 text-[11px] space-y-1.5">
                        <div>
                          <span className="text-dimmer uppercase tracking-wider mr-2">Entry triggers</span>
                          {reasons.length ? reasons.map((r, i) => (
                            <span key={i} className="inline-block mr-1.5 mb-1 px-1.5 py-0.5 rounded bg-bg-3 border border-line text-dim">{r}</span>
                          )) : <span className="text-dimmer">none recorded</span>}
                        </div>
                        {blockers.length > 0 && (
                          <div><span className="text-dimmer uppercase tracking-wider mr-2">Blockers</span>
                            <span className="text-amber-300">{blockers.join("; ")}</span></div>
                        )}
                        {s.paper_trade_error && (
                          <div><span className="text-dimmer uppercase tracking-wider mr-2">Paper trade error</span>
                            <span className="text-rose-300">{s.paper_trade_error}</span></div>
                        )}
                        <div className="text-dimmer">
                          tracked_for_pnl: {s.tracked_for_pnl ? "yes" : "no"} · lots: {s.lots ?? "—"} · qty: {s.quantity ?? "—"}
                          {s.closed_at ? ` · closed ${tsToTime(new Date(s.closed_at).getTime())}` : ""}
                        </div>
                      </td>
                    </tr>
                  )}
                </Fragment>
              );
            })}
          </tbody>
        </table>

        {/* Pagination */}
        <div className="px-3 py-2 border-t border-line flex items-center gap-2 text-[11px] text-dimmer">
          <span>Showing {total === 0 ? 0 : skip + 1}–{pageEnd} of {total}</span>
          <div className="ml-auto flex items-center gap-1">
            <Button variant="ghost" size="sm" disabled={skip === 0} onClick={() => setSkip(Math.max(0, skip - PAGE_SIZE))}
              className="h-6 text-[11px]" data-testid="ledger-prev-page">
              <ChevronLeft className="w-3 h-3 mr-0.5" /> Prev
            </Button>
            <Button variant="ghost" size="sm" disabled={pageEnd >= total} onClick={() => setSkip(skip + PAGE_SIZE)}
              className="h-6 text-[11px]" data-testid="ledger-next-page">
              Next <ChevronRight className="w-3 h-3 ml-0.5" />
            </Button>
          </div>
        </div>
      </div>

      <div className="text-[10px] text-dimmer px-1">
        Each row is a deployment's signal joined with its paper trade — the trade recommendation of record. Entries/exits are option premium (₹), never the spot index. Times are IST.
      </div>
    </div>
  );
}
