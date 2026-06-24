import LiveDashboard from "@/components/live/LiveDashboard";

/**
 * Live Trading page — real-money broker terminal (Flattrade / Noren).
 *
 * Thin wrapper: all page state, polling, OAuth post-redirect handling, and the
 * dashboard layout live in <LiveDashboard />. This file just mounts it so the
 * route stays a clean entry point.
 *
 * (The legacy LiveOrderPanel.jsx remains in the repo but is no longer rendered —
 * its order experience is now composed directly inside LiveDashboard.)
 */
export default function LiveTrading() {
  return <LiveDashboard />;
}
