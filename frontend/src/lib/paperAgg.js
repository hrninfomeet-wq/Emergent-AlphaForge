// Pure client aggregations for the Paper page (computed from the filtered
// statsRows the page already fetches, so they respect the active filters).

export const EXIT_BUCKETS = ["target", "stop", "eod", "manual", "other"];

export const normalizeExitReason = (reason) => {
  const r = String(reason || "").toLowerCase();
  if (r.includes("target")) return "target";
  if (r.includes("stop")) return "stop";
  if (r.includes("eod") || r.includes("square") || r.includes("expiry")) return "eod";
  if (r.includes("manual")) return "manual";
  return "other";
};

// rows -> { counts, pct, total } over CLOSED trades only.
export const exitReasonBreakdown = (rows) => {
  const counts = { target: 0, stop: 0, eod: 0, manual: 0, other: 0 };
  let total = 0;
  for (const t of rows || []) {
    if (String(t.status || "").toUpperCase() !== "CLOSED") continue;
    counts[normalizeExitReason(t.exit_reason)] += 1;
    total += 1;
  }
  const pct = {};
  for (const k of EXIT_BUCKETS) pct[k] = total ? Math.round((counts[k] / total) * 100) : 0;
  return { counts, pct, total };
};
