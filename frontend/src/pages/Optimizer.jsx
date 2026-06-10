import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { toast } from "sonner";
import { api } from "@/lib/api";
import { fmtInt, fmtNum, fmtPct, isoToFull } from "@/lib/fmt";
import { dateToMs, msToDate } from "@/lib/time";
import { exportOptConfig, exportOptJob, exportOptAlternatives } from "@/lib/optExports";
import { Button } from "@/components/ui/button";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { NumberSliderInput } from "@/components/NumberSliderInput";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs";
import {
  Gauge, Play, RefreshCw, Sparkles, Trash2, ChevronDown, ChevronRight,
  Save, Activity, Trophy, StopCircle, Download, FileJson, FileText, FolderOpen,
  ExternalLink, Copy, PauseCircle, PlayCircle,
} from "lucide-react";

const INSTRUMENTS = ["NIFTY", "BANKNIFTY", "SENSEX"];
const OPT_MONEYNESS = ["atm", "otm1", "otm2", "itm1", "itm2"];
const OPT_DTE = [
  { id: "all", name: "All expiries" },
  { id: "dte0", name: "DTE0 (expiry day)" },
  { id: "dte1", name: "DTE1" },
  { id: "dte2", name: "DTE2" },
  { id: "dte3", name: "DTE3" },
];
const METHODS = [
  { id: "bayesian", name: "Bayesian (Optuna TPE)", desc: "Smart, focuses on promising regions. Recommended.", default_trials: 150 },
  { id: "grid", name: "Grid Search", desc: "Deterministic, exhaustive (sampled if space too large).", default_trials: 200 },
  { id: "genetic", name: "Genetic (CMA-ES)", desc: "Multi-objective, good for many params.", default_trials: 200 },
];
const OBJECTIVES = [
  { id: "risk_adjusted", name: "Risk-Adjusted Return (default)", desc: "Sharpe / drawdown — balanced quality" },
  { id: "net_pnl_inr", name: "Maximize Net P&L (₹)", desc: "Net rupee P&L = net points × lot size (enable costs)" },
  { id: "sharpe", name: "Maximize Sharpe Ratio", desc: "Risk-adjusted return per std-dev" },
  { id: "profit_factor", name: "Maximize Profit Factor", desc: "Gross profit / |gross loss|" },
  { id: "total_pnl_pts", name: "Maximize Net P&L (pts)", desc: "Raw profit; ignores drawdown" },
  { id: "win_rate", name: "Maximize Win Rate", desc: "% of trades profitable" },
  { id: "neg_max_dd", name: "Minimize Max Drawdown", desc: "Stable equity curve" },
];

// Persist the Optimization Setup panel config across navigation. Only setup
// fields are stored (never transient run state), so returning to the page
// restores the operator's last configuration instead of resetting to defaults.
const SETUP_KEY = "alphaforge.optimizer.setupConfig";

const DEFAULT_SETUP = {
  instrument: "NIFTY",
  mode: "SCALP",
  strategy_id: "confluence_scalper",
  method: "bayesian",
  objective: "risk_adjusted",
  n_trials: 150,
  costs_enabled: true,
  pretrade_filters: {},
  pretrade_profile: "None",
  param_overrides: {},
  optimize_indicator_periods: false,
  guards_enabled: true,
  min_trades: 10,
  min_direction_pct: 0,
  // Evaluation mode: "spot" (fast, scores index backtest) or "option_rerank"
  // (re-rank top-K by real paired-option net rupee).
  evaluation_mode: "spot",
  rerank_top_k: 50,
  option_moneyness: "atm",
  option_dte_filter: "all",
  option_lots: 1,
  option_exit_mode: "spot_exit",
  option_target_pct: "",
  option_stop_pct: "",
  option_costs_enabled: true,
  option_brokerage_per_order: 0,
  option_spread_pct: 1.0,
  name: "Optimization run",
  start_date: "",
  end_date: "",
};

function loadSetup() {
  try {
    const raw = localStorage.getItem(SETUP_KEY);
    if (!raw) return { ...DEFAULT_SETUP };
    const parsed = JSON.parse(raw);
    if (!parsed || typeof parsed !== "object") return { ...DEFAULT_SETUP };
    // Shallow-merge onto defaults so newly added fields always have a value.
    return { ...DEFAULT_SETUP, ...parsed };
  } catch {
    return { ...DEFAULT_SETUP };
  }
}

// The optimizer scores guard-failing / zero-trade trials with a large negative
// sentinel (~ -1e9). Render that as "—" instead of a meaningless huge number.
const fmtBest = (v) => (v == null || v <= -1e8) ? "—" : Number(v).toFixed(3);

