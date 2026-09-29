/**
 * What the Live page says about the 08:45 IST pre-open readiness verdict. No imports,
 * no JSX — loads under node (tests/test_preopen_readiness_view.py executes it).
 *
 * The backend check (runtime._run_preopen_readiness) detects things like "Flattrade
 * session expired while live deployments are armed" and persists the verdict — and,
 * until this, nothing displayed it. The opposite failure is just as real: a verdict is
 * computed ONCE at 08:45 and then sits in the database, so yesterday's NOT READY, or
 * a blocker the operator has since fixed, must never read as the current state. So:
 *
 *   - nothing is shown unless the verdict has a blocker or a warning (a green day is
 *     not a banner);
 *   - a verdict from an earlier IST day is shown muted, dated, and labelled as not
 *     today's — never in the alarm colours;
 *   - today's blockers the operator has since fixed (the broker chips now say
 *     connected) are dropped, so a resolved morning problem does not linger.
 *
 * Unknown stays unknown: `resolved` is only ever true on positive evidence.
 */

const IST_OFFSET_MS = (5 * 60 + 30) * 60 * 1000;
const DAY_NAMES = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
const MONTH_NAMES = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                     "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const pad2 = (n) => String(n).padStart(2, "0");

const LABELS = {
  flattrade_not_connected: "Flattrade not connected",
  flattrade_token_expired: "Flattrade session expired",
  upstox_not_connected: "Upstox not connected",
  upstox_token_expired: "Upstox token expired",
  warehouse_actions_pending: "Warehouse actions pending",
};

/** "YYYY-MM-DD" -> "Fri 25 Sep", by calendar arithmetic (no timezone involved). */
export function fmtSessionDate(iso) {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(iso || ""));
  if (!m) return null;
  const d = new Date(Date.UTC(Number(m[1]), Number(m[2]) - 1, Number(m[3])));
  // Date.UTC rolls an impossible date over ("2026-13-40" -> 9 Feb 2027): require the
  // parts to survive the round trip so a bad date is null, never a plausible wrong one.
  if (Number.isNaN(d.getTime()) || d.getUTCFullYear() !== Number(m[1])
      || d.getUTCMonth() !== Number(m[2]) - 1 || d.getUTCDate() !== Number(m[3])) return null;
  return `${DAY_NAMES[d.getUTCDay()]} ${d.getUTCDate()} ${MONTH_NAMES[d.getUTCMonth()]}`;
}

/** An ISO instant -> "08:45" in IST; null when unparseable. */
export function fmtIstTime(iso) {
  const t = Date.parse(String(iso || ""));
  if (!Number.isFinite(t)) return null;
  const d = new Date(t + IST_OFFSET_MS);
  return `${pad2(d.getUTCHours())}:${pad2(d.getUTCMinutes())}`;
}

/**
 * Is the Upstox token good right now, per the live-feed health payload?
 * true / false / null (feed health not loaded — unknown, never assumed).
 */
export function upstoxConnectedFrom(feedHealth) {
  const token = feedHealth && typeof feedHealth === "object" ? feedHealth.token : null;
  if (!token || typeof token !== "object") return null;
  return Boolean(token.connected) && !token.expired;
}

/**
 * @param {object|null} resp   GET /live-broker/preopen-readiness
 * @param {{flattradeConnected?: boolean|null, upstoxConnected?: boolean|null}} [now]
 *        what the broker chips say RIGHT NOW (null/undefined = unknown)
 * @returns {null | {tone: "danger"|"warn"|"muted", isToday: boolean, title: string,
 *                   sub: string, items: Array<{id: string, kind: "blocker"|"warning",
 *                   label: string, detail: string}>, resolved: string[]}}
 */
export function preopenView(resp, now = {}) {
  const v = resp && typeof resp === "object" ? resp.verdict : null;
  if (!v || typeof v !== "object") return null;
  if (v.reason === "not_a_trading_day") return null;

  const blockers = Array.isArray(v.blockers) ? v.blockers : [];
  const warnings = Array.isArray(v.warnings) ? v.warnings : [];

  // Only a verdict for TODAY can be "resolved since". An unknown is_today is not today.
  const isToday = resp.is_today === true;
  const dateText = fmtSessionDate(v.session_date);
  const timeText = fmtIstTime(v.evaluated_at);

  const resolvedNow = (id) => {
    if (!isToday) return false;
    const s = String(id || "");
    if (s.startsWith("flattrade_")) return now.flattradeConnected === true;
    if (s.startsWith("upstox_")) return now.upstoxConnected === true;
    return false;
  };

  const resolved = [];
  const items = [];
  for (const b of blockers) {
    const id = String((b && b.id) || "unknown");
    if (resolvedNow(id)) { resolved.push(id); continue; }
    items.push({ id, kind: "blocker", label: LABELS[id] || id, detail: String((b && b.detail) || "") });
  }
  for (const w of warnings) {
    const id = String((w && w.id) || "unknown");
    items.push({ id, kind: "warning", label: LABELS[id] || id, detail: String((w && w.detail) || "") });
  }
  // A clean verdict (nothing flagged), or every blocker since fixed and nothing else
  // to say: not a banner.
  if (items.length === 0) return null;

  const hasBlocker = items.some((i) => i.kind === "blocker");
  if (isToday) {
    const at = timeText ? `${timeText} IST today` : "today";
    return {
      tone: hasBlocker ? "danger" : "warn",
      isToday: true,
      title: `Pre-open check (${at}): ${hasBlocker ? "NOT READY" : "warnings"}`,
      sub: "Checked once before the open — this is that morning verdict, not a live re-check."
        + (resolved.length ? ` Since resolved: ${resolved.map((r) => LABELS[r] || r).join(", ")}.` : ""),
      items, resolved,
    };
  }

  const ago = Number.isFinite(Number(resp.days_ago)) && Number(resp.days_ago) > 0
    ? ` (${Number(resp.days_ago)} day${Number(resp.days_ago) === 1 ? "" : "s"} ago)` : "";
  const when = dateText ? `${dateText}${ago}` : "an earlier day";
  return {
    tone: "muted",
    isToday: false,
    title: `Previous pre-open check — ${when}, NOT today's`,
    sub: "No pre-open check has been recorded for today. What follows is what that earlier check found, "
      + "not the current state.",
    items, resolved,
  };
}

/** Tailwind classes for the banner tone. */
export function preopenToneClass(tone) {
  switch (tone) {
    case "danger": return "border-danger bg-danger/15 text-danger";
    case "warn": return "border-amber-500 bg-amber-500/15 text-warning";
    default: return "border-line bg-bg-2/40 text-dim";
  }
}
