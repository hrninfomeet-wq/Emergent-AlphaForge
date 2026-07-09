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
import { TrustScorecard } from "@/components/TrustScorecard";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { NumberSliderInput } from "@/components/NumberSliderInput";
import { useMaximize, MaximizeButton } from "@/components/MaximizeButton";
import { useTableSort, SortHeader } from "@/components/SortHeader";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs";
import {
  Gauge, Play, RefreshCw, Sparkles, Trash2, ChevronDown, ChevronRight,
  Save, Activity, Trophy, StopCircle, Download, FileJson, FileText, FolderOpen,
  ExternalLink, Copy, PauseCircle, PlayCircle, Rocket, Pencil, Search, X, HelpCircle,
} from "lucide-react";
import { Tooltip, TooltipTrigger, TooltipContent, TooltipProvider } from "@/components/ui/tooltip";

// Small inline "?" help affordance: a focusable icon that reveals a styled tooltip
// on hover/focus (self-contained provider so it works wherever it's dropped).
const Hint = ({ children, label = "help" }) => (
  <TooltipProvider delayDuration={150}>
    <Tooltip>
      <TooltipTrigger asChild>
        <button
          type="button"
          aria-label={label}
          className="ml-1 inline-flex align-middle text-dimmer hover:text-dim focus:outline-none focus-visible:text-dim"
        >
          <HelpCircle className="h-3 w-3" />
        </button>
      </TooltipTrigger>
      <TooltipContent className="max-w-xs text-[11px] leading-snug">
        {children}
      </TooltipContent>
    </Tooltip>
  </TooltipProvider>
);

const INSTRUMENTS = ["NIFTY", "BANKNIFTY", "SENSEX"];
const OPT_MONEYNESS = ["atm", "otm1", "otm2", "itm1", "itm2"];
const DTE_VALUES = [0, 1, 2, 3, 4, 5, 6];

// DTE filter is a multi-select array of ints (empty = all). Older jobs/setups
// stored a single token ("dte2") or "all" — normalize every shape.
const parseDteFilter = (value) => {
  if (value == null || value === "all") return [];
  const toInt = (v) => {
    const s = String(v).trim().toLowerCase().replace(/^dte/, "");
    const n = parseInt(s, 10);
    return Number.isFinite(n) && n >= 0 ? n : null;
  };
  const arr = Array.isArray(value) ? value : [value];
  return [...new Set(arr.map(toInt).filter((n) => n !== null))].sort((a, b) => a - b);
};
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
// Persist the RUNNING job id too, so navigating away and back re-attaches to the
// live progress instead of dropping it (the backend job keeps running). Mirrors
// the JobsProvider pattern used for warehouse jobs. Cleared once the job is terminal.
const RUN_KEY = "alphaforge.optimizer.activeJobId";

const DEFAULT_SETUP = {
  instrument: "NIFTY",
  mode: "SCALP",
  strategy_id: "confluence_scalper",
  method: "bayesian",
  objective: "risk_adjusted",
  n_trials: 150,
  // Parallel trial workers (bayesian only; backend ignores for grid). 1 = serial
  // & deterministic; >1 is faster but non-deterministic and uses more RAM.
  opt_workers: 1,
  // Auto-stop when the best plateaus — n_trials becomes a ceiling, not a target.
  early_stop: true,
  // Run type: "single" (one optimization over the whole window) or
  // "walkforward" (re-optimize per train window, stitch OOS — honest result).
  run_kind: "single",
  wf_train_days: 60,
  wf_test_days: 20,
  wf_step_days: "",
  wf_mode: "rolling",
  wf_trials_per_window: 40,
  wf_max_windows: 12,
  // WFO v2: pair the stitched OOS trades with real option candles (rupee
  // reality check). Uses the same option sub-panel config as the re-rank.
  wfo_option_aware: true,
  costs_enabled: true,
  pretrade_filters: {},
  pretrade_profile: "None",
  param_overrides: {},
  optimize_indicator_periods: false,
  guards_enabled: true,
  min_trades: 10,
  min_direction_pct: 0,
  // Evaluation mode: "option_rerank" (DEFAULT — re-rank top-K by REAL
  // paired-option net rupee, the number you actually trade) or "spot" (fast,
  // scores the index backtest only; can badly mislead for option buying — a
  // 2026-07 audit found a confluence run whose spot score was +289k but real
  // option net was -207k). Option-buying app -> option-net is the honest default.
  evaluation_mode: "option_rerank",
  rerank_top_k: 50,
  rerank_diversity: false,
  // Analyzing budget (minutes). Caps the option re-rank / survival-check phase;
  // on hit the backend returns the best evaluated so far. 0 = unlimited.
  analyze_budget_min: 30,
  option_moneyness: "atm",
  option_dte_filter: [], // multi-select ints; empty = all
  option_lots: 1,
  option_exit_mode: "spot_exit",
  option_sl_tp_unit: "pct",
  option_target_pts: "",
  option_stop_pts: "",
  option_target_pct: "",
  option_stop_pct: "",
  option_costs_enabled: true,
  option_brokerage_per_order: 0,
  option_spread_pct: 1.0,
  // Trading capital the survival gate scales DD% / risk-of-ruin against (O2). Was a
  // hidden hard-wired ₹200k; now user-set and posted as option_config.sizing_config.capital.
  option_capital: 200000,
  survival_config: {
    enabled: false,
    min_equity: 0,
    max_drawdown_pct: 35,
    max_ror_pct: 5,
    ruin_floor: 0,
    objective: "calmar",
    min_oos_folds: "all",
  },
  // Exit-control search (Piece 2): auto-tune trailing stop / breakeven trigger per survivor.
  // Requires survival enabled + option_rerank. Posted as top-level search_exit_controls.
  search_exit_controls: false,
  // Optional explicit grid bounds — empty string = use backend defaults.
  exit_search_trail_distance: "",
  exit_search_breakeven_trigger: "",
  // Entry-time window (IST). Default = the LIVE-EFFECTIVE window every deployment
  // enforces (09:25 open block → 14:50 close block), so validated results never
  // include 14:50–15:00 entries live can't take (O6).
  trade_window_start: "09:25",
  trade_window_end: "14:50",
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
    const merged = { ...DEFAULT_SETUP, ...parsed };
    // DTE filter became a multi-select array; coerce legacy "all"/"dte2" tokens.
    merged.option_dte_filter = parseDteFilter(merged.option_dte_filter);
    // Deep-merge survival_config so new sub-keys added to DEFAULT_SETUP are always present.
    merged.survival_config = { ...DEFAULT_SETUP.survival_config, ...(parsed.survival_config || {}) };
    return merged;
  } catch {
    return { ...DEFAULT_SETUP };
  }
}

// The optimizer scores guard-failing / zero-trade trials with a large negative
// sentinel (~ -1e9). Render that as "—" instead of a meaningless huge number.
const fmtBest = (v) => (v == null || v <= -1e8) ? "—" : Number(v).toFixed(3);

// Format an ETA in seconds as "Xh Ym" / "Ym Ss" / "Ss", or "—" when null/unknown.
const fmtEta = (s) => {
  if (s == null) return "—";
  s = Math.round(s); const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), ss = s % 60;
  return h > 0 ? `${h}h ${m}m` : m > 0 ? `${m}m ${ss}s` : `${ss}s`;
};

// Auto run-name = descriptive (strategy · instrument · objective) + a timestamp,
// so a forgotten default never collides and runs are identifiable in Job History.
const runNameStamp = () => {
  const d = new Date();
  const p = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
};
const autoRunName = (cfg) => `${cfg.strategy_id} · ${cfg.instrument} · ${cfg.objective} · ${runNameStamp()}`;

