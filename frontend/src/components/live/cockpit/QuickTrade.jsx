import LiveOrderTicket from "@/components/live/LiveOrderTicket";
import { SectionCard } from "@/components/live/liveHelpers";
import { useLiveData } from "@/components/live/LiveDataProvider";

/**
 * Quick-trade card — a thin wrapper around the existing one-click LiveOrderTicket
 * so manual real-money entry stays in the always-on core. No trade logic here.
 */
export default function QuickTrade({ mode }) {
  const { refetch } = useLiveData();
  return (
    <SectionCard
      title="Quick Trade"
      badge={
        <span className="text-[10px] font-mono text-danger px-2 py-0.5 rounded-full border border-danger/40 bg-danger/10 uppercase tracking-wider font-bold">
          real money · 1-click
        </span>
      }
    >
      {/* onPlaced refreshes the shared broker slices the moment a real order
          fills, so positions / orders / day-P&L reflect the trade immediately
          instead of lagging up to a full poll cycle. */}
      <LiveOrderTicket mode={mode} disabled={false} onPlaced={refetch.all} />
    </SectionCard>
  );
}
