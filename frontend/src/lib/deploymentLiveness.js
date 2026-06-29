// Maps a deployment's lifecycle status + the GLOBAL live-feed health into the
// truthful status LED. Green ("ACTIVE · LIVE") appears ONLY when the strategy can
// actually trade right now (fresh candles_1m bars). Pure — no React, no imports.
// feedHealth = { state, reason, cta } from GET /live-feed/health (null while loading).
export function deploymentLiveness(dep, feedHealth) {
  const status = String(dep?.status || "").toUpperCase();
  if (status === "PAUSED") {
    return {
      dot: "bg-amber-400", text: "text-amber-300", label: "PAUSED",
      tooltip: dep?.paused_reason || dep?.kill_switch_reason || "Paused",
    };
  }
  if (status !== "ACTIVE") {
    return { dot: "bg-dimmer", text: "text-dimmer", label: status || "—", tooltip: status || "—" };
  }
  const state = feedHealth?.state;
  const reason = feedHealth?.reason || "";
  switch (state) {
    case "LIVE":
      return { dot: "bg-emerald-400", text: "text-emerald-300", label: "ACTIVE · LIVE",
               tooltip: reason || "Receiving fresh candles." };
    case "WARMING_UP":
      return { dot: "bg-amber-400", text: "text-amber-300", label: "ACTIVE · STARTING",
               tooltip: reason || "Feed starting — first candle shortly." };
    case "NEEDS_LOGIN":
      return { dot: "bg-rose-400", text: "text-rose-300", label: "ACTIVE · FEED OFFLINE",
               tooltip: reason || "Upstox isn't connected — connect to go live." };
    case "DEGRADED":
      return { dot: "bg-rose-400", text: "text-rose-300", label: "ACTIVE · NO LIVE CANDLES",
               tooltip: reason || "Live feed stalled." };
    case "MARKET_CLOSED":
      return { dot: "bg-dimmer", text: "text-dimmer", label: "ACTIVE · MARKET CLOSED",
               tooltip: "Market is closed." };
    default:
      // feedHealth not loaded yet — DO NOT claim green; show neutral "checking".
      return { dot: "bg-dimmer", text: "text-dim", label: "ACTIVE", tooltip: "Checking live feed…" };
  }
}
