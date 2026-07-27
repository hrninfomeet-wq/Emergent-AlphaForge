import { useCallback, useEffect, useMemo, useState } from "react";
import { api } from "@/lib/api";
import { useLiveData } from "@/components/live/LiveDataProvider";
import {
  SectionCard, ReconcileChip, PositionsBlotter, fmtAsOf,
  asPositionRows, isOpenPosition,
} from "@/components/live/liveHelpers";

import CommandBar from "@/components/live/cockpit/CommandBar";
import AlertRail from "@/components/live/cockpit/AlertRail";
import MarketPulse from "@/components/live/cockpit/MarketPulse";
import MarketAnalysis from "@/components/live/cockpit/MarketAnalysis";
import RiskKpis from "@/components/live/cockpit/RiskKpis";
import QuickTrade from "@/components/live/cockpit/QuickTrade";
import DeploymentSummary from "@/components/live/cockpit/DeploymentSummary";
import AccountTabs from "@/components/live/cockpit/AccountTabs";
import ConfigDrawer from "@/components/live/cockpit/ConfigDrawer";

import ExecutionStateStrip from "@/components/live/ExecutionStateStrip";
import SafetyLatchBanner from "@/components/live/SafetyLatchBanner";
import KillSwitchPanel from "@/components/live/KillSwitchPanel";
import GuardPanel from "@/components/live/GuardPanel";
import GreeksCard from "@/components/live/GreeksCard";

/**
 * LiveCockpit — the trader-first re-organisation of the Live Trading terminal.
 *
 * An ALWAYS-ON CORE keeps market intelligence (left) and the book + quick actions
 * (right) in view; a slide-out CONFIG DRAWER holds the set-and-forget controls
 * (deployments, backstop, overall controls); a tabbed ACCOUNT panel gives the
 * demat account detail. Every panel reuses an existing live component. Phase 1
 * ships this shell on the existing data; MarketPulse/MarketAnalysis fill in when
 * the /market/analysis engine lands (Phase 2).
 */
