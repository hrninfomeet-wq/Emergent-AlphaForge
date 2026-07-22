import { useCallback, useEffect, useState } from "react";
import { useLiveData } from "@/components/live/LiveDataProvider";
import {
  SectionCard, ReconcileChip, PositionsBlotter, fmtAsOf,
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

import MarketHeader from "@/components/MarketHeader";
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
  } = useLiveData();
  const fetchAll = refetch.all;

  const [authMsg, setAuthMsg] = useState(null);
  const [drawerOpen, setDrawerOpen] = useState(false);

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

  const mode = armState?.mode ?? null;
  const activeCount = (deployments || []).filter((d) => String(d?.status || "").toUpperCase() === "ACTIVE").length;

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

  const openDrawer = useCallback(() => setDrawerOpen(true), []);

  return (
    <div className="space-y-4">
      <CommandBar flattradeStatus={status} onConfigure={openDrawer} onChanged={fetchAll} />

      {/* Full market ticker — the existing header, now on the trading page too. */}
      <MarketHeader />

      <AlertRail
        health={health}
        unguardedPositions={unguardedPositions}
        noBackstopPositions={noBackstopPositions}
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
          <MarketPulse analysis={null} />
          <MarketAnalysis analysis={null} />
          <GreeksCard />
        </div>

        {/* RIGHT — book & actions */}
        <div className="space-y-4">
          <RiskKpis limits={limits} positions={positions} orders={orders} guard={guard} />
          <SectionCard title="Open Positions" badge={<ReconcileChip reconcile={reconcile} />}>
            <PositionsBlotter positions={positions} />
          </SectionCard>
          <div id="kill-switch"><KillSwitchPanel /></div>
          <GuardPanel />
          <QuickTrade mode={mode} />
          <DeploymentSummary deployments={deployments} onManage={openDrawer} />
        </div>
      </div>

      {/* Tabbed account panel */}
      <AccountTabs limits={limits} orders={orders} blotter={blotter} gtt={gtt} holdings={null} />

      <ConfigDrawer open={drawerOpen} onClose={() => setDrawerOpen(false)} onArmedSummaryChange={() => {}} />
    </div>
  );
}
