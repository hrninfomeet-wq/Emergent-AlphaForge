import { useEffect, useState, useCallback } from "react";
import { api } from "@/lib/api";
import { Skeleton } from "@/components/ui/skeleton";
import { Badge } from "@/components/ui/badge";
import {
  DropdownMenu, DropdownMenuTrigger, DropdownMenuContent, DropdownMenuItem,
  DropdownMenuSeparator,
} from "@/components/ui/dropdown-menu";
import { toast } from "sonner";
import AuthoringWizard from "@/components/strategy/AuthoringWizard";
import {
  Library, CheckCircle2, AlertCircle, TrendingUp, MoreVertical,
  PauseCircle, PlayCircle, Trash2, Search, Plus,
} from "lucide-react";

const FILTERS = ["All", "Built-in", "Custom", "Failed", "Retired"];

export default function StrategyLibrary() {
  const [strategies, setStrategies] = useState([]);
  const [metricsByStrategy, setMetricsByStrategy] = useState({});
  const [loading, setLoading] = useState(true);
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState("All");
  const [authorOpen, setAuthorOpen] = useState(false);

  const load = useCallback(async () => {
    try {
      const strategyData = await api.listStrategies();
      setStrategies(strategyData.items || []);
      try {
        const metricData = await api.listDeploymentMetrics({ include_ineligible: 1 });
        const grouped = {};
        for (const item of metricData.items || []) {
          if (!(item.closed_trade_count > 0)) continue;
          const key = item.strategy_id || "";
          grouped[key] = [...(grouped[key] || []), item];
        }
        setMetricsByStrategy(grouped);
      } catch {
        setMetricsByStrategy({});
      }
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  async function onRetire(s) {
    try {
      const res = await api.retireStrategy(s.id);
      toast.success(`Retired ${s.name}${res.squared_off_count ? ` · squared off ${res.squared_off_count} trade(s)` : ""}.`);
      load();
    } catch (e) {
      toast.error(`Retire failed: ${e.response?.data?.detail || e.message}`);
    }
  }
  async function onUnretire(s) {
    try {
      await api.unretireStrategy(s.id);
      toast.success(`Un-retired ${s.name}.`);
      load();
    } catch (e) {
      toast.error(`Un-retire failed: ${e.response?.data?.detail || e.message}`);
    }
  }
  async function onDelete(s) {
    // Blast radius first: deleting orphans every preset/backtest/optimizer job
    // that references the strategy (no re-link path) — the confirm must say so.
    let refsMsg = "";
    try {
      const r = await api.strategyReferences(s.id);
      const refs = r.references || {};
      if (r.orphaned_total > 0) {
        refsMsg =
          `\n\nThis orphans ${r.orphaned_total} saved artifact(s):\n` +
          `  · ${refs.presets ?? 0} preset(s)\n` +
          `  · ${refs.backtest_runs ?? 0} backtest run(s)\n` +
          `  · ${refs.optimization_jobs ?? 0} optimizer job(s)\n` +
          `Their stored results remain readable but can never be re-run or re-linked.`;
      }
    } catch { /* backend re-checks and blocks on its own if this failed */ }
    if (!window.confirm(`Delete the file for "${s.name}" permanently? This cannot be undone.${refsMsg}`)) return;
    try {
      await api.deleteStrategy(s.id, true);
      toast.success(`Deleted ${s.name}.`);
      load();
    } catch (e) {
      const detail = e.response?.data?.detail;
      toast.error(`Delete failed: ${(typeof detail === "string" ? detail : detail?.message) || e.message}`);
    }
  }

  if (loading) {
    return (
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
        {[1, 2, 3, 4].map((i) => <Skeleton key={i} className="h-40 bg-bg-1" />)}
      </div>
    );
  }

  const q = query.trim().toLowerCase();
  const matchesQuery = (s) =>
    !q || (s.name || "").toLowerCase().includes(q) || (s.id || "").toLowerCase().includes(q);
  const matchesFilter = (s) => {
    if (filter === "Retired") return s.is_retired;
    if (s.is_retired) return false;
    if (filter === "Built-in") return s.origin === "builtin";
    if (filter === "Custom") return s.origin === "custom";
    if (filter === "Failed") return s.is_loaded === false;
    return true;
  };

  const visible = strategies.filter(matchesQuery).filter(matchesFilter);
  const retiredVisible = strategies.filter(matchesQuery).filter((s) => s.is_retired);
  const activeCount = strategies.filter((s) => !s.is_retired).length;
  const retiredCount = strategies.filter((s) => s.is_retired).length;

  return (
    <div className="space-y-3" data-testid="strategy-library-page">
      <div className="flex items-center gap-2 flex-wrap">
        <div className="text-sm text-dim">{activeCount} active · {retiredCount} retired</div>
        <div className="flex-1" />
        <button
          onClick={() => setAuthorOpen(true)}
          className="text-xs font-semibold px-3 py-1.5 rounded-md bg-info/15 border border-info/50 text-foreground flex items-center gap-1"
          data-testid="new-strategy-button"
        >
          <Plus className="w-3.5 h-3.5" /> New strategy
        </button>
        <div className="relative">
          <Search className="w-3.5 h-3.5 text-dimmer absolute left-2 top-1/2 -translate-y-1/2" />
          <input
            value={query} onChange={(e) => setQuery(e.target.value)} placeholder="search…"
            className="text-xs pl-7 pr-2 py-1.5 rounded-md bg-bg-2 border border-line text-foreground focus:outline-none focus:ring-1 focus:ring-info"
            data-testid="strategy-search"
          />
        </div>
      </div>

      <div className="flex gap-1.5 flex-wrap">
        {FILTERS.map((f) => (
          <button
            key={f} onClick={() => setFilter(f)}
            className={`text-[11px] px-2.5 py-1 rounded-full border ${
              filter === f ? "bg-info/15 border-info/50 text-foreground" : "bg-bg-1 border-line text-dim"
            }`}
            data-testid={`strategy-filter-${f}`}
          >{f}</button>
        ))}
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
        {visible.map((s) => (
          <StrategyCard key={s.id} s={s} metrics={metricsByStrategy[s.id] || []}
            onRetire={onRetire} onUnretire={onUnretire} onDelete={onDelete} />
        ))}
      </div>

      {filter !== "Retired" && retiredVisible.length > 0 && (
        <details className="rounded-lg border border-dashed border-line bg-bg-1/50 p-3">
          <summary className="text-xs text-dim cursor-pointer">Retired ({retiredVisible.length}) — hidden from pickers, deployments paused</summary>
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-3 mt-3">
            {retiredVisible.map((s) => (
              <StrategyCard key={s.id} s={s} metrics={metricsByStrategy[s.id] || []}
                onRetire={onRetire} onUnretire={onUnretire} onDelete={onDelete} />
            ))}
          </div>
        </details>
      )}

      <AuthoringWizard open={authorOpen} onOpenChange={setAuthorOpen} onInstalled={load} />
    </div>
  );
}

function StrategyCard({ s, metrics, onRetire, onUnretire, onDelete }) {
  const loaded = s.is_loaded !== false;
  const isCustom = s.origin === "custom";
  return (
    <div className={`rounded-lg border border-line bg-bg-1 p-3 ${s.is_retired ? "opacity-60" : ""}`} data-testid={`strategy-card-${s.id}`}>
      <div className="flex items-start gap-3 mb-2">
        <div className="w-9 h-9 rounded-md bg-bg-3 border border-line-strong flex items-center justify-center shrink-0">
          <Library className="w-4 h-4 text-info" />
        </div>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <div className="text-sm font-semibold">{s.name}</div>
            <span className="font-mono text-[10px] text-dimmer">v{s.version}</span>
            {loaded ? (
              <Badge className="bg-emerald-950 text-emerald-200 border-emerald-900"><CheckCircle2 className="w-3 h-3 mr-1" />loaded</Badge>
            ) : (
              <Badge className="bg-rose-950 text-rose-200 border-rose-900"><AlertCircle className="w-3 h-3 mr-1" />failed</Badge>
            )}
            {isCustom ? (
              <Badge className="bg-sky-950 text-sky-200 border-sky-900">custom</Badge>
            ) : (
              <Badge className="bg-bg-3 text-dim border-line">built-in</Badge>
            )}
            {s.is_retired && <Badge className="bg-amber-950 text-warning border-amber-900">retired</Badge>}
          </div>
          <div className="text-[11px] font-mono text-dimmer mt-0.5">{s.id}</div>
        </div>
        <StrategyMenu s={s} isCustom={isCustom} onRetire={onRetire} onUnretire={onUnretire} onDelete={onDelete} />
      </div>
      <div className="text-xs text-dim leading-snug mb-3">{s.description}</div>
      <ForwardMetricsBlock metrics={metrics} />
      {!loaded && s.error && (
        <div className="text-[11px] text-rose-300 bg-rose-950/50 border border-rose-900 rounded-md p-2 mb-2 font-mono">
          {s.error}
        </div>
      )}
      <div className="grid grid-cols-3 gap-2 mb-3">
        <Pill label="Instruments" items={s.supported_instruments} />
        <Pill label="Modes" items={s.supported_modes} />
        <Pill label="Timeframes" items={s.supported_timeframes} />
      </div>
      {s.parameter_schema && Object.keys(s.parameter_schema).length > 0 && (
        <div>
          <div className="text-[10px] uppercase tracking-wider text-dimmer mb-1">Parameters ({Object.keys(s.parameter_schema).length})</div>
          <div className="flex flex-wrap gap-1">
            {Object.entries(s.parameter_schema).map(([k, def]) => (
              <span key={k} className="text-[10px] font-mono px-1.5 py-0.5 rounded bg-bg-2 border border-line text-dim">
                {k}=<span className="text-foreground">{String(def.default)}</span>
              </span>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function StrategyMenu({ s, isCustom, onRetire, onUnretire, onDelete }) {
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <button className="p-1 rounded hover:bg-bg-2 text-dimmer shrink-0" data-testid={`strategy-menu-${s.id}`} aria-label="Strategy actions">
          <MoreVertical className="w-4 h-4" />
        </button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-48">
        {s.is_retired ? (
          <DropdownMenuItem onClick={() => onUnretire(s)}><PlayCircle className="w-3.5 h-3.5 mr-2" />Un-retire</DropdownMenuItem>
        ) : (
          <DropdownMenuItem onClick={() => onRetire(s)}><PauseCircle className="w-3.5 h-3.5 mr-2" />Retire</DropdownMenuItem>
        )}
        <DropdownMenuSeparator />
        <DropdownMenuItem
          disabled={!isCustom}
          onClick={() => isCustom && onDelete(s)}
          className={isCustom ? "text-rose-300" : "opacity-40"}
          title={isCustom ? "" : "Built-in strategies can only be retired"}
        >
          <Trash2 className="w-3.5 h-3.5 mr-2" />Delete file
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

function ForwardMetricsBlock({ metrics }) {
  const visible = (metrics || []).slice(0, 3);
  if (!visible.length) return null;
  return (
    <div className="border-t border-line pt-2 mb-3" data-testid="forward-metrics-block">
      <div className="flex items-center gap-1.5 text-[10px] uppercase tracking-wider text-dimmer mb-2">
        <TrendingUp className="w-3 h-3 text-emerald-400" />
        Forward
      </div>
      <div className="space-y-2">
        {visible.map((item) => {
          const minSessions = item.library_gate?.min_complete_sessions || 10;
          const sessions = item.promotion_session_completeness?.complete_session_count
            ?? item.session_completeness?.complete_session_count
            ?? 0;
          const lowSample = sessions < minSessions;
          const validation = item.forward_validation || {};
          const policy = validation.policy || {};
          const promotionReady = Boolean(validation.promotion_allowed);
          const validationLabel = promotionReady ? "promotion ready"
            : validation.phase === "plumbing_ready" ? "plumbing ready" : "collecting";
          return (
            <div key={item.deployment_id} className="grid grid-cols-2 sm:grid-cols-4 gap-2 text-[11px]">
              <div className="min-w-0">
                <div className="font-medium truncate flex items-center gap-1.5">
                  {item.deployment_name || item.deployment_id}
                  {lowSample && (
                    <span
                      className="text-[9px] uppercase tracking-wide px-1 py-px rounded bg-amber-950 text-warning border border-amber-900 shrink-0"
                      title={`Only ${sessions} of ${minSessions} complete forward sessions — treat these numbers as preliminary, not evidence.`}
                    >
                      low sample
                    </span>
                  )}
                  <span
                    className={`text-[9px] uppercase tracking-wide px-1 py-px rounded border shrink-0 ${promotionReady ? "bg-emerald-950 text-emerald-300 border-emerald-900" : "bg-slate-950 text-dimmer border-line"}`}
                    title={promotionReady ? "All pre-registered forward gates passed." : `Failed: ${(validation.failed_checks || []).join(", ") || "evidence not available"}`}
                  >
                    {validationLabel}
                  </span>
                </div>
                <div className="text-dimmer font-mono">
                  {sessions}/{policy.min_forward_sessions || 60} sessions · {item.trade_count || 0}/{policy.min_forward_trades || 120} trades
                  {lowSample ? ` · visibility ${minSessions}` : ""}
                </div>
              </div>
              <Metric label="WR" value={fmtPct(item.win_rate)} />
              <Metric label="Avg PnL" value={fmtSigned(item.avg_pnl)} tone={item.avg_pnl} />
              <Metric label="PF" value={fmtNum(item.profit_factor)} />
            </div>
          );
        })}
      </div>
    </div>
  );
}

function Metric({ label, value, tone }) {
  const toneClass = Number(tone || 0) > 0 ? "text-emerald-300" : Number(tone || 0) < 0 ? "text-rose-300" : "text-foreground";
  return (
    <div>
      <div className="text-[10px] uppercase tracking-wider text-dimmer">{label}</div>
      <div className={`font-mono ${toneClass}`}>{value}</div>
    </div>
  );
}

function fmtNum(value, digits = 2) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "n/a";
  return Number(value).toFixed(digits);
}

function fmtPct(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "n/a";
  return `${Number(value).toFixed(1)}%`;
}

function fmtSigned(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "n/a";
  const n = Number(value);
  return `${n > 0 ? "+" : ""}${n.toFixed(0)}`;
}

function Pill({ label, items }) {
  return (
    <div>
      <div className="text-[10px] uppercase tracking-wider text-dimmer mb-1">{label}</div>
      <div className="flex flex-wrap gap-1">
        {(items || []).map((i) => (
          <span key={i} className="text-[10px] font-mono px-1.5 py-0.5 rounded bg-bg-2 border border-line text-dim">{i}</span>
        ))}
      </div>
    </div>
  );
}
