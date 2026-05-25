import { useEffect, useMemo, useState } from "react";
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
import { MultiPaneChart } from "@/components/charts/MultiPaneChart";
import { NumberSliderInput } from "@/components/NumberSliderInput";
import { Play, Save, Filter, ChevronDown, ChevronRight, Download, FileJson, FileText, FolderOpen } from "lucide-react";

const INSTRUMENTS = ["NIFTY", "BANKNIFTY", "SENSEX"];
const MODES = ["SCALP", "INTRADAY"];

// Convert "YYYY-MM-DD" (interpreted as IST 09:15) to ms epoch UTC. Returns null if empty.
const dateToMs = (s, endOfDay = false) => {
  if (!s) return null;
  // IST = UTC+5:30 → IST midnight = previous day 18:30 UTC
  const [y, m, d] = s.split("-").map(Number);
  if (!y || !m || !d) return null;
  // Date.UTC returns ms UTC for given UTC y/m/d
  const istHour = endOfDay ? 15 : 9;
  const istMin = endOfDay ? 30 : 15;
  // ms = Date.UTC(y, m-1, d, istHour-5, istMin-30) but easier: use offset
  const baseUtc = Date.UTC(y, m - 1, d, istHour, istMin, 0);
  // IST is +5:30, so UTC = IST - 5:30 = baseUtc - 5h30m
  return baseUtc - (5 * 60 + 30) * 60 * 1000;
};

