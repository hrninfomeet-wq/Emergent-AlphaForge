import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import {
  Activity, Archive, ChevronLeft, ChevronRight, Pause, Play, Plus,
  RefreshCw, Rocket, ShieldAlert, X, Zap,
} from "lucide-react";
import { toast } from "sonner";
import { api } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

/**
 * Deployments command center (route /live, rebuilt 2026-06-12).
 *
 * One card per deployed strategy: what is deployed, what it did today, and
 * lifetime paper results — with pause / resume / undeploy controls. New
 * deployments are created through a 3-step wizard (preset → execution → risk).
 * The old Pending Approval panel and manual research-signal console were
 * retired: deployments journal and auto-trade their own signals.
 */

const MONEYNESS = ["atm", "otm1", "itm1"];
const DTE_VALUES = [0, 1, 2, 3, 4, 5, 6];

const inr = (v) =>
  v == null ? "—" : `₹${Number(v).toLocaleString("en-IN", { maximumFractionDigits: 0 })}`;

const toneClass = (v) =>
  Number(v || 0) > 0 ? "text-emerald-400" : Number(v || 0) < 0 ? "text-red-400" : "text-dim";

export default function LiveSignals() {
  const navigate = useNavigate();
  const [overview, setOverview] = useState(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [wizardOpen, setWizardOpen] = useState(false);
  const [presets, setPresets] = useState([]);

  const refresh = useCallback(async () => {
    try {
      const [ov, presetList] = await Promise.all([
        api.deploymentsOverview(),
        api.listPresets().catch(() => ({ items: [] })),
      ]);
      setOverview(ov);
      setPresets(presetList.items || []);
    } catch (e) {
      toast.error(`Load failed: ${e.response?.data?.detail || e.message}`);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { refresh(); }, [refresh]);
  // Live cadence: the evaluator fires each minute during market hours.
  useEffect(() => {
    const id = window.setInterval(refresh, 30000);
    return () => window.clearInterval(id);
  }, [refresh]);

  // Deep-link /live?preset=NAME (Optimizer's Deploy rocket): open the wizard
  // with that preset preselected. Applied once per page load.
  const [searchParams] = useSearchParams();
  const deepLinkRef = useRef(false);
  const [wizardPreset, setWizardPreset] = useState("");
  useEffect(() => {
    const name = searchParams.get("preset");
    if (!name || deepLinkRef.current || presets.length === 0) return;
    if (!presets.some((p) => p.name === name)) return;
    deepLinkRef.current = true;
    setWizardPreset(name);
    setWizardOpen(true);
  }, [searchParams, presets]);

  const act = async (fn, okMsg) => {
    setBusy(true);
    try {
      await fn();
      if (okMsg) toast.success(okMsg);
      await refresh();
    } catch (e) {
      toast.error(e.response?.data?.detail?.message || e.response?.data?.detail || e.message);
    } finally {
      setBusy(false);
    }
  };

  const undeploy = (item) => {
    const name = item.deployment.name;
    if (!window.confirm(`Undeploy "${name}"?\n\nThis stops signal generation and paper trading for this strategy. Its journaled signals and trades are kept.`)) return;
    const purge = item.lifetime.closed_trades > 0 || item.today.clean_signals + item.today.blocked_signals > 0
      ? window.confirm("Also DELETE its journaled signals and CLOSED trades?\n\nOK = delete journals too (open trades are kept until the marker/square-off closes them)\nCancel = keep all journals for analysis")
      : false;
    act(() => api.archiveDeployment(item.deployment.id, purge ? { purge: 1 } : {}),
      purge ? `Undeployed "${name}" and purged its journals` : `Undeployed "${name}"`);
  };

  const items = overview?.items || [];
  const totals = overview?.totals || {};
  const todayMtm = Number(totals.realized_today || 0) + Number(totals.open_unrealized || 0);

  if (loading) {
    return <div className="h-96 rounded-lg border border-line bg-bg-1 animate-pulse" data-testid="deployments-page" />;
  }

  return (
    <div className="space-y-3" data-testid="deployments-page">
      {/* Header: combined live picture across all deployed strategies */}
      <div className="rounded-lg border border-line bg-bg-1 px-3 py-2 flex items-center gap-4 flex-wrap">
        <div className="flex items-center gap-2">
          <Activity className="w-4 h-4 text-info" />
          <div className="text-xs font-semibold uppercase tracking-wider text-dim">Deployed Strategies</div>
          <span className="text-[11px] text-dimmer">{items.length} deployed</span>
        </div>
        <HeaderStat label="Today MTM" value={inr(todayMtm)} tone={todayMtm} />
        <HeaderStat label="Realized today" value={inr(totals.realized_today)} tone={totals.realized_today} />
        <HeaderStat label="Open trades" value={totals.open_trades ?? 0} />
        <HeaderStat label="Signals today" value={totals.signals_today ?? 0} />
        <div className="ml-auto flex items-center gap-1.5">
          <Button size="sm" variant="ghost" className="h-7 text-xs" disabled={busy}
            onClick={() => act(() => api.evaluateActiveDeployments(), "Evaluation triggered")}
            title="Run the 1m-close evaluator once for every ACTIVE deployment"
            data-testid="evaluate-all-button">
            <Zap className="w-3 h-3 mr-1" /> Evaluate now
          </Button>
          <Button size="sm" variant="ghost" className="h-7 text-xs" onClick={refresh} data-testid="deployments-refresh">
            <RefreshCw className="w-3 h-3 mr-1" /> Refresh
          </Button>
          <Button size="sm" className="h-7 text-xs bg-info text-bg-0 hover:bg-info/90 font-semibold"
            onClick={() => { setWizardPreset(""); setWizardOpen(true); }}
            data-testid="open-deploy-wizard">
            <Plus className="w-3 h-3 mr-1" /> Deploy strategy
          </Button>
        </div>
      </div>

      {/* Deployment cards */}
      {items.length === 0 ? (
        <div className="rounded-lg border border-line bg-bg-1 p-8 text-center text-dimmer text-sm">
          Nothing deployed. Click <b>Deploy strategy</b> to run a saved preset live —
          signal generation and (in paper mode) automatic paper trading start with the next market minute.
        </div>
      ) : (
        <div className="grid grid-cols-1 xl:grid-cols-2 gap-3">
          {items.map((item) => (
            <DeploymentCard key={item.deployment.id} item={item} busy={busy}
              onPause={() => act(() => api.pauseDeployment(item.deployment.id), "Paused")}
              onResume={() => act(() => api.resumeDeployment(item.deployment.id), "Resumed")}
              onEvaluate={() => act(() => api.evaluateDeployment(item.deployment.id), "Evaluated")}
              onUndeploy={() => undeploy(item)}
              onSignals={() => navigate(`/journal?deployment=${encodeURIComponent(item.deployment.id)}`)}
              onTrades={() => navigate(`/paper?deployment=${encodeURIComponent(item.deployment.id)}`)}
            />
          ))}
        </div>
      )}

      {wizardOpen && (
        <DeployWizard
          presets={presets}
          initialPreset={wizardPreset}
          onClose={() => setWizardOpen(false)}
          onCreated={() => { setWizardOpen(false); refresh(); }}
        />
      )}
    </div>
  );
}

function HeaderStat({ label, value, tone }) {
  return (
    <div className="flex items-baseline gap-1.5">
      <span className="text-[10px] uppercase tracking-wider text-dimmer">{label}</span>
      <span className={`text-sm font-mono ${tone !== undefined ? toneClass(tone) : "text-foreground"}`}>{value}</span>
    </div>
  );
}

function DeploymentCard({ item, busy, onPause, onResume, onEvaluate, onUndeploy, onSignals, onTrades }) {
  const d = item.deployment;
  const t = item.today;
  const lt = item.lifetime;
  const paused = d.status === "PAUSED";
  const isPaper = d.mode === "paper";
  const pausedReason = d.kill_switch_reason || d.drift_reason;
  const mtm = Number(t.realized_pnl || 0) + Number(t.open_unrealized || 0);
  return (
    <div className="rounded-lg border border-line bg-bg-1 p-3 space-y-2" data-testid="deployment-card">
      <div className="flex items-start gap-2">
        <div className="min-w-0">
          <div className="text-sm font-semibold truncate" title={d.name}>{d.name}</div>
          <div className="text-[11px] font-mono text-dimmer truncate">
            {d.strategy_id} · {d.instrument} · {(d.option_policy?.moneyness || []).join("/").toUpperCase() || "ATM"}
            {" · DTE "}{(d.option_policy?.dte_filter || []).join(",") || "all"}
            {" · from "}{d.source_type === "preset" ? `preset "${d.source_id}"` : "backtest run"}
          </div>
        </div>
        <div className="ml-auto flex items-center gap-1.5 shrink-0">
          <span className={`text-[10px] px-1.5 py-0.5 rounded border font-mono ${isPaper ? "border-emerald-500/40 text-emerald-300" : "border-info/40 text-info"}`}>
            {isPaper ? "PAPER AUTO-TRADE" : "SIGNAL ONLY"}
          </span>
          <span className={`text-[10px] px-1.5 py-0.5 rounded border font-mono ${paused ? "border-amber-500/40 text-amber-300" : "border-emerald-500/40 text-emerald-300"}`}>
            {d.status}
          </span>
        </div>
      </div>

      {paused && pausedReason && (
        <div className="flex items-center gap-1.5 text-[11px] text-amber-300" data-testid="deployment-pause-reason">
          <ShieldAlert className="w-3.5 h-3.5 shrink-0" />
          <span className="truncate" title={pausedReason}>Auto-paused: {pausedReason}</span>
        </div>
      )}

      <div className="grid grid-cols-3 lg:grid-cols-6 gap-2 text-xs">
        <CardStat label="Signals today" value={`${t.clean_signals}${t.blocked_signals ? ` (+${t.blocked_signals} blocked)` : ""}`} />
        <CardStat label="Open trades" value={t.open_trades} />
        <CardStat label="Open MTM" value={inr(t.open_unrealized)} tone={t.open_unrealized} />
        <CardStat label="Today ₹" value={inr(mtm)} tone={mtm} />
        <CardStat label="Lifetime ₹" value={inr(lt.realized_pnl)} tone={lt.realized_pnl} />
        <CardStat label="Win rate" value={lt.win_rate != null ? `${lt.win_rate}% (${lt.closed_trades})` : `— (${lt.closed_trades})`} />
      </div>

      <div className="flex items-center gap-1.5 pt-1 border-t border-line flex-wrap">
        {paused ? (
          <Button size="sm" variant="ghost" className="h-7 text-xs" disabled={busy} onClick={onResume} data-testid="resume-deployment">
            <Play className="w-3 h-3 mr-1" /> Resume
          </Button>
        ) : (
          <Button size="sm" variant="ghost" className="h-7 text-xs" disabled={busy} onClick={onPause} data-testid="pause-deployment">
            <Pause className="w-3 h-3 mr-1" /> Pause
          </Button>
        )}
        <Button size="sm" variant="ghost" className="h-7 text-xs" disabled={busy || paused} onClick={onEvaluate} title="Evaluate the latest closed 1m bar now">
          <Zap className="w-3 h-3 mr-1" /> Evaluate
        </Button>
        <Button size="sm" variant="ghost" className="h-7 text-xs text-dim" onClick={onSignals}>Signals →</Button>
        <Button size="sm" variant="ghost" className="h-7 text-xs text-dim" onClick={onTrades}>Trades →</Button>
        <Button size="sm" variant="ghost" className="h-7 text-xs ml-auto text-rose-300 hover:text-rose-200" disabled={busy}
          onClick={onUndeploy} title="Stop signal generation and paper trading for this strategy" data-testid="undeploy-button">
          <Archive className="w-3 h-3 mr-1" /> Undeploy
        </Button>
      </div>
    </div>
  );
}

function CardStat({ label, value, tone }) {
  return (
    <div className="rounded-md border border-line bg-bg-2 p-1.5">
      <div className="text-[9px] uppercase tracking-wider text-dimmer">{label}</div>
      <div className={`font-mono mt-0.5 ${tone !== undefined ? toneClass(tone) : ""}`}>{value}</div>
    </div>
  );
}

/* ------------------------------------------------------------------------ */
/* Deploy wizard: preset → execution → risk & go                            */
/* ------------------------------------------------------------------------ */

const WIZARD_DEFAULTS = {
  name: "",
  source_id: "",
  mode: "paper",
  option_moneyness: "atm",
  dte_filter: [],
  default_lots: 1,
  pretrade_profile: "Balanced",
  auto_paper: true,
  auto_paper_unit: "pct",
  auto_paper_target_pts: "",
  auto_paper_stop_pts: "",
  auto_paper_target_pct: "",
  auto_paper_stop_pct: "",
  allow_overnight: false,
  max_consecutive_losses: "",
  daily_loss_cutoff_pct: "",
  max_open_paper_trades: "",
  acknowledged_warnings: false,
};

function DeployWizard({ presets, initialPreset, onClose, onCreated }) {
  const [step, setStep] = useState(1);
  const [form, setForm] = useState({ ...WIZARD_DEFAULTS, source_id: initialPreset || "" });
  const [busy, setBusy] = useState(false);
  const [readiness, setReadiness] = useState(null);
  const [readinessBusy, setReadinessBusy] = useState(false);
  const [quality, setQuality] = useState(null);
  const set = (k, v) => setForm((prev) => ({ ...prev, [k]: v }));

  const preset = presets.find((p) => p.name === form.source_id);
  const instrument = (preset?.config?.instrument || "").toUpperCase();

  // Evidence + quality for the chosen preset.
  useEffect(() => {
    let cancelled = false;
    if (!form.source_id) { setReadiness(null); setQuality(null); return () => {}; }
    setReadinessBusy(true);
    setForm((prev) => ({ ...prev, acknowledged_warnings: false }));
    Promise.all([
      api.deploymentReadiness("preset", form.source_id).catch(() => null),
      api.deploymentQuality("preset", form.source_id).catch(() => null),
    ]).then(([r, q]) => {
      if (cancelled) return;
      setReadiness(r);
      setQuality(q);
    }).finally(() => { if (!cancelled) setReadinessBusy(false); });
    return () => { cancelled = true; };
  }, [form.source_id]);

  // Execution policy travels with the preset: prefill once per preset choice.
  const prefillRef = useRef(null);
  useEffect(() => {
    if (!form.source_id || prefillRef.current === form.source_id) return;
    const ex = preset?.config?.execution;
    prefillRef.current = form.source_id;
    setForm((prev) => ({
      ...prev,
      name: prev.name || `${form.source_id} deployment`,
      ...(ex ? {
        option_moneyness: ex.moneyness || prev.option_moneyness,
        dte_filter: Array.isArray(ex.dte_filter) ? ex.dte_filter : prev.dte_filter,
        default_lots: ex.lots || prev.default_lots,
        ...(ex.exit_mode === "option_levels" ? {
          auto_paper_unit: (ex.option_target_pts != null || ex.option_stop_pts != null) ? "pts" : "pct",
          auto_paper_target_pts: ex.option_target_pts ?? "",
          auto_paper_stop_pts: ex.option_stop_pts ?? "",
          auto_paper_target_pct: ex.option_target_pct ?? "",
          auto_paper_stop_pct: ex.option_stop_pct ?? "",
        } : {}),
      } : {}),
    }));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [form.source_id, preset]);

  const create = async () => {
    setBusy(true);
    try {
      const payload = {
        name: form.name || `${form.source_id} deployment`,
        source_type: "preset",
        source_id: form.source_id,
        mode: form.mode,
        confirmation_mode: "1m_close",
        option_moneyness: [form.option_moneyness],
        pretrade_profile: form.pretrade_profile,
        dte_filter: form.dte_filter.length ? form.dte_filter : DTE_VALUES,
        default_lots: Math.max(1, parseInt(form.default_lots, 10) || 1),
        auto_paper: form.mode === "paper" ? Boolean(form.auto_paper) : false,
        auto_paper_target_pts: form.auto_paper_unit === "pts" && form.auto_paper_target_pts !== "" ? Number(form.auto_paper_target_pts) : null,
        auto_paper_stop_pts: form.auto_paper_unit === "pts" && form.auto_paper_stop_pts !== "" ? Number(form.auto_paper_stop_pts) : null,
        auto_paper_target_pct: form.auto_paper_unit === "pct" && form.auto_paper_target_pct !== "" ? Number(form.auto_paper_target_pct) : null,
        auto_paper_stop_pct: form.auto_paper_unit === "pct" && form.auto_paper_stop_pct !== "" ? Number(form.auto_paper_stop_pct) : null,
        allow_overnight: Boolean(form.allow_overnight),
        max_consecutive_losses: form.max_consecutive_losses === "" ? null : Math.max(0, parseInt(form.max_consecutive_losses, 10) || 0),
        daily_loss_cutoff_pct: form.daily_loss_cutoff_pct === "" ? null : Number(form.daily_loss_cutoff_pct),
        max_open_paper_trades: form.max_open_paper_trades === "" ? null : Math.max(0, parseInt(form.max_open_paper_trades, 10) || 0),
        acknowledged_warnings: Boolean(form.acknowledged_warnings),
      };
      const res = await api.createDeployment(payload);
      const stream = res.option_stream || {};
      toast.success(stream.restarted
        ? `Deployed. Option stream realigned (radius ${stream.radius}).`
        : "Deployed. Signals start with the next market minute.");
      onCreated();
    } catch (e) {
      toast.error(`Deployment failed: ${e.response?.data?.detail?.message || e.response?.data?.detail || e.message}`);
    } finally {
      setBusy(false);
    }
  };

  const needAck = Boolean(quality?.acknowledgment_required);
  const canNext1 = Boolean(form.source_id);
  const canCreate = canNext1 && (!needAck || form.acknowledged_warnings);

  return (
    <div className="fixed inset-0 z-50 bg-black/60 flex items-start justify-center p-4 overflow-y-auto" data-testid="deploy-wizard">
      <div className="w-full max-w-2xl rounded-lg border border-line bg-bg-1 mt-8">
        <div className="px-4 py-3 border-b border-line flex items-center gap-2">
          <Rocket className="w-4 h-4 text-info" />
          <div className="text-sm font-semibold">Deploy strategy</div>
          <div className="ml-3 flex items-center gap-1 text-[10px] font-mono text-dimmer">
            {[1, 2, 3].map((n) => (
              <span key={n} className={`px-1.5 py-0.5 rounded ${step === n ? "bg-info text-bg-0" : "bg-bg-2"}`}>
                {n}. {n === 1 ? "Preset" : n === 2 ? "Execution" : "Risk & go"}
              </span>
            ))}
          </div>
          <button onClick={onClose} className="ml-auto text-dimmer hover:text-foreground" data-testid="wizard-close">
            <X className="w-4 h-4" />
          </button>
        </div>

        <div className="p-4 space-y-3 text-xs">
          {step === 1 && (
            <>
              <label className="block text-[11px] text-dim">
                Strategy preset (optimized + saved in the Optimizer)
                <select
                  value={form.source_id}
                  onChange={(e) => set("source_id", e.target.value)}
                  className="mt-1 h-8 w-full rounded-md border border-input bg-bg-2 px-2 text-xs"
                  data-testid="wizard-preset-select"
                >
                  <option value="">— choose a preset —</option>
                  {presets.map((p) => (
                    <option key={p.name} value={p.name}>
                      {p.name} ({p.config?.strategy_id} · {p.config?.instrument}{p.config?.execution ? " · exec policy" : ""})
                    </option>
                  ))}
                </select>
              </label>
              <label className="block text-[11px] text-dim">
                Deployment name
                <Input value={form.name} onChange={(e) => set("name", e.target.value)}
                  placeholder={form.source_id ? `${form.source_id} deployment` : "name shown on the card"}
                  className="mt-1 bg-bg-2 border-line h-8" />
              </label>
              {readinessBusy && <div className="text-dimmer text-[11px]">Checking validation evidence…</div>}
              {readiness && <ReadinessSummary readiness={readiness} />}
              {form.source_id && preset?.config?.execution == null && (
                <div className="text-[10px] text-dimmer">
                  This preset carries no execution policy (older preset or spot-only run) — review step 2 manually.
                </div>
              )}
            </>
          )}

          {step === 2 && (
            <>
              <div className="grid grid-cols-3 gap-2">
                <label className="block text-[11px] text-dim">
                  Mode
                  <select value={form.mode} onChange={(e) => set("mode", e.target.value)}
                    className="mt-1 h-8 w-full rounded-md border border-input bg-bg-2 px-2 text-xs" data-testid="wizard-mode-select">
                    <option value="paper">Paper — auto-trade every clean signal</option>
                    <option value="signal_only">Signal only — journal, no trades</option>
                  </select>
                </label>
                <label className="block text-[11px] text-dim">
                  Moneyness
                  <select value={form.option_moneyness} onChange={(e) => set("option_moneyness", e.target.value)}
                    className="mt-1 h-8 w-full rounded-md border border-input bg-bg-2 px-2 text-xs">
                    {MONEYNESS.map((m) => <option key={m} value={m}>{m.toUpperCase()}</option>)}
                  </select>
                </label>
                <label className="block text-[11px] text-dim">
                  Lots per trade
                  <Input type="number" min="1" step="1" value={form.default_lots}
                    onChange={(e) => set("default_lots", e.target.value)} className="mt-1 bg-bg-2 border-line h-8"
                    title="Lot size always comes from the option contract (Upstox)." />
                </label>
              </div>

              <div>
                <div className="text-[11px] text-dim mb-1">DTE filter (days to expiry — none selected = all)</div>
                <div className="flex flex-wrap items-center gap-1">
                  <button type="button" onClick={() => set("dte_filter", [])}
                    className={`px-2 py-1 rounded text-[11px] font-mono border ${form.dte_filter.length === 0 ? "bg-info text-bg-0 border-info" : "bg-bg-2 text-dim border-line hover:text-foreground"}`}>
                    ALL
                  </button>
                  {DTE_VALUES.map((d) => {
                    const sel = form.dte_filter.includes(d);
                    return (
                      <button key={d} type="button"
                        onClick={() => {
                          const cur = new Set(form.dte_filter);
                          if (cur.has(d)) cur.delete(d); else cur.add(d);
                          set("dte_filter", [...cur].sort((a, b) => a - b));
                        }}
                        className={`px-2 py-1 rounded text-[11px] font-mono border ${sel ? "bg-info text-bg-0 border-info" : "bg-bg-2 text-dim border-line hover:text-foreground"}`}>
                        {d}
                      </button>
                    );
                  })}
                </div>
              </div>

              {form.mode === "paper" && (
                <div className="rounded-md border border-emerald-500/30 bg-emerald-500/5 p-2 space-y-2">
                  <label className="text-[11px] text-dim flex items-center gap-2">
                    <input type="checkbox" checked={Boolean(form.auto_paper)}
                      onChange={(e) => set("auto_paper", e.target.checked)} className="h-4 w-4 rounded border-line" />
                    <span><b>Auto paper trade on every clean signal</b> — entry at live option premium</span>
                  </label>
                  {form.auto_paper && (
                    <>
                      <div className="flex items-center gap-2">
                        <span className="text-[11px] text-dim">Fallback exit unit</span>
                        <div className="flex rounded-md border border-line overflow-hidden">
                          {["pts", "pct"].map((u) => (
                            <button key={u} type="button" onClick={() => set("auto_paper_unit", u)}
                              className={`px-2 py-1 text-[11px] font-mono ${form.auto_paper_unit === u ? "bg-info text-bg-0" : "bg-bg-2 text-dim hover:text-foreground"}`}>
                              {u === "pts" ? "₹ points" : "Percent"}
                            </button>
                          ))}
                        </div>
                        <span className="text-[10px] text-dimmer">used only when the strategy gives no exit hints</span>
                      </div>
                      <div className="grid grid-cols-2 gap-2">
                        <label className="text-[11px] text-dim">
                          Target {form.auto_paper_unit === "pts" ? "(pts of premium)" : "(% of premium)"}
                          <Input type="number" min="0" step={form.auto_paper_unit === "pts" ? "0.5" : "5"}
                            value={form.auto_paper_unit === "pts" ? form.auto_paper_target_pts : form.auto_paper_target_pct}
                            onChange={(e) => set(form.auto_paper_unit === "pts" ? "auto_paper_target_pts" : "auto_paper_target_pct", e.target.value)}
                            className="mt-1 bg-bg-2 border-line h-8" placeholder="strategy hint" />
                        </label>
                        <label className="text-[11px] text-dim">
                          Stop {form.auto_paper_unit === "pts" ? "(pts of premium)" : "(% of premium)"}
                          <Input type="number" min="0" step={form.auto_paper_unit === "pts" ? "0.5" : "5"}
                            value={form.auto_paper_unit === "pts" ? form.auto_paper_stop_pts : form.auto_paper_stop_pct}
                            onChange={(e) => set(form.auto_paper_unit === "pts" ? "auto_paper_stop_pts" : "auto_paper_stop_pct", e.target.value)}
                            className="mt-1 bg-bg-2 border-line h-8" placeholder="strategy hint" />
                        </label>
                      </div>
                    </>
                  )}
                  <div className="text-[10px] text-dimmer leading-snug">
                    The strategy's own exits always win: spot-point levels are mirrored automatically (option closes
                    when the index hits the level), premium-% hints apply directly. No live premium → no trade, reason journaled.
                  </div>
                </div>
              )}
            </>
          )}

          {step === 3 && (
            <>
              <div className="grid grid-cols-3 gap-2">
                <label className="text-[11px] text-dim">
                  Max consecutive losses
                  <Input type="number" min="0" value={form.max_consecutive_losses}
                    onChange={(e) => set("max_consecutive_losses", e.target.value)}
                    className="mt-1 bg-bg-2 border-line h-8" placeholder="off"
                    title="Auto-PAUSE the deployment after this many losing paper trades in a row" />
                </label>
                <label className="text-[11px] text-dim">
                  Daily loss cutoff (%)
                  <Input type="number" value={form.daily_loss_cutoff_pct}
                    onChange={(e) => set("daily_loss_cutoff_pct", e.target.value)}
                    className="mt-1 bg-bg-2 border-line h-8" placeholder="off"
                    title="Auto-PAUSE when today's realized paper P&L falls to/below this negative % of capital deployed today" />
                </label>
                <label className="text-[11px] text-dim">
                  Max open trades
                  <Input type="number" min="0" value={form.max_open_paper_trades}
                    onChange={(e) => set("max_open_paper_trades", e.target.value)}
                    className="mt-1 bg-bg-2 border-line h-8" placeholder="off"
                    title="Soft-block new signals while this many paper trades are open (self-clears)" />
                </label>
              </div>
              <label className="text-[11px] text-dim flex items-center gap-2">
                <input type="checkbox" checked={Boolean(form.allow_overnight)}
                  onChange={(e) => set("allow_overnight", e.target.checked)} className="h-4 w-4 rounded border-line" />
                <span>Allow overnight (skip the 15:00 IST auto-square-off for this deployment)</span>
              </label>

              {needAck && (
                <div className="rounded-md border border-amber-500/40 bg-amber-500/5 p-2 space-y-1">
                  <div className="text-[11px] text-amber-300 font-semibold">
                    Quality warnings on this preset — acknowledge to deploy:
                  </div>
                  <ul className="text-[11px] text-amber-200/90 list-disc pl-4">
                    {(quality?.warnings || []).map((w) => <li key={w.id}>{w.label}{w.detail ? ` — ${w.detail}` : ""}</li>)}
                  </ul>
                  <label className="text-[11px] text-dim flex items-center gap-2 pt-1">
                    <input type="checkbox" checked={Boolean(form.acknowledged_warnings)}
                      onChange={(e) => set("acknowledged_warnings", e.target.checked)}
                      className="h-4 w-4 rounded border-line" data-testid="wizard-ack-checkbox" />
                    <span>I understand these warnings and want to deploy anyway</span>
                  </label>
                </div>
              )}

              <div className="text-[10px] text-dimmer leading-snug">
                Summary: <b className="text-dim">{form.source_id || "?"}</b> on <b className="text-dim">{instrument || "?"}</b> ·
                {form.mode === "paper" ? " paper auto-trade" : " signal only"} · {String(form.option_moneyness).toUpperCase()} ·
                DTE {form.dte_filter.length ? form.dte_filter.join(",") : "all"} · {form.default_lots} lot(s).
                Evaluation runs every market minute (09:15–15:30 IST, signal window 09:25–14:50); square-off 15:00 IST.
              </div>
            </>
          )}
        </div>

        <div className="px-4 py-3 border-t border-line flex items-center gap-2">
          {step > 1 && (
            <Button size="sm" variant="ghost" className="h-8 text-xs" onClick={() => setStep(step - 1)}>
              <ChevronLeft className="w-3 h-3 mr-1" /> Back
            </Button>
          )}
          <div className="ml-auto flex items-center gap-2">
            <Button size="sm" variant="ghost" className="h-8 text-xs" onClick={onClose}>Cancel</Button>
            {step < 3 ? (
              <Button size="sm" className="h-8 text-xs bg-info text-bg-0 hover:bg-info/90" disabled={!canNext1}
                onClick={() => setStep(step + 1)} data-testid="wizard-next">
                Next <ChevronRight className="w-3 h-3 ml-1" />
              </Button>
            ) : (
              <Button size="sm" className="h-8 text-xs bg-emerald-500 text-bg-0 hover:bg-emerald-400 font-semibold"
                disabled={busy || !canCreate} onClick={create} data-testid="wizard-create">
                <Rocket className="w-3 h-3 mr-1" /> Deploy
              </Button>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

// Compact validation-evidence rows (honest-WFO + option-rupee) for step 1.
function ReadinessSummary({ readiness }) {
  const wfo = readiness.wfo;
  const oe = readiness.option_evidence;
  const wfoOk = wfo && wfo.efficiency != null && wfo.efficiency >= 0.7 && (wfo.consistency_pct ?? 0) >= 50;
  const oeOk = oe && oe.params_match && Number(oe.net_pnl_value || 0) > 0;
  const row = (ok, present, okText, weakText, missText) => (
    <div className={`flex items-start gap-2 text-[11px] ${!present ? "text-dimmer" : ok ? "text-emerald-400" : "text-amber-400"}`}>
      <span className={`mt-1.5 inline-block w-1.5 h-1.5 rounded-full shrink-0 ${!present ? "bg-zinc-600" : ok ? "bg-emerald-400" : "bg-amber-400"}`} />
      <span>{!present ? missText : ok ? okText : weakText}</span>
    </div>
  );
  return (
    <div className="rounded-md border border-line bg-bg-2 p-2 space-y-1" data-testid="readiness-badge">
      <div className="text-[10px] uppercase tracking-wider text-dimmer">Validation evidence (informational)</div>
      {row(wfoOk, Boolean(wfo),
        `Walk-forward: efficiency ${wfo?.efficiency}, ${wfo?.positive_windows}/${wfo?.windows} windows OOS-positive${wfo?.option_oos_net != null ? `, option OOS ${inr(wfo.option_oos_net)}` : ""}${wfo?.params_match ? "" : " (params differ)"}`,
        `Walk-forward found but weak: efficiency ${wfo?.efficiency}, ${wfo?.positive_windows}/${wfo?.windows} OOS-positive${wfo?.params_match ? "" : " (params differ)"}`,
        "No completed honest walk-forward for this strategy — run one in the Optimizer first.")}
      {row(oeOk, Boolean(oe),
        `Option rupee (${oe?.kind === "rerank" ? "re-rank" : "backtest"}): net ${inr(oe?.net_pnl_value)}, ${oe?.paired_trade_count} paired`,
        `Option rupee evidence ${oe?.params_match ? `is negative (${inr(oe?.net_pnl_value)})` : "exists but for different params"}`,
        "No option-rupee validation — run an Option re-rank or option backtest first.")}
    </div>
  );
}
