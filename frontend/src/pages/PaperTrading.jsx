import { useCallback, useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { api, API } from "@/lib/api";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { fmtNum, fmtINR } from "@/lib/fmt";
import {
  Briefcase, RefreshCw, Download, Trash2, XCircle, Wifi, WifiOff,
} from "lucide-react";
import AccountHero from "@/components/paper/AccountHero";
import PeriodPnlCards from "@/components/paper/PeriodPnlCards";
import StrategyStatsTable from "@/components/paper/StrategyStatsTable";
import DeploymentControlStrip from "@/components/paper/DeploymentControlStrip";
import OverallSettingsPanel from "@/components/live/OverallSettingsPanel";
import FeedHealthBanner from "@/components/live/FeedHealthBanner";
import PnlCalendar from "@/components/paper/PnlCalendar";
import ExitReasonBreakdown from "@/components/paper/ExitReasonBreakdown";
import TradeBlotter from "@/components/paper/TradeBlotter";
import { exitReasonBreakdown } from "@/lib/paperAgg";

/**
 * Paper Trading dashboard (route /paper, redesigned 2026-06-21).
 *
 * An analytics dashboard over the paper-trade journal: a configurable-capital
 * account hero (value + equity curve), period P&L cards, per-strategy
 * attribution, the Live Deployments control strip, a P&L calendar, and a flat
 * sortable blotter with per-trade Max/Min/Now P&L, sparkline, SL/TP + duration.
 *
 * The page is the orchestrator — it owns all server fetches, the close/purge
 * flows, the 2s live open-positions poll and the 30s refresh, and passes data +
 * handlers down to presentational components under components/paper/.
 * Filter / sort / paginate / CSV is server-side; the table page auto-refreshes
 * ≤30s. Purge (POST /api/paper/trades/purge) deletes CLOSED trades only — OPEN
 * trades are never deletable (trading-domain rule). Entries/exits are option
 * PREMIUM (₹), never the spot index. Times are IST.
 */

const INSTRUMENTS = ["NIFTY", "BANKNIFTY", "SENSEX"];
const PAGE_SIZE = 100;
const STATS_CAP = 500; // P&L calendar / per-deploy fallback computed over up to this many filtered trades

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

export default function PaperTrading() {
  const [searchParams, setSearchParams] = useSearchParams();

  const [data, setData] = useState({ items: [], total: 0 });
  const [statsRows, setStatsRows] = useState([]);
  const [deployments, setDeployments] = useState([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);

  // Analytics dashboard state (realized-based; refreshed on the 30s cadence).
  const [analytics, setAnalytics] = useState(null);
  const [strategyStats, setStrategyStats] = useState([]);
  const [startingCapital, setStartingCapital] = useState(null);

  const [filters, setFilters] = useState({
    deployment_id: searchParams.get("deployment") || "",
    instrument: "",
    status: "",
    strategy_id: "",
    direction: "",
    exit_reason: "",
    date_from: "",
    date_to: "",
  });
  const [sort, setSort] = useState("-created_at");
  const [skip, setSkip] = useState(0);
  const [olderDays, setOlderDays] = useState("30");
  const [selected, setSelected] = useState(() => new Set());

  // Server params for the paginated table (per-trade analytics included).
  const params = useMemo(() => {
    const p = { sort, skip, limit: PAGE_SIZE, include_analytics: true };
    if (filters.deployment_id) p.deployment_id = filters.deployment_id;
    if (filters.instrument) p.instrument = filters.instrument;
    if (filters.status) p.status = filters.status;
    if (filters.strategy_id) p.strategy_id = filters.strategy_id;
    if (filters.direction) p.direction = filters.direction;
    if (filters.exit_reason) p.exit_reason = filters.exit_reason;
    if (filters.date_from) p.date_from = filters.date_from;
    if (filters.date_to) p.date_to = filters.date_to;
    return p;
  }, [filters, sort, skip]);

  // Stats fetch ignores the status + pagination so the P&L calendar / per-deploy
  // fallback reflect the whole filtered set (deployment + instrument + date), capped.
  const statsParams = useMemo(() => {
    const p = { sort: "created_at", skip: 0, limit: STATS_CAP };
    if (filters.deployment_id) p.deployment_id = filters.deployment_id;
    if (filters.instrument) p.instrument = filters.instrument;
    if (filters.strategy_id) p.strategy_id = filters.strategy_id;
    if (filters.date_from) p.date_from = filters.date_from;
    if (filters.date_to) p.date_to = filters.date_to;
    return p;
  }, [filters.deployment_id, filters.instrument, filters.strategy_id, filters.date_from, filters.date_to]);

  // Options for the blotter's Strategy header filter: distinct strategy_id across
  // non-archived deployments, labelled by deployment name (sets filters.strategy_id).
  const strategyOptions = useMemo(() => {
    const seen = new Map();
    for (const d of deployments) {
      if (String(d.status || "").toUpperCase() === "ARCHIVED") continue;
      const sid = d.strategy_id;
      if (!sid || seen.has(sid)) continue;
      seen.set(sid, d.name || sid);
    }
    return [...seen.entries()]
      .map(([value, label]) => ({ value, label }))
      .sort((a, b) => a.label.localeCompare(b.label));
  }, [deployments]);

  const fetchRows = useCallback(async () => {
    try {
      const [page, stats, an, ss] = await Promise.all([
        api.listPaperTrades(params),
        api.listPaperTrades(statsParams),
        api.paperAnalytics().catch(() => null),
        api.paperStrategyStats().catch(() => null),
      ]);
      setData({ items: page.items || [], total: page.total || 0 });
      setStatsRows(stats.items || []);
      if (an) setAnalytics(an);
      if (ss) setStrategyStats(ss.items || []);
    } catch (e) {
      toast.error(`Paper trades load failed: ${e.response?.data?.detail || e.message}`);
    } finally {
      setLoading(false);
    }
  }, [params, statsParams]);

  useEffect(() => {
    api.listDeployments({ limit: 200 }).then((d) => setDeployments(d.items || [])).catch(() => {});
  }, []);

  // Starting capital is fetched once (the user edits it via the AccountHero).
  const [acctCapCfg, setAcctCapCfg] = useState({ enforce_capital: false, capital_basis: "fixed" });
  useEffect(() => {
    api.getPaperAccountConfig()
      .then((c) => {
        setStartingCapital(c?.starting_capital ?? null);
        setAcctCapCfg({ enforce_capital: Boolean(c?.enforce_capital), capital_basis: c?.capital_basis || "fixed" });
      })
      .catch(() => {});
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
        const d = await api.openPositions();
        if (alive) setLivePos(d || { items: [], open_mtm: 0 });
      } catch { /* transient; keep last value */ }
    };
    tick();
    const id = window.setInterval(tick, 2000);
    return () => { alive = false; window.clearInterval(id); };
  }, []);

  // Per-deployment OPEN count + MTM for the control strip. The strip is GLOBAL,
  // so it must NOT inherit the table's deployment filter — prefer the global live
  // open-positions feed (carries deployment_id + fresh unrealized_pnl). Fall back
  // to the (filtered) stats set only until the live feed's first poll lands.
  const perDeployOpen = useMemo(() => {
    const m = {};
    const add = (k, pnl) => {
      const key = k || "—";
      (m[key] = m[key] || { openCount: 0, openMtm: 0 });
      m[key].openCount += 1; m[key].openMtm += Number(pnl || 0);
    };
    const live = livePos.items || [];
    if (live.length && live.some((p) => p.deployment_id != null)) {
      for (const p of live) add(p.deployment_id, p.unrealized_pnl);
      return m;
    }
    for (const t of statsRows) {
      if (String(t.status || "").toUpperCase() === "OPEN") add(t.deployment_id, t.unrealized_pnl);
    }
    return m;
  }, [statsRows, livePos]);

  // Open-trade count for the close-all guard (from the live feed, fallback to stats).
  const openCount = useMemo(() => {
    const live = livePos.items || [];
    if (live.length) return live.length;
    return statsRows.filter((t) => String(t.status || "").toUpperCase() === "OPEN").length;
  }, [statsRows, livePos]);

  // Feed-health chip: green "Live" when we have fresh marks, amber otherwise.
  const livePosHealth = useMemo(() => {
    const live = livePos.items || [];
    if (live.length === 0) return { live: false, label: "Estimated / stale" };
    const allStale = live.every((p) => p.live_stale);
    if (allStale) return { live: false, label: "Estimated / stale" };
    return { live: true, label: "Live" };
  }, [livePos]);

  // Live-feed health from the backend endpoint (drives the banner + LED truthfulness).
  const [feedHealth, setFeedHealth] = useState(null);
  useEffect(() => {
    let alive = true;
    const tick = () => api.getLiveFeedHealth().then((h) => { if (alive) setFeedHealth(h); }).catch(() => {});
    tick();
    const id = setInterval(tick, 5000);
    return () => { alive = false; clearInterval(id); };
  }, []);

  // Active deployment count for the banner (non-archived, ACTIVE status).
  const activeCount = (deployments || []).filter((d) => String(d.status || "").toUpperCase() === "ACTIVE").length;

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

  const closedVisibleIds = useMemo(
    () => data.items.filter((t) => String(t.status || "").toUpperCase() === "CLOSED").map((t) => t.id),
    [data.items],
  );
  const allClosedSelected = closedVisibleIds.length > 0 && closedVisibleIds.every((id) => selected.has(id));
  const toggleRow = (id) => setSelected((s) => { const n = new Set(s); n.has(id) ? n.delete(id) : n.add(id); return n; });
  const toggleAll = () => setSelected(() => (allClosedSelected ? new Set() : new Set(closedVisibleIds)));

  // TradeBlotter passes the raw sort field (e.g. "created_at"); toggle asc/desc/-.
  const toggleSort = (field) => {
    if (!field) return;
    setSkip(0);
    setSort((cur) => (cur === field ? `-${field}` : cur === `-${field}` ? field : `-${field}`));
  };

  const exportCsv = () => {
    const qs = new URLSearchParams();
    // CSV export keeps include_analytics off (heavier per-row payload not needed).
    const { include_analytics, ...csvParams } = params; // eslint-disable-line no-unused-vars
    Object.entries({ ...csvParams, limit: STATS_CAP, skip: 0, format: "csv" }).forEach(([k, v]) => qs.set(k, String(v)));
    window.open(`${API}/paper/trades?${qs.toString()}`, "_blank");
  };

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

  const exitBreakdown = useMemo(() => exitReasonBreakdown(statsRows), [statsRows]);

  // ---- Purge (CLOSED only). Phase-2: "Delete selected" is restored via per-row
  // checkboxes in the blotter. OPEN trades are never deletable (trading-domain rule).
  const purge = async (payload, confirmMsg) => {
    if (!window.confirm(confirmMsg)) return;
    setBusy(true);
    try {
      const res = await api.purgePaperTrades(payload);
      toast.success(`Deleted ${res.deleted} closed trade${res.deleted === 1 ? "" : "s"}.`);
      setSkip(0);
      setSelected(new Set());
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

  const closeAllOpen = async () => {
    if (openCount === 0) { toast.info("No open trades to close."); return; }
    const offHours = (livePos.items || []).length === 0; // no live marks → estimated fills
    const warn = offHours ? "\n\nMarket looks closed — open positions will close at an ESTIMATED price (last mark/entry), not a live fill." : "";
    if (!window.confirm(`Close all ${openCount} open trade(s)?${warn}`)) return;
    setBusy(true);
    try {
      const res = await api.squareOffAll();
      toast.success(`Squared off ${res.count ?? (res.items?.length ?? 0)} trade(s).`);
      await fetchRows();
    } catch (e) {
      toast.error(`Close all failed: ${e.response?.data?.detail || e.message}`);
    } finally { setBusy(false); }
  };

  // ---- Configurable starting capital (+ optional account-wide entry ceiling) ----
  const handleSetCapital = async (v, opts = {}) => {
    setBusy(true);
    try {
      const res = await api.setPaperAccountConfig({ starting_capital: v, ...opts });
      const cap = res?.starting_capital ?? v;
      setStartingCapital(cap);
      setAcctCapCfg({ enforce_capital: Boolean(res?.enforce_capital), capital_basis: res?.capital_basis || "fixed" });
      toast.success(`Starting capital set to ${fmtINR(cap)}${res?.enforce_capital ? " (account-wide entry ceiling ON)" : ""}`);
      const an = await api.paperAnalytics().catch(() => null);
      if (an) setAnalytics(an);
    } catch (e) {
      toast.error(`Couldn't update starting capital: ${e.response?.data?.detail || e.message}`);
    } finally {
      setBusy(false);
    }
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
    try { const r = await api.stopAllDeployments(); toast.success(`Stopped all · squared off ${r.squared_off_count ?? 0} · paused ${r.paused_deployment_ids?.length ?? 0}`); await refreshAll(); }
    catch (e) { toast.error(e.response?.data?.detail || e.message); }
    finally { setBusy(false); }
  };

  // Non-archived deployments for the Live Deployments strip.
  const liveDeployments = useMemo(
    () => deployments.filter((d) => String(d.status || "").toUpperCase() !== "ARCHIVED"),
    [deployments],
  );

  const total = data.total;
  const pageEnd = Math.min(skip + data.items.length, total);

  if (loading) return <Skeleton className="h-96 bg-bg-1" data-testid="paper-trading-page" />;

  return (
    <div className="space-y-3" data-testid="paper-trading-page">
      {/* Header row + feed-health chip */}
      <div className="flex items-center gap-2">
        <Briefcase className="w-4 h-4 text-info" />
        <div className="text-sm font-semibold uppercase tracking-wider text-dim">Paper Trading</div>
        <span
          className={`ml-auto inline-flex items-center gap-1 text-[11px] px-2 py-0.5 rounded-full border font-mono ${
            livePosHealth.live
              ? "border-emerald-500/40 text-emerald-300 bg-emerald-500/10"
              : "border-amber-500/40 text-warning bg-amber-500/10"
          }`}
          data-testid="paper-feed-health"
          title={livePosHealth.live
            ? "Live ticks streaming — open P&L is from fresh marks."
            : "No fresh marks — open P&L is estimated from last mark/entry."}
        >
          {livePosHealth.live ? <Wifi className="w-3 h-3" /> : <WifiOff className="w-3 h-3" />}
          {livePosHealth.label}
        </span>
      </div>

      {/* Account hero — value + equity curve + editable starting capital */}
      <AccountHero analytics={analytics} startingCapital={startingCapital} capitalConfig={acctCapCfg} onSetCapital={handleSetCapital} busy={busy} />

      {/* Period P&L cards */}
      <PeriodPnlCards period={analytics?.period_pnl} />

      {/* Per-strategy attribution — click a row to filter the blotter */}
      <StrategyStatsTable stats={strategyStats} onFilterStrategy={(sid) => setFilter("strategy_id", sid)} />

      {/* Feed health prompt banner (shown when active deployments exist but feed is offline) */}
      <FeedHealthBanner feedHealth={feedHealth} activeCount={activeCount} />

      {/* Live Deployments control strip */}
      <DeploymentControlStrip
        liveDeployments={liveDeployments}
        perDeployOpen={perDeployOpen}
        busy={busy}
        feedHealth={feedHealth}
        onPause={doPause}
        onResume={doResume}
        onStop={doStop}
        onStopAll={doStopAll}
        onCapsSaved={refreshAll}
      />

      {/* Basket-level overall controls (Live-page parity): SL / target / trailing
          on the WHOLE open paper basket, evaluated by the exit monitor. */}
      <div className="rounded-lg border border-line bg-bg-1" data-testid="paper-overall-controls">
        <div className="px-3 py-2 border-b border-line">
          <div className="text-[11px] font-semibold uppercase tracking-wider text-dim">
            Overall Controls — paper basket
          </div>
          <div className="text-[10px] text-dimmer">
            Basket SL / target / trailing across ALL open paper positions; a breach squares the whole basket (evaluated by the exit monitor, ~1.5s).
          </div>
        </div>
        <div className="p-3">
          <OverallSettingsPanel scope="paper" />
        </div>
      </div>

      {/* P&L calendar heat-grid (per-day realized ₹, filtered set) + global exit-reason card */}
      <div className="grid lg:grid-cols-[2fr_1fr] gap-3">
        <PnlCalendar dayPnl={dayPnl} />
        <ExitReasonBreakdown breakdown={exitBreakdown} variant="full" />
      </div>

      {/* Filters + actions */}
      <div className="rounded-lg border border-line bg-bg-1">
        <div className="px-3 py-2 border-b border-line flex items-center gap-2 flex-wrap">
          <Briefcase className="w-4 h-4 text-info" />
          <div className="text-xs font-semibold uppercase tracking-wider text-dim">Paper Trading Journal</div>
          <div className="text-[11px] text-dimmer ml-1">
            {total === 0 ? "no trades" : `${skip + 1}–${pageEnd} of ${total}`}
          </div>

          {filters.strategy_id && (
            <span className="text-[11px] text-info inline-flex items-center gap-1 border border-info/40 rounded px-1.5 py-0.5"
              data-testid="paper-strategy-filter-chip">
              strategy: {filters.strategy_id}
              <button onClick={() => setFilter("strategy_id", "")} className="text-dimmer hover:text-foreground" title="Clear strategy filter">×</button>
            </span>
          )}

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

          <Input type="date" value={filters.date_from} onChange={(e) => setFilter("date_from", e.target.value)}
            className="bg-bg-2 border-line h-7 text-xs w-[150px] pr-1" data-testid="paper-date-from" title="From (IST)" />
          <Input type="date" value={filters.date_to} onChange={(e) => setFilter("date_to", e.target.value)}
            className="bg-bg-2 border-line h-7 text-xs w-[150px] pr-1" data-testid="paper-date-to" title="To (IST)" />

          <Button variant="ghost" size="sm" onClick={closeAllOpen} disabled={busy || openCount === 0}
            className="h-7 text-xs text-warning" data-testid="paper-close-all">
            <XCircle className="w-3 h-3 mr-1" /> Close all open
          </Button>
          <Button variant="ghost" size="sm" onClick={exportCsv} className="h-7 text-xs" data-testid="paper-export-csv">
            <Download className="w-3 h-3 mr-1" /> CSV
          </Button>
          <Button variant="ghost" size="sm" onClick={fetchRows} className="h-7 text-xs" data-testid="paper-refresh-button">
            <RefreshCw className="w-3 h-3 mr-1" /> Refresh
          </Button>
        </div>

        {/* Deletion toolkit (CLOSED only). Phase-2: "Delete selected" restored via blotter checkboxes. */}
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

      {/* Redesigned flat sortable blotter (per-trade analytics) */}
      <TradeBlotter rows={data.items} sort={sort} onToggleSort={toggleSort} onCloseAtMarket={closeAtMarket} busy={busy}
        selected={selected} onToggleRow={toggleRow} onToggleAll={toggleAll} allClosedSelected={allClosedSelected}
        filters={filters} onSetFilter={setFilter} strategyOptions={strategyOptions} />

      {/* Pagination */}
      <div className="rounded-lg border border-line bg-bg-1 px-3 py-2 flex items-center gap-2 text-[11px] text-dimmer">
        <span>Showing {total === 0 ? 0 : skip + 1}–{pageEnd} of {total}</span>
        <div className="ml-auto flex items-center gap-1">
          <Button variant="ghost" size="sm" disabled={skip === 0} onClick={() => setSkip(Math.max(0, skip - PAGE_SIZE))}
            className="h-6 text-[11px]" data-testid="paper-prev-page">
            Prev
          </Button>
          <Button variant="ghost" size="sm" disabled={pageEnd >= total} onClick={() => setSkip(skip + PAGE_SIZE)}
            className="h-6 text-[11px]" data-testid="paper-next-page">
            Next
          </Button>
        </div>
      </div>

      <div className="text-[10px] text-dimmer px-1">
        Paper trades are simulated on real streamed prices — no broker orders. Entries/exits are option premium (₹), never the spot index; lot size comes from the contract. OPEN trades are never deletable. Times are IST. The P&amp;L calendar covers up to {STATS_CAP} trades for the current filter.
      </div>
    </div>
  );
}