export default function Optimizer() {
  const navigate = useNavigate();
  const [strategies, setStrategies] = useState([]);
  const [presets, setPresets] = useState([]);
  const [profiles, setProfiles] = useState([]);
  const [config, setConfig] = useState(loadSetup);
  const [currentJobId, setCurrentJobId] = useState(null);
  const [currentJob, setCurrentJob] = useState(null);
  const [jobs, setJobs] = useState([]);
  const [showOverrides, setShowOverrides] = useState(false);
  const [pollKey, setPollKey] = useState(0);
  const pollRef = useRef(null);

  // Persist setup config on every change (transient run state lives in separate
  // state and is never written here). Storage failures are swallowed so a full
  // or unavailable localStorage never blocks the page.
  useEffect(() => {
    try {
      localStorage.setItem(SETUP_KEY, JSON.stringify(config));
    } catch { /* ignore quota / privacy-mode errors */ }
  }, [config]);

  useEffect(() => {
    api.listStrategies().then((d) => setStrategies(d.items || []));
    api.listOptJobs(30).then((d) => setJobs(d.items || []));
    api.listPresets().then((d) => setPresets(d.items || []));
    api.listProfiles().then((d) => setProfiles(d.items || []));
  }, []);

  const refreshJobs = () => api.listOptJobs(30).then((d) => setJobs(d.items || []));
  const refreshPresets = () => api.listPresets().then((d) => setPresets(d.items || []));

  const selectedProfile = profiles.find((p) => p.name === config.pretrade_profile);

  const selectedStrategy = strategies.find((s) => s.id === config.strategy_id);
  const numericParams = useMemo(() => {
    if (!selectedStrategy) return [];
    return Object.entries(selectedStrategy.parameter_schema || {}).filter(
      ([k, def]) => def.type === "int" || def.type === "float"
    );
  }, [selectedStrategy]);

  // Poll job progress
  useEffect(() => {
    if (!currentJobId) return;
    let cancelled = false;
    const tick = async () => {
      try {
        const j = await api.getOptJob(currentJobId);
        if (cancelled) return;
        setCurrentJob(j);
        const TERMINAL = ["done", "failed", "cancelled", "paused", "interrupted"];
        if (TERMINAL.includes(j.status)) {
          if (pollRef.current) clearInterval(pollRef.current);
          pollRef.current = null;
          refreshJobs();
          if (j.status === "done") {
            toast.success(`Optimization complete: best ${fmtBest(j.best_value)}`);
          } else if (j.status === "cancelled") {
            toast.info("Optimization stopped. Best result so far was saved.");
          } else if (j.status === "paused") {
            toast.info("Optimization paused — Resume to continue from here.");
          } else if (j.status === "interrupted") {
            toast.warning("Optimization was interrupted — Resume to continue.");
          } else {
            toast.error(`Optimization failed: ${j.error}`);
          }
        }
      } catch (e) {
        // ignore transient
      }
    };
    tick();
    pollRef.current = setInterval(tick, 2000);
    return () => {
      cancelled = true;
      if (pollRef.current) clearInterval(pollRef.current);
      pollRef.current = null;
    };
  }, [currentJobId, pollKey]);

  const start = async () => {
    try {
      const optionRerank = config.evaluation_mode === "option_rerank";
      const optionConfig = optionRerank ? {
        moneyness: config.option_moneyness,
        dte_filter: config.option_dte_filter && config.option_dte_filter !== "all" ? config.option_dte_filter : null,
        lots: Math.max(1, Number(config.option_lots || 1)),
        entry_max_age_sec: 120,
        exit_max_age_sec: 180,
        exit_mode: config.option_exit_mode,
        option_target_pct: config.option_exit_mode === "option_levels" && config.option_target_pct !== "" ? Number(config.option_target_pct) : null,
        option_stop_pct: config.option_exit_mode === "option_levels" && config.option_stop_pct !== "" ? Number(config.option_stop_pct) : null,
        cost_config: config.option_costs_enabled ? {
          enabled: true,
          brokerage_per_order: Number(config.option_brokerage_per_order || 0),
          spread_pct_of_premium: Number(config.option_spread_pct || 0),
        } : null,
      } : null;
      const payload = {
        instrument: config.instrument,
        mode: config.mode,
        strategy_id: config.strategy_id,
        method: config.method,
        objective: config.objective,
        n_trials: config.n_trials,
        costs_enabled: config.costs_enabled,
        pretrade_filters: config.pretrade_profile && config.pretrade_profile !== "None"
          ? (selectedProfile?.settings || {})
          : {},
        pretrade_profile: config.pretrade_profile || "None",
        param_overrides: config.param_overrides,
        optimize_indicator_periods: config.optimize_indicator_periods,
        min_trades: config.guards_enabled ? (Number(config.min_trades) || 0) : 0,
        min_direction_share: config.guards_enabled
          ? Math.max(0, Math.min(50, Number(config.min_direction_pct) || 0)) / 100
          : 0,
        start_ts: dateToMs(config.start_date, false),
        end_ts: dateToMs(config.end_date, true),
        name: config.name,
        evaluation_mode: config.evaluation_mode,
        rerank_top_k: Math.max(1, Math.min(500, Number(config.rerank_top_k) || 50)),
        option_config: optionConfig,
      };
      const res = await api.startOptimization(payload);
      setCurrentJobId(res.job_id);
      setCurrentJob({ id: res.job_id, status: "queued" });
      toast.success("Optimization started");
    } catch (e) {
      toast.error("Failed to start: " + (e.response?.data?.detail || e.message));
    }
  };

  const applyAsPreset = async (jobId) => {
    const name = prompt("Save best params as preset (name):", `${config.strategy_id} optimized ${new Date().toISOString().slice(0, 10)}`);
    if (!name) return;
    try {
      await api.applyOptAsPreset(jobId, name);
      await refreshPresets();
      toast.success(`Saved as preset "${name}" → now available in Backtest Lab`);
    } catch (e) {
      toast.error("Save failed: " + (e.response?.data?.detail || e.message));
    }
  };

  const deletePreset = async (name) => {
    if (!confirm(`Delete preset "${name}"? This cannot be undone.`)) return;
    try {
      await api.deletePreset(name);
      await refreshPresets();
      toast.success(`Deleted preset "${name}"`);
    } catch (e) {
      toast.error("Delete failed: " + (e.response?.data?.detail || e.message));
    }
  };

  const stopJob = async () => {
    if (!currentJobId) return;
    if (!confirm("Stop the current optimization? The best result so far will still be saved.")) return;
    try {
      await api.cancelOptJob(currentJobId);
      toast.info("Cancellation requested — finishing the current trial…");
    } catch (e) {
      toast.error("Cancel failed: " + (e.response?.data?.detail || e.message));
    }
  };

  const pauseJob = async () => {
    if (!currentJobId) return;
    try {
      await api.pauseOptJob(currentJobId);
      toast.info("Pausing — progress is being saved at the current trial…");
    } catch (e) {
      toast.error("Pause failed: " + (e.response?.data?.detail || e.message));
    }
  };

  const resumeJob = async (id) => {
    const jid = id || currentJobId;
    if (!jid) return;
    try {
      await api.resumeOptJob(jid);
      setCurrentJobId(jid);
      setCurrentJob((j) => (j && j.id === jid ? { ...j, status: "running" } : { id: jid, status: "running" }));
      setPollKey((k) => k + 1); // restart polling
      toast.success("Resuming from the last saved stage…");
    } catch (e) {
      toast.error("Resume failed: " + (e.response?.data?.detail || e.message));
    }
  };

  const openBestInLab = (runId) => {
    if (!runId) {
      toast.error("Best backtest run not available yet");
      return;
    }
    navigate(`/backtest?run=${runId}`);
  };

  const removeJob = async (id) => {
    if (!confirm("Delete this optimization job?")) return;
    await api.deleteOptJob(id);
    if (id === currentJobId) {
      setCurrentJobId(null);
      setCurrentJob(null);
    }
    refreshJobs();
    toast.success("Deleted");
  };

  // Repopulate the Setup panel from a past job's stored config so the operator
  // can re-run it with tweaks (the job doc stores the full start payload).
  const cloneJobConfig = (job) => {
    const c = job.config || {};
    const share = Number(c.min_direction_share || 0);
    setConfig((prev) => ({
      ...prev,
      instrument: c.instrument ?? prev.instrument,
      mode: c.mode ?? prev.mode,
      strategy_id: c.strategy_id ?? prev.strategy_id,
      method: c.method ?? prev.method,
      objective: c.objective ?? prev.objective,
      n_trials: c.n_trials ?? prev.n_trials,
      costs_enabled: c.costs_enabled ?? prev.costs_enabled,
      optimize_indicator_periods: !!c.optimize_indicator_periods,
      param_overrides: c.param_overrides || {},
      guards_enabled: (Number(c.min_trades || 0) > 0 || share > 0),
      min_trades: Number(c.min_trades ?? 10),
      min_direction_pct: Math.round(share * 100),
      pretrade_profile: c.pretrade_profile || "None",
      evaluation_mode: c.evaluation_mode || "spot",
      rerank_top_k: c.rerank_top_k ?? 50,
      option_moneyness: c.option_config?.moneyness ?? prev.option_moneyness,
      option_dte_filter: c.option_config?.dte_filter ?? "all",
      option_lots: c.option_config?.lots ?? prev.option_lots,
      option_exit_mode: c.option_config?.exit_mode ?? "spot_exit",
      option_target_pct: c.option_config?.option_target_pct ?? "",
      option_stop_pct: c.option_config?.option_stop_pct ?? "",
      option_costs_enabled: c.option_config?.cost_config?.enabled ?? prev.option_costs_enabled,
      option_brokerage_per_order: c.option_config?.cost_config?.brokerage_per_order ?? prev.option_brokerage_per_order,
      option_spread_pct: c.option_config?.cost_config?.spread_pct_of_premium ?? prev.option_spread_pct,
      start_date: c.start_ts ? msToDate(c.start_ts) : "",
      end_date: c.end_ts ? msToDate(c.end_ts) : "",
      name: `${c.name || "Optimization run"} (copy)`,
    }));
    toast.success("Config loaded into setup — tweak and Auto-Optimize.");
    if (typeof window !== "undefined") window.scrollTo({ top: 0, behavior: "smooth" });
  };

  return (
    <div className="grid grid-cols-1 lg:grid-cols-[340px_minmax(0,1fr)] gap-3" data-testid="optimizer-page">
      {/* LEFT: Setup */}
      <aside className="space-y-3">
        <Panel title="Optimization Setup" testid="opt-setup-panel">
          <div className="space-y-3">
            <div>
              <Label className="text-xs text-dim">Run name</Label>
              <Input
                value={config.name}
                onChange={(e) => setConfig({ ...config, name: e.target.value })}
                className="bg-bg-2 border-line h-8 mt-1"
                data-testid="opt-name-input"
              />
            </div>
            <Row label="Instrument">
              <Select value={config.instrument} onValueChange={(v) => setConfig({ ...config, instrument: v })}>
                <SelectTrigger className="bg-bg-2 border-line h-8" data-testid="opt-instrument-select"><SelectValue /></SelectTrigger>
                <SelectContent>
                  {INSTRUMENTS.map((i) => <SelectItem key={i} value={i}>{i}</SelectItem>)}
                </SelectContent>
              </Select>
            </Row>
            <Row label="Strategy">
              <Select value={config.strategy_id} onValueChange={(v) => setConfig({ ...config, strategy_id: v })}>
                <SelectTrigger className="bg-bg-2 border-line h-8" data-testid="opt-strategy-select"><SelectValue /></SelectTrigger>
                <SelectContent>
                  {strategies.filter((s) => s.is_loaded !== false).map((s) => <SelectItem key={s.id} value={s.id}>{s.name}</SelectItem>)}
                </SelectContent>
              </Select>
            </Row>
            <Row label="Method">
              <Select value={config.method} onValueChange={(v) => setConfig({ ...config, method: v, n_trials: METHODS.find(x => x.id === v)?.default_trials || 150 })}>
                <SelectTrigger className="bg-bg-2 border-line h-8" data-testid="opt-method-select"><SelectValue /></SelectTrigger>
                <SelectContent>
                  {METHODS.map((m) => <SelectItem key={m.id} value={m.id}>{m.name}</SelectItem>)}
                </SelectContent>
              </Select>
              <div className="text-[10px] text-dimmer mt-1">{METHODS.find((m) => m.id === config.method)?.desc}</div>
            </Row>
            <Row label="Objective">
              <Select value={config.objective} onValueChange={(v) => setConfig({ ...config, objective: v })}>
                <SelectTrigger className="bg-bg-2 border-line h-8" data-testid="opt-objective-select"><SelectValue /></SelectTrigger>
                <SelectContent>
                  {OBJECTIVES.map((o) => <SelectItem key={o.id} value={o.id}>{o.name}</SelectItem>)}
                </SelectContent>
              </Select>
              <div className="text-[10px] text-dimmer mt-1">{OBJECTIVES.find((o) => o.id === config.objective)?.desc}</div>
            </Row>
            <Row label="Pre-trade profile">
              <Select value={config.pretrade_profile} onValueChange={(v) => setConfig({ ...config, pretrade_profile: v })}>
                <SelectTrigger className="bg-bg-2 border-line h-8" data-testid="opt-profile-select"><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="None">None (no pre-trade filter)</SelectItem>
                  {profiles.map((p) => <SelectItem key={p.name} value={p.name}>{p.name}</SelectItem>)}
                </SelectContent>
              </Select>
              <div className="text-[10px] text-dimmer mt-1">
                Apply the same pre-trade filter you'll backtest/trade with, so optimized params match live behaviour. "None" optimizes raw strategy signals.
              </div>
            </Row>
            <Row label="Evaluation">
              <Select value={config.evaluation_mode} onValueChange={(v) => setConfig({ ...config, evaluation_mode: v })}>
                <SelectTrigger className="bg-bg-2 border-line h-8" data-testid="opt-eval-mode-select"><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="spot">Spot points (fast)</SelectItem>
                  <SelectItem value="option_rerank">Option re-rank (realistic)</SelectItem>
                </SelectContent>
              </Select>
              <div className="text-[10px] text-dimmer mt-1">
                {config.evaluation_mode === "option_rerank"
                  ? "Searches on spot, then re-ranks the top-K by REAL paired-option net rupee (delta/theta/costs). Slower but reflects what you actually trade."
                  : "Scores the index backtest only. Fast, but spot P&L can mislead for option buying."}
              </div>
            </Row>

            {config.evaluation_mode === "option_rerank" && (
              <div className="rounded-md border border-info/30 bg-info/5 p-2 space-y-2">
                <div className="text-[10px] uppercase tracking-wider text-info">Option execution (re-rank)</div>
                <div className="grid grid-cols-2 gap-2">
                  <div>
                    <Label className="text-[11px] text-dim">Re-rank top-K</Label>
                    <Input type="number" min={1} max={500} value={config.rerank_top_k}
                      onChange={(e) => setConfig({ ...config, rerank_top_k: e.target.value })}
                      className="bg-bg-2 border-line h-8 text-xs font-mono mt-1" data-testid="opt-rerank-k" />
                  </div>
                  <div>
                    <Label className="text-[11px] text-dim">Moneyness</Label>
                    <Select value={config.option_moneyness} onValueChange={(v) => setConfig({ ...config, option_moneyness: v })}>
                      <SelectTrigger className="bg-bg-2 border-line h-8 mt-1"><SelectValue /></SelectTrigger>
                      <SelectContent>{OPT_MONEYNESS.map((m) => <SelectItem key={m} value={m}>{m.toUpperCase()}</SelectItem>)}</SelectContent>
                    </Select>
                  </div>
                  <div>
                    <Label className="text-[11px] text-dim">DTE filter</Label>
                    <Select value={config.option_dte_filter} onValueChange={(v) => setConfig({ ...config, option_dte_filter: v })}>
                      <SelectTrigger className="bg-bg-2 border-line h-8 mt-1"><SelectValue /></SelectTrigger>
                      <SelectContent>{OPT_DTE.map((d) => <SelectItem key={d.id} value={d.id}>{d.name}</SelectItem>)}</SelectContent>
                    </Select>
                  </div>
                  <div>
                    <Label className="text-[11px] text-dim">Lots</Label>
                    <Input type="number" min={1} value={config.option_lots}
                      onChange={(e) => setConfig({ ...config, option_lots: e.target.value })}
                      className="bg-bg-2 border-line h-8 text-xs font-mono mt-1" />
                  </div>
                </div>
                <div>
                  <Label className="text-[11px] text-dim">Option exit</Label>
                  <Select value={config.option_exit_mode} onValueChange={(v) => setConfig({ ...config, option_exit_mode: v })}>
                    <SelectTrigger className="bg-bg-2 border-line h-8 mt-1"><SelectValue /></SelectTrigger>
                    <SelectContent>
                      <SelectItem value="spot_exit">Mirror spot exit</SelectItem>
                      <SelectItem value="option_levels">Option premium SL/target (%)</SelectItem>
                    </SelectContent>
                  </Select>
                </div>
                {config.option_exit_mode === "option_levels" && (
                  <div className="grid grid-cols-2 gap-2">
                    <div>
                      <Label className="text-[11px] text-dim">Target % of premium</Label>
                      <Input type="number" min={0} step={5} value={config.option_target_pct}
                        onChange={(e) => setConfig({ ...config, option_target_pct: e.target.value })}
                        className="bg-bg-2 border-line h-8 text-xs font-mono mt-1" placeholder="e.g. 40" />
                    </div>
                    <div>
                      <Label className="text-[11px] text-dim">Stop % of premium</Label>
                      <Input type="number" min={0} step={5} value={config.option_stop_pct}
                        onChange={(e) => setConfig({ ...config, option_stop_pct: e.target.value })}
                        className="bg-bg-2 border-line h-8 text-xs font-mono mt-1" placeholder="e.g. 50" />
                    </div>
                  </div>
                )}
                <div className="flex items-center gap-2">
                  <Switch checked={config.option_costs_enabled} onCheckedChange={(v) => setConfig({ ...config, option_costs_enabled: v })} data-testid="opt-rerank-costs" />
                  <span className="text-[11px] text-dim">Apply option costs (charges + {config.option_spread_pct}% spread)</span>
                </div>
                <div className="text-[10px] text-dimmer leading-snug">
                  Higher top-K = more candidates re-ranked on real option P&L (slower). Option candles are loaded once per run.
                </div>
              </div>
            )}

            <NumberSliderInput
              label="Trial budget"
              value={config.n_trials}
              min={10} max={5000} step={10} decimals={0}
              onChange={(v) => setConfig({ ...config, n_trials: v })}
              testid="opt-trials"
            />
            <div className="text-[10px] text-dimmer -mt-1 leading-snug">
              Up to 5000. More trials ≠ better — beyond a few hundred the gains flatten for small spaces and overfitting risk rises. Scale the budget to how many params you're searching.
            </div>
            <div className="flex items-center gap-2 pt-1">
              <Switch checked={config.costs_enabled} onCheckedChange={(v) => setConfig({ ...config, costs_enabled: v })} data-testid="opt-costs-switch" />
              <span className="text-xs text-dim">Apply realistic costs</span>
            </div>
            <div className="flex items-center gap-2">
              <Switch checked={config.optimize_indicator_periods} onCheckedChange={(v) => setConfig({ ...config, optimize_indicator_periods: v })} data-testid="opt-indicator-periods-switch" />
              <span className="text-xs text-dim">Optimize indicator periods</span>
            </div>
            <div className="text-[10px] text-dimmer -mt-1 leading-snug">
              Also tune RSI / MACD / ATR / EMA / ADX lengths (indicators are recomputed per trial). Slower but searches the real space.
            </div>
            <div className="pt-2 border-t border-line">
              <div className="flex items-center gap-2 mb-2">
                <Switch
                  checked={config.guards_enabled}
                  onCheckedChange={(v) => setConfig({ ...config, guards_enabled: v })}
                  data-testid="opt-guards-switch"
                />
                <span className="text-xs text-dim">Guard rails</span>
              </div>
              {config.guards_enabled ? (
                <>
                  <div className="grid grid-cols-2 gap-2">
                    <div>
                      <Label className="text-[11px] text-dim">Min trades</Label>
                      <Input
                        type="number" min={0}
                        value={config.min_trades}
                        onChange={(e) => setConfig({ ...config, min_trades: e.target.value })}
                        className="bg-bg-2 border-line h-8 text-xs font-mono mt-1"
                        data-testid="opt-min-trades"
                      />
                    </div>
                    <div>
                      <Label className="text-[11px] text-dim">Min CE/PE side %</Label>
                      <Input
                        type="number" min={0} max={50}
                        value={config.min_direction_pct}
                        onChange={(e) => setConfig({ ...config, min_direction_pct: e.target.value })}
                        className="bg-bg-2 border-line h-8 text-xs font-mono mt-1"
                        data-testid="opt-min-direction-pct"
                      />
                    </div>
                  </div>
                  <div className="text-[10px] text-dimmer mt-1.5 leading-snug">
                    Disqualifies degenerate solutions: fewer than <b>{config.min_trades || 0}</b> trades (statistical-significance floor), or where the minority side (CE vs PE) is below <b>{config.min_direction_pct || 0}%</b> of trades (0 = off).
                  </div>
                </>
              ) : (
                <div className="text-[10px] text-dimmer leading-snug">
                  Off — the optimizer maximizes your selected objective purely, even if the best params are one-sided (all CE / all PE) or take few trades. Zero-trade param sets can still never win. Lean on walk-forward and the robustness score to judge if a result will hold up.
                </div>
              )}
            </div>
            <div className="pt-2 border-t border-line">
              <Label className="text-xs text-dim">Date window (IST, optional)</Label>
              <div className="grid grid-cols-2 gap-2 mt-1">
                <Input type="date" value={config.start_date} onChange={(e) => setConfig({ ...config, start_date: e.target.value })} className="bg-bg-2 border-line h-8 text-xs" data-testid="opt-start-date" />
                <Input type="date" value={config.end_date} onChange={(e) => setConfig({ ...config, end_date: e.target.value })} className="bg-bg-2 border-line h-8 text-xs" data-testid="opt-end-date" />
              </div>
            </div>
          </div>
        </Panel>

        <Panel
          title="Parameter Search Bounds (advanced)"
          right={
            <button onClick={() => setShowOverrides(!showOverrides)} className="text-dim hover:text-foreground" data-testid="opt-overrides-toggle">
              {showOverrides ? <ChevronDown className="w-4 h-4" /> : <ChevronRight className="w-4 h-4" />}
            </button>
          }
          testid="opt-overrides-panel"
        >
          {showOverrides ? (
            <div className="space-y-2">
              <div className="text-[10px] text-dimmer leading-snug mb-2">
                Override the search range for any param. Leave blank to use the strategy's default bounds.
              </div>
              {numericParams.map(([name, def]) => {
                const ov = config.param_overrides[name] || {};
                const set = (k, v) => setConfig({ ...config, param_overrides: { ...config.param_overrides, [name]: { ...ov, [k]: v === "" ? undefined : Number(v) } } });
                return (
                  <div key={name} className="grid grid-cols-[1fr_64px_64px] items-center gap-2 text-xs">
                    <div className="text-dim font-mono truncate">{name}</div>
                    <Input type="number" placeholder={String(def.min ?? "")} value={ov.min ?? ""} onChange={(e) => set("min", e.target.value)} className="bg-bg-2 border-line h-7 text-xs font-mono text-right" data-testid={`override-${name}-min`} />
                    <Input type="number" placeholder={String(def.max ?? "")} value={ov.max ?? ""} onChange={(e) => set("max", e.target.value)} className="bg-bg-2 border-line h-7 text-xs font-mono text-right" data-testid={`override-${name}-max`} />
                  </div>
                );
              })}
            </div>
          ) : (
            <div className="text-[11px] text-dimmer">Expand to widen/narrow the search range for individual parameters.</div>
          )}
        </Panel>

        <Button
          onClick={start}
          disabled={currentJob?.status === "running" || currentJob?.status === "queued" || currentJob?.status === "analyzing"}
          className="w-full bg-info text-bg-0 hover:bg-info/90 font-semibold"
          data-testid="opt-start-button"
        >
          <Sparkles className="w-4 h-4 mr-2" />
          {currentJob?.status === "running" || currentJob?.status === "queued" || currentJob?.status === "analyzing" ? "Optimizing…" : "Auto-Optimize"}
        </Button>

        <PresetsPanel presets={presets} onLoadInLab={(name) => navigate(`/backtest?preset=${encodeURIComponent(name)}`)} onRefresh={refreshPresets} onDelete={deletePreset} />
      </aside>

      {/* RIGHT: Progress + Results + History */}
      <section className="min-w-0 space-y-3">
        {currentJob ? (
          <CurrentJobView
            job={currentJob}
            onApply={applyAsPreset}
            onStop={stopJob}
            onPause={pauseJob}
            onResume={() => resumeJob(currentJob?.id)}
            onOpenBest={openBestInLab}
          />
        ) : <EmptyOptimizer />}
        <JobHistory jobs={jobs} onLoad={(id) => { setCurrentJobId(id); setPollKey((k) => k + 1); }} onClone={cloneJobConfig} onResume={resumeJob} onDelete={removeJob} onRefresh={refreshJobs} />
      </section>
    </div>
  );
}

