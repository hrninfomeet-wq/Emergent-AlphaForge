// Shared exit-reason classification for the Paper blotter. Maps a raw backend
// exit_reason string to {bucket, label}. Precedence (first match wins):
// target > manual > eod > stop(not time_stop) > other. Mirrors the backend
// normalize_exit_reason (paper_analytics.py) and paperAgg.normalizeExitReason —
// keep all three in lockstep. "manual" is checked before "eod" because
// "manual_square_off" contains both "manual" and "square" and is a user-initiated
// close, not End-of-day; "time_stop" is a time exit, not a price stop.
export function classifyExitReason(raw) {
  const r = String(raw || "").toLowerCase();
  if (r.includes("target")) return { bucket: "target", label: "Target achieved" };
  if (r.includes("manual")) return { bucket: "manual", label: "Manual" };
  if (r.includes("eod") || r.includes("square") || r.includes("expiry")) return { bucket: "eod", label: "End of day" };
  if (r !== "time_stop" && r.includes("stop")) return { bucket: "stop", label: "Stoploss hit" };
  return { bucket: "other", label: "Others" };
}

// Ordered options for the Exit Reason header filter (value = backend bucket key).
export const EXIT_REASON_OPTIONS = [
  { value: "target", label: "Target achieved" },
  { value: "stop", label: "Stoploss hit" },
  { value: "eod", label: "End of day" },
  { value: "manual", label: "Manual" },
  { value: "other", label: "Others" },
];
