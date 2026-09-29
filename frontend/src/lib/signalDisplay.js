/**
 * How the Signal Journal labels a signal's STATE. No imports beyond istWhen's pure
 * helpers, no JSX — loads under node (tests/test_signal_display.py executes it).
 *
 * A clean signal is journaled CONFIRMED ("passed the pre-trade filter") and is acted
 * on ONLY inside the evaluator pass for its own bar: `evaluate_active_deployments`
 * routes that pass's fresh results to the paper / live sink seconds after the bar
 * closes, and nothing ever revisits it. There is no manual-approve route, a refused
 * sink releases its claim but nothing retries, and a signal-only deployment has no
 * sink at all. So CONFIRMED describes "the pass is happening right now", and a CONFIRMED
 * signal whose bar is minutes old — or from an earlier day — will never be acted on. The
 * amber CONFIRMED chip read as "awaiting action" for ever.
 *
 * The same rule is enforced server-side (signal_lifecycle.expire_unactioned_signals
 * moves such signals to AUDITED); this is the display-level rule for the gap before that
 * sweep has run, and it must agree with it: EXPIRE_AFTER_MS mirrors
 * UNACTIONED_AFTER_MINUTES.
 *
 * Unknown stays unknown: a CONFIRMED signal whose bar time cannot be read is not
 * called expired, and one that a sink claimed without recording a trade is called
 * UNVERIFIED, never "not acted on" (a trade may exist).
 */
import { formatIstWhen, istDay, toEpochMs } from "./istWhen.js";

/** Mirrors backend signal_lifecycle.UNACTIONED_AFTER_MINUTES (15). */
export const EXPIRE_AFTER_MS = 15 * 60 * 1000;

const refusalOf = (sig) => (
  sig?.live_trade_error || sig?.paper_trade_error || sig?.paper_trade_skip || null
);

/**
 * @param {object} sig  a row of GET /signals/enriched
 * @param {number} [nowMs]
 * @returns {{state: string, label: string, tone: "pending"|"expired"|"unverified"|"normal",
 *            expired: boolean, note: string|null}}
 */
export function signalDisplay(sig, nowMs = Date.now()) {
  const state = String(sig?.state || "").toUpperCase();
  if (state !== "CONFIRMED") {
    return { state, label: state || "—", tone: "normal", expired: false, note: null };
  }

  const hasTrade = Boolean(sig?.paper_trade_id || sig?.live_trade_id);
  if (hasTrade) {
    // Linked to a trade: the lifecycle write lagged the trade; not "unactioned".
    return { state, label: "CONFIRMED", tone: "normal", expired: false,
             note: "a trade is linked to this signal" };
  }
  if (sig?.paper_trade_claim) {
    return {
      state, label: "UNVERIFIED", tone: "unverified", expired: false,
      note: "A trade sink claimed this signal but no trade was recorded on it (a crash "
        + "between the two writes). A trade may exist — check the paper / live blotter.",
    };
  }

  const refusal = refusalOf(sig);
  if (refusal) {
    // The sink refused and released its claim; nothing retries. Definitive at once.
    return { state, label: "NOT ACTED ON", tone: "expired", expired: true,
             note: `Not traded — refused: ${refusal}` };
  }

  // /signals/enriched resolves the bar into `bar_ts`; a raw signal doc stores it as
  // `candle_ts` / `context.candle.ts` (bar_ts belongs to the audit record) — read all.
  const barMs = toEpochMs(sig?.bar_ts ?? sig?.candle_ts ?? sig?.context?.candle?.ts);
  if (barMs === null) {
    return { state, label: "CONFIRMED", tone: "pending", expired: false,
             note: "bar time not recorded — cannot tell whether it is still being routed" };
  }
  const now = Number(nowMs);
  const barWhen = formatIstWhen(barMs, now);
  const earlierDay = Number.isFinite(now) && istDay(barMs) < istDay(now);
  const stale = Number.isFinite(now) && now - barMs > EXPIRE_AFTER_MS;
  if (earlierDay || stale) {
    return {
      state, label: "EXPIRED", tone: "expired", expired: true,
      note: `Not acted on — its bar (${barWhen ? barWhen.label : "unknown time"}) has passed. `
        + "Signals are routed to a trade only during their own bar's evaluation; nothing "
        + "revisits a CONFIRMED signal afterwards.",
    };
  }
  return { state, label: "CONFIRMED", tone: "pending", expired: false,
           note: "being routed for this bar" };
}

/** Tailwind classes for the chip, by tone. `fallback` is the existing per-state style. */
export function signalChipClass(view, fallback) {
  switch (view?.tone) {
    case "expired": return "border-line text-dimmer";
    case "unverified": return "border-rose-500/40 text-rose-300";
    default: return fallback;
  }
}