const msToDate = (ms) => {
  if (!ms) return "";
  // Convert UTC ms to IST date string YYYY-MM-DD
  const d = new Date(Number(ms) + (5 * 60 + 30) * 60 * 1000);
  return d.toISOString().slice(0, 10);
};

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
  });
  const [running, setRunning] = useState(false);
  const [result, setResult] = useState(null);
  const [paramsOpen, setParamsOpen] = useState(true);
  const [showFiltersOpen, setShowFiltersOpen] = useState(false);

  const refreshRuns = () => api.listBacktestRuns(50).then((d) => setPastRuns(d.items || []));
  const refreshPresets = () => api.listPresets().then((d) => setPresets(d.items || []));

  const [searchParams, setSearchParams] = useSearchParams();

  useEffect(() => {
    api.listStrategies().then((d) => setStrategies(d.items || []));
    api.listProfiles().then((d) => setProfiles(d.items || []));
    refreshRuns();
    refreshPresets();
  }, []);

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

  const applyPreset = async (name) => {
    try {
      const list = presets.length ? presets : (await api.listPresets()).items;
      const p = list.find((x) => x.name === name);
      if (!p) { toast.error(`Preset "${name}" not found`); return; }
      const cfg = p.config || {};
      setConfig((c) => ({
        ...c,
        instrument: cfg.instrument || c.instrument,
        mode: cfg.mode || c.mode,
        strategy_id: cfg.strategy_id || c.strategy_id,
        params: cfg.params || c.params,
        name: name,
      }));
      toast.success(`Preset "${name}" applied. Click Run Backtest to test it.`);
    } catch (e) {
      toast.error("Failed to apply preset");
    }
  };

  const selectedStrategy = useMemo(
    () => strategies.find((s) => s.id === config.strategy_id),
    [strategies, config.strategy_id]
  );
  const selectedProfile = profiles.find((p) => p.name === config.pretrade_profile);

  // Reset params when strategy changes
  useEffect(() => {
    if (!selectedStrategy) return;
    const defaults = {};
    for (const [k, def] of Object.entries(selectedStrategy.parameter_schema || {})) {
      defaults[k] = def.default;
    }
    setConfig((c) => ({ ...c, params: defaults }));
  }, [selectedStrategy?.id]);

  const setParam = (k, v) => setConfig((c) => ({ ...c, params: { ...c.params, [k]: v } }));

  const runBacktest = async () => {
    setRunning(true);
    setResult(null);
    try {
      const payload = {
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
      };
      const res = await api.runBacktest(payload);
      setResult(res);
      await refreshRuns();
      toast.success(`Backtest complete: ${res.metrics.trade_count} trades`);
    } catch (e) {
      const msg = e.response?.data?.detail || e.message;
      toast.error(`Backtest failed: ${msg}`);
    } finally {
      setRunning(false);
    }
  };

  const loadPastRun = async (runId) => {
    if (!runId) return;
    try {
      const r = await api.getBacktestRun(runId);
      setResult(r);
      // Restore configuration from the saved run
      setConfig((c) => ({
        ...c,
        instrument: r.instrument || c.instrument,
        mode: r.config?.mode || c.mode,
        strategy_id: r.strategy_id || r.config?.strategy_id || c.strategy_id,
        timeframe: r.config?.timeframe || c.timeframe,
        params: r.params_applied || r.config?.params || c.params,
        costs_enabled: r.config?.costs_enabled ?? c.costs_enabled,
        walkforward: r.config?.walkforward ?? c.walkforward,
        train_pct: r.config?.train_pct ?? c.train_pct,
        n_folds: r.config?.n_folds ?? c.n_folds,
        name: r.name || c.name,
        start_date: msToDate(r.config?.start_ts),
        end_date: msToDate(r.config?.end_ts),
      }));
      toast.success(`Loaded: ${r.name}`);
    } catch (e) {
      toast.error("Failed to load run");
    }
  };

  return (
    <div className="grid grid-cols-1 lg:grid-cols-[320px_minmax(0,1fr)] gap-3" data-testid="backtest-lab-page">
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
                onChange={(e) => setConfig({ ...config, name: e.target.value })}
                className="bg-bg-2 border-line h-8 mt-1"
                data-testid="backtest-name-input"
                placeholder="e.g. NIFTY scalp v2"
              />
            </div>
            <Row label="Instrument">
              <Select value={config.instrument} onValueChange={(v) => setConfig({ ...config, instrument: v })}>
                <SelectTrigger className="bg-bg-2 border-line h-8" data-testid="backtest-instrument-select">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {INSTRUMENTS.map((i) => <SelectItem key={i} value={i}>{i}</SelectItem>)}
                </SelectContent>
              </Select>
            </Row>
            <Row label="Mode">
              <Select value={config.mode} onValueChange={(v) => setConfig({ ...config, mode: v })}>
                <SelectTrigger className="bg-bg-2 border-line h-8" data-testid="backtest-mode-select">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {MODES.map((m) => <SelectItem key={m} value={m}>{m}</SelectItem>)}
                </SelectContent>
              </Select>
            </Row>
            <Row label="Strategy">
              <Select value={config.strategy_id} onValueChange={(v) => setConfig({ ...config, strategy_id: v })}>
                <SelectTrigger className="bg-bg-2 border-line h-8" data-testid="backtest-strategy-select">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {strategies.filter((s) => s.is_loaded !== false).map((s) => <SelectItem key={s.id} value={s.id}>{s.name}</SelectItem>)}
                </SelectContent>
              </Select>
            </Row>
            {selectedStrategy && (
              <div className="text-[11px] text-dim leading-snug px-1">{selectedStrategy.description}</div>
            )}
            <Row label="Pre-trade profile">
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
            </div>
            <div className="flex items-center gap-2">
              <Switch
                checked={config.walkforward}
                onCheckedChange={(v) => setConfig({ ...config, walkforward: v })}
                data-testid="backtest-walkforward-switch"
              />
              <span className="text-xs text-dim">Walk-forward (IS vs OOS)</span>
            </div>
            <div className="pt-2 border-t border-line">
              <Label className="text-xs text-dim">Date window (IST, optional)</Label>
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
          </div>
        </Panel>

        <Panel
          title="Strategy Parameters"
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
            </div>
          )}
        </Panel>

        <Button
          onClick={runBacktest}
          disabled={running}
          className="w-full bg-info text-bg-0 hover:bg-info/90 font-semibold"
          data-testid="backtest-run-button"
        >
          <Play className="w-4 h-4 mr-2" />
          {running ? "Running…" : "Run Backtest"}
        </Button>
      </aside>

      {/* RIGHT: Results */}
      <section className="min-w-0 space-y-3">
        {running ? (
          <div className="space-y-3">
            <Skeleton className="h-24 bg-bg-1" />
            <Skeleton className="h-[400px] bg-bg-1" />
            <Skeleton className="h-40 bg-bg-1" />
          </div>
        ) : !result ? (
          <EmptyResults />
        ) : (
          <ResultsView result={result} />
        )}
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

