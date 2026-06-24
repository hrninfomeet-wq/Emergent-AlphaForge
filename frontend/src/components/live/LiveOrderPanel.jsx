import { useCallback, useEffect, useState } from "react";
import { ShieldAlert, Zap } from "lucide-react";
import { api } from "@/lib/api";
import ModeSwitch from "./ModeSwitch";
import PositionMonitor from "./PositionMonitor";
import LiveOrderTicket from "./LiveOrderTicket";
import ApprovalQueue from "./ApprovalQueue";
import OverallSettingsPanel from "./OverallSettingsPanel";
import GttBook from "./GttBook";

/**
 * LiveOrderPanel — the primary order experience: ModeSwitch + PositionMonitor +
 * the multi-lot LiveOrderTicket (preview → queue) + the approval-gated
 * ApprovalQueue (approve → place).
 *
 * The ticket's preview/queue do NOT require LIVE_TEST — building and queueing an
 * approval is allowed in any mode. Only the approve-to-place step is gated, and
 * that gate is enforced server-side and surfaced by the ApprovalQueue.
 *
 * One-shot approval tokens are returned ONCE by createOrderApproval (never by the
 * list endpoint), so this panel holds them in memory keyed by approval_id and
 * hands them to the queue. handleConsumed drops a token once it has been
 * approved/rejected/terminally-gone.
 */

function SectionCard({ title, badge, children }) {
  return (
    <div className="rounded-lg border border-line bg-bg-1 overflow-hidden">
      <div className="px-4 py-2.5 border-b border-line bg-bg-2/40 flex items-center gap-2">
        <span className="text-sm font-semibold text-foreground">{title}</span>
        {badge}
      </div>
      <div className="px-4 py-3">{children}</div>
    </div>
  );
}

export default function LiveOrderPanel() {
  const [mode, setMode] = useState(null); // null = loading
  const [tokens, setTokens] = useState({}); // { [approval_id]: token }

  const fetchMode = useCallback(() => {
    api
      .getLiveMode()
      .then((data) => setMode(data.mode ?? null))
      .catch(() => {
        /* backend not wired yet — stay null */
      });
  }, []);

  useEffect(() => {
    fetchMode();
  }, [fetchMode]);

  const handleQueued = useCallback((res) => {
    setTokens((prev) => ({ ...prev, [res.approval_id]: res.token }));
  }, []);

  const handleConsumed = useCallback((id) => {
    setTokens((prev) => {
      const next = { ...prev };
      delete next[id];
      return next;
    });
  }, []);

  const isLiveTest = mode === "LIVE_TEST";

  return (
    <div className="space-y-4">
      {/* Mode switch card */}
      <SectionCard
        title="Execution Mode"
        badge={
          isLiveTest ? (
            <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full border border-danger/60 bg-danger/15 text-danger text-[10px] font-mono font-bold uppercase tracking-wider">
              <Zap className="w-3 h-3" />
              LIVE TEST
            </span>
          ) : (
            <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full border border-line bg-bg-3 text-dimmer text-[10px] font-mono uppercase tracking-wider">
              <ShieldAlert className="w-3 h-3" />
              {mode ?? "loading…"}
            </span>
          )
        }
      >
        <ModeSwitch mode={mode} onModeChange={(m) => setMode(m)} />
      </SectionCard>

      {/* Position monitor — only shown when a session is active */}
      <PositionMonitor />

      {/* Order ticket — preview + queue (queueing is allowed in any mode) */}
      <SectionCard
        title="Live Order — Approval Gated"
        badge={
          <span className="text-[10px] font-mono text-danger px-2 py-0.5 rounded-full border border-danger/40 bg-danger/10 uppercase tracking-wider font-bold">
            REAL MONEY
          </span>
        }
      >
        <LiveOrderTicket mode={mode} disabled={false} onQueued={handleQueued} />
      </SectionCard>

      {/* Approval queue — approve (places real order) or reject */}
      <SectionCard title="Approval Queue">
        <ApprovalQueue tokens={tokens} mode={mode} onConsumed={handleConsumed} />
      </SectionCard>

      {/* Overall controls — basket SL / target / trailing / re-entry (AlgoTest parity) */}
      <SectionCard
        title="Overall Controls"
        badge={
          <span className="text-[10px] font-mono text-dimmer px-2 py-0.5 rounded-full border border-line bg-bg-3 uppercase tracking-wider">
            basket SL / target / trailing
          </span>
        }
      >
        <OverallSettingsPanel scope="overall" />
      </SectionCard>

      {/* GTT / OCO disaster backstop (NRML-only) */}
      <SectionCard
        title="GTT / OCO Backstop"
        badge={
          <span className="text-[10px] font-mono text-dimmer px-2 py-0.5 rounded-full border border-line bg-bg-3 uppercase tracking-wider">
            NRML PC-died net
          </span>
        }
      >
        <GttBook />
      </SectionCard>
    </div>
  );
}
