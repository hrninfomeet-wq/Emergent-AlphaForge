import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "@/lib/api";
import { fmtNum, isoToFull } from "@/lib/fmt";
import { Button } from "@/components/ui/button";
import {
  ShieldCheck, RefreshCw, CheckCircle2, AlertTriangle, Activity, Clock, Database,
} from "lucide-react";

/**
 * Warehouse-health banner (quality-hardening Slice A item 2).
 *
 * One strip answering "can I trust today's data?": last auto-update result +
 * time, per-index daily ATM-band coverage, live-stream running/stale, and the
 * OAuth token countdown. Green only when everything is verified/running; amber
 * otherwise.
 *
 * The band-coverage plan (POST /data-hygiene/plan) costs ~5s, so it is NOT run
 * on mount — the user clicks Check, and the result is cached for the browser
 * session (module-level) so navigating away and back does not re-fetch. The
 * cheap status calls (auto-update / stream / token) run on mount and refresh.
 */

// Session-level cache for the expensive plan, keyed nowhere (single warehouse).
let PLAN_CACHE = null;

const STREAM_STALE_MS = 3 * 60 * 1000; // a running stream with no tick in 3m is stale

function coverageTone(pct) {
  if (pct == null) return "text-dim";
  if (pct >= 99.0) return "text-emerald-300";
  if (pct >= 95.0) return "text-warning";
  return "text-rose-300";
}

