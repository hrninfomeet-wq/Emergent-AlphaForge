import LiveOrderTicket from "@/components/live/LiveOrderTicket";
import { SectionCard } from "@/components/live/liveHelpers";

/**
 * Quick-trade card — a thin wrapper around the existing one-click LiveOrderTicket
 * so manual real-money entry stays in the always-on core. No trade logic here.
 */
export default function QuickTrade({ mode }) {
  return (
    <SectionCard
      title="Quick Trade"
      badge={
        <span className="text-[10px] font-mono text-danger px-2 py-0.5 rounded-full border border-danger/40 bg-danger/10 uppercase tracking-wider font-bold">
          real money · 1-click
        </span>
      }
    >
      <LiveOrderTicket mode={mode} disabled={false} />
    </SectionCard>
  );
}
