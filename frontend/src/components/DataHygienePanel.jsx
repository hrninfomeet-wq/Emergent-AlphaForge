import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { useJobs } from "@/lib/jobs";
import { fmtInt, fmtNum, isoToFull } from "@/lib/fmt";
import { ShieldCheck, RefreshCw, Play, CheckCircle2, AlertTriangle, AlertCircle, Download, ChevronDown, ChevronRight, History } from "lucide-react";

const STATUS_STYLES = {
  verified: "bg-emerald-950 text-emerald-200 border-emerald-900",
  warning: "bg-amber-950 text-amber-200 border-amber-900",
  degraded: "bg-rose-950 text-rose-200 border-rose-900",
};

const STATUS_ICON = {
  verified: CheckCircle2,
  warning: AlertTriangle,
  degraded: AlertCircle,
};

// Auto-update run status (ok | skipped | error) → text color.
const STATUS_TEXT = {
  ok: "text-emerald-300",
  skipped: "text-dim",
  error: "text-rose-300",
};

const KIND_LABEL = {
  spot: "Spot candles",
  contracts: "Option contracts",
  option_candles: "Option candles",
};

function StatusPill({ status }) {
  const Icon = STATUS_ICON[status] || AlertCircle;
  return (
    <span className={`text-[10px] px-1.5 py-0.5 rounded border font-mono inline-flex items-center gap-1 ${STATUS_STYLES[status] || STATUS_STYLES.degraded}`}>
      <Icon className="w-3 h-3" />
      {status}
    </span>
  );
}

/**
 * Data Hygiene panel — the one-click warehouse health + fill control.
 *
 * Plan: diff the desired warehouse (default scope 2024-11-27 -> today,
 * NIFTY/BANKNIFTY/SENSEX, ATM CE+PE) against what is actually stored.
 * Execute: submit the suggested fetches in dependency order (spot -> contracts
 * -> option_candles). Execute jobs are tracked by the global JobsProvider so
 * progress survives navigation.
 */
