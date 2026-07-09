import { createContext, useCallback, useContext, useEffect, useMemo } from "react";
import { api } from "@/lib/api";
import { usePoll } from "@/hooks/usePoll";

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
  const { data: positions, error: ePositions, lastSuccess: lsPositions, refetch: rPositions } = usePoll(() => api.liveBrokerPositions(), SLOW_MS);
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

  const refetchSlow = useCallback(() => {
    rStatus(); rLimits(); rPositions(); rOrders();
    rReconcile(); rArmState(); rBlotter(); rDeployments(); rGreeks();
  }, [rStatus, rLimits, rPositions, rOrders, rReconcile, rArmState, rBlotter, rDeployments, rGreeks]);

  const refetchAll = useCallback(() => {
    refetchSlow();
    rGuard();
    rSession();
    rGtt();
    rDeployLive();
    rFeedHealth();
  }, [refetchSlow, rGuard, rSession, rGtt, rDeployLive, rFeedHealth]);

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
      guard, session, gtt, greeks, feedHealth,
      deployLive: deployLiveData || {},
      // per-slice last error (null when the latest call succeeded)
      errors: {
        status: eStatus, limits: eLimits, positions: ePositions, orders: eOrders,
        reconcile: eReconcile, armState: eArmState, blotter: eBlotter, deployments: eDeployments,
        guard: eGuard, session: eSession, gtt: eGtt, deployLive: eDeployLive, greeks: eGreeks,
        feedHealth: eFeedHealth,
      },
      // epoch-ms of the last successful fetch for the money slices (null until first)
      lastSuccess: { limits: lsLimits, positions: lsPositions, orders: lsOrders },
      health,
      refetch,
    }),
    [
      status, limits, positions, orders, reconcile, armState, blotter, deployments,
      guard, session, gtt, greeks, feedHealth, deployLiveData,
      eStatus, eLimits, ePositions, eOrders, eReconcile, eArmState, eBlotter, eDeployments,
      eGuard, eSession, eGtt, eDeployLive, eGreeks, eFeedHealth,
      lsLimits, lsPositions, lsOrders, health, refetch,
    ],
  );

  return <LiveDataContext.Provider value={value}>{children}</LiveDataContext.Provider>;
}
