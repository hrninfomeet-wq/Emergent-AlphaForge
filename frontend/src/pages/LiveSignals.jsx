import { useCallback, useEffect, useMemo, useState } from "react";
import { Activity, Briefcase, Check, RefreshCw, ShieldAlert, SkipForward, Sparkles, X, Zap } from "lucide-react";
import { toast } from "sonner";
import { api } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { fmtNum, isoToFull } from "@/lib/fmt";

const INSTRUMENTS = ["NIFTY", "BANKNIFTY", "SENSEX"];
const STATES = ["WATCHING", "FORMING", "CONFIRMED", "TRIGGERED", "ACTIVE", "EXITED", "AUDITED"];

export default function LiveSignals() {
  const [signals, setSignals] = useState([]);
  const [deployments, setDeployments] = useState([]);
  const [presets, setPresets] = useState([]);
  const [backtestRuns, setBacktestRuns] = useState([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [filterState, setFilterState] = useState("");
  const [deploymentForm, setDeploymentForm] = useState({
    name: "NIFTY shadow deployment",
    source_type: "preset",
    source_id: "",
    mode: "shadow",
    confirmation_mode: "1m_close",
    option_moneyness: "atm",
    pretrade_profile: "Balanced",
    dte_filter: "0,1,2,3,4,5,6",
    default_lots: 1,
    allow_overnight: false,
  });
  const [form, setForm] = useState({
    instrument: "NIFTY",
    direction: "LONG",
    entry_price: 24000,
    confidence: 70,
    strategy_id: "manual_research",
    reasons: "manual setup, offline validation",
    trading_symbol: "NIFTY PAPER CE",
    lot_size: 50,
    stop_price: "",
    target_price: "",
  });

  const refresh = useCallback(async () => {
    try {
      const res = await api.listSignals({ ...(filterState ? { state: filterState } : {}), limit: 50 });
      setSignals(res.items || []);
    } catch (e) {
      toast.error(`Signals load failed: ${e.response?.data?.detail || e.message}`);
    } finally {
      setLoading(false);
    }
  }, [filterState]);

  const refreshDeployments = useCallback(async () => {
    try {
      const [dep, presetList, runList] = await Promise.all([
        api.listDeployments({ limit: 50 }),
        api.listPresets(),
        api.listBacktestRuns(50),
      ]);
      setDeployments(dep.items || []);
      setPresets(presetList.items || []);
      setBacktestRuns(runList.items || []);
      setDeploymentForm((prev) => {
        if (prev.source_id) return prev;
        const firstSource = prev.source_type === "preset" ? presetList.items?.[0]?.name : runList.items?.[0]?.id;
        return firstSource ? { ...prev, source_id: firstSource } : prev;
      });
    } catch (e) {
      toast.error(`Deployments load failed: ${e.response?.data?.detail || e.message}`);
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  // Auto-refresh signals every 15s so deployment-generated signals appear without manual reload.
  useEffect(() => {
    const id = window.setInterval(refresh, 15000);
    return () => window.clearInterval(id);
  }, [refresh]);

  useEffect(() => {
    refreshDeployments();
  }, [refreshDeployments]);

  const set = (key, value) => setForm((prev) => ({ ...prev, [key]: value }));
  const setDeployment = (key, value) => {
    setDeploymentForm((prev) => {
      const next = { ...prev, [key]: value };
      if (key === "source_type") {
        const firstSource = value === "preset" ? presets[0]?.name : backtestRuns[0]?.id;
        next.source_id = firstSource || "";
      }
      return next;
    });
  };

  const createDeployment = async () => {
    if (!deploymentForm.source_id) {
      toast.error("Choose a saved preset or backtest run first");
      return;
    }
    setBusy(true);
    try {
      const payload = {
        ...deploymentForm,
        option_moneyness: String(deploymentForm.option_moneyness || "atm")
          .split(",")
          .map((item) => item.trim().toLowerCase())
          .filter(Boolean),
        dte_filter: String(deploymentForm.dte_filter || "0,1,2,3,4,5,6")
          .split(",")
          .map((item) => parseInt(item.trim(), 10))
          .filter((n) => Number.isFinite(n) && n >= 0),
        default_lots: Math.max(1, parseInt(deploymentForm.default_lots, 10) || 1),
        allow_overnight: Boolean(deploymentForm.allow_overnight),
      };
      await api.createDeployment(payload);
      toast.success("Strategy deployment created");
      await refreshDeployments();
    } catch (e) {
      toast.error(`Deployment failed: ${e.response?.data?.detail || e.message}`);
    } finally {
      setBusy(false);
    }
  };

  const setDeploymentStatus = async (deployment, action) => {
    setBusy(true);
    try {
      if (action === "pause") await api.pauseDeployment(deployment.id);
      if (action === "resume") await api.resumeDeployment(deployment.id);
      if (action === "archive") await api.archiveDeployment(deployment.id);
      await refreshDeployments();
    } catch (e) {
      toast.error(`Deployment update failed: ${e.response?.data?.detail || e.message}`);
    } finally {
      setBusy(false);
    }
  };

  const evaluateNow = async (deployment) => {
    setBusy(true);
    try {
      const res = await api.evaluateDeployment(deployment.id);
      if (res.outcome === "clean") {
        toast.success(`Clean ${res.direction} signal journaled — pending approval`);
      } else if (res.outcome === "blocked") {
        toast.warning(`Signal blocked: ${(res.blockers || [])[0] || "see audit"}`);
      } else if (res.outcome === "no_setup") {
        toast.message("No setup on the latest closed bar");
      } else {
        toast.message(`Evaluator: ${res.outcome} (${res.reason || "ok"})`);
      }
      await refresh();
      await refreshDeployments();
    } catch (e) {
      toast.error(`Evaluate failed: ${e.response?.data?.detail || e.message}`);
    } finally {
      setBusy(false);
    }
  };

  const approveSignal = async (signal, note = "") => {
    setBusy(true);
    try {
      const res = await api.approveSignal(signal.id, { note });
      if (res?.trade) {
        toast.success(`Approved + paper trade opened: ${res.trade.trading_symbol || res.trade.id}`);
      } else if (res?.signal?.paper_trade_error) {
        toast.warning(`Signal approved but paper trade failed: ${res.signal.paper_trade_error}`);
      } else {
        toast.success("Signal approved");
      }
      await refresh();
    } catch (e) {
      toast.error(`Approve failed: ${e.response?.data?.detail || e.message}`);
    } finally {
      setBusy(false);
    }
  };

  const skipSignal = async (signal, note = "") => {
    setBusy(true);
    try {
      await api.skipSignal(signal.id, { note });
      toast.success("Signal skipped");
      await refresh();
    } catch (e) {
      toast.error(`Skip failed: ${e.response?.data?.detail || e.message}`);
    } finally {
      setBusy(false);
    }
  };

  const markBlocked = async (signal, note = "") => {
    setBusy(true);
    try {
      await api.markBlockedSignal(signal.id, { note: note || "manual review" });
      toast.success("Marked as blocked");
      await refresh();
    } catch (e) {
      toast.error(`Mark blocked failed: ${e.response?.data?.detail || e.message}`);
    } finally {
      setBusy(false);
    }
  };

  const createResearchSignal = async () => {
    setBusy(true);
    try {
      const payload = {
        instrument: form.instrument,
        direction: form.direction,
        strategy_id: form.strategy_id,
        entry_price: Number(form.entry_price || 0),
        confidence: Number(form.confidence || 0),
        reasons: String(form.reasons || "").split(",").map((item) => item.trim()).filter(Boolean),
        option_contract: {
          trading_symbol: form.trading_symbol,
          lot_size: Number(form.lot_size || 1),
        },
        context: { source: "manual_offline_console" },
      };
      await api.createSignal(payload);
      toast.success("Research signal created");
      await refresh();
    } catch (e) {
      toast.error(`Create failed: ${e.response?.data?.detail || e.message}`);
    } finally {
      setBusy(false);
    }
  };

  const transition = async (signal, toState) => {
    setBusy(true);
    try {
      await api.transitionSignal(signal.id, { to_state: toState, reason: `manual ${toState.toLowerCase()}` });
      toast.success(`Signal moved to ${toState}`);
      await refresh();
    } catch (e) {
      toast.error(`Transition failed: ${e.response?.data?.detail || e.message}`);
    } finally {
      setBusy(false);
    }
  };

  const deployToPaper = async (signal) => {
    setBusy(true);
    try {
      await api.deploySignalToPaper(signal.id, {
        lots: 1,
        entry_price: signal.entry_price,
        stop_price: form.stop_price === "" ? null : Number(form.stop_price),
        target_price: form.target_price === "" ? null : Number(form.target_price),
      });
      toast.success("Deployed to paper");
      await refresh();
    } catch (e) {
      toast.error(`Paper deploy failed: ${e.response?.data?.detail || e.message}`);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="space-y-3" data-testid="live-signals-page">
      <StrategyDeploymentsPanel
        deployments={deployments}
        presets={presets}
        backtestRuns={backtestRuns}
        form={deploymentForm}
        setFormValue={setDeployment}
        onCreate={createDeployment}
        onStatus={setDeploymentStatus}
        onEvaluate={evaluateNow}
        busy={busy}
      />

      <PendingApprovalPanel
        signals={signals}
        busy={busy}
        onApprove={approveSignal}
        onSkip={skipSignal}
        onMarkBlocked={markBlocked}
        onRefresh={refresh}
      />
      <section className="rounded-lg border border-line bg-bg-1" data-testid="live-signal-console">
        <div className="px-3 py-2 border-b border-line flex items-center gap-2">
          <Activity className="w-4 h-4 text-info" />
          <div className="text-xs font-semibold uppercase tracking-wider text-dim">Live Signal Console</div>
          <div className="ml-auto text-[10px] font-mono text-dimmer">offline lifecycle foundation</div>
        </div>
        <div className="p-3 grid grid-cols-1 xl:grid-cols-[420px_1fr] gap-3">
          <div className="rounded-md border border-line bg-bg-2 p-3" data-testid="create-research-signal">
            <div className="grid grid-cols-2 gap-2">
              <label className="text-[11px] text-dim">
                Instrument
                <select value={form.instrument} onChange={(e) => set("instrument", e.target.value)} className="mt-1 h-9 w-full rounded-md border border-input bg-bg-1 px-3 text-sm">
                  {INSTRUMENTS.map((inst) => <option key={inst} value={inst}>{inst}</option>)}
                </select>
              </label>
              <label className="text-[11px] text-dim">
                Direction
                <select value={form.direction} onChange={(e) => set("direction", e.target.value)} className="mt-1 h-9 w-full rounded-md border border-input bg-bg-1 px-3 text-sm">
                  <option value="LONG">LONG</option>
                  <option value="SHORT">SHORT</option>
                </select>
              </label>
              <label className="text-[11px] text-dim">
                Entry
                <Input type="number" value={form.entry_price} onChange={(e) => set("entry_price", e.target.value)} className="mt-1 bg-bg-1 border-line" />
              </label>
              <label className="text-[11px] text-dim">
                Confidence
                <Input type="number" value={form.confidence} onChange={(e) => set("confidence", e.target.value)} className="mt-1 bg-bg-1 border-line" />
              </label>
              <label className="text-[11px] text-dim col-span-2">
                Strategy
                <Input value={form.strategy_id} onChange={(e) => set("strategy_id", e.target.value)} className="mt-1 bg-bg-1 border-line" />
              </label>
              <label className="text-[11px] text-dim">
                Paper symbol
                <Input value={form.trading_symbol} onChange={(e) => set("trading_symbol", e.target.value)} className="mt-1 bg-bg-1 border-line" />
              </label>
              <label className="text-[11px] text-dim">
                Lot size
                <Input type="number" value={form.lot_size} onChange={(e) => set("lot_size", e.target.value)} className="mt-1 bg-bg-1 border-line" />
              </label>
              <label className="text-[11px] text-dim">
                Paper stop
                <Input type="number" value={form.stop_price} onChange={(e) => set("stop_price", e.target.value)} className="mt-1 bg-bg-1 border-line" />
              </label>
              <label className="text-[11px] text-dim">
                Paper target
                <Input type="number" value={form.target_price} onChange={(e) => set("target_price", e.target.value)} className="mt-1 bg-bg-1 border-line" />
              </label>
              <label className="text-[11px] text-dim col-span-2">
                Reasons
                <Input value={form.reasons} onChange={(e) => set("reasons", e.target.value)} className="mt-1 bg-bg-1 border-line" />
              </label>
            </div>
            <Button onClick={createResearchSignal} disabled={busy} className="mt-3 h-9 text-xs bg-bg-3 border border-line hover:bg-bg-2">
              <Zap className="w-3 h-3 mr-1" />
              Create Research Signal
            </Button>
          </div>

          <div className="rounded-md border border-line bg-bg-2 p-3">
            <div className="flex items-center gap-2 mb-3">
              <select value={filterState} onChange={(e) => setFilterState(e.target.value)} className="h-8 rounded-md border border-input bg-bg-1 px-2 text-xs" data-testid="signal-state">
                <option value="">All states</option>
                {STATES.map((state) => <option key={state} value={state}>{state}</option>)}
              </select>
              <Button size="sm" variant="ghost" onClick={refresh} className="h-8 text-xs">
                <RefreshCw className="w-3 h-3 mr-1" />
                Refresh
              </Button>
            </div>
            {loading ? (
              <div className="text-sm text-dim">Loading signals...</div>
            ) : (
              <div className="space-y-2">
                {signals.map((signal) => (
                  <SignalCard
                    key={signal.id}
                    signal={signal}
                    busy={busy}
                    onTransition={transition}
                    onDeploy={deployToPaper}
                  />
                ))}
                {signals.length === 0 && (
                  <div className="rounded-md border border-line bg-bg-1 p-4 text-sm text-dim">
                    No signals yet. Create a research signal to exercise the lifecycle without live market data.
                  </div>
                )}
              </div>
            )}
          </div>
        </div>
      </section>
    </div>
  );
}

function StrategyDeploymentsPanel({ deployments, presets, backtestRuns, form, setFormValue, onCreate, onStatus, onEvaluate, busy }) {
  const sourceOptions = form.source_type === "preset"
    ? presets.map((preset) => ({ id: preset.name, label: preset.name })).filter((item) => item.id)
    : backtestRuns
      .map((run) => {
        const id = String(run.id || "");
        return { id, label: `${run.name || run.strategy_id || "Backtest"} · ${id.slice(0, 8)}` };
      })
      .filter((item) => item.id);

  return (
    <section className="rounded-lg border border-line bg-bg-1" data-testid="strategy-deployments-panel">
      <div className="px-3 py-2 border-b border-line flex items-center gap-2">
        <Zap className="w-4 h-4 text-info" />
        <div className="text-xs font-semibold uppercase tracking-wider text-dim">Strategy Deployments</div>
        <div className="ml-auto text-[10px] font-mono text-dimmer">1m close · manual approval</div>
      </div>
      <div className="p-3 grid grid-cols-1 xl:grid-cols-[420px_1fr] gap-3">
        <div className="rounded-md border border-line bg-bg-2 p-3">
          <div className="grid grid-cols-2 gap-2">
            <label className="text-[11px] text-dim col-span-2">
              Name
              <Input value={form.name} onChange={(e) => setFormValue("name", e.target.value)} className="mt-1 bg-bg-1 border-line" />
            </label>
            <label className="text-[11px] text-dim">
              Source
              <select
                value={form.source_type}
                onChange={(e) => setFormValue("source_type", e.target.value)}
                className="mt-1 h-9 w-full rounded-md border border-input bg-bg-1 px-3 text-sm"
                data-testid="deployment-source-type"
              >
                <option value="preset">Saved preset</option>
                <option value="backtest_run">Backtest result</option>
              </select>
            </label>
            <label className="text-[11px] text-dim">
              Mode
              <select
                value={form.mode}
                onChange={(e) => setFormValue("mode", e.target.value)}
                className="mt-1 h-9 w-full rounded-md border border-input bg-bg-1 px-3 text-sm"
                data-testid="deployment-mode"
              >
                <option value="shadow">Shadow</option>
                <option value="paper">Paper approval</option>
                <option value="recommendation">Recommendation</option>
              </select>
            </label>
            <label className="text-[11px] text-dim col-span-2">
              Source artifact
              <select
                value={form.source_id}
                onChange={(e) => setFormValue("source_id", e.target.value)}
                className="mt-1 h-9 w-full rounded-md border border-input bg-bg-1 px-3 text-sm"
              >
                <option value="">Choose source</option>
                {sourceOptions.map((item) => <option key={item.id} value={item.id}>{item.label}</option>)}
              </select>
            </label>
            <label className="text-[11px] text-dim">
              Confirmation
              <select value={form.confirmation_mode} onChange={(e) => setFormValue("confirmation_mode", e.target.value)} className="mt-1 h-9 w-full rounded-md border border-input bg-bg-1 px-3 text-sm">
                <option value="1m_close">1m close</option>
                <option value="tick">Tick/manual later</option>
              </select>
            </label>
            <label className="text-[11px] text-dim">
              Moneyness
              <select value={form.option_moneyness} onChange={(e) => setFormValue("option_moneyness", e.target.value)} className="mt-1 h-9 w-full rounded-md border border-input bg-bg-1 px-3 text-sm">
                <option value="atm">ATM</option>
                <option value="atm,otm1">ATM + OTM1</option>
                <option value="atm,itm1">ATM + ITM1</option>
              </select>
            </label>
            <label className="text-[11px] text-dim">
              DTE filter
              <Input
                value={form.dte_filter}
                onChange={(e) => setFormValue("dte_filter", e.target.value)}
                className="mt-1 bg-bg-1 border-line"
                placeholder="0,1,2,3,4,5,6"
                title="Days-to-expiry list (comma separated). Default 0-6."
              />
            </label>
            <label className="text-[11px] text-dim">
              Default lots
              <Input
                type="number"
                min="1"
                value={form.default_lots}
                onChange={(e) => setFormValue("default_lots", e.target.value)}
                className="mt-1 bg-bg-1 border-line"
                title="Number of lots when paper trade is auto-created on approval. Lot size comes from option_contracts (Upstox)."
              />
            </label>
            <label className="text-[11px] text-dim col-span-2 flex items-center gap-2 pt-1">
              <input
                type="checkbox"
                checked={Boolean(form.allow_overnight)}
                onChange={(e) => setFormValue("allow_overnight", e.target.checked)}
                className="h-4 w-4 rounded border-line"
                data-testid="allow-overnight-checkbox"
              />
              <span>Allow overnight (skip 15:00 IST auto-square-off for this deployment)</span>
            </label>
          </div>
          <Button
            onClick={onCreate}
            disabled={busy || !form.source_id}
            className="mt-3 h-9 text-xs bg-bg-3 border border-line hover:bg-bg-2"
            data-testid="create-deployment-button"
          >
            Create Deployment
          </Button>
        </div>

        <div className="rounded-md border border-line bg-bg-2 p-3">
          <div className="text-[10px] uppercase tracking-wider text-dimmer mb-2">Deployments</div>
          <div className="space-y-2">
            {deployments.map((deployment) => (
              <article key={deployment.id} className="rounded-md border border-line bg-bg-1 p-3" data-testid="deployment-card">
                <div className="flex items-start gap-3">
                  <div className="min-w-0">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="font-semibold">{deployment.name}</span>
                      <span className="text-[10px] px-1.5 py-0.5 rounded border border-line bg-bg-3 font-mono">{deployment.status}</span>
                      <span className="text-[10px] text-dimmer font-mono">{deployment.mode} · {deployment.confirmation_mode}</span>
                    </div>
                    <div className="mt-1 text-xs text-dim">
                      {deployment.instrument} · {deployment.strategy_id} · {deployment.source_type}:{deployment.source_id}
                    </div>
                    <div className="mt-1 text-[11px] text-dimmer">
                      {deployment.option_policy?.moneyness?.join(", ").toUpperCase()} · manual approval required
                    </div>
                  </div>
                  <div className="ml-auto flex flex-wrap justify-end gap-1.5">
                    {deployment.status === "ACTIVE" && (
                      <Button size="sm" variant="secondary" disabled={busy} onClick={() => onEvaluate(deployment)} className="h-7 text-xs border border-line" data-testid="evaluate-now-button" title="Run the 1m close evaluator against this deployment now">
                        <Sparkles className="w-3 h-3 mr-1" />
                        Evaluate now
                      </Button>
                    )}
                    {deployment.status === "ACTIVE" ? (
                      <Button size="sm" variant="secondary" disabled={busy} onClick={() => onStatus(deployment, "pause")} className="h-7 text-xs border border-line">Pause</Button>
                    ) : deployment.status === "PAUSED" ? (
                      <Button size="sm" variant="secondary" disabled={busy} onClick={() => onStatus(deployment, "resume")} className="h-7 text-xs border border-line">Resume</Button>
                    ) : null}
                    {deployment.status !== "ARCHIVED" && (
                      <Button size="sm" variant="secondary" disabled={busy} onClick={() => onStatus(deployment, "archive")} className="h-7 text-xs border border-line">Archive</Button>
                    )}
                  </div>
                </div>
              </article>
            ))}
            {deployments.length === 0 && (
              <div className="rounded-md border border-line bg-bg-1 p-4 text-sm text-dim">
                No deployments yet. Create one from a saved preset or backtest result.
              </div>
            )}
          </div>
        </div>
      </div>
    </section>
  );
}

function PendingApprovalPanel({ signals, busy, onApprove, onSkip, onMarkBlocked, onRefresh }) {
  const pending = useMemo(
    () => (signals || []).filter((s) => s.state === "CONFIRMED" && s.deployment_id),
    [signals],
  );
  const recentlyClosed = useMemo(
    () => (signals || [])
      .filter((s) => s.deployment_id && (s.state === "AUDITED" || s.state === "ACTIVE"))
      .slice(0, 5),
    [signals],
  );

  return (
    <section className="rounded-lg border border-line bg-bg-1" data-testid="pending-approval-panel">
      <div className="px-3 py-2 border-b border-line flex items-center gap-2">
        <ShieldAlert className="w-4 h-4 text-warning" />
        <div className="text-xs font-semibold uppercase tracking-wider text-dim">Pending Approval</div>
        <div className="ml-auto flex items-center gap-2">
          <span className="text-[10px] font-mono text-dimmer">{pending.length} awaiting · auto-refreshes 15s</span>
          <Button size="sm" variant="ghost" onClick={onRefresh} className="h-7 text-xs">
            <RefreshCw className="w-3 h-3 mr-1" />
            Refresh
          </Button>
        </div>
      </div>
      <div className="p-3 space-y-2">
        {pending.length === 0 ? (
          <div className="rounded-md border border-line bg-bg-2 p-4 text-sm text-dim">
            No deployment-generated signals awaiting approval. Active deployments will journal clean signals here once a 1-minute close fires a setup.
          </div>
        ) : (
          pending.map((signal) => (
            <PendingSignalCard
              key={signal.id}
              signal={signal}
              busy={busy}
              onApprove={onApprove}
              onSkip={onSkip}
              onMarkBlocked={onMarkBlocked}
            />
          ))
        )}
        {recentlyClosed.length > 0 && (
          <details className="mt-2 rounded-md border border-line bg-bg-2 p-2">
            <summary className="cursor-pointer text-[11px] uppercase tracking-wider text-dimmer">Recently decided ({recentlyClosed.length})</summary>
            <div className="mt-2 space-y-1.5">
              {recentlyClosed.map((s) => (
                <div key={s.id} className="text-[11px] font-mono text-dim flex flex-wrap items-center gap-2">
                  <span className={s.blocked ? "text-red-400" : "text-emerald-400"}>{s.state}</span>
                  <span>{s.instrument}</span>
                  <span>{s.direction}</span>
                  <span>score {fmtNum(s.confidence)}</span>
                  <span className="text-dimmer">{isoToFull(s.updated_at)}</span>
                  {s.blocked && (
                    <span className="text-dimmer truncate max-w-[260px]">{(s.blockers || [])[0] || ""}</span>
                  )}
                </div>
              ))}
            </div>
          </details>
        )}
      </div>
    </section>
  );
}


function PendingSignalCard({ signal, busy, onApprove, onSkip, onMarkBlocked }) {
  const [note, setNote] = useState("");
  const ctx = signal.context || {};
  const candle = ctx.candle || {};
  const contract = signal.option_contract || {};
  const deploymentMode = String(ctx.deployment_mode || "shadow").toLowerCase();
  const willCreateTrade = deploymentMode === "paper";
  return (
    <article className="rounded-md border border-warning/40 bg-bg-2 p-3" data-testid="pending-signal-card">
      <div className="flex flex-wrap items-start gap-3">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className="font-semibold text-base">{signal.instrument}</span>
            <span className={`text-xs font-mono px-1.5 py-0.5 rounded border ${signal.direction === "CE" ? "border-emerald-500/40 text-emerald-400" : "border-red-500/40 text-red-400"}`}>{signal.direction}</span>
            <span className="text-[10px] px-1.5 py-0.5 rounded border border-line bg-bg-3 font-mono">{signal.state}</span>
            <span className={`text-[10px] px-1.5 py-0.5 rounded border font-mono ${willCreateTrade ? "border-emerald-500/40 text-emerald-400" : "border-line text-dimmer"}`}>
              {deploymentMode}
            </span>
            <span className="text-[10px] text-dimmer font-mono">score {fmtNum(signal.confidence)}</span>
            {candle.ist_time && <span className="text-[10px] text-dimmer font-mono">bar {candle.ist_time} IST</span>}
          </div>
          <div className="mt-1 text-xs text-dim">
            <span className="font-mono">{signal.strategy_id}</span>
            {ctx.strategy_version && <span className="text-dimmer font-mono"> v{ctx.strategy_version}</span>}
            {ctx.strategy_hash && <span className="text-dimmer font-mono"> · {ctx.strategy_hash.slice(0, 8)}</span>}
          </div>
          <div className="mt-1 text-xs text-dim">
            entry {fmtNum(signal.entry_price)} · regime {ctx.regime || "?"} · profile {ctx.pretrade_profile_name || "?"}
          </div>
          {contract.trading_symbol && (
            <div className="mt-1 text-[11px] text-dimmer font-mono">
              contract {contract.trading_symbol} · strike {fmtNum(contract.strike)} · {contract.side} · lot {contract.lot_size || "?"}
            </div>
          )}
          {signal.reasons?.length > 0 && (
            <div className="mt-1 text-[11px] text-dimmer">
              reasons: {signal.reasons.join(", ")}
            </div>
          )}
          {willCreateTrade && (
            <div className="mt-1 text-[11px] text-emerald-400/80">
              Approve will auto-create a paper trade.
            </div>
          )}
          <div className="mt-2 flex items-center gap-2">
            <Input
              placeholder="optional note for audit"
              value={note}
              onChange={(e) => setNote(e.target.value)}
              className="h-8 text-xs bg-bg-1 border-line"
              data-testid="approval-note"
            />
          </div>
        </div>
        <div className="flex flex-col gap-1.5 min-w-[140px]">
          <Button
            size="sm"
            disabled={busy}
            onClick={() => onApprove(signal, note)}
            className="h-8 text-xs bg-emerald-600/80 hover:bg-emerald-600 text-white border border-emerald-500/60"
            data-testid="approve-button"
          >
            <Check className="w-3 h-3 mr-1" />
            {willCreateTrade ? "Approve + Paper" : "Approve"}
          </Button>
          <Button
            size="sm"
            variant="secondary"
            disabled={busy}
            onClick={() => onSkip(signal, note)}
            className="h-8 text-xs border border-line"
            data-testid="skip-button"
          >
            <SkipForward className="w-3 h-3 mr-1" />
            Skip
          </Button>
          <Button
            size="sm"
            variant="secondary"
            disabled={busy}
            onClick={() => onMarkBlocked(signal, note)}
            className="h-8 text-xs border border-line text-red-400 hover:text-red-300"
            data-testid="mark-blocked-button"
          >
            <X className="w-3 h-3 mr-1" />
            Mark blocked
          </Button>
        </div>
      </div>
    </article>
  );
}


function SignalCard({ signal, busy, onTransition, onDeploy }) {
  const nextState = {
    WATCHING: "FORMING",
    FORMING: "CONFIRMED",
    CONFIRMED: "TRIGGERED",
    TRIGGERED: "ACTIVE",
    ACTIVE: "EXITED",
    EXITED: "AUDITED",
  }[signal.state];
  return (
    <article className="rounded-md border border-line bg-bg-1 p-3">
      <div className="flex items-start gap-3">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <span className="font-semibold">{signal.instrument}</span>
            <span className="text-xs font-mono text-dim">{signal.direction}</span>
            <span className="text-[10px] px-1.5 py-0.5 rounded border border-line bg-bg-3 font-mono">{signal.state}</span>
          </div>
          <div className="mt-1 text-xs text-dim">
            {signal.strategy_id} · entry {fmtNum(signal.entry_price)} · confidence {fmtNum(signal.confidence)}%
          </div>
          <div className="mt-1 text-[11px] text-dimmer">
            {signal.reasons?.join(", ") || "no reasons"} · {isoToFull(signal.updated_at || signal.created_at)}
          </div>
        </div>
        <div className="ml-auto flex flex-wrap justify-end gap-1.5">
          {nextState && (
            <Button size="sm" variant="secondary" disabled={busy} onClick={() => onTransition(signal, nextState)} className="h-7 text-xs border border-line">
              {nextState}
            </Button>
          )}
          <Button size="sm" disabled={busy || signal.state === "AUDITED"} onClick={() => onDeploy(signal)} className="h-7 text-xs bg-bg-3 border border-line hover:bg-bg-2" data-testid="deploy-paper-button">
            <Briefcase className="w-3 h-3 mr-1" />
            Paper
          </Button>
        </div>
      </div>
    </article>
  );
}
