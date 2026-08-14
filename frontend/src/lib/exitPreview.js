/**
 * Render the backend's `exit_preview` as display rows for the enable dialog.
 *
 * Audit finding [17]. On 2026-08-14 a deployment went live with a 50% deep-default
 * premium stop, no premium target, and a trail the live path silently discarded —
 * none of it shown anywhere. This turns the backend's preview into the rows the
 * confirm step lists.
 *
 * Pure and framework-free ON PURPOSE, so it can be driven from node instead of
 * inspected as JSX. All policy (precedence, deep-default detection, the notices)
 * lives in `backend/app/live_exit_preview.py`, computed by the SAME resolver the
 * arm path uses; this file only formats what it is handed and must never re-derive
 * a level, or it becomes the second source of truth the backend module exists to
 * avoid.
 */

/** Trim a trailing `.0` so 10 renders as "10", not "10.0". */
function num(v) {
  if (v === null || v === undefined || v === "" || !Number.isFinite(Number(v))) return null;
  return Number(v);
}

function fmt(v) {
  const n = num(v);
  if (n === null) return null;
  return String(Number(n.toFixed(4)));
}

/**
 * One leg (stop or target) as display text.
 * `pts` wins over `pct`, mirroring the backend resolver's own precedence.
 */
export function formatLeg(leg) {
  if (!leg) return "—";
  const pts = fmt(leg.pts);
  const pct = fmt(leg.pct);
  const level = fmt(leg.level);
  let base;
  if (pts !== null) base = `${pts} pts of premium`;
  else if (pct !== null) base = `${pct}% of premium`;
  else return "none";
  return level !== null ? `${base} (₹${level})` : base;
}

/** Human label for where a level came from. */
export function sourceLabel(source) {
  switch (source) {
    case "strategy_hint": return "from the strategy";
    case "deployment_pts": return "from this deployment";
    case "deployment_pct": return "from this deployment";
    case "deep_default": return "deep default — nothing configured";
    case "none": return "";
    default: return "source unknown";
  }
}

/**
 * The rows the confirm step renders, in order.
 * Returns `[]` for a missing preview so the caller can skip the whole card.
 */
export function exitRows(preview) {
  if (!preview || typeof preview !== "object") return [];
  const rows = [];

  const stop = preview.premium_stop || {};
  rows.push({
    key: "stop",
    label: "Premium stop",
    value: formatLeg(stop),
    note: sourceLabel(stop.source),
    // The one row that must draw the eye: a stop nobody chose.
    danger: Boolean(stop.is_deep_default),
  });

  const target = preview.premium_target || {};
  rows.push({
    key: "target",
    label: "Premium target",
    value: target.present ? formatLeg(target) : "none",
    note: target.present ? sourceLabel(target.source) : "",
    danger: !target.present,
  });

  const trail = preview.trail || {};
  rows.push({
    key: "trail",
    label: "Trail / breakeven",
    value: trail.present ? trail.summary : "none",
    note: "",
    danger: false,
  });

  const spot = preview.spot_exit || {};
  if (spot.present) {
    const t = fmt(spot.target_pts);
    const s = fmt(spot.stop_pts);
    const parts = [];
    if (t !== null) parts.push(`target ${t} pts`);
    if (s !== null) parts.push(`stop ${s} pts`);
    rows.push({ key: "spot", label: "Spot mirror", value: parts.join(" · "),
                note: "on the underlying", danger: false });
  }

  const ts = num(preview.time_stop_minutes);
  if (ts !== null) {
    rows.push({ key: "time", label: "Time stop", value: `${fmt(ts)} min`,
                note: "", danger: false });
  }

  rows.push({
    key: "eod",
    label: "EOD square",
    value: `${preview.eod_square_ist || "15:00"} IST`,
    note: "always applies",
    danger: false,
  });
  return rows;
}

/** Advisory notices, worst first. Never blocking — arming stays the operator's call. */
export function exitNotices(preview) {
  const notices = (preview && Array.isArray(preview.notices)) ? preview.notices : [];
  const rank = { warn: 0, info: 1 };
  return notices
    .filter((n) => n && n.id && n.message)
    .slice()
    .sort((a, b) => (rank[a.severity] ?? 9) - (rank[b.severity] ?? 9));
}

/** True when the preview is worth drawing at all. */
export function hasExitPreview(preview) {
  return exitRows(preview).length > 0;
}
