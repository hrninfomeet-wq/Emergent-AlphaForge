import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { toast } from "sonner";
import { api } from "@/lib/api";
import { fmtInt, fmtNum, fmtPct, isoToFull } from "@/lib/fmt";
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
  ExternalLink,
} from "lucide-react";

const INSTRUMENTS = ["NIFTY", "BANKNIFTY", "SENSEX"];
const MODES = ["SCALP", "INTRADAY"];
const METHODS = [
  { id: "bayesian", name: "Bayesian (Optuna TPE)", desc: "Smart, focuses on promising regions. Recommended.", default_trials: 150 },
  { id: "grid", name: "Grid Search", desc: "Deterministic, exhaustive (sampled if space too large).", default_trials: 200 },
  { id: "genetic", name: "Genetic (CMA-ES)", desc: "Multi-objective, good for many params.", default_trials: 200 },
];
const OBJECTIVES = [
  { id: "risk_adjusted", name: "Risk-Adjusted Return (default)", desc: "Sharpe / drawdown — balanced quality" },
  { id: "sharpe", name: "Maximize Sharpe Ratio", desc: "Risk-adjusted return per std-dev" },
  { id: "profit_factor", name: "Maximize Profit Factor", desc: "Gross profit / |gross loss|" },
  { id: "total_pnl_pts", name: "Maximize Net P&L (pts)", desc: "Raw profit; ignores drawdown" },
  { id: "win_rate", name: "Maximize Win Rate", desc: "% of trades profitable" },
  { id: "neg_max_dd", name: "Minimize Max Drawdown", desc: "Stable equity curve" },
];

