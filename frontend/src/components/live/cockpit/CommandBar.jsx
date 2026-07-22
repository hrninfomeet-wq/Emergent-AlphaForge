import { Settings2, Zap } from "lucide-react";
import BrokerConnect from "@/components/live/cockpit/BrokerConnect";

/**
 * Cockpit command bar — the persistent control row: brand, a live market-status
 * pill (OPEN/CLOSED, derived client-side from IST hours), the broker connection
 * module, a Kill anchor (scrolls to the always-visible KillSwitchPanel — the kill
 * LOGIC is never duplicated), and the ⚙ Configure button that opens the drawer.
 * The full market ticker is the existing <MarketHeader/>, mounted just below.
 */

function istNow() {
  // IST = UTC + 5:30. Returns {weekday(0-6, 0=Sun), minutes-since-midnight}.
  const now = new Date();
  const ist = new Date(now.getTime() + (now.getTimezoneOffset() + 330) * 60000);
  return { day: ist.getDay(), mins: ist.getHours() * 60 + ist.getMinutes() };
}

function marketOpen() {
  const { day, mins } = istNow();
  const weekday = day >= 1 && day <= 5;              // Mon–Fri (holidays not modelled client-side)
  return weekday && mins >= 555 && mins <= 930;      // 09:15–15:30 IST
}

export default function CommandBar({ flattradeStatus, onConfigure, onChanged }) {
  const open = marketOpen();
  return (
    <div className="sticky top-0 z-20 flex items-center gap-3 flex-wrap rounded-lg border border-line bg-bg-1/90 backdrop-blur px-3 py-2">
      <span className="font-semibold tracking-wide text-foreground whitespace-nowrap">LIVE COCKPIT</span>
      <span
        className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-[11px] font-semibold border ${
          open ? "border-emerald-500/40 bg-emerald-500/10 text-emerald-300"
               : "border-line bg-bg-2 text-dimmer"
        }`}
        title={open ? "NSE cash/F&O open 09:15–15:30 IST" : "Market closed"}
      >
        <span className={`w-1.5 h-1.5 rounded-full ${open ? "bg-emerald-400 animate-pulse" : "bg-dimmer"}`} />
        {open ? "MARKET OPEN · 15:30 close" : "MARKET CLOSED"}
      </span>

      <div className="flex-1" />

      <BrokerConnect flattradeStatus={flattradeStatus} onChanged={onChanged} />

      <a
        href="#kill-switch"
        className="inline-flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg border border-danger/50 bg-danger/10 text-danger text-xs font-semibold hover:bg-danger/20"
        title="Jump to the kill switch"
      >
        <Zap className="w-3.5 h-3.5" /> Kill
      </a>
      <button
        type="button"
        onClick={onConfigure}
        className="inline-flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg border border-sky-500/50 bg-sky-500/10 text-sky-300 text-xs font-semibold hover:bg-sky-500/20"
      >
        <Settings2 className="w-3.5 h-3.5" /> Configure
      </button>
    </div>
  );
}
