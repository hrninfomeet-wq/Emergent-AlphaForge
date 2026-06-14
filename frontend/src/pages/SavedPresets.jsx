import { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  Bookmark, Rocket, FlaskConical, Gauge, Pencil, Copy, Trash2,
  RefreshCw, Search, ChevronDown, ChevronRight, Cog, AlertTriangle,
  ShieldCheck, Columns, X, Loader2,
} from "lucide-react";
import { toast } from "sonner";
import { api } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

/**
 * Saved Presets (route /presets) — one place for every preset saved from the
 * Backtest Lab and the Optimizer, grouped by source. Each preset is the full
 * deployable artifact (strategy + params + option-execution policy); this page
 * surfaces what each will deploy, whether it is already live, a validation
 * badge (honest-WFO + option-rupee OOS), and one-click Deploy / Open-in-Lab /
 * Rename / Duplicate / Delete, plus multi-select Compare and bulk delete.
 */

const INSTRUMENTS = ["NIFTY", "BANKNIFTY", "SENSEX"];

const presetSource = (p) => {
  const c = p.config || {};
  if (c.source === "optimizer" || c.source === "backtest") return c.source;
  return (c.source_optimization_job || c.source_job_kind || c.optimization_method || c.objective != null)
    ? "optimizer" : "backtest";
};

const execSummary = (ex) => {
  if (!ex) return null;
  const out = [(ex.moneyness || "atm").toUpperCase()];
  if (ex.exit_mode === "option_levels") {
    const tgt = ex.option_target_pct != null ? `${ex.option_target_pct}%`
      : ex.option_target_pts != null ? `${ex.option_target_pts}pt` : "—";
    const sl = ex.option_stop_pct != null ? `${ex.option_stop_pct}%`
      : ex.option_stop_pts != null ? `${ex.option_stop_pts}pt` : "—";
    out.push(`premium Tgt ${tgt} / SL ${sl}`);
  } else {
    out.push("spot-mirror exit");
  }
  out.push(Array.isArray(ex.dte_filter) && ex.dte_filter.length ? `DTE ${ex.dte_filter.join(",")}` : "DTE all");
  out.push(`${ex.lots || 1} lot${(ex.lots || 1) > 1 ? "s" : ""}`);
  if (ex.cost_config?.enabled) out.push(`costs ${ex.cost_config.spread_pct_of_premium ?? 0}%`);
  return out.join(" · ");
};

// Derive a validation verdict from a /deployments/readiness response: honest-WFO
// efficiency + option-rupee OOS, both required to be params-matched + positive
// for a "strong" badge. Surfaces whether the edge is proven out of sample.
const validationVerdict = (rd) => {
  if (!rd) return null;
  const wfo = rd.wfo;
  const oe = rd.option_evidence;
  const wfoStrong = wfo && wfo.efficiency != null && Number(wfo.efficiency) >= 0.5 && wfo.params_match;
  const optGood = oe && oe.params_match && Number(oe.net_pnl_value || 0) > 0;
  let level, label;
  if (wfoStrong && optGood) { level = "strong"; label = "Validated"; }
  else if (wfoStrong || optGood) { level = "partial"; label = "Partly validated"; }
  else if (wfo || oe) { level = "weak"; label = "Weak / params differ"; }
  else { level = "none"; label = "Unvalidated"; }
  const tip = [
    wfo ? `WFO: eff ${wfo.efficiency}, ${wfo.positive_windows}/${wfo.windows} OOS+${wfo.option_oos_net != null ? `, opt OOS ₹${Math.round(wfo.option_oos_net).toLocaleString("en-IN")}` : ""}${wfo.params_match ? "" : " (params differ)"}` : "no honest walk-forward",
    oe ? `option ${oe.kind === "rerank" ? "re-rank" : "backtest"}: net ₹${Math.round(oe.net_pnl_value || 0).toLocaleString("en-IN")}${oe.params_match ? "" : " (params differ)"}` : "no option-rupee evidence",
    rd.n_trials ? `best of ${rd.n_trials} trials` : null,
  ].filter(Boolean).join(" · ");
  return { level, label, tip };
};

