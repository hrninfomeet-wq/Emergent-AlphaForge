import { Fragment, useCallback, useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { api, API } from "@/lib/api";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { fmtNum, fmtPct, colorPnL, isoToFull } from "@/lib/fmt";
import {
  Briefcase, RefreshCw, Download, Trash2, Zap, XCircle,
  ChevronLeft, ChevronRight, CalendarDays,
  Pause, Play, Square, OctagonX, Activity,
} from "lucide-react";

/**
 * Paper Trading journal (route /paper, rebuilt 2026-06-12, forward-surfaces R4).
 *
 * A strategy-named trading journal over the upgraded GET /api/paper/trades:
 * one row per paper trade (deployment/strategy, option contract, CE/PE,
 * lots × lot size, entry/exit time+price, exit reason, holding time, P&L in ₹
 * and % of entry premium, status). Rows are grouped day-wise with per-day
 * subtotals. A summary strip (today realized, open MTM, open count, win rate,
 * profit factor) sits above a small cumulative-realized equity sparkline.
 * Filter / sort / paginate / CSV is server-side; the table page auto-refreshes
 * ≤30s. The manual type-a-price flow is replaced with one-click "Close @
 * market" (uses the trade's last_price; prompt fallback only when null) plus a
 * confirmed "Close all open"; a small manual-price field remains for off-hours.
 * Purge (POST /api/paper/trades/purge) deletes CLOSED trades only — OPEN trades
 * are never deletable (trading-domain rule). Entries/exits are option PREMIUM
 * (₹), never the spot index. Times are IST.
 */

const INSTRUMENTS = ["NIFTY", "BANKNIFTY", "SENSEX"];
const PAGE_SIZE = 100;
const STATS_CAP = 500; // summary/sparkline computed over up to this many filtered trades

// Server-sortable columns (must match _TRADES_SORT_FIELDS in server.py).
const SORTABLE = {
  created: "created_at",
  pnl: "realized_pnl",
  entry: "entry_price",
  updated: "updated_at",
  closed: "closed_at",
};

const inr = (v) =>
  v == null ? "—" : `₹${fmtNum(v, 0)}`;

// IST date (YYYY-MM-DD) and HH:MM from an ISO timestamp, offset-arithmetic
// (matches lib/fmt's IST handling — no locale dependency).
const IST_OFFSET_MS = 330 * 60 * 1000;
const pad = (n) => String(n).padStart(2, "0");
const istParts = (iso) => {
  if (!iso) return null;
  const d = new Date(new Date(iso).getTime() + IST_OFFSET_MS);
  return {
    day: `${d.getUTCFullYear()}-${pad(d.getUTCMonth() + 1)}-${pad(d.getUTCDate())}`,
    time: `${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}`,
  };
};
const istToday = () => istParts(new Date().toISOString()).day;

// Compact holding time between two ISO timestamps (or now when still open).
const holdingTime = (fromIso, toIso) => {
  if (!fromIso) return "—";
  const a = new Date(fromIso).getTime();
  const b = toIso ? new Date(toIso).getTime() : Date.now();
  let mins = Math.max(0, Math.round((b - a) / 60000));
  if (mins < 60) return `${mins}m`;
  const h = Math.floor(mins / 60);
  mins = mins % 60;
  return `${h}h ${pad(mins)}m`;
};

// P&L as a % of the trade's entry premium notional (entry_price × quantity).
const pnlPct = (trade, pnl) => {
  const notional = Number(trade.entry_price || 0) * Number(trade.quantity || 0);
  if (!notional) return null;
  return (Number(pnl || 0) / notional) * 100;
};

const tradePnl = (trade) =>
  String(trade.status || "").toUpperCase() === "OPEN"
    ? Number(trade.unrealized_pnl || 0)
    : Number(trade.realized_pnl || 0);

export default function PaperTrading() {
  const [searchParams, setSearchParams] = useSearchParams();

  const [data, setData] = useState({ items: [], total: 0 });
  const [statsRows, setStatsRows] = useState([]);
  const [deployments, setDeployments] = useState([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);

  const [filters, setFilters] = useState({
    deployment_id: searchParams.get("deployment") || "",
    instrument: "",
    status: "",
    date_from: "",
    date_to: "",
  });
  const [sort, setSort] = useState("-created_at");
  const [skip, setSkip] = useState(0);
  const [selected, setSelected] = useState(() => new Set());
  const [manualPrice, setManualPrice] = useState({});
  const [olderDays, setOlderDays] = useState("30");

  // Server params for the paginated table.
  const params = useMemo(() => {
    const p = { sort, skip, limit: PAGE_SIZE };
    if (filters.deployment_id) p.deployment_id = filters.deployment_id;
    if (filters.instrument) p.instrument = filters.instrument;
    if (filters.status) p.status = filters.status;
    if (filters.date_from) p.date_from = filters.date_from;
    if (filters.date_to) p.date_to = filters.date_to;
    return p;
  }, [filters, sort, skip]);

  // Stats fetch ignores the status + pagination so the summary strip / sparkline
  // reflect the whole filtered set (deployment + instrument + date), capped.
  const statsParams = useMemo(() => {
    const p = { sort: "created_at", skip: 0, limit: STATS_CAP };
    if (filters.deployment_id) p.deployment_id = filters.deployment_id;
    if (filters.instrument) p.instrument = filters.instrument;
    if (filters.date_from) p.date_from = filters.date_from;
    if (filters.date_to) p.date_to = filters.date_to;
    return p;
  }, [filters.deployment_id, filters.instrument, filters.date_from, filters.date_to]);

  const fetchRows = useCallback(async () => {
    try {
      const [page, stats] = await Promise.all([
        api.listPaperTrades(params),
        api.listPaperTrades(statsParams),
      ]);
      setData({ items: page.items || [], total: page.total || 0 });
      setStatsRows(stats.items || []);
    } catch (e) {
      toast.error(`Paper trades load failed: ${e.response?.data?.detail || e.message}`);
    } finally {
      setLoading(false);
    }
  }, [params, statsParams]);

  useEffect(() => {
    api.listDeployments({ limit: 200 }).then((d) => setDeployments(d.items || [])).catch(() => {});
  }, []);

  useEffect(() => { fetchRows(); }, [fetchRows]);

  // Auto-refresh ≤30s (the evaluator marks open trades each market minute).
  useEffect(() => {
    const id = window.setInterval(fetchRows, 30000);
    return () => window.clearInterval(id);
  }, [fetchRows]);

  // Fast live open-positions poll (~2s) — overlays live P&L/premium onto OPEN rows only.
  const [livePos, setLivePos] = useState({ items: [], open_mtm: 0 });
  useEffect(() => {
    let alive = true;
    const tick = async () => {
      try {
        const data = await api.openPositions();
        if (alive) setLivePos(data || { items: [], open_mtm: 0 });
      } catch { /* transient; keep last value */ }
    };
    tick();
    const id = window.setInterval(tick, 2000);
    return () => { alive = false; window.clearInterval(id); };
  }, []);

  // Keyed by trade id for O(1) lookup in the row renderer.
  const liveById = useMemo(
    () => Object.fromEntries((livePos.items || []).map((p) => [p.id, p])),
    [livePos],
  );

  // Per-deployment OPEN count + MTM, derived from the filtered stats set (which
  // reliably carries deployment_id). Live unrealized_pnl overlays the snapshot
  // when a matching open position exists, for freshness.
  const perDeployOpen = useMemo(() => {
    const m = {};
    for (const t of statsRows) {
      if (String(t.status || "").toUpperCase() !== "OPEN") continue;
      const k = t.deployment_id || "—";
      const live = (livePos.items || []).find((p) => p.id === t.id);
      const pnl = live ? Number(live.unrealized_pnl || 0) : Number(t.unrealized_pnl || 0);
      (m[k] = m[k] || { openCount: 0, openMtm: 0 });
      m[k].openCount += 1; m[k].openMtm += pnl;
    }
    return m;
  }, [statsRows, livePos]);

  const setFilter = (k, v) => { setSkip(0); setSelected(new Set()); setFilters((f) => ({ ...f, [k]: v })); };

  // Keep ?deployment= in sync for links + reloads.
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
    Object.entries({ ...params, limit: STATS_CAP, skip: 0, format: "csv" }).forEach(([k, v]) => qs.set(k, String(v)));
    window.open(`${API}/paper/trades?${qs.toString()}`, "_blank");
  };

  // ---- Summary strip + sparkline (computed over the filtered stats set) ----
  const summary = useMemo(() => {
    const today = istToday();
    let todayRealized = 0, openMtm = 0, openCount = 0;
    let wins = 0, losses = 0, grossWin = 0, grossLoss = 0;
    const equity = [];
    let cum = 0;
    // Closed trades drive realized equity; order by closed_at ascending.
    const closed = statsRows
      .filter((t) => String(t.status || "").toUpperCase() === "CLOSED")
      .sort((a, b) => new Date(a.closed_at || a.updated_at || 0) - new Date(b.closed_at || b.updated_at || 0));
    for (const t of closed) {
      const pnl = Number(t.realized_pnl || 0);
      cum += pnl;
      equity.push(cum);
      if (pnl > 0) { wins += 1; grossWin += pnl; }
      else if (pnl < 0) { losses += 1; grossLoss += Math.abs(pnl); }
      const closedDay = istParts(t.closed_at || t.updated_at)?.day;
      if (closedDay === today) todayRealized += pnl;
    }
    for (const t of statsRows) {
      if (String(t.status || "").toUpperCase() === "OPEN") {
        openCount += 1;
        openMtm += Number(t.unrealized_pnl || 0);
      }
    }
    const decided = wins + losses;
    const winRate = decided ? (wins / decided) * 100 : null;
    const profitFactor = grossLoss > 0 ? grossWin / grossLoss : (grossWin > 0 ? Infinity : null);
    return { todayRealized, openMtm, openCount, winRate, profitFactor, equity, closedCount: closed.length };
  }, [statsRows]);

  // ---- Per-day realized P&L for the calendar heat-grid (closed trades only,
  // bucketed by IST close day) ----
  const dayPnl = useMemo(() => {
    const map = new Map(); // day -> { pnl, count }
    for (const t of statsRows) {
      if (String(t.status || "").toUpperCase() !== "CLOSED") continue;
      const day = istParts(t.closed_at || t.updated_at)?.day;
      if (!day) continue;
      const cur = map.get(day) || { pnl: 0, count: 0 };
      cur.pnl += Number(t.realized_pnl || 0);
      cur.count += 1;
      map.set(day, cur);
    }
    return map;
  }, [statsRows]);
  const [showCalendar, setShowCalendar] = useState(true);

  // ---- Selection / purge (CLOSED only) ----
  const closedVisibleIds = data.items.filter((t) => String(t.status || "").toUpperCase() === "CLOSED").map((t) => t.id);
  const toggleSelect = (id) => setSelected((s) => {
    const n = new Set(s);
    if (n.has(id)) n.delete(id); else n.add(id);
    return n;
  });
  const allClosedSelected = closedVisibleIds.length > 0 && closedVisibleIds.every((id) => selected.has(id));
  const toggleSelectAll = () => setSelected((s) => {
    if (allClosedSelected) return new Set();
    return new Set(closedVisibleIds);
  });

  const purge = async (payload, confirmMsg) => {
    if (!window.confirm(confirmMsg)) return;
    setBusy(true);
    try {
      const res = await api.purgePaperTrades(payload);
      toast.success(`Deleted ${res.deleted} closed trade${res.deleted === 1 ? "" : "s"}.`);
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
    purge({ ids: [...selected] }, `Delete ${selected.size} selected CLOSED trade${selected.size === 1 ? "" : "s"}? OPEN trades are never deleted. This cannot be undone.`);
  };
  const deleteOlder = () => {
    const n = parseInt(olderDays, 10);
    if (!n || n < 1) { toast.error("Enter a valid number of days."); return; }
    purge({ older_than_days: n }, `Delete all CLOSED trades older than ${n} days? This cannot be undone.`);
  };
  const purgeDeployment = () => {
    if (!filters.deployment_id) { toast.error("Select a deployment filter first."); return; }
    const name = deployments.find((d) => d.id === filters.deployment_id)?.name || filters.deployment_id;
    purge({ deployment_id: filters.deployment_id }, `Delete ALL CLOSED trades for deployment "${name}"? OPEN trades stay. This cannot be undone.`);
  };

  // ---- Close flows (premium, never spot) ----
  // Close with the backend safety guards surfaced to the operator: a flagged
  // implausible premium (e.g. a fat-fingered spot level) prompts an explicit
  // override; a concurrent auto-close/square-off (409) refreshes instead of
  // clobbering. Returns true on success, false if the operator declined.
  const closeWithSanity = async (tradeId, body) => {
    try {
      await api.closePaperTrade(tradeId, body);
      return true;
    } catch (e) {
      const detail = e.response?.data?.detail;
      if (e.response?.status === 400 && detail?.code === "implausible_premium") {
        if (window.confirm(`${detail.message}\n\nBook it anyway?`)) {
          await api.closePaperTrade(tradeId, { ...body, override_sanity: true });
          return true;
        }
        return false;  // operator chose to re-enter the price
      }
      if (e.response?.status === 409) {
        toast.info("Trade was already closed — refreshed.");
        await fetchRows();
        return false;
      }
      throw e;
    }
  };

  const closeAtMarket = async (trade) => {
    let price = trade.last_price;
    if (price == null) {
      const raw = window.prompt(`No live mark for ${trade.trading_symbol || trade.instrument}. Enter the exit premium (₹):`, trade.entry_price ?? "");
      if (raw == null) return;
      price = Number(raw);
      if (!Number.isFinite(price) || price < 0) { toast.error("Enter a valid premium."); return; }
    }
    setBusy(true);
    try {
      if (await closeWithSanity(trade.id, { exit_price: Number(price), reason: "manual_close_at_market" })) {
        toast.success(`Closed ${trade.trading_symbol || trade.instrument} @ ₹${fmtNum(price)}`);
        await fetchRows();
      }
    } catch (e) {
      toast.error(`Close failed: ${e.response?.data?.detail?.message || e.response?.data?.detail || e.message}`);
    } finally {
      setBusy(false);
    }
  };

  const closeManual = async (trade) => {
    const raw = manualPrice[trade.id];
    const price = Number(raw);
    if (raw === undefined || raw === "" || !Number.isFinite(price) || price < 0) {
      toast.error("Enter a valid exit premium first.");
      return;
    }
    setBusy(true);
    try {
      if (await closeWithSanity(trade.id, { exit_price: price, reason: "manual_close" })) {
        toast.success(`Closed ${trade.trading_symbol || trade.instrument} @ ₹${fmtNum(price)}`);
        setManualPrice((m) => { const n = { ...m }; delete n[trade.id]; return n; });
        await fetchRows();
      }
    } catch (e) {
      toast.error(`Close failed: ${e.response?.data?.detail?.message || e.response?.data?.detail || e.message}`);
    } finally {
      setBusy(false);
    }
  };

  const closeAllOpen = async () => {
    if (summary.openCount === 0) { toast.info("No open trades to close."); return; }
    const offHours = (livePos.items || []).length === 0; // no live marks → estimated fills
    const warn = offHours ? "\n\nMarket looks closed — open positions will close at an ESTIMATED price (last mark/entry), not a live fill." : "";
    if (!window.confirm(`Close all ${summary.openCount} open trade(s)?${warn}`)) return;
    setBusy(true);
    try {
      const res = await api.squareOffAll();
      toast.success(`Squared off ${res.count ?? (res.items?.length ?? 0)} trade(s).`);
      await fetchRows();
    } catch (e) {
      toast.error(`Close all failed: ${e.response?.data?.detail || e.message}`);
    } finally { setBusy(false); }
  };

  // ---- Live deployment controls (pause / resume / stop + master stop-all) ----
  const refreshAll = async () => {
    try { const d = await api.listDeployments({ limit: 200 }); setDeployments(d.items || []); } catch { /* keep last */ }
    await fetchRows();
  };
  const doPause = async (dep) => {
    setBusy(true);
    try { await api.pauseDeployment(dep.id); toast.success(`Paused "${dep.name || dep.id}"`); await refreshAll(); }
    catch (e) { toast.error(e.response?.data?.detail || e.message); }
    finally { setBusy(false); }
  };
  const doResume = async (dep) => {
    setBusy(true);
    try { await api.resumeDeployment(dep.id); toast.success(`Resumed "${dep.name || dep.id}"`); await refreshAll(); }
    catch (e) { toast.error(e.response?.data?.detail || e.message); }
    finally { setBusy(false); }
  };
  const doStop = async (dep) => {
    const oc = perDeployOpen[dep.id]?.openCount || 0;
    const offHours = (livePos.items || []).length === 0;
    const warn = (oc > 0 && offHours) ? "\n\nMarket looks closed — its open position(s) will close at an ESTIMATED price." : "";
    if (!window.confirm(`Stop "${dep.name || dep.id}"? This squares off its ${oc} open position(s) and pauses it (no new entries until Resume).${warn}`)) return;
    setBusy(true);
    try { const r = await api.stopDeployment(dep.id); toast.success(`Stopped "${dep.name || dep.id}" · squared off ${r.squared_off_count ?? 0}`); await refreshAll(); }
    catch (e) { toast.error(e.response?.data?.detail || e.message); }
    finally { setBusy(false); }
  };
  const doStopAll = async () => {
    const offHours = (livePos.items || []).length === 0;
    const warn = offHours ? "\n\nMarket looks closed — open positions will close at an ESTIMATED price." : "";
    if (!window.confirm(`Stop ALL paper trading? Squares off every open position and pauses all active strategies.${warn}`)) return;
    setBusy(true);
    try { const r = await api.stopAllPaper(); toast.success(`Stopped all · squared off ${r.squared_off_count ?? 0} · paused ${r.paused_deployment_ids?.length ?? 0}`); await refreshAll(); }
    catch (e) { toast.error(e.response?.data?.detail || e.message); }
    finally { setBusy(false); }
  };

  // Non-archived deployments for the Live Deployments strip.
  const liveDeployments = useMemo(
    () => deployments.filter((d) => String(d.status || "").toUpperCase() !== "ARCHIVED"),
    [deployments],
  );

  // ---- Day-wise grouping of the current page ----
  const groups = useMemo(() => {
    const map = new Map();
    for (const t of data.items) {
      const day = istParts(t.created_at)?.day || "—";
      if (!map.has(day)) map.set(day, []);
      map.get(day).push(t);
    }
    // Map preserves insertion order (rows already sorted server-side).
    return [...map.entries()].map(([day, rows]) => {
      const realized = rows.reduce((s, t) => s + (String(t.status).toUpperCase() === "CLOSED" ? Number(t.realized_pnl || 0) : 0), 0);
      const open = rows.reduce((s, t) => s + (String(t.status).toUpperCase() === "OPEN" ? Number(t.unrealized_pnl || 0) : 0), 0);
      return { day, rows, realized, open };
    });
  }, [data.items]);

  const total = data.total;
  const pageEnd = Math.min(skip + data.items.length, total);

  if (loading) return <Skeleton className="h-96 bg-bg-1" data-testid="paper-trading-page" />;

  return (
    <div className="space-y-3" data-testid="paper-trading-page">
      {/* Summary strip */}
      <div className="grid grid-cols-2 lg:grid-cols-6 gap-2" data-testid="paper-summary-strip">
        <Stat label="Today realized" value={inr(summary.todayRealized)} tone={summary.todayRealized} />
        <Stat label="Open MTM" value={inr((livePos.items || []).length > 0 ? livePos.open_mtm : summary.openMtm)} tone={(livePos.items || []).length > 0 ? livePos.open_mtm : summary.openMtm} />
        <Stat label="Open trades" value={summary.openCount} />
        <Stat label="Win rate" value={summary.winRate == null ? "—" : fmtPct(summary.winRate, 1)} />
        <Stat label="Profit factor" value={summary.profitFactor == null ? "—" : (summary.profitFactor === Infinity ? "∞" : fmtNum(summary.profitFactor, 2))} />
        <div className="rounded-md border border-line bg-bg-2 p-2" data-testid="paper-equity-sparkline">
          <div className="text-[10px] uppercase tracking-wider text-dimmer">Realized equity</div>
          <Sparkline values={summary.equity} />
        </div>
      </div>

      {/* P&L calendar heat-grid (per-day realized ₹, filtered set) */}
      <div className="rounded-lg border border-line bg-bg-1" data-testid="paper-pnl-calendar">
        <div className="px-3 py-2 border-b border-line flex items-center gap-2">
          <CalendarDays className="w-4 h-4 text-info" />
          <div className="text-xs font-semibold uppercase tracking-wider text-dim">P&amp;L Calendar</div>
          <span className="text-[11px] text-dimmer">realized ₹ per IST day{filters.deployment_id ? " · this deployment" : " · all deployments"}</span>
          <Button variant="ghost" size="sm" onClick={() => setShowCalendar((v) => !v)} className="ml-auto h-6 text-[11px]" data-testid="paper-calendar-toggle">
            {showCalendar ? "Hide" : "Show"}
          </Button>
        </div>
        {showCalendar && (
          <div className="p-3">
            <CalendarHeatGrid dayPnl={dayPnl} />
          </div>
        )}
      </div>

      {/* Live Deployments control strip */}
      <div className="rounded-lg border border-line bg-bg-1" data-testid="paper-deploy-strip">
        <div className="px-3 py-2 border-b border-line flex items-center gap-2">
          <Activity className="w-4 h-4 text-info" />
          <div className="text-xs font-semibold uppercase tracking-wider text-dim">Live Deployments</div>
          <span className="text-[11px] text-dimmer">pause / resume / stop · squares off open positions</span>
          <Button variant="outline" size="sm" disabled={busy || liveDeployments.length === 0} onClick={doStopAll}
            className="ml-auto h-7 text-xs border-rose-500/40 text-rose-300 hover:text-rose-200" data-testid="paper-stop-all"
            title="Square off every open position and pause all active strategies">
            <OctagonX className="w-3.5 h-3.5 mr-1" /> Stop ALL paper trading
          </Button>
        </div>
        {liveDeployments.length === 0 ? (
          <div className="px-3 py-3 text-[11px] text-dimmer">No active deployments.</div>
        ) : (
          <div className="divide-y divide-line">
            {liveDeployments.map((dep) => (
              <DeploymentControlRow
                key={dep.id}
                dep={dep}
                open={perDeployOpen[dep.id]}
                busy={busy}
                onPause={doPause}
                onResume={doResume}
                onStop={doStop}
              />
            ))}
          </div>
        )}
      </div>

      {/* Filters + actions */}
      <div className="rounded-lg border border-line bg-bg-1">
        <div className="px-3 py-2 border-b border-line flex items-center gap-2 flex-wrap">
          <Briefcase className="w-4 h-4 text-info" />
          <div className="text-xs font-semibold uppercase tracking-wider text-dim">Paper Trading Journal</div>
          <div className="text-[11px] text-dimmer ml-1">
            {total === 0 ? "no trades" : `${skip + 1}–${pageEnd} of ${total}`}
          </div>

          <select
            value={filters.deployment_id}
            onChange={(e) => setFilter("deployment_id", e.target.value)}
            className="ml-auto h-7 rounded-md border border-input bg-bg-2 px-2 text-xs text-foreground max-w-[180px]"
            data-testid="paper-deployment-filter"
          >
            <option value="">All deployments</option>
            {deployments.map((d) => <option key={d.id} value={d.id}>{d.name || d.id?.slice(0, 8)}</option>)}
          </select>

          <select value={filters.instrument} onChange={(e) => setFilter("instrument", e.target.value)}
            className="h-7 rounded-md border border-input bg-bg-2 px-2 text-xs text-foreground" data-testid="paper-instrument-filter">
            <option value="">All instruments</option>
            {INSTRUMENTS.map((i) => <option key={i} value={i}>{i}</option>)}
          </select>

          <select value={filters.status} onChange={(e) => setFilter("status", e.target.value)}
            className="h-7 rounded-md border border-input bg-bg-2 px-2 text-xs text-foreground" data-testid="paper-status-filter">
            <option value="">All statuses</option>
            <option value="OPEN">OPEN</option>
            <option value="CLOSED">CLOSED</option>
          </select>

          <Input type="date" value={filters.date_from} onChange={(e) => setFilter("date_from", e.target.value)}
            className="bg-bg-2 border-line h-7 text-xs w-[150px] pr-1" data-testid="paper-date-from" title="From (IST)" />
          <Input type="date" value={filters.date_to} onChange={(e) => setFilter("date_to", e.target.value)}
            className="bg-bg-2 border-line h-7 text-xs w-[150px] pr-1" data-testid="paper-date-to" title="To (IST)" />

          <Button variant="ghost" size="sm" onClick={closeAllOpen} disabled={busy || summary.openCount === 0}
            className="h-7 text-xs text-amber-300 hover:text-amber-200" data-testid="paper-close-all">
            <XCircle className="w-3 h-3 mr-1" /> Close all open
          </Button>
          <Button variant="ghost" size="sm" onClick={exportCsv} className="h-7 text-xs" data-testid="paper-export-csv">
            <Download className="w-3 h-3 mr-1" /> CSV
          </Button>
          <Button variant="ghost" size="sm" onClick={fetchRows} className="h-7 text-xs" data-testid="paper-refresh-button">
            <RefreshCw className="w-3 h-3 mr-1" /> Refresh
          </Button>
        </div>

        {/* Deletion toolkit (CLOSED only) */}
        <div className="px-3 py-1.5 flex items-center gap-2 flex-wrap text-[11px] text-dimmer">
          <Trash2 className="w-3.5 h-3.5" />
          <span>Cleanup (closed only):</span>
          <Button variant="outline" size="sm" disabled={busy || selected.size === 0} onClick={deleteSelected}
            className="h-6 text-[11px] border-rose-500/40 text-rose-300 hover:text-rose-200" data-testid="paper-delete-selected">
            Delete selected ({selected.size})
          </Button>
          <span className="ml-2">Older than</span>
          <Input value={olderDays} onChange={(e) => setOlderDays(e.target.value)} type="number" min={1}
            className="bg-bg-2 border-line h-6 text-[11px] w-16" data-testid="paper-delete-older-days" />
          <span>days</span>
          <Button variant="outline" size="sm" disabled={busy} onClick={deleteOlder}
            className="h-6 text-[11px] border-rose-500/40 text-rose-300 hover:text-rose-200" data-testid="paper-delete-older">
            Purge old
          </Button>
          <Button variant="outline" size="sm" disabled={busy || !filters.deployment_id} onClick={purgeDeployment}
            className="h-6 text-[11px] border-rose-500/40 text-rose-300 hover:text-rose-200 ml-2" data-testid="paper-purge-deployment"
            title={filters.deployment_id ? "" : "Select a deployment filter first"}>
            Purge this deployment
          </Button>
        </div>
      </div>

      {/* Table */}
      <div className="rounded-lg border border-line bg-bg-1 overflow-x-auto" data-testid="paper-trading-journal">
        <table className="w-full text-xs" data-testid="paper-trade-table">
          <thead className="sticky top-0 bg-bg-2 z-10">
            <tr className="text-dim border-b border-line">
              <th className="p-2 w-8">
                <input type="checkbox" checked={allClosedSelected} onChange={toggleSelectAll} data-testid="paper-select-all" title="Select closed trades on this page" />
              </th>
              <th className="text-left p-2">Deployment / Strategy</th>
              <th className="text-left p-2">Contract</th>
              <th className="text-left p-2">Side</th>
              <th className="text-right p-2">Lots × size</th>
              <th className="text-left p-2 cursor-pointer hover:text-foreground" onClick={() => toggleSort("created")}>Entry (IST){sortMark("created")}</th>
              <th className="text-right p-2 cursor-pointer hover:text-foreground" onClick={() => toggleSort("entry")}>Entry ₹{sortMark("entry")}</th>
              <th className="text-left p-2 cursor-pointer hover:text-foreground" onClick={() => toggleSort("closed")}>Exit (IST){sortMark("closed")}</th>
              <th className="text-right p-2">Exit ₹</th>
              <th className="text-left p-2">Exit reason</th>
              <th className="text-right p-2">Hold</th>
              <th className="text-left p-2">Risk</th>
              <th className="text-right p-2 cursor-pointer hover:text-foreground" onClick={() => toggleSort("pnl")} title="Net of slippage, spread and charges when the deployment priced fills like the backtest">P&L ₹ (net){sortMark("pnl")}</th>
              <th className="text-right p-2">P&L %</th>
              <th className="text-left p-2">Status</th>
              <th className="text-right p-2">Actions</th>
            </tr>
          </thead>
          <tbody>
            {data.items.length === 0 && (
              <tr><td colSpan="16" className="p-6 text-center text-dimmer">
                No paper trades match these filters. Deploy a preset in the Deployments command center (paper mode) to start auto-trading signals.
              </td></tr>
            )}
            {groups.map((g) => (
              <Fragment key={g.day}>
                <tr className="bg-bg-2/60 border-y border-line" data-testid="paper-day-group">
                  <td></td>
                  <td colSpan="11" className="px-2 py-1 text-[11px] font-semibold text-dim">{g.day}</td>
                  <td className={`px-2 py-1 text-right font-mono text-[11px] ${colorPnL(g.realized)}`} title="Day realized">{inr(g.realized)}</td>
                  <td colSpan="3" className={`px-2 py-1 text-[11px] ${colorPnL(g.open)}`}>
                    {g.open !== 0 ? `open ${inr(g.open)}` : ""}
                  </td>
                </tr>
                {g.rows.map((t) => {
                  const isOpen = String(t.status || "").toUpperCase() === "OPEN";
                  // Merge live feed data for OPEN rows (2s poll); fall back to 30s snapshot.
                  const live = isOpen ? liveById[t.id] : undefined;
                  const pnl = isOpen && live != null
                    ? live.unrealized_pnl
                    : tradePnl(t);
                  const pct = pnlPct(t, pnl);
                  const entry = istParts(t.created_at);
                  const exit = istParts(t.closed_at);
                  const risk = t.risk || {};
                  const spotExit = t.spot_exit || {};
                  return (
                    <tr key={t.id} className="border-b border-line hover:bg-bg-2" data-testid="paper-trade-row">
                      <td className="p-2">
                        {!isOpen && (
                          <input type="checkbox" checked={selected.has(t.id)} onChange={() => toggleSelect(t.id)} data-testid="paper-row-select" />
                        )}
                      </td>
                      <td className="p-2">
                        <div className="font-medium truncate max-w-[150px]" title={t.deployment_name}>{t.deployment_name || t.deployment_id?.slice(0, 8) || "—"}</div>
                        <div className="text-dimmer truncate max-w-[150px]" title={t.strategy_id}>{t.strategy_id}</div>
                      </td>
                      <td className="p-2 font-mono text-dim whitespace-nowrap">{t.trading_symbol || t.instrument || "—"}</td>
                      <td className="p-2"><span className={`font-mono ${t.direction === "CE" ? "text-emerald-400" : t.direction === "PE" ? "text-red-400" : "text-dim"}`}>{t.direction || "—"}</span></td>
                      <td className="p-2 font-mono text-right text-dim">{t.lots ?? "—"} × {t.lot_size ?? "—"}</td>
                      <td className="p-2 font-mono text-dim whitespace-nowrap">{entry ? entry.time : "—"}</td>
                      <td className="p-2 font-mono text-right">{fmtNum(t.entry_price)}</td>
                      <td className="p-2 font-mono text-dim whitespace-nowrap">{exit ? exit.time : (isOpen ? "open" : "—")}</td>
                      <td className="p-2 font-mono text-right">
                        {t.exit_price != null
                          ? fmtNum(t.exit_price)
                          : isOpen
                            ? fmtNum(live?.live_premium ?? t.last_price)
                            : "—"}
                        {isOpen && live?.live_stale && (
                          <span className="ml-1 text-[9px] px-1 rounded border border-amber-500/40 text-amber-300" title="Live premium is stale — no fresh tick recently">stale</span>
                        )}
                      </td>
                      <td className="p-2 text-dimmer">
                        <div className="flex items-center gap-1 max-w-[150px]">
                          <span className="truncate" title={t.exit_reason}>{t.exit_reason || "—"}</span>
                          {t.exit_price_stale && (
                            <span className="shrink-0 text-[9px] px-1 rounded border border-amber-500/40 text-amber-300" data-testid="paper-stale-fill"
                              title={`Exit price estimated (${t.exit_price_source || "stale"}) — not a fresh fill at the exit minute; treat the P&L as approximate.`}>est</span>
                          )}
                        </div>
                      </td>
                      <td className="p-2 font-mono text-right text-dim">{holdingTime(t.created_at, t.closed_at)}</td>
                      <td className="p-2">
                        <span className="text-[10px] px-1.5 py-0.5 rounded border border-line bg-bg-3 font-mono" data-testid="risk-badge"
                          title={spotExit.spot_target != null || spotExit.spot_stop != null ? `spot T ${spotExit.spot_target ?? "--"} / S ${spotExit.spot_stop ?? "--"}` : ""}>
                          S {risk.stop_price ?? "--"} / T {risk.target_price ?? "--"}
                        </span>
                        {isOpen && live != null && (live.dist_to_stop != null || live.dist_to_target != null) && (
                          <div className="text-[9px] font-mono text-dimmer mt-0.5 whitespace-nowrap" data-testid="live-dist-badge"
                            title="Live distance to stop / target (pts from current premium)">
                            Δs {live.dist_to_stop != null ? fmtNum(live.dist_to_stop, 1) : "--"} / Δt {live.dist_to_target != null ? fmtNum(live.dist_to_target, 1) : "--"}
                          </div>
                        )}
                      </td>
                      <td className={`p-2 font-mono text-right ${colorPnL(pnl)}`}>
                        {inr(pnl)}
                        {!isOpen && t.friction_cost != null && Number(t.friction_cost) !== 0 && (
                          <div className="text-[9px] text-dimmer font-normal" data-testid="paper-gross-friction"
                            title={`gross ${inr(t.gross_realized_pnl)} − friction ${inr(Math.abs(Number(t.friction_cost)))} (incl. charges ${inr(t.total_charges)}) = net ${inr(pnl)}`}>
                            gross {inr(t.gross_realized_pnl)} · −{inr(Math.abs(Number(t.friction_cost)))} fric
                          </div>
                        )}
                      </td>
                      <td className={`p-2 font-mono text-right ${colorPnL(pct)}`}>{pct == null ? "—" : fmtPct(pct, 1)}</td>
                      <td className="p-2">
                        <span className={`text-[10px] px-1.5 py-0.5 rounded border font-mono ${isOpen ? "border-emerald-500/40 text-emerald-300" : "border-line text-dim"}`}>{t.status}</span>
                      </td>
                      <td className="p-2">
                        {isOpen && (
                          <div className="flex items-center justify-end gap-1.5">
                            <Input
                              type="number"
                              placeholder="₹ exit"
                              value={manualPrice[t.id] ?? ""}
                              onChange={(e) => setManualPrice((m) => ({ ...m, [t.id]: e.target.value }))}
                              className="h-7 w-20 bg-bg-1 border-line text-right text-[11px]"
                              data-testid="mark-paper-trade"
                              title="Manual exit premium (off-hours fallback)"
                            />
                            <Button size="sm" variant="secondary" disabled={busy} onClick={() => closeManual(t)}
                              className="h-7 text-[11px] border border-line px-2" title="Close at the entered premium">
                              Close
                            </Button>
                            <Button size="sm" disabled={busy} onClick={() => closeAtMarket(t)}
                              className="h-7 text-[11px] bg-bg-3 border border-line hover:bg-bg-2 px-2" data-testid="close-paper-trade"
                              title="Close at the trade's last live mark (premium)">
                              <Zap className="w-3 h-3 mr-1" /> @ market
                            </Button>
                          </div>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </Fragment>
            ))}
          </tbody>
        </table>

        {/* Pagination */}
        <div className="px-3 py-2 border-t border-line flex items-center gap-2 text-[11px] text-dimmer">
          <span>Showing {total === 0 ? 0 : skip + 1}–{pageEnd} of {total}</span>
          <div className="ml-auto flex items-center gap-1">
            <Button variant="ghost" size="sm" disabled={skip === 0} onClick={() => setSkip(Math.max(0, skip - PAGE_SIZE))}
              className="h-6 text-[11px]" data-testid="paper-prev-page">
              <ChevronLeft className="w-3 h-3 mr-0.5" /> Prev
            </Button>
            <Button variant="ghost" size="sm" disabled={pageEnd >= total} onClick={() => setSkip(skip + PAGE_SIZE)}
              className="h-6 text-[11px]" data-testid="paper-next-page">
              Next <ChevronRight className="w-3 h-3 ml-0.5" />
            </Button>
          </div>
        </div>
      </div>

      <div className="text-[10px] text-dimmer px-1">
        Paper trades are simulated on real streamed prices — no broker orders. Entries/exits are option premium (₹), never the spot index; lot size comes from the contract. OPEN trades are never deletable. Times are IST. Summary + sparkline cover up to {STATS_CAP} trades for the current filter.
      </div>
    </div>
  );
}

function Stat({ label, value, tone = null }) {
  const toneClass = tone == null ? "" : Number(tone) > 0 ? "text-success" : Number(tone) < 0 ? "text-danger" : "";
  return (
    <div className="rounded-md border border-line bg-bg-2 p-2">
      <div className="text-[10px] uppercase tracking-wider text-dimmer">{label}</div>
      <div className={`text-sm font-mono mt-0.5 ${toneClass}`}>{value}</div>
    </div>
  );
}

// One row of the Live Deployments strip: status dot + strategy/name, live open
// count + MTM, and the Pause/Resume + Stop controls.
function DeploymentControlRow({ dep, open, busy, onPause, onResume, onStop }) {
  const status = String(dep.status || "").toUpperCase();
  const isActive = status === "ACTIVE";
  const isPaused = status === "PAUSED";
  const dot = isActive ? "bg-emerald-400" : isPaused ? "bg-amber-400" : "bg-dimmer";
  const statusText = isActive ? "text-emerald-300" : isPaused ? "text-amber-300" : "text-dimmer";
  const openCount = open?.openCount || 0;
  const openMtm = open?.openMtm || 0;
  return (
    <div className="px-3 py-2 flex items-center gap-2 flex-wrap" data-testid="paper-deploy-row">
      <span className={`w-2 h-2 rounded-full shrink-0 ${dot}`} title={status || "—"} />
      <div className="min-w-0">
        <div className="font-medium text-xs truncate max-w-[200px]" title={dep.name}>{dep.name || dep.id?.slice(0, 8) || "—"}</div>
        <div className="text-[10px] text-dimmer truncate max-w-[200px]" title={dep.strategy_id}>{dep.strategy_id || "—"}</div>
      </div>
      <span className={`ml-2 text-[11px] uppercase tracking-wider ${statusText}`}>{status || "—"}</span>
      <span className="ml-3 text-[11px] font-mono text-dim whitespace-nowrap">
        {openCount} open · MTM <span className={colorPnL(openMtm)}>{inr(openMtm)}</span>
      </span>
      <div className="ml-auto flex items-center gap-1.5">
        {isActive && (
          <Button variant="ghost" size="sm" disabled={busy} onClick={() => onPause(dep)}
            className="h-7 text-xs text-amber-300 hover:text-amber-200" data-testid="paper-deploy-pause">
            <Pause className="w-3 h-3 mr-1" /> Pause
          </Button>
        )}
        {isPaused && (
          <Button variant="ghost" size="sm" disabled={busy} onClick={() => onResume(dep)}
            className="h-7 text-xs text-emerald-300 hover:text-emerald-200" data-testid="paper-deploy-resume">
            <Play className="w-3 h-3 mr-1" /> Resume
          </Button>
        )}
        <Button variant="outline" size="sm" disabled={busy} onClick={() => onStop(dep)}
          className="h-7 text-xs border-rose-500/40 text-rose-300 hover:text-rose-200" data-testid="paper-deploy-stop">
          <Square className="w-3 h-3 mr-1" /> Stop
        </Button>
      </div>
    </div>
  );
}

// GitHub-style P&L calendar heat-grid: weekday rows (Mon–Fri) × week columns,
// each cell colored by that IST day's realized ₹ (green positive, red negative).
function CalendarHeatGrid({ dayPnl }) {
  const days = [...dayPnl.keys()].sort();
  if (days.length === 0) {
    return <div className="text-[11px] text-dimmer font-mono">No closed trades to chart yet.</div>;
  }
  const dayToUTC = (s) => { const [y, m, d] = s.split("-").map(Number); return Date.UTC(y, m - 1, d); };
  const utcToDay = (ms) => {
    const d = new Date(ms);
    return `${d.getUTCFullYear()}-${pad(d.getUTCMonth() + 1)}-${pad(d.getUTCDate())}`;
  };
  const DAY_MS = 86400000;
  let startMs = dayToUTC(days[0]);
  const endMs = dayToUTC(days[days.length - 1]);
  // Align the first column to Monday (getUTCDay: 0=Sun..6=Sat).
  const startDow = new Date(startMs).getUTCDay();
  startMs -= ((startDow + 6) % 7) * DAY_MS;
  // Cap to the most recent ~16 weeks to keep the grid compact.
  const MAX_WEEKS = 16;
  const minStart = endMs - (MAX_WEEKS * 7 - 1) * DAY_MS;
  if (startMs < minStart) {
    const ms = new Date(minStart);
    startMs = minStart - ((ms.getUTCDay() + 6) % 7) * DAY_MS;
  }
  const maxAbs = Math.max(1, ...[...dayPnl.values()].map((v) => Math.abs(v.pnl)));

  const weeks = [];
  for (let wkMs = startMs; wkMs <= endMs; wkMs += 7 * DAY_MS) {
    const cells = [];
    for (let i = 0; i < 5; i++) { // Mon..Fri (trading days)
      const cellMs = wkMs + i * DAY_MS;
      const day = utcToDay(cellMs);
      cells.push({ day, future: cellMs > Date.now(), info: dayPnl.get(day) || null });
    }
    weeks.push({ key: utcToDay(wkMs), cells });
  }

  const WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri"];
  const cellStyle = (info) => {
    if (!info || info.count === 0) return {};
    const intensity = 0.25 + 0.75 * (Math.abs(info.pnl) / maxAbs);
    if (info.pnl === 0) return {};
    return {
      backgroundColor: info.pnl > 0 ? "var(--color-success)" : "var(--color-danger)",
      opacity: intensity,
    };
  };

  return (
    <div className="flex items-start gap-2">
      <div className="flex flex-col gap-1 pt-0.5 mr-1">
        {WEEKDAYS.map((d) => <div key={d} className="text-[9px] text-dimmer h-3.5 leading-3.5">{d}</div>)}
      </div>
      <div className="flex gap-1 overflow-x-auto">
        {weeks.map((wk) => (
          <div key={wk.key} className="flex flex-col gap-1">
            {wk.cells.map((c) => (
              <div
                key={c.day}
                className={`w-3.5 h-3.5 rounded-sm border ${c.info && c.info.count ? "border-transparent" : "border-line bg-bg-3"} ${c.future ? "opacity-20" : ""}`}
                style={cellStyle(c.info)}
                title={c.info && c.info.count
                  ? `${c.day}: ₹${fmtNum(c.info.pnl, 0)} · ${c.info.count} trade${c.info.count === 1 ? "" : "s"}`
                  : `${c.day}: no trades`}
                data-testid="paper-calendar-cell"
              />
            ))}
          </div>
        ))}
      </div>
      <div className="flex items-center gap-1 ml-3 self-end text-[9px] text-dimmer">
        <span>loss</span>
        <span className="w-3 h-3 rounded-sm" style={{ backgroundColor: "var(--color-danger)" }} />
        <span className="w-3 h-3 rounded-sm border border-line bg-bg-3" />
        <span className="w-3 h-3 rounded-sm" style={{ backgroundColor: "var(--color-success)" }} />
        <span>profit</span>
      </div>
    </div>
  );
}

// Tiny inline SVG sparkline of cumulative realized P&L.
function Sparkline({ values }) {
  if (!values || values.length < 2) {
    return <div className="text-[11px] text-dimmer mt-1 font-mono">no closed trades</div>;
  }
  const W = 120, H = 28;
  const min = Math.min(...values), max = Math.max(...values);
  const span = max - min || 1;
  const pts = values.map((v, i) => {
    const x = (i / (values.length - 1)) * W;
    const y = H - ((v - min) / span) * H;
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  }).join(" ");
  const last = values[values.length - 1];
  const stroke = last >= 0 ? "var(--color-success)" : "var(--color-danger)";
  return (
    <svg width={W} height={H} className="mt-1 overflow-visible" preserveAspectRatio="none">
      <polyline points={pts} fill="none" stroke={stroke} strokeWidth="1.5" />
    </svg>
  );
}