export default function WarehouseHealthBanner() {
  const [auto, setAuto] = useState(null);
  const [stream, setStream] = useState(null);
  const [token, setToken] = useState(null);
  const [plan, setPlan] = useState(PLAN_CACHE);
  const [checking, setChecking] = useState(false);
  const [now, setNow] = useState(Date.now());
  const aliveRef = useRef(true);

  const loadStatuses = async () => {
    const [a, s, t] = await Promise.allSettled([
      api.autoUpdateStatus(),
      api.upstoxStreamStatus(),
      api.upstoxStatus(),
    ]);
    if (!aliveRef.current) return;
    if (a.status === "fulfilled") setAuto(a.value);
    if (s.status === "fulfilled") setStream(s.value);
    if (t.status === "fulfilled") setToken(t.value);
  };

  useEffect(() => {
    aliveRef.current = true;
    loadStatuses();
    const poll = setInterval(loadStatuses, 60000);
    const tick = setInterval(() => setNow(Date.now()), 30000);
    return () => { aliveRef.current = false; clearInterval(poll); clearInterval(tick); };
  }, []);

  const runCheck = async () => {
    setChecking(true);
    try {
      const res = await api.dataHygienePlan();
      PLAN_CACHE = res;
      setPlan(res);
    } catch {
      // leave any prior cached plan in place
    } finally {
      if (aliveRef.current) setChecking(false);
    }
  };

  // --- Derived health signals -------------------------------------------------
  const autoOk = auto?.last_status === "ok" || auto?.last_status === "skipped";

  const lastTickMs = stream?.last_tick_at ? Date.parse(stream.last_tick_at) : NaN;
  const streamRunning = !!stream?.running;
  const streamStale = streamRunning && (Number.isNaN(lastTickMs) || now - lastTickMs > STREAM_STALE_MS);
  const streamOk = streamRunning && !streamStale;

  const expMs = token?.expires_at ? Date.parse(token.expires_at) : NaN;
  const tokenMins = Number.isNaN(expMs) ? -1 : Math.floor((expMs - now) / 60000);
  const tokenExpired = !token?.connected || token?.expired || tokenMins <= 0;
  const tokenLabel = tokenExpired
    ? (token?.connected ? "token expired" : "not connected")
    : tokenMins >= 60
      ? `${Math.floor(tokenMins / 60)}h ${tokenMins % 60}m left`
      : `${tokenMins}m left`;
  const tokenOk = !tokenExpired;

  const indices = plan?.instruments || [];
  const bandPcts = indices.map((i) => i.option_candles?.coverage_pct).filter((v) => v != null);
  const minBand = bandPcts.length ? Math.min(...bandPcts) : null;
  const bandOk = plan ? (minBand != null && minBand >= 99.0) : null;

  // Overall: green only when every verified signal we have is good. If the band
  // has not been checked this session, it cannot be confirmed green.
  const allVerified = autoOk && streamOk && tokenOk && bandOk === true;
  const accent = allVerified ? "border-l-emerald-500" : "border-l-amber-500";

  return (
    <div className={`rounded-lg border border-line bg-bg-1 border-l-2 ${accent}`} data-testid="warehouse-health-banner">
      <div className="px-3 py-2 border-b border-line flex items-center gap-2">
        <ShieldCheck className={`w-4 h-4 ${allVerified ? "text-emerald-400" : "text-warning"}`} />
        <div className="text-xs font-semibold uppercase tracking-wider text-dim">Warehouse Health</div>
        <span
          className={`text-[10px] px-2 py-0.5 rounded-full border font-mono ${allVerified ? "border-emerald-500/30 bg-emerald-500/10 text-emerald-300" : "border-amber-500/30 bg-amber-500/10 text-warning"}`}
          data-testid="warehouse-health-overall"
        >
          {allVerified ? "trustworthy" : "check needed"}
        </span>
        <Button
          size="sm"
          variant="secondary"
          onClick={runCheck}
          disabled={checking}
          className="ml-auto h-7 text-xs"
          data-testid="warehouse-health-check"
        >
          <RefreshCw className={`w-3 h-3 mr-1 ${checking ? "animate-spin" : ""}`} />
          {checking ? "Checking…" : plan ? "Re-check band" : "Check band coverage"}
        </Button>
        <Link to="/warehouse" className="text-xs text-info hover:underline" data-testid="warehouse-health-link">
          Warehouse
        </Link>
      </div>

      <div className="p-3 grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3 text-[11px]">
        {/* Auto-update */}
        <div className="rounded-md border border-line bg-bg-2 p-2.5" data-testid="health-auto-update">
          <div className="flex items-center gap-1.5 text-dimmer uppercase tracking-wider mb-1">
            <RefreshCw className="w-3 h-3" /> Auto-update
          </div>
          <div className="flex items-center gap-1.5">
            {autoOk ? <CheckCircle2 className="w-3.5 h-3.5 text-emerald-400" /> : <AlertTriangle className="w-3.5 h-3.5 text-warning" />}
            <span className={autoOk ? "text-emerald-300" : "text-warning"}>{auto?.last_status || "never run"}</span>
            {auto?.last_submitted_count ? <span className="text-dimmer">· {auto.last_submitted_count} jobs</span> : null}
          </div>
          <div className="text-dimmer font-mono mt-0.5">{auto?.last_finished_at ? isoToFull(auto.last_finished_at) : "—"}</div>
        </div>

        {/* Band coverage */}
        <div className="rounded-md border border-line bg-bg-2 p-2.5" data-testid="health-band-coverage">
          <div className="flex items-center gap-1.5 text-dimmer uppercase tracking-wider mb-1">
            <Database className="w-3 h-3" /> ATM-band coverage
          </div>
          {plan ? (
            <div className="space-y-0.5">
              {indices.map((i) => (
                <div key={i.instrument} className="flex items-center justify-between gap-2">
                  <span className="text-dim font-mono">{i.instrument}</span>
                  <span className={`font-mono ${coverageTone(i.option_candles?.coverage_pct)}`}>
                    {fmtNum(i.option_candles?.coverage_pct ?? 100, 1)}%
                    {(i.option_candles?.missing_pairs || 0) > 0 ? <span className="text-dimmer"> · {i.option_candles.missing_pairs} miss</span> : null}
                  </span>
                </div>
              ))}
            </div>
          ) : (
            <div className="text-dimmer">Not checked this session — costs ~5s.</div>
          )}
        </div>

        {/* Live stream */}
        <div className="rounded-md border border-line bg-bg-2 p-2.5" data-testid="health-stream">
          <div className="flex items-center gap-1.5 text-dimmer uppercase tracking-wider mb-1">
            <Activity className="w-3 h-3" /> Live stream
          </div>
          <div className="flex items-center gap-1.5">
            <span className={`w-2 h-2 rounded-full ${streamOk ? "bg-emerald-500" : streamRunning ? "bg-amber-500" : "bg-slate-500"}`} />
            <span className={streamOk ? "text-emerald-300" : streamRunning ? "text-warning" : "text-dim"}>
              {!streamRunning ? "stopped" : streamStale ? "stale" : "running"}
            </span>
            {streamRunning ? <span className="text-dimmer">· {stream?.instrument_count ?? 0} keys</span> : null}
          </div>
          <div className="text-dimmer font-mono mt-0.5">{stream?.last_tick_at ? `tick ${isoToFull(stream.last_tick_at)}` : "no ticks"}</div>
        </div>

        {/* OAuth token */}
        <div className="rounded-md border border-line bg-bg-2 p-2.5" data-testid="health-token">
          <div className="flex items-center gap-1.5 text-dimmer uppercase tracking-wider mb-1">
            <Clock className="w-3 h-3" /> Upstox token
          </div>
          <div className="flex items-center gap-1.5">
            {tokenOk ? <CheckCircle2 className="w-3.5 h-3.5 text-emerald-400" /> : <AlertTriangle className="w-3.5 h-3.5 text-rose-400" />}
            <span className={tokenOk ? "text-emerald-300" : "text-rose-300"}>{tokenLabel}</span>
          </div>
          <div className="text-dimmer font-mono mt-0.5">{token?.expires_at ? isoToFull(token.expires_at) : "—"}</div>
        </div>
      </div>
    </div>
  );
}