const VAL_CLS = {
  strong: "border-emerald-500/40 text-emerald-300",
  partial: "border-sky-500/40 text-sky-300",
  weak: "border-amber-500/40 text-amber-300",
  none: "border-line text-dimmer",
};

const relTime = (iso) => {
  if (!iso) return "—";
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return "—";
  const s = Math.max(0, (Date.now() - t) / 1000);
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  if (s < 86400 * 30) return `${Math.floor(s / 86400)}d ago`;
  return new Date(t).toISOString().slice(0, 10);
};

const SORTS = [
  { id: "saved", label: "Newest" },
  { id: "name", label: "Name" },
  { id: "strategy", label: "Strategy" },
];

export default function SavedPresets() {
  const navigate = useNavigate();
  const [presets, setPresets] = useState([]);
  const [deployed, setDeployed] = useState({});
  const [validation, setValidation] = useState({});
  const [validating, setValidating] = useState(false);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [search, setSearch] = useState("");
  const [srcFilter, setSrcFilter] = useState("all");
  const [instFilter, setInstFilter] = useState("all");
  const [deployableOnly, setDeployableOnly] = useState(false);
  const [deployedOnly, setDeployedOnly] = useState(false);
  const [sortBy, setSortBy] = useState("saved");
  const [collapsed, setCollapsed] = useState({});
  const [expanded, setExpanded] = useState(() => new Set());
  const [selected, setSelected] = useState(() => new Set());
  const [compareOpen, setCompareOpen] = useState(false);

  const refresh = useCallback(async () => {
    try {
      const [pl, ov] = await Promise.all([
        api.listPresets(),
        api.deploymentsOverview().catch(() => ({ items: [] })),
      ]);
      setPresets(pl.items || []);
      const map = {};
      for (const it of ov.items || []) {
        const d = it.deployment || {};
        if (d.source_type === "preset" && d.source_id) {
          const e = map[d.source_id] || { count: 0, statuses: [] };
          e.count += 1;
          e.statuses.push(d.status);
          map[d.source_id] = e;
        }
      }
      setDeployed(map);
    } catch (e) {
      toast.error(`Load failed: ${e.response?.data?.detail || e.message}`);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { refresh(); }, [refresh]);

  // Validation badges: fetch the readiness evidence (honest WFO + option-rupee
  // OOS) per preset in the background once the list is in. Non-blocking — the
  // list renders immediately and badges fill in as each resolves.
  useEffect(() => {
    if (!presets.length) return undefined;
    let cancelled = false;
    setValidating(true);
    (async () => {
      const results = await Promise.allSettled(
        presets.map((p) => api.deploymentReadiness("preset", p.name).then((rd) => [p.name, rd]))
      );
      if (cancelled) return;
      const map = {};
      for (const r of results) if (r.status === "fulfilled" && r.value) map[r.value[0]] = r.value[1];
      setValidation(map);
      setValidating(false);
    })();
    return () => { cancelled = true; };
  }, [presets]);

  const filtered = useMemo(() => {
    let list = presets.map((p) => ({
      ...p,
      _source: presetSource(p),
      _ex: p.config?.execution || null,
      _dep: deployed[p.name] || null,
      _val: validationVerdict(validation[p.name]),
    }));
    const q = search.trim().toLowerCase();
    if (q) list = list.filter((p) =>
      (p.name || "").toLowerCase().includes(q) || (p.config?.strategy_id || "").toLowerCase().includes(q));
    if (srcFilter !== "all") list = list.filter((p) => p._source === srcFilter);
    if (instFilter !== "all") list = list.filter((p) => (p.config?.instrument || "").toUpperCase() === instFilter);
    if (deployableOnly) list = list.filter((p) => p._ex);
    if (deployedOnly) list = list.filter((p) => p._dep);
    list.sort((a, b) => {
      if (sortBy === "name") return (a.name || "").localeCompare(b.name || "");
      if (sortBy === "strategy") return (a.config?.strategy_id || "").localeCompare(b.config?.strategy_id || "");
      return (b.saved_at || "").localeCompare(a.saved_at || "");
    });
    return list;
  }, [presets, deployed, validation, search, srcFilter, instFilter, deployableOnly, deployedOnly, sortBy]);

  const groups = useMemo(() => ({
    optimizer: filtered.filter((p) => p._source === "optimizer"),
    backtest: filtered.filter((p) => p._source === "backtest"),
  }), [filtered]);

  const stats = useMemo(() => ({
    total: presets.length,
    deployable: presets.filter((p) => p.config?.execution).length,
    deployed: Object.keys(deployed).length,
  }), [presets, deployed]);

  const compareItems = useMemo(
    () => presets.filter((p) => selected.has(p.name)).slice(0, 2),
    [presets, selected],
  );

  // --- actions ---
  const deploy = (p) => navigate(`/live?preset=${encodeURIComponent(p.name)}`);
  const openInLab = (p) => navigate(`/backtest?preset=${encodeURIComponent(p.name)}`);

  const act = async (fn, okMsg) => {
    setBusy(true);
    try { await fn(); if (okMsg) toast.success(okMsg); await refresh(); }
    catch (e) { toast.error(e.response?.data?.detail || e.message); }
    finally { setBusy(false); }
  };

  const rename = (p) => {
    const raw = window.prompt(`Rename preset "${p.name}" to:`, p.name);
    if (raw == null) return;
    const nn = raw.trim();
    if (!nn || nn === p.name) return;
    if (presets.some((x) => x.name === nn)) { toast.error(`A preset named "${nn}" already exists.`); return; }
    act(() => api.renamePreset(p.name, nn), `Renamed to "${nn}"`);
  };

  const duplicate = (p) => {
    const raw = window.prompt(`Duplicate "${p.name}" as:`, `${p.name} (copy)`);
    if (raw == null) return;
    const nn = raw.trim();
    if (!nn) return;
    if (presets.some((x) => x.name === nn) && !window.confirm(`"${nn}" already exists. Overwrite it?`)) return;
    act(() => api.savePreset(nn, { ...(p.config || {}) }), `Duplicated as "${nn}"`);
  };

  const remove = (p) => {
    const msg = p._dep
      ? `"${p.name}" is used by ${p._dep.count} live deployment${p._dep.count > 1 ? "s" : ""}.\n\nDeleting the preset does NOT stop those deployments, but their readiness/quality lookups (which resolve by preset name) will break. Delete anyway?`
      : `Delete preset "${p.name}"? This cannot be undone.`;
    if (!window.confirm(msg)) return;
    act(() => api.deletePreset(p.name), `Deleted "${p.name}"`);
  };

  const toggleSelect = (name) => setSelected((prev) => {
    const next = new Set(prev);
    next.has(name) ? next.delete(name) : next.add(name);
    return next;
  });

  const bulkDelete = () => {
    const names = [...selected];
    if (!names.length) return;
    const deployedNames = names.filter((n) => deployed[n]);
    const warn = `Delete ${names.length} preset${names.length > 1 ? "s" : ""}?`
      + (deployedNames.length ? `\n\n${deployedNames.length} of them back a live deployment — those lookups will break.` : "")
      + "\nThis cannot be undone.";
    if (!window.confirm(warn)) return;
    setBusy(true);
    (async () => {
      let ok = 0;
      for (const n of names) { try { await api.deletePreset(n); ok += 1; } catch { /* keep going */ } }
      toast.success(`Deleted ${ok}/${names.length} preset${names.length > 1 ? "s" : ""}`);
      setSelected(new Set());
      await refresh();
      setBusy(false);
    })();
  };

  const toggleExpand = (name) => setExpanded((prev) => {
    const next = new Set(prev);
    next.has(name) ? next.delete(name) : next.add(name);
    return next;
  });

  if (loading) {
    return <div className="h-96 rounded-lg border border-line bg-bg-1 animate-pulse" data-testid="saved-presets-page" />;
  }

  const FilterChip = ({ active, onClick, children }) => (
    <button type="button" onClick={onClick}
      className={`px-2 py-1 rounded text-[11px] font-mono border ${active ? "bg-info text-bg-0 border-info" : "bg-bg-2 text-dim border-line hover:text-foreground"}`}>
      {children}
    </button>
  );

  const groupProps = {
    collapsed, setCollapsed, expanded, toggleExpand, busy,
    selected, onToggleSelect: toggleSelect,
    onDeploy: deploy, onOpenLab: openInLab, onRename: rename, onDuplicate: duplicate, onRemove: remove,
  };

  return (
    <div className="space-y-3" data-testid="saved-presets-page">
      {/* Header */}
      <div className="rounded-lg border border-line bg-bg-1 px-3 py-2 flex items-center gap-4 flex-wrap">
        <div className="flex items-center gap-2">
          <Bookmark className="w-4 h-4 text-info" />
          <div className="text-xs font-semibold uppercase tracking-wider text-dim">Saved Presets</div>
        </div>
        <HeaderStat label="Total" value={stats.total} />
        <HeaderStat label="Deployable" value={stats.deployable} title="Presets that carry an option-execution policy" />
        <HeaderStat label="Deployed" value={stats.deployed} title="Presets currently backing a live deployment" />
        {validating && (
          <span className="flex items-center gap-1 text-[10px] text-dimmer"><Loader2 className="w-3 h-3 animate-spin" /> validating…</span>
        )}
        <div className="ml-auto flex items-center gap-2">
          <div className="relative">
            <Search className="w-3.5 h-3.5 absolute left-2 top-1/2 -translate-y-1/2 text-dimmer" />
            <Input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Search name / strategy"
              className="h-8 w-52 bg-bg-2 border-line pl-7 text-xs" data-testid="preset-search" />
          </div>
          <Button size="sm" variant="ghost" className="h-8 text-xs" onClick={refresh} data-testid="presets-refresh">
            <RefreshCw className="w-3 h-3 mr-1" /> Refresh
          </Button>
        </div>
      </div>

      {/* Filters */}
      <div className="rounded-lg border border-line bg-bg-1 px-3 py-2 flex items-center gap-3 flex-wrap text-[11px]">
        <div className="flex items-center gap-1">
          <span className="text-dimmer mr-1">Source</span>
          {["all", "optimizer", "backtest"].map((s) => (
            <FilterChip key={s} active={srcFilter === s} onClick={() => setSrcFilter(s)}>{s === "all" ? "All" : s}</FilterChip>
          ))}
        </div>
        <div className="flex items-center gap-1">
          <span className="text-dimmer mr-1">Instrument</span>
          <FilterChip active={instFilter === "all"} onClick={() => setInstFilter("all")}>All</FilterChip>
          {INSTRUMENTS.map((i) => (
            <FilterChip key={i} active={instFilter === i} onClick={() => setInstFilter(i)}>{i}</FilterChip>
          ))}
        </div>
        <label className="flex items-center gap-1.5 text-dim cursor-pointer">
          <input type="checkbox" checked={deployableOnly} onChange={(e) => setDeployableOnly(e.target.checked)} className="h-3.5 w-3.5 rounded border-line" />
          deployable only
        </label>
        <label className="flex items-center gap-1.5 text-dim cursor-pointer">
          <input type="checkbox" checked={deployedOnly} onChange={(e) => setDeployedOnly(e.target.checked)} className="h-3.5 w-3.5 rounded border-line" />
          deployed only
        </label>
        <div className="ml-auto flex items-center gap-1">
          <span className="text-dimmer mr-1">Sort</span>
          {SORTS.map((s) => (
            <FilterChip key={s.id} active={sortBy === s.id} onClick={() => setSortBy(s.id)}>{s.label}</FilterChip>
          ))}
        </div>
      </div>

      {/* Selection toolbar */}
      {selected.size > 0 && (
        <div className="rounded-lg border border-info/40 bg-info/5 px-3 py-2 flex items-center gap-2 text-xs" data-testid="presets-selection-bar">
          <span className="text-dim font-mono">{selected.size} selected</span>
          <Button size="sm" variant="ghost" className="h-7 text-xs" disabled={compareItems.length !== 2}
            onClick={() => setCompareOpen(true)} data-testid="presets-compare"
            title={compareItems.length === 2 ? "Compare the two selected presets" : "Select exactly two to compare"}>
            <Columns className="w-3 h-3 mr-1" /> Compare
          </Button>
          <Button size="sm" variant="ghost" className="h-7 text-xs text-rose-300 hover:text-rose-200" disabled={busy}
            onClick={bulkDelete} data-testid="presets-bulk-delete">
            <Trash2 className="w-3 h-3 mr-1" /> Delete selected
          </Button>
          <Button size="sm" variant="ghost" className="h-7 text-xs ml-auto text-dimmer" onClick={() => setSelected(new Set())}>
            Clear
          </Button>
        </div>
      )}

      {presets.length === 0 ? (
        <div className="rounded-lg border border-line bg-bg-1 p-8 text-center text-dimmer text-sm">
          No presets yet. Save one from a <b>Backtest Lab</b> result ("Save as preset") or the <b>Optimizer</b> ("Apply as preset") to see it here.
        </div>
      ) : (
        <>
          <PresetGroup id="optimizer" title="From Optimizer" icon={Gauge} accent="text-info" items={groups.optimizer} {...groupProps} />
          <PresetGroup id="backtest" title="From Backtest Lab" icon={FlaskConical} accent="text-emerald-300" items={groups.backtest} {...groupProps} />
          {groups.optimizer.length === 0 && groups.backtest.length === 0 && (
            <div className="rounded-lg border border-line bg-bg-1 p-6 text-center text-dimmer text-sm">No presets match these filters.</div>
          )}
        </>
      )}

      {compareOpen && compareItems.length === 2 && (
        <CompareDialog a={compareItems[0]} b={compareItems[1]} onClose={() => setCompareOpen(false)} />
      )}
    </div>
  );
}

function HeaderStat({ label, value, title }) {
  return (
    <div className="flex items-baseline gap-1.5" title={title}>
      <span className="text-[10px] uppercase tracking-wider text-dimmer">{label}</span>
      <span className="text-sm font-mono text-foreground">{value}</span>
    </div>
  );
}

function PresetGroup({ id, title, icon: Icon, accent, items, collapsed, setCollapsed, expanded, toggleExpand, busy, selected, onToggleSelect, onDeploy, onOpenLab, onRename, onDuplicate, onRemove }) {
  const isCollapsed = !!collapsed[id];
  return (
    <div data-testid={`preset-group-${id}`}>
      <button type="button"
        onClick={() => setCollapsed((c) => ({ ...c, [id]: !c[id] }))}
        className="w-full flex items-center gap-2 px-1 py-1.5 text-left">
        {isCollapsed ? <ChevronRight className="w-3.5 h-3.5 text-dimmer" /> : <ChevronDown className="w-3.5 h-3.5 text-dimmer" />}
        <Icon className={`w-4 h-4 ${accent}`} />
        <span className="text-xs font-semibold uppercase tracking-wider text-dim">{title}</span>
        <span className="text-[11px] text-dimmer">{items.length}</span>
      </button>
      {!isCollapsed && (
        items.length === 0 ? (
          <div className="text-[11px] text-dimmer px-6 py-2">None.</div>
        ) : (
          <div className="grid grid-cols-1 xl:grid-cols-2 gap-2">
            {items.map((p) => (
              <PresetCard key={p.name} p={p} source={id} expanded={expanded.has(p.name)} onToggle={() => toggleExpand(p.name)}
                selected={selected.has(p.name)} onToggleSelect={() => onToggleSelect(p.name)} busy={busy}
                onDeploy={onDeploy} onOpenLab={onOpenLab} onRename={onRename} onDuplicate={onDuplicate} onRemove={onRemove} />
            ))}
          </div>
        )
      )}
    </div>
  );
}

function PresetCard({ p, source, expanded, onToggle, selected, onToggleSelect, busy, onDeploy, onOpenLab, onRename, onDuplicate, onRemove }) {
  const c = p.config || {};
  const summary = execSummary(p._ex);
  const params = c.params || {};
  const paramCount = Object.keys(params).length;
  const val = p._val;
  return (
    <div className={`rounded-lg border bg-bg-1 p-3 space-y-2 ${selected ? "border-info" : "border-line"}`} data-testid="preset-card">
      <div className="flex items-start gap-2">
        <input type="checkbox" checked={selected} onChange={onToggleSelect} data-testid="preset-select"
          className="mt-1 h-3.5 w-3.5 rounded border-line shrink-0" title="Select for compare / bulk delete" />
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="text-sm font-semibold truncate" title={p.name}>{p.name}</span>
            <span className={`text-[9px] px-1.5 py-0.5 rounded border font-mono ${source === "optimizer" ? "border-info/40 text-info" : "border-emerald-500/40 text-emerald-300"}`}>
              {source === "optimizer" ? "OPTIMIZER" : "BACKTEST"}
            </span>
            {val && (
              <span className={`text-[9px] px-1.5 py-0.5 rounded border font-mono inline-flex items-center gap-1 ${VAL_CLS[val.level]}`}
                data-testid="preset-validation" title={val.tip}>
                <ShieldCheck className="w-2.5 h-2.5" /> {val.label}
              </span>
            )}
            {p._dep && (
              <span className="text-[9px] px-1.5 py-0.5 rounded border border-emerald-500/40 text-emerald-300 font-mono" data-testid="preset-deployed-badge"
                title={`Live deployment(s): ${p._dep.statuses.join(", ")}`}>
                ● Deployed{p._dep.count > 1 ? ` ×${p._dep.count}` : ""}
              </span>
            )}
          </div>
          <div className="text-[11px] font-mono text-dimmer truncate">
            {c.strategy_id || "?"} · {(c.instrument || "?").toUpperCase()}
            {source === "optimizer" && (c.objective || c.optimization_method) ? ` · ${c.optimization_method || "?"}/${c.objective || "?"}` : ""}
            {source === "optimizer" && c.source_job_kind === "wfo" ? " · WFO" : source === "optimizer" ? " · single" : ""}
            {" · "}{paramCount} params · saved {relTime(p.saved_at)}
          </div>
        </div>
        <div className="flex items-center gap-1 shrink-0">
          <Button size="sm" className="h-7 text-[11px] bg-info text-bg-0 hover:bg-info/90 font-semibold px-2"
            disabled={busy} onClick={() => onDeploy(p)} data-testid="preset-deploy" title="Deploy this preset to Live Signals">
            <Rocket className="w-3 h-3 mr-1" /> Deploy
          </Button>
          <Button size="sm" variant="ghost" className="h-7 text-[11px] px-2 text-dim" disabled={busy}
            onClick={() => onOpenLab(p)} data-testid="preset-open-lab" title="Open in Backtest Lab (applies params + execution)">
            <FlaskConical className="w-3 h-3" />
          </Button>
          <Button size="sm" variant="ghost" className="h-7 w-7 p-0 text-dim" disabled={busy} onClick={() => onRename(p)} data-testid="preset-rename" title="Rename">
            <Pencil className="w-3 h-3" />
          </Button>
          <Button size="sm" variant="ghost" className="h-7 w-7 p-0 text-dim" disabled={busy} onClick={() => onDuplicate(p)} data-testid="preset-duplicate" title="Duplicate">
            <Copy className="w-3 h-3" />
          </Button>
          <Button size="sm" variant="ghost" className="h-7 w-7 p-0 text-rose-300 hover:text-rose-200" disabled={busy} onClick={() => onRemove(p)} data-testid="preset-delete" title="Delete">
            <Trash2 className="w-3 h-3" />
          </Button>
        </div>
      </div>

      {summary ? (
        <div className="flex items-center gap-1.5 text-[11px] text-sky-300/90" title="The option-execution policy this preset deploys with">
          <Cog className="w-3.5 h-3.5 shrink-0" />
          <span className="truncate">{summary}</span>
        </div>
      ) : (
        <div className="flex items-center gap-1.5 text-[11px] text-amber-400/80" title="No option-execution policy — deploys without option pairing (spot signals only)">
          <AlertTriangle className="w-3.5 h-3.5 shrink-0" />
          <span>spot-only — no option execution policy</span>
        </div>
      )}

      {paramCount > 0 && (
        <div>
          <button type="button" onClick={onToggle} className="text-[10px] text-dimmer hover:text-dim flex items-center gap-1">
            {expanded ? <ChevronDown className="w-3 h-3" /> : <ChevronRight className="w-3 h-3" />} params
          </button>
          {expanded && (
            <div className="mt-1 grid grid-cols-2 gap-x-3 gap-y-0.5 text-[10px] font-mono text-dim">
              {Object.entries(params).map(([k, v]) => (
                <div key={k} className="truncate" title={`${k}: ${v}`}>
                  <span className="text-dimmer">{k}</span> {typeof v === "number" ? (Number.isInteger(v) ? v : v.toFixed(3)) : String(v)}
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// Side-by-side comparison of two presets: every param (union of keys, differences
// highlighted) and the option-execution policy. Helps decide between two saved
// candidates before deploying.
function CompareDialog({ a, b, onClose }) {
  const pa = a.config?.params || {};
  const pb = b.config?.params || {};
  const keys = [...new Set([...Object.keys(pa), ...Object.keys(pb)])].sort();
  const fmt = (v) => v === undefined ? "—" : (typeof v === "number" ? (Number.isInteger(v) ? String(v) : v.toFixed(4)) : String(v));
  const diffCount = keys.filter((k) => fmt(pa[k]) !== fmt(pb[k])).length;
  const Col = ({ p }) => (
    <div className="text-[11px] font-mono text-dim truncate" title={p.name}>
      {p.name}
      <div className="text-[10px] text-dimmer">{p.config?.strategy_id} · {(p.config?.instrument || "").toUpperCase()}</div>
    </div>
  );
  return (
    <div className="fixed inset-0 z-50 bg-black/60 flex items-start justify-center p-4 overflow-y-auto" data-testid="compare-dialog">
      <div className="w-full max-w-2xl rounded-lg border border-line bg-bg-1 mt-8">
        <div className="px-4 py-3 border-b border-line flex items-center gap-2">
          <Columns className="w-4 h-4 text-info" />
          <div className="text-sm font-semibold">Compare presets</div>
          <span className="text-[11px] text-dimmer">{diffCount} param{diffCount === 1 ? "" : "s"} differ</span>
          <button onClick={onClose} className="ml-auto text-dimmer hover:text-foreground" data-testid="compare-close"><X className="w-4 h-4" /></button>
        </div>
        <div className="p-4 space-y-3 text-xs">
          <div className="grid grid-cols-[160px_1fr_1fr] gap-2 items-end">
            <div className="text-[10px] uppercase tracking-wider text-dimmer">Field</div>
            <Col p={a} />
            <Col p={b} />
          </div>
          <div className="rounded-md border border-line divide-y divide-line">
            {keys.map((k) => {
              const differ = fmt(pa[k]) !== fmt(pb[k]);
              return (
                <div key={k} className={`grid grid-cols-[160px_1fr_1fr] gap-2 px-2 py-1 ${differ ? "bg-amber-500/5" : ""}`}>
                  <div className="text-[11px] text-dimmer truncate" title={k}>{k}</div>
                  <div className={`text-[11px] font-mono ${differ ? "text-amber-300" : "text-dim"}`}>{fmt(pa[k])}</div>
                  <div className={`text-[11px] font-mono ${differ ? "text-amber-300" : "text-dim"}`}>{fmt(pb[k])}</div>
                </div>
              );
            })}
          </div>
          <div className="grid grid-cols-[160px_1fr_1fr] gap-2 px-2">
            <div className="text-[11px] text-dimmer">option execution</div>
            <div className="text-[11px] font-mono text-sky-300/90">{execSummary(a.config?.execution) || "spot-only"}</div>
            <div className="text-[11px] font-mono text-sky-300/90">{execSummary(b.config?.execution) || "spot-only"}</div>
          </div>
        </div>
      </div>
    </div>
  );
}
