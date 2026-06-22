import { useCallback, useEffect, useState } from "react";
import { ShieldAlert, Zap } from "lucide-react";
import { api } from "@/lib/api";
import ModeSwitch from "./ModeSwitch";
import OrderTicket from "./OrderTicket";
import PositionMonitor from "./PositionMonitor";

/**
 * LiveTestPanel — the L3 panel that composes ModeSwitch + OrderTicket + PositionMonitor.
 *
 * The OrderTicket is only enabled when mode === "LIVE_TEST".
 * Mode is loaded once on mount and refreshed after every ModeSwitch action.
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

export default function LiveTestPanel() {
  const [mode, setMode] = useState(null); // null = loading

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

  const handleModeChange = (newMode) => {
    setMode(newMode);
  };

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
        <ModeSwitch mode={mode} onModeChange={handleModeChange} />
      </SectionCard>

      {/* Position monitor — only shown when a session is active */}
      <PositionMonitor />

      {/* Order ticket — disabled unless LIVE_TEST */}
      <SectionCard
        title="Order Ticket (1-lot buy)"
        badge={
          !isLiveTest ? (
            <span className="text-[10px] font-mono text-dimmer px-2 py-0.5 rounded-full border border-line bg-bg-3 uppercase tracking-wider">
              Requires LIVE_TEST
            </span>
          ) : (
            <span className="text-[10px] font-mono text-danger px-2 py-0.5 rounded-full border border-danger/40 bg-danger/10 uppercase tracking-wider font-bold">
              REAL MONEY
            </span>
          )
        }
      >
        <OrderTicket mode={mode} disabled={!isLiveTest} />
      </SectionCard>
    </div>
  );
}