export default function LiveCockpit() {
  const {
    status, limits, positions, orders, reconcile, armState, blotter, guard, gtt,
    refetch, feedHealth, deployments, health, lastSuccess,
    marketAnalysis, holdings, greeks, errors, deployLive,
  } = useLiveData();
  const fetchAll = refetch.all;

  const [authMsg, setAuthMsg] = useState(null);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [standDownBusy, setStandDownBusy] = useState(false);

  // Stand down: revert the MANUAL ticket path to LIVE_OFFLINE. Restored with
  // ExecutionStateStrip below — dropping it in the cockpit rewrite removed the
  // only one-click way to neutralise an armed manual ticket.
  const handleStandDown = useCallback(async () => {
    setStandDownBusy(true);
    try {
      await api.setLiveMode("LIVE_OFFLINE");
    } catch {
      /* surfaced by the unchanged strip state on the next poll */
    } finally {
      setStandDownBusy(false);
      fetchAll();
    }
  }, [fetchAll]);

  // OAuth post-redirect (Flattrade bounces to /live-trading?flattrade_connected=1).
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    if (params.has("flattrade_connected")) {
      setAuthMsg({ ok: true, text: "Flattrade login successful — connected." });
      fetchAll();
    } else if (params.has("flattrade_error")) {
      setAuthMsg({ ok: false, text: `Flattrade login failed: ${params.get("flattrade_error")}` });
    }
    if (params.has("flattrade_connected") || params.has("flattrade_error")) {
      window.history.replaceState({}, "", window.location.pathname);
    }
  }, [fetchAll]);

  // Auto-dismiss the SUCCESS banner only. Left pinned it would still claim
  // "login successful" hours later, after the token expired — a stale green
  // banner contradicting a red broker chip. An OAuth ERROR must NOT vanish: it
  // is the only diagnostic the operator gets when a login round-trip fails.
  useEffect(() => {
    if (!authMsg?.ok) return undefined;
    const t = window.setTimeout(() => setAuthMsg(null), 8000);
    return () => window.clearTimeout(t);
  }, [authMsg]);

  // ...and drop it immediately if the session actually goes away.
  useEffect(() => {
    if (authMsg?.ok && status && !(status.connected && !status.expired)) setAuthMsg(null);
  }, [authMsg, status]);

  const mode = armState?.mode ?? null;
  const activeCount = (deployments || []).filter((d) => String(d?.status || "").toUpperCase() === "ACTIVE").length;
  // Real open broker exposure — used to warn before a Flattrade disconnect.
  const openPositionCount = (asPositionRows(positions) || []).filter(isOpenPosition).length;

  // UNGUARDED broker positions (server reconcile diff) + NO-BACKSTOP live positions
  // (blotter rows carrying oco_error) — same derivations the old dashboard used.
  const unguardedPositions = (reconcile?.mismatches ?? [])
    .filter((m) => m?.type === "unknown_broker_position")
    .map((m) => ({ tsym: m?.detail?.tsym }))
    .filter((p) => p.tsym);
  const noBackstopPositions = (blotter?.rows ?? [])
    .filter((r) => r?.oco_error && String(r?.status ?? "").toUpperCase() === "LIVE")
    .map((r) => ({ tsym: r?.trading_symbol }))
    .filter((p) => p.tsym);
  // Positions re-attached after a restart carry a DEEP-DEFAULT catastrophe stop,
  // not the levels the operator set — surface that at the top, not only inside
  // the guard panel.
  const rehydratedPositions = (guard?.guarded ?? [])
    .filter((g) => String(g?.source ?? "") === "rehydrated")
    .map((g) => ({ tsym: g?.tsym }))
    .filter((p) => p.tsym);

  const openDrawer = useCallback(() => setDrawerOpen(true), []);

  // The analysis endpoint deliberately leaves net greeks null (they are
  // position-driven, already polled at /live-broker/greeks, and would cost a
  // duplicate broker read). Merge that slice in here so MarketAnalysis renders
  // one coherent payload — and so the panel still works when disconnected.
  const analysis = useMemo(() => {
    if (!marketAnalysis) return null;
    return {
      ...marketAnalysis,
      options: {
        ...(marketAnalysis.options || {}),
        net_delta_rupee: greeks?.net_delta_rupees_per_point ?? null,
        net_theta_rupee: greeks?.net_theta_rupees_per_day ?? null,
      },
    };
  }, [marketAnalysis, greeks]);

  return (
    <div className="space-y-4">
      <CommandBar
        flattradeStatus={status} onConfigure={openDrawer} onChanged={fetchAll}
        openPositions={openPositionCount}
      />

      {/* THE execution-state verdict: will a signal transmit a REAL order right
          now? Also carries the backend's exit_gap/warning banner and the
          Stand-down control. Kept directly under the command bar — it is the
          single most consequential line on the page. */}
      <ExecutionStateStrip
        armState={armState}
        onStandDown={handleStandDown}
        standingDown={standDownBusy}
      />

      {/* A tripped broker-stop-loss latch halts EVERY new live entry and never
          self-clears. It sits directly under the execution verdict because it is
          the one condition that silently overrides everything above it, and
          until now it had no in-app exit at all. */}
      <SafetyLatchBanner onChanged={fetchAll} />

      {/* NOTE: no <MarketHeader/> here — the app shell (Layout.jsx) already
          renders one on every route. Mounting a second copy duplicated ~270px of
          ticker, opened a second SSE connection, and gave the page two
          independent Upstox Start/Stop stream toggles that could disagree. */}

      <AlertRail
        health={health}
        unguardedPositions={unguardedPositions}
        noBackstopPositions={noBackstopPositions}
        rehydratedPositions={rehydratedPositions}
        feedHealth={feedHealth}
        activeCount={activeCount}
        authMsg={authMsg}
      />

      {/* Broker-data as-of stamp — a failing poll keeps the last-good value on
          screen, so this makes a STALE reading legible (never looks live). */}
      <div className="flex items-center justify-between px-0.5">
        <span className="text-[10px] font-mono uppercase tracking-wider text-dimmer">Broker account</span>
        <span
          className={`text-[10px] font-mono ${health?.degraded ? "text-warning" : "text-dimmer"}`}
          data-testid="live-hero-asof"
          title="Time of the last successful broker read"
        >
          as of {fmtAsOf(lastSuccess?.positions ?? lastSuccess?.limits)}{health?.degraded ? " · STALE" : ""}
        </span>
      </div>

      {/* Always-on core */}
      <div className="grid grid-cols-1 lg:grid-cols-[1.55fr_1fr] gap-4">
        {/* LEFT — market intelligence */}
        <div className="space-y-4">
          <MarketPulse analysis={analysis} />
          <MarketAnalysis analysis={analysis} />
          <GreeksCard />
        </div>

        {/* RIGHT — book & actions */}
        <div className="space-y-4">
          <RiskKpis
            limits={limits} positions={positions} orders={orders} guard={guard}
            deployments={deployments} deployLive={deployLive}
          />
          <SectionCard title="Open Positions" badge={<ReconcileChip reconcile={reconcile} error={errors?.reconcile} />}>
            <PositionsBlotter positions={positions} />
          </SectionCard>
          {/* scroll-margin-top clears the sticky command bar, so jumping here
              from it doesn't land with the panel header hidden underneath. */}
          <div id="kill-switch" style={{ scrollMarginTop: "72px" }}><KillSwitchPanel /></div>
          <GuardPanel />
          <QuickTrade mode={mode} />
          <DeploymentSummary deployments={deployments} onManage={openDrawer} />
        </div>
      </div>

      {/* Tabbed account panel */}
      <AccountTabs
        limits={limits} orders={orders} blotter={blotter} gtt={gtt}
        holdings={holdings} errors={errors}
      />

      <ConfigDrawer open={drawerOpen} onClose={() => setDrawerOpen(false)} onArmedSummaryChange={() => {}} />
    </div>
  );
}