export default function Optimizer() {
  const navigate = useNavigate();
  const [strategies, setStrategies] = useState([]);
  const [presets, setPresets] = useState([]);
  const [profiles, setProfiles] = useState([]);
  const [config, setConfig] = useState(loadSetup);
  // Rehydrate the running job id on mount so a long optimization survives
  // navigating away (e.g. to check the warehouse) and back.
  const [currentJobId, setCurrentJobId] = useState(() => {
    try { return localStorage.getItem(RUN_KEY) || null; } catch { return null; }
  });
  const [currentJob, setCurrentJob] = useState(null);
  const [jobs, setJobs] = useState([]);
  const [showOverrides, setShowOverrides] = useState(false);
  const [pollKey, setPollKey] = useState(0);
  const pollRef = useRef(null);
  // True once the operator types their own run name (or a clone set one), so the
  // auto-name regenerator below leaves it alone. Reset after each successful start.
  const nameTouchedRef = useRef(false);
  // Last-seen status for the active job, so we toast only on a genuine
  // transition into a terminal state — not when re-attaching to a job that had
  // already finished while we were on another page.
  const prevStatusRef = useRef(null);

  // Persist setup config on every change (transient run state lives in separate
  // state and is never written here). Storage failures are swallowed so a full
  // or unavailable localStorage never blocks the page.
  useEffect(() => {
    try {
      localStorage.setItem(SETUP_KEY, JSON.stringify(config));
    } catch { /* ignore quota / privacy-mode errors */ }
  }, [config]);

  // Deep-link: /optimizer?strategy=<id> selects the strategy (the authoring wizard's
  // "Optimize" next-step CTA). Mount-only; clears the param so a reload doesn't re-apply.
  useEffect(() => {
    const sid = new URLSearchParams(window.location.search).get("strategy");
    if (sid) {
      setConfig((c) => ({ ...c, strategy_id: sid }));
      navigate("/optimizer", { replace: true });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Persist / clear the active job id so it can be re-attached after navigation.
  useEffect(() => {
    try {
      if (currentJobId) localStorage.setItem(RUN_KEY, currentJobId);
      else localStorage.removeItem(RUN_KEY);
    } catch { /* ignore */ }
  }, [currentJobId]);

  useEffect(() => {
    api.listStrategies().then((d) => setStrategies(d.items || []));
    api.listOptJobs(1000).then((d) => setJobs(d.items || []));
    api.listPresets().then((d) => setPresets(d.items || []));
    api.listProfiles().then((d) => setProfiles(d.items || []));
  }, []);

  // Auto-fill the Run name with a fresh descriptive+timestamp default whenever the
  // strategy / instrument / objective changes (and on mount) — unless the operator
  // typed their own or a clone set one. This is what stops a forgotten default from
  // saving multiple jobs under the same name.
  useEffect(() => {
    if (nameTouchedRef.current) return;
    setConfig((c) => ({ ...c, name: autoRunName(c) }));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [config.strategy_id, config.instrument, config.objective]);

  const refreshJobs = () => api.listOptJobs(1000).then((d) => setJobs(d.items || []));
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
    prevStatusRef.current = null;  // reset per job so we detect the terminal transition
    const TERMINAL = ["done", "failed", "cancelled", "paused", "interrupted"];
    const tick = async () => {
      try {
        const j = await api.getOptJob(currentJobId);
        if (cancelled) return;
        setCurrentJob(j);
        // Was the job ALREADY terminal on our first look? (re-attached to a job
        // that finished while we were elsewhere) -> update UI but don't re-toast.
        const alreadyDone = prevStatusRef.current === null && TERMINAL.includes(j.status);
        prevStatusRef.current = j.status;
        if (TERMINAL.includes(j.status)) {
          if (pollRef.current) clearInterval(pollRef.current);
          pollRef.current = null;
          try { localStorage.removeItem(RUN_KEY); } catch { /* ignore */ }
          refreshJobs();
          if (!alreadyDone) {
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
        }
      } catch (e) {
        // A rehydrated id that no longer exists (deleted job) would otherwise
        // poll forever — drop it on the first failed look. Established runs
        // tolerate transient errors (prevStatusRef already set).
        if (!cancelled && prevStatusRef.current === null) {
          if (pollRef.current) clearInterval(pollRef.current);
          pollRef.current = null;
          setCurrentJobId(null);
        }
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

  // One option_config builder shared by the re-rank payload and the WFO
  // option-aware OOS payload, so both validate under identical execution terms.
  const buildOptionConfig = () => {
    // Parse comma-separated exit-search grid bounds (e.g. "20,25,30").
    // Returns an array of numbers when the string is non-empty and valid, otherwise null.
    const parseGrid = (s) => {
      if (!s || !String(s).trim()) return null;
      const nums = String(s).split(",").map((v) => Number(v.trim())).filter((n) => Number.isFinite(n) && n > 0);
      return nums.length > 0 ? nums : null;
    };
    const exitControlSearch =
      config.search_exit_controls &&
      config.survival_config?.enabled &&
      config.evaluation_mode === "option_rerank"
        ? (() => {
            const trail = parseGrid(config.exit_search_trail_distance);
            const be = parseGrid(config.exit_search_breakeven_trigger);
            // Omit exit_control_search entirely when both grids are default (backend uses built-in grid).
            if (!trail && !be) return null;
            return {
              ...(trail ? { trail_distance: trail } : {}),
              ...(be ? { breakeven_trigger: be } : {}),
            };
          })()
        : null;
    return {
      moneyness: config.option_moneyness,
      dte_filter: Array.isArray(config.option_dte_filter)
        && config.option_dte_filter.length > 0
        && config.option_dte_filter.length < DTE_VALUES.length
        ? config.option_dte_filter
        : null,
      lots: Math.max(1, Number(config.option_lots || 1)),
      entry_max_age_sec: 120,
      exit_max_age_sec: 180,
      exit_mode: config.option_exit_mode,
      option_target_pts: config.option_exit_mode === "option_levels" && config.option_sl_tp_unit === "pts" && config.option_target_pts !== "" ? Number(config.option_target_pts) : null,
      option_stop_pts: config.option_exit_mode === "option_levels" && config.option_sl_tp_unit === "pts" && config.option_stop_pts !== "" ? Number(config.option_stop_pts) : null,
      option_target_pct: config.option_exit_mode === "option_levels" && config.option_sl_tp_unit === "pct" && config.option_target_pct !== "" ? Number(config.option_target_pct) : null,
      option_stop_pct: config.option_exit_mode === "option_levels" && config.option_sl_tp_unit === "pct" && config.option_stop_pct !== "" ? Number(config.option_stop_pct) : null,
      // Survival gating judges the rupee curve AFTER costs, so costs are forced ON
      // whenever survival is enabled (O3 — the backend 400s otherwise).
      cost_config: (config.option_costs_enabled || config.survival_config?.enabled) ? {
        enabled: true,
        brokerage_per_order: Number(config.option_brokerage_per_order || 0),
        spread_pct_of_premium: Number(config.option_spread_pct || 0),
      } : null,
      // Capital basis for the survival gate's DD% / risk-of-ruin (O2).
      sizing_config: { capital: Math.max(1, Number(config.option_capital || 200000)) },
      ...(exitControlSearch ? { exit_control_search: exitControlSearch } : {}),
    };
  };

  const start = async () => {
    // Finalize the run name: an explicitly-set name is kept (with a dup warning),
    // otherwise a fresh descriptive+timestamp name so a forgotten default is unique.
    const auto = !nameTouchedRef.current;
    const finalName = auto ? autoRunName(config) : (config.name?.trim() || autoRunName(config));
    if (!auto) {
      const dup = (jobs || []).some((j) => (j.name || "") === finalName);
      if (dup && !window.confirm(`An optimization is already named "${finalName}".\nStart another with the same name?`)) return;
    }
    const afterStart = () => {
      nameTouchedRef.current = false;
      setConfig((c) => ({ ...c, name: autoRunName(c) }));
    };
    try {
      if (config.run_kind === "walkforward") {
        const payload = {
          instrument: config.instrument,
          mode: config.mode,
          strategy_id: config.strategy_id,
          method: config.method === "grid" ? "bayesian" : config.method,
          objective: config.objective,
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
          trade_window_start: config.trade_window_start || "09:25",
          trade_window_end: config.trade_window_end || "14:50",
          name: finalName,
          train_days: Number(config.wf_train_days) || 60,
          test_days: Number(config.wf_test_days) || 20,
          step_days: config.wf_step_days === "" ? null : Number(config.wf_step_days),
          wf_mode: config.wf_mode,
          n_trials_per_window: Number(config.wf_trials_per_window) || 40,
          max_windows: Number(config.wf_max_windows) || 12,
          // Opt-in parallel trial workers (per-window Bayesian search). 1 = sequential & reproducible.
          opt_workers: Number(config.opt_workers) || 1,
          option_aware: Boolean(config.wfo_option_aware),
          option_config: config.wfo_option_aware ? buildOptionConfig() : null,
        };
        const res = await api.startWfo(payload);
        setCurrentJobId(res.job_id);
        setCurrentJob({ id: res.job_id, status: "queued", kind: "wfo" });
        toast.success("Walk-forward optimization started");
        afterStart();
        return;
      }
      const optionRerank = config.evaluation_mode === "option_rerank";
      const optionConfig = optionRerank ? buildOptionConfig() : null;
      const payload = {
        instrument: config.instrument,
        mode: config.mode,
        strategy_id: config.strategy_id,
        method: config.method,
        objective: config.objective,
        n_trials: config.n_trials,
        // Parallel trial workers (bayesian only; backend ignores for grid).
        opt_workers: Number(config.opt_workers) || 1,
        // Auto-stop when converged — n_trials becomes a ceiling.
        early_stop: Boolean(config.early_stop),
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
        trade_window_start: config.trade_window_start || "09:25",
        trade_window_end: config.trade_window_end || "14:50",
        name: finalName,
        evaluation_mode: config.evaluation_mode,
        rerank_top_k: Math.max(1, Math.min(500, Number(config.rerank_top_k) || 50)),
        rerank_diversity: Boolean(config.rerank_diversity),
        // Analyzing budget in seconds (0 = unlimited). On hit the backend returns
        // the best evaluated so far and sets analyze_budget_hit on the result.
        analyze_budget_sec: Math.max(0, Number(config.analyze_budget_min ?? 30)) * 60,
        option_config: optionConfig,
        survival_config: optionRerank ? {
          enabled: Boolean(config.survival_config?.enabled),
          min_equity: Number(config.survival_config?.min_equity ?? 0),
          max_drawdown_pct: Number(config.survival_config?.max_drawdown_pct ?? 35),
          max_ror_pct: Number(config.survival_config?.max_ror_pct ?? 5),
          ruin_floor: Number(config.survival_config?.ruin_floor ?? 0),
          objective: config.survival_config?.objective ?? "calmar",
          min_oos_folds: config.survival_config?.min_oos_folds ?? "all",
        } : null,
        // Exit-control search: only meaningful when survival + option_rerank are on.
        search_exit_controls: optionRerank && Boolean(config.survival_config?.enabled) && Boolean(config.search_exit_controls),
      };
      const res = await api.startOptimization(payload);
      setCurrentJobId(res.job_id);
      setCurrentJob({ id: res.job_id, status: "queued" });
      toast.success("Optimization started");
      afterStart();
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

  const renamePreset = async (name) => {
    const raw = window.prompt(`Rename preset "${name}" to:`, name);
    if (raw == null) return;
    const next = raw.trim();
    if (!next || next === name) return;
    try {
      await api.renamePreset(name, next);
      await refreshPresets();
      toast.success(`Renamed "${name}" → "${next}"`);
    } catch (e) {
      toast.error("Rename failed: " + (e.response?.data?.detail || e.message));
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
    nameTouchedRef.current = true; // clone sets an explicit "(copy)" name — keep it
    setConfig((prev) => ({
      ...prev,
      run_kind: job.kind === "wfo" ? "walkforward" : "single",
      wf_train_days: c.train_days ?? prev.wf_train_days,
      wf_test_days: c.test_days ?? prev.wf_test_days,
      wf_step_days: c.step_days ?? "",
      wf_mode: c.wf_mode ?? prev.wf_mode,
      wf_trials_per_window: c.n_trials_per_window ?? prev.wf_trials_per_window,
      wf_max_windows: c.max_windows ?? prev.wf_max_windows,
      wfo_option_aware: job.kind === "wfo" ? Boolean(c.option_aware) : prev.wfo_option_aware,
      instrument: c.instrument ?? prev.instrument,
      mode: c.mode ?? prev.mode,
      strategy_id: c.strategy_id ?? prev.strategy_id,
      method: c.method ?? prev.method,
      objective: c.objective ?? prev.objective,
      n_trials: c.n_trials ?? prev.n_trials,
      opt_workers: c.opt_workers ?? prev.opt_workers,
      costs_enabled: c.costs_enabled ?? prev.costs_enabled,
      optimize_indicator_periods: !!c.optimize_indicator_periods,
      param_overrides: c.param_overrides || {},
      guards_enabled: (Number(c.min_trades || 0) > 0 || share > 0),
      min_trades: Number(c.min_trades ?? 10),
      min_direction_pct: Math.round(share * 100),
      pretrade_profile: c.pretrade_profile || "None",
      evaluation_mode: c.evaluation_mode || "spot",
      rerank_top_k: c.rerank_top_k ?? 50,
      rerank_diversity: c.rerank_diversity ?? false,
      analyze_budget_min: c.analyze_budget_sec != null ? Math.round(Number(c.analyze_budget_sec) / 60) : prev.analyze_budget_min,
      option_moneyness: c.option_config?.moneyness ?? prev.option_moneyness,
      option_dte_filter: parseDteFilter(c.option_config?.dte_filter),
      option_lots: c.option_config?.lots ?? prev.option_lots,
      option_exit_mode: c.option_config?.exit_mode ?? "spot_exit",
      option_sl_tp_unit: (c.option_config?.option_target_pts != null || c.option_config?.option_stop_pts != null) ? "pts" : "pct",
      option_target_pts: c.option_config?.option_target_pts ?? "",
      option_stop_pts: c.option_config?.option_stop_pts ?? "",
      option_target_pct: c.option_config?.option_target_pct ?? "",
      option_stop_pct: c.option_config?.option_stop_pct ?? "",
      option_costs_enabled: c.option_config?.cost_config?.enabled ?? prev.option_costs_enabled,
      option_brokerage_per_order: c.option_config?.cost_config?.brokerage_per_order ?? prev.option_brokerage_per_order,
      option_spread_pct: c.option_config?.cost_config?.spread_pct_of_premium ?? prev.option_spread_pct,
      option_capital: c.option_config?.sizing_config?.capital ?? prev.option_capital,
      start_date: c.start_ts ? msToDate(c.start_ts) : "",
      end_date: c.end_ts ? msToDate(c.end_ts) : "",
      trade_window_start: c.trade_window_start ?? prev.trade_window_start,
      trade_window_end: c.trade_window_end ?? prev.trade_window_end,
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
              <Label className="text-xs text-dim">Run name<Hint label="Run name">Labels this run in Job History. Leave it blank and it auto-stamps <code>strategy · instrument · objective · timestamp</code> so every run stays unique; type your own only when you want a memorable tag. A duplicate typed name prompts before starting.</Hint></Label>
              <Input
                value={config.name}
                onChange={(e) => { nameTouchedRef.current = true; setConfig({ ...config, name: e.target.value }); }}
                className="bg-bg-2 border-line h-8 mt-1"
                data-testid="opt-name-input"
              />
            </div>
            <Row label="Instrument" hint={<>Which index to optimize — NIFTY, BANKNIFTY or SENSEX. Liquidity and lot size differ, so params don't transfer between them. <b>Optimize the one you'll actually trade.</b> Drives the lot size used for the Net P&L (₹) objective.</>}>
              <Select value={config.instrument} onValueChange={(v) => setConfig({ ...config, instrument: v })}>
                <SelectTrigger className="bg-bg-2 border-line h-8" data-testid="opt-instrument-select"><SelectValue /></SelectTrigger>
                <SelectContent>
                  {INSTRUMENTS.map((i) => <SelectItem key={i} value={i}>{i}</SelectItem>)}
                </SelectContent>
              </Select>
            </Row>
            <Row label="Strategy" hint={<>The strategy plugin whose parameters get tuned. Only loaded, non-retired strategies appear. Pick the one you intend to deploy — its tunable params define the entire search space.</>}>
              <Select value={config.strategy_id} onValueChange={(v) => setConfig({ ...config, strategy_id: v })}>
                <SelectTrigger className="bg-bg-2 border-line h-8" data-testid="opt-strategy-select"><SelectValue /></SelectTrigger>
                <SelectContent>
                  {strategies.filter((s) => s.is_loaded !== false && !s.is_retired).map((s) => <SelectItem key={s.id} value={s.id}>{s.name}</SelectItem>)}
                </SelectContent>
              </Select>
            </Row>
            <Row label="Method" hint={<>How the search explores params. <b>Bayesian (TPE) is the default</b> — smart, focuses on promising regions, best for most runs. Grid is exhaustive (sampled if large); genetic (CMA-ES) suits many params. Switching method resets the trial budget to that method's default. <b>Grid is auto-swapped to Bayesian in walk-forward.</b></>}>
              <Select value={config.method} onValueChange={(v) => setConfig({ ...config, method: v, n_trials: METHODS.find(x => x.id === v)?.default_trials || 150 })}>
                <SelectTrigger className="bg-bg-2 border-line h-8" data-testid="opt-method-select"><SelectValue /></SelectTrigger>
                <SelectContent>
                  {METHODS.map((m) => <SelectItem key={m.id} value={m.id}>{m.name}</SelectItem>)}
                </SelectContent>
              </Select>
              <div className="text-[10px] text-dimmer mt-1">
                {METHODS.find((m) => m.id === config.method)?.desc}
                {config.run_kind === "walkforward" && config.method === "grid" && (
                  <span className="text-warning"> Walk-forward does not support Grid — Bayesian will be used.</span>
                )}
              </div>
            </Row>
            <Row label="Objective" hint={<>What the search maximizes. <b>Risk-Adjusted is the balanced default</b> (Sharpe penalized by drawdown). Net P&L (₹) and (pts) <b>want costs ON</b> to be honest — and (pts) <i>ignores drawdown</i>, so it can pick a high-variance fit. Minimize Max DD trades return for stability.</>}>
              <Select value={config.objective} onValueChange={(v) => setConfig({ ...config, objective: v })}>
                <SelectTrigger className="bg-bg-2 border-line h-8" data-testid="opt-objective-select"><SelectValue /></SelectTrigger>
                <SelectContent>
                  {OBJECTIVES.map((o) => <SelectItem key={o.id} value={o.id}>{o.name}</SelectItem>)}
                </SelectContent>
              </Select>
              <div className="text-[10px] text-dimmer mt-1">{OBJECTIVES.find((o) => o.id === config.objective)?.desc}</div>
            </Row>
            <Row label="Pre-trade profile" hint={<>Applies a pre-trade filter during every trial. <b>Use the same profile you'll backtest and trade with</b> so the optimized params match live behaviour; <b>None</b> optimizes the strategy's raw signals (params won't match a filtered live run).</>}>
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
            <Row label="Run type" hint={<><b>Single</b>: one fast search over the whole window — but the result is in-sample. <b>Walk-forward</b>: re-fits per train window and scores the unseen window after it, the honest out-of-sample answer. <b>Verify a Single run with walk-forward before trusting it</b> (note: grid becomes Bayesian here).</>}>
              <Select value={config.run_kind} onValueChange={(v) => setConfig({ ...config, run_kind: v })}>
                <SelectTrigger className="bg-bg-2 border-line h-8" data-testid="opt-run-kind-select"><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="single">Single optimization</SelectItem>
                  <SelectItem value="walkforward">Walk-forward (honest OOS)</SelectItem>
                </SelectContent>
              </Select>
              <div className="text-[10px] text-dimmer mt-1">
                {config.run_kind === "walkforward"
                  ? "Re-optimizes on each train window, evaluates on the UNSEEN window after it, stitches the out-of-sample results. The honest answer to \"would these params have worked?\""
                  : "One search over the whole date window. Fast, but the result is in-sample — verify with walk-forward before trusting it."}
              </div>
            </Row>

            {config.run_kind === "walkforward" && (
              <div className="rounded-md border border-emerald-500/30 bg-emerald-500/5 p-2 space-y-2" data-testid="opt-wf-config">
                <div className="text-[10px] uppercase tracking-wider text-emerald-400">
                  Walk-forward windows (trading days)
                  <Hint label="What is walk-forward">
                    Re-optimizes the strategy on each <b>Train</b> window, scores those settings on the next <b>unseen Test</b> window, and stitches all Test results into one honest out-of-sample record. Answers: <i>would these params have worked on data they weren't fitted to?</i> All days below are trading days actually present in your data (holiday-aware). With more windows than Max, the oldest are dropped — deployable params come from the most recent train window. Window re-optimization runs on spot evaluation.
                  </Hint>
                </div>
                <div className="grid grid-cols-2 gap-2">
                  <div>
                    <Label className="text-[11px] text-dim">Train days
                      <Hint label="Train days">
                        In-sample window the optimizer fits on. Bigger = steadier fit but fewer windows and slower to adapt. <b>Suggested: 40–60</b> (≈2–3 months) for intraday NIFTY.
                      </Hint>
                    </Label>
                    <Input type="number" min={20} max={250} value={config.wf_train_days}
                      onChange={(e) => setConfig({ ...config, wf_train_days: e.target.value })}
                      className="bg-bg-2 border-line h-8 text-xs font-mono mt-1" data-testid="opt-wf-train-days" />
                  </div>
                  <div>
                    <Label className="text-[11px] text-dim">Test days
                      <Hint label="Test days">
                        Unseen window each fit is scored on. Long enough for meaningful trades, short enough to stay recent — about ¼–⅓ of Train. <b>Suggested: 15–20.</b>
                      </Hint>
                    </Label>
                    <Input type="number" min={5} max={60} value={config.wf_test_days}
                      onChange={(e) => setConfig({ ...config, wf_test_days: e.target.value })}
                      className="bg-bg-2 border-line h-8 text-xs font-mono mt-1" data-testid="opt-wf-test-days" />
                  </div>
                  <div>
                    <Label className="text-[11px] text-dim">Step (blank = test days)
                      <Hint label="Step">
                        How far the window slides each cycle. <b>Leave blank</b> to step by Test days → back-to-back, non-overlapping tests (standard). Smaller = overlapping windows (more, but correlated). <b>Suggested: blank.</b>
                      </Hint>
                    </Label>
                    <Input type="number" min={1} max={60} value={config.wf_step_days} placeholder={String(config.wf_test_days)}
                      onChange={(e) => setConfig({ ...config, wf_step_days: e.target.value })}
                      className="bg-bg-2 border-line h-8 text-xs font-mono mt-1" data-testid="opt-wf-step-days" />
                  </div>
                  <div>
                    <Label className="text-[11px] text-dim">Window mode
                      <Hint label="Window mode">
                        <b>Rolling</b>: Train is a fixed size that slides forward (adapts to the latest regime). <b>Anchored</b>: Train grows from day one (more data, but old regimes dilute it). <b>Suggested: Rolling.</b>
                      </Hint>
                    </Label>
                    <Select value={config.wf_mode} onValueChange={(v) => setConfig({ ...config, wf_mode: v })}>
                      <SelectTrigger className="bg-bg-2 border-line h-8 mt-1" data-testid="opt-wf-mode"><SelectValue /></SelectTrigger>
                      <SelectContent>
                        <SelectItem value="rolling">Rolling (fixed train size)</SelectItem>
                        <SelectItem value="anchored">Anchored (growing train)</SelectItem>
                      </SelectContent>
                    </Select>
                  </div>
                  <div>
                    <Label className="text-[11px] text-dim">Trials per window
                      <Hint label="Trials per window">
                        Bayesian search trials per Train window. Too few underfits the search; too many overfits the train window and is slow. <b>Suggested: 40–80</b> (toward 80+ when tuning many params).
                      </Hint>
                    </Label>
                    <Input type="number" min={10} max={500} value={config.wf_trials_per_window}
                      onChange={(e) => setConfig({ ...config, wf_trials_per_window: e.target.value })}
                      className="bg-bg-2 border-line h-8 text-xs font-mono mt-1" data-testid="opt-wf-trials" />
                  </div>
                  <div>
                    <Label className="text-[11px] text-dim">Max windows
                      <Hint label="Max windows">
                        Caps the window count; if more fit, the oldest are dropped (recent data matters most; deployable params come from the last window). Your data + the suggested sizes give ~6–8 windows. <b>Suggested: 8–12</b> to keep them all.
                      </Hint>
                    </Label>
                    <Input type="number" min={2} max={36} value={config.wf_max_windows}
                      onChange={(e) => setConfig({ ...config, wf_max_windows: e.target.value })}
                      className="bg-bg-2 border-line h-8 text-xs font-mono mt-1" data-testid="opt-wf-max-windows" />
                  </div>
                </div>
                <div className="pt-1 border-t border-emerald-500/20">
                  <Label className="text-[11px] text-dim">Parallel workers
                    <Hint label="Parallel workers">
                      Runs several trial backtests at once to speed each window's search. <b>1 = sequential and reproducible</b> (best for the deploy decision); more than 1 is faster but makes the OOS result vary run-to-run. <b>Suggested: 1</b> (4–8 only for quick exploration).
                    </Hint>
                  </Label>
                  <Input
                    type="number" min={1} max={15}
                    value={config.opt_workers}
                    onChange={(e) => setConfig({ ...config, opt_workers: e.target.value })}
                    className="bg-bg-2 border-line h-8 text-xs font-mono mt-1 w-24"
                    data-testid="opt-wf-parallel-workers"
                  />
                  {Number(config.opt_workers) > 1 && (
                    <div className="text-[10px] text-warning mt-1 leading-snug" data-testid="opt-wf-workers-warning">
                      experimental · non-deterministic · more RAM. With parallel workers the OOS result and deployable params can vary run-to-run — walk-forward is your honest-OOS validation; use 1 worker for a reproducible deploy decision.
                    </div>
                  )}
                </div>
                <div className="flex items-center gap-2 pt-1 border-t border-emerald-500/20">
                  <Switch
                    checked={Boolean(config.wfo_option_aware)}
                    onCheckedChange={(v) => setConfig({ ...config, wfo_option_aware: v })}
                    data-testid="opt-wfo-option-aware"
                  />
                  <span className="text-[11px] text-dim">
                    <b>Option-aware OOS (₹)</b><Hint label="Option-aware OOS">After stitching the out-of-sample windows, re-pairs those OOS trades with <b>real option candles</b> and reports net rupee plus per-window rupee consistency beside the spot stitch — the check that your spot edge survives in actual option ₹. Uses the option sub-panel below (moneyness/DTE/lots/exit); it only reports, never fails or re-ranks the run, and data gaps degrade to a low-pairing note. <b>Suggested: ON.</b></Hint> — after stitching, pair the OOS trades with real option candles and report net rupee + per-window rupee consistency
                  </span>
                </div>
              </div>
            )}

            {config.run_kind !== "walkforward" && (
            <Row label="Evaluation" hint={<>How candidates are scored. <b>Option re-rank (default)</b> takes the top spot configs and re-scores them on REAL paired-option net rupee (delta/theta/costs) — the number you actually trade. <b>Spot points</b> ranks on the index backtest only (fast, but spot P&L can badly mislead for option buying — a run can score +289k on spot while losing 2 lakh on real options). <b>Keep Option re-rank for any deploy decision.</b></>}>
              <Select value={config.evaluation_mode} onValueChange={(v) => setConfig({ ...config, evaluation_mode: v })}>
                <SelectTrigger className="bg-bg-2 border-line h-8" data-testid="opt-eval-mode-select"><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="option_rerank">Option re-rank (realistic · default)</SelectItem>
                  <SelectItem value="spot">Spot points (fast, spot-only)</SelectItem>
                </SelectContent>
              </Select>
              <div className="text-[10px] text-dimmer mt-1">
                {config.evaluation_mode === "option_rerank"
                  ? "Default. Searches on spot, then re-ranks the top-K by REAL paired-option net rupee (delta/theta/costs) — the winner is chosen by what you actually trade."
                  : "Scores the index backtest only. Faster, but spot P&L can mislead for option buying — the winner may lose on real options."}
              </div>
            </Row>
            )}

            {((config.run_kind !== "walkforward" && config.evaluation_mode === "option_rerank")
              || (config.run_kind === "walkforward" && config.wfo_option_aware)) && (
              <div className="rounded-md border border-info/30 bg-info/5 p-2 space-y-2">
                <div className="text-[10px] uppercase tracking-wider text-info">
                  {config.run_kind === "walkforward" ? "Option execution (OOS rupee check)" : "Option execution (re-rank)"}
                </div>
                <div className="grid grid-cols-2 gap-2">
                  {config.run_kind !== "walkforward" && (
                  <div>
                    <Label className="text-[11px] text-dim">Re-rank top-K<Hint label="Re-rank top-K">How many top spot candidates get fully option-backtested (and survival-checked, if enabled) on real option P&L — every extra one costs Analyzing time. <b>~50 is plenty</b> (the default); above 80 turns amber and is slow. Clamped 1-500.</Hint></Label>
                    <Input type="number" min={1} max={500} value={config.rerank_top_k}
                      onChange={(e) => setConfig({ ...config, rerank_top_k: e.target.value })}
                      className="bg-bg-2 border-line h-8 text-xs font-mono mt-1" data-testid="opt-rerank-k" />
                    <div className={`text-[10px] mt-1 ${Number(config.rerank_top_k) > 80 ? "text-warning" : "text-dimmer"}`} data-testid="opt-rerank-k-hint">
                      Each candidate is fully option-backtested + survival-checked in Analyzing — higher = slower. ~50 is usually plenty.
                    </div>
                    <label className="flex items-center gap-1.5 mt-1.5 text-[10px] text-dim"
                      title="Also re-score a spread of lower-spot-ranked configs (same option-eval budget) so an option-best-but-spot-mediocre setup can surface.">
                      <input type="checkbox" checked={Boolean(config.rerank_diversity)}
                        onChange={(e) => setConfig({ ...config, rerank_diversity: e.target.checked })}
                        className="h-3 w-3 rounded border-line" data-testid="opt-rerank-diversity" />
                      Diversity shortlist<Hint label="Diversity shortlist">Also re-scores a spread of lower-spot-ranked configs (within the SAME top-K budget) so an option-best-but-spot-mediocre setup can still surface. <b>Default OFF</b> (pure top-K). Turn ON if spot and option rupee look poorly correlated; it keeps the top ~70% and evenly samples the rest.</Hint>
                    </label>
                  </div>
                  )}
                  {config.run_kind !== "walkforward" && (
                  <div>
                    <Label className="text-[11px] text-dim">Analyzing budget (min)<Hint label="Analyzing budget">Time cap (minutes) on the option re-rank + survival phase only. <b>0 = unlimited</b>; default is 30. On a hit it stops and returns the best evaluated so far (flagged), so a high Re-rank top-K can be cut short — leave headroom or set 0.</Hint></Label>
                    <Input type="number" min={0} max={240} value={config.analyze_budget_min}
                      onChange={(e) => setConfig({ ...config, analyze_budget_min: e.target.value })}
                      className="bg-bg-2 border-line h-8 text-xs font-mono mt-1" data-testid="opt-analyze-budget" />
                    <div className="text-[10px] text-dimmer mt-1 leading-snug">
                      0 = unlimited. On hit, returns the best evaluated so far.
                    </div>
                  </div>
                  )}
                  <div>
                    <Label className="text-[11px] text-dim">Moneyness<Hint label="Moneyness">Which strike the signals are paired with. <b>ATM</b> (default) is the most liquid with the tightest spread. <b>OTM</b> is cheaper premium but lower delta and more theta decay; <b>ITM</b> moves more with the index but costs more. <b>Keep ATM unless you have a reason.</b></Hint></Label>
                    <Select value={config.option_moneyness} onValueChange={(v) => setConfig({ ...config, option_moneyness: v })}>
                      <SelectTrigger className="bg-bg-2 border-line h-8 mt-1"><SelectValue /></SelectTrigger>
                      <SelectContent>{OPT_MONEYNESS.map((m) => <SelectItem key={m} value={m}>{m.toUpperCase()}</SelectItem>)}</SelectContent>
                    </Select>
                  </div>
                  <div>
                    <Label className="text-[11px] text-dim">DTE filter<Hint label="DTE filter">Which days-to-expiry sessions to include. <b>0 = expiry-day / 0DTE</b> (max gamma but brutal theta); higher numbers (up to 6) are steadier. <b>ALL</b> (default) uses every weekly session. The SAME filter is applied in survival, so a narrow pick means fewer paired trades to gate on.</Hint></Label>
                    <div className="flex flex-wrap items-center gap-1 mt-1" data-testid="opt-dte-multiselect">
                      <button
                        type="button"
                        onClick={() => setConfig({ ...config, option_dte_filter: [] })}
                        className={`px-1.5 py-1 rounded text-[10px] font-mono border ${(config.option_dte_filter || []).length === 0 ? "bg-info text-bg-0 border-info" : "bg-bg-2 text-dim border-line hover:text-foreground"}`}
                        title="Every weekly-expiry session"
                      >
                        ALL
                      </button>
                      {DTE_VALUES.map((d) => {
                        const selected = (config.option_dte_filter || []).includes(d);
                        return (
                          <button
                            key={d}
                            type="button"
                            onClick={() => {
                              const cur = new Set(config.option_dte_filter || []);
                              if (cur.has(d)) cur.delete(d); else cur.add(d);
                              setConfig({ ...config, option_dte_filter: [...cur].sort((a, b) => a - b) });
                            }}
                            className={`px-1.5 py-1 rounded text-[10px] font-mono border ${selected ? "bg-info text-bg-0 border-info" : "bg-bg-2 text-dim border-line hover:text-foreground"}`}
                            title={d === 0 ? "Expiry day (0DTE)" : `${d} trading day${d > 1 ? "s" : ""} before expiry`}
                          >
                            {d}
                          </button>
                        );
                      })}
                    </div>
                  </div>
                  <div>
                    <Label className="text-[11px] text-dim">Lots<Hint label="Lots">Position size in lots. <b>Default 1.</b> It scales the rupee P&L linearly but does NOT change the % / Sharpe / drawdown-% metrics — only raise it to see realistic rupee figures at your intended size.</Hint></Label>
                    <Input type="number" min={1} value={config.option_lots}
                      onChange={(e) => setConfig({ ...config, option_lots: e.target.value })}
                      className="bg-bg-2 border-line h-8 text-xs font-mono mt-1" />
                  </div>
                </div>
                <div>
                  <Label className="text-[11px] text-dim">Option exit<Hint label="Option exit">When the option leg closes. <b>Mirror spot exit</b> (default) closes the option when the index SL/target hits — clean and recommended. <b>Option premium SL/target</b> instead exits on the option's own premium levels, which only then enables the Target/Stop fields below.</Hint></Label>
                  <Select value={config.option_exit_mode} onValueChange={(v) => setConfig({ ...config, option_exit_mode: v })}>
                    <SelectTrigger className="bg-bg-2 border-line h-8 mt-1"><SelectValue /></SelectTrigger>
                    <SelectContent>
                      <SelectItem value="spot_exit">Mirror spot exit</SelectItem>
                      <SelectItem value="option_levels">Option premium SL/target</SelectItem>
                    </SelectContent>
                  </Select>
                </div>
                {config.option_exit_mode === "option_levels" && (
                  <>
                    <div className="flex items-center gap-2">
                      <span className="text-[11px] text-dim">Level unit<Hint label="Option exit levels">Premium exit levels for the option leg. <b>Only used when Option exit = Option premium SL/target</b> — ignored under Mirror spot exit. <b>pts</b> = premium points (e.g. +40 / -30); <b>pct</b> = % of entry premium (default). Set the Target above the Stop.</Hint></span>
                      <div className="flex rounded-md border border-line overflow-hidden">
                        {["pts", "pct"].map((u) => (
                          <button
                            key={u}
                            type="button"
                            onClick={() => setConfig({ ...config, option_sl_tp_unit: u })}
                            className={`px-2 py-1 text-[11px] font-mono ${config.option_sl_tp_unit === u ? "bg-info text-bg-0" : "bg-bg-2 text-dim hover:text-foreground"}`}
                            data-testid={`opt-option-unit-${u}`}
                          >
                            {u === "pts" ? "Points" : "Percent"}
                          </button>
                        ))}
                      </div>
                    </div>
                    <div className="grid grid-cols-2 gap-2">
                      <div>
                        <Label className="text-[11px] text-dim">Target ({config.option_sl_tp_unit === "pts" ? "pts of premium" : "% of premium"})</Label>
                        <Input type="number" min={0} step={config.option_sl_tp_unit === "pts" ? 0.5 : 5}
                          value={config.option_sl_tp_unit === "pts" ? config.option_target_pts : config.option_target_pct}
                          onChange={(e) => setConfig({
                            ...config,
                            [config.option_sl_tp_unit === "pts" ? "option_target_pts" : "option_target_pct"]: e.target.value,
                          })}
                          className="bg-bg-2 border-line h-8 text-xs font-mono mt-1" placeholder="e.g. 40" />
                      </div>
                      <div>
                        <Label className="text-[11px] text-dim">Stop ({config.option_sl_tp_unit === "pts" ? "pts of premium" : "% of premium"})</Label>
                        <Input type="number" min={0} step={config.option_sl_tp_unit === "pts" ? 0.5 : 5}
                          value={config.option_sl_tp_unit === "pts" ? config.option_stop_pts : config.option_stop_pct}
                          onChange={(e) => setConfig({
                            ...config,
                            [config.option_sl_tp_unit === "pts" ? "option_stop_pts" : "option_stop_pct"]: e.target.value,
                          })}
                          className="bg-bg-2 border-line h-8 text-xs font-mono mt-1" placeholder="e.g. 30" />
                      </div>
                    </div>
                  </>
                )}
                <div className="flex items-center gap-2">
                  <Switch
                    checked={config.option_costs_enabled || Boolean(config.survival_config?.enabled)}
                    disabled={Boolean(config.survival_config?.enabled)}
                    onCheckedChange={(v) => setConfig({ ...config, option_costs_enabled: v })}
                    data-testid="opt-rerank-costs" />
                  <span className="text-[11px] text-dim">
                    Apply option costs (charges + {config.option_spread_pct}% spread)
                    {config.survival_config?.enabled && <span className="text-purple-400"> — locked ON by Survivability</span>}
                  </span>
                  <Hint label="Apply option costs">Charges plus a spread % on the option leg, giving net (not gross) rupee. <b>Keep ON</b> (default) for realistic ranking — the survival gate also expects costs ON, so its survivors are judged AFTER costs per OOS fold. Turning Survivability on force-locks this switch.</Hint>
                </div>
                <div className="text-[10px] text-dimmer leading-snug">
                  Higher top-K = more candidates re-ranked on real option P&L (slower). Option candles are loaded once per run.
                </div>
              </div>
            )}

            {config.run_kind !== "walkforward" && config.evaluation_mode === "option_rerank" && (() => {
              const setSurvival = (patch) => setConfig((c) => ({
                ...c,
                survival_config: { ...c.survival_config, ...patch },
              }));
              return (
                <div className="rounded-md border border-purple-500/30 bg-purple-500/5 p-2 space-y-2" data-testid="opt-survival-panel">
                  <div className="text-[10px] uppercase tracking-wider text-purple-400">Survivability</div>
                  <label className="flex items-center gap-2 text-[11px] text-dim">
                    <input
                      type="checkbox"
                      checked={Boolean(config.survival_config?.enabled)}
                      onChange={(e) => setSurvival({ enabled: e.target.checked })}
                      className="h-3 w-3 rounded border-line"
                      data-testid="opt-survival-enabled"
                    />
                    Enable survival gating<Hint label="Enable survival gating">Re-scores each finalist on its actual rupee equity curve, per walk-forward OOS fold, and keeps only the ones that stay alive AND end profitable — the spot-points score can look great while the option ₹ curve quietly ruins you. <b>Requires Option re-rank; needs costs ON</b> (it's the rupee curve being judged). <b>Suggested: ON</b> for any deploy decision.</Hint>
                  </label>
                  {config.survival_config?.enabled && (
                    <>
                      <div className="text-[10px] text-dimmer leading-snug">
                        Requires option execution + costs ON. Gates finalists on the rupee equity curve, evaluated per walk-forward OOS fold.
                      </div>
                      <div>
                        <Label className="text-[11px] text-dim">Trading capital (₹)<Hint label="Trading capital">The account size the gate scales drawdown % and risk-of-ruin against — DD% is a fraction of THIS, and RoR paths start from it. <b>Default ₹2,00,000.</b> Set it to your real deployable capital: a smaller account makes every % gate stricter (a ₹35k drawdown is 70% of ₹50k but only 17.5% of ₹200k). Equity floor / ruin floor must be below it.</Hint></Label>
                        <Input
                          type="number"
                          min={1}
                          value={config.option_capital ?? 200000}
                          onChange={(e) => setConfig((c) => ({ ...c, option_capital: Number(e.target.value) }))}
                          className="bg-bg-2 border-line h-8 text-xs font-mono mt-1"
                          data-testid="opt-survival-capital"
                        />
                        {Number(config.survival_config?.min_equity ?? 0) >= Number(config.option_capital || 200000) && (
                          <span className="text-warning text-[10px] block mt-1">Equity floor ≥ capital — nothing can pass. Lower the floor or raise capital.</span>
                        )}
                      </div>
                      <div className="grid grid-cols-2 gap-2">
                        <div>
                          <Label className="text-[11px] text-dim">Equity floor (₹)<Hint label="Equity floor">Hard-reject any finalist whose account ₹ ever touches or drops to this level (the primary, first-checked gate, applied each OOS fold). <b>Default 0</b> = reject only if equity hits zero or goes negative. Set it to the smallest balance you could stomach mid-run, e.g. starting capital minus your max tolerable loss.</Hint></Label>
                          <Input
                            type="number"
                            value={config.survival_config?.min_equity ?? 0}
                            onChange={(e) => setSurvival({ min_equity: Number(e.target.value) })}
                            className="bg-bg-2 border-line h-8 text-xs font-mono mt-1"
                            data-testid="opt-survival-min-equity"
                          />
                        </div>
                        <div>
                          <Label className="text-[11px] text-dim">Max drawdown %<Hint label="Max drawdown %">Reject a finalist if its worst peak-to-trough drop on the rupee curve exceeds this percent (magnitude). Lower = stricter. <b>Default 35.</b> Checked after the equity floor and before risk-of-ruin, on every OOS fold.</Hint></Label>
                          <Input
                            type="number"
                            min={0} max={100} step={1}
                            value={config.survival_config?.max_drawdown_pct ?? 35}
                            onChange={(e) => setSurvival({ max_drawdown_pct: Number(e.target.value) })}
                            className="bg-bg-2 border-line h-8 text-xs font-mono mt-1"
                            data-testid="opt-survival-max-dd"
                          />
                        </div>
                        <div>
                          <Label className="text-[11px] text-dim">Max risk-of-ruin %<Hint label="Max risk-of-ruin %">Reject if the modelled chance of blowing the account (Monte-Carlo bootstrap of daily ₹, judged on its <i>upper</i> 95% bound — fail-closed) exceeds this. <b>Default 5; lower = stricter.</b> Needs ≥100 stitched-OOS paired option trades, else the finalist is flagged insufficient-sample (not passed).</Hint></Label>
                          <Input
                            type="number"
                            min={0} max={100} step={1}
                            value={config.survival_config?.max_ror_pct ?? 5}
                            onChange={(e) => setSurvival({ max_ror_pct: Number(e.target.value) })}
                            className="bg-bg-2 border-line h-8 text-xs font-mono mt-1"
                            data-testid="opt-survival-max-ror"
                          />
                        </div>
                        <div>
                          <Label className="text-[11px] text-dim">Objective<Hint label="Survival objective">How surviving finalists are ranked. <b>Calmar (default)</b> = return ÷ worst drawdown (denominator floored at 5%), so it prefers steady survivors over jackpot-or-bust ones; <b>Total ₹</b> = raw net rupees, ignoring the ride. Stick with Calmar unless you only care about the final number.</Hint></Label>
                          <Select
                            value={config.survival_config?.objective ?? "calmar"}
                            onValueChange={(v) => setSurvival({ objective: v })}
                          >
                            <SelectTrigger className="bg-bg-2 border-line h-8 mt-1" data-testid="opt-survival-objective">
                              <SelectValue />
                            </SelectTrigger>
                            <SelectContent>
                              <SelectItem value="calmar">Risk-adjusted (Calmar)</SelectItem>
                              <SelectItem value="net_inr">Total ₹</SelectItem>
                            </SelectContent>
                          </Select>
                        </div>
                      </div>
                    </>
                  )}
                </div>
              );
            })()}

            {config.run_kind !== "walkforward" && config.evaluation_mode === "option_rerank" && (
              <div className="rounded-md border border-amber-500/30 bg-amber-500/5 p-2 space-y-2" data-testid="opt-exit-search-panel">
                <div className="text-[10px] uppercase tracking-wider text-warning">Exit-Control Search</div>
                <label className="flex items-center gap-2 text-[11px] text-dim">
                  <input
                    type="checkbox"
                    checked={Boolean(config.search_exit_controls)}
                    onChange={(e) => setConfig({ ...config, search_exit_controls: e.target.checked })}
                    className="h-3 w-3 rounded border-line"
                    data-testid="search-exit-controls"
                  />
                  Auto-tune exit controls per survivor<Hint label="Auto-tune exit controls">For each survivor, sweeps a small grid of trailing-stop give-backs and breakeven triggers and adopts whichever <i>still survives</i>, stays profitable, and improves Calmar — saved as <code>chosen_exit_controls</code>. <b>Requires Survivability ON + Option re-rank; single runs only (not walk-forward).</b> Adds time (each survivor is re-simulated across the whole grid).</Hint>
                </label>
                <div className="text-[10px] text-dimmer leading-snug">
                  Requires Survivability ON + Option re-rank. For each surviving finalist the optimizer sweeps a grid of trailing-stop distances and breakeven-trigger levels and keeps the best-surviving config (saved as <code className="font-mono">chosen_exit_controls</code> on the result).
                  {(!config.survival_config?.enabled) && (
                    <span className="text-warning block mt-1"> Enable Survivability above to activate this search.</span>
                  )}
                </div>
                {config.search_exit_controls && config.survival_config?.enabled && (
                  <div className="space-y-2 pt-1 border-t border-amber-500/20">
                    <div className="text-[10px] text-dimmer">
                      Grid bounds (comma-separated fractions; leave blank to use backend defaults: trail_distance 0.20, 0.35; breakeven_trigger 0.0, 0.30 — fractions of premium when unit=pct, e.g. 0.20 = give back 20% of peak, 0.30 = 30% gain arms breakeven).
                    </div>
                    <div className="grid grid-cols-2 gap-2">
                      <div>
                        <Label className="text-[11px] text-dim">Trail distances (fraction)<Hint label="Trail distances">Comma-separated fractions of peak premium you'll give back before the trailing stop fires (e.g. 0.20 = give back 20% off the high). Smaller = tighter, locks gains sooner but stops out on noise. <b>Blank = backend default [0.20, 0.35].</b> Each value must be between 0 and 1 (a fraction).</Hint></Label>
                        <Input
                          value={config.exit_search_trail_distance}
                          onChange={(e) => setConfig({ ...config, exit_search_trail_distance: e.target.value })}
                          placeholder="e.g. 0.20, 0.30, 0.40"
                          className="bg-bg-2 border-line h-8 text-xs font-mono mt-1"
                          data-testid="exit-search-trail-distance"
                        />
                      </div>
                      <div>
                        <Label className="text-[11px] text-dim">BE trigger (fraction)<Hint label="BE trigger">Comma-separated premium-gain fractions that arm the breakeven stop (e.g. 0.30 = once +30%, lock the stop at entry). Each value must be strictly between 0 and 1 — a 0 you type is dropped, so you can't add a no-breakeven variant here. <b>Blank = backend default [0.0, 0.30]</b> (the default already includes a no-BE variant).</Hint></Label>
                        <Input
                          value={config.exit_search_breakeven_trigger}
                          onChange={(e) => setConfig({ ...config, exit_search_breakeven_trigger: e.target.value })}
                          placeholder="e.g. 0.0, 0.30"
                          className="bg-bg-2 border-line h-8 text-xs font-mono mt-1"
                          data-testid="exit-search-breakeven-trigger"
                        />
                      </div>
                    </div>
                  </div>
                )}
              </div>
            )}

            {config.run_kind !== "walkforward" && (
              <>
                <NumberSliderInput
                  label={<>Trial budget<Hint label="Trial budget">How many backtests the search runs (each trial = one param set). Scale it to how many params you're tuning. Beyond a few hundred for small spaces the gains flatten and overfitting risk rises (max 5000). <b>Default 150</b>; push higher with many params or indicator periods on. With Auto-stop ON this is a ceiling, not a target.</Hint></>}
                  value={config.n_trials}
                  min={10} max={5000} step={10} decimals={0}
                  onChange={(v) => setConfig({ ...config, n_trials: v })}
                  testid="opt-trials"
                />
                <div className="text-[10px] text-dimmer -mt-1 leading-snug">
                  Up to 5000. More trials ≠ better — beyond a few hundred the gains flatten for small spaces and overfitting risk rises. Scale the budget to how many params you're searching.
                </div>
                <div className="flex items-center gap-2 pt-1">
                  <Switch
                    checked={Boolean(config.early_stop)}
                    onCheckedChange={(v) => setConfig({ ...config, early_stop: v })}
                    data-testid="opt-early-stop"
                  />
                  <span className="text-xs text-dim">Auto-stop when converged</span>
                  <Hint label="Auto-stop when converged">Stops the search early once the best result plateaus, so <b>Trial budget becomes a ceiling, not a target</b> (it ends sooner if converged). <b>Default ON</b> — safe to leave on; it only skips trials that weren't improving.</Hint>
                </div>
                <div className="text-[10px] text-dimmer -mt-1 leading-snug">
                  n_trials becomes a ceiling — stops when the best plateaus.
                </div>
                {config.method === "bayesian" && (
                  <div>
                    <Label className="text-[11px] text-dim">Parallel workers<Hint label="Parallel workers">Runs trial backtests concurrently (<b>Bayesian only</b> — ignored for Grid, and the field hides for other methods). <b>Keep 1 for a deploy decision</b> — sequential and reproducible; more than 1 is experimental, non-deterministic (results vary run-to-run) and uses more RAM. <b>Default 1.</b></Hint></Label>
                    <Input
                      type="number" min={1} max={15}
                      value={config.opt_workers}
                      onChange={(e) => setConfig({ ...config, opt_workers: e.target.value })}
                      className="bg-bg-2 border-line h-8 text-xs font-mono mt-1"
                      data-testid="opt-parallel-workers"
                    />
                    <div className="text-[10px] text-dimmer mt-1 leading-snug">
                      experimental · non-deterministic · more RAM
                    </div>
                  </div>
                )}
              </>
            )}
            <div className="flex items-center gap-2 pt-1">
              <Switch checked={config.costs_enabled} onCheckedChange={(v) => setConfig({ ...config, costs_enabled: v })} data-testid="opt-costs-switch" />
              <span className="text-xs text-dim">Apply realistic costs</span>
              <Hint label="Apply realistic costs">Charges slippage and brokerage on the <i>spot</i> backtest so P&L reflects real fills. <b>Keep ON</b> for any rupee objective — <i>net ₹</i> just scales the cost-adjusted points by lot size. <b>Default ON.</b> (Option re-rank has its own separate option-cost toggle.)</Hint>
            </div>
            <div className="flex items-center gap-2">
              <Switch checked={config.optimize_indicator_periods} onCheckedChange={(v) => setConfig({ ...config, optimize_indicator_periods: v })} data-testid="opt-indicator-periods-switch" />
              <span className="text-xs text-dim">Optimize indicator periods</span>
              <Hint label="Optimize indicator periods">Also tunes RSI / MACD / ATR / EMA / ADX lengths (indicators are recomputed every trial). This enlarges the real search space and is slower, so <b>give it more trials</b> when on. <b>Default OFF.</b></Hint>
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
                <Hint label="Guard rails">Master switch for the two disqualifiers below (Min trades + Min CE/PE side %). <b>Default ON</b> — keeps the optimizer from "winning" with degenerate few-trade or all-CE/all-PE param sets. Off, only zero-trade sets are still rejected.</Hint>
              </div>
              {config.guards_enabled ? (
                <>
                  <div className="grid grid-cols-2 gap-2">
                    <div>
                      <Label className="text-[11px] text-dim">Min trades<Hint label="Min trades">Statistical-significance floor: any trial taking fewer trades than this is disqualified (so a lucky 2-trade run can't win). <b>Default 10.</b> Needs <b>Guard rails ON</b>.</Hint></Label>
                      <Input
                        type="number" min={0}
                        value={config.min_trades}
                        onChange={(e) => setConfig({ ...config, min_trades: e.target.value })}
                        className="bg-bg-2 border-line h-8 text-xs font-mono mt-1"
                        data-testid="opt-min-trades"
                      />
                    </div>
                    <div>
                      <Label className="text-[11px] text-dim">Min CE/PE side %<Hint label="Min CE/PE side %">Rejects one-sided winners: the minority side (CE vs PE) must be at least this % of trades, else disqualified. <b>0 = off (default)</b>; capped at <b>50</b> (a 50/50 split). Needs <b>Guard rails ON</b>.</Hint></Label>
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
              <Label className="text-xs text-dim">Date window (IST, optional)<Hint label="Date window">Limits which candles the search uses. <b>Leave blank for all available data.</b> A recent window reflects the current regime (good for a deploy decision); a longer window tests more conditions. End blank defaults to the latest candle.</Hint></Label>
              <div className="grid grid-cols-2 gap-2 mt-1">
                <Input type="date" value={config.start_date} onChange={(e) => setConfig({ ...config, start_date: e.target.value })} className="bg-bg-2 border-line h-8 text-xs" data-testid="opt-start-date" />
                <Input type="date" value={config.end_date} onChange={(e) => setConfig({ ...config, end_date: e.target.value })} className="bg-bg-2 border-line h-8 text-xs" data-testid="opt-end-date" />
              </div>
              <Label className="text-xs text-dim mt-2 block">Entry window (IST)<Hint label="Entry window">Only entries INSIDE this intraday window count. <b>Default 09:25–14:50</b> = the live-effective window every deployment enforces (it blocks the first 10 min and last 40 min), so the optimizer never rewards 14:50–15:00 entries live can never take. Exits are unaffected.</Hint></Label>
              <div className="grid grid-cols-2 gap-2 mt-1">
                <Input type="time" value={config.trade_window_start} onChange={(e) => setConfig({ ...config, trade_window_start: e.target.value })} className="bg-bg-2 border-line h-8 text-xs" data-testid="opt-trade-window-start" />
                <Input type="time" value={config.trade_window_end} onChange={(e) => setConfig({ ...config, trade_window_end: e.target.value })} className="bg-bg-2 border-line h-8 text-xs" data-testid="opt-trade-window-end" />
              </div>
              {config.trade_window_end > "14:50" && (
                <span className="text-warning text-[10px] block mt-1">Entries after 14:50 can't be taken live (deployments block the last 40 min) — results may overstate.</span>
              )}
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

        <PresetsPanel presets={presets} onLoadInLab={(name) => navigate(`/backtest?preset=${encodeURIComponent(name)}`)} onDeploy={(name) => navigate(`/live?preset=${encodeURIComponent(name)}`)} onRefresh={refreshPresets} onRename={renamePreset} onDelete={deletePreset} />
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

function Row({ label, hint, children }) {
  return (
    <div>
      <Label className="text-xs text-dim">{label}{hint && <Hint label={typeof label === "string" ? label : "help"}>{hint}</Hint>}</Label>
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
  const isWfo = job.kind === "wfo";
  const status = job.status;
  const finished = status === "done" || status === "done_no_survivor";
  const cancelled = status === "cancelled";
  const failed = status === "failed";
  const paused = status === "paused";
  const interrupted = status === "interrupted";
  const inProgress = status === "running" || status === "queued" || status === "analyzing";
  const resumable = paused || interrupted || failed;
  const hasBest = (bsf.params && Object.keys(bsf.params).length > 0)
    || (isWfo && job.best_params && Object.keys(job.best_params).length > 0);
  const showResults = finished || cancelled || resumable;
  const isOptionRerank = (job.evaluation_mode || job.config?.evaluation_mode) === "option_rerank";
  const optionPnl = job.best_option_pnl_value ?? job.best_metrics?.option_pnl_value ?? job.rerank?.ranked?.[0]?.option_pnl_value ?? null;
  // The SPOT search objective (the live "best so far" value during the trial loop).
  // NOT job.best_value, which for a survivor becomes the survival objective (e.g. calmar).
  const spotObjective = bsf.value;
  const fmtINR = (v) => (v == null ? "—" : `${v < 0 ? "-" : ""}₹${fmtNum(Math.abs(v), 0)}`);

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
          <span>
            {job.n_trials_completed || 0} / {job.n_trials_total || 0} trials
            {isWfo && job.wfo_progress && (
              <span className="text-emerald-400 ml-2">
                window {job.wfo_progress.window}/{job.wfo_progress.window_count}
              </span>
            )}
          </span>
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
          <div className="text-xs text-warning mt-2">Optimization was cancelled. Best result so far has been preserved.</div>
        )}
        {paused && (
          <div className="text-xs text-sky-300 mt-2">Paused at trial {job.n_trials_completed}/{job.n_trials_total}. Click Resume to continue from here.</div>
        )}
        {interrupted && (
          <div className="text-xs text-warning mt-2">Interrupted by a restart at trial {job.n_trials_completed}/{job.n_trials_total}. Click Resume to continue.</div>
        )}
        {status === "analyzing" && job.rerank_progress && (
          <div className="text-[11px] font-mono text-warning mt-2" data-testid="opt-analyze-eta">
            Analyzing {job.rerank_progress?.stage}: {job.rerank_progress?.done ?? 0}/{job.rerank_progress?.total ?? 0} · ETA {fmtEta(job.rerank_progress?.eta_sec)}
          </div>
        )}
        {finished && job.analyze_budget_hit && (
          <div className="text-xs text-warning mt-2 leading-snug" data-testid="opt-analyze-budget-hit">
            Analyzing budget hit — evaluated {job.analyzed_candidates ?? "?"} candidate(s). Raise the budget or lower Re-rank top-K for full coverage.
          </div>
        )}
      </div>

      {/* No usable result — every trial took no trades or failed the guard rails */}
      {(finished || cancelled) && !hasBest && (
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
            <Trophy className="w-4 h-4 text-warning" />
            <div className="text-xs font-semibold uppercase tracking-wider text-dim">Best so far</div>
            <div className="ml-auto text-right" data-testid="opt-best-headline">
              {isOptionRerank ? (
                optionPnl != null ? (
                  <>
                    <div className={`font-mono text-base ${optionPnl >= 0 ? "text-emerald-400" : "text-rose-400"}`} data-testid="opt-headline-value">{fmtINR(optionPnl)}</div>
                    <div className="text-[10px] text-dimmer">
                      {job.status === "done_no_survivor" ? "best candidate option ₹ · no survivor" : "promoted option ₹ (net of costs)"} · spot obj {fmtBest(spotObjective)}
                    </div>
                  </>
                ) : (
                  <>
                    <div className="font-mono text-base text-foreground" data-testid="opt-headline-value">{fmtBest(spotObjective)}</div>
                    <div className="text-[10px] text-dimmer">spot objective · option ₹ ranked after the search</div>
                  </>
                )
              ) : (
                <div className="font-mono text-base text-foreground" data-testid="opt-headline-value">{fmtBest(job.best_value ?? bsf.value)}</div>
              )}
            </div>
          </div>
          <div className="grid grid-cols-2 lg:grid-cols-3 gap-2 mb-3">
            {Object.entries(bsf.params).map(([k, v]) => (
              <div key={k} className="rounded-md bg-bg-2 border border-line p-2 text-xs">
                <div className="text-[10px] uppercase tracking-wider text-dimmer truncate">{k}</div>
                <div className="font-mono text-foreground mt-0.5 truncate">{typeof v === "number" ? v.toFixed(2) : String(v)}</div>
              </div>
            ))}
          </div>
          {isOptionRerank && <div className="text-[10px] text-dimmer mb-1">spot backtest metrics of the best config (not the option trade)</div>}
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
        isWfo ? (
          <WfoResults job={job} />
        ) : (
          <>
            {/* Trust verdict (Piece 3) for the promoted best — advisory, never blocks. */}
            {job.best_quality && <TrustScorecard quality={job.best_quality} />}
            {job.rerank ? (
              <RerankResults rerank={job.rerank} survivalSummary={job.survival_summary ?? job.rerank?.survival_summary} jobStatus={job.status} />
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
        )
      )}
    </div>
  );
}

// Compact SVG equity line for the stitched OOS curve (no chart dependency).
function OosEquitySparkline({ curve }) {
  if (!curve || curve.length < 2) return null;
  const W = 640, H = 120, PAD = 6;
  const vals = curve.map((p) => p.equity_pts);
  const lo = Math.min(0, ...vals);
  const hi = Math.max(0, ...vals);
  const span = hi - lo || 1;
  const x = (i) => PAD + (i / (curve.length - 1)) * (W - 2 * PAD);
  const y = (v) => PAD + (1 - (v - lo) / span) * (H - 2 * PAD);
  const pts = vals.map((v, i) => `${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(" ");
  const zeroY = y(0);
  const last = vals[vals.length - 1];
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="w-full h-28" preserveAspectRatio="none" data-testid="wfo-equity-sparkline">
      <line x1={PAD} y1={zeroY} x2={W - PAD} y2={zeroY} stroke="currentColor" strokeOpacity="0.2" strokeDasharray="4 4" />
      <polyline points={pts} fill="none" stroke={last >= 0 ? "#10b981" : "#f43f5e"} strokeWidth="1.5" />
    </svg>
  );
}

function WfoResults({ job }) {
  const wfo = job.wfo;
  if (!wfo) return null;
  const s = wfo.stitched_oos || {};
  const eff = wfo.efficiency;
  const cons = wfo.consistency || {};
  const windows = wfo.windows || [];
  const stability = wfo.param_stability || [];
  const effColor = eff == null ? "text-dim" : eff >= 0.7 ? "text-emerald-400" : eff >= 0.4 ? "text-warning" : "text-rose-400";
  const effLabel = eff == null ? "n/a" : eff.toFixed(2);
  return (
    <div className="space-y-3" data-testid="wfo-results">
      {/* Headline: the stitched OOS result is the number to believe */}
      <div className="rounded-lg border border-emerald-500/30 bg-bg-1 p-3">
        <div className="flex items-center gap-2 mb-2">
          <Trophy className="w-4 h-4 text-emerald-400" />
          <div className="text-xs font-semibold uppercase tracking-wider text-dim">Stitched Out-of-Sample Result</div>
          <div className="ml-auto text-[10px] text-dimmer">every trade below was earned on data the optimizer never saw</div>
        </div>
        <div className="grid grid-cols-3 lg:grid-cols-7 gap-2 text-xs mb-2">
          <SmallMetric label="OOS Net Pts" value={fmtNum(s.total_pnl_pts)} />
          <SmallMetric label="Trades" value={fmtInt(s.trade_count)} />
          <SmallMetric label="WinRate" value={fmtPct(s.win_rate)} />
          <SmallMetric label="PF" value={fmtNum(s.profit_factor)} />
          <SmallMetric label="MaxDD" value={fmtNum(s.max_dd_pts)} />
          <SmallMetric label="Sharpe" value={fmtNum(s.sharpe)} />
          <div className="rounded-md bg-bg-2 border border-line p-2">
            <div className="text-[10px] uppercase tracking-wider text-dimmer">WF Efficiency</div>
            <div className={`font-mono mt-0.5 ${effColor}`}>{effLabel}</div>
          </div>
        </div>
        <OosEquitySparkline curve={wfo.stitched_oos_equity} />
        <div className="flex items-center gap-3 text-[11px] text-dim mt-1 flex-wrap">
          <span>
            Consistency: <b className="text-foreground">{cons.positive_windows ?? 0}/{cons.windows ?? 0}</b> windows OOS-positive
            {cons.consistency_pct != null && <span className="text-dimmer"> ({cons.consistency_pct}%)</span>}
          </span>
          <span className="text-dimmer">
            WF efficiency = OOS pnl/day ÷ IS pnl/day. ≥0.7 strong · 0.4–0.7 some decay · &lt;0.4 likely overfit. Deployable params come from the most recent window (saved via Save as Preset / View Best in Lab).
          </span>
        </div>
      </div>

      {/* Option-aware OOS: the rupee reality check on the stitch */}
      {wfo.option_oos && wfo.option_oos.error && (
        <div className="rounded-lg border border-amber-500/40 bg-amber-500/5 p-3 text-[11px] text-warning" data-testid="wfo-option-oos-error">
          Option-aware OOS pairing failed: {String(wfo.option_oos.error)}. The spot stitch above is unaffected — check option-data coverage for the window.
        </div>
      )}
      {wfo.option_oos && !wfo.option_oos.error && (() => {
        const oo = wfo.option_oos;
        const rc = oo.rupee_consistency || {};
        const cov = oo.coverage || {};
        const covPct = cov.paired_trade_count != null && cov.spot_trade_count
          ? Math.round((cov.paired_trade_count / cov.spot_trade_count) * 100)
          : null;
        const netColor = (oo.net_pnl_value || 0) > 0 ? "text-emerald-400" : "text-rose-400";
        return (
          <div className="rounded-lg border border-info/30 bg-bg-1 p-3" data-testid="wfo-option-oos">
            <div className="text-xs font-semibold uppercase tracking-wider text-dim mb-2">
              Option OOS (₹ on real options)
              <span className="text-dimmer normal-case font-normal"> — same stitched trades, paired with {String(oo.config?.moneyness || "atm").toUpperCase()} option candles{oo.config?.costs_enabled ? ", costs on" : ", costs off"}</span>
            </div>
            <div className="grid grid-cols-3 lg:grid-cols-5 gap-2 text-xs mb-2">
              <div className="rounded-md bg-bg-2 border border-line p-2">
                <div className="text-[10px] uppercase tracking-wider text-dimmer">OOS Net ₹</div>
                <div className={`font-mono mt-0.5 ${netColor}`}>{Number(oo.net_pnl_value || 0).toLocaleString("en-IN", { maximumFractionDigits: 0 })}</div>
              </div>
              <SmallMetric label="Win Rate" value={fmtPct(oo.win_rate)} />
              <SmallMetric label="Paired" value={fmtInt(oo.paired_trade_count)} />
              <SmallMetric label="Charges ₹" value={Number(oo.total_charges || 0).toLocaleString("en-IN", { maximumFractionDigits: 0 })} />
              <SmallMetric label="Pairing" value={covPct != null ? `${covPct}%` : "—"} />
            </div>
            {(oo.per_window || []).length > 0 && (
              <div className="flex items-center gap-1 flex-wrap text-[10px] font-mono mb-1" title="Per-window OOS rupee P&L">
                {oo.per_window.map((r) => (
                  <span
                    key={r.index}
                    className={`px-1.5 py-0.5 rounded border ${r.paired_trade_count === 0 ? "border-line text-dimmer" : r.pnl_value > 0 ? "border-emerald-500/40 text-emerald-400" : "border-rose-500/40 text-rose-400"}`}
                  >
                    #{(r.index ?? 0) + 1}: {r.paired_trade_count === 0 ? "no trades" : `₹${Number(r.pnl_value).toLocaleString("en-IN", { maximumFractionDigits: 0 })}`}
                  </span>
                ))}
              </div>
            )}
            <div className="text-[11px] text-dim">
              Rupee consistency: <b className="text-foreground">{rc.positive_windows ?? 0}/{rc.windows_with_trades ?? 0}</b> windows ₹-positive.
              <span className="text-dimmer"> A spot-positive stitch with a negative rupee result means theta/spread/costs eat the edge — do not deploy on the spot number alone. Low pairing % means option data is missing for part of the OOS span.</span>
            </div>
          </div>
        );
      })()}

      {/* Final params (most recent train window) */}
      {wfo.final_params && (
        <div className="rounded-lg border border-line bg-bg-1 p-3" data-testid="wfo-final-params">
          <div className="text-xs font-semibold uppercase tracking-wider text-dim mb-2">
            Final Params <span className="text-dimmer normal-case">(re-optimized on the most recent train window #{(wfo.final_params_window ?? 0) + 1})</span>
          </div>
          <div className="grid grid-cols-2 lg:grid-cols-4 gap-2">
            {Object.entries(wfo.final_params).map(([k, v]) => (
              <div key={k} className="rounded-md bg-bg-2 border border-line p-2 text-xs">
                <div className="text-[10px] uppercase tracking-wider text-dimmer truncate">{k}</div>
                <div className="font-mono text-foreground mt-0.5 truncate">{typeof v === "number" ? v.toFixed(2) : String(v)}</div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Per-window table */}
      <div className="rounded-lg border border-line bg-bg-1" data-testid="wfo-window-table">
        <div className="px-3 py-2 border-b border-line text-xs font-semibold uppercase tracking-wider text-dim">
          Windows ({windows.length})
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead>
              <tr className="text-dim border-b border-line">
                <th className="text-left p-2">#</th>
                <th className="text-left p-2">Train</th>
                <th className="text-left p-2">Test (OOS)</th>
                <th className="text-right p-2">IS Obj</th>
                <th className="text-right p-2">IS Pts</th>
                <th className="text-right p-2">OOS Pts</th>
                <th className="text-right p-2">OOS Trades</th>
                <th className="text-right p-2">OOS WR</th>
                <th className="text-left p-2">Params</th>
              </tr>
            </thead>
            <tbody>
              {windows.map((w) => {
                const oosPts = w.oos_metrics?.total_pnl_pts;
                return (
                  <tr key={w.index} className="border-b border-line">
                    <td className="p-2 font-mono text-dim">{w.index + 1}</td>
                    <td className="p-2 font-mono text-dim whitespace-nowrap">{w.train_start} → {w.train_end}</td>
                    <td className="p-2 font-mono whitespace-nowrap">{w.test_start} → {w.test_end}</td>
                    {w.no_qualifying_params ? (
                      <td colSpan="6" className="p-2 text-warning">no qualifying params in this train window (guard rails) — no OOS trades taken</td>
                    ) : (
                      <>
                        <td className="p-2 font-mono text-right text-dim">{fmtBest(w.is_objective)}</td>
                        <td className="p-2 font-mono text-right text-dim">{fmtNum(w.is_metrics?.total_pnl_pts)}</td>
                        <td className={`p-2 font-mono text-right ${oosPts > 0 ? "text-emerald-400" : oosPts < 0 ? "text-rose-400" : ""}`}>{fmtNum(oosPts)}</td>
                        <td className="p-2 font-mono text-right">{fmtInt(w.oos_trade_count)}</td>
                        <td className="p-2 font-mono text-right">{fmtPct(w.oos_metrics?.win_rate)}</td>
                        <td className="p-2">
                          <details>
                            <summary className="cursor-pointer text-dim hover:text-foreground">view</summary>
                            <pre className="text-[10px] font-mono text-dimmer whitespace-pre-wrap max-w-[260px]">{JSON.stringify(w.best_params, null, 1)}</pre>
                          </details>
                        </td>
                      </>
                    )}
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>

      {/* Param stability across windows */}
      {stability.length > 0 && (
        <div className="rounded-lg border border-line bg-bg-1 p-3" data-testid="wfo-stability">
          <div className="text-xs font-semibold uppercase tracking-wider text-dim mb-1">Parameter Stability Across Windows</div>
          <div className="text-[10px] text-dimmer mb-2">
            Low spread = the optimizer keeps choosing the same value (robust). High spread = the value wanders window to window (fitted to noise — treat with suspicion).
          </div>
          <div className="space-y-1.5">
            {stability.map((row) => (
              <div key={row.param} className="grid grid-cols-[140px_1fr_120px] items-center gap-2 text-xs">
                <div className="font-mono text-dim truncate">{row.param}</div>
                <div className="h-2 bg-bg-2 rounded-sm overflow-hidden border border-line">
                  <div
                    className={`h-full ${row.rel_spread <= 0.25 ? "bg-emerald-600" : row.rel_spread <= 0.5 ? "bg-amber-500" : "bg-rose-600"}`}
                    style={{ width: `${Math.max(3, Math.round(row.rel_spread * 100))}%` }}
                  />
                </div>
                <div className="font-mono text-dim text-right">
                  {row.type === "bool"
                    ? `${row.agreement_pct}% agree`
                    : `med ${row.median} · ±${Math.round(row.rel_spread * 100)}%`}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function PresetsPanel({ presets, onLoadInLab, onDeploy, onRefresh, onRename, onDelete }) {
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
                    {p.config?.execution ? " · exec policy" : ""}
                  </div>
                </button>
                <button
                  onClick={() => onDeploy(p.name)}
                  className="px-2 self-stretch flex items-center text-dimmer hover:text-emerald-400 shrink-0"
                  data-testid={`preset-deploy-${p.name.replace(/[^a-z0-9]/gi, "_")}`}
                  title={`Deploy "${p.name}" — open the deployment form with this preset preselected (readiness evidence shown there)`}
                  aria-label={`Deploy preset ${p.name}`}
                >
                  <Rocket className="w-3.5 h-3.5" />
                </button>
                <button
                  onClick={() => onRename(p.name)}
                  className="px-2 self-stretch flex items-center text-dimmer hover:text-info shrink-0"
                  data-testid={`preset-rename-${p.name.replace(/[^a-z0-9]/gi, "_")}`}
                  title={`Rename preset "${p.name}"`}
                  aria-label={`Rename preset ${p.name}`}
                >
                  <Pencil className="w-3.5 h-3.5" />
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
    analyzing: { c: "bg-amber-950 text-warning border-amber-900 animate-pulse", label: "ANALYZING" },
    done: { c: "bg-emerald-950 text-emerald-200 border-emerald-900", label: "DONE" },
    done_no_survivor: { c: "bg-rose-950 text-rose-200 border-rose-900", label: "NO SURVIVOR" },
    cancelled: { c: "bg-amber-950 text-warning border-amber-900", label: "CANCELLED" },
    paused: { c: "bg-sky-950 text-sky-200 border-sky-900", label: "PAUSED" },
    interrupted: { c: "bg-orange-950 text-warning border-orange-900", label: "INTERRUPTED" },
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

// Inline SVG scatter: x = max drawdown %, y = total return %, coloured by survived.
function SurvivalScatter({ ranked }) {
  const points = ranked
    .filter((r) => r.survival)
    .map((r) => ({
      x: Math.abs(r.survival.max_dd_pct),
      y: r.survival.total_return_pct,
      survived: r.survival.survived,
    }));
  if (points.length === 0) return null;

  const W = 460, H = 300, PL = 48, PR = 16, PT = 24, PB = 36;
  const IW = W - PL - PR;
  const IH = H - PT - PB;

  const xs = points.map((p) => p.x);
  const ys = points.map((p) => p.y);
  const xMin = 0;
  const xMax = Math.max(...xs) * 1.1 || 10;
  const yMin = Math.min(0, ...ys) * 1.1 || -10;
  const yMax = Math.max(0, ...ys) * 1.1 || 10;

  const toSvgX = (v) => PL + ((v - xMin) / (xMax - xMin)) * IW;
  const toSvgY = (v) => PT + IH - ((v - yMin) / (yMax - yMin)) * IH;

  const zeroY = toSvgY(0);

  // Simple axis tick labels
  const xTicks = [0, xMax * 0.25, xMax * 0.5, xMax * 0.75, xMax].map((v) => ({
    v, sx: toSvgX(v),
  }));
  const yRange = yMax - yMin;
  const yTicks = [yMin, yMin + yRange * 0.25, yMin + yRange * 0.5, yMin + yRange * 0.75, yMax].map((v) => ({
    v, sy: toSvgY(v),
  }));

  return (
    <div className="rounded-lg border border-line bg-bg-1 p-3" data-testid="opt-survival-scatter">
      <div className="text-xs font-semibold uppercase tracking-wider text-dim mb-2">Return vs Drawdown (finalists)</div>
      <svg viewBox={`0 0 ${W} ${H}`} className="w-full" style={{ maxHeight: 300 }}>
        {/* zero line */}
        <line x1={PL} y1={zeroY} x2={W - PR} y2={zeroY} stroke="currentColor" strokeOpacity="0.15" strokeDasharray="4 3" />
        {/* axes */}
        <line x1={PL} y1={PT} x2={PL} y2={PT + IH} stroke="currentColor" strokeOpacity="0.3" />
        <line x1={PL} y1={PT + IH} x2={W - PR} y2={PT + IH} stroke="currentColor" strokeOpacity="0.3" />
        {/* x ticks */}
        {xTicks.map(({ v, sx }, i) => (
          <g key={i}>
            <line x1={sx} y1={PT + IH} x2={sx} y2={PT + IH + 4} stroke="currentColor" strokeOpacity="0.3" />
            <text x={sx} y={PT + IH + 14} textAnchor="middle" fontSize="9" fill="currentColor" fillOpacity="0.5">{v.toFixed(1)}</text>
          </g>
        ))}
        {/* y ticks */}
        {yTicks.map(({ v, sy }, i) => (
          <g key={i}>
            <line x1={PL - 4} y1={sy} x2={PL} y2={sy} stroke="currentColor" strokeOpacity="0.3" />
            <text x={PL - 6} y={sy + 3} textAnchor="end" fontSize="9" fill="currentColor" fillOpacity="0.5">{v.toFixed(1)}</text>
          </g>
        ))}
        {/* axis labels */}
        <text x={PL + IW / 2} y={H - 2} textAnchor="middle" fontSize="10" fill="currentColor" fillOpacity="0.6">Max DD %</text>
        <text x={10} y={PT + IH / 2} textAnchor="middle" fontSize="10" fill="currentColor" fillOpacity="0.6" transform={`rotate(-90, 10, ${PT + IH / 2})`}>Return %</text>
        {/* data points */}
        {points.map((p, i) => (
          <circle
            key={i}
            cx={toSvgX(p.x)}
            cy={toSvgY(p.y)}
            r={5}
            fill={p.survived ? "#10b981" : "#f43f5e"}
            fillOpacity={0.75}
            stroke={p.survived ? "#10b981" : "#f43f5e"}
            strokeWidth={1}
          />
        ))}
      </svg>
      <div className="flex items-center gap-4 mt-1 text-[10px] text-dimmer">
        <span className="flex items-center gap-1"><span className="inline-block w-2.5 h-2.5 rounded-full bg-emerald-500" />Survived</span>
        <span className="flex items-center gap-1"><span className="inline-block w-2.5 h-2.5 rounded-full bg-rose-500" />Disqualified</span>
      </div>
    </div>
  );
}

function RerankResults({ rerank, survivalSummary, jobStatus }) {
  const ranked = rerank?.ranked || [];

  // Zero-survivor banner — shown even when ranked list is empty (done_no_survivor path).
  const noSurvivor = jobStatus === "done_no_survivor" || survivalSummary?.survivors === 0;

  if (ranked.length === 0) {
    return (
      <div className="space-y-2">
        {noSurvivor && <NoSurvivorBanner summary={survivalSummary} />}
        <div className="rounded-lg border border-warning/40 bg-warning/10 p-3 text-xs text-warning" data-testid="opt-rerank-empty">
          Option re-rank produced no paired results — likely missing option data for this window/strikes. Check option-data coverage in the Data Warehouse, or widen moneyness/DTE.
        </div>
      </div>
    );
  }
  const fmtRs = (v) => {
    const n = Number(v || 0);
    const s = n < 0 ? "-" : "";
    return `${s}₹${Math.abs(n).toLocaleString("en-IN", { maximumFractionDigits: 0 })}`;
  };

  // Check whether any finalist has survival data — determines whether to show badge column + scatter.
  const hasSurvivalData = ranked.some((r) => r.survival);
  // Check whether any finalist has a chosen exit config (from exit-control search).
  const hasChosenExit = ranked.some((r) => r.chosen_exit_controls);

  return (
    <div className="space-y-2">
      {noSurvivor && <NoSurvivorBanner summary={survivalSummary} />}
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
                {hasSurvivalData && <th className="text-left p-2">Survival</th>}
                {hasChosenExit && <th className="text-left p-2">Auto-tuned exit</th>}
                <th className="text-left p-2">Params</th>
              </tr>
            </thead>
            <tbody>
              {ranked.map((r, i) => {
                const cov = r.coverage || {};
                const covPct = cov.spot_trade_count ? Math.round((cov.paired_trade_count / cov.spot_trade_count) * 100) : null;
                const pnl = Number(r.option_pnl_value || 0);
                const sv = r.survival;
                return (
                  <tr key={i} className={`border-b border-line ${i === 0 ? "bg-info/5" : ""}`}>
                    <td className="p-2 font-mono">{i + 1}</td>
                    <td className={`p-2 font-mono text-right font-semibold ${pnl >= 0 ? "text-success" : "text-danger"}`}>{fmtRs(pnl)}</td>
                    <td className="p-2 font-mono text-right">{fmtPct(r.option_win_rate)}</td>
                    <td className="p-2 font-mono text-right">{fmtInt(r.paired_trade_count)}/{fmtInt(r.spot_trade_count)}</td>
                    <td className="p-2 font-mono text-right text-dim">{fmtNum(r.spot_objective)}</td>
                    <td className={`p-2 font-mono text-right ${covPct != null && covPct < 80 ? "text-warning" : "text-dim"}`}>{covPct != null ? `${covPct}%` : "–"}</td>
                    {hasSurvivalData && (
                      <td className="p-2">
                        {sv ? (
                          sv.survived ? (
                            <span className="text-[10px] font-mono px-1.5 py-0.5 rounded border bg-emerald-950 text-emerald-200 border-emerald-900 whitespace-nowrap" data-testid="opt-survival-badge-survived">
                              Survived · Calmar {Number(sv.calmar).toFixed(2)} · DD {Math.abs(sv.max_dd_pct).toFixed(1)}% · RoR {Number(sv.ror_pct).toFixed(1)}%
                            </span>
                          ) : (
                            <span className="text-[10px] font-mono px-1.5 py-0.5 rounded border bg-rose-950 text-rose-200 border-rose-900 whitespace-nowrap" data-testid="opt-survival-badge-disqualified">
                              Disqualified · {sv.reason}
                            </span>
                          )
                        ) : (
                          <span className="text-[10px] text-dimmer">—</span>
                        )}
                      </td>
                    )}
                    {hasChosenExit && (
                      <td className="p-2" data-testid="finalist-chosen-exit">
                        {r.chosen_exit_controls ? (() => {
                          const ec = r.chosen_exit_controls;
                          const trail = ec.trailing?.distance ?? ec.trail_distance ?? null;
                          const be = ec.breakeven?.trigger ?? ec.breakeven_trigger ?? null;
                          const parts = [];
                          if (trail != null) parts.push(`trail ${trail}%`);
                          if (be != null) parts.push(`BE ${be}%`);
                          return (
                            <span className="text-[10px] font-mono px-1.5 py-0.5 rounded border bg-amber-950 text-warning border-amber-900 whitespace-nowrap">
                              auto-tuned: {parts.length > 0 ? parts.join(" / ") : "exit config"}
                            </span>
                          );
                        })() : (
                          <span className="text-[10px] text-dimmer">—</span>
                        )}
                      </td>
                    )}
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
      {hasSurvivalData && <SurvivalScatter ranked={ranked} />}
    </div>
  );
}

function NoSurvivorBanner({ summary }) {
  return (
    <div className="rounded-lg border border-rose-500/40 bg-rose-500/10 p-3 text-xs text-rose-300 space-y-2" data-testid="opt-no-survivor">
      <div className="font-semibold text-rose-200">No strategy survived your constraints.</div>
      {summary?.reason_counts && Object.keys(summary.reason_counts).length > 0 && (
        <div className="space-y-0.5 text-[11px] text-rose-300/80">
          {Object.entries(summary.reason_counts).map(([reason, n]) => (
            <div key={reason} className="font-mono">{reason}: {n}</div>
          ))}
        </div>
      )}
      {summary?.suggestions && summary.suggestions.length > 0 && (
        <ul className="list-disc list-inside space-y-0.5 text-[11px] text-rose-300/80">
          {summary.suggestions.map((s, i) => <li key={i}>{s}</li>)}
        </ul>
      )}
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
  const { panelRef, maximized, toggleMaximize } = useMaximize();
  const [filter, setFilter] = useState("");
  const { sort, onSort, sortRows } = useTableSort();
  const matches = (j) => {
    if (!filter) return true;
    const q = filter.toLowerCase();
    return [j.strategy_id, j.instrument, j.method, j.objective, j.status, j.name, j.kind === "wfo" ? "walk-fwd" : ""]
      .some((v) => String(v || "").toLowerCase().includes(q));
  };
  const sortValue = (j, key) => ({
    created: j.created_at, status: j.status, strategy: j.strategy_id, instrument: j.instrument,
    method: j.kind === "wfo" ? "walk-fwd" : j.method, objective: j.objective,
    trials: j.n_trials_completed || 0, best: j.best_so_far?.value,
  }[key]);
  const view = sortRows((jobs || []).filter(matches), sortValue);
  return (
    <div ref={panelRef} className="rounded-lg border border-line bg-bg-1 overflow-auto" data-testid="opt-job-history">
      <div className="px-3 py-2 border-b border-line flex items-center gap-2 flex-wrap">
        <Activity className="w-3.5 h-3.5 text-dim" />
        <div className="text-xs font-semibold uppercase tracking-wider text-dim">Job History</div>
        <div className="text-[11px] text-dimmer">{view.length} of {(jobs || []).length}</div>
        <div className="ml-auto flex items-center gap-1.5">
          <div className="relative">
            <Search className="absolute left-2 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-dimmer pointer-events-none" />
            <Input value={filter} onChange={(e) => setFilter(e.target.value)} placeholder="Filter strategy, instrument, status…" className="bg-bg-2 border-line h-7 text-xs pl-7 w-60" data-testid="opt-history-filter" />
            {filter && <button onClick={() => setFilter("")} className="absolute right-1.5 top-1/2 -translate-y-1/2 text-dimmer hover:text-foreground"><X className="w-3 h-3" /></button>}
          </div>
          <Button variant="ghost" size="sm" onClick={onRefresh} className="h-7 text-xs"><RefreshCw className="w-3 h-3" /></Button>
          <MaximizeButton maximized={maximized} onToggle={toggleMaximize} label="job history" testid="opt-job-history-maximize" />
        </div>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead>
            <tr className="text-dim border-b border-line">
              <th className="text-right p-2 w-10">#</th>
              <SortHeader col={{ key: "created", label: "Created", align: "left" }} sort={sort} onSort={onSort} testidPrefix="opt-history-sort" />
              <SortHeader col={{ key: "status", label: "Status", align: "left" }} sort={sort} onSort={onSort} testidPrefix="opt-history-sort" />
              <SortHeader col={{ key: "strategy", label: "Strategy", align: "left" }} sort={sort} onSort={onSort} testidPrefix="opt-history-sort" />
              <SortHeader col={{ key: "instrument", label: "Instr.", align: "left" }} sort={sort} onSort={onSort} testidPrefix="opt-history-sort" />
              <SortHeader col={{ key: "method", label: "Method", align: "left" }} sort={sort} onSort={onSort} testidPrefix="opt-history-sort" />
              <SortHeader col={{ key: "objective", label: "Objective", align: "left" }} sort={sort} onSort={onSort} testidPrefix="opt-history-sort" />
              <SortHeader col={{ key: "trials", label: "Trials", align: "right" }} sort={sort} onSort={onSort} testidPrefix="opt-history-sort" />
              <SortHeader col={{ key: "best", label: "Best", align: "right" }} sort={sort} onSort={onSort} testidPrefix="opt-history-sort" />
              <th className="p-2"></th>
            </tr>
          </thead>
          <tbody>
            {jobs.length === 0 && (
              <tr><td colSpan="10" className="p-4 text-center text-dimmer">No optimizations yet.</td></tr>
            )}
            {jobs.length > 0 && view.length === 0 && (
              <tr><td colSpan="10" className="p-4 text-center text-dimmer">No jobs match filter.</td></tr>
            )}
            {view.map((j, idx) => (
              <tr key={j.id} className="border-b border-line hover:bg-bg-2 cursor-pointer" onClick={() => onLoad(j.id)} data-testid="opt-history-row">
                <td className="p-2 font-mono text-dimmer text-right">{idx + 1}</td>
                <td className="p-2 font-mono text-dim">{isoToFull(j.created_at)}</td>
                <td className="p-2"><StatusBadge status={j.status} /></td>
                <td className="p-2 font-mono text-dim">{j.strategy_id}</td>
                <td className="p-2 font-mono">{j.instrument}</td>
                <td className="p-2 font-mono">
                  {j.kind === "wfo" ? <span className="text-emerald-400">walk-fwd</span> : j.method}
                </td>
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
