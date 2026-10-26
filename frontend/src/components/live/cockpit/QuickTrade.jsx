import { useState } from "react";
import LiveOrderTicket from "@/components/live/LiveOrderTicket";
import ConditionalOrderPanel from "@/components/live/ConditionalOrderPanel";
import { SectionCard } from "@/components/live/liveHelpers";
import { useLiveData } from "@/components/live/LiveDataProvider";

/**
 * Conditional orders default to paper. The existing supervised live ticket
 * remains available in its own tab; trading logic stays on the server.
 */
export default function QuickTrade({ mode }) {
  const { refetch } = useLiveData();
  const [tab, setTab] = useState("conditional");
  return (
    <SectionCard
      title="Quick Trade"
      badge={
        <span className="text-[10px] font-mono text-danger px-2 py-0.5 rounded-full border border-danger/40 bg-danger/10 uppercase tracking-wider font-bold">
          {tab === "conditional" ? "conditional · paper default" : "manual · real money"}
        </span>
      }
    >
      <div role="tablist" aria-label="Quick Trade type" className="flex gap-2 mb-4">
        <button type="button" role="tab" aria-selected={tab === "conditional"} onClick={() => setTab("conditional")} className="rounded border border-border px-3 py-2 text-sm">Conditional orders</button>
        <button type="button" role="tab" aria-selected={tab === "manual"} onClick={() => setTab("manual")} className="rounded border border-border px-3 py-2 text-sm">Manual live ticket</button>
      </div>
      {tab === "conditional" ? <ConditionalOrderPanel /> : <LiveOrderTicket mode={mode} disabled={false} onPlaced={refetch.all} />}
    </SectionCard>
  );
}
