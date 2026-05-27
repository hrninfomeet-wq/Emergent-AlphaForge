import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Database, Download, RefreshCw, CheckCircle2, AlertCircle, Link2, Unplug, ShieldCheck, Trash2 } from "lucide-react";
import { fmtInt, fmtNum, fmtSigned, isoToFull, tsToFull } from "@/lib/fmt";
import { Skeleton } from "@/components/ui/skeleton";

const INSTRUMENTS = ["NIFTY", "BANKNIFTY", "SENSEX"];
const MONEYNESS_OPTIONS = ["atm", "itm1", "itm2", "otm1", "otm2", "otm3"];
const LEG_OPTIONS = ["CE", "PE"];

function dateInput(daysAgo = 0) {
  const d = new Date(Date.now() - daysAgo * 24 * 60 * 60 * 1000);
  return d.toISOString().slice(0, 10);
}

function dateToMs(s, endOfDay = false) {
  if (!s) return null;
  const [y, m, d] = s.split("-").map(Number);
  if (!y || !m || !d) return null;
  const istHour = endOfDay ? 15 : 9;
  const istMin = endOfDay ? 30 : 15;
  return Date.UTC(y, m - 1, d, istHour, istMin, 0) - (5 * 60 + 30) * 60 * 1000;
}

function quoteTimeDisplay(quote) {
  const raw = quote?.timestamp || quote?.last_trade_time;
  if (!raw) return "n/a";
  if (/^\d+$/.test(String(raw))) return tsToFull(Number(raw));
  return isoToFull(raw);
}

