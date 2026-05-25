import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Database, Download, RefreshCw, CheckCircle2, AlertCircle } from "lucide-react";
import { fmtInt, isoToFull } from "@/lib/fmt";
import { Skeleton } from "@/components/ui/skeleton";

const INSTRUMENTS = ["NIFTY", "BANKNIFTY", "SENSEX"];

export default function DataWarehouse() {
  const [coverage, setCoverage] = useState(null);
  const [runs, setRuns] = useState([]);
  const [ingesting, setIngesting] = useState({});
  const [loading, setLoading] = useState(true);

  const refresh = async () => {
    try {
      const [cov, r] = await Promise.all([api.coverage(), api.warehouseRuns(20)]);
      setCoverage(cov.instruments || {});
      setRuns(r.items || []);
    } catch (e) {
      toast.error("Failed to load warehouse status");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { refresh(); }, []);

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