function Panel({ title, children, right, testid }) {
  return (
    <div className="rounded-lg border border-line bg-bg-1" data-testid={testid}>
      <div className="px-3 py-2 border-b border-line flex items-center">
        <div className="text-[11px] font-semibold uppercase tracking-wider text-dim">{title}</div>
        {right && <div className="ml-auto">{right}</div>}
      </div>
      <div className="p-3">{children}</div>
    </div>
  );
}

function Row({ label, children }) {
  return (
    <div>
      <Label className="text-xs text-dim">{label}</Label>
      <div className="mt-1">{children}</div>
    </div>
  );
}

function EmptyOptimizer() {
  return (
    <div className="rounded-lg border border-dashed border-line-strong bg-bg-1 p-8 text-center">
      <Gauge className="w-10 h-10 mx-auto text-info mb-3" />
      <div className="text-base font-semibold mb-1">Auto-Optimizer ready</div>
      <div className="text-sm text-dim max-w-xl mx-auto">
        Pick a strategy + instrument + objective on the left, then click <b>Auto-Optimize</b>.
        The engine will run hundreds of backtests with Bayesian/Grid/Genetic search and return the best parameters.
        No manual tuning ever.
      </div>
    </div>
  );
}

function CurrentJobView({ job, onApply, onStop, onPause, onResume, onOpenBest }) {
  const pct = job.n_trials_total ? Math.round((job.n_trials_completed / job.n_trials_total) * 100) : 0;
  const bsf = job.best_so_far || {};
  const status = job.status;
  const finished = status === "done";
  const cancelled = status === "cancelled";
  const failed = status === "failed";
  const paused = status === "paused";
  const interrupted = status === "interrupted";
  const inProgress = status === "running" || status === "queued" || status === "analyzing";
  const resumable = paused || interrupted || failed;
  const hasBest = bsf.params && Object.keys(bsf.params).length > 0;
  const showResults = finished || cancelled || resumable;

  return (
    <div className="space-y-3" data-testid="opt-current-job">
      {/* Progress card */}
      <div className="rounded-lg border border-line bg-bg-1 p-3">
        <div className="flex items-center gap-2 flex-wrap mb-2">
          <StatusBadge status={status} />
          <div className="text-sm font-medium">{job.config?.name || "Optimization"}</div>
          <div className="text-xs text-dim font-mono">{job.strategy_id} · {job.instrument} · {job.method} · obj={job.objective}</div>
          <div className="ml-auto flex items-center gap-1">
            {inProgress && (
              <>
                <Button size="sm" variant="secondary" onClick={onPause} className="h-7 text-xs" data-testid="opt-pause-button" title="Pause — progress is saved; resume later from this point">
                  <PauseCircle className="w-3.5 h-3.5 mr-1" /> Pause
                </Button>
                <Button size="sm" variant="destructive" onClick={onStop} className="h-7 text-xs" data-testid="opt-stop-button" title="Stop the optimization (best result so far will still be saved)">
                  <StopCircle className="w-3.5 h-3.5 mr-1" /> Stop
                </Button>
              </>
            )}
            {resumable && (
              <Button size="sm" onClick={onResume} className="h-7 text-xs bg-info text-bg-0 hover:bg-info/90" data-testid="opt-resume-button" title="Resume from the last saved trial">
                <PlayCircle className="w-3.5 h-3.5 mr-1" /> Resume
              </Button>
            )}
            {showResults && (
              <>
                {job.best_backtest_run_id && (
                  <Button size="sm" variant="secondary" onClick={() => onOpenBest(job.best_backtest_run_id)} className="h-7 text-xs" data-testid="opt-view-best-button" title="View the full backtest of best params (trades, equity, walk-forward, exports)">
                    <ExternalLink className="w-3.5 h-3.5 mr-1" /> View Best in Lab
                  </Button>
                )}
                <Button size="sm" variant="secondary" onClick={() => exportOptConfig(job)} className="h-7 text-xs" title="Export optimizer config as JSON">
                  <FileJson className="w-3.5 h-3.5 mr-1" /> Config
                </Button>
                <Button size="sm" variant="secondary" onClick={() => exportOptJob(job)} className="h-7 text-xs" title="Export full optimizer job as JSON (importance, heatmap, top-N, robustness)">
                  <Download className="w-3.5 h-3.5 mr-1" /> Result
                </Button>
                {job.top_n_alternatives && (
                  <Button size="sm" variant="secondary" onClick={() => exportOptAlternatives(job)} className="h-7 text-xs" title="Export top-N alternative parameter sets as CSV">
                    <FileText className="w-3.5 h-3.5 mr-1" /> Alts.csv
                  </Button>
                )}
                {hasBest && (
                  <Button size="sm" onClick={() => onApply(job.id)} className="h-7 text-xs bg-info text-bg-0 hover:bg-info/90" data-testid="opt-apply-preset-button">
                    <Save className="w-3.5 h-3.5 mr-1" /> Save as Preset
                  </Button>
                )}
              </>
            )}
          </div>
        </div>
        <div className="text-[11px] font-mono text-dim mb-1 flex items-center justify-between">
          <span>{job.n_trials_completed || 0} / {job.n_trials_total || 0} trials</span>
          <span>{pct}%</span>
        </div>
        <div className="h-2 bg-bg-2 rounded-sm overflow-hidden border border-line">
          <div
            className={`h-full transition-[width] duration-300 ${failed ? "bg-rose-600" : cancelled ? "bg-amber-500" : finished ? "bg-emerald-600" : "bg-info"}`}
            style={{ width: `${pct}%` }}
            data-testid="opt-progress-fill"
          />
        </div>
        {failed && job.error && (
          <div className="text-xs text-rose-300 mt-2 font-mono">{job.error}</div>
        )}
        {cancelled && (
          <div className="text-xs text-amber-300 mt-2">Optimization was cancelled. Best result so far has been preserved.</div>
        )}
        {paused && (
          <div className="text-xs text-sky-300 mt-2">Paused at trial {job.n_trials_completed}/{job.n_trials_total}. Click Resume to continue from here.</div>
        )}
        {interrupted && (
          <div className="text-xs text-orange-300 mt-2">Interrupted by a restart at trial {job.n_trials_completed}/{job.n_trials_total}. Click Resume to continue.</div>
        )}
      </div>

      {/* No usable result — every trial took no trades or failed the guard rails */}
      {(finished || cancelled) && (!bsf.params || Object.keys(bsf.params).length === 0) && (
        <div className="rounded-lg border border-warning/40 bg-warning/10 p-3 text-xs text-warning leading-relaxed" data-testid="opt-no-result">
          No trial produced a usable result — every candidate either took no trades or was disqualified by the guard rails.
          Try lowering <b>Min trades</b> / <b>Min CE-PE side %</b> (or turning Guard rails off), widening the date window,
          or loosening the strategy's parameter bounds, then Auto-Optimize again.
        </div>
      )}

      {/* Best-so-far */}
      {bsf.params && Object.keys(bsf.params).length > 0 && (
        <div className="rounded-lg border border-line bg-bg-1 p-3" data-testid="opt-best-so-far">
          <div className="flex items-center gap-2 mb-2">
            <Trophy className="w-4 h-4 text-amber-400" />
            <div className="text-xs font-semibold uppercase tracking-wider text-dim">Best so far</div>
            <div className="ml-auto font-mono text-base text-foreground">{fmtBest(job.best_value ?? bsf.value)}</div>
          </div>
          <div className="grid grid-cols-2 lg:grid-cols-3 gap-2 mb-3">
            {Object.entries(bsf.params).map(([k, v]) => (
              <div key={k} className="rounded-md bg-bg-2 border border-line p-2 text-xs">
                <div className="text-[10px] uppercase tracking-wider text-dimmer truncate">{k}</div>
                <div className="font-mono text-foreground mt-0.5 truncate">{typeof v === "number" ? v.toFixed(2) : String(v)}</div>
              </div>
            ))}
          </div>
          {bsf.metrics && Object.keys(bsf.metrics).length > 0 && (
            <div className="grid grid-cols-3 lg:grid-cols-6 gap-2 text-xs">
              <SmallMetric label="Trades" value={fmtInt(bsf.metrics.trade_count)} />
              <SmallMetric label="WinRate" value={fmtPct(bsf.metrics.win_rate)} />
              <SmallMetric label="PF" value={fmtNum(bsf.metrics.profit_factor)} />
              <SmallMetric label="Net Pts" value={fmtNum(bsf.metrics.total_pnl_pts)} />
              <SmallMetric label="MaxDD" value={fmtNum(bsf.metrics.max_dd_pts)} />
              <SmallMetric label="Sharpe" value={fmtNum(bsf.metrics.sharpe)} />
            </div>
          )}
          {bsf.metrics && (bsf.metrics.ce_count != null || bsf.metrics.pe_count != null) && (
            <DirectionSplit ce={bsf.metrics.ce_count} pe={bsf.metrics.pe_count} />
          )}
        </div>
      )}

      {(finished || cancelled) && (
        <>
          {job.rerank ? (
            <RerankResults rerank={job.rerank} />
          ) : (
            <>
              <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
                <RobustnessCard robustness={job.robustness} />
                <ImportanceCard importance={job.parameter_importance} />
              </div>
              <HeatmapCard heatmap={job.heatmap} />
            </>
          )}
          <TopAlternatives items={job.top_n_alternatives} />
        </>
      )}
    </div>
  );
}

