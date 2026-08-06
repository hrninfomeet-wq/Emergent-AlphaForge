import { Settings2, Zap } from "lucide-react";
import BrokerConnect from "@/components/live/cockpit/BrokerConnect";

/**
 * Cockpit command bar — the persistent control row: brand, a live market-status
 * pill (OPEN/CLOSED, derived client-side from IST hours), the broker connection
 * module, a Kill anchor (scrolls to the always-visible KillSwitchPanel — the kill
 * LOGIC is never duplicated), and the ⚙ Configure button that opens the drawer.
 * The full market ticker is the existing <MarketHeader/>, mounted just below.
 */

// SEBI's Closing Auction Session split the trading day on 2026-08-03: the cash
// segment trades continuously only to 15:15 and then settles by auction, while
// equity derivatives run on to 15:40. Mirrors backend/app/session_spec.py — keep
// the two in step. Date-aware so the label stays right for pre-CAS timestamps.
export const CAS_EFFECTIVE_ISO = "2026-08-03";
const OPEN_MIN = 9 * 60 + 15;        // 09:15
const CAS_START_MIN = 15 * 60 + 15;  // 15:15
const CASH_CLOSE_MIN = 15 * 60 + 30; // 15:30
const FNO_CLOSE_MIN = 15 * 60 + 40;  // 15:40, from 2026-08-03

function istNow() {
  // IST = UTC + 5:30. Returns {weekday(0-6, 0=Sun), minutes-since-midnight, iso date}.
  const now = new Date();
  const ist = new Date(now.getTime() + (now.getTimezoneOffset() + 330) * 60000);
  const iso = `${ist.getFullYear()}-${String(ist.getMonth() + 1).padStart(2, "0")}-${String(ist.getDate()).padStart(2, "0")}`;
  return { day: ist.getDay(), mins: ist.getHours() * 60 + ist.getMinutes(), iso };
}

// Exported for unit testing — takes the clock reading so it stays pure.
export function marketPhase({ day, mins, iso }) {
  const weekday = day >= 1 && day <= 5;              // Mon–Fri (holidays not modelled client-side)
  const closed = { open: false, label: "MARKET CLOSED", title: "Market closed" };
  if (!weekday) return closed;

  const cas = iso >= CAS_EFFECTIVE_ISO;
  const close = cas ? FNO_CLOSE_MIN : CASH_CLOSE_MIN;
  if (mins < OPEN_MIN || mins >= close) return closed;

  if (cas && mins >= CASH_CLOSE_MIN) {
    return {
      open: true,
      label: "F&O ONLY · 15:40 close",
      title: "Cash has settled; equity derivatives trade until 15:40 IST",
    };
  }
  if (cas && mins >= CAS_START_MIN) {
    return {
      open: true,
      label: "CLOSING AUCTION · F&O to 15:40",
      title: "Cash in closing auction 15:15–15:35 — the index is frozen. F&O trades until 15:40 IST",
    };
  }
  return {
    open: true,
    label: cas ? "MARKET OPEN · 15:40 F&O close" : "MARKET OPEN · 15:30 close",
    title: cas
      ? "NSE cash open 09:15–15:15 (auction to 15:35); F&O to 15:40 IST"
      : "NSE cash/F&O open 09:15–15:30 IST",
  };
}

export default function CommandBar({ flattradeStatus, onConfigure, onChanged, openPositions = 0 }) {
  const phase = marketPhase(istNow());
  const open = phase.open;
  return (
    <div className="sticky top-0 z-20 flex items-center gap-3 flex-wrap rounded-lg border border-line bg-bg-1/90 backdrop-blur px-3 py-2">
      <span className="font-semibold tracking-wide text-foreground whitespace-nowrap">LIVE COCKPIT</span>
      {/* Theme tokens, not raw palette values: the app has a light theme and
          emerald-300-on-emerald-500/10 is unreadable on a white ground. */}
      <span
        className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-[11px] font-semibold border ${
          open ? "border-success/40 bg-success/10 text-success"
               : "border-line bg-bg-2 text-dimmer"
        }`}
        title={phase.title}
      >
        <span className={`w-1.5 h-1.5 rounded-full ${open ? "bg-success animate-pulse" : "bg-dimmer"}`} />
        {phase.label}
      </span>

      <div className="flex-1" />

      <BrokerConnect flattradeStatus={flattradeStatus} onChanged={onChanged} openPositions={openPositions} />

      {/* NAVIGATION, not a fire button. Styled as a quiet outline with a "↓" so
          it cannot be mistaken for the real kill control — firing still requires
          the typed confirmation inside KillSwitchPanel. */}
      <a
        href="#kill-switch"
        className="inline-flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg border border-danger/40 text-danger text-xs font-semibold hover:bg-danger/10"
        title="Scroll to the kill switch (does not fire it)"
      >
        <Zap className="w-3.5 h-3.5" /> Kill switch ↓
      </a>
      <button
        type="button"
        onClick={onConfigure}
        className="inline-flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg border border-info/50 bg-info/10 text-info text-xs font-semibold hover:bg-info/20"
      >
        <Settings2 className="w-3.5 h-3.5" /> Configure
      </button>
    </div>
  );
}
