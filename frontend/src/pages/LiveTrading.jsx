import LiveCockpit from "@/components/live/LiveCockpit";
import { LiveDataProvider } from "@/components/live/LiveDataProvider";
import LiveErrorBoundary from "@/components/live/LiveErrorBoundary";

/**
 * Live Trading page — real-money broker terminal (Flattrade / Noren).
 *
 * Thin wrapper: <LiveDataProvider> owns ALL polling (one fetch per endpoint at
 * its cadence), and <LiveCockpit /> + its children consume that data via context.
 * The cockpit re-organises the terminal into an always-on core + config drawer +
 * tabbed account panel (2026-07 redesign — see docs/superpowers/specs/
 * 2026-07-22-live-cockpit-redesign-design.md). The previous LiveDashboard is
 * retired; its helpers moved to liveHelpers.js (reused verbatim).
 */
export default function LiveTrading() {
  return (
    <LiveDataProvider>
      <LiveErrorBoundary>
        <LiveCockpit />
      </LiveErrorBoundary>
    </LiveDataProvider>
  );
}