function ResultsView({ result }) {
  const m = result.metrics || {};
  const regimeDist = result.regime_distribution || {};
  const totalRegime = Object.values(regimeDist).reduce((s, v) => s + v, 0);
  const funnel = result.signal_funnel || {};

  const [candles, setCandles] = useState([]);
  useEffect(() => {
    // Fetch candles for the instrument to display in the price pane
    api.candles(result.instrument, 500).then((r) => {
      const items = (r.items || []).map((c) => ({
        time: Math.floor(Number(c.ts) / 1000),
        open: Number(c.open),
        high: Number(c.high),
        low: Number(c.low),
        close: Number(c.close),
      }));
      // Deduplicate by time + ensure ascending order (lightweight-charts requirement)
      const seen = new Set();
      const dedup = [];
      for (const c of items) {
        if (!seen.has(c.time)) {
          seen.add(c.time);
          dedup.push(c);
        }
      }
      dedup.sort((a, b) => a.time - b.time);
      setCandles(dedup);
    }).catch(() => setCandles([]));
  }, [result.instrument, result.id]);

  // Build equity / drawdown — handle dedup as well
  const equity = useMemo(() => {
    const seen = new Set();
    const out = [];
    for (const p of result.equity_curve || []) {
      const t = Math.floor(Number(p.ts) / 1000);
      if (seen.has(t)) continue;
      seen.add(t);
      out.push({ time: t, value: p.equity_pts });
    }
    return out.sort((a, b) => a.time - b.time);
  }, [result]);
  const drawdown = useMemo(() => {
    const seen = new Set();
    const out = [];
    for (const p of result.equity_curve || []) {
      const t = Math.floor(Number(p.ts) / 1000);
      if (seen.has(t)) continue;
      seen.add(t);
      out.push({ time: t, value: p.drawdown_pts });
    }
    return out.sort((a, b) => a.time - b.time);
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
        </div>
      </div>

      {/* KPI grid */}
      <div className="grid grid-cols-2 lg:grid-cols-6 gap-3">
        <MetricCard label="Trades" value={fmtInt(m.trade_count)} testid="result-trades" />
        <MetricCard label="Win Rate" value={fmtPct(m.win_rate)} testid="result-winrate" />
        <MetricCard label="Profit Factor" value={fmtNum(m.profit_factor, 2)} testid="result-pf" />
        <MetricCard label="Net P&L (pts)" value={fmtPnL(m.total_pnl_pts)} accent={colorPnL(m.total_pnl_pts)} testid="result-pnl" />
        <MetricCard label="Max DD (pts)" value={fmtPnL(m.max_dd_pts)} accent="text-danger" testid="result-dd" />
        <MetricCard label="Sharpe" value={fmtNum(m.sharpe, 2)} testid="result-sharpe" />
      </div>

      {/* Chart */}
      <MultiPaneChart candles={candles} equity={equity} drawdown={drawdown} height={520} />

      {/* Two-column: walkforward + funnel + regime */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
        <WalkForwardCard wf={result.walkforward} />
        <SignalFunnelCard funnel={funnel} regimeDist={regimeDist} totalRegime={totalRegime} />
      </div>

      {/* Trades table */}
      <TradesTable trades={result.trades || []} />
    </div>
  );
}

function WalkForwardCard({ wf }) {
  if (!wf || !wf.folds || wf.folds.length === 0) {
    return (
      <Panel title="Walk-Forward (IS vs OOS)" testid="walkforward-panel">
        <div className="text-xs text-dimmer">Walk-forward disabled or insufficient candles (≥200 needed).</div>
      </Panel>
    );
  }
  const iv = wf.is_vs_oos;
  const diverging = iv.divergence_warning;
  return (
    <Panel title="Walk-Forward (IS vs OOS)" testid="walkforward-panel">
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

function TradesTable({ trades }) {
  if (!trades.length) {
    return (
      <Panel title="Trades" testid="trades-panel">
        <div className="text-xs text-dimmer">No trades were taken in this run.</div>
      </Panel>
    );
  }
  return (
    <Panel title={`Trades (${trades.length})`} testid="trades-panel">
      <div className="overflow-x-auto max-h-[400px]">
        <table className="w-full text-xs" data-testid="trades-table">
          <thead className="sticky top-0 bg-bg-2 z-10">
            <tr className="text-dim">
              <th className="text-left p-2">#</th>
              <th className="text-left p-2">Dir</th>
              <th className="text-left p-2">Entry</th>
              <th className="text-right p-2">Entry Px</th>
              <th className="text-left p-2">Exit</th>
              <th className="text-right p-2">Exit Px</th>
              <th className="text-left p-2">Reason</th>
              <th className="text-right p-2">Score</th>
              <th className="text-right p-2">P&L (pts)</th>
              <th className="text-right p-2">P&L %</th>
            </tr>
          </thead>
          <tbody>
            {trades.map((t, idx) => (
              <tr key={idx} className="border-b border-line hover:bg-bg-2" data-testid="trade-row">
                <td className="p-2 font-mono">{idx + 1}</td>
                <td className={`p-2 font-mono font-medium ${t.direction === "CE" ? "text-emerald-300" : "text-rose-300"}`}>{t.direction}</td>
                <td className="p-2 text-dim">{tsToTime(t.entry_ts)}</td>
                <td className="p-2 text-right font-mono">{fmtNum(t.entry_price)}</td>
                <td className="p-2 text-dim">{tsToTime(t.exit_ts)}</td>
                <td className="p-2 text-right font-mono">{fmtNum(t.exit_price)}</td>
                <td className="p-2 text-[10px] text-dim">{t.exit_reason}</td>
                <td className="p-2 text-right font-mono text-dim">{t.score}</td>
                <td className={`p-2 text-right font-mono ${colorPnL(t.pnl_pts)}`}>{fmtPnL(t.pnl_pts)}</td>
                <td className={`p-2 text-right font-mono ${colorPnL(t.pnl_pts)}`}>{fmtPct(t.pnl_pct, 2)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Panel>
  );
}

function buildCandlesFromTrades() {
  return [];
}
// (legacy placeholder — candle fetching now done in ResultsView via api.candles)
