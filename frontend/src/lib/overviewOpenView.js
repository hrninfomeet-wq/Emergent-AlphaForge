/**
 * What the Deployed Strategies card says about OPEN positions beyond the bare count.
 * No imports, no JSX — loads under node (tests/test_overview_open_view.py executes it).
 *
 * The overview's `today` block used to fold every OPEN row into "Open trades" /
 * "Open MTM" whatever day it was entered, and a live deployment demoted to paper
 * (pause / kill switch) lost its live_trades from the card altogether. The backend
 * (app/overview_open.py) now reports:
 *
 *   today.open_carried / open_carried_oldest   OPEN rows entered on an EARLIER IST day
 *   today.other_book                           OPEN / just-closed rows in the book that
 *                                              does NOT match the deployment's mode
 *   totals.other_book_live_open (+ realized)   the same, summed over demoted deployments
 *
 * Those are shown as separate lines — never folded into the figures above them (paper
 * and real money are never one number), and never dropped.
 */

const MONTH_NAMES = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                     "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const DAY_NAMES = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];

/** "2026-09-25" -> "Fri 25 Sep" (calendar arithmetic; no timezone involved). */
export function fmtDay(iso) {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(iso || ""));
  if (!m) return null;
  const d = new Date(Date.UTC(Number(m[1]), Number(m[2]) - 1, Number(m[3])));
  // Date.UTC rolls an impossible date over ("2026-13-40" -> 9 Feb 2027): require the
  // parts to survive the round trip so a bad date is null, never a plausible wrong one.
  if (Number.isNaN(d.getTime()) || d.getUTCFullYear() !== Number(m[1])
      || d.getUTCMonth() !== Number(m[2]) - 1 || d.getUTCDate() !== Number(m[3])) return null;
  return `${DAY_NAMES[d.getUTCDay()]} ${d.getUTCDate()} ${MONTH_NAMES[d.getUTCMonth()]}`;
}

/** Signed rupees, "—" for a non-finite value: "−₹300" / "+₹40" / "₹0". */
export function fmtRupees(v) {
  if (v === null || v === undefined || v === "") return "—";
  const n = Number(v);
  if (!Number.isFinite(n)) return "—";
  const body = `₹${Math.abs(Math.round(n)).toLocaleString("en-IN")}`;
  if (n > 0) return `+${body}`;
  if (n < 0) return `−${body}`;
  return body;
}

const plural = (n, one, many) => `${n} ${n === 1 ? one : many}`;

/**
 * The extra lines for one deployment card.
 * @param {object} today  item.today from GET /deployments/overview
 * @param {string} [mode] the deployment's current mode ("live" / "paper" / ...)
 * @returns {Array<{id: string, tone: "warn"|"danger", text: string, title: string}>}
 */
export function openNotes(today, mode) {
  const notes = [];
  const t = today && typeof today === "object" ? today : {};

  const carried = Number(t.open_carried) || 0;
  if (carried > 0) {
    const since = fmtDay(t.open_carried_oldest);
    notes.push({
      id: "carried",
      tone: "warn",
      text: `${plural(carried, "open trade", "open trades")} carried from an earlier day`
        + (since ? ` (oldest entered ${since})` : ""),
      title: "\"Open trades\" counts positions open right now, not entered today. A carried "
        + "position whose mark is stale is excluded from Open MTM and counted as unverified.",
    });
  }

  const ob = t.other_book && typeof t.other_book === "object" ? t.other_book : null;
  if (ob) {
    const open = Number(ob.open_trades) || 0;
    const since = fmtDay(ob.oldest_open_entry_ist);
    const m = String(mode || "").toLowerCase() || "paper";
    if (ob.book === "live") {
      const parts = [];
      if (open > 0) {
        parts.push(`${plural(open, "OPEN LIVE trade", "OPEN LIVE trades")} (real money)`
          + (since ? `, oldest entered ${since}` : ""));
      }
      const realized = Number(ob.realized_today) || 0;
      if (realized !== 0) parts.push(`live realized today ${fmtRupees(realized)}`);
      notes.push({
        id: "other-book-live",
        tone: "danger",
        text: `This deployment is now ${m.toUpperCase()}, but its live book still shows: ${parts.join("; ")}.`,
        title: "These rows are in live_trades. They are NOT in the figures above (paper and "
          + "real money are never summed) — check the Live page and the broker book.",
      });
    } else if (open > 0) {
      notes.push({
        id: "other-book-paper",
        tone: "warn",
        text: `${plural(open, "stray paper trade", "stray paper trades")} still OPEN on this live deployment`
          + (since ? ` (oldest entered ${since})` : ""),
        title: "These rows are in paper_trades, not the live book, and are not counted above.",
      });
    }
  }
  return notes;
}

/**
 * The header line for real-money rows sitting under deployments that are no longer
 * live, summed across the page; null when there are none.
 */
export function headerOtherBookNote(totals) {
  const t = totals && typeof totals === "object" ? totals : {};
  const open = Number(t.other_book_live_open) || 0;
  const realized = Number(t.other_book_live_realized_today) || 0;
  if (open <= 0 && realized === 0) return null;
  const parts = [];
  if (open > 0) parts.push(`${plural(open, "open live trade", "open live trades")}`);
  if (realized !== 0) parts.push(`${fmtRupees(realized)} realized today`);
  return {
    tone: "danger",
    text: `Live book under non-live deployments: ${parts.join(" · ")}`,
    title: "Real-money live_trades still owned by deployments that were demoted to paper by "
      + "a pause or kill switch. Not included in Today MTM / Realized today / Open trades.",
  };
}
