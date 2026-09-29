/**
 * Trading-clock countdowns against the SERVER's instants. No imports, no JSX —
 * loads under node (tests/test_session_clock_view.py executes it).
 *
 * The backend's `session` block (live/session_clock.describe_session, delivered on
 * the arm-state poll) carries absolute epoch-ms boundaries. The browser's own wall
 * clock is never compared against them: a PC clock ten minutes out would make every
 * countdown ten minutes wrong. Instead the server instant is anchored to
 * performance.now() — monotonic, immune to clock skew, NTP jumps and sleep — and
 * advanced from there.
 */

/** Anchor a server instant to the local monotonic clock at the moment of receipt. */
export function makeAnchor(serverNowMs, perfNowMs) {
  const s = Number(serverNowMs);
  const p = Number(perfNowMs);
  if (!Number.isFinite(s) || !Number.isFinite(p)) return null;
  return { serverMs: s, perf: p };
}

/** Server "now" from an anchor and the current monotonic reading. */
export function serverNowFrom(anchor, perfNowMs) {
  if (!anchor) return null;
  return anchor.serverMs + (Number(perfNowMs) - anchor.perf);
}

/** "1h 12m" / "12m 05s" / "45s". Negative → "0s". */
export function fmtRemaining(ms) {
  const t = Math.max(0, Math.floor(Number(ms) / 1000));
  const h = Math.floor(t / 3600);
  const m = Math.floor((t % 3600) / 60);
  const s = t % 60;
  if (h > 0) return `${h}h ${String(m).padStart(2, "0")}m`;
  if (m > 0) return `${m}m ${String(s).padStart(2, "0")}s`;
  return `${s}s`;
}

//: An anchor older than this means the poll has been failing: stop counting.
export const ANCHOR_STALE_MS = 60_000;

const IST_MS = 5.5 * 3600 * 1000;
const istHHMM = (ms) => new Date(ms + IST_MS).toISOString().slice(11, 16);
const istDay = (ms) => new Date(ms + IST_MS).toISOString().slice(0, 10);

/**
 * The countdown line for the execution strip.
 * Returns {text, tone, stale, crossed}: `crossed` = the next boundary has passed
 * locally, so the caller should re-fetch the authoritative phase.
 */
export function sessionCountdown(session, anchor, perfNowMs) {
  if (!session || session.error) {
    return { text: "session clock unavailable", tone: "warn", stale: true, crossed: false };
  }
  if (!anchor || Number(perfNowMs) - anchor.perf > ANCHOR_STALE_MS) {
    return { text: "clock stale — —", tone: "warn", stale: true, crossed: false };
  }
  const now = serverNowFrom(anchor, perfNowMs);
  const ev = session.next_event;
  const remaining = ev ? Number(ev.at_ms) - now : null;
  const crossed = remaining !== null && remaining <= 0;
  const at = ev ? istHHMM(Number(ev.at_ms)) : null;
  switch (session.phase) {
    case "pre_open":
      return { text: `market opens ${at} · in ${fmtRemaining(remaining)}`, tone: "dim", stale: false, crossed };
    case "session":
      return {
        text: `entries close ${at} · ${fmtRemaining(remaining)}`,
        tone: remaining !== null && remaining < 15 * 60 * 1000 ? "warn" : "dim",
        stale: false, crossed,
      };
    case "after_cutoff":
      return ev
        ? { text: `no new entries · EOD square ${at} · ${fmtRemaining(remaining)}`, tone: "warn", stale: false, crossed }
        : { text: "no new entries · EOD square time unknown", tone: "warn", stale: false, crossed: false };
    case "after_eod":
    case "closed_day": {
      const why = session.phase === "after_eod" ? "session over"
        : session.day_kind === "holiday"
          ? `market holiday${session.holiday_label ? ` (${session.holiday_label})` : ""}`
          : "market closed (weekend)";
      if (!ev) return { text: `${why} · next session unknown`, tone: "dim", stale: false, crossed: false };
      return { text: `${why} · next session ${istDay(Number(ev.at_ms))} ${at}`, tone: "dim", stale: false, crossed };
    }
    default:
      return { text: "—", tone: "warn", stale: true, crossed: false };
  }
}

/**
 * This deployment's own last entry time, when it is EARLIER than the global cutoff
 * (the default entry window ends 14:50, ten minutes before the 15:00 gate). Null
 * when it adds nothing.
 */
export function deploymentEntryEnd(entryWindow, session) {
  const end = entryWindow?.effective_end;
  const global = session?.entry_cutoff_ist;
  if (!end) return null;
  if (global && end >= global) return null;
  return `entries until ${end}`;
}

/**
 * The cockpit's market pill, overridden when the SERVER says the day is not a
 * trading day. The client-side pill knows weekdays but not NSE holidays, and read
 * "MARKET OPEN" on them. Null = no override (the client's intraday phase stands).
 */
export function marketPillOverride(session) {
  if (!session || session.error || session.is_trading_day !== false) return null;
  const holiday = session.day_kind === "holiday";
  return {
    open: false,
    label: holiday ? "HOLIDAY — MARKET CLOSED" : "MARKET CLOSED",
    title: holiday
      ? `NSE holiday${session.holiday_label ? `: ${session.holiday_label}` : ""}`
      : "Market closed (weekend)",
  };
}
