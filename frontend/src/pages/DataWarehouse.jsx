import { useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { AlertCircle, CheckCircle2, Database, RefreshCw } from "lucide-react";
import { fmtInt, isoToFull } from "@/lib/fmt";
import { dateToMs } from "@/lib/time";
import { Skeleton } from "@/components/ui/skeleton";
import HolidayCalendarDialog from "@/components/HolidayCalendarDialog";
import DataHygienePanel from "@/components/DataHygienePanel";
import WarehouseLookup from "@/components/WarehouseLookup";
import WarehouseChart from "@/components/WarehouseChart";
import { useJobs } from "@/lib/jobs";
import { INSTRUMENTS, RUN_SOURCE_LABELS, SectionHeader, dateInput } from "@/components/warehouse/shared";
import { UpstoxPanel } from "@/components/warehouse/UpstoxPanel";
import { OptionWarehousePanel } from "@/components/warehouse/OptionPlannerPanel";
import { ExpiredContractBackfillPanel } from "@/components/warehouse/ExpiredBackfillPanel";
import { CoverageHeatmap, OptionCoverageHeatmap } from "@/components/warehouse/CoverageHeatmaps";
import { DataTrustPanel } from "@/components/warehouse/DataTrustPanel";
import { VolatilityAuditPanel } from "@/components/warehouse/VolatilityAuditPanel";
import { AdvancedTools, HowThisPageWorks } from "@/components/warehouse/Disclosures";


export default function DataWarehouse() {
  const { jobs, startJob, onJobComplete, isJobActive } = useJobs();
  const [coverage, setCoverage] = useState(null);
  const [runs, setRuns] = useState([]);
  const [upstoxStatus, setUpstoxStatus] = useState(null);
  const [upstoxBusy, setUpstoxBusy] = useState(false);
  const [quoteLoading, setQuoteLoading] = useState(false);
  const [marketQuote, setMarketQuote] = useState(null);
  const [upstoxForm, setUpstoxForm] = useState({
    instrument: "NIFTY",
    from_date: dateInput(30),
    to_date: dateInput(0),
    chunk_days: "",
  });
  const [optionPlanForm, setOptionPlanForm] = useState({
    underlying: "NIFTY",
    from_date: dateInput(30),
    to_date: dateInput(0),
    moneyness: ["atm"],
    legs: ["CE", "PE"],
    expiry_policy: "next_available",
    fixed_expiry_date: "",
    sample_interval_minutes: 15,
    chunk_days: "",
    fetch_missing_only: true,
    max_contracts: 50,
  });
  const [optionPlanResult, setOptionPlanResult] = useState(null);
  const [optionFetchResult, setOptionFetchResult] = useState(null);
  const [optionPlanning, setOptionPlanning] = useState(false);
  const [optionClearing, setOptionClearing] = useState(false);
  const [expiredBackfillForm, setExpiredBackfillForm] = useState({
    instrument: "NIFTY",
    from_date: dateInput(60),
    to_date: dateInput(0),
    max_expiries: 8,
    confirm_large_fetch: false,
  });
  const [expiredBackfillResult, setExpiredBackfillResult] = useState(null);
  const [expiredBackfilling, setExpiredBackfilling] = useState(false);
  const [auditForm, setAuditForm] = useState({
    instrument: "NIFTY",
    from_date: dateInput(30),
    to_date: dateInput(0),
  });
  const [auditResult, setAuditResult] = useState(null);
  const [auditLoading, setAuditLoading] = useState(false);
  const [clearInstrument, setClearInstrument] = useState("ALL");
  const [clearing, setClearing] = useState(false);
  const [loading, setLoading] = useState(true);
  const [runsStatusFilter, setRunsStatusFilter] = useState("");
  const visibleRuns = runsStatusFilter ? runs.filter((r) => r.status === runsStatusFilter) : runs;

  // Background jobs are tracked globally so progress survives navigation.
  const upstoxIngestJob = jobs.upstox_ingest || null;
  const optionFetchJob = jobs.option_fetch || null;
  const upstoxIngesting = isJobActive("upstox_ingest");
  const optionFetching = isJobActive("option_fetch");
  // Mirror the plan into a ref so the (mount-once) job-completion listener can
  // read the latest value without re-subscribing on every plan change.
  const optionPlanResultRef = useRef(null);
  useEffect(() => {
    optionPlanResultRef.current = optionPlanResult;
  }, [optionPlanResult]);

  const refresh = async () => {
    // Fast calls only — the option band heatmap reads the persisted hygiene
    // plan on its own (instant), so nothing heavy gates the page render.
    try {
      const [cov, r, upstox] = await Promise.all([
        api.coverage(),
        api.warehouseRuns(20),
        api.upstoxStatus(),
      ]);
      setCoverage(cov.instruments || {});
      setRuns(r.items || []);
      setUpstoxStatus(upstox);
    } catch (e) {
      toast.error("Failed to load warehouse status");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    if (params.get("upstox_connected") === "1") {
      toast.success("Upstox connected");
      window.history.replaceState({}, "", window.location.pathname);
    } else if (params.get("upstox_error")) {
      toast.error(`Upstox auth failed: ${params.get("upstox_error")}`);
      window.history.replaceState({}, "", window.location.pathname);
    }
    refresh();
  }, []);

  // When a background job finishes, refresh the views it affected. These run
  // even if the user navigated away and came back, because the job tracker is
  // global and replays the completion to whoever is currently mounted.
  useEffect(() => {
    const offIngest = onJobComplete("upstox_ingest", () => {
      refresh();
    });
    const offFetch = onJobComplete("option_fetch", (job) => {
      setOptionFetchResult(job);
      refresh();
      // Re-run the planner preview so Planned coverage reflects the new candles,
      // but only if the user still has a plan loaded for the same instrument.
      if (optionPlanResultRef.current) {
        handleOptionPreview(false);
      }
    });
    // (the option band heatmap refreshes itself from the persisted plan)
    return () => {
      offIngest();
      offFetch();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const handleUpstoxConnect = async () => {
    setUpstoxBusy(true);
    try {
      const res = await api.startUpstoxAuth();
      if (!res.login_url) throw new Error("Upstox did not return a login URL");
      window.location.href = res.login_url;
    } catch (e) {
      const msg = e.response?.data?.detail || e.message;
      toast.error(`Upstox login failed: ${msg}`);
    } finally {
      setUpstoxBusy(false);
    }
  };

  const handleUpstoxDisconnect = async () => {
    setUpstoxBusy(true);
    try {
      await api.disconnectUpstox();
      toast.success("Upstox disconnected");
      await refresh();
    } catch (e) {
      const msg = e.response?.data?.detail || e.message;
      toast.error(`Disconnect failed: ${msg}`);
    } finally {
      setUpstoxBusy(false);
    }
  };

  const handleUpstoxIngest = async () => {
    try {
      const res = await api.startUpstoxIngestJob({
        ...upstoxForm,
        chunk_days: upstoxForm.chunk_days === "" ? null : Number(upstoxForm.chunk_days),
      });
      startJob("upstox_ingest", res);
      toast.success(`Upstox ${upstoxForm.instrument} ingest started in background`);
    } catch (e) {
      const msg = e.response?.data?.detail || e.message;
      toast.error(`Upstox ingest failed: ${msg}`);
    }
  };

  const handleMarketQuote = async () => {
    setQuoteLoading(true);
    try {
      const res = await api.marketQuote(upstoxForm.instrument);
      setMarketQuote(res);
      toast.success(`Live ${upstoxForm.instrument}: ${res.last_price ?? "quote received"}`);
    } catch (e) {
      const msg = e.response?.data?.detail || e.message;
      toast.error(`Live quote failed: ${msg}`);
    } finally {
      setQuoteLoading(false);
    }
  };

  const optionWarehousePayload = () => ({
    ...optionPlanForm,
    sample_interval_minutes: Number(optionPlanForm.sample_interval_minutes || 15),
    chunk_days: optionPlanForm.chunk_days === "" ? null : Number(optionPlanForm.chunk_days),
    max_contracts: Number(optionPlanForm.max_contracts || 50),
    fixed_expiry_date: optionPlanForm.expiry_policy === "fixed" ? optionPlanForm.fixed_expiry_date : null,
  });

  const handleOptionPreview = async (clearFetch = true) => {
    setOptionPlanning(true);
    if (clearFetch) {
      setOptionFetchResult(null);
    }
    try {
      const res = await api.previewOptionWarehouse(optionWarehousePayload());
      setOptionPlanResult(res);
      const s = res.summary || {};
      toast.success(`Option plan: ${fmtInt(s.planned_contracts || 0)} contracts, ${fmtInt(s.missing_data_contracts || 0)} need fetch`);
    } catch (e) {
      const msg = e.response?.data?.detail || e.message;
      toast.error(`Option preview failed: ${msg}`);
    } finally {
      setOptionPlanning(false);
    }
  };

  const handleOptionFetch = async () => {
    try {
      const res = await api.startOptionWarehouseFetchJob(optionWarehousePayload());
      setOptionFetchResult(null);
      startJob("option_fetch", res);
      toast.success(`Option fetch started in background: ${fmtInt(res.fetch_contracts || 0)} contracts`);
    } catch (e) {
      const msg = e.response?.data?.detail || e.message;
      toast.error(`Option fetch failed: ${msg}`);
    }
  };

  const handleClearOptionData = async () => {
    const target = auditForm.instrument || "ALL";
    // Typed confirmation: mass deletes must not be one accidental click away.
    const typed = window.prompt(
      `This permanently deletes ALL stored option candles for ${target} (contract metadata and index candles are kept).\n\nType ${target} to confirm:`,
    );
    if (typed !== target) {
      if (typed !== null) toast.warning("Confirmation text did not match — nothing deleted.");
      return;
    }
    setOptionClearing(true);
    try {
      const res = await api.clearOptionData(target);
      toast.success(`Cleared ${fmtInt(res.option_candles_deleted || 0)} option candles`);
      await refresh();
    } catch (e) {
      const msg = e.response?.data?.detail || e.message;
      toast.error(`Option clear failed: ${msg}`);
    } finally {
      setOptionClearing(false);
    }
  };

  const handleExpiredBackfill = async () => {
    setExpiredBackfilling(true);
    try {
      const payload = {
        from_date: expiredBackfillForm.from_date,
        to_date: expiredBackfillForm.to_date,
        max_expiries: Number(expiredBackfillForm.max_expiries || 8),
        confirm_large_fetch: !!expiredBackfillForm.confirm_large_fetch,
      };
      const res = await api.backfillExpiredOptionContracts(expiredBackfillForm.instrument, payload);
      setExpiredBackfillResult(res);
      if (res.status === "blocked") {
        toast.warning(`Backfill blocked: ${res.expiry_count || 0} expiries above guard`);
      } else if (res.status === "ok" || res.status === "partial" || res.status === "empty") {
        toast.success(`Expired contracts ${res.status}: ${fmtInt(res.upserted || 0)} stored`);
        await refresh();
      } else {
        toast.error(`Expired contract backfill ${res.status}`);
      }
    } catch (e) {
      const msg = e.response?.data?.detail || e.message;
      toast.error(`Expired contract backfill failed: ${msg}`);
    } finally {
      setExpiredBackfilling(false);
    }
  };

  const handleAudit = async () => {
    setAuditLoading(true);
    try {
      const res = await api.auditWarehouse(
        auditForm.instrument,
        dateToMs(auditForm.from_date, false),
        dateToMs(auditForm.to_date, true),
      );
      setAuditResult(res);
      const s = res.summary || {};
      toast.success(`Audit complete: ${s.complete_days || 0}/${s.expected_days || 0} complete days`);
    } catch (e) {
      const msg = e.response?.data?.detail || e.message;
      toast.error(`Audit failed: ${msg}`);
    } finally {
      setAuditLoading(false);
    }
  };

  const handleClearWarehouse = async () => {
    const target = clearInstrument || "ALL";
    const typed = window.prompt(
      `This permanently deletes stored ${target} index candles, integrity hashes, and ingest runs.\n\nType ${target} to confirm:`,
    );
    if (typed !== target) {
      if (typed !== null) toast.warning("Confirmation text did not match — nothing deleted.");
      return;
    }
    setClearing(true);
    try {
      const res = await api.clearWarehouseData(target);
      toast.success(`Cleared ${fmtInt(res.candles_deleted || 0)} candles`);
      setAuditResult(null);
      await refresh();
    } catch (e) {
      const msg = e.response?.data?.detail || e.message;
      toast.error(`Clear failed: ${msg}`);
    } finally {
      setClearing(false);
    }
  };

  if (loading) {
    return (
      <div className="space-y-3">
        <Skeleton className="h-48 bg-bg-1" />
        <Skeleton className="h-48 bg-bg-1" />
      </div>
    );
  }

  return (
    <div className="space-y-3" data-testid="data-warehouse-page">
      <div className="flex items-center gap-2">
        <div className="text-[11px] text-dimmer">
          Data warehouse: index spot + ATM option candles, audited against the NSE/BSE trading calendar.
        </div>
        <div className="ml-auto">
          <HolidayCalendarDialog />
        </div>
      </div>

      <HowThisPageWorks />

      <UpstoxPanel
        status={upstoxStatus}
        busy={upstoxBusy}
        ingesting={upstoxIngesting}
        form={upstoxForm}
        setForm={setUpstoxForm}
        ingestJob={upstoxIngestJob}
        onConnect={handleUpstoxConnect}
        onDisconnect={handleUpstoxDisconnect}
        onIngest={handleUpstoxIngest}
        onQuote={handleMarketQuote}
        quote={marketQuote}
        quoteLoading={quoteLoading}
      />

      {/* ============ Data Hygiene (one-click health + fill) ============ */}
      <SectionHeader title="Data Hygiene" subtitle="One-click warehouse health check and gap fill" />
      <DataHygienePanel upstoxConnected={upstoxStatus?.connected && !upstoxStatus?.expired} />

      {/* ============ Index data ============ */}
      <SectionHeader title="Index Data" subtitle="1-minute spot candles for NIFTY, BANKNIFTY, SENSEX" />

      {/* Index data coverage (status only; ingest is handled via Upstox above) */}
      <div className="rounded-lg border border-line bg-bg-1">
        <div className="px-3 py-2 border-b border-line flex items-center">
          <Database className="w-4 h-4 mr-2 text-info" />
          <div className="text-xs font-semibold uppercase tracking-wider text-dim">Index Data Coverage</div>
          <Button variant="ghost" size="sm" onClick={refresh} className="ml-auto h-7 text-xs" data-testid="warehouse-refresh-button">
            <RefreshCw className="w-3 h-3 mr-1" /> Refresh
          </Button>
        </div>
        <div className="p-3 grid grid-cols-1 lg:grid-cols-3 gap-3">
          {INSTRUMENTS.map((inst) => {
            const c = (coverage || {})[inst];
            return (
              <div key={inst} className="rounded-md border border-line bg-bg-2 p-3" data-testid={`warehouse-card-${inst.toLowerCase()}`}>
                <div className="flex items-center justify-between mb-2">
                  <div className="text-sm font-semibold">{inst}</div>
                  {c ? (
                    <span className="text-[10px] px-1.5 py-0.5 rounded bg-emerald-950 text-emerald-200 border border-emerald-900">
                      <CheckCircle2 className="w-3 h-3 inline mr-0.5" />{fmtInt(c.candle_count)}
                    </span>
                  ) : (
                    <span className="text-[10px] px-1.5 py-0.5 rounded bg-rose-950 text-rose-200 border border-rose-900">
                      <AlertCircle className="w-3 h-3 inline mr-0.5" />empty
                    </span>
                  )}
                </div>
                {c ? (
                  <div className="text-[11px] text-dim font-mono">
                    {c.min_datetime} <span className="text-dimmer">→</span> {c.max_datetime}
                    <div className="mt-1">{c.days?.length || 0} trading days</div>
                  </div>
                ) : (
                  <div className="text-[11px] text-dimmer">No candles stored. Use Upstox Broker Data above to ingest.</div>
                )}
              </div>
            );
          })}
        </div>
      </div>

      <CoverageHeatmap coverage={coverage} />

      <WarehouseChart />

      {/* ============ Option data ============ */}
      <SectionHeader title="Option Data" subtitle="Daily ATM-band coverage — maintained automatically by Sync / auto-update" />

      <OptionCoverageHeatmap />

      <AdvancedTools>
        <OptionWarehousePanel
          status={upstoxStatus}
          form={optionPlanForm}
          setForm={setOptionPlanForm}
          plan={optionPlanResult}
          fetchResult={optionFetchResult}
          fetchJob={optionFetchJob}
          planning={optionPlanning}
          fetching={optionFetching}
          onPreview={handleOptionPreview}
          onFetch={handleOptionFetch}
        />
        <ExpiredContractBackfillPanel
          status={upstoxStatus}
          form={expiredBackfillForm}
          setForm={setExpiredBackfillForm}
          result={expiredBackfillResult}
          running={expiredBackfilling}
          onBackfill={handleExpiredBackfill}
        />
      </AdvancedTools>

      {/* ============ Verify & audit ============ */}
      <SectionHeader title="Verify & Audit" subtitle="Confirm completeness and integrity of stored data" />

      <WarehouseLookup />

      <VolatilityAuditPanel />

      <DataTrustPanel
        auditForm={auditForm}
        setAuditForm={setAuditForm}
        auditResult={auditResult}
        auditLoading={auditLoading}
        onAudit={handleAudit}
        clearInstrument={clearInstrument}
        setClearInstrument={setClearInstrument}
        clearing={clearing}
        onClear={handleClearWarehouse}
        optionClearing={optionClearing}
        onClearOptions={handleClearOptionData}
      />

      {/* ============ Diagnostics ============ */}
      <SectionHeader title="Diagnostics" subtitle="Recent ingest and fetch activity" />

      {/* Recent ingest runs */}
      <div className="rounded-lg border border-line bg-bg-1">
        <div className="px-3 py-2 border-b border-line flex items-center gap-2">
          <div className="text-xs font-semibold uppercase tracking-wider text-dim">Recent Ingest Runs</div>
          <select
            value={runsStatusFilter}
            onChange={(e) => setRunsStatusFilter(e.target.value)}
            className="ml-auto h-6 rounded-md border border-input bg-bg-1 px-2 text-[11px] text-foreground"
            data-testid="warehouse-runs-filter"
          >
            <option value="">All statuses</option>
            {["ok", "partial", "failed", "empty", "skipped", "running", "queued"].map((s) => (
              <option key={s} value={s}>{s}</option>
            ))}
          </select>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-xs" data-testid="warehouse-runs-table">
            <thead>
              <tr className="text-dim border-b border-line">
                <th className="text-left p-2">Started</th>
                <th className="text-left p-2">Instrument</th>
                <th className="text-left p-2">Source</th>
                <th className="text-left p-2">Days</th>
                <th className="text-right p-2">Fetched</th>
                <th className="text-right p-2">Added</th>
                <th className="text-right p-2">Updated</th>
                <th className="text-left p-2">Status</th>
              </tr>
            </thead>
            <tbody>
              {visibleRuns.length === 0 && (
                <tr><td colSpan="8" className="p-4 text-center text-dimmer">No ingest runs{runsStatusFilter ? ` with status ${runsStatusFilter}` : " yet"}</td></tr>
              )}
              {visibleRuns.map((r) => (
                <tr key={r.id} className="border-b border-line" data-testid="warehouse-run-row">
                  <td className="p-2 font-mono text-dim">{isoToFull(r.started_at)}</td>
                  <td className="p-2 font-mono">{r.instrument}</td>
                  <td className="p-2 text-dim" title={`${r.source}${r.kind ? ` · ${r.kind}` : ""}`}>
                    {RUN_SOURCE_LABELS[r.source] || r.source}{r.kind ? <span className="text-dimmer"> · {r.kind}</span> : null}
                  </td>
                  <td className="p-2 font-mono">{r.days}</td>
                  <td className="p-2 font-mono text-right">{fmtInt(r.total_fetched || 0)}</td>
                  <td className="p-2 font-mono text-right text-success">{fmtInt(r.candles_added || 0)}</td>
                  <td className="p-2 font-mono text-right text-dim">{fmtInt(r.candles_updated || 0)}</td>
                  <td className="p-2">
                    <span className={`text-[10px] px-1.5 py-0.5 rounded font-mono ${r.status === "ok" ? "bg-emerald-950 text-emerald-200 border border-emerald-900" : r.status === "failed" ? "bg-rose-950 text-rose-200 border border-rose-900" : "bg-amber-950 text-amber-200 border border-amber-900"}`}>
                      {r.status}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
