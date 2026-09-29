/**
 * "Last evaluated" label for a deployment card. No imports, no JSX — loads under
 * node (tests/test_last_evaluated_view.py executes it).
 *
 * The card used to print only "Last evaluated 15:29 IST". With no date, a
 * deployment that had not been evaluated for DAYS (this machine is rarely up in
 * market hours) looked exactly like one that ran a minute ago — right beside a
 * green ACTIVE chip. The backend field exists precisely "so an operator can tell a
 * live deployment from a stalled one" (routers/deployments.py), so the label must
 * say WHEN, not just what o'clock.
 *
 * Dates are IST calendar dates, computed by arithmetic on the epoch rather than
 * Intl: the viewer's own timezone must never move a date, and a PC set to another
 * zone must not either.
 */

const IST_OFFSET_MS = (5 * 60 + 30) * 60 * 1000;
const DAY_NAMES = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
const MONTH_NAMES = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                     "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

const pad2 = (n) => String(n).padStart(2, "0");

/** The wall-clock parts of an epoch-ms instant, read in IST. */
function istParts(ms) {
  const d = new Date(ms + IST_OFFSET_MS);            // UTC getters now read IST
  return {
    y: d.getUTCFullYear(), m: d.getUTCMonth(), day: d.getUTCDate(),
    dow: d.getUTCDay(), hh: d.getUTCHours(), mm: d.getUTCMinutes(),
  };
}

/**
 * Describe when a deployment was last evaluated.
 *
 * @param {number|string|null} ms   the candle minute, epoch ms (`last_evaluated_ts`)
 * @param {number} [nowMs]          "now", epoch ms (defaults to Date.now())
 * @returns {null | {label: string, today: boolean}}
 *   null when there is nothing to show (missing / non-finite / non-positive) —
 *   the caller renders its own "not yet evaluated" copy, never a made-up time.
 *   `today` is false when the evaluation was on a different IST date, so the card
 *   can colour it as a warning.
 */
export function formatLastEvaluated(ms, nowMs = Date.now()) {
  if (ms == null || ms === "") return null;
  const t = Number(ms);
  if (!Number.isFinite(t) || t <= 0) return null;
  const then = istParts(t);
  const hm = `${pad2(then.hh)}:${pad2(then.mm)}`;
  const now = Number.isFinite(Number(nowMs)) ? istParts(Number(nowMs)) : null;
  const sameDay = now !== null
    && now.y === then.y && now.m === then.m && now.day === then.day;
  if (sameDay) return { label: `Last evaluated ${hm} IST`, today: true };
  // A different year is spelled out; within the year "Fri 25 Sep" is unambiguous.
  const year = now !== null && now.y !== then.y ? ` ${then.y}` : "";
  return {
    label: `Last evaluated ${DAY_NAMES[then.dow]} ${then.day} ${MONTH_NAMES[then.m]}${year} ${hm} IST`,
    today: false,
  };
}