export default function Optimizer() {
  const navigate = useNavigate();
  const [strategies, setStrategies] = useState([]);
  const [presets, setPresets] = useState([]);
  const [config, setConfig] = useState({
    instrument: "NIFTY",
    mode: "SCALP",
    strategy_id: "confluence_scalper",
    method: "bayesian",
    objective: "risk_adjusted",
    n_trials: 150,
    costs_enabled: true,
    pretrade_filters: {},
    param_overrides: {},
    name: "Optimization run",
    start_date: "",
    end_date: "",
  });
  const [currentJobId, setCurrentJobId] = useState(null);
  const [currentJob, setCurrentJob] = useState(null);
  const [jobs, setJobs] = useState([]);
  const [showOverrides, setShowOverrides] = useState(false);
  const pollRef = useRef(null);

  useEffect(() => {
    api.listStrategies().then((d) => setStrategies(d.items || []));
    api.listOptJobs(30).then((d) => setJobs(d.items || []));
    api.listPresets().then((d) => setPresets(d.items || []));
  }, []);

  const refreshJobs = () => api.listOptJobs(30).then((d) => setJobs(d.items || []));
  const refreshPresets = () => api.listPresets().then((d) => setPresets(d.items || []));

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
        if (j.status === "done" || j.status === "failed") {
          if (pollRef.current) clearInterval(pollRef.current);
          pollRef.current = null;
          refreshJobs();
          if (j.status === "done") {
            toast.success(`Optimization complete: best ${j.best_value?.toFixed(3)}`);
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
  }, [currentJobId]);

  const dateToMs = (s, end = false) => {
    if (!s) return null;
    const [y, m, d] = s.split("-").map(Number);
    if (!y || !m || !d) return null;
    const istHour = end ? 15 : 9;
    const istMin = end ? 30 : 15;
    const baseUtc = Date.UTC(y, m - 1, d, istHour, istMin, 0);
    return baseUtc - (5 * 60 + 30) * 60 * 1000;
  };

  const start = async () => {
    try {
      const payload = {
        instrument: config.instrument,
        mode: config.mode,
        strategy_id: config.strategy_id,
        method: config.method,
        objective: config.objective,
        n_trials: config.n_trials,
        costs_enabled: config.costs_enabled,
        pretrade_filters: config.pretrade_filters,
        param_overrides: config.param_overrides,
        start_ts: dateToMs(config.start_date, false),
        end_ts: dateToMs(config.end_date, true),
        name: config.name,
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
            <Row label="Mode">
              <Select value={config.mode} onValueChange={(v) => setConfig({ ...config, mode: v })}>
                <SelectTrigger className="bg-bg-2 border-line h-8" data-testid="opt-mode-select"><SelectValue /></SelectTrigger>
                <SelectContent>
                  {MODES.map((m) => <SelectItem key={m} value={m}>{m}</SelectItem>)}
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
            <NumberSliderInput
              label="Trial budget"
              value={config.n_trials}
              min={10} max={1000} step={10} decimals={0}
              onChange={(v) => setConfig({ ...config, n_trials: v })}
              testid="opt-trials"
            />
            <div className="flex items-center gap-2 pt-1">
              <Switch checked={config.costs_enabled} onCheckedChange={(v) => setConfig({ ...config, costs_enabled: v })} data-testid="opt-costs-switch" />
              <span className="text-xs text-dim">Apply realistic costs</span>
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

        <PresetsPanel presets={presets} onLoadInLab={(name) => navigate(`/backtest?preset=${encodeURIComponent(name)}`)} onRefresh={refreshPresets} />
      </aside>

      {/* RIGHT: Progress + Results + History */}
      <section className="min-w-0 space-y-3">
        {currentJob ? (
          <CurrentJobView
            job={currentJob}
            onApply={applyAsPreset}
            onStop={stopJob}
            onOpenBest={openBestInLab}
          />
        ) : <EmptyOptimizer />}
        <JobHistory jobs={jobs} onLoad={(id) => { setCurrentJobId(id); }} onDelete={removeJob} onRefresh={refreshJobs} />
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

function CurrentJobView({ job, onApply, onStop, onOpenBest }) {
  const pct = job.n_trials_total ? Math.round((job.n_trials_completed / job.n_trials_total) * 100) : 0;
  const bsf = job.best_so_far || {};
  const status = job.status;
  const finished = status === "done";
  const cancelled = status === "cancelled";
  const failed = status === "failed";
  const inProgress = status === "running" || status === "queued" || status === "analyzing";

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
              <Button size="sm" variant="destructive" onClick={onStop} className="h-7 text-xs" data-testid="opt-stop-button" title="Stop the optimization (best result so far will still be saved)">
                <StopCircle className="w-3.5 h-3.5 mr-1" /> Stop
              </Button>
            )}
            {(finished || cancelled) && (
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
                <Button size="sm" onClick={() => onApply(job.id)} className="h-7 text-xs bg-info text-bg-0 hover:bg-info/90" data-testid="opt-apply-preset-button">
                  <Save className="w-3.5 h-3.5 mr-1" /> Save as Preset
                </Button>
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
      </div>

      {/* Best-so-far */}
      {bsf.params && Object.keys(bsf.params).length > 0 && (
        <div className="rounded-lg border border-line bg-bg-1 p-3" data-testid="opt-best-so-far">
          <div className="flex items-center gap-2 mb-2">
            <Trophy className="w-4 h-4 text-amber-400" />
            <div className="text-xs font-semibold uppercase tracking-wider text-dim">Best so far</div>
            <div className="ml-auto font-mono text-base text-foreground">{job.best_value?.toFixed(3) ?? bsf.value?.toFixed(3)}</div>
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
        </div>
      )}

      {(finished || cancelled) && (
        <>
          {/* Robustness + Importance + Top alternatives */}
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
            <RobustnessCard robustness={job.robustness} />
            <ImportanceCard importance={job.parameter_importance} />
          </div>
          <HeatmapCard heatmap={job.heatmap} />
          <TopAlternatives items={job.top_n_alternatives} />
        </>
      )}
    </div>
  );
}

function PresetsPanel({ presets, onLoadInLab, onRefresh }) {
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
              <button
                key={p.name}
                onClick={() => onLoadInLab(p.name)}
                className="w-full text-left rounded-md bg-bg-2 hover:bg-bg-3 border border-line p-2 transition-colors"
                data-testid={`preset-load-${p.name.replace(/[^a-z0-9]/gi, "_")}`}
                title="Open this preset's params in Backtest Lab"
              >
                <div className="text-xs font-medium truncate">{p.name}</div>
                <div className="text-[10px] font-mono text-dimmer truncate">
                  {p.config?.strategy_id || "?"} · {p.config?.instrument || "?"}
                  {p.config?.source_optimization_job ? " · from optimizer" : ""}
                </div>
              </button>
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

function JobHistory({ jobs, onLoad, onDelete, onRefresh }) {
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
                <td className="p-2 font-mono text-right text-foreground">{j.best_so_far?.value !== undefined ? Number(j.best_so_far.value).toFixed(3) : "–"}</td>
                <td className="p-2" onClick={(e) => e.stopPropagation()}>
                  <Button size="sm" variant="ghost" className="h-6 w-6 p-0" onClick={() => onDelete(j.id)} data-testid={`opt-delete-${j.id.slice(0, 8)}`}>
                    <Trash2 className="w-3 h-3 text-rose-400" />
                  </Button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
