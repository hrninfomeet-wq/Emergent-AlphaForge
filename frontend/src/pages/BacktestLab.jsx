import { useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { toast } from "sonner";
import { api } from "@/lib/api";
import { fmtInt, fmtNum, fmtPct, fmtPnL, colorPnL, tsToTime } from "@/lib/fmt";
import { exportBacktestConfig, exportBacktestResult, exportTradesCsv } from "@/lib/exports";
import { Button } from "@/components/ui/button";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { Slider } from "@/components/ui/slider";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs";
import { Skeleton } from "@/components/ui/skeleton";
import { MetricCard } from "@/components/MetricCard";
import { RegimeBadge } from "@/components/RegimeBadge";
import { SignificanceBadge } from "@/components/SignificanceBadge";
import { PerformanceOverview } from "@/components/backtest/PerformanceOverview";
import { BacktestChart } from "@/components/backtest/BacktestChart";
import { TrustScorecard } from "@/components/TrustScorecard";
import { useMaximize, MaximizeButton } from "@/components/MaximizeButton";
import { buildPerformanceSeries } from "@/lib/backtestMetrics";
import { NumberSliderInput } from "@/components/NumberSliderInput";
import BacktestRunJournal from "@/components/BacktestRunJournal";
import { Play, Save, Filter, ChevronDown, ChevronRight, ChevronsUpDown, ArrowUp, ArrowDown, Download, FileJson, FileText, FolderOpen, ShieldCheck, Loader2, AlertTriangle, HelpCircle } from "lucide-react";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";
import { dateToMs, msToDate } from "@/lib/time";

const INSTRUMENTS = ["NIFTY", "BANKNIFTY", "SENSEX"];
const OPTION_MONEYNESS = ["atm", "otm1", "otm2", "otm3", "itm1", "itm2"];
const DTE_VALUES = [0, 1, 2, 3, 4, 5, 6];

// Canonical (sorted-key) JSON of a pretrade-filters object, for matching a saved
// run's stored filters back to a named profile so loading a run restores the
// profile that produced it (the run stores resolved filters, not the profile name).
const canonFilters = (o) => JSON.stringify(Object.fromEntries(Object.entries(o || {}).sort()));

// Auto run-name = descriptive (strategy · instrument) + a timestamp, so a forgotten
// default never collides and runs are identifiable in the journal / Load-past-run list.
const runNameStamp = () => {
  const d = new Date();
  const p = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
};
const autoRunName = (cfg) => `${cfg.strategy_id} · ${cfg.instrument} · ${runNameStamp()}`;

// DTE filter is a multi-select array of ints (empty = all). Older runs stored
// a single token ("dte2", "2") or null/"all" — normalize every shape here so
// cloning an old run still works.
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

// Persist the last viewed run id so the results survive tab navigation /
// unmount. We store only the id (not the heavy result payload) and re-hydrate
// from the backtest_runs API on mount.
const LAST_RUN_KEY = "alphaforge.backtest.lastRunId";

export default function BacktestLab() {
  const [strategies, setStrategies] = useState([]);
  const [profiles, setProfiles] = useState([]);
  const [pastRuns, setPastRuns] = useState([]);
  const [presets, setPresets] = useState([]);
  const [config, setConfig] = useState({
    instrument: "NIFTY",
    mode: "SCALP",
    strategy_id: "confluence_scalper",
    timeframe: "1m",
    params: {},
    costs_enabled: true,
    walkforward: true,
    train_pct: 0.6,
    n_folds: 3,
    pretrade_profile: "Balanced",
    name: "Untitled Run",
    start_date: "",  // YYYY-MM-DD (IST)
    end_date: "",
    trade_window_start: "09:25",
    trade_window_end: "15:00",
    // ON by default: rupee-honest paired-option results are the primary
    // workflow; flip off for spot-only research.
    option_backtest_enabled: true,
    option_expiry_mode: "auto",
    option_expiry_date: "",
    // ATM default: matches the warehouse's auto-maintained scope (Data Hygiene
    // keeps ATM CE/PE current) and the deployment default.
    option_moneyness: "atm",
    option_lots: 1,
    option_auto_fetch: true,
    option_exit_mode: "spot_exit",
    option_target_pts: "",
    option_stop_pts: "",
    option_target_pct: "",
    option_stop_pct: "",
    option_sl_tp_unit: "pts",
    // Multi-select DTE: array of ints (0..6). Empty = all weekly-expiry sessions.
    option_dte_filter: [],
    // Rupee cost model (opt-in). Flattrade brokerage = 0 by default.
    option_costs_enabled: false,
    option_brokerage_per_order: 0,
    option_spread_pct: 1.0,
    option_spread_min_pts: 0,
    // Position sizing + capital (opt-in). Lot size always from contract.
    option_sizing_enabled: false,
    option_sizing_mode: "premium_at_risk",
    option_capital: 200000,
    option_risk_per_trade_pct: 1.0,
    option_fixed_lots: 1,
    option_max_lots: 10,
    option_assumed_stop_pct: 50,
    // Exit / risk overlay (Piece 2 on the Backtest page). Off by default =>
    // buildPayload/buildExecution* emit no new keys => byte-identical. Fractions
    // for pct (0.25 = 25%), matching the deploy wizard + optimizer overlay panels.
    exit_controls_enabled: false,
    exit_controls_unit: "pct",
    breakeven_trigger: "",
    breakeven_lock: "",
    trailing_activation: "",
    trailing_distance: "",
    daily_cap_loss: "",
    daily_cap_target: "",
    daily_cap_max_trades: "",
  });
  const [running, setRunning] = useState(false);
  const [result, setResult] = useState(null);
  const [progress, setProgress] = useState(0);
  const [paramsOpen, setParamsOpen] = useState(true);
  const [showFiltersOpen, setShowFiltersOpen] = useState(false);

  // When a preset or past run is loaded it sets BOTH the strategy and its
  // params. But changing the strategy triggers the "reset params to defaults"
  // effect below, which would clobber those loaded params. We stash the
  // intended params here keyed by strategy id; the reset effect consumes them
  // instead of applying defaults for that one transition.
  const pendingParamsRef = useRef(null);
  // True once the user types their own run name (or a preset/past-run load set one),
  // so the auto-name regenerator leaves it alone. Reset after each completed run.
  const nameTouchedRef = useRef(false);

  const refreshRuns = () => api.listBacktestRuns(50).then((d) => setPastRuns(d.items || []));
  const refreshPresets = () => api.listPresets().then((d) => setPresets(d.items || []));

  const [searchParams, setSearchParams] = useSearchParams();

  useEffect(() => {
    api.listStrategies().then((d) => setStrategies(d.items || []));
    api.listProfiles().then((d) => setProfiles(d.items || []));
    refreshRuns();
    refreshPresets();
    // Re-hydrate the last viewed result so switching tabs and returning does
    // not blank the results panel. Deep-link (?run=) takes precedence and is
    // handled in the effect below, so skip rehydration when one is present.
    const sp = new URLSearchParams(window.location.search);
    if (!sp.get("run") && !sp.get("preset")) {
      const lastId = (() => { try { return localStorage.getItem(LAST_RUN_KEY); } catch { return null; } })();
      if (lastId) {
        api.getBacktestRun(lastId)
          .then((r) => setResult(r))
          .catch(() => { try { localStorage.removeItem(LAST_RUN_KEY); } catch { /* ignore */ } });
      }
    }
  }, []);

  // Persist the id of whatever result is currently shown so it can be restored
  // on remount. Storing only the id keeps localStorage small.
  useEffect(() => {
    try {
      if (result?.id) localStorage.setItem(LAST_RUN_KEY, result.id);
    } catch { /* ignore quota / privacy-mode errors */ }
  }, [result?.id]);

  // Deep-link: ?run=<id> auto-loads that run, ?preset=<name> applies preset
  useEffect(() => {
    const runId = searchParams.get("run");
    const presetName = searchParams.get("preset");
    if (runId) {
      loadPastRun(runId);
      setSearchParams({}, { replace: true });
    } else if (presetName) {
      applyPreset(presetName);
      setSearchParams({}, { replace: true });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [searchParams.get("run"), searchParams.get("preset")]);

  // Auto-fill the Run name with a fresh descriptive+timestamp default whenever the
  // strategy / instrument changes (and on mount) — unless the user typed their own
  // or a preset/past-run load set one. Stops a forgotten default from saving many
  // runs to the journal under one name.
  useEffect(() => {
    if (nameTouchedRef.current) return;
    setConfig((c) => ({ ...c, name: autoRunName(c) }));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [config.strategy_id, config.instrument]);

  const applyPreset = async (name) => {
    try {
      const list = presets.length ? presets : (await api.listPresets()).items;
      const p = list.find((x) => x.name === name);
      if (!p) { toast.error(`Preset "${name}" not found`); return; }
      const cfg = p.config || {};
      const targetStrategy = cfg.strategy_id || config.strategy_id;
      if (cfg.params) {
        pendingParamsRef.current = { strategy_id: targetStrategy, params: { ...cfg.params } };
      }
      // Execution policy travels with the preset: re-apply the option context
      // the result was validated under (moneyness, DTE, exit mode, levels,
      // costs) so a re-test runs under the same terms it was optimized under.
      nameTouchedRef.current = true; // applying a named preset sets an explicit name
      const ex = cfg.execution || null;
      const exFields = ex ? {
        option_backtest_enabled: true,
        option_moneyness: ex.moneyness || "atm",
        option_dte_filter: parseDteFilter(ex.dte_filter),
        option_lots: ex.lots || 1,
        option_exit_mode: ex.exit_mode || "spot_exit",
        option_sl_tp_unit: (ex.option_target_pts != null || ex.option_stop_pts != null) ? "pts" : "pct",
        option_target_pts: ex.option_target_pts ?? "",
        option_stop_pts: ex.option_stop_pts ?? "",
        option_target_pct: ex.option_target_pct ?? "",
        option_stop_pct: ex.option_stop_pct ?? "",
        option_costs_enabled: Boolean(ex.cost_config?.enabled),
        ...(ex.cost_config?.enabled ? {
          option_brokerage_per_order: ex.cost_config.brokerage_per_order ?? 0,
          option_spread_pct: ex.cost_config.spread_pct_of_premium ?? 1.0,
        } : {}),
        // Exit/risk overlay travels with the preset -> prefill the panel (fractions,
        // no conversion). Same null-tolerant shape the deploy wizard reads.
        exit_controls_enabled: Boolean(ex.exit_controls?.enabled),
        exit_controls_unit: ex.exit_controls?.unit || "pct",
        breakeven_trigger: ex.exit_controls?.breakeven?.trigger ?? "",
        breakeven_lock: ex.exit_controls?.breakeven?.lock ?? "",
        trailing_activation: ex.exit_controls?.trailing?.activation ?? "",
        trailing_distance: ex.exit_controls?.trailing?.distance ?? "",
        daily_cap_loss: ex.daily_caps?.loss ?? "",
        daily_cap_target: ex.daily_caps?.target ?? "",
        daily_cap_max_trades: ex.daily_caps?.max_trades ?? "",
      } : {};
      setConfig((c) => ({
        ...c,
        instrument: cfg.instrument || c.instrument,
        mode: cfg.mode || c.mode,
        strategy_id: cfg.strategy_id || c.strategy_id,
        params: cfg.params ? { ...cfg.params } : c.params,
        name: name,
        ...exFields,
      }));
      toast.success(ex
        ? `Preset "${name}" applied with its execution policy (option pairing on). Click Run Backtest.`
        : `Preset "${name}" applied. Click Run Backtest to test it.`);
    } catch (e) {
      toast.error("Failed to apply preset");
    }
  };

  // Derive the preset `execution` block from the current Option Execution form,
  // matching execution_from_option_config on the backend so the saved preset
  // re-applies in the Lab and prefills the deploy wizard identically.
  const buildExecutionFromConfig = () => {
    if (!config.option_backtest_enabled) return null;
    const ex = {
      moneyness: config.option_moneyness || "atm",
      dte_filter: parseDteFilter(config.option_dte_filter),
      exit_mode: config.option_exit_mode || "spot_exit",
      lots: Math.max(1, Number(config.option_lots || 1)),
    };
    if (config.option_exit_mode === "option_levels") {
      if (config.option_sl_tp_unit === "pts") {
        if (config.option_target_pts !== "") ex.option_target_pts = Number(config.option_target_pts);
        if (config.option_stop_pts !== "") ex.option_stop_pts = Number(config.option_stop_pts);
      } else {
        if (config.option_target_pct !== "") ex.option_target_pct = Number(config.option_target_pct);
        if (config.option_stop_pct !== "") ex.option_stop_pct = Number(config.option_stop_pct);
      }
    }
    if (config.option_costs_enabled) {
      ex.cost_config = {
        enabled: true,
        brokerage_per_order: Number(config.option_brokerage_per_order || 0),
        spread_pct_of_premium: Number(config.option_spread_pct || 0),
      };
    }
    // Exit/risk overlay -> execution (same nested shape the deploy wizard reads).
    // exit_controls only under option_levels (premium trailing); daily_caps whenever
    // a cap is set, regardless of exit_mode (governor-vs-trail split).
    if (config.exit_controls_enabled && config.option_exit_mode === "option_levels") {
      ex.exit_controls = {
        enabled: true,
        unit: config.exit_controls_unit,
        breakeven: (config.breakeven_trigger !== "" || config.breakeven_lock !== "")
          ? {
              trigger: config.breakeven_trigger !== "" ? Number(config.breakeven_trigger) : null,
              lock: config.breakeven_lock !== "" ? Number(config.breakeven_lock) : null,
            }
          : null,
        trailing: (config.trailing_activation !== "" || config.trailing_distance !== "")
          ? {
              activation: config.trailing_activation !== "" ? Number(config.trailing_activation) : null,
              distance: config.trailing_distance !== "" ? Number(config.trailing_distance) : null,
            }
          : null,
      };
    }
    if (config.daily_cap_loss !== "" || config.daily_cap_target !== "" || config.daily_cap_max_trades !== "") {
      ex.daily_caps = {
        loss: config.daily_cap_loss !== "" ? Number(config.daily_cap_loss) : null,
        target: config.daily_cap_target !== "" ? Number(config.daily_cap_target) : null,
        max_trades: config.daily_cap_max_trades !== "" ? Math.max(0, parseInt(config.daily_cap_max_trades, 10) || 0) : null,
      };
    }
    return ex;
  };

  // Save the current Backtest Lab setup (params + option execution/exit policy)
  // as a named preset so it can be re-tested and deployed as-is.
  const saveAsPreset = async () => {
    const suggested = config.name && config.name !== "Untitled Run"
      ? config.name
      : `${config.strategy_id} ${new Date().toISOString().slice(0, 10)}`;
    const raw = window.prompt("Save current setup as a preset (name):", suggested);
    if (raw == null) return;
    const name = raw.trim();
    if (!name) { toast.error("Preset name cannot be empty."); return; }
    if (presets.some((p) => p.name === name) && !window.confirm(`Preset "${name}" already exists. Overwrite it?`)) return;
    const cfg = {
      strategy_id: config.strategy_id,
      instrument: config.instrument,
      mode: config.mode,
      params: { ...config.params },
      source: "backtest",  // origin tag for the Saved Presets page grouping
    };
    const ex = buildExecutionFromConfig();
    if (ex) cfg.execution = ex;
    try {
      await api.savePreset(name, cfg);
      await refreshPresets();
      toast.success(`Saved preset "${name}"${ex ? " with execution policy" : ""}. Deploy it from Live Signals.`);
    } catch (e) {
      toast.error(`Save failed: ${e.response?.data?.detail || e.message}`);
    }
  };

  // Build the preset `execution` block from a LOADED RUN DOC (not the editable
  // form), so "Save as preset" on a result always reflects what produced the
  // displayed result. Same shape as buildExecutionFromConfig / the backend's
  // execution_from_option_config, so it re-applies in the Lab and prefills the
  // deploy wizard identically.
  const buildExecutionFromRun = (run) => {
    const ob = run?.config?.option_backtest;
    if (!ob?.enabled) return null;
    const ex = {
      moneyness: ob.moneyness || "atm",
      dte_filter: parseDteFilter(ob.dte_filter),
      exit_mode: ob.exit_mode || "spot_exit",
      lots: Math.max(1, Number(ob.lots || 1)),
    };
    if ((ob.exit_mode || "spot_exit") === "option_levels") {
      if (ob.option_target_pts != null) ex.option_target_pts = Number(ob.option_target_pts);
      if (ob.option_stop_pts != null) ex.option_stop_pts = Number(ob.option_stop_pts);
      if (ob.option_target_pct != null) ex.option_target_pct = Number(ob.option_target_pct);
      if (ob.option_stop_pct != null) ex.option_stop_pct = Number(ob.option_stop_pct);
    }
    if (ob.cost_config?.enabled) {
      ex.cost_config = {
        enabled: true,
        brokerage_per_order: Number(ob.cost_config.brokerage_per_order || 0),
        spread_pct_of_premium: Number(ob.cost_config.spread_pct_of_premium || 0),
      };
    }
    // Exit/risk overlay from the RUN DOC -> execution (same shape the deploy wizard
    // reads). exit_controls only under option_levels; daily_caps regardless. Sourced
    // from ob.exit_controls/ob.daily_caps (round-tripped via OptionBacktestReq).
    const rec = ob.exit_controls;
    if (rec?.enabled && (ob.exit_mode || "spot_exit") === "option_levels") {
      ex.exit_controls = {
        enabled: true,
        unit: rec.unit || "pct",
        breakeven: rec.breakeven
          ? { trigger: rec.breakeven.trigger ?? null, lock: rec.breakeven.lock ?? null }
          : null,
        trailing: rec.trailing
          ? { activation: rec.trailing.activation ?? null, distance: rec.trailing.distance ?? null }
          : null,
      };
    }
    const rdc = ob.daily_caps;
    if (rdc && (rdc.loss != null || rdc.target != null || rdc.max_trades != null)) {
      ex.daily_caps = {
        loss: rdc.loss ?? null,
        target: rdc.target ?? null,
        max_trades: rdc.max_trades ?? null,
      };
    }
    return ex;
  };

  // Save THIS loaded result's exact strategy params + option execution as a
  // named preset, read from the run doc so it matches the displayed result even
  // if the setup form was edited afterwards. The preset deploys as-is; to replicate
  // the BACKTEST, re-run the loaded run as-is — the ?run= load restores params +
  // date window + option execution + the matching pretrade profile. The preset
  // itself carries no dates (presets are for live deployment, which has no window).
  const savePresetFromResult = async (run) => {
    if (!run) return;
    const strategy_id = run.strategy_id || run.config?.strategy_id;
    const params = run.params_applied || run.config?.params;
    if (!strategy_id || !params) { toast.error("This result has no saved params to preset."); return; }
    const suggested = run.name && run.name !== "Untitled Run"
      ? run.name
      : `${strategy_id} ${new Date().toISOString().slice(0, 10)}`;
    const raw = window.prompt("Save THIS result's strategy params + option execution as a preset (name):", suggested);
    if (raw == null) return;
    const name = raw.trim();
    if (!name) { toast.error("Preset name cannot be empty."); return; }
    if (presets.some((p) => p.name === name) && !window.confirm(`Preset "${name}" already exists. Overwrite it?`)) return;
    const cfg = {
      strategy_id,
      instrument: run.instrument || run.config?.instrument,
      mode: run.config?.mode || config.mode,
      params: { ...params },
      source: "backtest",  // origin tag for the Saved Presets page grouping
    };
    const ex = buildExecutionFromRun(run);
    if (ex) cfg.execution = ex;
    try {
      await api.savePreset(name, cfg);
      await refreshPresets();
      toast.success(`Saved preset "${name}"${ex ? " with this run's option execution" : " (spot params only — option execution was off)"}. Deploy it from Live Signals.`);
    } catch (e) {
      toast.error(`Save failed: ${e.response?.data?.detail || e.message}`);
    }
  };

  const selectedStrategy = useMemo(
    () => strategies.find((s) => s.id === config.strategy_id),
    [strategies, config.strategy_id]
  );
  const selectedProfile = profiles.find((p) => p.name === config.pretrade_profile);

  // Reset params when strategy changes — unless a preset/past-run load stashed
  // the params it wants for this strategy (then apply those instead of defaults).
  useEffect(() => {
    if (!selectedStrategy) return;
    const pending = pendingParamsRef.current;
    pendingParamsRef.current = null; // consume on every strategy transition
    if (pending && pending.strategy_id === selectedStrategy.id && pending.params) {
      setConfig((c) => ({ ...c, params: { ...pending.params } }));
      return;
    }
    const defaults = {};
    for (const [k, def] of Object.entries(selectedStrategy.parameter_schema || {})) {
      defaults[k] = def.default;
    }
    setConfig((c) => ({ ...c, params: defaults }));
    // Intentionally keyed only on the strategy id: reset params once per strategy
    // transition, not on every `strategies` refresh (which re-creates the memoized
    // selectedStrategy object and would wipe edited params).
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedStrategy?.id]);

  const setParam = (k, v) => setConfig((c) => ({ ...c, params: { ...c.params, [k]: v } }));

  const buildPayload = () => ({
    instrument: config.instrument,
    mode: config.mode,
    strategy_id: config.strategy_id,
    timeframe: config.timeframe,
    params: config.params,
    costs_enabled: config.costs_enabled,
    walkforward: config.walkforward,
    train_pct: config.train_pct,
    n_folds: config.n_folds,
    pretrade_filters: selectedProfile?.settings || {},
    name: config.name,
    start_ts: dateToMs(config.start_date, false),
    end_ts: dateToMs(config.end_date, true),
    trade_window_start: config.trade_window_start || "09:25",
    trade_window_end: config.trade_window_end || "15:00",
    option_backtest: {
      enabled: !!config.option_backtest_enabled,
      expiry_date: config.option_expiry_mode === "fixed" ? (config.option_expiry_date || null) : null,
      moneyness: config.option_moneyness,
      lots: Math.max(1, Number(config.option_lots || 1)),
      entry_max_age_sec: 120,
      exit_max_age_sec: 180,
      auto_fetch: !!config.option_auto_fetch,
      max_auto_fetch_contracts: 60,
      exit_mode: config.option_exit_mode,
      option_target_pts: config.option_exit_mode === "option_levels" && config.option_sl_tp_unit === "pts" && config.option_target_pts !== "" ? Number(config.option_target_pts) : null,
      option_stop_pts: config.option_exit_mode === "option_levels" && config.option_sl_tp_unit === "pts" && config.option_stop_pts !== "" ? Number(config.option_stop_pts) : null,
      option_target_pct: config.option_exit_mode === "option_levels" && config.option_sl_tp_unit === "pct" && config.option_target_pct !== "" ? Number(config.option_target_pct) : null,
      option_stop_pct: config.option_exit_mode === "option_levels" && config.option_sl_tp_unit === "pct" && config.option_stop_pct !== "" ? Number(config.option_stop_pct) : null,
      dte_filter: Array.isArray(config.option_dte_filter)
        && config.option_dte_filter.length > 0
        && config.option_dte_filter.length < DTE_VALUES.length
        ? config.option_dte_filter
        : null,
      cost_config: config.option_costs_enabled ? {
        enabled: true,
        brokerage_per_order: Number(config.option_brokerage_per_order || 0),
        spread_pct_of_premium: Number(config.option_spread_pct || 0),
        spread_min_pts: Number(config.option_spread_min_pts || 0),
      } : null,
      sizing_config: config.option_sizing_enabled ? {
        enabled: true,
        mode: config.option_sizing_mode,
        capital: Number(config.option_capital || 200000),
        risk_per_trade_pct: Number(config.option_risk_per_trade_pct || 1),
        fixed_lots: Math.max(1, Number(config.option_fixed_lots || 1)),
        max_lots: Math.max(1, Number(config.option_max_lots || 10)),
        assumed_stop_pct_of_premium: Number(config.option_assumed_stop_pct || 50),
      } : null,
      // Exit/risk overlay — gated emission (off => key absent, never {}). exit_controls
      // only under option_levels (premium trailing is impossible spot-only); daily_caps
      // whenever a cap is set (the governor runs regardless of exit_mode). Breakeven/
      // trailing emitted as numeric-only dicts (ExitControlsReq sub-models reject null).
      ...(config.exit_controls_enabled && config.option_exit_mode === "option_levels"
        ? {
            exit_controls: (() => {
              const ec = { enabled: true, unit: config.exit_controls_unit };
              const be = {};
              if (config.breakeven_trigger !== "") be.trigger = Number(config.breakeven_trigger);
              if (config.breakeven_lock !== "") be.lock = Number(config.breakeven_lock);
              if (Object.keys(be).length) ec.breakeven = be;
              const tr = {};
              if (config.trailing_activation !== "") tr.activation = Number(config.trailing_activation);
              if (config.trailing_distance !== "") tr.distance = Number(config.trailing_distance);
              if (Object.keys(tr).length) ec.trailing = tr;
              return ec;
            })(),
          }
        : {}),
      ...((config.daily_cap_loss !== "" || config.daily_cap_target !== "" || config.daily_cap_max_trades !== "")
        ? {
            daily_caps: {
              loss: config.daily_cap_loss !== "" ? Number(config.daily_cap_loss) : null,
              target: config.daily_cap_target !== "" ? Number(config.daily_cap_target) : null,
              max_trades: config.daily_cap_max_trades !== "" ? Math.max(0, parseInt(config.daily_cap_max_trades, 10) || 0) : null,
            },
          }
        : {}),
    },
  });

  const [preflight, setPreflight] = useState(null);
  const [preflighting, setPreflighting] = useState(false);

  const checkOptionData = async (ingest = false) => {
    if (!config.option_backtest_enabled) {
      toast.info("Enable Option Execution first to check option data.");
      return;
    }
    setPreflighting(true);
    try {
      const res = await api.optionPreflight(buildPayload(), ingest);
      setPreflight(res);
      if (res.enabled === false) {
        toast.info("Option execution is off.");
      } else if (res.ingest?.status === "started") {
        toast.success(`Coverage ${res.coverage_pct}% · ingesting missing option data (run ${res.ingest.run_id.slice(0, 8)})`);
      } else {
        toast.success(`Option data coverage: ${res.would_pair}/${res.total_spot_trades} signals (${res.coverage_pct}%)`);
      }
    } catch (e) {
      toast.error(`Preflight failed: ${e.response?.data?.detail || e.message}`);
    } finally {
      setPreflighting(false);
    }
  };

  // While a preflight ingest run is active, poll it; when it lands, re-check
  // coverage automatically (closes the old "re-check in a minute" manual loop).
  const ingestRunId = preflight?.ingest?.status === "started" ? preflight.ingest.run_id : null;
  useEffect(() => {
    if (!ingestRunId) return undefined;
    let stopped = false;
    const timer = setInterval(async () => {
      try {
        const job = await api.preflightIngestJob(ingestRunId);
        if (stopped) return;
        setPreflight((p) =>
          p?.ingest?.run_id === ingestRunId
            ? { ...p, ingest: { ...p.ingest, progress_pct: job.progress_pct, stage: job.stage, job_status: job.status, error: job.error } }
            : p
        );
        if (["ok", "partial", "failed", "empty"].includes(String(job.status))) {
          clearInterval(timer);
          stopped = true;
          if (job.status === "ok" || job.status === "partial") {
            toast.success("Option ingest finished — re-checking coverage…");
            checkOptionData(false);
          } else {
            toast.error(`Option ingest ${job.status}${job.error ? `: ${job.error}` : ""}`);
            setPreflight((p) =>
              p?.ingest?.run_id === ingestRunId
                ? { ...p, ingest: { ...p.ingest, status: "failed", error: job.error } }
                : p
            );
          }
        }
      } catch {
        /* transient poll errors: keep polling */
      }
    }, 5000);
    return () => {
      stopped = true;
      clearInterval(timer);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ingestRunId]);

  const runBacktest = async () => {
    // Finalize the run name: keep an explicitly-set name (warn on dup), else a fresh
    // descriptive+timestamp name so a forgotten default never collides in the journal.
    const auto = !nameTouchedRef.current;
    const finalName = auto ? autoRunName(config) : (config.name?.trim() || autoRunName(config));
    if (!auto) {
      const dup = (pastRuns || []).some((r) => (r.name || "") === finalName);
      if (dup && !window.confirm(`A saved run is already named "${finalName}".\nRun and save another with the same name?`)) return;
    }
    setRunning(true);
    setResult(null);
    setProgress(0);
    // The backtest now runs as a BACKGROUND JOB (POST /backtest/start → poll
    // GET /backtest/runs/{id}); the heavy compute runs off the event loop on the
    // backend, so a long run no longer holds one request open (no 60s timeout /
    // duplicate-on-retry). The eased bar is a "working" indicator until real
    // per-step progress lands (Phase 2); on completion it snaps to 100%.
    const startedAt = Date.now();
    const timer = setInterval(() => {
      const elapsed = Date.now() - startedAt;
      // Ease-out curve: fast at first, asymptotic toward ~90%.
      const pct = Math.min(90, 90 * (1 - Math.exp(-elapsed / 4000)));
      setProgress((cur) => Math.max(cur, pct));
    }, 200);
    try {
      const payload = { ...buildPayload(), name: finalName };
      const { run_id } = await api.startBacktest(payload);
      // Poll until the job reaches a terminal state.
      let doc = null;
      const MAX_POLLS = 600; // ~20 min at 2s; the job keeps running past this
      for (let i = 0; i < MAX_POLLS; i += 1) {
        await new Promise((r) => setTimeout(r, 2000));
        doc = await api.getBacktestRun(run_id).catch(() => null);
        const st = doc?.status;
        if (st === "done" || st === "failed") break;
        if (doc && typeof doc.progress === "number" && doc.progress > 0) {
          setProgress((cur) => Math.max(cur, Math.min(95, doc.progress)));
        }
      }
      if (!doc || doc.status === "running") {
        toast.info("Backtest still running — it'll appear in “Load past run” when done.");
        return;
      }
      if (doc.status === "failed") throw new Error(doc.error || "Backtest failed");
      setProgress(100);
      setResult(doc);
      await refreshRuns();
      // Reset to auto-naming so the next run gets a fresh, unique default name.
      nameTouchedRef.current = false;
      setConfig((c) => ({ ...c, name: autoRunName(c) }));
      toast.success(`Backtest complete: ${doc.metrics.trade_count} trades`);
    } catch (e) {
      const msg = e.response?.data?.detail || e.message;
      toast.error(`Backtest failed: ${msg}`);
    } finally {
      clearInterval(timer);
      setRunning(false);
    }
  };

  const loadPastRun = async (runId) => {
    if (!runId) return;
    try {
      const r = await api.getBacktestRun(runId);
      setResult(r);
      nameTouchedRef.current = true; // a loaded run carries its own name (re-run warns on dup)
      // Stash the run's params so the strategy-change effect doesn't reset them.
      const runStrategy = r.strategy_id || r.config?.strategy_id || config.strategy_id;
      const runParams = r.params_applied || r.config?.params;
      if (runParams) {
        pendingParamsRef.current = { strategy_id: runStrategy, params: { ...runParams } };
      }
      // Restore the pretrade PROFILE too: the run stores resolved filters, not the
      // profile name, so match those filters back to a known profile. Without this,
      // re-running a loaded run used the form's current profile and the numbers
      // wouldn't replicate. Falls back to the current profile if none matches
      // (a genuinely custom filter set) or if profiles haven't loaded yet.
      const runFilters = r.config?.pretrade_filters;
      const matchedProfile = runFilters
        ? profiles.find((p) => canonFilters(p.settings) === canonFilters(runFilters))?.name
        : null;
      // Restore configuration from the saved run
      setConfig((c) => ({
        ...c,
        instrument: r.instrument || c.instrument,
        mode: r.config?.mode || c.mode,
        strategy_id: r.strategy_id || r.config?.strategy_id || c.strategy_id,
        timeframe: r.config?.timeframe || c.timeframe,
        params: r.params_applied || r.config?.params || c.params,
        pretrade_profile: matchedProfile || c.pretrade_profile,
        costs_enabled: r.config?.costs_enabled ?? c.costs_enabled,
        walkforward: r.config?.walkforward ?? c.walkforward,
        train_pct: r.config?.train_pct ?? c.train_pct,
        n_folds: r.config?.n_folds ?? c.n_folds,
        name: r.name || c.name,
        start_date: msToDate(r.config?.start_ts),
        end_date: msToDate(r.config?.end_ts),
        trade_window_start: r.config?.trade_window_start || "09:25",
        trade_window_end: r.config?.trade_window_end || "15:00",
        option_backtest_enabled: !!r.config?.option_backtest?.enabled,
        option_expiry_mode: r.config?.option_backtest?.expiry_date ? "fixed" : "auto",
        option_expiry_date: r.config?.option_backtest?.expiry_date || "",
        option_moneyness: r.config?.option_backtest?.moneyness || c.option_moneyness,
        option_lots: r.config?.option_backtest?.lots || c.option_lots,
        option_auto_fetch: r.config?.option_backtest?.auto_fetch ?? c.option_auto_fetch,
        option_exit_mode: r.config?.option_backtest?.exit_mode || c.option_exit_mode,
        option_target_pts: r.config?.option_backtest?.option_target_pts ?? "",
        option_stop_pts: r.config?.option_backtest?.option_stop_pts ?? "",
        option_target_pct: r.config?.option_backtest?.option_target_pct ?? "",
        option_stop_pct: r.config?.option_backtest?.option_stop_pct ?? "",
        option_sl_tp_unit: (r.config?.option_backtest?.option_target_pct != null || r.config?.option_backtest?.option_stop_pct != null) ? "pct" : "pts",
        option_dte_filter: parseDteFilter(r.config?.option_backtest?.dte_filter),
        option_costs_enabled: !!r.config?.option_backtest?.cost_config?.enabled,
        option_brokerage_per_order: r.config?.option_backtest?.cost_config?.brokerage_per_order ?? 0,
        option_spread_pct: r.config?.option_backtest?.cost_config?.spread_pct_of_premium ?? 1.0,
        option_spread_min_pts: r.config?.option_backtest?.cost_config?.spread_min_pts ?? 0,
        option_sizing_enabled: !!r.config?.option_backtest?.sizing_config?.enabled,
        option_sizing_mode: r.config?.option_backtest?.sizing_config?.mode || "premium_at_risk",
        option_capital: r.config?.option_backtest?.sizing_config?.capital ?? 200000,
        option_risk_per_trade_pct: r.config?.option_backtest?.sizing_config?.risk_per_trade_pct ?? 1.0,
        option_fixed_lots: r.config?.option_backtest?.sizing_config?.fixed_lots ?? 1,
        option_max_lots: r.config?.option_backtest?.sizing_config?.max_lots ?? 10,
        option_assumed_stop_pct: r.config?.option_backtest?.sizing_config?.assumed_stop_pct_of_premium ?? 50,
        // Exit/risk overlay from the loaded run -> prefill the panel (fractions).
        exit_controls_enabled: Boolean(r.config?.option_backtest?.exit_controls?.enabled),
        exit_controls_unit: r.config?.option_backtest?.exit_controls?.unit || "pct",
        breakeven_trigger: r.config?.option_backtest?.exit_controls?.breakeven?.trigger ?? "",
        breakeven_lock: r.config?.option_backtest?.exit_controls?.breakeven?.lock ?? "",
        trailing_activation: r.config?.option_backtest?.exit_controls?.trailing?.activation ?? "",
        trailing_distance: r.config?.option_backtest?.exit_controls?.trailing?.distance ?? "",
        daily_cap_loss: r.config?.option_backtest?.daily_caps?.loss ?? "",
        daily_cap_target: r.config?.option_backtest?.daily_caps?.target ?? "",
        daily_cap_max_trades: r.config?.option_backtest?.daily_caps?.max_trades ?? "",
      }));
      toast.success(`Loaded: ${r.name}`);
    } catch (e) {
      toast.error("Failed to load run");
    }
  };

  return (
    <div className="space-y-3" data-testid="backtest-lab-page">
    <div className="grid grid-cols-1 lg:grid-cols-[320px_minmax(0,1fr)] gap-3">
      {/* LEFT: Setup Panel */}
      <aside className="space-y-3">
        <Panel title="Setup" testid="backtest-setup-panel">
          <div className="space-y-3">
            {pastRuns.length > 0 && (
              <div>
                <Label className="text-xs text-dim">Load past run</Label>
                <Select value="" onValueChange={loadPastRun}>
                  <SelectTrigger className="bg-bg-2 border-line h-8 mt-1" data-testid="backtest-load-run-select">
                    <SelectValue placeholder="Pick a saved run to restore config…" />
                  </SelectTrigger>
                  <SelectContent>
                    {pastRuns.slice(0, 30).map((r) => (
                      <SelectItem key={r.id} value={r.id}>
                        {r.name} · {r.instrument} · {r.strategy_id?.slice(0, 16)} · WR {fmtPct(r.metrics?.win_rate)}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
            )}
            {presets.length > 0 && (
              <div>
                <Label className="text-xs text-dim">Load preset (optimized params)</Label>
                <Select value="" onValueChange={applyPreset}>
                  <SelectTrigger className="bg-bg-2 border-line h-8 mt-1" data-testid="backtest-load-preset-select">
                    <SelectValue placeholder="Pick a saved preset…" />
                  </SelectTrigger>
                  <SelectContent>
                    {presets.map((p) => (
                      <SelectItem key={p.name} value={p.name}>
                        {p.name}
                        {p.config?.source_optimization_job ? " · from optimizer" : ""}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
            )}
            <div>
              <Label className="text-xs text-dim">Run name (saved to journal)</Label>
              <Input
                value={config.name}
                onChange={(e) => { nameTouchedRef.current = true; setConfig({ ...config, name: e.target.value }); }}
                className="bg-bg-2 border-line h-8 mt-1"
                data-testid="backtest-name-input"
                placeholder="e.g. NIFTY scalp v2"
              />
            </div>
            <Row label="Instrument" hint="Which index to backtest. NIFTY is the most liquid (tightest option spreads); BANKNIFTY/SENSEX move more but have wider spreads — keep realistic costs on for them.">
              <Select value={config.instrument} onValueChange={(v) => setConfig({ ...config, instrument: v })}>
                <SelectTrigger className="bg-bg-2 border-line h-8" data-testid="backtest-instrument-select">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {INSTRUMENTS.map((i) => <SelectItem key={i} value={i}>{i}</SelectItem>)}
                </SelectContent>
              </Select>
            </Row>
            <Row label="Strategy" hint="The signal logic (its description shows below). Its tunable parameters appear in the Strategy Parameters card lower down.">
              <Select value={config.strategy_id} onValueChange={(v) => setConfig({ ...config, strategy_id: v })}>
                <SelectTrigger className="bg-bg-2 border-line h-8" data-testid="backtest-strategy-select">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {strategies.filter((s) => s.is_loaded !== false && !s.is_retired).map((s) => <SelectItem key={s.id} value={s.id}>{s.name}</SelectItem>)}
                </SelectContent>
              </Select>
            </Row>
            {selectedStrategy && (
              <div className="text-[11px] text-dim leading-snug px-1">{selectedStrategy.description}</div>
            )}
            <Row label="Pre-trade profile" hint="A bundle of signal-quality gates (regime, VIX, time-of-day) applied BEFORE the strategy fires. 'Balanced' is a good default; stricter profiles trade less but cleaner; a permissive/off profile trades the most — use it to see raw signal quality.">
              <Select value={config.pretrade_profile} onValueChange={(v) => setConfig({ ...config, pretrade_profile: v })}>
                <SelectTrigger className="bg-bg-2 border-line h-8" data-testid="backtest-profile-select">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {profiles.map((p) => <SelectItem key={p.name} value={p.name}>{p.name}</SelectItem>)}
                </SelectContent>
              </Select>
            </Row>
            <div className="flex items-center gap-2 pt-1">
              <Switch
                checked={config.costs_enabled}
                onCheckedChange={(v) => setConfig({ ...config, costs_enabled: v })}
                data-testid="backtest-costs-switch"
              />
              <span className="text-xs text-dim">Apply realistic costs (slippage + brokerage)</span>
              <Hint label="Apply realistic costs">Adds index-side slippage. Keep ON — an edge that only survives with costs OFF isn't deployable. For the option ₹ P&L, also enable 'Apply rupee costs' in Option Execution.</Hint>
            </div>
            <div className="flex items-center gap-2">
              <Switch
                checked={config.walkforward}
                onCheckedChange={(v) => setConfig({ ...config, walkforward: v })}
                data-testid="backtest-walkforward-switch"
              />
              <span
                className="text-xs text-dim"
                title="Splits this run's window into folds and replays the SAME parameter set in-sample vs out-of-sample (a stability check). It does NOT re-optimize parameters — for the honest re-optimizing version use the Optimizer's Run type 'Walk-forward (honest OOS)'."
              >
                Walk-forward split check (same params, IS vs OOS)
              </span>
              <Hint label="Walk-forward split check">Replays the SAME parameters in-sample vs out-of-sample as a stability check — it does NOT re-optimize. A large IS-vs-OOS gap means the result is overfit/fragile. For honest re-optimization use the Optimizer's walk-forward run instead.</Hint>
            </div>
            <div className="pt-2 border-t border-line">
              <Label className="text-xs text-dim">Date window (IST, optional)<Hint label="Date window">Leave blank to use all warehouse data. A range tests a specific period. Aim for ≥ ~6 months for a meaningful sample; under ~1 month is anecdote, not evidence.</Hint></Label>
              <div className="grid grid-cols-2 gap-2 mt-1">
                <Input
                  type="date"
                  value={config.start_date}
                  onChange={(e) => setConfig({ ...config, start_date: e.target.value })}
                  className="bg-bg-2 border-line h-8 text-xs"
                  data-testid="backtest-start-date"
                />
                <Input
                  type="date"
                  value={config.end_date}
                  onChange={(e) => setConfig({ ...config, end_date: e.target.value })}
                  className="bg-bg-2 border-line h-8 text-xs"
                  data-testid="backtest-end-date"
                />
              </div>
              <div className="text-[10px] text-dimmer mt-1">
                Leave empty to use all available candles. Phase 4 (Upstox) will support years of history.
              </div>
            </div>
            <div className="pt-2 border-t border-line">
              <Label className="text-xs text-dim">Trade window (IST entries)<Hint label="Trade window">Intraday entry window (IST). Default 09:25–15:00 skips the noisy first 10 minutes and stops NEW entries before the 15:30 square-off. Open to 09:15 only if the strategy is built for the open.</Hint></Label>
              <div className="grid grid-cols-2 gap-2 mt-1">
                <Input
                  type="time"
                  value={config.trade_window_start}
                  onChange={(e) => setConfig({ ...config, trade_window_start: e.target.value })}
                  className="bg-bg-2 border-line h-8 text-xs"
                  data-testid="backtest-window-start"
                />
                <Input
                  type="time"
                  value={config.trade_window_end}
                  onChange={(e) => setConfig({ ...config, trade_window_end: e.target.value })}
                  className="bg-bg-2 border-line h-8 text-xs"
                  data-testid="backtest-window-end"
                />
              </div>
              <div className="text-[10px] text-dimmer mt-1">
                No entries outside this window. Default 09:25–15:00 skips the first 10 min and last 30 min.
              </div>
            </div>
          </div>
        </Panel>

        <Panel title="Option Execution" testid="option-backtest-panel">
          <div className="space-y-3">
            <div className="flex items-center gap-2">
              <Switch
                checked={config.option_backtest_enabled}
                onCheckedChange={(v) => setConfig({ ...config, option_backtest_enabled: v })}
                data-testid="option-backtest-switch"
              />
              <span className="text-xs text-dim">Pair signals with option candles</span>
              <Hint label="Pair signals with option candles">Re-prices each spot signal as a REAL ATM-band option trade (actual CE/PE premium candles) instead of index points. Turn ON for a realistic ₹ P&L — this is what makes a result deployable, and it reveals the Exit/risk controls below.</Hint>
            </div>
            <div className="grid grid-cols-2 gap-2">
              <Row label="Expiry" hint="Which expiry to trade. 'Nearest weekly (auto)' is the realistic default (most liquid, highest theta). 'Fixed' pins one expiry — research only.">
                <Select
                  value={config.option_expiry_mode}
                  onValueChange={(v) => setConfig({ ...config, option_expiry_mode: v })}
                >
                  <SelectTrigger className="bg-bg-2 border-line h-8" data-testid="option-expiry-mode-select">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="auto">Nearest weekly (auto, per trade)</SelectItem>
                    <SelectItem value="fixed">Fixed expiry date</SelectItem>
                  </SelectContent>
                </Select>
              </Row>
              <Row label="Moneyness" hint="Strike vs spot. ATM = best liquidity + balanced delta/theta (default). OTM = cheaper, higher gamma, faster decay (needs a quick move). ITM = pricier, more delta, less theta. For intraday momentum, ATM or OTM1.">
                <Select
                  value={config.option_moneyness}
                  onValueChange={(v) => setConfig({ ...config, option_moneyness: v })}
                >
                  <SelectTrigger className="bg-bg-2 border-line h-8" data-testid="option-moneyness-select">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {OPTION_MONEYNESS.map((m) => <SelectItem key={m} value={m}>{m.toUpperCase()}</SelectItem>)}
                  </SelectContent>
                </Select>
              </Row>
            </div>
            {config.option_expiry_mode === "fixed" && (
              <Row label="Fixed expiry date">
                <Input
                  type="date"
                  value={config.option_expiry_date}
                  onChange={(e) => setConfig({ ...config, option_expiry_date: e.target.value })}
                  className="bg-bg-2 border-line h-8 text-xs"
                  data-testid="option-expiry-input"
                />
                <div className="text-[10px] text-amber-300 mt-1">
                  Pins ALL trades to this one expiry. Only use for single-expiry-day studies — for multi-day
                  backtests keep "Nearest weekly (auto)".
                </div>
              </Row>
            )}
            <div className="grid grid-cols-2 gap-2">
              <Row label="Lots" hint="Lots per trade (contract lot size is automatic). Ignored when Capital & position sizing is ON — the sizing panel sets the lots then.">
                <Input
                  type="number"
                  min="1"
                  step="1"
                  value={config.option_lots}
                  onChange={(e) => setConfig({ ...config, option_lots: e.target.value })}
                  className="bg-bg-2 border-line h-8 text-xs disabled:opacity-50"
                  disabled={config.option_sizing_enabled}
                  title={config.option_sizing_enabled
                    ? "Ignored while Capital & position sizing is on — the sizing panel below controls the lot count."
                    : "Lots per trade (lot size always comes from the contract)."}
                  data-testid="option-lots-input"
                />
                {config.option_sizing_enabled && (
                  <div className="text-[10px] text-amber-300 mt-1">
                    Controlled by the sizing panel below while sizing is on.
                  </div>
                )}
              </Row>
              <div className="flex items-end gap-2 pb-1">
                <Switch
                  checked={config.option_auto_fetch}
                  onCheckedChange={(v) => setConfig({ ...config, option_auto_fetch: v })}
                  data-testid="option-auto-fetch-switch"
                />
                <span className="text-xs text-dim">Auto-fetch</span>
                <Hint label="Auto-fetch">If option candles for a needed strike-day aren't in the warehouse, fetch them on the fly. Keep ON unless you've pre-filled the warehouse.</Hint>
              </div>
            </div>

            {/* DTE filter — multi-select: restrict the backtest to sessions a
                chosen number of trading days before the weekly expiry (e.g. tick
                DTE0+DTE1+DTE2 for the 0-2 DTE buying window). None ticked = all. */}
            <Row label="DTE filter (days to expiry)" hint="Only take trades when days-to-expiry is in the selected set. 0 = expiry day (max theta + gamma, whippy). Leave ALL unless isolating a DTE regime.">
              <div className="flex flex-wrap items-center gap-1" data-testid="option-dte-multiselect">
                <button
                  type="button"
                  onClick={() => setConfig({ ...config, option_dte_filter: [] })}
                  className={`px-2 py-1 rounded text-[11px] font-mono border ${(config.option_dte_filter || []).length === 0 ? "bg-info text-bg-0 border-info" : "bg-bg-2 text-dim border-line hover:text-foreground"}`}
                  title="Every weekly-expiry session (no DTE restriction)"
                  data-testid="option-dte-all"
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
                      className={`px-2 py-1 rounded text-[11px] font-mono border ${selected ? "bg-info text-bg-0 border-info" : "bg-bg-2 text-dim border-line hover:text-foreground"}`}
                      title={d === 0 ? "Expiry day (0DTE)" : `${d} trading day${d > 1 ? "s" : ""} before expiry`}
                      data-testid={`option-dte-${d}`}
                    >
                      {d}
                    </button>
                  );
                })}
              </div>
              <div className="text-[10px] text-dimmer mt-1">
                {(config.option_dte_filter || []).length === 0
                  ? "All sessions (every weekly expiry)."
                  : `Only sessions ${config.option_dte_filter.map((d) => `DTE${d}`).join(", ")} before the nearest expiry.`}
              </div>
            </Row>

            {/* Rupee cost model — brokerage + statutory charges + % bid-ask spread.
                Flattrade = ₹0 brokerage; statutory charges always apply when on. */}
            <div className="pt-2 border-t border-line space-y-2">
              <div className="flex items-center gap-2">
                <Switch
                  checked={config.option_costs_enabled}
                  onCheckedChange={(v) => setConfig({ ...config, option_costs_enabled: v })}
                  data-testid="option-costs-switch"
                />
                <span className="text-xs text-dim">Apply rupee costs (brokerage + STT + charges + spread)</span>
                <Hint label="Apply rupee costs">Adds brokerage + bid-ask spread to the OPTION P&L (net ₹). Turn ON for realism — the daily ₹ caps below also require this.</Hint>
              </div>
              {config.option_costs_enabled && (
                <>
                  <div className="grid grid-cols-2 gap-2">
                    <Row label="Brokerage / order (₹)" hint="Flat ₹ per order; entry and exit each count. ~₹20–40/order is typical for a discount broker. 0 = ignore.">
                      <Input
                        type="number" min="0" step="1"
                        value={config.option_brokerage_per_order}
                        onChange={(e) => setConfig({ ...config, option_brokerage_per_order: e.target.value })}
                        className="bg-bg-2 border-line h-8 text-xs"
                        data-testid="option-brokerage-input"
                      />
                    </Row>
                    <Row label="Bid-ask spread (% of premium)" hint="Half-spread paid on BOTH entry and exit, as % of premium. ATM NIFTY ≈ 0.5–1%; wider for BANKNIFTY/SENSEX/OTM. 1% is a safe default — too low gives optimistic fills.">
                      <Input
                        type="number" min="0" step="0.25"
                        value={config.option_spread_pct}
                        onChange={(e) => setConfig({ ...config, option_spread_pct: e.target.value })}
                        className="bg-bg-2 border-line h-8 text-xs"
                        data-testid="option-spread-pct-input"
                      />
                    </Row>
                  </div>
                  <div className="text-[10px] text-dimmer leading-snug">
                    STT, exchange, SEBI, GST and stamp are applied automatically. Set brokerage to 0 for Flattrade,
                    ₹20 for Upstox/Zerodha. Spread is modeled as a % of premium (half crossed each side) — this is the
                    silent killer on cheap OTM / 0DTE options.
                  </div>
                </>
              )}
            </div>

            {/* Position sizing + capital. Lot SIZE always from contract; here the
                user picks fixed lots or premium-at-risk sizing of the lot COUNT. */}
            <div className="pt-2 border-t border-line space-y-2">
              <div className="flex items-center gap-2">
                <Switch
                  checked={config.option_sizing_enabled}
                  onCheckedChange={(v) => setConfig({ ...config, option_sizing_enabled: v })}
                  data-testid="option-sizing-switch"
                />
                <span className="text-xs text-dim">Capital & position sizing (rupee equity curve)</span>
                <Hint label="Capital & position sizing">Size each trade from an account balance instead of fixed lots — gives a real ₹ equity curve, return %, and drawdown. Turn ON to see deployable economics.</Hint>
              </div>
              {config.option_sizing_enabled && (
                <>
                  <div className="grid grid-cols-2 gap-2">
                    <Row label="Capital (₹)" hint="Starting account ₹. Use a realistic amount you'd actually deploy (e.g. ₹2–5L). Drives return % and the Account value chart.">
                      <Input
                        type="number" min="0" step="10000"
                        value={config.option_capital}
                        onChange={(e) => setConfig({ ...config, option_capital: e.target.value })}
                        className="bg-bg-2 border-line h-8 text-xs"
                        data-testid="option-capital-input"
                      />
                    </Row>
                    <Row label="Sizing mode" hint="'Premium at risk' sizes by how much you'd lose if stopped (recommended — risk-based). 'Fixed lots' always trades the same lots.">
                      <Select
                        value={config.option_sizing_mode}
                        onValueChange={(v) => setConfig({ ...config, option_sizing_mode: v })}
                      >
                        <SelectTrigger className="bg-bg-2 border-line h-8" data-testid="option-sizing-mode-select">
                          <SelectValue />
                        </SelectTrigger>
                        <SelectContent>
                          <SelectItem value="premium_at_risk">Premium-at-risk (% of capital)</SelectItem>
                          <SelectItem value="fixed_lots">Fixed lots</SelectItem>
                        </SelectContent>
                      </Select>
                    </Row>
                  </div>
                  {config.option_sizing_mode === "premium_at_risk" ? (
                    <div className="grid grid-cols-3 gap-2">
                      <Row label="Risk/trade (%)" hint="Max % of capital risked per trade (premium × assumed stop). 0.5–2% is sane; above 2% risks ruin on a losing streak. With a 50% assumed stop, 1% risk ≈ 2% of capital deployed in premium.">
                        <Input
                          type="number" min="0.1" step="0.1"
                          value={config.option_risk_per_trade_pct}
                          onChange={(e) => setConfig({ ...config, option_risk_per_trade_pct: e.target.value })}
                          className="bg-bg-2 border-line h-8 text-xs"
                          data-testid="option-risk-pct-input"
                        />
                      </Row>
                      <Row label="Max lots" hint="Hard cap on lots per trade regardless of the risk calc — stops one signal taking an oversized position when premiums are tiny.">
                        <Input
                          type="number" min="1" step="1"
                          value={config.option_max_lots}
                          onChange={(e) => setConfig({ ...config, option_max_lots: e.target.value })}
                          className="bg-bg-2 border-line h-8 text-xs"
                          data-testid="option-max-lots-input"
                        />
                      </Row>
                      <Row label="Assumed stop (%)" hint="The % premium drop assumed FOR SIZING (not the real exit). 50% is typical. A tighter assumed stop → bigger position for the same risk %. Keep it close to your actual stop.">
                        <Input
                          type="number" min="1" step="5"
                          value={config.option_assumed_stop_pct}
                          onChange={(e) => setConfig({ ...config, option_assumed_stop_pct: e.target.value })}
                          className="bg-bg-2 border-line h-8 text-xs"
                          data-testid="option-assumed-stop-input"
                        />
                      </Row>
                    </div>
                  ) : (
                    <div className="grid grid-cols-2 gap-2">
                      <Row label="Fixed lots" hint="Lots per trade in fixed-lots mode.">
                        <Input
                          type="number" min="1" step="1"
                          value={config.option_fixed_lots}
                          onChange={(e) => setConfig({ ...config, option_fixed_lots: e.target.value })}
                          className="bg-bg-2 border-line h-8 text-xs"
                          data-testid="option-fixed-lots-input"
                        />
                      </Row>
                      <Row label="Max lots" hint="Hard cap on lots per trade regardless of the risk calc — stops one signal taking an oversized position when premiums are tiny.">
                        <Input
                          type="number" min="1" step="1"
                          value={config.option_max_lots}
                          onChange={(e) => setConfig({ ...config, option_max_lots: e.target.value })}
                          className="bg-bg-2 border-line h-8 text-xs"
                          data-testid="option-max-lots-input-2"
                        />
                      </Row>
                    </div>
                  )}
                  <div className="text-[10px] text-dimmer leading-snug">
                    Lot size is taken from the option contract automatically. Premium-at-risk sizes the lot count so
                    each trade risks ≤ your % of capital (using the option stop, or the assumed-stop % when no option
                    stop is set). A trade that can't fit even one lot in budget still takes one lot, tagged as risk-exceeded.
                  </div>
                  {config.option_sizing_mode === "premium_at_risk"
                    && !(config.option_exit_mode === "option_levels"
                      && (config.option_sl_tp_unit === "pts" ? config.option_stop_pts !== "" : config.option_stop_pct !== "")) && (
                    <div className="text-[10px] text-amber-300 leading-snug" data-testid="option-sizing-estimate-note">
                      No premium stop is set (exit mode is "{config.option_exit_mode === "spot_exit" ? "Mirror spot exit" : "Option premium SL/target"}{config.option_exit_mode === "option_levels" ? " without a stop" : ""}"),
                      so the per-trade rupee risk uses the Assumed stop % — an estimate, not an exact bound.
                    </div>
                  )}
                </>
              )}
            </div>

            {/* Option exit mode — item 9. spot_exit mirrors the index trade;
                option_levels exits on the option's own premium target/stop. */}
            <div className="pt-2 border-t border-line space-y-2">
              <Row label="Option exit mode" hint="How the option exits. 'Mirror spot exit' closes the option when the spot signal exits (simple). 'Option premium SL/target' exits on the OPTION's own price (levels below) — REQUIRED for trailing/breakeven to work.">
                <Select
                  value={config.option_exit_mode}
                  onValueChange={(v) => setConfig({ ...config, option_exit_mode: v })}
                >
                  <SelectTrigger className="bg-bg-2 border-line h-8" data-testid="option-exit-mode-select">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="spot_exit">Mirror spot exit (index SL/target)</SelectItem>
                    <SelectItem value="option_levels">Option premium SL/target</SelectItem>
                  </SelectContent>
                </Select>
              </Row>
              {config.option_exit_mode === "option_levels" && (
                <>
                  <div className="flex items-center gap-2">
                    <span className="text-xs text-dim">Level unit</span>
                    <Hint label="Level unit">Target/Stop as premium Points or Percent of entry premium. % is more robust across strikes/days; pts is absolute.</Hint>
                    <div className="flex rounded-md border border-line overflow-hidden">
                      {["pts", "pct"].map((u) => (
                        <button
                          key={u}
                          type="button"
                          onClick={() => setConfig({ ...config, option_sl_tp_unit: u })}
                          className={`px-2 py-1 text-[11px] font-mono ${config.option_sl_tp_unit === u ? "bg-info text-bg-0" : "bg-bg-2 text-dim hover:text-foreground"}`}
                          data-testid={`option-unit-${u}`}
                        >
                          {u === "pts" ? "Points" : "Percent"}
                        </button>
                      ))}
                    </div>
                  </div>
                  <div className="grid grid-cols-2 gap-2">
                    <Row label={`Target (${config.option_sl_tp_unit === "pts" ? "pts" : "%"})`} hint="Profit target on the option premium (e.g. 30%). Keep it realistic for the expected move; too greedy and few trades hit it.">
                      <Input
                        type="number"
                        min="0"
                        step="0.5"
                        placeholder="e.g. 40"
                        value={config.option_sl_tp_unit === "pts" ? config.option_target_pts : config.option_target_pct}
                        onChange={(e) => setConfig({
                          ...config,
                          [config.option_sl_tp_unit === "pts" ? "option_target_pts" : "option_target_pct"]: e.target.value,
                        })}
                        className="bg-bg-2 border-line h-8 text-xs"
                        data-testid="option-target-input"
                      />
                    </Row>
                    <Row label={`Stop (${config.option_sl_tp_unit === "pts" ? "pts" : "%"})`} hint="Stop on the option premium (e.g. 50%). Wider than the typical adverse wiggle or you'll be stopped on noise; too wide and losers hurt.">
                      <Input
                        type="number"
                        min="0"
                        step="0.5"
                        placeholder="e.g. 30"
                        value={config.option_sl_tp_unit === "pts" ? config.option_stop_pts : config.option_stop_pct}
                        onChange={(e) => setConfig({
                          ...config,
                          [config.option_sl_tp_unit === "pts" ? "option_stop_pts" : "option_stop_pct"]: e.target.value,
                        })}
                        className="bg-bg-2 border-line h-8 text-xs"
                        data-testid="option-stop-input"
                      />
                    </Row>
                  </div>
                  <div className="text-[10px] text-dimmer leading-snug">
                    Exits the option when its premium hits your target/stop, independent of the index.
                    If neither is hit, the spot signal's exit closes the trade. Stop is assumed to fill
                    first if a single 1-min bar spans both levels.
                  </div>
                  <div className="text-[10px] text-amber-300 leading-snug">
                    Live parity: these values do not travel with presets into deployments. To replicate premium
                    exits in live auto-paper trading, set the deployment's auto-paper fallback target/stop
                    (points or %) in the Live Signals form — strategy-defined exits still take priority there.
                  </div>
                </>
              )}
            </div>

            {/* Exit / risk overlay (optional). Premium trailing-stop + breakeven +
                per-day caps — the SAME engine the optimizer searches and the deploy
                wizard enforces. Off by default => byte-identical payload. */}
            {config.option_backtest_enabled && (
              <div className="pt-2 border-t border-line space-y-2" data-testid="exit-controls-panel">
                <div className="flex items-center gap-2">
                  <Switch
                    checked={config.exit_controls_enabled}
                    onCheckedChange={(v) => setConfig({ ...config, exit_controls_enabled: v })}
                    data-testid="exit-controls-switch"
                  />
                  <span className="text-xs text-dim">Exit / risk controls (trailing · breakeven · daily caps)</span>
                  <Hint label="Exit / risk controls">Adds a premium TRAILING stop, a BREAKEVEN lock, and daily ₹/trade caps on top of the option exit. Trailing & breakeven need exit mode = 'Option premium SL/target' (they trail the option's own premium); daily caps always apply.</Hint>
                </div>
                {config.exit_controls_enabled && (
                  <>
                    {config.option_exit_mode !== "option_levels" && (
                      <div className="text-[10px] text-amber-300 leading-snug" data-testid="exit-controls-mode-note">
                        Trailing &amp; breakeven need exit mode “Option premium SL/target” (they trail the option’s
                        own premium). They’re skipped under “Mirror spot exit”. Daily caps still apply.
                      </div>
                    )}
                    <div className="flex items-center gap-2">
                      <span className="text-xs text-dim">Unit</span>
                      <Hint label="Exit controls unit">Read the four fields below as a Fraction of entry premium (0.30 = 30%) or absolute Points. Fraction is recommended — it scales across strikes and days.</Hint>
                      <div className="flex rounded-md border border-line overflow-hidden">
                        {["pct", "pts"].map((u) => (
                          <button
                            key={u}
                            type="button"
                            onClick={() => setConfig({ ...config, exit_controls_unit: u })}
                            className={`px-2 py-1 text-[11px] font-mono ${config.exit_controls_unit === u ? "bg-info text-bg-0" : "bg-bg-2 text-dim hover:text-foreground"}`}
                            data-testid={`exit-controls-unit-${u}`}
                          >
                            {u === "pct" ? "Fraction" : "Points"}
                          </button>
                        ))}
                      </div>
                      <span className="text-[10px] text-dimmer">
                        {config.exit_controls_unit === "pct" ? "0.25 = 25% of entry premium" : "absolute premium points"}
                      </span>
                    </div>
                    <div className="grid grid-cols-2 gap-2">
                      <Row label={`Breakeven trigger ${config.exit_controls_unit === "pts" ? "(pts profit)" : "(fraction)"}`} hint="Once the option is up by this much, the stop jumps to the Breakeven lock level so you stop risking your own capital. e.g. 0.30 = at +30%, arm breakeven. Set it ABOVE your typical noise (too low = armed on a wiggle, then stopped flat). Must be GREATER than Breakeven lock.">
                        <Input
                          type="number" min="0" step={config.exit_controls_unit === "pts" ? "0.5" : "0.05"}
                          value={config.breakeven_trigger}
                          onChange={(e) => setConfig({ ...config, breakeven_trigger: e.target.value })}
                          placeholder={config.exit_controls_unit === "pts" ? "off" : "0.30 = +30% arms BE"}
                          className="bg-bg-2 border-line h-8 text-xs" data-testid="exit-be-trigger"
                          disabled={config.option_exit_mode !== "option_levels"}
                        />
                      </Row>
                      <Row label={`Breakeven lock ${config.exit_controls_unit === "pts" ? "(pts above entry)" : "(fraction)"}`} hint="Where the stop sits after the trigger fires. 0 = lock at exact entry (can't lose on the trade). 0.05 = lock +5% (bank a small gain). Must be LESS than Breakeven trigger. A higher lock is safer but stops out sooner.">
                        <Input
                          type="number" min="0" step={config.exit_controls_unit === "pts" ? "0.5" : "0.05"}
                          value={config.breakeven_lock}
                          onChange={(e) => setConfig({ ...config, breakeven_lock: e.target.value })}
                          placeholder={config.exit_controls_unit === "pts" ? "0 = exact entry" : "0.0 = lock at entry"}
                          className="bg-bg-2 border-line h-8 text-xs" data-testid="exit-be-lock"
                          disabled={config.option_exit_mode !== "option_levels"}
                        />
                      </Row>
                      <Row label={`Trail activate ${config.exit_controls_unit === "pts" ? "(pts profit)" : "(fraction)"}`} hint="Premium gain at which the TRAILING stop switches on. e.g. 0.40 = start trailing at +40%. Set it ≥ Breakeven trigger so breakeven engages first and trailing then takes over for the big moves. Too low = trails on noise.">
                        <Input
                          type="number" min="0" step={config.exit_controls_unit === "pts" ? "0.5" : "0.05"}
                          value={config.trailing_activation}
                          onChange={(e) => setConfig({ ...config, trailing_activation: e.target.value })}
                          placeholder={config.exit_controls_unit === "pts" ? "off" : "0.40 = +40%"}
                          className="bg-bg-2 border-line h-8 text-xs" data-testid="exit-trail-activation"
                          disabled={config.option_exit_mode !== "option_levels"}
                        />
                      </Row>
                      <Row label={`Trail distance ${config.exit_controls_unit === "pts" ? "(pts from peak)" : "(fraction)"}`} hint="How far the trailing stop sits below the running peak. e.g. 0.25 = give back 25% from the peak. Tighter (0.15) locks more but exits on small pullbacks; looser (0.35) rides trends but gives back more. Match it to the instrument's intraday swings.">
                        <Input
                          type="number" min="0" step={config.exit_controls_unit === "pts" ? "0.5" : "0.05"}
                          value={config.trailing_distance}
                          onChange={(e) => setConfig({ ...config, trailing_distance: e.target.value })}
                          placeholder={config.exit_controls_unit === "pts" ? "—" : "0.25 = give back 25%"}
                          className="bg-bg-2 border-line h-8 text-xs" data-testid="exit-trail-distance"
                          disabled={config.option_exit_mode !== "option_levels"}
                        />
                      </Row>
                    </div>
                    <div className="pt-1 grid grid-cols-3 gap-2">
                      <Row label="Daily loss ₹" hint="Stop taking NEW trades once the session's realized loss reaches this (soft halt; auto-resets next session). Needs rupee costs on. Set to a loss you can stomach — e.g. 2–3× your average per-trade risk.">
                        <Input
                          type="number" min="0" step="500"
                          value={config.daily_cap_loss}
                          onChange={(e) => setConfig({ ...config, daily_cap_loss: e.target.value })}
                          placeholder={config.option_costs_enabled ? "e.g. 5000" : "needs costs"}
                          className="bg-bg-2 border-line h-8 text-xs" data-testid="exit-cap-loss"
                          disabled={!config.option_costs_enabled}
                        />
                      </Row>
                      <Row label="Daily target ₹" hint="Stop taking NEW trades once the session is up this much (lock in a good day). Needs rupee costs on.">
                        <Input
                          type="number" min="0" step="500"
                          value={config.daily_cap_target}
                          onChange={(e) => setConfig({ ...config, daily_cap_target: e.target.value })}
                          placeholder={config.option_costs_enabled ? "e.g. 8000" : "needs costs"}
                          className="bg-bg-2 border-line h-8 text-xs" data-testid="exit-cap-target"
                          disabled={!config.option_costs_enabled}
                        />
                      </Row>
                      <Row label="Max trades / day" hint="Cap entries per session — curbs overtrading in choppy regimes. e.g. 3–5 for a scalper. Doesn't need costs on.">
                        <Input
                          type="number" min="0" step="1"
                          value={config.daily_cap_max_trades}
                          onChange={(e) => setConfig({ ...config, daily_cap_max_trades: e.target.value })}
                          placeholder="e.g. 5"
                          className="bg-bg-2 border-line h-8 text-xs" data-testid="exit-cap-max-trades"
                        />
                      </Row>
                    </div>
                    <div className="text-[10px] text-dimmer leading-snug">
                      Daily ₹ caps are soft per-session governors (auto-resume next session). Loss/target need
                      costs on (they act on net ₹); max-trades doesn’t. Walk-forward toggle runs the spot WF only —
                      the overlay shows in the full-window option result, not the IS/OOS WF panel.
                    </div>
                  </>
                )}
              </div>
            )}
          </div>
        </Panel>

        <Panel
          title={<span>Strategy Parameters<Hint label="Strategy Parameters">Strategy-specific knobs. Each comes from the strategy's own schema — hover the strategy description above for what the strategy does. Start from the defaults; change one at a time and re-run, and prefer values the Optimizer found robust (stable in-sample vs out-of-sample) over a single lucky peak.</Hint></span>}
          right={<button onClick={() => setParamsOpen(!paramsOpen)} className="text-dim hover:text-foreground" data-testid="backtest-params-toggle">{paramsOpen ? <ChevronDown className="w-4 h-4" /> : <ChevronRight className="w-4 h-4" />}</button>}
          testid="backtest-params-panel"
        >
          {paramsOpen && selectedStrategy && (
            <div className="space-y-3">
              {Object.entries(selectedStrategy.parameter_schema || {}).map(([key, def]) => (
                <ParamRow key={key} name={key} def={def} value={config.params[key]} onChange={(v) => setParam(key, v)} />
              ))}
              {(!selectedStrategy.parameter_schema || Object.keys(selectedStrategy.parameter_schema).length === 0) && (
                <div className="text-xs text-dimmer">No tunable parameters.</div>
              )}
              {(() => {
                // Params carried invisibly from an optimizer preset (e.g. tuned
                // indicator periods) — show them so the run is reproducible on sight.
                const schemaKeys = new Set(Object.keys(selectedStrategy.parameter_schema || {}));
                const carried = Object.entries(config.params || {}).filter(([k]) => !schemaKeys.has(k));
                if (carried.length === 0) return null;
                return (
                  <div className="rounded border border-line bg-bg-0 px-2 py-1.5 text-[11px] text-dimmer" data-testid="carried-preset-params">
                    <span className="text-dim">Carried from preset:</span>{" "}
                    <span className="font-mono">{carried.map(([k, v]) => `${k}=${v}`).join(" · ")}</span>
                  </div>
                );
              })()}
            </div>
          )}
        </Panel>

        {config.option_backtest_enabled && (
          <PreflightPanel
            preflight={preflight}
            preflighting={preflighting}
            onCheck={() => checkOptionData(false)}
            onIngest={() => checkOptionData(true)}
          />
        )}

        <Button
          onClick={runBacktest}
          disabled={running}
          className="w-full bg-info text-bg-0 hover:bg-info/90 font-semibold"
          data-testid="backtest-run-button"
        >
          {running ? <Loader2 className="w-4 h-4 mr-2 animate-spin" /> : <Play className="w-4 h-4 mr-2" />}
          {running ? "Running…" : "Run Backtest"}
        </Button>

        <div>
          <Button
            variant="outline"
            size="sm"
            onClick={saveAsPreset}
            className="w-full h-8 text-xs border-line"
            data-testid="backtest-save-preset"
          >
            <Save className="w-3.5 h-3.5 mr-1.5" /> Save setup as preset
          </Button>
          <div className="text-[10px] text-dimmer mt-1">
            Captures the strategy params + option execution / exit policy. Deployable as-is from Live Signals.
          </div>
        </div>
      </aside>

      {/* RIGHT: Results */}
      <section className="min-w-0 space-y-3">
        {running ? (
          <RunningResults progress={progress} config={config} />
        ) : !result ? (
          <EmptyResults />
        ) : (
          <ResultsView result={result} onSaveAsPreset={() => savePresetFromResult(result)} />
        )}
      </section>
    </div>

      <BacktestRunJournal onLoadRun={loadPastRun} refreshKey={pastRuns.length} />
    </div>
  );
}

function PreflightPanel({ preflight, preflighting, onCheck, onIngest }) {
  const pct = preflight?.coverage_pct;
  const covColor = pct == null ? "text-dim" : pct >= 90 ? "text-positive" : pct >= 60 ? "text-warning" : "text-negative";
  return (
    <div className="rounded-lg border border-line bg-bg-1" data-testid="option-preflight-panel">
      <div className="px-3 py-2 border-b border-line flex items-center gap-2">
        <ShieldCheck className="w-3.5 h-3.5 text-info" />
        <div className="text-[11px] font-semibold uppercase tracking-wider text-dim">Option Data Preflight</div>
      </div>
      <div className="p-3 space-y-3">
        <p className="text-[11px] text-dimmer leading-relaxed">
          Verify that option candles exist for every spot signal before running. Missing data can be ingested from your broker.
        </p>
        <div className="flex flex-wrap gap-2">
          <Button
            onClick={onCheck}
            disabled={preflighting}
            variant="outline"
            className="flex-1 min-w-[8.5rem] text-xs h-8"
            data-testid="option-preflight-check"
          >
            {preflighting ? <Loader2 className="w-3.5 h-3.5 mr-1.5 animate-spin" /> : <ShieldCheck className="w-3.5 h-3.5 mr-1.5" />}
            Check option data
          </Button>
          <Button
            onClick={onIngest}
            disabled={preflighting || !preflight || (preflight.missing_contract === 0 && preflight.missing_candle === 0)}
            variant="outline"
            className="flex-1 min-w-[8.5rem] text-xs h-8"
            data-testid="option-preflight-ingest"
          >
            Ingest missing & recheck
          </Button>
        </div>

        {preflight && preflight.enabled !== false && (
          <div className="rounded-md border border-line bg-bg-0 p-3 space-y-2">
            <div className="flex items-center justify-between">
              <span className="text-[11px] text-dim">Coverage</span>
              <span className={`text-sm font-semibold ${covColor}`}>{pct}%</span>
            </div>
            <div className="h-1.5 rounded-full bg-bg-2 overflow-hidden">
              <div
                className={`h-full ${pct >= 90 ? "bg-positive" : pct >= 60 ? "bg-warning" : "bg-negative"}`}
                style={{ width: `${Math.min(100, Math.max(0, pct || 0))}%` }}
              />
            </div>
            <div className="grid grid-cols-2 gap-x-3 gap-y-1.5 text-[11px] pt-1">
              <div className="flex justify-between"><span className="text-dimmer">Spot signals</span><span className="text-dim font-mono">{preflight.total_spot_trades}</span></div>
              <div className="flex justify-between"><span className="text-dimmer">Would pair</span><span className="text-positive font-mono">{preflight.would_pair}</span></div>
              <div className="flex justify-between"><span className="text-dimmer">Missing contract</span><span className={`font-mono ${preflight.missing_contract ? "text-negative" : "text-dim"}`}>{preflight.missing_contract}</span></div>
              <div className="flex justify-between"><span className="text-dimmer">Missing candles</span><span className={`font-mono ${preflight.missing_candle ? "text-warning" : "text-dim"}`}>{preflight.missing_candle}</span></div>
            </div>

            {preflight.ingest?.status === "started" && (
              <div className="rounded border border-info/40 bg-info/10 px-2 py-1.5 text-[11px] text-info">
                Ingesting missing option data…
                {preflight.ingest.stage === "contracts"
                  ? " syncing contracts"
                  : ` ${Math.round(preflight.ingest.progress_pct || 0)}%`}
                {" "}· run {String(preflight.ingest.run_id).slice(0, 8)} · auto re-check when done
              </div>
            )}
            {preflight.ingest?.status && preflight.ingest.status !== "started" && (
              <div className="rounded border border-warning/40 bg-warning/10 px-2 py-1.5 text-[11px] text-warning">
                {preflight.ingest.status === "failed" ? "Ingest failed" : "Ingest not started"}:{" "}
                {preflight.ingest.reason || preflight.ingest.error || preflight.ingest.status}
              </div>
            )}

            {Array.isArray(preflight.missing_contract_keys) && preflight.missing_contract_keys.length > 0 && (
              <details className="text-[11px]">
                <summary className="cursor-pointer text-dimmer hover:text-dim">
                  {preflight.missing_contract_keys.length} unresolved contract{preflight.missing_contract_keys.length > 1 ? "s" : ""}
                </summary>
                <div className="mt-1.5 max-h-28 overflow-auto font-mono text-dimmer space-y-0.5">
                  {preflight.missing_contract_keys.slice(0, 40).map((k, i) => (
                    <div key={i}>{k}</div>
                  ))}
                </div>
              </details>
            )}
          </div>
        )}

        {preflight && preflight.enabled === false && (
          <div className="rounded border border-line bg-bg-0 px-2 py-1.5 text-[11px] text-dimmer">
            Option execution is disabled — nothing to check.
          </div>
        )}
      </div>
    </div>
  );
}

function Panel({ title, children, right, testid, rootRef, className = "", bodyClassName = "p-3", bodyStyle }) {
  return (
    <div ref={rootRef} className={`rounded-lg border border-line bg-bg-1 ${className}`} data-testid={testid}>
      <div className="px-3 py-2 border-b border-line flex items-center shrink-0">
        <div className="text-[11px] font-semibold uppercase tracking-wider text-dim">{title}</div>
        {right && <div className="ml-auto">{right}</div>}
      </div>
      <div className={bodyClassName} style={bodyStyle}>{children}</div>
    </div>
  );
}

// Inline "?" help affordance: focusable icon revealing a styled tooltip on
// hover/focus. Self-contained provider so it works wherever it's dropped.
const Hint = ({ children, label = "help" }) => (
  <TooltipProvider delayDuration={150}>
    <Tooltip>
      <TooltipTrigger asChild>
        <button type="button" aria-label={label}
          className="ml-1 inline-flex align-middle text-dimmer hover:text-dim focus:outline-none focus-visible:text-dim">
          <HelpCircle className="h-3 w-3" />
        </button>
      </TooltipTrigger>
      <TooltipContent className="max-w-xs text-[11px] leading-snug">{children}</TooltipContent>
    </Tooltip>
  </TooltipProvider>
);

function Row({ label, hint, children }) {
  return (
    <div>
      <Label className="text-xs text-dim">{label}{hint && <Hint label={label}>{hint}</Hint>}</Label>
      <div className="mt-1">{children}</div>
    </div>
  );
}

function ParamRow({ name, def, value, onChange }) {
  const t = def.type;
  if (t === "bool") {
    return (
      <div className="flex items-center gap-2">
        <Switch checked={!!value} onCheckedChange={onChange} data-testid={`param-${name}-switch`} />
        <span className="text-xs text-dim">{name}</span>
      </div>
    );
  }
  if (t === "int" || t === "float") {
    const min = def.min ?? 0;
    const max = def.max ?? 100;
    const decimals = t === "int" ? 0 : 2;
    const step = t === "int" ? 1 : (max - min) / 200;
    return (
      <NumberSliderInput
        label={name}
        value={Number(value ?? def.default)}
        min={min}
        max={max}
        step={step}
        decimals={decimals}
        onChange={onChange}
        testid={`param-${name}`}
      />
    );
  }
  return (
    <Row label={name}>
      <Input
        value={value ?? def.default}
        onChange={(e) => onChange(e.target.value)}
        className="bg-bg-2 border-line h-8"
        data-testid={`param-${name}-input`}
      />
    </Row>
  );
}

function EmptyResults() {
  return (
    <div
      className="rounded-lg border border-dashed border-line-strong bg-bg-1 p-8 text-center"
      data-testid="backtest-empty-state"
    >
      <div className="text-sm font-semibold mb-1">No backtest run yet</div>
      <div className="text-xs text-dim">Configure your setup on the left and click <b>Run Backtest</b>. Make sure the data warehouse has candles for the selected instrument first (Data Warehouse → Ingest).</div>
    </div>
  );
}

function RunningResults({ progress, config }) {
  const pct = Math.max(0, Math.min(100, Number(progress || 0)));
  // Rough phase label derived from estimated progress so the user sees the
  // pipeline moving even though it's a single backend request.
  const phase = pct < 25
    ? "Loading candles + indicators…"
    : pct < 55
      ? "Running strategy + simulating trades…"
      : pct < 85
        ? (config?.walkforward ? "Walk-forward folds (IS vs OOS)…" : "Computing metrics…")
        : config?.option_backtest_enabled
          ? "Pairing option candles…"
          : "Finalizing results…";
  return (
    <div
      className="rounded-lg border border-line bg-bg-1 p-6"
      data-testid="backtest-running-state"
      role="status"
      aria-live="polite"
      aria-busy="true"
    >
      <div className="flex items-center gap-3">
        <Loader2 className="w-5 h-5 text-info animate-spin shrink-0" />
        <div className="min-w-0">
          <div className="text-sm font-semibold">Backtest running, please wait…</div>
          <div className="text-xs text-dim truncate">
            <span className="font-mono">{config?.instrument}</span> · <span className="font-mono">{config?.strategy_id}</span> · {phase}
          </div>
        </div>
        <div className="ml-auto text-lg font-mono font-semibold text-info tabular-nums" data-testid="backtest-progress-pct">
          {Math.round(pct)}%
        </div>
      </div>
      <div className="mt-4 h-2 rounded bg-bg-3 overflow-hidden">
        <div
          className="h-full bg-info transition-all duration-200"
          style={{ width: `${pct}%` }}
        />
      </div>
      <div className="mt-4 space-y-3">
        <Skeleton className="h-20 bg-bg-2" />
        <Skeleton className="h-[320px] bg-bg-2" />
      </div>
    </div>
  );
}

function ResultsView({ result, onSaveAsPreset }) {
  const m = result.metrics || {};
  const regimeDist = result.regime_distribution || {};
  const totalRegime = Object.values(regimeDist).reduce((s, v) => s + v, 0);
  const funnel = result.signal_funnel || {};

  // Lowest / highest the account (capital growth) ever reached — shown in the
  // KPI grid alongside Trades / Win Rate.
  const acctRange = useMemo(() => {
    const s = buildPerformanceSeries(result);
    const vals = s.accountValue.map((p) => p.value).filter((v) => Number.isFinite(v));
    return {
      currency: s.currency,
      capital: s.capital,
      min: vals.length ? Math.min(...vals) : null,
      max: vals.length ? Math.max(...vals) : null,
    };
  }, [result]);

  return (
    <div className="space-y-3" data-testid="backtest-results">
      {/* Header with badges + export */}
      <div className="flex items-center gap-2 flex-wrap">
        <SignificanceBadge significance={result.significance} />
        <div className="text-xs text-dim">
          <span className="font-mono">{result.instrument}</span> · <span className="font-mono">{result.strategy_id}</span> · {fmtInt(result.candle_count)} candles
          {result.name && <span className="ml-2 text-foreground font-medium">· {result.name}</span>}
        </div>
        {result.walkforward?.is_vs_oos?.divergence_warning && (
          <span className="text-xs px-2 py-1 rounded bg-amber-950 text-amber-200 border border-amber-900" data-testid="divergence-warning">
            ⚠ IS vs OOS divergence &gt;15%
          </span>
        )}
        <div className="ml-auto flex items-center gap-1">
          <Button
            size="sm"
            variant="secondary"
            className="h-7 text-xs"
            onClick={() => exportBacktestConfig(result)}
            data-testid="export-config-button"
            title="Export strategy + params + filters as JSON (for re-import or sharing)"
          >
            <FileJson className="w-3 h-3 mr-1" /> Config
          </Button>
          <Button
            size="sm"
            variant="secondary"
            className="h-7 text-xs"
            onClick={() => exportBacktestResult(result)}
            data-testid="export-result-button"
            title="Export full backtest result (metrics + trades + equity + walk-forward) as JSON"
          >
            <Download className="w-3 h-3 mr-1" /> Result
          </Button>
          <Button
            size="sm"
            variant="secondary"
            className="h-7 text-xs"
            onClick={() => exportTradesCsv(result)}
            data-testid="export-trades-csv-button"
            title="Export trades table as CSV (Excel-friendly)"
          >
            <FileText className="w-3 h-3 mr-1" /> Trades.csv
          </Button>
          {onSaveAsPreset && (
            <Button
              size="sm"
              className="h-7 text-xs bg-info text-bg-0 hover:bg-info/90 font-semibold"
              onClick={onSaveAsPreset}
              data-testid="result-save-preset"
              title="Save THIS result's exact strategy params + option execution as a preset — deploy it to paper trading as-is, or re-test it"
            >
              <Save className="w-3 h-3 mr-1" /> Save as preset
            </Button>
          )}
        </div>
      </div>

      {/* KPI grid */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
        <MetricCard label="Trades" value={fmtInt(m.trade_count)} testid="result-trades" />
        <MetricCard label="Win Rate" value={fmtPct(m.win_rate)} testid="result-winrate" />
        <MetricCard label="Profit Factor" value={fmtNum(m.profit_factor, 2)} testid="result-pf" />
        <MetricCard label="Net P&L (pts)" value={fmtPnL(m.total_pnl_pts)} accent={colorPnL(m.total_pnl_pts)} testid="result-pnl" />
        <MetricCard label="Max DD (pts)" value={fmtPnL(m.max_dd_pts)} accent="text-danger" testid="result-dd" />
        <MetricCard label="Sharpe" value={fmtNum(m.sharpe, 2)} testid="result-sharpe" />
        <MetricCard
          label={acctRange.currency ? "Lowest Acct Value" : "Lowest Equity"}
          value={acctRange.min == null ? "—" : (acctRange.currency ? `₹${fmtInt(acctRange.min)}` : fmtNum(acctRange.min, 0))}
          sub={acctRange.currency && acctRange.capital != null ? `from ₹${fmtInt(acctRange.capital)}` : undefined}
          accent={acctRange.currency && acctRange.min != null && acctRange.capital != null && acctRange.min < acctRange.capital ? "text-danger" : undefined}
          testid="result-acct-low"
        />
        <MetricCard
          label={acctRange.currency ? "Highest Acct Value" : "Highest Equity"}
          value={acctRange.max == null ? "—" : (acctRange.currency ? `₹${fmtInt(acctRange.max)}` : fmtNum(acctRange.max, 0))}
          accent="text-success"
          testid="result-acct-high"
        />
      </div>

      {/* Trust verdict (Piece 3): advisory option-₹ fragility / ruin / coverage
          flags. Never blocks; absent for spot-only / clean runs. */}
      <TrustScorecard quality={result?.quality} />

      {/* Performance: rupee-first hero + account/underlying chart + drawdown +
          high-value trade-quality metrics. The decision view, kept scannable. */}
      <PerformanceOverview result={result} />

      {/* Price chart with the strategy's trades drawn on it — moved out of
          Advanced analytics to sit directly below the performance/trade-quality
          view. Timeframe switch, entry/exit markers, focused-trade SL/target
          lines, and a date/time go-to. */}
      <BacktestChart result={result} />

      {/* Deep research analytics — collapsed by default so the decision view
          above stays uncluttered; expand for data-trust, option pairing,
          context breakdown, excursions, robustness and the signal funnel. */}
      <AdvancedAnalytics>
        <DataAuditCard audit={result.data_audit} />
        <OptionBacktestCard optionBacktest={result.option_backtest} />
        <ContextBreakdownCard optionBacktest={result.option_backtest} />
        <MaeMfeCard trades={result.trades} optionBacktest={result.option_backtest} />
        <MonteCarloCard trades={result.trades} optionBacktest={result.option_backtest} />
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
          <WalkForwardCard wf={result.walkforward} />
          <SignalFunnelCard funnel={funnel} regimeDist={regimeDist} totalRegime={totalRegime} />
        </div>
      </AdvancedAnalytics>

      {/* Trades table */}
      <TradesTable trades={result.trades || []} optionBacktest={result.option_backtest} />
    </div>
  );
}

function AdvancedAnalytics({ children }) {
  const [open, setOpen] = useState(true);
  return (
    <div className="rounded-lg border border-line bg-bg-1" data-testid="backtest-advanced-analytics">
      <button
        onClick={() => setOpen((v) => !v)}
        className="w-full px-3 py-2 flex items-center gap-2 text-left"
        data-testid="backtest-advanced-toggle"
      >
        {open ? <ChevronDown className="w-4 h-4 text-dim" /> : <ChevronRight className="w-4 h-4 text-dim" />}
        <span className="text-[11px] font-semibold uppercase tracking-wider text-dim">Advanced analytics</span>
        <span className="text-[11px] text-dimmer">data trust · option pairing · context · excursions · robustness · funnel</span>
      </button>
      {open && <div className="px-3 pb-3 space-y-3">{children}</div>}
    </div>
  );
}

function DataAuditCard({ audit }) {
  if (!audit) return null;
  const before = audit.before || {};
  const after = audit.after || {};
  const fill = audit.fill || {};
  const complete = after.complete;
  const badgeClass = complete
    ? "bg-emerald-950 text-emerald-200 border-emerald-900"
    : "bg-amber-950 text-amber-200 border-amber-900";
  const fillText = fill.attempted
    ? `${fill.status || "unknown"} · ${fmtInt(fill.fetched || 0)} fetched`
    : `${fill.status || "skipped"} · ${fill.reason || "coverage_complete"}`;

  return (
    <Panel
      title="Data Audit"
      testid="data-audit-card"
      right={<ShieldCheck className={`w-4 h-4 ${complete ? "text-success" : "text-amber-300"}`} />}
    >
      <div className="grid grid-cols-2 lg:grid-cols-5 gap-2">
        <div className="rounded-md border border-line bg-bg-2 p-2">
          <div className="text-[10px] uppercase tracking-wider text-dimmer">Status</div>
          <div className={`mt-1 inline-flex text-[10px] px-1.5 py-0.5 rounded border font-mono ${badgeClass}`}>
            {complete ? "trusted" : "needs review"}
          </div>
        </div>
        <AuditMetric label="Before" value={`${before.complete_days || 0}/${before.expected_days || 0}`} />
        <AuditMetric label="After" value={`${after.complete_days || 0}/${after.expected_days || 0}`} />
        <AuditMetric label="Missing" value={fmtInt(after.missing_days || 0)} />
        <AuditMetric label="Fill" value={fillText} />
      </div>
    </Panel>
  );
}

function AuditMetric({ label, value }) {
  return (
    <div className="rounded-md border border-line bg-bg-2 p-2 min-w-0">
      <div className="text-[10px] uppercase tracking-wider text-dimmer">{label}</div>
      <div className="text-xs font-mono mt-0.5 truncate" title={String(value)}>{value}</div>
    </div>
  );
}

function OptionBacktestCard({ optionBacktest }) {
  if (!optionBacktest?.enabled) return null;
  const metrics = optionBacktest.metrics || {};
  const coverage = optionBacktest.coverage || {};
  const data = optionBacktest.data || {};
  const autoFetch = data.auto_fetch || {};
  const trades = optionBacktest.trades || [];
  const paired = Number(metrics.paired_trade_count || 0);
  const totalSpot = Number(coverage.spot_trade_count || 0);
  const trusted = totalSpot > 0 && paired === totalSpot && !coverage.missing_contract && !coverage.missing_entry_candle && !coverage.missing_exit_candle;
  const badgeClass = trusted
    ? "bg-emerald-950 text-emerald-200 border-emerald-900"
    : "bg-amber-950 text-amber-200 border-amber-900";

  return (
    <Panel
      title="Option Execution"
      testid="option-backtest-card"
      right={<ShieldCheck className={`w-4 h-4 ${trusted ? "text-success" : "text-amber-300"}`} />}
    >
      {data.candles_capped && (
        <div
          className="mb-3 rounded-md border border-amber-900 bg-amber-950 text-amber-200 p-2 flex items-start gap-2 text-[11px]"
          role="alert"
          data-testid="option-candles-capped-warning"
        >
          <AlertTriangle className="w-3.5 h-3.5 mt-px shrink-0 text-amber-300" />
          <span>
            Option-candle load hit its row cap. Candles load oldest-first, so the{" "}
            <span className="font-semibold">newest</span> candles were dropped — trades in the most
            recent period may be unpaired and this result is incomplete. Narrow the date range and
            re-run to confirm pairing.
          </span>
        </div>
      )}
      <div className="grid grid-cols-2 lg:grid-cols-5 gap-2 mb-3">
        <AuditMetric label="Option P&L" value={fmtPnL(metrics.total_option_pnl_value)} />
        <AuditMetric label="Paired" value={`${fmtInt(paired)}/${fmtInt(totalSpot)}`} />
        <AuditMetric label="Win Rate" value={fmtPct(metrics.win_rate)} />
        <AuditMetric label="Candles" value={fmtInt(data.candles_loaded || 0)} />
        <div className="rounded-md border border-line bg-bg-2 p-2">
          <div className="text-[10px] uppercase tracking-wider text-dimmer">Trust</div>
          <div className={`mt-1 inline-flex text-[10px] px-1.5 py-0.5 rounded border font-mono ${badgeClass}`}>
            {trusted ? "paired" : "review"}
          </div>
        </div>
      </div>

      {/* Rupee account view: capital, ending equity, return, drawdown, Sharpe. */}
      {optionBacktest.portfolio && optionBacktest.sizing_config?.enabled && (
        <div className="mb-3" data-testid="option-portfolio-summary">
          <div className="text-[10px] uppercase tracking-wider text-dimmer mb-1">Account (rupee) — {optionBacktest.sizing_config.mode === "premium_at_risk" ? `${optionBacktest.sizing_config.risk_per_trade_pct}% risk/trade` : "fixed lots"}</div>
          <div className="grid grid-cols-2 lg:grid-cols-6 gap-2">
            <AuditMetric label="Capital" value={`₹${fmtInt(optionBacktest.portfolio.starting_capital)}`} />
            <AuditMetric label="Ending Equity" value={`₹${fmtInt(optionBacktest.portfolio.ending_equity)}`} />
            <div className="rounded-md border border-line bg-bg-2 p-2 min-w-0">
              <div className="text-[10px] uppercase tracking-wider text-dimmer">Return</div>
              <div className={`text-xs font-mono mt-0.5 ${colorPnL(optionBacktest.portfolio.total_return_pct)}`}>{fmtPct(optionBacktest.portfolio.total_return_pct, 2)}</div>
            </div>
            <div className="rounded-md border border-line bg-bg-2 p-2 min-w-0">
              <div className="text-[10px] uppercase tracking-wider text-dimmer">Max DD</div>
              <div className="text-xs font-mono mt-0.5 text-danger">{fmtPct(optionBacktest.portfolio.max_drawdown_pct, 2)}</div>
            </div>
            <AuditMetric label="Sharpe (daily)" value={optionBacktest.portfolio.sharpe_daily ?? "—"} />
            <AuditMetric label="Sortino" value={optionBacktest.portfolio.sortino_daily ?? "—"} />
          </div>
        </div>
      )}

      {/* Cost summary: gross vs net after rupee charges + spread. */}
      {optionBacktest.cost_config?.enabled && (
        <div className="mb-3 rounded-md border border-line bg-bg-2 p-2 flex flex-wrap items-center gap-3 text-[11px]" data-testid="option-cost-summary">
          <span className="text-dim">Costs: <span className="font-mono text-foreground">on</span></span>
          <span className="font-mono text-dimmer">gross {fmtPnL(metrics.total_gross_option_pnl_value)}</span>
          <span className="font-mono text-rose-300">charges -{fmtNum(metrics.total_charges, 2)}</span>
          <span className={`font-mono ${colorPnL(metrics.total_option_pnl_value)}`}>net {fmtPnL(metrics.total_option_pnl_value)}</span>
          <span className="text-dimmer font-mono ml-auto">
            brokerage ₹{optionBacktest.cost_config.brokerage_per_order}/order · spread {optionBacktest.cost_config.spread_pct_of_premium}%
          </span>
        </div>
      )}

      {/* Exit-mode summary: when premium SL/target is on, show the exit breakdown. */}
      {optionBacktest.exit_mode === "option_levels" && (
        <div className="mb-3 rounded-md border border-line bg-bg-2 p-2 flex flex-wrap items-center gap-3 text-[11px]" data-testid="option-exit-mode-summary">
          <span className="text-dim">Exit mode: <span className="font-mono text-foreground">option premium SL/target</span></span>
          <span className="text-emerald-300 font-mono">target {fmtInt(metrics.option_target_exits || 0)}</span>
          <span className="text-rose-300 font-mono">stop {fmtInt(metrics.option_stop_exits || 0)}</span>
          <span className="text-dimmer font-mono">signal/EOD {fmtInt(metrics.option_signal_exits || 0)}</span>
          {optionBacktest.option_exit_config && (
            <span className="text-dimmer font-mono ml-auto">
              {optionBacktest.option_exit_config.target_pts != null
                ? `T ${optionBacktest.option_exit_config.target_pts}pt / S ${optionBacktest.option_exit_config.stop_pts ?? "—"}pt`
                : `T ${optionBacktest.option_exit_config.target_pct ?? "—"}% / S ${optionBacktest.option_exit_config.stop_pct ?? "—"}%`}
            </span>
          )}
        </div>
      )}

      {/* DTE filter summary: how many sessions matched the selected DTE. */}
      {data.dte_filter && data.dte_filter.filter && data.dte_filter.filter !== "all" && (
        <div className="mb-3 rounded-md border border-line bg-bg-2 p-2 flex flex-wrap items-center gap-3 text-[11px]" data-testid="option-dte-summary">
          <span className="text-dim">DTE filter: <span className="font-mono text-foreground uppercase">{data.dte_filter.filter}</span></span>
          <span className="text-dimmer font-mono">
            {fmtInt(data.dte_filter.matched_trades || 0)} of {fmtInt(data.dte_filter.input_trades || 0)} spot signals matched
          </span>
          {(data.dte_filter.matched_trades || 0) === 0 && (
            <span className="text-amber-300">No signals fell on this DTE in the window — widen the date range or pick another DTE.</span>
          )}
        </div>
      )}

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-2">
        <div className="rounded-md border border-line bg-bg-2 p-2" data-testid="option-pairing-coverage">
          <div className="text-[10px] uppercase tracking-wider text-dimmer mb-1">Pairing Coverage</div>
          <div className="grid grid-cols-2 gap-x-3 gap-y-1 text-xs">
            <PairMetric label="Missing contract" value={coverage.missing_contract || 0} />
            <PairMetric label="Missing entry" value={coverage.missing_entry_candle || 0} />
            <PairMetric label="Missing exit" value={coverage.missing_exit_candle || 0} />
            <PairMetric label="Keys needed" value={data.instrument_keys_needed || 0} />
          </div>
        </div>
        <div className="rounded-md border border-line bg-bg-2 p-2" data-testid="option-auto-fetch-status">
          <div className="text-[10px] uppercase tracking-wider text-dimmer mb-1">Auto-Fetch</div>
          <div className="grid grid-cols-2 gap-x-3 gap-y-1 text-xs">
            <PairMetric label="Status" value={autoFetch.status || "skipped"} />
            <PairMetric label="Keys" value={autoFetch.keys_fetched || 0} />
            <PairMetric label="Added" value={autoFetch.candles_added || 0} />
            <PairMetric label="Failed" value={(autoFetch.failed || []).length} />
          </div>
        </div>
      </div>

      {trades.length > 0 && (
        <div className="overflow-x-auto mt-3 max-h-[260px]">
          <table className="w-full text-xs">
            <thead className="sticky top-0 bg-bg-2 z-10">
              <tr className="text-dim">
                <th className="text-left p-2">#</th>
                <th className="text-left p-2">Option</th>
                <th className="text-left p-2">Dir</th>
                <th className="text-right p-2">Entry</th>
                <th className="text-right p-2">Exit</th>
                <th className="text-right p-2">Target</th>
                <th className="text-right p-2">Stop</th>
                <th className="text-right p-2">Lots</th>
                <th className="text-left p-2">Exit Reason</th>
                <th className="text-right p-2">P&L</th>
                <th className="text-left p-2">Status</th>
              </tr>
            </thead>
            <tbody>
              {trades.slice(0, 50).map((t, idx) => (
                <tr key={`${t.index_trade_id}-${idx}`} className="border-b border-line">
                  <td className="p-2 font-mono">{idx + 1}</td>
                  <td className="p-2 font-mono">{t.trading_symbol || t.instrument_key || "-"}</td>
                  <td className={`p-2 font-mono font-medium ${t.direction === "CE" ? "text-emerald-300" : "text-rose-300"}`}>{t.direction}</td>
                  <td className="p-2 text-right font-mono">{fmtNum(t.entry_option_price)}</td>
                  <td className="p-2 text-right font-mono">{fmtNum(t.exit_option_price)}</td>
                  <td className="p-2 text-right font-mono text-dimmer">{t.option_target_level != null ? fmtNum(t.option_target_level) : "—"}</td>
                  <td className="p-2 text-right font-mono text-dimmer">{t.option_stop_level != null ? fmtNum(t.option_stop_level) : "—"}</td>
                  <td className="p-2 text-right font-mono" title={t.risk_amount != null ? `risk ₹${fmtNum(t.risk_amount, 0)}${t.risk_exceeded ? " (exceeded budget)" : ""}` : ""}>
                    {t.lots != null ? t.lots : "—"}{t.risk_exceeded ? <span className="text-amber-300"> ⚠</span> : ""}
                  </td>
                  <td className="p-2 text-[10px]">
                    <ExitReasonBadge reason={t.option_exit_reason} />
                  </td>
                  <td className={`p-2 text-right font-mono ${colorPnL(t.option_pnl_value)}`}>{fmtPnL(t.option_pnl_value)}</td>
                  <td className="p-2 text-[10px] text-dim" title={t.miss_reason || ""}>{t.status}{t.miss_reason ? " ⓘ" : ""}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Panel>
  );
}

function ExitReasonBadge({ reason }) {
  if (!reason) return <span className="text-dimmer">—</span>;
  const map = {
    OPTION_TARGET: "bg-emerald-950 text-emerald-200 border-emerald-900",
    OPTION_STOP: "bg-rose-950 text-rose-200 border-rose-900",
    OPTION_SIGNAL_EXIT: "bg-slate-800 text-slate-200 border-slate-700",
    SPOT_EXIT: "bg-slate-800 text-slate-200 border-slate-700",
  };
  const label = {
    OPTION_TARGET: "target",
    OPTION_STOP: "stop",
    OPTION_SIGNAL_EXIT: "signal exit",
    SPOT_EXIT: "spot exit",
  }[reason] || reason.toLowerCase();
  return (
    <span className={`px-1.5 py-0.5 rounded border font-mono ${map[reason] || "bg-bg-3 text-dim border-line"}`}>
      {label}
    </span>
  );
}

function PairMetric({ label, value }) {
  return (
    <>
      <span className="text-dim truncate">{label}</span>
      <span className="text-right font-mono truncate" title={String(value)}>{typeof value === "number" ? fmtInt(value) : value}</span>
    </>
  );
}

// Context edge table — where the strategy actually makes/loses money, by
// regime / time-of-day / DTE / VIX bucket. This is the regime-routing insight.
const CONTEXT_DIMS = [
  { key: "regime", label: "Regime" },
  { key: "time_of_day", label: "Time of Day" },
  { key: "dte", label: "DTE" },
  { key: "vix_bucket", label: "VIX Regime" },
];

function ContextBreakdownCard({ optionBacktest }) {
  if (!optionBacktest?.enabled) return null;
  const cb = optionBacktest.context_breakdown;
  if (!cb) return null;
  const hasAny = CONTEXT_DIMS.some((d) => cb[d.key] && Object.keys(cb[d.key]).length > 0);
  if (!hasAny) return null;

  return (
    <Panel title="Context Edge — where this strategy works" testid="context-breakdown-card">
      <div className="text-[11px] text-dimmer mb-2">
        Net option P&L grouped by market context. Use this to find the regimes/sessions/DTEs where the strategy
        has real edge — and where to switch it off. (VIX shows once India VIX is ingested.)
      </div>
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
        {CONTEXT_DIMS.map((dim) => {
          const buckets = cb[dim.key] || {};
          const rows = Object.entries(buckets).sort((a, b) => (b[1].total_pnl_value || 0) - (a[1].total_pnl_value || 0));
          if (rows.length === 0) return null;
          return (
            <div key={dim.key} className="rounded-md border border-line bg-bg-2 p-2" data-testid={`context-dim-${dim.key}`}>
              <div className="text-[10px] uppercase tracking-wider text-dimmer mb-1">{dim.label}</div>
              <table className="w-full text-xs">
                <thead>
                  <tr className="text-dim">
                    <th className="text-left p-1">Bucket</th>
                    <th className="text-right p-1">Trades</th>
                    <th className="text-right p-1">Win%</th>
                    <th className="text-right p-1">Net P&L</th>
                    <th className="text-right p-1">Avg</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map(([bucket, s]) => (
                    <tr key={bucket} className="border-t border-line">
                      <td className="p-1 font-mono">{dim.key === "dte" && bucket !== "UNKNOWN" ? `DTE${bucket}` : bucket}</td>
                      <td className="p-1 text-right font-mono text-dim">{fmtInt(s.trade_count)}</td>
                      <td className="p-1 text-right font-mono">{fmtPct(s.win_rate, 1)}</td>
                      <td className={`p-1 text-right font-mono ${colorPnL(s.total_pnl_value)}`}>{fmtPnL(s.total_pnl_value)}</td>
                      <td className={`p-1 text-right font-mono ${colorPnL(s.avg_pnl_value)}`}>{fmtPnL(s.avg_pnl_value)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          );
        })}
      </div>
    </Panel>
  );
}

// ---- Client-side research analytics (no backend math) ----
// MAE = Maximum Adverse Excursion: how far a trade ran AGAINST you before it
// closed. MFE = Maximum Favorable Excursion: how far it ran in your favor.
// When option execution ran we use the option-leg excursions (premium points —
// what a real stop/target acts on); otherwise the spot-leg ones.
function median(arr) {
  if (!arr.length) return null;
  const s = [...arr].sort((a, b) => a - b);
  const mid = Math.floor(s.length / 2);
  return s.length % 2 ? s[mid] : (s[mid - 1] + s[mid]) / 2;
}

function buildHistogram(values, bins = 12) {
  if (!values.length) return [];
  const max = Math.max(...values, 0);
  if (max <= 0) return [{ lo: 0, hi: 0, count: values.length }];
  const width = max / bins;
  const out = Array.from({ length: bins }, (_, i) => ({ lo: i * width, hi: (i + 1) * width, count: 0 }));
  for (const v of values) {
    const idx = Math.min(bins - 1, Math.max(0, Math.floor(v / width)));
    out[idx].count += 1;
  }
  return out;
}

function MiniHistogram({ data, color, testid }) {
  const maxCount = Math.max(...data.map((d) => d.count), 1);
  return (
    <div className="flex items-end gap-0.5 h-20 border-b border-line" data-testid={testid}>
      {data.map((d, i) => (
        <div
          key={i}
          className="flex-1 flex items-end h-full"
          title={`${fmtNum(d.lo, 1)}–${fmtNum(d.hi, 1)} pts · ${fmtInt(d.count)} trades`}
        >
          <div className={`w-full rounded-t-sm ${color}`} style={{ height: `${(d.count / maxCount) * 100}%` }} />
        </div>
      ))}
    </div>
  );
}

function MaeMfeCard({ trades, optionBacktest }) {
  const optionEnabled = !!optionBacktest?.enabled;
  const source = optionEnabled ? (optionBacktest.trades || []) : (trades || []);
  const mfeKey = optionEnabled ? "option_mfe_pts" : "mfe_pts";
  const maeKey = optionEnabled ? "option_mae_pts" : "mae_pts";
  const mfe = source.map((t) => Number(t[mfeKey])).filter((v) => Number.isFinite(v));
  const mae = source.map((t) => Number(t[maeKey])).filter((v) => Number.isFinite(v));
  if (mfe.length === 0 && mae.length === 0) return null;

  const medMfe = median(mfe);
  const medMae = median(mae);
  const maxMfe = mfe.length ? Math.max(...mfe) : 0;
  const maxMae = mae.length ? Math.max(...mae) : 0;
  const mfeHist = buildHistogram(mfe);
  const maeHist = buildHistogram(mae);
  const unit = optionEnabled ? "premium pts" : "spot pts";

  return (
    <Panel title="MAE / MFE Distribution" testid="mae-mfe-card">
      <div className="text-[11px] text-dimmer mb-3">
        Excursion of each trade in {unit}, from the {optionEnabled ? "paired option leg" : "spot leg"}. MFE = best
        unrealized move in your favor; MAE = worst move against you before exit.
      </div>
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <div data-testid="mfe-histogram-block">
          <div className="flex items-baseline justify-between mb-1">
            <span className="text-[10px] uppercase tracking-wider text-dimmer">MFE (favorable)</span>
            <span className="text-xs font-mono text-emerald-300">median {fmtNum(medMfe)} · max {fmtNum(maxMfe)}</span>
          </div>
          <MiniHistogram data={mfeHist} color="bg-emerald-700" testid="mfe-histogram" />
          <div className="flex justify-between text-[10px] text-dimmer font-mono mt-0.5">
            <span>0</span><span>{fmtNum(maxMfe, 1)}</span>
          </div>
        </div>
        <div data-testid="mae-histogram-block">
          <div className="flex items-baseline justify-between mb-1">
            <span className="text-[10px] uppercase tracking-wider text-dimmer">MAE (adverse)</span>
            <span className="text-xs font-mono text-rose-300">median {fmtNum(medMae)} · max {fmtNum(maxMae)}</span>
          </div>
          <MiniHistogram data={maeHist} color="bg-rose-700" testid="mae-histogram" />
          <div className="flex justify-between text-[10px] text-dimmer font-mono mt-0.5">
            <span>0</span><span>{fmtNum(maxMae, 1)}</span>
          </div>
        </div>
      </div>
      {medMae != null && (
        <div className="mt-3 text-[11px] text-dim" data-testid="mae-mfe-hint">
          Median MAE is <span className="font-mono text-foreground">{fmtNum(medMae)} {unit}</span> — a stop tighter than
          that would have closed half your trades early, including winners that later recovered.
        </div>
      )}
    </Panel>
  );
}

// ---- Monte Carlo: bootstrap-resample the per-trade P&L sequence ----
// We draw N trades WITH REPLACEMENT from the run's realized per-trade P&L,
// 1,000 times, rebuilding the equity path each time. This yields a distribution
// of both max drawdown and ending P&L (a plain order-shuffle would leave ending
// P&L constant). Shows P5/P50/P95 drawdown, ending-P&L percentiles, and the
// probability the strategy ends underwater P(net<0). All client-side.
function quantileAsc(sortedAsc, q) {
  if (!sortedAsc.length) return null;
  const pos = (sortedAsc.length - 1) * q;
  const base = Math.floor(pos);
  const rest = pos - base;
  const next = sortedAsc[base + 1];
  return next !== undefined ? sortedAsc[base] + rest * (next - sortedAsc[base]) : sortedAsc[base];
}

function MonteCarloCard({ trades, optionBacktest }) {
  const optionEnabled = !!optionBacktest?.enabled;
  const isMoney = optionEnabled;
  const sims = useMemo(() => {
    const source = optionEnabled ? (optionBacktest?.trades || []) : (trades || []);
    const pnlKey = optionEnabled ? "option_pnl_value" : "pnl_pts";
    const pnl = source.map((t) => Number(t[pnlKey])).filter((v) => Number.isFinite(v)).slice(0, 1000);
    const N = pnl.length;
    if (N < 5) return { N };
    const RUNS = 1000;
    const dd = new Array(RUNS);
    const end = new Array(RUNS);
    let negCount = 0;
    for (let r = 0; r < RUNS; r++) {
      let cum = 0, peak = 0, maxDD = 0;
      for (let i = 0; i < N; i++) {
        cum += pnl[(Math.random() * N) | 0];
        if (cum > peak) peak = cum;
        const d = peak - cum;
        if (d > maxDD) maxDD = d;
      }
      dd[r] = maxDD;
      end[r] = cum;
      if (cum < 0) negCount += 1;
    }
    dd.sort((a, b) => a - b);
    end.sort((a, b) => a - b);
    return {
      N, runs: RUNS,
      ddP5: quantileAsc(dd, 0.05), ddP50: quantileAsc(dd, 0.5), ddP95: quantileAsc(dd, 0.95),
      endP5: quantileAsc(end, 0.05), endP50: quantileAsc(end, 0.5), endP95: quantileAsc(end, 0.95),
      pNeg: negCount / RUNS,
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [trades, optionBacktest, optionEnabled]);

  const fmtV = (v) => (v == null ? "–" : isMoney ? `₹${fmtInt(v)}` : fmtNum(v));
  const unit = isMoney ? "net ₹" : "spot pts";

  if (!sims || sims.N < 5) {
    return (
      <Panel title="Monte Carlo (trade resampling)" testid="monte-carlo-card">
        <div className="text-xs text-dimmer">Need at least 5 trades to resample. This run has {fmtInt(sims?.N || 0)}.</div>
      </Panel>
    );
  }

  const pNegPct = sims.pNeg * 100;
  return (
    <Panel title="Monte Carlo (trade resampling)" testid="monte-carlo-card">
      <div className="text-[11px] text-dimmer mb-3">
        {fmtInt(sims.runs)} bootstrap runs over {fmtInt(sims.N)} trades (drawn with replacement, {unit}). Shows how
        path-luck could reshape drawdown and the final result given this strategy's per-trade P&L.
      </div>
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-2">
        <div className="rounded-md border border-line bg-bg-2 p-2" data-testid="mc-drawdown-block">
          <div className="text-[10px] uppercase tracking-wider text-dimmer">Max DD P5 / P50 / P95</div>
          <div className="text-xs font-mono mt-0.5 text-danger">
            {fmtV(sims.ddP5)} <span className="text-dimmer">/</span> {fmtV(sims.ddP50)} <span className="text-dimmer">/</span> {fmtV(sims.ddP95)}
          </div>
        </div>
        <div className="rounded-md border border-line bg-bg-2 p-2" data-testid="mc-ending-block">
          <div className="text-[10px] uppercase tracking-wider text-dimmer">Ending P&L P5 / P50 / P95</div>
          <div className="text-xs font-mono mt-0.5">
            <span className={colorPnL(sims.endP5)}>{fmtV(sims.endP5)}</span> <span className="text-dimmer">/</span>{" "}
            <span className={colorPnL(sims.endP50)}>{fmtV(sims.endP50)}</span> <span className="text-dimmer">/</span>{" "}
            <span className={colorPnL(sims.endP95)}>{fmtV(sims.endP95)}</span>
          </div>
        </div>
        <div className="rounded-md border border-line bg-bg-2 p-2" data-testid="mc-pneg-block">
          <div className="text-[10px] uppercase tracking-wider text-dimmer">P(net &lt; 0)</div>
          <div className={`text-base font-mono mt-0.5 ${pNegPct >= 25 ? "text-danger" : pNegPct >= 10 ? "text-amber-300" : "text-success"}`}>
            {fmtPct(pNegPct, 1)}
          </div>
        </div>
        <div className="rounded-md border border-line bg-bg-2 p-2">
          <div className="text-[10px] uppercase tracking-wider text-dimmer">Median Ending</div>
          <div className={`text-base font-mono mt-0.5 ${colorPnL(sims.endP50)}`}>{fmtV(sims.endP50)}</div>
        </div>
      </div>
      <div className="mt-2 text-[11px] text-dim" data-testid="monte-carlo-hint">
        {pNegPct >= 25
          ? `In ${fmtPct(pNegPct, 0)} of resampled paths this strategy ends underwater — the edge is fragile to trade order/luck.`
          : `Only ${fmtPct(pNegPct, 0)} of resampled paths end underwater, and the P5 drawdown is ${fmtV(sims.ddP5)} — size for the P95 drawdown of ${fmtV(sims.ddP95)}.`}
      </div>
    </Panel>
  );
}

function WalkForwardCard({ wf }) {
  if (!wf || !wf.folds || wf.folds.length === 0) {
    return (
      <Panel title="Walk-Forward Split Check (same params, IS vs OOS)" testid="walkforward-panel">
        <div className="text-xs text-dimmer">Walk-forward split check disabled or insufficient candles (≥200 needed).</div>
      </Panel>
    );
  }
  const iv = wf.is_vs_oos;
  const diverging = iv.divergence_warning;
  return (
    <Panel title="Walk-Forward Split Check (same params, IS vs OOS)" testid="walkforward-panel">
      <div className="grid grid-cols-2 gap-3 mb-3">
        <div className="rounded-md border border-line bg-bg-2 p-2">
          <div className="text-[10px] uppercase tracking-wider text-dimmer">Avg IS Win Rate</div>
          <div className="text-base font-mono mt-0.5">{fmtPct(iv.avg_is_win_rate)}</div>
        </div>
        <div className={`rounded-md border bg-bg-2 p-2 ${diverging ? "border-amber-900" : "border-line"}`}>
          <div className="text-[10px] uppercase tracking-wider text-dimmer">Avg OOS Win Rate</div>
          <div className="text-base font-mono mt-0.5">{fmtPct(iv.avg_oos_win_rate)}</div>
        </div>
      </div>
      <div className="text-xs space-y-1">
        <div className="flex justify-between"><span className="text-dim">Folds</span><span className="font-mono">{iv.fold_count}</span></div>
        <div className="flex justify-between"><span className="text-dim">Avg IS PF</span><span className="font-mono">{fmtNum(iv.avg_is_profit_factor)}</span></div>
        <div className="flex justify-between"><span className="text-dim">Avg OOS PF</span><span className="font-mono">{fmtNum(iv.avg_oos_profit_factor)}</span></div>
        <div className="flex justify-between"><span className="text-dim">OOS Trades</span><span className="font-mono">{fmtInt(wf.stitched_oos_trade_count)}</span></div>
      </div>
    </Panel>
  );
}

function SignalFunnelCard({ funnel, regimeDist, totalRegime }) {
  const stages = [
    { label: "Bars evaluated", value: funnel.evaluated, color: "bg-info" },
    { label: "Out of window", value: funnel.out_of_window, color: "bg-slate-600" },
    { label: "Score below threshold", value: funnel.score_below_threshold, color: "bg-slate-500" },
    { label: "Blocked by strategy", value: funnel.blocked_by_strategy, color: "bg-amber-700" },
    { label: "Blocked by pretrade", value: funnel.blocked_by_pretrade, color: "bg-amber-600" },
    { label: "In cooldown", value: funnel.in_cooldown, color: "bg-slate-700" },
    { label: "Signals FIRED", value: funnel.signals_fired, color: "bg-emerald-600" },
  ];
  const max = Math.max(...stages.map((s) => s.value || 0), 1);
  return (
    <Panel title="Signal Funnel + Regimes" testid="signal-funnel-panel">
      <div className="space-y-1.5 mb-3">
        {stages.map((s) => (
          <div key={s.label} className="flex items-center gap-2 text-xs">
            <div className="w-32 text-dim shrink-0">{s.label}</div>
            <div className="flex-1 h-3 bg-bg-2 rounded-sm overflow-hidden border border-line">
              <div className={`h-full ${s.color}`} style={{ width: `${((s.value || 0) / max) * 100}%` }}></div>
            </div>
            <div className="w-12 text-right font-mono">{fmtInt(s.value || 0)}</div>
          </div>
        ))}
      </div>
      <div className="border-t border-line pt-2">
        <div className="text-[10px] uppercase tracking-wider text-dimmer mb-1">Regime Distribution</div>
        <div className="flex flex-wrap gap-1">
          {Object.entries(regimeDist).sort((a, b) => b[1] - a[1]).map(([r, c]) => (
            <RegimeBadge key={r} regime={r} count={c} total={totalRegime} />
          ))}
        </div>
      </div>
    </Panel>
  );
}

const TRADE_COLUMNS = [
  { key: "idx", label: "#", align: "left", sortable: true },
  { key: "direction", label: "Dir", align: "left", sortable: true },
  { key: "entry_ts", label: "Entry", align: "left", sortable: true },
  { key: "entry_price", label: "Entry Px", align: "right", sortable: true },
  { key: "exit_ts", label: "Exit", align: "left", sortable: true },
  { key: "exit_price", label: "Exit Px", align: "right", sortable: true },
  { key: "exit_reason", label: "Reason", align: "left", sortable: true },
  { key: "score", label: "Score", align: "right", sortable: true },
  { key: "pnl_pts", label: "P&L (pts)", align: "right", sortable: true },
  { key: "pnl_pct", label: "P&L %", align: "right", sortable: true },
];

function SortHeader({ col, sort, onSort }) {
  const active = sort.key === col.key;
  const Icon = !active ? ChevronsUpDown : sort.dir === "asc" ? ArrowUp : ArrowDown;
  const alignCls = col.align === "right" ? "text-right" : "text-left";
  if (!col.sortable) {
    return <th className={`${alignCls} p-2`}>{col.label}</th>;
  }
  return (
    <th className={`${alignCls} p-2`}>
      <button
        type="button"
        onClick={() => onSort(col.key)}
        className={`inline-flex items-center gap-1 hover:text-foreground transition-colors ${active ? "text-foreground" : ""} ${col.align === "right" ? "flex-row-reverse" : ""}`}
        data-testid={`trades-sort-${col.key}`}
        title={`Sort by ${col.label}`}
      >
        <span>{col.label}</span>
        <Icon className={`w-3 h-3 ${active ? "text-info" : "text-dimmer"}`} />
      </button>
    </th>
  );
}

function TradesTable({ trades, optionBacktest }) {
  const { panelRef, maximized, toggleMaximize } = useMaximize();
  const [sort, setSort] = useState({ key: "idx", dir: "asc" });
  const [dirFilter, setDirFilter] = useState("ALL");
  const [reasonFilter, setReasonFilter] = useState("ALL");
  const [resultFilter, setResultFilter] = useState("ALL"); // ALL | win | loss

  const onSort = (key) => {
    setSort((s) => (s.key === key ? { key, dir: s.dir === "asc" ? "desc" : "asc" } : { key, dir: "asc" }));
  };

  // When option backtest ran, build a map from the spot trade index to its
  // matched option leg so the table can show the correlated option detail.
  const optionEnabled = !!optionBacktest?.enabled;
  const optionByTradeId = useMemo(() => {
    const map = {};
    for (const ot of (optionBacktest?.trades || [])) {
      if (ot?.index_trade_id != null) map[ot.index_trade_id] = ot;
    }
    return map;
  }, [optionBacktest]);

  // Stamp original index so "#" remains stable regardless of sort/filter, and
  // flatten the matched option leg fields onto each row for sorting/rendering.
  const indexed = useMemo(
    () => (trades || []).map((t, i) => {
      const opt = optionByTradeId[i] || null;
      return {
        ...t,
        idx: i + 1,
        opt_symbol: opt?.trading_symbol || opt?.instrument_key || null,
        opt_strike: opt?.strike ?? null,
        opt_side: opt?.side ?? null,
        opt_entry: opt?.entry_option_price ?? null,
        opt_exit: opt?.exit_option_price ?? null,
        // Premium move in % — long-option (buying) semantics: exit vs entry.
        opt_pnl_pct:
          opt?.entry_option_price != null && opt?.exit_option_price != null && Number(opt.entry_option_price) !== 0
            ? ((Number(opt.exit_option_price) - Number(opt.entry_option_price)) / Number(opt.entry_option_price)) * 100
            : null,
        opt_pnl_value: opt?.option_pnl_value ?? null,
        opt_exit_reason: opt?.option_exit_reason ?? null,
        opt_status: opt?.status ?? null,
        opt_lots: opt?.lots ?? null,
        opt_qty: opt?.quantity ?? null,
        opt_charges: opt?.status === "PAIRED" ? Number(opt.total_charges || 0) : null,
        // Buy value loads all charges on entry so Sell − Buy = net option P&L
        // (entry premium × qty + round-trip charges; sell = exit premium × qty).
        opt_buy_value: opt?.status === "PAIRED"
          ? Number(opt.entry_option_price) * Number(opt.quantity) + Number(opt.total_charges || 0)
          : null,
        opt_sell_value: opt?.status === "PAIRED"
          ? Number(opt.exit_option_price) * Number(opt.quantity)
          : null,
      };
    }),
    [trades, optionByTradeId]
  );

  const exitReasons = useMemo(() => {
    const set = new Set();
    for (const t of indexed) if (t.exit_reason) set.add(t.exit_reason);
    return Array.from(set).sort();
  }, [indexed]);

  const filtered = useMemo(() => {
    let rows = indexed;
    if (dirFilter !== "ALL") rows = rows.filter((t) => t.direction === dirFilter);
    if (reasonFilter !== "ALL") rows = rows.filter((t) => t.exit_reason === reasonFilter);
    if (resultFilter === "win") rows = rows.filter((t) => Number(t.pnl_pts) > 0);
    if (resultFilter === "loss") rows = rows.filter((t) => Number(t.pnl_pts) <= 0);
    return rows;
  }, [indexed, dirFilter, reasonFilter, resultFilter]);

  const sorted = useMemo(() => {
    const rows = [...filtered];
    const { key, dir } = sort;
    const mul = dir === "asc" ? 1 : -1;
    rows.sort((a, b) => {
      const av = a[key];
      const bv = b[key];
      // Numeric vs string comparison.
      const an = typeof av === "number" ? av : Number(av);
      const bn = typeof bv === "number" ? bv : Number(bv);
      if (Number.isFinite(an) && Number.isFinite(bn)) return (an - bn) * mul;
      return String(av ?? "").localeCompare(String(bv ?? "")) * mul;
    });
    return rows;
  }, [filtered, sort]);

  if (!trades.length) {
    return (
      <Panel title="Trades" testid="trades-panel">
        <div className="text-xs text-dimmer">No trades were taken in this run.</div>
      </Panel>
    );
  }

  const FilterSelect = ({ value, onChange, children, testid, title }) => (
    <select
      value={value}
      onChange={(e) => onChange(e.target.value)}
      className="h-7 rounded-md border border-line bg-bg-2 px-2 text-[11px] text-foreground outline-none focus:ring-1 focus:ring-ring"
      data-testid={testid}
      title={title}
    >
      {children}
    </select>
  );

  // Build column set: base spot columns, plus option columns when paired.
  const columns = optionEnabled
    ? [
        ...TRADE_COLUMNS,
        { key: "opt_symbol", label: "Opt Leg", align: "left", sortable: true },
        { key: "opt_qty", label: "Lots (Qty)", align: "right", sortable: true },
        { key: "opt_entry", label: "Opt Entry", align: "right", sortable: true },
        { key: "opt_exit", label: "Opt Exit", align: "right", sortable: true },
        { key: "opt_pnl_pct", label: "Opt P&L%", align: "right", sortable: true },
        { key: "opt_buy_value", label: "Buy ₹", align: "right", sortable: true },
        { key: "opt_sell_value", label: "Sell ₹", align: "right", sortable: true },
        { key: "opt_charges", label: "Charges ₹", align: "right", sortable: true },
        { key: "opt_exit_reason", label: "Opt Exit", align: "left", sortable: true },
        { key: "opt_pnl_value", label: "Opt P&L (₹)", align: "right", sortable: true },
      ]
    : TRADE_COLUMNS;

  return (
    <Panel
      rootRef={panelRef}
      className={maximized ? "flex flex-col overflow-hidden" : "overflow-auto"}
      bodyClassName={maximized ? "p-3 flex flex-col flex-1" : "p-3"}
      bodyStyle={maximized ? { minHeight: 0 } : undefined}
      title={`Trades (${sorted.length}${sorted.length !== indexed.length ? ` of ${indexed.length}` : ""})`}
      testid="trades-panel"
      right={
        <div className="flex items-center gap-1.5">
          <Filter className="w-3.5 h-3.5 text-dimmer" />
          <FilterSelect value={dirFilter} onChange={setDirFilter} testid="trades-filter-dir" title="Filter by direction">
            <option value="ALL">All dirs</option>
            <option value="CE">CE</option>
            <option value="PE">PE</option>
          </FilterSelect>
          <FilterSelect value={reasonFilter} onChange={setReasonFilter} testid="trades-filter-reason" title="Filter by exit reason">
            <option value="ALL">All exits</option>
            {exitReasons.map((r) => <option key={r} value={r}>{r}</option>)}
          </FilterSelect>
          <FilterSelect value={resultFilter} onChange={setResultFilter} testid="trades-filter-result" title="Filter by outcome">
            <option value="ALL">Win+Loss</option>
            <option value="win">Wins</option>
            <option value="loss">Losses</option>
          </FilterSelect>
          <MaximizeButton maximized={maximized} onToggle={toggleMaximize} label="trades" testid="trades-maximize" />
        </div>
      }
    >
      {optionEnabled && (
        <div className="mb-2 text-[10px] text-dimmer">
          Spot signal paired with the option leg. "Opt Leg" is the contract chosen for the signal; Opt Entry/Exit are premium fills (after slippage).
        </div>
      )}
      <div
        className={maximized ? "overflow-auto flex-1" : "overflow-x-auto max-h-[400px]"}
        style={maximized ? { minHeight: 0 } : undefined}
      >
        <table className="w-full text-xs" data-testid="trades-table">
          <thead className="sticky top-0 bg-bg-2 z-10">
            <tr className="text-dim">
              {columns.map((col) => (
                <SortHeader key={col.key} col={col} sort={sort} onSort={onSort} />
              ))}
            </tr>
          </thead>
          <tbody>
            {sorted.map((t) => (
              <tr key={t.idx} className="border-b border-line hover:bg-bg-2" data-testid="trade-row">
                <td className="p-2 font-mono">{t.idx}</td>
                <td className={`p-2 font-mono font-medium ${t.direction === "CE" ? "text-emerald-300" : "text-rose-300"}`}>{t.direction}</td>
                <td className="p-2 text-dim">{tsToTime(t.entry_ts)}</td>
                <td className="p-2 text-right font-mono">{fmtNum(t.entry_price)}</td>
                <td className="p-2 text-dim">{tsToTime(t.exit_ts)}</td>
                <td className="p-2 text-right font-mono">{fmtNum(t.exit_price)}</td>
                <td className="p-2 text-[10px] text-dim">{t.exit_reason}</td>
                <td className="p-2 text-right font-mono text-dim">{t.score}</td>
                <td className={`p-2 text-right font-mono ${colorPnL(t.pnl_pts)}`}>{fmtPnL(t.pnl_pts)}</td>
                <td className={`p-2 text-right font-mono ${colorPnL(t.pnl_pts)}`}>{fmtPct(t.pnl_pct, 2)}</td>
                {optionEnabled && (
                  <>
                    <td className="p-2 font-mono text-[10px]" title={t.opt_symbol || ""}>
                      {t.opt_symbol
                        ? <span>{t.opt_side ? <span className={t.opt_side === "CE" ? "text-emerald-300" : "text-rose-300"}>{t.opt_strike} {t.opt_side}</span> : t.opt_symbol}</span>
                        : <span className="text-dimmer">{t.opt_status ? t.opt_status.replace(/_/g, " ").toLowerCase() : "—"}</span>}
                    </td>
                    <td className="p-2 text-right font-mono text-[10px]" title={t.opt_qty != null ? `${fmtInt(t.opt_qty)} qty` : ""}>
                      {t.opt_lots != null ? `${fmtInt(t.opt_lots)} (${fmtInt(t.opt_qty)})` : "—"}
                    </td>
                    <td className="p-2 text-right font-mono">{t.opt_entry != null ? fmtNum(t.opt_entry) : "—"}</td>
                    <td className="p-2 text-right font-mono">{t.opt_exit != null ? fmtNum(t.opt_exit) : "—"}</td>
                    <td className={`p-2 text-right font-mono ${colorPnL(t.opt_pnl_pct)}`}>{t.opt_pnl_pct != null ? fmtPct(t.opt_pnl_pct, 1) : "—"}</td>
                    <td className="p-2 text-right font-mono text-dim">{t.opt_buy_value != null ? `₹${fmtInt(t.opt_buy_value)}` : "—"}</td>
                    <td className="p-2 text-right font-mono text-dim">{t.opt_sell_value != null ? `₹${fmtInt(t.opt_sell_value)}` : "—"}</td>
                    <td className="p-2 text-right font-mono text-dimmer" title="Round-trip statutory charges (brokerage/STT/exchange/GST/SEBI/stamp; 0 when 'Apply realistic costs' is off)">{t.opt_charges != null ? `₹${fmtNum(t.opt_charges, 0)}` : "—"}</td>
                    <td className="p-2 text-[10px]"><ExitReasonBadge reason={t.opt_exit_reason} /></td>
                    <td className={`p-2 text-right font-mono ${colorPnL(t.opt_pnl_value)}`}>{t.opt_pnl_value != null ? fmtPnL(t.opt_pnl_value) : "—"}</td>
                  </>
                )}
              </tr>
            ))}
            {sorted.length === 0 && (
              <tr>
                <td colSpan={columns.length} className="p-4 text-center text-dimmer">No trades match the current filters.</td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </Panel>
  );
}
