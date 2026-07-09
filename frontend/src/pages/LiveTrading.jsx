import LiveDashboard from "@/components/live/LiveDashboard";
import { LiveDataProvider } from "@/components/live/LiveDataProvider";
import LiveErrorBoundary from "@/components/live/LiveErrorBoundary";

/**
 * Live Trading page — real-money broker terminal (Flattrade / Noren).
 *
 * Thin wrapper: <LiveDataProvider> owns ALL polling (one fetch per endpoint at
 * its cadence), and <LiveDashboard /> + its children consume that data via
 * context. This file just mounts the pair so the route stays a clean entry point.
 */
export default function LiveTrading() {
  return (
    <LiveDataProvider>
      <LiveErrorBoundary>
        <LiveDashboard />
      </LiveErrorBoundary>
    </LiveDataProvider>
  );
}
