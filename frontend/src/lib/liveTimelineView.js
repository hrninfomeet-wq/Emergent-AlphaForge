/**
 * Pure formatting for the "Today's timeline" panel (GET /deployments/{id}/timeline).
 *
 * No imports, no JSX, no alias — loads under plain node and is tested by EXECUTING
 * it (tests/test_live_timeline_view.py). The server already merges and orders the
 * events; this module only turns them into rows the panel renders: an IST wall-clock
 * time, the label/detail text, a tone, and the gaps footnote.
 */

const IST_MS = 5.5 * 3600 * 1000;

/** IST HH:MM:SS from an ISO timestamp, or null when it cannot be read. */
export function istClock(ts) {
  const ms = typeof ts === "string" ? Date.parse(ts) : NaN;
  if (!Number.isFinite(ms)) return null;
  return new Date(ms + IST_MS).toISOString().slice(11, 19);
}

// Only what an operator should notice at a glance; everything else stays quiet.
const TONE = {
  refused: "danger", disabled: "danger", hold: "warn", caps: "warn",
  entry: "default", exit: "default", intended: "dim", order: "dim", state: "dim", signal: "default",
};

/**
 * `{rows, gaps, date, empty, unavailable}` from the timeline response.
 * A response that is not an object (a failed fetch that slipped through) is
 * `unavailable` — never rendered as "nothing happened today".
 */
export function formatTimeline(resp) {
  const ok = Boolean(resp) && typeof resp === "object" && !Array.isArray(resp);
  const events = ok && Array.isArray(resp.events) ? resp.events : [];
  const rows = events
    .filter((e) => e && typeof e === "object")
    .map((e, i) => ({
      key: `${i}:${e.ts}:${e.kind}`,
      time: istClock(e.ts) ?? "—",
      label: String(e.label ?? ""),
      detail: String(e.detail ?? ""),
      kind: String(e.kind ?? ""),
      tone: TONE[e.kind] || "dim",
    }));
  const gaps = ok && Array.isArray(resp.gaps)
    ? resp.gaps.filter((g) => typeof g === "string" && g.trim() !== "")
    : [];
  return {
    rows,
    gaps,
    date: ok && typeof resp.date === "string" ? resp.date : null,
    empty: ok && rows.length === 0,
    unavailable: !ok,
  };
}
