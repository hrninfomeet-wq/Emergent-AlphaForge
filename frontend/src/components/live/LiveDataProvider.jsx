import { createContext, useCallback, useContext, useEffect, useMemo } from "react";
import { api } from "@/lib/api";
import { usePoll } from "@/hooks/usePoll";
import { useTickStream } from "@/hooks/useTickStream";

/**
 * LiveDataProvider — the SINGLE owner of all Live-Trading-page polling.
 *
 * Before this, the dashboard ran a bespoke 8-endpoint fetchAll@15s while four
 * children each self-polled (GuardPanel + PositionMonitor @3s, GttBook @6s,
 * LiveDeploymentStrip @10s) — guard-status was fetched TWICE (dashboard 15s +
 * GuardPanel 3s). This centralizes every poll at its correct cadence and fans the
 * data out via context, so each endpoint is fetched exactly once.
 *
 * Cadences (deliberately preserved — do NOT collapse): the 3s guard/session
 * cadence is a real-money exit-visibility property; the 15s broker cadence bounds
 * broker rate-limit/cost.
 *
 * Safety note: the UNGUARDED-positions banner is NOT derived from a guard×positions
 * client diff (that would desync — the guard drops a squared entry within ~1.5s
 * while positions lags up to 15s, flashing a false alert). Consumers derive it from
 * `reconcile.mismatches` (`unknown_broker_position`), which the server computes from
 * a fresh broker-book + guard-registry read in ONE call — so guard-status can be a
 * single 3s poll with no banner desync.
 */
const SLOW_MS = 15_000; // broker book / arm-state / blotter / deployments
const FAST_MS = 3_000; // software guard + the 10-min live session (exit visibility)
const GTT_MS = 6_000; // resting GTT/OCO backstop
const DEPLOY_MS = 10_000; // batched per-deployment live status
const ANALYSIS_MS = 10_000; // market analysis (server-cached ~8s, so this is cheap)
const HOLDINGS_MS = 30_000; // DP/demat holdings — changes slowly

const LiveDataContext = createContext(null);

export function useLiveData() {
  const ctx = useContext(LiveDataContext);
  if (ctx == null) {
    throw new Error("useLiveData must be used within <LiveDataProvider>");
  }
  return ctx;
}

