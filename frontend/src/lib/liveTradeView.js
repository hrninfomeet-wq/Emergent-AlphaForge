/**
 * How the Live Trade Statistics tab labels a journaled trade's OPEN state. No imports,
 * no JSX — loads under node (tests/test_live_trade_view.py executes it).
 *
 * The tab lists the raw `live_trades` journal, and rendered `status: "OPEN"` as a
 * green chip. An OPEN row is the JOURNAL's word: nothing at the broker was asked. The
 * root cause of stranded OPEN rows is fixed (the reconcile now closes stale OPEN docs),
 * but a row can still be OPEN while the app's own guard has stopped marking it (a restart,
 * an expired token, a close that never landed) — and the green chip then vouched for a
 * position nobody was watching.
 *
 * The backend (overview_open.annotate_live_open_row) now says, per OPEN row, whether the
 * guard's mark is fresh (`open_state` verified / unverified), how old it is
 * (`mark_age_s`) and whether the row was entered on an earlier day (`carried`,
 * `entry_day_ist`). Green is reserved for `verified`; a row with no `open_state` at all
 * (an older backend) is treated as unverified — unknown is never green.
 */

const MONTH_NAMES = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                     "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/** "2026-09-25" -> "25 Sep" (calendar arithmetic; null when it is not a real date). */
function dayLabel(iso) {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(iso || ""));
  if (!m) return null;
  const d = new Date(Date.UTC(Number(m[1]), Number(m[2]) - 1, Number(m[3])));
  if (Number.isNaN(d.getTime()) || d.getUTCFullYear() !== Number(m[1])
      || d.getUTCMonth() !== Number(m[2]) - 1 || d.getUTCDate() !== Number(m[3])) return null;
  return `${d.getUTCDate()} ${MONTH_NAMES[d.getUTCMonth()]}`;
}

/** "45s ago" / "12 min ago" / "3 h ago" / "2 d ago"; "never marked" when unknown. */
export function markAge(seconds) {
  if (seconds === null || seconds === undefined || seconds === "") return "never marked";
  const s = Number(seconds);
  if (!Number.isFinite(s) || s < 0) return "never marked";
  if (s < 90) return `${Math.round(s)}s ago`;
  if (s < 5400) return `${Math.round(s / 60)} min ago`;
  if (s < 172800) return `${Math.round(s / 3600)} h ago`;
  return `${Math.round(s / 86400)} d ago`;
}

/**
 * The status chip for one row of GET /live-broker/trade-history.
 * @returns {{label: string, tone: "live"|"unverified"|"closed"|"other", title: string}}
 */
export function tradeStatusChip(t) {
  const status = String(t?.status ?? "").toUpperCase();
  if (status === "CLOSED") return { label: "CLOSED", tone: "closed", title: "" };
  if (status !== "OPEN") {
    return { label: String(t?.status ?? "—") || "—", tone: "other", title: "" };
  }
  const since = t?.carried ? dayLabel(t?.entry_day_ist) : null;
  const carried = t?.carried ? ` · carried${since ? ` ${since}` : ""}` : "";
  if (t?.open_state === "verified") {
    return {
      label: `OPEN${carried}`, tone: "live",
      title: `The app's guard is marking this position (last mark ${markAge(t?.mark_age_s)}).`
        + (t?.carried ? " It was entered on an earlier day." : ""),
    };
  }
  return {
    label: `OPEN · unverified${carried}`, tone: "unverified",
    title: `The journal says OPEN, but the guard has no fresh mark on it (${markAge(t?.mark_age_s)}), `
      + "so nothing is vouching that this position still exists. Check the broker position book.",
  };
}

/**
 * The per-strategy "Open" cell of GET /live-broker/trade-stats.
 * @returns {{text: string, tone: "plain"|"warn", title: string}}
 */
export function openCountView(s) {
  const open = Number(s?.open_count) || 0;
  const unverified = Number(s?.open_unverified) || 0;
  const carried = Number(s?.open_carried) || 0;
  const bits = [];
  if (unverified > 0) bits.push(`${unverified} unverified`);
  if (carried > 0) bits.push(`${carried} carried`);
  const oldest = dayLabel(s?.open_carried_oldest);
  return {
    text: bits.length ? `${open} (${bits.join(", ")})` : String(open),
    tone: unverified > 0 || carried > 0 ? "warn" : "plain",
    title: bits.length
      ? "\"Unverified\": the guard has no fresh mark on the row, so the journal's OPEN is not "
        + "vouched for. \"Carried\": entered on an earlier day"
        + (oldest ? ` (oldest ${oldest})` : "") + "."
      : "",
  };
}

/** Tailwind classes for the chip, by tone. */
export function tradeChipClass(tone) {
  switch (tone) {
    case "live": return "border-emerald-500/40 text-emerald-300";
    case "unverified": return "border-amber-500/40 text-warning";
    default: return "border-line text-dim";
  }
}