export default function DataHygienePanel({ upstoxConnected }) {
  const { startHygieneBatch, hygiene, isHygieneActive, onJobComplete } = useJobs();
  const [plan, setPlan] = useState(null);
  const [planning, setPlanning] = useState(false);
  const [executing, setExecuting] = useState(false);
  const [catchingUp, setCatchingUp] = useState(false);
  const [catchUpResult, setCatchUpResult] = useState(null);
  const [autoUpdate, setAutoUpdate] = useState(null);
  const [vix, setVix] = useState(null);
  const [vixBusy, setVixBusy] = useState(false);
  const [expandedBand, setExpandedBand] = useState(null); // instrument whose missing-sample list is open
  const [showHistory, setShowHistory] = useState(false);

  const loadVix = async () => {
    try {
      setVix(await api.vixCoverage());
    } catch {
      setVix(null);
    }
  };

  const loadAutoUpdate = async () => {
    try {
      const res = await api.autoUpdateStatus();
      setAutoUpdate(res);
    } catch {
      setAutoUpdate(null);
    }
  };

  // Last persisted plan -> the page shows warehouse health instantly on load
  // (with its checked-at time) instead of forcing a 5-15s "Check warehouse".
  const loadLatest = async () => {
    try {
      const res = await api.dataHygieneLatest();
      if (res?.plan) setPlan(res.plan);
    } catch {
      /* no cached plan yet — the Check button still works */
    }
  };

  useEffect(() => {
    loadAutoUpdate();
    loadVix();
    loadLatest();
  }, []);

  const ingestVix = async () => {
    if (!upstoxConnected) {
      toast.error("Connect Upstox before ingesting VIX.");
      return;
    }
    setVixBusy(true);
    try {
      // Baseline comes from the backend (VIX_BASELINE_START); dedup makes re-runs safe.
      const today = new Date(Date.now() + (5 * 60 + 30) * 60 * 1000).toISOString().slice(0, 10);
      const res = await api.vixIngest({ from_date: vix?.baseline_start || "2025-12-29", to_date: today, chunk_days: 7 });
      toast.success(`India VIX: ${res.status} · +${res.candles_added || 0} candles`);
      await loadVix();
    } catch (e) {
      toast.error(`VIX ingest failed: ${e.response?.data?.detail || e.message}`);
    } finally {
      setVixBusy(false);
    }
  };

  const toggleAutoUpdate = async () => {
    try {
      const res = await api.autoUpdateToggle(!(autoUpdate?.enabled));
      setAutoUpdate(res);
      toast.success(`Auto-update ${res.enabled ? "enabled" : "disabled"}`);
    } catch (e) {
      toast.error("Failed to toggle auto-update");
    }
  };

  const runPlan = async () => {
    setPlanning(true);
    try {
      const res = await api.dataHygienePlan();
      setPlan(res);
      const s = res.summary || {};
      toast.success(`Hygiene plan: ${s.total_actions || 0} action(s) across ${s.instruments_count || 0} instruments`);
    } catch (e) {
      const msg = e.response?.data?.detail || e.message;
      toast.error(`Hygiene plan failed: ${msg}`);
    } finally {
      setPlanning(false);
    }
  };

  const execute = async () => {
    if (!plan) return;
    const totalActions = plan.summary?.total_actions || 0;
    if (totalActions === 0) {
      toast.info("Nothing to fetch — the warehouse already matches the desired scope.");
      return;
    }
    const ok = window.confirm(
      `Submit ${totalActions} background fetch job(s) to fill warehouse gaps?\n\n` +
        `They run in dependency order (spot → contracts → option candles) and may take a while. ` +
        `Progress is shown here and in the top bar.`,
    );
    if (!ok) return;
    setExecuting(true);
    try {
      const res = await api.dataHygieneExecute(plan);
      const submitted = startHygieneBatch(res);
      const errors = (res.errors || []).length;
      if (submitted > 0) {
        toast.success(`Submitted ${submitted} hygiene job(s) in dependency order`);
      } else {
        toast.warning("No jobs were submitted. Re-run the plan and check Upstox connection.");
      }
      if (errors) toast.error(`${errors} action(s) failed to submit`);
    } catch (e) {
      const msg = e.response?.data?.detail || e.message;
      toast.error(`Hygiene execute failed: ${msg}`);
    } finally {
      setExecuting(false);
    }
  };

  // Re-run the plan automatically when a hygiene batch completes, so the diff
  // reflects the freshly fetched data.
  useEffect(() => {
    const off = onJobComplete("data_hygiene", () => {
      runPlan();
      loadAutoUpdate();
    });
    return off;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // One-button sync: catch-up new sessions (spot -> contracts -> band-exact
  // option fill over the full rolling window), plus a band sweep for
  // instruments whose spot is already current. Broker-proven-empty strike-days
  // are excluded by the ledger, so an unfixable gap never blocks "up to date".
  const runCatchUp = async () => {
    if (!upstoxConnected) {
      toast.error("Connect Upstox before syncing.");
      return;
    }
    setCatchingUp(true);
    setCatchUpResult(null);
    try {
      const res = await api.warehouseSync({ include_options: true });
      if (res.up_to_date) {
        toast.info("Warehouse already in sync — nothing to fetch.");
        setCatchUpResult({ up_to_date: true, plan: res.plan });
        return;
      }
      const submitted = startHygieneBatch(res);
      const errors = (res.errors || []).length;
      setCatchUpResult(res);
      if (submitted > 0) {
        const sweeps = (res.band_sweeps || []).length;
        toast.success(`Sync started: ${submitted} job(s)${sweeps ? ` incl. ${sweeps} band sweep(s)` : ""}`);
      } else {
        toast.warning("No sync jobs were submitted. Check Upstox connection.");
      }
      if (errors) toast.error(`${errors} action(s) failed to submit`);
    } catch (e) {
      const msg = e.response?.data?.detail || e.message;
      toast.error(`Sync failed: ${msg}`);
    } finally {
      setCatchingUp(false);
    }
  };

  const summary = plan?.summary;
  const overall = summary?.overall_status;
  const hygieneActive = isHygieneActive();

  return (
    <div className="rounded-lg border border-line bg-bg-1" data-testid="data-hygiene-panel">
      <div className="px-3 py-2 border-b border-line flex items-center gap-2">
        <ShieldCheck className="w-4 h-4 text-info" />
        <div className="text-xs font-semibold uppercase tracking-wider text-dim">Data Hygiene</div>
        {overall && (
          <span className="ml-2"><StatusPill status={overall} /></span>
        )}
        <div className="ml-auto flex items-center gap-2">
          <Button
            size="sm"
            variant="secondary"
            onClick={runPlan}
            disabled={planning}
            className="h-7 text-xs"
            data-testid="hygiene-plan-button"
          >
            <RefreshCw className={`w-3 h-3 mr-1 ${planning ? "animate-spin" : ""}`} />
            {planning ? "Checking…" : "Check warehouse"}
          </Button>
          <Button
            size="sm"
            onClick={execute}
            disabled={!plan || executing || hygieneActive || (summary?.total_actions || 0) === 0 || !upstoxConnected}
            className="h-7 text-xs bg-bg-3 border border-line hover:bg-bg-2"
            data-testid="hygiene-execute-button"
          >
            <Play className="w-3 h-3 mr-1" />
            {hygieneActive
              ? "Filling…"
              : !upstoxConnected
                ? "Connect Upstox"
                : (summary?.total_actions || 0) === 0
                  ? plan ? "Up to date" : "Fill gaps"
                  : `Fill gaps (${summary.total_actions})`}
          </Button>
        </div>
      </div>

      <div className="p-3 space-y-3">
        <div className="text-[11px] text-dim">
          Diffs the warehouse against the rolling 9-month scope
          {plan?.window ? <span className="font-mono"> ({plan.window.start} → {plan.window.end})</span> : null}
          {" "}· NIFTY + BANKNIFTY + SENSEX · daily ATM-band CE/PE — and fills gaps in dependency order:
          spot → contracts → option candles. Re-running is safe; only missing data is fetched, and strike-days
          the broker has proven empty are excluded automatically.
        </div>

        {/* Instant health strip from the last persisted plan. */}
        {plan && (
          <div className="rounded-md border border-line bg-bg-2 p-2.5 flex items-center gap-3 flex-wrap" data-testid="hygiene-hero">
            {(plan.instruments || []).map((i) => {
              const oc = i.option_candles || {};
              const cls = oc.status === "verified" ? "text-emerald-300" : oc.status === "warning" ? "text-amber-300" : "text-rose-300";
              return (
                <span key={i.instrument} className="text-[11px] font-mono inline-flex items-center gap-1.5">
                  <span className="text-dim">{i.instrument}</span>
                  <span className={cls} title={`band coverage · spot ${i.spot?.coverage_pct}%`}>
                    {fmtNum(oc.coverage_pct ?? 0, 1)}%
                  </span>
                  {(oc.broker_empty_pairs || 0) > 0 && (
                    <span className="text-dimmer" title="strike-days the broker has proven empty (excluded)">
                      −{fmtInt(oc.broker_empty_pairs)}
                    </span>
                  )}
                </span>
              );
            })}
            {plan.computed_at && (
              <span className="ml-auto text-[10px] text-dimmer font-mono" data-testid="hygiene-checked-at">
                checked {isoToFull(plan.computed_at)}
              </span>
            )}
          </div>
        )}

        {/* Auto-update status + toggle */}
        <div className="rounded-md border border-line bg-bg-2 p-2.5 flex items-center gap-2 flex-wrap" data-testid="auto-update-row">
          <span className={`w-2 h-2 rounded-full ${autoUpdate?.enabled ? "bg-emerald-500" : "bg-dimmer"}`} />
          <span className="text-[11px] text-dim">
            Auto-update {autoUpdate?.enabled ? "on" : "off"}
            <span className="text-dimmer"> · runs on connect, on startup, and daily 18:00 IST</span>
          </span>
          {autoUpdate?.last_finished_at && (
            <span className="text-[10px] text-dimmer font-mono">
              last: {autoUpdate.last_status || "—"}
              {autoUpdate.last_submitted_count ? ` (${autoUpdate.last_submitted_count} jobs)` : ""} · {isoToFull(autoUpdate.last_finished_at)}
            </span>
          )}
          <div className="ml-auto flex items-center gap-2">
            <Button
              size="sm"
              onClick={runCatchUp}
              disabled={catchingUp || hygieneActive || !upstoxConnected}
              className="h-6 text-[11px] bg-bg-3 border border-line hover:bg-bg-2"
              data-testid="catch-up-button"
              title="Catch up new sessions and band-fill wick-edge gaps over the full rolling window — one click brings the warehouse fully in sync"
            >
              <Download className={`w-3 h-3 mr-1 ${catchingUp ? "animate-pulse" : ""}`} />
              {catchingUp ? "Syncing…" : hygieneActive ? "Running…" : !upstoxConnected ? "Connect Upstox" : "Sync now"}
            </Button>
            <Button
              size="sm"
              variant="secondary"
              onClick={toggleAutoUpdate}
              className="h-6 text-[11px]"
              data-testid="auto-update-toggle"
            >
              {autoUpdate?.enabled ? "Disable" : "Enable"}
            </Button>
          </div>
        </div>

        {/* Auto-update run history (last ~10 runs) — collapsible. */}
        {(autoUpdate?.history || []).length > 0 && (
          <div className="rounded-md border border-line bg-bg-2 p-2.5" data-testid="auto-update-history">
            <button
              onClick={() => setShowHistory((v) => !v)}
              className="flex items-center gap-1.5 text-[11px] text-dim hover:text-foreground w-full"
              data-testid="auto-update-history-toggle"
            >
              {showHistory ? <ChevronDown className="w-3.5 h-3.5" /> : <ChevronRight className="w-3.5 h-3.5" />}
              <History className="w-3 h-3" />
              Auto-update history
              <span className="text-dimmer">({autoUpdate.history.length})</span>
            </button>
            {showHistory && (
              <div className="mt-2 space-y-1">
                {[...autoUpdate.history].reverse().map((h, i) => (
                  <div key={i} className="flex items-center gap-2 text-[10px] font-mono" data-testid="auto-update-history-row">
                    <span className={STATUS_TEXT[h.status] || "text-dimmer"}>{h.status || "—"}</span>
                    <span className="text-dim">{h.trigger || "—"}</span>
                    <span className="text-dimmer">{fmtInt(h.submitted_count || 0)} job(s)</span>
                    {h.error && <span className="text-rose-300 truncate max-w-[140px]" title={h.error}>{h.error}</span>}
                    <span className="ml-auto text-dimmer">{isoToFull(h.finished_at)}</span>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}

        {/* India VIX status + ingest — powers the volatility-context layer. */}
        <div className="rounded-md border border-line bg-bg-2 p-2.5 flex items-center gap-2 flex-wrap" data-testid="vix-row">
          <span className={`w-2 h-2 rounded-full ${vix?.count > 0 ? "bg-emerald-500" : "bg-dimmer"}`} />
          <span className="text-[11px] text-dim">
            India VIX {vix?.count > 0 ? `· ${fmtInt(vix.count)} candles` : "· not ingested"}
            <span className="text-dimmer"> · used for volatility-regime context</span>
          </span>
          <Button
            size="sm"
            variant="secondary"
            onClick={ingestVix}
            disabled={vixBusy || !upstoxConnected}
            className="ml-auto h-6 text-[11px]"
            data-testid="vix-ingest-button"
            title={`Fetch India VIX 1m candles from ${vix?.baseline_start || "the configured baseline"} to today`}
          >
            <Download className={`w-3 h-3 mr-1 ${vixBusy ? "animate-pulse" : ""}`} />
            {vixBusy ? "Fetching…" : !upstoxConnected ? "Connect Upstox" : vix?.count > 0 ? "Update VIX" : "Ingest VIX"}
          </Button>
        </div>

        {/* Catch-up summary: which instruments had a gap and the target window */}
        {catchUpResult && (
          <div className="rounded-md border border-line bg-bg-2 p-2.5 text-[11px]" data-testid="catch-up-summary">
            {catchUpResult.up_to_date ? (
              <span className="text-emerald-300 inline-flex items-center gap-1">
                <CheckCircle2 className="w-3 h-3" /> All instruments up to date (through{" "}
                {catchUpResult.plan?.summary?.target_end || "last session"}).
              </span>
            ) : (
              <div className="space-y-1">
                <div className="text-dim">
                  Catching up to <span className="font-mono">{catchUpResult.plan?.summary?.target_end || "—"}</span> · spot + options
                </div>
                <div className="flex flex-wrap gap-1.5">
                  {(catchUpResult.plan?.instruments || [])
                    .filter((i) => !i.up_to_date)
                    .map((i) => (
                      <span key={i.instrument} className="px-1.5 py-0.5 rounded bg-amber-950 text-amber-200 border border-amber-900 font-mono">
                        {i.instrument}: {i.missing_trading_days}d ({i.from_date} → {i.to_date})
                      </span>
                    ))}
                </div>
              </div>
            )}
          </div>
        )}

        {/* Active hygiene batch progress */}
        {hygiene && (
          <div className="rounded-md border border-line bg-bg-2 p-3" data-testid="hygiene-progress">
            <div className="flex items-center justify-between gap-2 text-xs">
              <span className="font-mono uppercase">{hygiene.done ? "complete" : "running"}</span>
              <span className="font-mono text-dimmer">
                {hygiene.completed}/{hygiene.total} jobs · {Math.round(hygiene.progress_pct || 0)}%
              </span>
            </div>
            <div className="mt-2 h-2 rounded bg-bg-3 overflow-hidden">
              <div
                className="h-full bg-info transition-all"
                style={{ width: `${Math.min(100, Math.max(0, Number(hygiene.progress_pct || 0)))}%` }}
              />
            </div>
            {hygiene.failed > 0 && (
              <div className="mt-2 text-[11px] text-danger">{hygiene.failed} job(s) failed — check Recent Ingest Runs.</div>
            )}
          </div>
        )}

        {/* Plan results per instrument */}
        {plan && (
          <div className="space-y-2" data-testid="hygiene-plan-result">
            <div className="text-[11px] text-dimmer font-mono">
              Window {plan.window?.start} → {plan.window?.end} · {summary?.total_actions || 0} action(s)
            </div>
            <div className="grid grid-cols-1 lg:grid-cols-3 gap-2">
              {(plan.instruments || []).map((inst) => (
                <div key={inst.instrument} className="rounded-md border border-line bg-bg-2 p-3" data-testid={`hygiene-instrument-${inst.instrument.toLowerCase()}`}>
                  <div className="text-sm font-semibold mb-2">{inst.instrument}</div>
                  <div className="space-y-1.5 text-[11px]">
                    <div className="flex items-center justify-between gap-2">
                      <span className="text-dim">Spot</span>
                      <span className="flex items-center gap-1.5">
                        <span className="font-mono text-dimmer">{inst.spot?.coverage_pct}%</span>
                        <StatusPill status={inst.spot?.status} />
                      </span>
                    </div>
                    <div className="flex items-center justify-between gap-2">
                      <span className="text-dim">Contracts</span>
                      <span className="flex items-center gap-1.5">
                        <span className="font-mono text-dimmer">{fmtInt(inst.contracts?.expiries_in_window || 0)} exp</span>
                        <StatusPill status={inst.contracts?.status} />
                      </span>
                    </div>
                    <div className="flex items-center justify-between gap-2">
                      <span className="text-dim">Option band</span>
                      <span className="flex items-center gap-1.5">
                        <span className="font-mono text-dimmer" title="Daily ATM-band coverage (both legs, every strike the day's spot range touched)">
                          {fmtNum(inst.option_candles?.coverage_pct ?? 100, 1)}%
                        </span>
                        <StatusPill status={inst.option_candles?.status} />
                      </span>
                    </div>
                  </div>

                  {/* Broker-empty footnote: pairs Upstox has proven it has no
                      data for — excluded from coverage/actions by the ledger,
                      so verified is reachable while staying honest. */}
                  {(inst.option_candles?.broker_empty_pairs || 0) > 0 && (
                    <div
                      className="mt-1 text-[10px] text-dimmer"
                      data-testid={`hygiene-broker-empty-${inst.instrument.toLowerCase()}`}
                      title="Strike-days a clean fetch proved the broker has no candles for. They are excluded from coverage and never re-requested."
                    >
                      {fmtInt(inst.option_candles.broker_empty_pairs)} strike-day(s) broker-empty (excluded)
                    </div>
                  )}

                  {/* Band coverage detail — the daily ATM-band truth (CHANGELOG 0.23.x). */}
                  {(inst.option_candles?.missing_pairs || 0) > 0 && (
                    <div className="mt-2 pt-2 border-t border-line space-y-1 text-[10px]" data-testid={`hygiene-band-${inst.instrument.toLowerCase()}`}>
                      <div className="flex items-center justify-between gap-2">
                        <span className="text-amber-300">{fmtInt(inst.option_candles.missing_pairs)} strike-day(s) missing</span>
                        <span className="text-dimmer font-mono">{fmtInt(inst.option_candles.judged_days || 0)} days judged</span>
                      </div>
                      <div className="text-dimmer font-mono leading-snug" title="missing strike-days by month">
                        {Object.entries(inst.option_candles.missing_by_month || {}).map(([m, n]) => `${m}: ${n}`).join(" · ") || "—"}
                      </div>
                      {(inst.option_candles.missing_sample || []).length > 0 && (
                        <>
                          <button
                            onClick={() => setExpandedBand(expandedBand === inst.instrument ? null : inst.instrument)}
                            className="text-info hover:underline inline-flex items-center gap-1"
                            data-testid={`hygiene-band-toggle-${inst.instrument.toLowerCase()}`}
                          >
                            {expandedBand === inst.instrument ? <ChevronDown className="w-3 h-3" /> : <ChevronRight className="w-3 h-3" />}
                            {expandedBand === inst.instrument ? "Hide" : "Show"} sample ({inst.option_candles.missing_sample.length})
                          </button>
                          {expandedBand === inst.instrument && (
                            <div className="max-h-40 overflow-y-auto space-y-0.5 font-mono text-dimmer pl-1" data-testid={`hygiene-band-sample-${inst.instrument.toLowerCase()}`}>
                              {inst.option_candles.missing_sample.map((mp, i) => (
                                <div key={i}>{mp.date} · {mp.expiry} · {fmtInt(mp.strike)} {mp.side}</div>
                              ))}
                            </div>
                          )}
                        </>
                      )}
                    </div>
                  )}
                  {(inst.actions || []).length > 0 ? (
                    <div className="mt-2 pt-2 border-t border-line flex flex-wrap gap-1">
                      {inst.actions.map((a) => (
                        <span key={a.id} className="text-[10px] px-1.5 py-0.5 rounded bg-amber-950 text-amber-200 border border-amber-900" title={a.reason}>
                          {KIND_LABEL[a.kind] || a.kind}
                        </span>
                      ))}
                    </div>
                  ) : (
                    <div className="mt-2 pt-2 border-t border-line text-[10px] text-emerald-300 flex items-center gap-1">
                      <CheckCircle2 className="w-3 h-3" /> Up to date
                    </div>
                  )}
                </div>
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