export function LiveDataProvider({ children }) {
  // Each usePoll's `refetch` is stable (memoized), so destructuring it by name
  // gives the dep arrays below real, stable identifiers (no re-create churn).

  // ── Slow broker group (15s) — one poll per endpoint for independent error
  //    isolation (a single failing endpoint never blanks the others). ──────────
  const { data: status, error: eStatus, refetch: rStatus } = usePoll(() => api.flattradeStatus(), SLOW_MS);
  const { data: limits, error: eLimits, lastSuccess: lsLimits, refetch: rLimits } = usePoll(() => api.liveBrokerLimits(), SLOW_MS);
  // ── Tick-fresh position marks (SSE) ─────────────────────────────────────────
  // The money numbers (LTP, open P&L, day MTM) come from here, pushed on every
  // Upstox tick. The 15s /live-broker/positions poll below is DISABLED while the
  // stream is connected — running both would double position_book calls on a key
  // whose rate budget is shared with the Flattrade MCP. On stream loss the poll
  // resumes automatically and the page is exactly what it was before.
  const marks = useTickStream("/live-broker/marks/stream", {
    fallback: () => api.liveBrokerMarks(),
    fallbackMs: SLOW_MS,
  });
  // Gate on the marks path DELIVERING, not on it streaming. /live-broker/marks
  // returns the same book as /live-broker/positions (marked where a tick exists),
  // so whenever marks has data — over SSE or over its own 15s poll — the raw
  // positions poll is pure duplication. Gating on `source === "stream"` alone made
  // the degraded path fetch both books, which is measurably MORE broker traffic
  // than before the change. Only a marks path with nothing at all falls back.
  // `broker_stale` matters as much as `error` here. The marks endpoint returns
  // HTTP 200 while it is serving a last-good book, so gating on `error` alone let
  // a squared-off position render as open (2026-09-15) AND kept the honest
  // positions poll disabled behind it. A stale book is not a usable book.
  const marksUsable =
    marks.data != null && !marks.error && !marks.data.broker_stale;

  const { data: polledPositions, error: ePositions, lastSuccess: lsPolledPositions, refetch: rPositions } =
    usePoll(() => api.liveBrokerPositions(), SLOW_MS, { enabled: !marksUsable });
  // Marked rows ARE broker rows with lp/urmtom overwritten, so every consumer
  // (PositionsBlotter, RiskKpis, deriveDayPnl, isOpenPosition) works unchanged.
  const positions = marksUsable ? marks.data : polledPositions;
  const lsPositions = marksUsable ? marks.lastAt : lsPolledPositions;
  const { data: orders, error: eOrders, lastSuccess: lsOrders, refetch: rOrders } = usePoll(() => api.liveBrokerOrders(), SLOW_MS);
  const { data: reconcile, error: eReconcile, refetch: rReconcile } = usePoll(() => api.liveBrokerReconcile(), SLOW_MS);
  const { data: armState, error: eArmState, refetch: rArmState } = usePoll(() => api.getArmState(), SLOW_MS);
  const { data: blotter, error: eBlotter, refetch: rBlotter } = usePoll(() => api.getLiveBlotter(), SLOW_MS);
  const { data: greeks, error: eGreeks, refetch: rGreeks } = usePoll(() => api.getLiveGreeks(), SLOW_MS);
  const { data: deploymentsData, error: eDeployments, refetch: rDeployments } = usePoll(() => api.listDeployments({ limit: 200 }), SLOW_MS);

  // ── Fast group (3s) — software guard + the manual 10-min session. ────────────
  const { data: guard, error: eGuard, refetch: rGuard } = usePoll(() => api.getGuardStatus(), FAST_MS);
  const { data: session, error: eSession, refetch: rSession } = usePoll(() => api.getLiveTestSession(), FAST_MS);

  // ── GTT/OCO backstop (6s). ───────────────────────────────────────────────────
  const { data: gtt, error: eGtt, refetch: rGtt } = usePoll(() => api.listGtt(), GTT_MS);

  // ── Cockpit market intelligence + demat holdings (read-only, additive). ──────
  // marketAnalysis is server-cached ~8s so a 10s poll costs almost nothing; a
  // failure here is NON-money (it degrades the analysis panels to "—") and so is
  // deliberately kept OUT of the `health.degraded` money-slice set below.
  // Option chain / PCR / max pain / ATM straddle, pushed on the tick. Was a 10s
  // poll over an 8s server cache — up to ~18s stale on data that is derived from
  // the live tick map. The 10s poll remains as the automatic fallback.
  const analysisStream = useTickStream("/market/analysis/stream?instrument=NIFTY", {
    fallback: () => api.marketAnalysis("NIFTY"),
    fallbackMs: ANALYSIS_MS,
  });
  const { data: polledAnalysis, error: eMarketAnalysis, refetch: rMarketAnalysis } =
    usePoll(() => api.marketAnalysis("NIFTY"), ANALYSIS_MS,
            { enabled: analysisStream.data == null });
  const marketAnalysis = analysisStream.data ?? polledAnalysis;
  const { data: holdings, error: eHoldings, refetch: rHoldings } =
    usePoll(() => api.liveBrokerHoldings(), HOLDINGS_MS);

  // Non-archived deployments drive the strip rows AND the fan-out key set.
  const deployments = useMemo(
    () =>
      (deploymentsData?.items || []).filter(
        (d) => String(d?.status || "").toUpperCase() !== "ARCHIVED",
      ),
    [deploymentsData],
  );
  const depIds = useMemo(() => deployments.map((d) => d.id).filter(Boolean), [deployments]);
  const depIdsKey = depIds.join(",");

  // ── Batched per-deployment live status (10s) — ONE request for all ids. ──────
  const { data: deployLiveData, error: eDeployLive, refetch: rDeployLive } = usePoll(
    () => (depIds.length ? api.liveStatusBatch(depIds) : Promise.resolve({})),
    DEPLOY_MS,
  );

  // ── Live-feed health (10s) — Upstox stream → candle roller status. ───────────
  const { data: feedHealth, error: eFeedHealth, refetch: rFeedHealth } = usePoll(() => api.getLiveFeedHealth(), DEPLOY_MS);
  // Tighten the first-fetch window: refetch the batch the moment the id set changes
  // (mount → deployments load, or a roster change) instead of waiting up to 10s —
  // closes the null window that would otherwise drop the armed-deployment count.
  useEffect(() => {
    if (depIdsKey) rDeployLive();
  }, [depIdsKey, rDeployLive]);

  // These RETURN their promises: callers do `await refetch.all()` before clearing
  // a busy flag (LiveDeploymentStrip does exactly this). Returning undefined made
  // that await resolve instantly, so the UI un-greyed before the refreshed broker
  // data had landed and briefly showed the pre-action state as if it were current.
  // After a square / kill the point IS to spend one broker call and show the real
  // post-action book — a cached snapshot would render the pre-action state as if
  // it were current. When streaming, that call goes through the marks cache
  // (refresh=true) instead of the now-disabled positions poll: same one call.
  const rPositionsNow = useCallback(
    () => (marksUsable ? api.liveBrokerMarks(true) : rPositions()),
    [marksUsable, rPositions],
  );
  const refetchSlow = useCallback(() => Promise.all([
    rStatus(), rLimits(), rPositionsNow(), rOrders(),
    rReconcile(), rArmState(), rBlotter(), rDeployments(), rGreeks(),
  ]), [rStatus, rLimits, rPositionsNow, rOrders, rReconcile, rArmState, rBlotter, rDeployments, rGreeks]);

  const refetchAll = useCallback(() => Promise.all([
    refetchSlow(),
    rGuard(), rSession(), rGtt(), rDeployLive(), rFeedHealth(),
    rMarketAnalysis(), rHoldings(),
  ]), [refetchSlow, rGuard, rSession, rGtt, rDeployLive, rFeedHealth, rMarketAnalysis, rHoldings]);

  const refetch = useMemo(
    () => ({
      slow: refetchSlow,
      guard: rGuard,
      session: rSession,
      gtt: rGtt,
      deployLive: rDeployLive,
      deployments: rDeployments,
      feedHealth: rFeedHealth,
      all: refetchAll,
    }),
    [refetchSlow, rGuard, rSession, rGtt, rDeployLive, rDeployments, rFeedHealth, refetchAll],
  );

  // Page-level health: which MONEY-relevant slices are currently erroring (their
  // on-screen data is now stale/last-known). Drives the degraded banner so a
  // frozen value is never silently shown as live.
  const health = useMemo(() => {
    const moneyErrors = {
      status: eStatus, limits: eLimits, positions: ePositions, orders: eOrders,
      reconcile: eReconcile, armState: eArmState, blotter: eBlotter,
    };
    const errorSlices = Object.keys(moneyErrors).filter((k) => moneyErrors[k]);
    return { degraded: errorSlices.length > 0, errorSlices };
  }, [eStatus, eLimits, ePositions, eOrders, eReconcile, eArmState, eBlotter]);

  const value = useMemo(
    () => ({
      // data (null until the first successful fetch — consumers treat null = loading)
      status, limits, positions, orders, reconcile, armState, blotter, deployments,
      guard, session, gtt, greeks, feedHealth, marketAnalysis, holdings,
      deployLive: deployLiveData || {},
      // Freshness of the money slice: "stream" (tick-fresh) | "poll" (15s) | null.
      marksSource: marks.source,
      marksAt: marks.lastAt,
      // per-slice last error (null when the latest call succeeded)
      errors: {
        status: eStatus, limits: eLimits, positions: ePositions, orders: eOrders,
        reconcile: eReconcile, armState: eArmState, blotter: eBlotter, deployments: eDeployments,
        guard: eGuard, session: eSession, gtt: eGtt, deployLive: eDeployLive, greeks: eGreeks,
        feedHealth: eFeedHealth, marketAnalysis: eMarketAnalysis, holdings: eHoldings,
      },
      // epoch-ms of the last successful fetch for the money slices (null until first)
      lastSuccess: { limits: lsLimits, positions: lsPositions, orders: lsOrders },
      health,
      refetch,
    }),
    [
      status, limits, positions, orders, reconcile, armState, blotter, deployments,
      guard, session, gtt, greeks, feedHealth, deployLiveData, marketAnalysis, holdings,
      marks.source, marks.lastAt,
      eStatus, eLimits, ePositions, eOrders, eReconcile, eArmState, eBlotter, eDeployments,
      eGuard, eSession, eGtt, eDeployLive, eGreeks, eFeedHealth, eMarketAnalysis, eHoldings,
      lsLimits, lsPositions, lsOrders, health, refetch,
    ],
  );

  return <LiveDataContext.Provider value={value}>{children}</LiveDataContext.Provider>;
}