function PresetsPanel({ presets, onLoadInLab, onRefresh, onDelete }) {
  return (
    <div className="rounded-lg border border-line bg-bg-1" data-testid="opt-presets-panel">
      <div className="px-3 py-2 border-b border-line flex items-center">
        <FolderOpen className="w-3.5 h-3.5 mr-1.5 text-info" />
        <div className="text-[11px] font-semibold uppercase tracking-wider text-dim">Saved Presets</div>
        <div className="text-[10px] text-dimmer ml-2">{presets.length}</div>
        <Button variant="ghost" size="sm" onClick={onRefresh} className="ml-auto h-6 w-6 p-0">
          <RefreshCw className="w-3 h-3" />
        </Button>
      </div>
      <div className="p-2 max-h-56 overflow-y-auto">
        {presets.length === 0 ? (
          <div className="text-[11px] text-dimmer px-1 py-2">
            No presets yet. Click <b>Save as Preset</b> after an optimization to store the best params here.
          </div>
        ) : (
          <div className="space-y-1">
            {presets.map((p) => (
              <div
                key={p.name}
                className="group flex items-center rounded-md bg-bg-2 hover:bg-bg-3 border border-line transition-colors"
                data-testid={`preset-row-${p.name.replace(/[^a-z0-9]/gi, "_")}`}
              >
                <button
                  onClick={() => onLoadInLab(p.name)}
                  className="flex-1 min-w-0 text-left p-2"
                  data-testid={`preset-load-${p.name.replace(/[^a-z0-9]/gi, "_")}`}
                  title="Open this preset's params in Backtest Lab"
                >
                  <div className="text-xs font-medium truncate">{p.name}</div>
                  <div className="text-[10px] font-mono text-dimmer truncate">
                    {p.config?.strategy_id || "?"} · {p.config?.instrument || "?"}
                    {p.config?.source_optimization_job ? " · from optimizer" : ""}
                  </div>
                </button>
                <button
                  onClick={() => onDelete(p.name)}
                  className="px-2 self-stretch flex items-center text-dimmer hover:text-rose-400 shrink-0"
                  data-testid={`preset-delete-${p.name.replace(/[^a-z0-9]/gi, "_")}`}
                  title={`Delete preset "${p.name}"`}
                  aria-label={`Delete preset ${p.name}`}
                >
                  <Trash2 className="w-3.5 h-3.5" />
                </button>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

function StatusBadge({ status }) {
  const map = {
    queued: { c: "bg-slate-800 text-slate-200 border-slate-700", label: "QUEUED" },
    running: { c: "bg-info/20 text-info border-info/50 animate-pulse", label: "RUNNING" },
    analyzing: { c: "bg-amber-950 text-amber-200 border-amber-900 animate-pulse", label: "ANALYZING" },
    done: { c: "bg-emerald-950 text-emerald-200 border-emerald-900", label: "DONE" },
    cancelled: { c: "bg-amber-950 text-amber-200 border-amber-900", label: "CANCELLED" },
    paused: { c: "bg-sky-950 text-sky-200 border-sky-900", label: "PAUSED" },
    interrupted: { c: "bg-orange-950 text-orange-200 border-orange-900", label: "INTERRUPTED" },
    failed: { c: "bg-rose-950 text-rose-200 border-rose-900", label: "FAILED" },
  };
  const m = map[status] || map.queued;
  return <span className={`text-[10px] font-mono px-1.5 py-0.5 rounded border ${m.c}`}>{m.label}</span>;
}

function SmallMetric({ label, value }) {
  return (
    <div className="rounded-md bg-bg-2 border border-line p-1.5">
      <div className="text-[9px] uppercase tracking-wider text-dimmer">{label}</div>
      <div className="font-mono text-foreground text-sm">{value}</div>
    </div>
  );
}

function DirectionSplit({ ce, pe }) {
  const c = Number(ce || 0);
  const p = Number(pe || 0);
  const tot = c + p;
  if (tot === 0) return null;
  const cePct = Math.round((c / tot) * 100);
  const pePct = 100 - cePct;
  const minority = Math.min(cePct, pePct);
  const warn = minority < 10; // very one-sided
  return (
    <div className="mt-3" data-testid="opt-direction-split">
      <div className="flex items-center justify-between text-[10px] mb-1">
        <span className="text-dimmer uppercase tracking-wider">Direction split (CE / PE)</span>
        <span className={`font-mono ${warn ? "text-warning" : "text-dim"}`}>
          {c} CE · {p} PE{warn ? " · one-sided" : ""}
        </span>
      </div>
      <div className="h-2 rounded-sm overflow-hidden border border-line flex">
        <div className="h-full bg-emerald-600" style={{ width: `${cePct}%` }} title={`CE ${cePct}%`} />
        <div className="h-full bg-rose-600" style={{ width: `${pePct}%` }} title={`PE ${pePct}%`} />
      </div>
    </div>
  );
}

function RobustnessCard({ robustness }) {
  if (!robustness) {
    return <div className="rounded-lg border border-line bg-bg-1 p-3 text-xs text-dimmer">Robustness not computed</div>;
  }
  const score = robustness.score || 0;
  const color = score >= 70 ? "text-success" : score >= 50 ? "text-warning" : "text-danger";
  const label = score >= 70 ? "ROBUST" : score >= 50 ? "MODERATE" : "FRAGILE";
  return (
    <div className="rounded-lg border border-line bg-bg-1 p-3" data-testid="opt-robustness-card">
      <div className="text-xs font-semibold uppercase tracking-wider text-dim mb-2">Robustness</div>
      <div className="flex items-baseline gap-3 mb-2">
        <div className={`text-2xl font-mono ${color}`}>{score}</div>
        <div className={`text-xs font-semibold ${color}`}>{label}</div>
      </div>
      <div className="text-[10px] text-dimmer mb-2">% of ±10/20% param perturbations that stayed within 85% of best objective</div>
      <div className="max-h-40 overflow-y-auto">
        <table className="w-full text-[10px] font-mono">
          <thead><tr className="text-dimmer"><th className="text-left p-1">Param</th><th className="text-right p-1">Shift</th><th className="text-right p-1">Obj</th><th className="text-center p-1">OK</th></tr></thead>
          <tbody>
            {(robustness.perturbations || []).map((p, i) => (
              <tr key={i} className="border-t border-line">
                <td className="p-1 text-dim truncate">{p.param}</td>
                <td className="p-1 text-right">{p.shift_pct > 0 ? "+" : ""}{p.shift_pct}%</td>
                <td className="p-1 text-right">{p.objective}</td>
                <td className="p-1 text-center">{p.ok ? <span className="text-success">✓</span> : <span className="text-danger">✗</span>}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function ImportanceCard({ importance }) {
  const items = importance || [];
  const max = Math.max(...items.map((i) => i.importance || 0), 0.001);
  return (
    <div className="rounded-lg border border-line bg-bg-1 p-3" data-testid="opt-importance-card">
      <div className="text-xs font-semibold uppercase tracking-wider text-dim mb-2">Parameter Importance</div>
      {items.length === 0 ? (
        <div className="text-xs text-dimmer">Not computed</div>
      ) : (
        <div className="space-y-1.5">
          {items.map((it) => (
            <div key={it.param} className="flex items-center gap-2 text-xs">
              <div className="w-32 text-dim font-mono truncate">{it.param}</div>
              <div className="flex-1 h-2.5 bg-bg-2 rounded-sm overflow-hidden border border-line">
                <div className="h-full bg-info" style={{ width: `${(it.importance / max) * 100}%` }} />
              </div>
              <div className="w-12 text-right font-mono">{(it.importance * 100).toFixed(1)}%</div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function HeatmapCard({ heatmap }) {
  if (!heatmap) {
    return <div className="rounded-lg border border-line bg-bg-1 p-3 text-xs text-dimmer">Heatmap not generated (need ≥2 numeric params).</div>;
  }
  // Compute min/max objective in grid for color scale
  const vals = heatmap.grid.flat().map((c) => c.val);
  const minV = Math.min(...vals);
  const maxV = Math.max(...vals);
  const colorFor = (v) => {
    if (maxV === minV) return "#1B2330";
    const t = (v - minV) / (maxV - minV);
    // green at top, red at bottom
    const r = Math.round(255 * (1 - t) * 0.7 + 30);
    const g = Math.round(255 * t * 0.7 + 30);
    return `rgb(${r}, ${g}, 80)`;
  };
  return (
    <div className="rounded-lg border border-line bg-bg-1 p-3" data-testid="opt-heatmap-card">
      <div className="text-xs font-semibold uppercase tracking-wider text-dim mb-2">
        Heatmap · <span className="font-mono text-foreground">{heatmap.param_a}</span> × <span className="font-mono text-foreground">{heatmap.param_b}</span>
      </div>
      <div className="overflow-x-auto">
        <table className="text-[10px] font-mono">
          <thead>
            <tr>
              <th className="text-dimmer text-right pr-2">{heatmap.param_a} ↓ \ {heatmap.param_b} →</th>
              {heatmap.b_values.map((bv, j) => (
                <th key={j} className="text-dimmer text-center px-1" style={{ minWidth: 50 }}>{Number(bv).toFixed(2)}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {heatmap.grid.map((row, i) => (
              <tr key={i}>
                <td className="text-dimmer text-right pr-2 py-1">{Number(heatmap.a_values[i]).toFixed(2)}</td>
                {row.map((cell, j) => (
                  <td key={j} className="text-center p-0">
                    <div
                      className="w-12 h-7 mx-auto flex items-center justify-center text-[9px] font-mono"
                      style={{ backgroundColor: colorFor(cell.val), color: "#0B0F14" }}
                      title={`${heatmap.param_a}=${heatmap.a_values[i]}, ${heatmap.param_b}=${heatmap.b_values[j]}\nobj=${cell.val}\ntrades=${cell.trades}`}
                    >
                      {cell.val.toFixed(2)}
                    </div>
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="text-[10px] text-dimmer mt-2">Darker green = higher objective. Hover cells for trade count.</div>
    </div>
  );
}

function RerankResults({ rerank }) {
  const ranked = rerank?.ranked || [];
  if (ranked.length === 0) {
    return (
      <div className="rounded-lg border border-warning/40 bg-warning/10 p-3 text-xs text-warning" data-testid="opt-rerank-empty">
        Option re-rank produced no paired results — likely missing option data for this window/strikes. Check option-data coverage in the Data Warehouse, or widen moneyness/DTE.
      </div>
    );
  }
  const fmtRs = (v) => {
    const n = Number(v || 0);
    const s = n < 0 ? "-" : "";
    return `${s}₹${Math.abs(n).toLocaleString("en-IN", { maximumFractionDigits: 0 })}`;
  };
  return (
    <div className="rounded-lg border border-line bg-bg-1" data-testid="opt-rerank-results">
      <div className="px-3 py-2 border-b border-line flex items-center gap-2">
        <Trophy className="w-3.5 h-3.5 text-info" />
        <div className="text-xs font-semibold uppercase tracking-wider text-dim">Option Re-rank · top {rerank.evaluated} by net ₹</div>
      </div>
      <div className="px-3 py-2 text-[10px] text-dimmer leading-snug border-b border-line">
        Each candidate's spot signals were paired with real {String(rerank.option_config?.moneyness || "ATM").toUpperCase()} option candles and scored on net rupee P&L (delta/theta/costs). Ranked best-first — this is the realistic ranking.
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead>
            <tr className="text-dim border-b border-line">
              <th className="text-left p-2">#</th>
              <th className="text-right p-2">Net ₹ (option)</th>
              <th className="text-right p-2">Opt WR</th>
              <th className="text-right p-2">Paired</th>
              <th className="text-right p-2">Spot obj</th>
              <th className="text-right p-2">Coverage</th>
              <th className="text-left p-2">Params</th>
            </tr>
          </thead>
          <tbody>
            {ranked.map((r, i) => {
              const cov = r.coverage || {};
              const covPct = cov.spot_trade_count ? Math.round((cov.paired_trade_count / cov.spot_trade_count) * 100) : null;
              const pnl = Number(r.option_pnl_value || 0);
              return (
                <tr key={i} className={`border-b border-line ${i === 0 ? "bg-info/5" : ""}`}>
                  <td className="p-2 font-mono">{i + 1}</td>
                  <td className={`p-2 font-mono text-right font-semibold ${pnl >= 0 ? "text-success" : "text-danger"}`}>{fmtRs(pnl)}</td>
                  <td className="p-2 font-mono text-right">{fmtPct(r.option_win_rate)}</td>
                  <td className="p-2 font-mono text-right">{fmtInt(r.paired_trade_count)}/{fmtInt(r.spot_trade_count)}</td>
                  <td className="p-2 font-mono text-right text-dim">{fmtNum(r.spot_objective)}</td>
                  <td className={`p-2 font-mono text-right ${covPct != null && covPct < 80 ? "text-warning" : "text-dim"}`}>{covPct != null ? `${covPct}%` : "–"}</td>
                  <td className="p-2 font-mono text-[10px] text-dim">
                    {Object.entries(r.params).slice(0, 4).map(([k, v]) => `${k}=${typeof v === "number" ? v.toFixed(1) : v}`).join("  ")}
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

function TopAlternatives({ items }) {
  if (!items || items.length === 0) return null;
  return (
    <div className="rounded-lg border border-line bg-bg-1" data-testid="opt-top-alternatives">
      <div className="px-3 py-2 border-b border-line text-xs font-semibold uppercase tracking-wider text-dim">
        Top {items.length} Alternative Parameter Sets
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead>
            <tr className="text-dim border-b border-line">
              <th className="text-left p-2">#</th>
              <th className="text-right p-2">Objective</th>
              <th className="text-right p-2">Trades</th>
              <th className="text-right p-2">WinRate</th>
              <th className="text-right p-2">PF</th>
              <th className="text-right p-2">MaxDD</th>
              <th className="text-center p-2">CE/PE</th>
              <th className="text-left p-2">Params</th>
            </tr>
          </thead>
          <tbody>
            {items.map((it, i) => (
              <tr key={i} className="border-b border-line">
                <td className="p-2 font-mono">{i + 1}</td>
                <td className="p-2 font-mono text-right text-foreground">{Number(it.objective_value).toFixed(3)}</td>
                <td className="p-2 font-mono text-right">{fmtInt(it.metrics?.trade_count)}</td>
                <td className="p-2 font-mono text-right">{fmtPct(it.metrics?.win_rate)}</td>
                <td className="p-2 font-mono text-right">{fmtNum(it.metrics?.profit_factor)}</td>
                <td className="p-2 font-mono text-right text-danger">{fmtNum(it.metrics?.max_dd_pts)}</td>
                <td className="p-2 font-mono text-center text-dim">{it.metrics?.ce_count ?? "–"}/{it.metrics?.pe_count ?? "–"}</td>
                <td className="p-2 font-mono text-[10px] text-dim">
                  {Object.entries(it.params).slice(0, 5).map(([k, v]) => `${k}=${typeof v === "number" ? v.toFixed(1) : v}`).join("  ")}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function JobHistory({ jobs, onLoad, onClone, onResume, onDelete, onRefresh }) {
  return (
    <div className="rounded-lg border border-line bg-bg-1" data-testid="opt-job-history">
      <div className="px-3 py-2 border-b border-line flex items-center">
        <Activity className="w-3.5 h-3.5 mr-1.5 text-dim" />
        <div className="text-xs font-semibold uppercase tracking-wider text-dim">Job History</div>
        <Button variant="ghost" size="sm" onClick={onRefresh} className="ml-auto h-7 text-xs"><RefreshCw className="w-3 h-3" /></Button>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead>
            <tr className="text-dim border-b border-line">
              <th className="text-left p-2">Created</th>
              <th className="text-left p-2">Status</th>
              <th className="text-left p-2">Strategy</th>
              <th className="text-left p-2">Instr.</th>
              <th className="text-left p-2">Method</th>
              <th className="text-left p-2">Objective</th>
              <th className="text-right p-2">Trials</th>
              <th className="text-right p-2">Best</th>
              <th className="p-2"></th>
            </tr>
          </thead>
          <tbody>
            {jobs.length === 0 && (
              <tr><td colSpan="9" className="p-4 text-center text-dimmer">No optimizations yet.</td></tr>
            )}
            {jobs.map((j) => (
              <tr key={j.id} className="border-b border-line hover:bg-bg-2 cursor-pointer" onClick={() => onLoad(j.id)} data-testid="opt-history-row">
                <td className="p-2 font-mono text-dim">{isoToFull(j.created_at)}</td>
                <td className="p-2"><StatusBadge status={j.status} /></td>
                <td className="p-2 font-mono text-dim">{j.strategy_id}</td>
                <td className="p-2 font-mono">{j.instrument}</td>
                <td className="p-2 font-mono">{j.method}</td>
                <td className="p-2 font-mono text-dim">{j.objective}</td>
                <td className="p-2 font-mono text-right">{j.n_trials_completed || 0}/{j.n_trials_total}</td>
                <td className="p-2 font-mono text-right text-foreground">{j.best_so_far?.value !== undefined ? fmtBest(j.best_so_far.value) : "–"}</td>
                <td className="p-2" onClick={(e) => e.stopPropagation()}>
                  <div className="flex items-center justify-end gap-0.5">
                    {["paused", "interrupted", "failed"].includes(j.status) && (
                      <Button size="sm" variant="ghost" className="h-6 w-6 p-0" onClick={() => onResume(j.id)} title="Resume from the last saved trial" data-testid={`opt-resume-${j.id.slice(0, 8)}`}>
                        <PlayCircle className="w-3 h-3 text-info" />
                      </Button>
                    )}
                    <Button size="sm" variant="ghost" className="h-6 w-6 p-0" onClick={() => onClone(j)} title="Clone this config into the setup panel" data-testid={`opt-clone-${j.id.slice(0, 8)}`}>
                      <Copy className="w-3 h-3 text-dim" />
                    </Button>
                    <Button size="sm" variant="ghost" className="h-6 w-6 p-0" onClick={() => onDelete(j.id)} title="Delete job" data-testid={`opt-delete-${j.id.slice(0, 8)}`}>
                      <Trash2 className="w-3 h-3 text-rose-400" />
                    </Button>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