export default function DataWarehouse() {
  const [coverage, setCoverage] = useState(null);
  const [optionCoverage, setOptionCoverage] = useState(null);
  const [runs, setRuns] = useState([]);
  const [ingesting, setIngesting] = useState({});
  const [upstoxStatus, setUpstoxStatus] = useState(null);
  const [upstoxBusy, setUpstoxBusy] = useState(false);
  const [upstoxIngesting, setUpstoxIngesting] = useState(false);
  const [upstoxIngestJob, setUpstoxIngestJob] = useState(null);
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
  const [optionFetchJob, setOptionFetchJob] = useState(null);
  const [optionPlanning, setOptionPlanning] = useState(false);
  const [optionFetching, setOptionFetching] = useState(false);
  const [optionAuditForm, setOptionAuditForm] = useState({
    underlying: "NIFTY",
    from_date: dateInput(30),
    to_date: dateInput(0),
    expiry: "",
    side: "",
    limit_contracts: 500,
  });
  const [optionAuditResult, setOptionAuditResult] = useState(null);
  const [optionAuditLoading, setOptionAuditLoading] = useState(false);
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

  const refresh = async () => {
    try {
      const [cov, optionCov, r, upstox] = await Promise.all([api.coverage(), api.optionCoverage(), api.warehouseRuns(20), api.upstoxStatus()]);
      setCoverage(cov.instruments || {});
      setOptionCoverage(optionCov.instruments || {});
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

  const handleIngest = async (instrument, days = 7) => {
    setIngesting((s) => ({ ...s, [instrument]: true }));
    try {
      const res = await api.ingest(instrument, days);
      if (res.status === "ok" || res.status === "empty") {
        toast.success(`Ingested ${instrument}: +${res.candles_added} / ~${res.candles_updated} updated`);
      } else {
        toast.error(`Ingest failed: ${res.error || res.status}`);
      }
      await refresh();
    } catch (e) {
      const msg = e.response?.data?.detail || e.message;
      toast.error(`Ingest failed: ${msg}`);
    } finally {
      setIngesting((s) => ({ ...s, [instrument]: false }));
    }
  };

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

  const pollUpstoxIngestJob = async (runId) => {
    for (let attempt = 0; attempt < 720; attempt += 1) {
      const job = await api.getUpstoxIngestJob(runId);
      setUpstoxIngestJob(job);
      if (!["queued", "running"].includes(job.status)) {
        const chunk = job.chunk_days ? ` · chunk ${job.chunk_days}d` : "";
        if (job.status === "ok" || job.status === "empty") {
          toast.success(`Upstox ${job.instrument}: +${job.candles_added || 0} / ~${job.candles_updated || 0} updated${chunk}`);
        } else {
          toast.error(`Upstox ingest ${job.status}: ${(job.failed_chunks || [])[0]?.error || "check run details"}`);
        }
        await refresh();
        return job;
      }
      await new Promise((resolve) => setTimeout(resolve, 2500));
    }
    throw new Error("Ingest job polling timed out. The backend run may still be active; check Recent Ingest Runs.");
  };

  const handleUpstoxIngest = async () => {
    setUpstoxIngesting(true);
    try {
      const res = await api.startUpstoxIngestJob({
        ...upstoxForm,
        chunk_days: upstoxForm.chunk_days === "" ? null : Number(upstoxForm.chunk_days),
      });
      setUpstoxIngestJob(res);
      toast.success(`Upstox ${upstoxForm.instrument} ingest started in background`);
      await pollUpstoxIngestJob(res.id);
    } catch (e) {
      const msg = e.response?.data?.detail || e.message;
      toast.error(`Upstox ingest failed: ${msg}`);
    } finally {
      setUpstoxIngesting(false);
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
      setOptionFetchJob(null);
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

  const pollOptionFetchJob = async (runId) => {
    for (let attempt = 0; attempt < 1440; attempt += 1) {
      const job = await api.getOptionWarehouseFetchJob(runId);
      setOptionFetchJob(job);
      if (!["queued", "running"].includes(job.status)) {
        setOptionFetchResult(job);
        toast.success(`Option fetch ${job.status}: +${fmtInt(job.candles_added || 0)} / ~${fmtInt(job.candles_updated || 0)} updated`);
        await handleOptionPreview(false);
        await refresh();
        return job;
      }
      await new Promise((resolve) => setTimeout(resolve, 2500));
    }
    throw new Error("Option fetch polling timed out. The backend job may still be active; check Recent Ingest Runs.");
  };

  const handleOptionFetch = async () => {
    setOptionFetching(true);
    try {
      const res = await api.startOptionWarehouseFetchJob(optionWarehousePayload());
      setOptionFetchJob(res);
      setOptionFetchResult(null);
      toast.success(`Option fetch started in background: ${fmtInt(res.fetch_contracts || 0)} contracts`);
      await pollOptionFetchJob(res.id);
    } catch (e) {
      const msg = e.response?.data?.detail || e.message;
      toast.error(`Option fetch failed: ${msg}`);
    } finally {
      setOptionFetching(false);
    }
  };

  const handleOptionAudit = async () => {
    setOptionAuditLoading(true);
    try {
      const res = await api.auditOptionData(optionAuditForm.underlying, {
        start_ts: dateToMs(optionAuditForm.from_date, false),
        end_ts: dateToMs(optionAuditForm.to_date, true),
        ...(optionAuditForm.expiry ? { expiry: optionAuditForm.expiry } : {}),
        ...(optionAuditForm.side ? { side: optionAuditForm.side } : {}),
        limit_contracts: Number(optionAuditForm.limit_contracts || 500),
      });
      setOptionAuditResult(res);
      const s = res.summary || {};
      toast.success(`Option audit: ${fmtInt(s.complete_contracts || 0)}/${fmtInt(s.contracts_checked || 0)} contracts complete`);
    } catch (e) {
      const msg = e.response?.data?.detail || e.message;
      toast.error(`Option audit failed: ${msg}`);
    } finally {
      setOptionAuditLoading(false);
    }
  };

  const handleClearOptionData = async () => {
    const target = optionAuditForm.underlying || "ALL";
    const ok = window.confirm(`Clear stored option candles for ${target}? Contract metadata and index candles will be kept.`);
    if (!ok) return;
    setOptionClearing(true);
    try {
      const res = await api.clearOptionData(target);
      toast.success(`Cleared ${fmtInt(res.option_candles_deleted || 0)} option candles`);
      setOptionAuditResult(null);
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
    const ok = window.confirm(`Clear stored ${target} warehouse candles, hashes, and ingest runs?`);
    if (!ok) return;
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

      <OptionAuditPanel
        form={optionAuditForm}
        setForm={setOptionAuditForm}
        result={optionAuditResult}
        loading={optionAuditLoading}
        clearing={optionClearing}
        onAudit={handleOptionAudit}
        onClear={handleClearOptionData}
      />

      <ExpiredContractBackfillPanel
        status={upstoxStatus}
        form={expiredBackfillForm}
        setForm={setExpiredBackfillForm}
        result={expiredBackfillResult}
        running={expiredBackfilling}
        onBackfill={handleExpiredBackfill}
      />

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
      />

      {/* Ingest controls */}
      <div className="rounded-lg border border-line bg-bg-1">
        <div className="px-3 py-2 border-b border-line flex items-center">
          <Database className="w-4 h-4 mr-2 text-info" />
          <div className="text-xs font-semibold uppercase tracking-wider text-dim">Ingest Data</div>
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
                  <div className="text-[11px] text-dimmer">No candles. Click ingest to fetch last 7 days from yfinance.</div>
                )}
                <div className="flex gap-2 mt-3">
                  <Button
                    size="sm"
                    onClick={() => handleIngest(inst, 7)}
                    disabled={ingesting[inst]}
                    className="h-7 text-xs bg-bg-3 border border-line hover:bg-bg-2 flex-1"
                    data-testid={`warehouse-ingest-${inst.toLowerCase()}-7d`}
                  >
                    <Download className="w-3 h-3 mr-1" />
                    {ingesting[inst] ? "Fetching…" : "Ingest 7d"}
                  </Button>
                  <Button
                    size="sm"
                    variant="secondary"
                    onClick={() => handleIngest(inst, 14)}
                    disabled={ingesting[inst]}
                    className="h-7 text-xs"
                    data-testid={`warehouse-ingest-${inst.toLowerCase()}-14d`}
                  >
                    14d
                  </Button>
                </div>
              </div>
            );
          })}
        </div>
        <div className="px-3 py-2 border-t border-line text-[11px] text-dimmer">
          Note: yfinance limits 1-minute data to the last ~30 days. Use Upstox (Phase 4) for longer history.
        </div>
      </div>

      {/* Per-day coverage heatmap */}
      <CoverageHeatmap coverage={coverage} />
      <OptionCoverageHeatmap coverage={optionCoverage} />

      {/* Recent ingest runs */}
      <div className="rounded-lg border border-line bg-bg-1">
        <div className="px-3 py-2 border-b border-line">
          <div className="text-xs font-semibold uppercase tracking-wider text-dim">Recent Ingest Runs</div>
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
              {runs.length === 0 && (
                <tr><td colSpan="8" className="p-4 text-center text-dimmer">No ingest runs yet</td></tr>
              )}
              {runs.map((r) => (
                <tr key={r.id} className="border-b border-line" data-testid="warehouse-run-row">
                  <td className="p-2 font-mono text-dim">{isoToFull(r.started_at)}</td>
                  <td className="p-2 font-mono">{r.instrument}</td>
                  <td className="p-2 text-dim">{r.source}</td>
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

function UpstoxPanel({ status, busy, ingesting, form, setForm, ingestJob, onConnect, onDisconnect, onIngest, onQuote, quote, quoteLoading }) {
  const configured = status?.configured;
  const connected = status?.connected && !status?.expired;
  const statusClass = connected
    ? "bg-emerald-950 text-emerald-200 border-emerald-900"
    : configured
      ? "bg-amber-950 text-amber-200 border-amber-900"
      : "bg-rose-950 text-rose-200 border-rose-900";
  const statusText = connected ? "connected" : configured ? "ready for OAuth" : "needs credentials";

  const set = (key, value) => setForm((prev) => ({ ...prev, [key]: value }));

  return (
    <div className="rounded-lg border border-line bg-bg-1" data-testid="upstox-panel">
      <div className="px-3 py-2 border-b border-line flex items-center gap-2">
        <Link2 className="w-4 h-4 text-info" />
        <div className="text-xs font-semibold uppercase tracking-wider text-dim">Upstox Broker Data</div>
        <span className={`ml-auto text-[10px] px-1.5 py-0.5 rounded border font-mono ${statusClass}`} data-testid="upstox-status-badge">
          {statusText}
        </span>
      </div>
      <div className="p-3 grid grid-cols-1 xl:grid-cols-[1fr_2fr] gap-3">
        <div className="rounded-md border border-line bg-bg-2 p-3">
          <div className="text-sm font-semibold">OAuth</div>
          <div className="text-[11px] text-dim mt-1">
            {connected
              ? `Token stored${status.expires_at ? ` · expires ${status.expires_at}` : ""}`
              : configured
                ? "Broker keys loaded. Connect after the Upstox redirect URL is registered."
                : "Add broker keys in backend/.env and restart backend."}
          </div>
          <div className="flex gap-2 mt-3">
            <Button
              size="sm"
              onClick={onConnect}
              disabled={busy || !configured}
              className="h-7 text-xs bg-bg-3 border border-line hover:bg-bg-2 flex-1"
              data-testid="upstox-connect-button"
            >
              <Link2 className="w-3 h-3 mr-1" />
              {connected ? "Reconnect" : "Connect Upstox"}
            </Button>
            <Button
              size="sm"
              variant="secondary"
              onClick={onDisconnect}
              disabled={busy || !status?.connected}
              className="h-7 text-xs"
              data-testid="upstox-disconnect-button"
            >
              <Unplug className="w-3 h-3 mr-1" />
              Disconnect
            </Button>
          </div>
          <div className="mt-3 rounded-md border border-line bg-bg-1 p-2" data-testid="upstox-live-quote-card">
            <div className="flex items-center justify-between gap-2">
              <div>
                <div className="text-[10px] uppercase tracking-wider text-dimmer">Live market snapshot</div>
                <div className="mt-1 text-xs font-mono">
                  {quote?.underlying === form.instrument && quote?.last_price != null
                    ? `${quote.underlying} ${fmtNum(quote.last_price, 2)}`
                    : "No quote loaded"}
                </div>
              </div>
              <Button
                size="sm"
                variant="secondary"
                onClick={onQuote}
                disabled={quoteLoading || !connected}
                className="h-7 text-xs"
                data-testid="upstox-live-quote-button"
              >
                <RefreshCw className="w-3 h-3 mr-1" />
                {quoteLoading ? "Reading..." : "Quote"}
              </Button>
            </div>
            {quote?.underlying === form.instrument && (
              <div className="mt-2 grid grid-cols-2 gap-2 text-[11px] text-dim">
                <div>
                  <span className="text-dimmer">Change</span>
                  <span className="ml-1 font-mono">{fmtSigned(quote.net_change, 2)}</span>
                </div>
                <div>
                  <span className="text-dimmer">Time</span>
                  <span className="ml-1 font-mono">{quoteTimeDisplay(quote)}</span>
                </div>
              </div>
            )}
          </div>
        </div>

        <div className="rounded-md border border-line bg-bg-2 p-3">
          <div className="grid grid-cols-2 lg:grid-cols-[1fr_1fr_1fr_0.8fr_auto] gap-2 items-end">
            <label className="text-[11px] text-dim">
              Instrument
              <select
                value={form.instrument}
                onChange={(e) => set("instrument", e.target.value)}
                className="mt-1 h-9 w-full rounded-md border border-input bg-bg-1 px-3 py-1 text-sm text-foreground"
                data-testid="upstox-instrument-select"
              >
                {INSTRUMENTS.map((inst) => <option key={inst} value={inst}>{inst}</option>)}
              </select>
            </label>
            <label className="text-[11px] text-dim">
              From
              <Input
                type="date"
                value={form.from_date}
                onChange={(e) => set("from_date", e.target.value)}
                className="mt-1 bg-bg-1 border-line"
                data-testid="upstox-from-date"
              />
            </label>
            <label className="text-[11px] text-dim">
              To
              <Input
                type="date"
                value={form.to_date}
                onChange={(e) => set("to_date", e.target.value)}
                className="mt-1 bg-bg-1 border-line"
                data-testid="upstox-to-date"
              />
            </label>
            <label className="text-[11px] text-dim">
              Chunk
              <Input
                type="number"
                min="1"
                max="30"
                placeholder="Auto"
                value={form.chunk_days}
                onChange={(e) => set("chunk_days", e.target.value)}
                className="mt-1 bg-bg-1 border-line text-right"
                data-testid="upstox-chunk-days"
              />
            </label>
            <Button
              size="sm"
              onClick={onIngest}
              disabled={ingesting || !connected}
              className="h-9 text-xs bg-bg-3 border border-line hover:bg-bg-2"
              data-testid="upstox-ingest-button"
            >
              <Download className="w-3 h-3 mr-1" />
              {ingesting ? "Fetching..." : "Ingest"}
            </Button>
          </div>
          <div className="mt-2 text-[11px] text-dim" data-testid="upstox-chunk-guidance">
            Auto is recommended for index candles. It uses conservative broker calls for one instrument; use 1-3 after failures, or 14-30 only when you want faster but heavier requests.
          </div>
          <div className="mt-2 rounded-md border border-line bg-bg-1 p-2 text-[11px] text-dim" data-testid="upstox-large-import-help">
            Large imports run in background now. For 12-18 month spot history, leave Chunk as Auto, click Ingest once, keep this page open for progress, then run Data Trust Audit for the same date range.
          </div>
          {ingestJob && (
            <div className="mt-2 rounded-md border border-line bg-bg-1 p-2" data-testid="upstox-ingest-progress">
              <div className="flex items-center justify-between gap-2 text-[11px]">
                <span className="font-mono uppercase text-dim">{ingestJob.status}</span>
                <span className="font-mono text-dimmer">{fmtNum(ingestJob.progress_pct || 0, 1)}%</span>
              </div>
              <div className="mt-2 h-2 rounded bg-bg-3 overflow-hidden">
                <div
                  className="h-full bg-info transition-all"
                  style={{ width: `${Math.min(100, Math.max(0, Number(ingestJob.progress_pct || 0)))}%` }}
                />
              </div>
              <div className="mt-2 grid grid-cols-2 lg:grid-cols-4 gap-2 text-[11px] text-dim">
                <div>
                  <span className="text-dimmer">Chunks</span>
                  <span className="ml-1 font-mono">{fmtInt(ingestJob.completed_chunks || 0)}/{fmtInt(ingestJob.total_chunks || 0)}</span>
                </div>
                <div>
                  <span className="text-dimmer">Fetched</span>
                  <span className="ml-1 font-mono">{fmtInt(ingestJob.total_fetched || 0)}</span>
                </div>
                <div>
                  <span className="text-dimmer">Added</span>
                  <span className="ml-1 font-mono">{fmtInt(ingestJob.candles_added || 0)}</span>
                </div>
                <div>
                  <span className="text-dimmer">Matched</span>
                  <span className="ml-1 font-mono">{fmtInt(ingestJob.matched_existing || 0)}</span>
                </div>
              </div>
              {Boolean(ingestJob.failed_chunks?.length) && (
                <div className="mt-2 text-[11px] text-danger">
                  Failed chunks: {fmtInt(ingestJob.failed_chunks.length)}. Lower Chunk to 1-3 and retry the same date range.
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function ExpiredContractBackfillPanel({ status, form, setForm, result, running, onBackfill }) {
  const connected = status?.connected && !status?.expired;
  const set = (key, value) => setForm((prev) => ({ ...prev, [key]: value }));
  const blocked = result?.status === "blocked";

  return (
    <div className="rounded-lg border border-line bg-bg-1" data-testid="expired-contract-backfill-panel">
      <div className="px-3 py-2 border-b border-line flex items-center gap-2">
        <Database className="w-4 h-4 text-info" />
        <div className="text-xs font-semibold uppercase tracking-wider text-dim">Backfill expired option contracts</div>
        {result && (
          <span className={`ml-auto text-[10px] px-1.5 py-0.5 rounded border font-mono ${blocked ? "bg-amber-950 text-amber-200 border-amber-900" : result.status === "ok" ? "bg-emerald-950 text-emerald-200 border-emerald-900" : "bg-bg-3 text-dim border-line"}`}>
            {result.status}
          </span>
        )}
      </div>
      <div className="p-3 grid grid-cols-1 xl:grid-cols-[minmax(0,2fr)_minmax(260px,1fr)] gap-3">
        <div className="rounded-md border border-line bg-bg-2 p-3">
          <div className="grid grid-cols-2 lg:grid-cols-[1fr_1fr_1fr_0.8fr_auto] gap-2 items-end">
            <label className="text-[11px] text-dim">
              Instrument
              <select
                value={form.instrument}
                onChange={(e) => set("instrument", e.target.value)}
                className="mt-1 h-9 w-full rounded-md border border-input bg-bg-1 px-3 py-1 text-sm text-foreground"
                data-testid="expired-contract-instrument"
              >
                {INSTRUMENTS.map((inst) => <option key={inst} value={inst}>{inst}</option>)}
              </select>
            </label>
            <label className="text-[11px] text-dim">
              From
              <Input
                type="date"
                value={form.from_date}
                onChange={(e) => set("from_date", e.target.value)}
                className="mt-1 bg-bg-1 border-line"
                data-testid="expired-contract-from-date"
              />
            </label>
            <label className="text-[11px] text-dim">
              To
              <Input
                type="date"
                value={form.to_date}
                onChange={(e) => set("to_date", e.target.value)}
                className="mt-1 bg-bg-1 border-line"
                data-testid="expired-contract-to-date"
              />
            </label>
            <label className="text-[11px] text-dim">
              Max expiries
              <Input
                type="number"
                min="1"
                max="120"
                value={form.max_expiries}
                onChange={(e) => set("max_expiries", e.target.value)}
                className="mt-1 bg-bg-1 border-line text-right"
                data-testid="expired-contract-max-expiries"
              />
            </label>
            <Button
              size="sm"
              onClick={onBackfill}
              disabled={running || !connected}
              className="h-9 text-xs bg-bg-3 border border-line hover:bg-bg-2"
              data-testid="expired-contract-backfill-button"
            >
              <Download className="w-3 h-3 mr-1" />
              {running ? "Backfilling..." : "Backfill"}
            </Button>
          </div>
          <label className="mt-3 text-[11px] text-dim flex items-center gap-2">
            <input
              type="checkbox"
              checked={!!form.confirm_large_fetch}
              onChange={(e) => set("confirm_large_fetch", e.target.checked)}
              className="h-4 w-4"
              data-testid="expired-contract-confirm-large"
            />
            Allow more expiries than the max guard for this request
          </label>
          <div className="mt-2 text-[11px] text-dim">
            This is metadata only: it stores expired contracts by expiry, strike, side, and instrument key. It is required before old option candles can be planned reliably.
          </div>
        </div>

        <div className="rounded-md border border-line bg-bg-2 p-3" data-testid="expired-contract-backfill-summary">
          <div className="text-[10px] uppercase tracking-wider text-dimmer">Backfill result</div>
          {result ? (
            <div className="mt-2 grid grid-cols-2 gap-2 text-xs">
              <div>
                <div className="text-dimmer">Expiries</div>
                <div className="font-mono">{fmtInt(result.expiry_count || 0)}</div>
              </div>
              <div>
                <div className="text-dimmer">Contracts</div>
                <div className="font-mono">{fmtInt(result.fetched_contracts || 0)}</div>
              </div>
              <div>
                <div className="text-dimmer">Stored</div>
                <div className="font-mono">{fmtInt(result.upserted || 0)}</div>
              </div>
              <div>
                <div className="text-dimmer">Skipped</div>
                <div className="font-mono">{fmtInt(result.skipped || 0)}</div>
              </div>
              {result.reason && (
                <div className="col-span-2 rounded-md border border-amber-900 bg-amber-950/40 p-2 text-amber-100">
                  {result.reason}
                </div>
              )}
              {result.failed?.length > 0 && (
                <div className="col-span-2 text-rose-200">
                  Failed expiries: {result.failed.slice(0, 5).map((item) => item.expiry).join(", ")}
                </div>
              )}
            </div>
          ) : (
            <div className="mt-2 text-[11px] text-dim">
              Use this before historical option planning when old expiries are missing from local contract metadata. Requires Upstox expired-instruments access.
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function OptionWarehousePanel({
  status,
  form,
  setForm,
  plan,
  fetchResult,
  fetchJob,
  planning,
  fetching,
  onPreview,
  onFetch,
}) {
  const connected = status?.connected && !status?.expired;
  const summary = plan?.summary || {};
  const guidance = plan?.chunk_guidance;
  const items = plan?.items || [];
  const missingCount = summary.missing_data_contracts || 0;
  const missingMetaCount = summary.missing_contract_count || plan?.missing_count || 0;
  const plannedCoverage = Number(summary.planned_coverage_pct ?? 0);
  const fetchDateCount = summary.fetch_date_count || 0;
  const fetchContractCount = form.fetch_missing_only ? missingCount : items.length;
  const fetchBlockedByMax = !!plan && fetchContractCount > Number(form.max_contracts || 0);
  const fetchNothingToDo = !!plan && form.fetch_missing_only && missingCount === 0;
  const fixedExpiryMissing = form.expiry_policy === "fixed" && !form.fixed_expiry_date;
  const set = (key, value) => setForm((prev) => ({ ...prev, [key]: value }));
  const toggleListValue = (key, value, orderedValues) => {
    setForm((prev) => {
      const current = prev[key] || [];
      const nextRaw = current.includes(value)
        ? current.filter((item) => item !== value)
        : [...current, value];
      const next = orderedValues.filter((item) => nextRaw.includes(item));
      return { ...prev, [key]: next.length ? next : [value] };
    });
  };

  return (
    <div className="rounded-lg border border-line bg-bg-1" data-testid="option-warehouse-panel">
      <div className="px-3 py-2 border-b border-line flex items-center gap-2">
        <Database className="w-4 h-4 text-info" />
        <div className="text-xs font-semibold uppercase tracking-wider text-dim">Option Data Planner</div>
        {plan && (
          <span className="ml-auto text-[10px] px-1.5 py-0.5 rounded border font-mono bg-bg-3 text-dim border-line">
            {fmtInt(summary.planned_contracts || 0)} contracts
          </span>
        )}
      </div>
      <div className="p-3 space-y-3">
        <div className="rounded-md border border-line bg-bg-2 p-3">
          <div className="grid grid-cols-2 xl:grid-cols-[1fr_1fr_1fr_1fr_0.8fr_0.8fr_auto] gap-2 items-end">
            <label className="text-[11px] text-dim">
              Instrument
              <select
                value={form.underlying}
                onChange={(e) => set("underlying", e.target.value)}
                className="mt-1 h-9 w-full rounded-md border border-input bg-bg-1 px-3 py-1 text-sm text-foreground"
                data-testid="option-warehouse-instrument"
              >
                {INSTRUMENTS.map((inst) => <option key={inst} value={inst}>{inst}</option>)}
              </select>
            </label>
            <label className="text-[11px] text-dim">
              From
              <Input
                type="date"
                value={form.from_date}
                onChange={(e) => set("from_date", e.target.value)}
                className="mt-1 bg-bg-1 border-line"
                data-testid="option-warehouse-from-date"
              />
            </label>
            <label className="text-[11px] text-dim">
              To
              <Input
                type="date"
                value={form.to_date}
                onChange={(e) => set("to_date", e.target.value)}
                className="mt-1 bg-bg-1 border-line"
                data-testid="option-warehouse-to-date"
              />
            </label>
            <label className="text-[11px] text-dim">
              Expiry
              <select
                value={form.expiry_policy}
                onChange={(e) => set("expiry_policy", e.target.value)}
                className="mt-1 h-9 w-full rounded-md border border-input bg-bg-1 px-3 py-1 text-sm text-foreground"
                data-testid="option-warehouse-expiry"
              >
                <option value="next_available">Next available</option>
                <option value="fixed">Fixed date</option>
              </select>
            </label>
            <label className="text-[11px] text-dim">
              Sample
              <Input
                type="number"
                min="1"
                max="375"
                value={form.sample_interval_minutes}
                onChange={(e) => set("sample_interval_minutes", e.target.value)}
                className="mt-1 bg-bg-1 border-line text-right"
                data-testid="option-warehouse-sample-minutes"
              />
            </label>
            <label className="text-[11px] text-dim">
              Chunk
              <Input
                type="number"
                min="1"
                max="30"
                placeholder="Auto"
                value={form.chunk_days}
                onChange={(e) => set("chunk_days", e.target.value)}
                className="mt-1 bg-bg-1 border-line text-right"
                data-testid="option-warehouse-chunk-days"
              />
            </label>
            <Button
              size="sm"
              onClick={onPreview}
              disabled={planning || fixedExpiryMissing}
              className="h-9 text-xs bg-bg-3 border border-line hover:bg-bg-2"
              data-testid="option-warehouse-preview-button"
            >
              <RefreshCw className="w-3 h-3 mr-1" />
              {planning ? "Planning..." : fixedExpiryMissing ? "Set Expiry" : "Preview"}
            </Button>
          </div>

          {form.expiry_policy === "fixed" && (
            <div className="mt-2 max-w-xs">
              <label className="text-[11px] text-dim">
                Fixed expiry date
                <Input
                  type="date"
                  value={form.fixed_expiry_date}
                  onChange={(e) => set("fixed_expiry_date", e.target.value)}
                  className="mt-1 bg-bg-1 border-line"
                  data-testid="option-warehouse-fixed-expiry-date"
                />
              </label>
            </div>
          )}
          <div className="mt-3 grid grid-cols-1 lg:grid-cols-3 gap-2 text-[11px] text-dim" data-testid="option-warehouse-help">
            <div className="rounded-md border border-line bg-bg-1 p-2">
              <span className="font-semibold text-foreground">Expiry:</span> Next available uses stored contract expiries on or after each sampled spot date. Fixed date forces one expiry across the whole window.
            </div>
            <div className="rounded-md border border-line bg-bg-1 p-2">
              <span className="font-semibold text-foreground">Sample:</span> Sample every N minutes to choose strikes. Use 15 for quick planning; Use 1 for final strategy prep when accuracy matters.
            </div>
            <div className="rounded-md border border-line bg-bg-1 p-2">
              <span className="font-semibold text-foreground">History:</span> Historical expiry changes are handled only when those old contracts are stored locally; missing contract metadata means sync/backfill first.
            </div>
          </div>
          <div className="mt-2 rounded-md border border-line bg-bg-1 p-2 text-[11px] text-dim" data-testid="option-warehouse-background-help">
            Preview is the trust check for planner-selected moneyness. Fetch runs in background and only requests missing selected dates; the raw option audit below is a broader warehouse diagnostic.
          </div>
        </div>

        <div className="grid grid-cols-1 xl:grid-cols-[minmax(0,1.5fr)_minmax(0,1fr)] gap-3">
          <div className="rounded-md border border-line bg-bg-2 p-3 space-y-3">
            <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
              <div>
                <div className="text-[10px] uppercase tracking-wider text-dimmer mb-1">Moneyness</div>
                <div className="flex flex-wrap gap-1.5">
                  {MONEYNESS_OPTIONS.map((label) => {
                    const active = form.moneyness.includes(label);
                    return (
                      <Button
                        key={label}
                        type="button"
                        size="sm"
                        variant="secondary"
                        onClick={() => toggleListValue("moneyness", label, MONEYNESS_OPTIONS)}
                        className={`h-7 px-2 text-xs border ${active ? "border-info bg-bg-3 text-foreground" : "border-line bg-bg-1 text-dim"}`}
                        data-testid={`option-warehouse-moneyness-${label}`}
                      >
                        {label.toUpperCase()}
                      </Button>
                    );
                  })}
                </div>
              </div>
              <div>
                <div className="text-[10px] uppercase tracking-wider text-dimmer mb-1">Legs</div>
                <div className="flex flex-wrap gap-1.5">
                  {LEG_OPTIONS.map((label) => {
                    const active = form.legs.includes(label);
                    return (
                      <Button
                        key={label}
                        type="button"
                        size="sm"
                        variant="secondary"
                        onClick={() => toggleListValue("legs", label, LEG_OPTIONS)}
                        className={`h-7 px-3 text-xs border ${active ? "border-info bg-bg-3 text-foreground" : "border-line bg-bg-1 text-dim"}`}
                        data-testid={`option-warehouse-leg-${label.toLowerCase()}`}
                      >
                        {label}
                      </Button>
                    );
                  })}
                </div>
              </div>
            </div>

            <div className="grid grid-cols-[1fr_0.8fr_auto] gap-2 items-end">
              <label className="text-[11px] text-dim flex items-center gap-2 h-9">
                <input
                  type="checkbox"
                  checked={form.fetch_missing_only}
                  onChange={(e) => set("fetch_missing_only", e.target.checked)}
                  className="h-4 w-4"
                  data-testid="option-warehouse-missing-only"
                />
                Missing only
              </label>
              <label className="text-[11px] text-dim">
                Max contracts
                <Input
                  type="number"
                  min="1"
                  max="500"
                  value={form.max_contracts}
                  onChange={(e) => set("max_contracts", e.target.value)}
                  className="mt-1 bg-bg-1 border-line text-right"
                  data-testid="option-warehouse-max-contracts"
                />
              </label>
              <Button
                size="sm"
                onClick={onFetch}
                disabled={fetching || !connected || !plan || items.length === 0 || fetchBlockedByMax || fetchNothingToDo || fixedExpiryMissing}
                className="h-9 text-xs bg-bg-3 border border-line hover:bg-bg-2"
                data-testid="option-warehouse-fetch-button"
              >
                <Download className="w-3 h-3 mr-1" />
                {fetching ? "Fetching..." : fixedExpiryMissing ? "Set Expiry" : fetchBlockedByMax ? "Raise Max" : fetchNothingToDo ? "Complete" : "Fetch Missing"}
              </Button>
            </div>
          </div>

          <div className="rounded-md border border-line bg-bg-2 p-3" data-testid="option-warehouse-chunk-guidance">
            <div className="text-[10px] uppercase tracking-wider text-dimmer">Chunk guidance</div>
            {guidance ? (
              <div className="mt-2 grid grid-cols-2 gap-2 text-xs">
                <div>
                  <div className="text-dimmer">Mode</div>
                  <div className="font-mono">{guidance.mode}</div>
                </div>
                <div>
                  <div className="text-dimmer">Days/call</div>
                  <div className="font-mono">{guidance.chunk_days}</div>
                </div>
                <div>
                  <div className="text-dimmer">API calls</div>
                  <div className="font-mono">{fmtInt(guidance.estimated_api_calls || 0)}</div>
                </div>
                <div>
                  <div className="text-dimmer">Contracts</div>
                  <div className="font-mono">{fmtInt(guidance.contracts || 0)}</div>
                </div>
              </div>
            ) : (
              <div className="mt-2 text-[11px] text-dim">
                Auto picks smaller date chunks as the contract count grows. Use manual 1-3 for very large runs; 7 is fine for small previews.
              </div>
            )}
          </div>
        </div>

        {fetchJob && (
          <div className="rounded-md border border-line bg-bg-2 p-3" data-testid="option-warehouse-fetch-progress">
            <div className="flex items-center justify-between gap-2 text-xs">
              <span className="font-mono uppercase">{fetchJob.status}</span>
              <span className="font-mono text-dimmer">{fmtNum(fetchJob.progress_pct || 0, 1)}%</span>
            </div>
            <div className="mt-2 h-2 rounded bg-bg-3 overflow-hidden">
              <div
                className="h-full bg-info transition-all"
                style={{ width: `${Math.min(100, Math.max(0, Number(fetchJob.progress_pct || 0)))}%` }}
              />
            </div>
            <div className="mt-2 grid grid-cols-2 lg:grid-cols-5 gap-2 text-[11px] text-dim">
              <div>
                <span className="text-dimmer">Tasks</span>
                <span className="ml-1 font-mono">{fmtInt(fetchJob.completed_tasks || 0)}/{fmtInt(fetchJob.total_tasks || 0)}</span>
              </div>
              <div>
                <span className="text-dimmer">Contracts</span>
                <span className="ml-1 font-mono">{fmtInt(fetchJob.fetch_contracts || 0)}</span>
              </div>
              <div>
                <span className="text-dimmer">Fetched</span>
                <span className="ml-1 font-mono">{fmtInt(fetchJob.total_fetched || 0)}</span>
              </div>
              <div>
                <span className="text-dimmer">Added</span>
                <span className="ml-1 font-mono">{fmtInt(fetchJob.candles_added || 0)}</span>
              </div>
              <div>
                <span className="text-dimmer">Matched</span>
                <span className="ml-1 font-mono">{fmtInt(fetchJob.matched_existing || 0)}</span>
              </div>
            </div>
            {Boolean(fetchJob.failed?.length) && (
              <div className="mt-2 text-[11px] text-danger">
                Failed tasks: {fmtInt(fetchJob.failed.length)}. Lower Chunk to 1-2 or retry the same preview.
              </div>
            )}
          </div>
        )}

        {plan && (
          <div className="space-y-3" data-testid="option-warehouse-plan-summary">
            <div className="grid grid-cols-2 lg:grid-cols-6 gap-2">
              <AuditStat label="Spot used" value={fmtInt(summary.spot_candles_used || 0)} />
              <AuditStat label="Contracts" value={fmtInt(summary.planned_contracts || 0)} />
              <AuditStat label="Need fetch" value={fmtInt(summary.missing_data_contracts || 0)} />
              <AuditStat label="Coverage" value={`${plannedCoverage}%`} />
              <AuditStat label="Missing meta" value={fmtInt(missingMetaCount)} />
              <AuditStat label="Selections" value={fmtInt(summary.selection_count || 0)} />
            </div>

            <div className={`rounded-md border p-3 text-xs ${missingCount === 0 && missingMetaCount === 0 && items.length > 0 ? "border-emerald-900 bg-emerald-950/30 text-emerald-100" : "border-amber-900 bg-amber-950/30 text-amber-100"}`} data-testid="option-warehouse-planned-coverage">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <span className="font-semibold">Planned coverage</span>
                <span className="font-mono">{plannedCoverage}% · {fmtInt(summary.stored_selected_date_candles || 0)}/{fmtInt(summary.expected_candles_per_selected_dates || 0)} candles</span>
              </div>
              <div className="mt-1 text-[11px] opacity-90">
                {missingCount === 0 && missingMetaCount === 0 && items.length > 0
                  ? "This planner-selected moneyness window is locally covered."
                  : `${fmtInt(missingCount)} contracts still need option candles across ${fmtInt(fetchDateCount)} selected dates; ${fmtInt(missingMetaCount)} selections need contract metadata.`}
              </div>
            </div>

            {plan.warning && (
              <div className="rounded-md border border-amber-900 bg-amber-950/40 p-2 text-xs text-amber-100">
                {plan.warning}
              </div>
            )}

            {fetchResult && (
              <div className="rounded-md border border-line bg-bg-2 p-3 text-xs" data-testid="option-warehouse-fetch-summary">
                <span className="font-mono">{fetchResult.status}</span>
                <span className="text-dim ml-2">
                  {fmtInt(fetchResult.fetch_contracts || 0)} contracts · {fmtInt(fetchResult.total_fetched || 0)} candles · +{fmtInt(fetchResult.candles_added || 0)} / ~{fmtInt(fetchResult.candles_updated || 0)}
                </span>
              </div>
            )}

            <div className="overflow-x-auto rounded-md border border-line">
              <table className="w-full text-xs" data-testid="option-warehouse-plan-table">
                <thead>
                  <tr className="text-dim border-b border-line bg-bg-2">
                    <th className="text-left p-2">Symbol</th>
                    <th className="text-left p-2">Expiry</th>
                    <th className="text-right p-2">Strike</th>
                    <th className="text-left p-2">Side</th>
                    <th className="text-left p-2">Selected</th>
                    <th className="text-right p-2">Dates</th>
                    <th className="text-right p-2">Need</th>
                    <th className="text-right p-2">Coverage</th>
                  </tr>
                </thead>
                <tbody>
                  {items.slice(0, 80).map((item) => (
                    <tr key={item.instrument_key} className="border-b border-line" data-testid="option-warehouse-plan-row">
                      <td className="p-2 font-mono">{item.trading_symbol || item.instrument_key}</td>
                      <td className="p-2 font-mono text-dim">{item.expiry_date}</td>
                      <td className="p-2 font-mono text-right">{item.strike}</td>
                      <td className="p-2 font-mono">{item.side}</td>
                      <td className="p-2 text-dim">{item.selected_as}</td>
                      <td className="p-2 font-mono text-right">{fmtInt(item.selected_date_count || 0)}</td>
                      <td className="p-2 font-mono text-right">{item.needs_fetch ? fmtInt(item.fetch_date_count || 0) : "no"}</td>
                      <td className="p-2 font-mono text-right">{item.coverage_pct || 0}%</td>
                    </tr>
                  ))}
                  {items.length === 0 && (
                    <tr><td colSpan="8" className="p-4 text-center text-dimmer">No option contracts selected</td></tr>
                  )}
                </tbody>
              </table>
            </div>

            {items.length > 80 && (
              <div className="text-[11px] text-dimmer">Showing first 80 of {fmtInt(items.length)} planned contracts.</div>
            )}
            {missingCount === 0 && items.length > 0 && (
              <div className="text-[11px] text-dim">Stored option data covers the planner-selected window.</div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

function OptionAuditPanel({ form, setForm, result, loading, clearing, onAudit, onClear }) {
  const set = (key, value) => setForm((prev) => ({ ...prev, [key]: value }));
  const summary = result?.summary;
  const items = result?.items || [];
  const statusClass = (status) => {
    if (status === "ok") return "bg-emerald-950 text-emerald-200 border-emerald-900";
    if (status === "missing") return "bg-rose-950 text-rose-200 border-rose-900";
    if (status === "incomplete") return "bg-amber-950 text-amber-200 border-amber-900";
    return "bg-bg-3 text-dim border-line";
  };

  return (
    <div className="rounded-lg border border-line bg-bg-1" data-testid="option-audit-panel">
      <div className="px-3 py-2 border-b border-line flex items-center gap-2">
        <ShieldCheck className="w-4 h-4 text-info" />
        <div className="text-xs font-semibold uppercase tracking-wider text-dim">Raw Option Universe Audit</div>
        {summary && (
          <span className={`ml-auto text-[10px] px-1.5 py-0.5 rounded border font-mono ${summary.complete ? "bg-emerald-950 text-emerald-200 border-emerald-900" : "bg-amber-950 text-amber-200 border-amber-900"}`}>
            {summary.complete ? "trusted" : "needs review"}
          </span>
        )}
      </div>
      <div className="p-3 space-y-3">
        <div className="rounded-md border border-line bg-bg-2 p-2 text-[11px] text-dim" data-testid="option-audit-scope-note">
          Raw universe audit checks the stored contract metadata slice selected by expiry, side, and max contracts. It does not prove planner-selected ATM/OTM/ITM coverage; use Option Data Planner Planned coverage for that.
        </div>
        <div className="grid grid-cols-2 xl:grid-cols-[1fr_1fr_1fr_0.8fr_0.7fr_0.8fr_auto_auto] gap-2 items-end rounded-md border border-line bg-bg-2 p-3">
          <label className="text-[11px] text-dim">
            Underlying
            <select
              value={form.underlying}
              onChange={(e) => set("underlying", e.target.value)}
              className="mt-1 h-9 w-full rounded-md border border-input bg-bg-1 px-3 py-1 text-sm text-foreground"
              data-testid="option-audit-underlying"
            >
              {INSTRUMENTS.map((inst) => <option key={inst} value={inst}>{inst}</option>)}
            </select>
          </label>
          <label className="text-[11px] text-dim">
            From
            <Input
              type="date"
              value={form.from_date}
              onChange={(e) => set("from_date", e.target.value)}
              className="mt-1 bg-bg-1 border-line"
              data-testid="option-audit-from-date"
            />
          </label>
          <label className="text-[11px] text-dim">
            To
            <Input
              type="date"
              value={form.to_date}
              onChange={(e) => set("to_date", e.target.value)}
              className="mt-1 bg-bg-1 border-line"
              data-testid="option-audit-to-date"
            />
          </label>
          <label className="text-[11px] text-dim">
            Expiry
            <Input
              type="date"
              value={form.expiry}
              onChange={(e) => set("expiry", e.target.value)}
              className="mt-1 bg-bg-1 border-line"
              data-testid="option-audit-expiry"
            />
          </label>
          <label className="text-[11px] text-dim">
            Side
            <select
              value={form.side}
              onChange={(e) => set("side", e.target.value)}
              className="mt-1 h-9 w-full rounded-md border border-input bg-bg-1 px-3 py-1 text-sm text-foreground"
              data-testid="option-audit-side"
            >
              <option value="">All</option>
              <option value="CE">CE</option>
              <option value="PE">PE</option>
            </select>
          </label>
          <label className="text-[11px] text-dim">
            Max contracts
            <Input
              type="number"
              min="1"
              max="5000"
              value={form.limit_contracts}
              onChange={(e) => set("limit_contracts", e.target.value)}
              className="mt-1 bg-bg-1 border-line"
              data-testid="option-audit-limit"
            />
          </label>
          <Button
            size="sm"
            onClick={onAudit}
            disabled={loading}
            className="h-9 text-xs bg-bg-3 border border-line hover:bg-bg-2"
            data-testid="option-audit-button"
          >
            <ShieldCheck className="w-3 h-3 mr-1" />
            {loading ? "Auditing..." : "Audit"}
          </Button>
          <Button
            size="sm"
            variant="secondary"
            onClick={onClear}
            disabled={clearing}
            className="h-9 text-xs border border-rose-900 bg-rose-950/60 text-rose-100 hover:bg-rose-950"
            data-testid="option-clear-button"
          >
            <Trash2 className="w-3 h-3 mr-1" />
            {clearing ? "Clearing..." : "Clear"}
          </Button>
        </div>

        {summary && (
          <div className="grid grid-cols-2 lg:grid-cols-6 gap-2" data-testid="option-audit-summary">
            <AuditStat label="Complete" value={`${fmtInt(summary.complete_contracts)}/${fmtInt(summary.contracts_checked)}`} />
            <AuditStat label="Coverage" value={`${summary.coverage_pct || 0}%`} />
            <AuditStat label="Missing" value={fmtInt(summary.contracts_with_missing_days)} />
            <AuditStat label="Incomplete" value={fmtInt(summary.contracts_with_incomplete_days)} />
            <AuditStat label="Candles" value={`${fmtInt(summary.stored_candles)}/${fmtInt(summary.expected_candles)}`} />
            <AuditStat label="Days" value={fmtInt(summary.expected_days)} />
          </div>
        )}

        {items.length > 0 && (
          <div className="overflow-x-auto rounded-md border border-line" data-testid="option-audit-table">
            <table className="w-full text-xs">
              <thead className="bg-bg-2 text-dim">
                <tr>
                  <th className="p-2 text-left">Contract</th>
                  <th className="p-2 text-left">Expiry</th>
                  <th className="p-2 text-right">Strike</th>
                  <th className="p-2 text-left">Side</th>
                  <th className="p-2 text-right">Coverage</th>
                  <th className="p-2 text-right">Missing</th>
                  <th className="p-2 text-right">Incomplete</th>
                  <th className="p-2 text-left">Status</th>
                </tr>
              </thead>
              <tbody>
                {items.slice(0, 80).map((item) => (
                  <tr key={item.instrument_key} className="border-b border-line">
                    <td className="p-2 font-mono">{item.trading_symbol || item.instrument_key}</td>
                    <td className="p-2 font-mono text-dim">{item.expiry_date}</td>
                    <td className="p-2 font-mono text-right">{item.strike}</td>
                    <td className="p-2 font-mono">{item.side}</td>
                    <td className="p-2 font-mono text-right">{item.coverage_pct || 0}%</td>
                    <td className="p-2 font-mono text-right">{fmtInt(item.missing_days || 0)}</td>
                    <td className="p-2 font-mono text-right">{fmtInt(item.incomplete_days || 0)}</td>
                    <td className="p-2">
                      <span className={`text-[10px] px-1.5 py-0.5 rounded border font-mono ${statusClass(item.status)}`}>
                        {item.status}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        {summary && items.length === 0 && (
          <div className="rounded-md border border-line bg-bg-2 p-3 text-xs text-dim">
            No option contracts found for this filter. Sync current or expired contracts before auditing option candle coverage.
          </div>
        )}
      </div>
    </div>
  );
}

function DataTrustPanel({
  auditForm,
  setAuditForm,
  auditResult,
  auditLoading,
  onAudit,
  clearInstrument,
  setClearInstrument,
  clearing,
  onClear,
}) {
  const set = (key, value) => setAuditForm((prev) => ({ ...prev, [key]: value }));
  const summary = auditResult?.summary;
  const days = auditResult?.days || [];
  const statusClass = (status) => {
    if (status === "ok") return "bg-emerald-950 text-emerald-200 border-emerald-900";
    if (status === "missing" || status === "hash_mismatch") return "bg-rose-950 text-rose-200 border-rose-900";
    if (status === "incomplete" || status === "unverified") return "bg-amber-950 text-amber-200 border-amber-900";
    return "bg-bg-3 text-dim border-line";
  };

  return (
    <div className="rounded-lg border border-line bg-bg-1" data-testid="warehouse-audit-panel">
      <div className="px-3 py-2 border-b border-line flex items-center gap-2">
        <ShieldCheck className="w-4 h-4 text-info" />
        <div className="text-xs font-semibold uppercase tracking-wider text-dim">Data Trust Audit</div>
        {summary && (
          <span className={`ml-auto text-[10px] px-1.5 py-0.5 rounded border font-mono ${summary.complete ? "bg-emerald-950 text-emerald-200 border-emerald-900" : "bg-amber-950 text-amber-200 border-amber-900"}`}>
            {summary.complete ? "trusted" : "needs review"}
          </span>
        )}
      </div>
      <div className="p-3 space-y-3">
        <div className="grid grid-cols-1 xl:grid-cols-[minmax(0,2fr)_minmax(260px,1fr)] gap-3">
          <div className="rounded-md border border-line bg-bg-2 p-3">
            <div className="grid grid-cols-2 lg:grid-cols-[1fr_1fr_1fr_auto] gap-2 items-end">
              <label className="text-[11px] text-dim">
                Instrument
                <select
                  value={auditForm.instrument}
                  onChange={(e) => set("instrument", e.target.value)}
                  className="mt-1 h-9 w-full rounded-md border border-input bg-bg-1 px-3 py-1 text-sm text-foreground"
                  data-testid="warehouse-audit-instrument"
                >
                  {INSTRUMENTS.map((inst) => <option key={inst} value={inst}>{inst}</option>)}
                </select>
              </label>
              <label className="text-[11px] text-dim">
                From
                <Input
                  type="date"
                  value={auditForm.from_date}
                  onChange={(e) => set("from_date", e.target.value)}
                  className="mt-1 bg-bg-1 border-line"
                  data-testid="warehouse-audit-from-date"
                />
              </label>
              <label className="text-[11px] text-dim">
                To
                <Input
                  type="date"
                  value={auditForm.to_date}
                  onChange={(e) => set("to_date", e.target.value)}
                  className="mt-1 bg-bg-1 border-line"
                  data-testid="warehouse-audit-to-date"
                />
              </label>
              <Button
                size="sm"
                onClick={onAudit}
                disabled={auditLoading}
                className="h-9 text-xs bg-bg-3 border border-line hover:bg-bg-2"
                data-testid="warehouse-audit-button"
              >
                <ShieldCheck className="w-3 h-3 mr-1" />
                {auditLoading ? "Auditing..." : "Audit"}
              </Button>
            </div>
          </div>

          <div className="rounded-md border border-line bg-bg-2 p-3">
            <div className="grid grid-cols-[1fr_auto] gap-2 items-end">
              <label className="text-[11px] text-dim">
                Developer clear
                <select
                  value={clearInstrument}
                  onChange={(e) => setClearInstrument(e.target.value)}
                  className="mt-1 h-9 w-full rounded-md border border-input bg-bg-1 px-3 py-1 text-sm text-foreground"
                  data-testid="warehouse-clear-instrument"
                >
                  <option value="ALL">ALL</option>
                  {INSTRUMENTS.map((inst) => <option key={inst} value={inst}>{inst}</option>)}
                </select>
              </label>
              <Button
                size="sm"
                variant="secondary"
                onClick={onClear}
                disabled={clearing}
                className="h-9 text-xs border border-rose-900 bg-rose-950/60 text-rose-100 hover:bg-rose-950"
                data-testid="warehouse-clear-button"
              >
                <Trash2 className="w-3 h-3 mr-1" />
                {clearing ? "Clearing..." : "Clear"}
              </Button>
            </div>
          </div>
        </div>

        {summary && (
          <div className="grid grid-cols-2 lg:grid-cols-6 gap-2">
            <AuditStat label="Complete" value={`${summary.complete_days}/${summary.expected_days}`} />
            <AuditStat label="Candles" value={`${fmtInt(summary.stored_candles)}/${fmtInt(summary.expected_candles)}`} />
            <AuditStat label="Missing" value={fmtInt(summary.missing_days)} />
            <AuditStat label="Incomplete" value={fmtInt(summary.incomplete_days)} />
            <AuditStat label="Hash mismatch" value={fmtInt(summary.hash_mismatch_days)} />
            <AuditStat label="Unverified" value={fmtInt(summary.unverified_days)} />
          </div>
        )}

        {days.length > 0 && (
          <div className="flex flex-wrap gap-1.5" data-testid="warehouse-audit-days">
            {days.slice(0, 60).map((day) => (
              <span
                key={day.date}
                className={`text-[10px] px-1.5 py-0.5 rounded border font-mono ${statusClass(day.status)}`}
                title={`${day.date}: ${day.stored_candles}/${day.expected_candles} candles · ${day.status}`}
              >
                {day.date.slice(5)} {day.status}
              </span>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

function AuditStat({ label, value }) {
  return (
    <div className="rounded-md border border-line bg-bg-2 p-2">
      <div className="text-[10px] uppercase tracking-wider text-dimmer">{label}</div>
      <div className="text-sm font-mono mt-0.5">{value}</div>
    </div>
  );
}

function CoverageHeatmap({ coverage }) {
  if (!coverage || Object.keys(coverage).length === 0) {
    return (
      <div className="rounded-lg border border-line bg-bg-1 p-6 text-center text-dimmer text-sm" data-testid="coverage-heatmap-empty">
        Coverage heatmap will appear here after you ingest data.
      </div>
    );
  }
  // Build unique date list across all instruments
  const dateSet = new Set();
  Object.values(coverage).forEach((c) => (c.days || []).forEach((d) => dateSet.add(d.date)));
  const dates = [...dateSet].sort();
  // Expected ~375 candles per Indian trading day (NSE 09:15-15:30 = 375 minutes)
  const TARGET = 375;
  return (
    <div className="rounded-lg border border-line bg-bg-1" data-testid="data-coverage-heatmap">
      <div className="px-3 py-2 border-b border-line">
        <div className="text-xs font-semibold uppercase tracking-wider text-dim">Coverage Heatmap (per trading day)</div>
      </div>
      <div className="p-3 overflow-x-auto">
        <table className="text-[10px] font-mono">
          <thead>
            <tr>
              <th className="text-left text-dim pr-2"></th>
              {dates.map((d) => (
                <th key={d} className="px-0.5 text-dim text-[9px] writing-mode-vertical-rl transform -rotate-90 origin-bottom-left h-12" style={{ minWidth: 18 }}>{d.slice(5)}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {Object.entries(coverage).map(([inst, c]) => {
              const dayMap = Object.fromEntries((c.days || []).map((d) => [d.date, d]));
              return (
                <tr key={inst}>
                  <td className="text-dim text-xs pr-2 py-1">{inst}</td>
                  {dates.map((d) => {
                    const day = dayMap[d];
                    if (!day) return <td key={d} className="px-0.5"><div className="w-4 h-4 bg-bg-3 rounded-sm border border-line" title={`${d}: no data`}></div></td>;
                    const pct = Math.min(100, Math.round((day.candle_count / TARGET) * 100));
                    const color = pct >= 90 ? "bg-emerald-600" : pct >= 50 ? "bg-amber-600" : "bg-rose-700";
                    return (
                      <td key={d} className="px-0.5">
                        <div className={`w-4 h-4 ${color} rounded-sm border border-line cursor-help`} title={`${inst} · ${d}: ${day.candle_count}/${TARGET} candles (${pct}%)\nhash ${day.hash}`}></div>
                      </td>
                    );
                  })}
                </tr>
              );
            })}
          </tbody>
        </table>
        <div className="mt-3 flex items-center gap-4 text-[10px] text-dimmer">
          <div className="flex items-center gap-1"><span className="w-3 h-3 bg-emerald-600 rounded-sm"></span>≥90% coverage</div>
          <div className="flex items-center gap-1"><span className="w-3 h-3 bg-amber-600 rounded-sm"></span>50–90%</div>
          <div className="flex items-center gap-1"><span className="w-3 h-3 bg-rose-700 rounded-sm"></span>&lt;50%</div>
          <div className="flex items-center gap-1"><span className="w-3 h-3 bg-bg-3 rounded-sm border border-line"></span>no data</div>
        </div>
      </div>
    </div>
  );
}

function OptionCoverageHeatmap({ coverage }) {
  if (!coverage || Object.keys(coverage).length === 0) {
    return (
      <div className="rounded-lg border border-line bg-bg-1 p-6 text-center text-dimmer text-sm" data-testid="option-coverage-heatmap-empty">
        No option candles stored yet.
      </div>
    );
  }

  const dateSet = new Set();
  Object.values(coverage).forEach((c) => (c.days || []).forEach((d) => dateSet.add(d.date)));
  const dates = Array.from(dateSet).sort();

  return (
    <div className="rounded-lg border border-line bg-bg-1" data-testid="option-coverage-heatmap">
      <div className="px-3 py-2 border-b border-line flex items-center gap-2">
        <Database className="w-4 h-4 text-info" />
        <div className="text-xs font-semibold uppercase tracking-wider text-dim">Option Coverage Heatmap</div>
        <span className="ml-auto text-[10px] text-dimmer font-mono">stored candles by date</span>
      </div>
      <div className="p-3 overflow-x-auto">
        <table className="w-full text-xs">
          <thead>
            <tr className="text-dim border-b border-line">
              <th className="text-left p-2 sticky left-0 bg-bg-1 z-10">Underlying</th>
              {dates.map((d) => (
                <th key={d} className="p-1 text-center font-mono whitespace-nowrap">{d.slice(5)}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {Object.entries(coverage).map(([inst, c]) => {
              const byDate = Object.fromEntries((c.days || []).map((d) => [d.date, d]));
              return (
                <tr key={inst} className="border-b border-line">
                  <td className="p-2 sticky left-0 bg-bg-1 z-10">
                    <div className="font-semibold">{inst}</div>
                    <div className="text-[10px] text-dimmer font-mono">
                      {fmtInt(c.total_candles || 0)} candles · {fmtInt(c.contract_count || 0)} contracts
                    </div>
                    <div className="text-[10px] text-dimmer font-mono">
                      {c.first_date || "n/a"} → {c.last_date || "n/a"}
                    </div>
                  </td>
                  {dates.map((d) => {
                    const day = byDate[d];
                    const pct = Number(day?.coverage_pct || 0);
                    const cls = pct >= 95 ? "bg-emerald-600" : pct >= 50 ? "bg-amber-500" : day ? "bg-rose-700" : "bg-bg-3";
                    return (
                      <td key={d} className="p-1">
                        <div
                          className={`w-6 h-6 rounded-sm ${cls} border border-line`}
                          title={day ? `${inst} ${d}: ${fmtInt(day.candles)} candles, ${fmtInt(day.contracts)} contracts, ${pct}% stored-contract coverage` : `${inst} ${d}: no option candles`}
                        />
                      </td>
                    );
                  })}
                </tr>
              );
            })}
          </tbody>
        </table>
        <div className="mt-3 flex flex-wrap gap-3 text-[11px] text-dim">
          <div className="flex items-center gap-1"><span className="w-3 h-3 bg-emerald-600 rounded-sm"></span>≥95% for stored contracts</div>
          <div className="flex items-center gap-1"><span className="w-3 h-3 bg-amber-500 rounded-sm"></span>partial stored-contract day</div>
          <div className="flex items-center gap-1"><span className="w-3 h-3 bg-rose-700 rounded-sm"></span>low stored-contract coverage</div>
          <div className="flex items-center gap-1"><span className="w-3 h-3 bg-bg-3 border border-line rounded-sm"></span>no stored option candles</div>
        </div>
        <div className="mt-2 text-[11px] text-dimmer">
          This heatmap shows what is stored in `options_1m`. Use Option Data Planner Planned coverage to verify a specific ATM/OTM/ITM download plan.
        </div>
      </div>
    </div>
  );
}
