// Robust, framework-agnostic extraction of a human-readable error message from
// any shape a FastAPI/axios error can take. Never returns "[object Object]" or
// a raw object — always a string.
//
// Handles:
//   (a) error.response.data.detail is a plain string            -> use it
//   (b) error.response.data.detail is a FastAPI 422 validation
//       array of {loc, msg, type}                                -> joined, readable
//   (c) error.response.data.detail is an object with .message    -> use that
//   (d) error.response.data itself has a top-level .message      -> use that
//   (e) otherwise fall back to error.message
//   (f) otherwise fall back to the fallback string

// One FastAPI validation-error item -> "field: message".
function formatValidationItem(item) {
  if (!item || typeof item !== "object") return String(item);
  const loc = Array.isArray(item.loc) ? item.loc.filter((p) => p !== "body").join(".") : item.loc;
  const msg = item.msg || item.message || "Invalid value";
  return loc ? `${loc}: ${msg}` : String(msg);
}

export function getApiErrorMessage(error, fallback = "Something went wrong") {
  const data = error?.response?.data;
  const detail = data?.detail;

  if (typeof detail === "string" && detail.trim()) return detail;

  if (Array.isArray(detail) && detail.length > 0) {
    const joined = detail.map(formatValidationItem).filter(Boolean).join("; ");
    if (joined) return joined;
  }

  if (detail && typeof detail === "object" && typeof detail.message === "string" && detail.message.trim()) {
    return detail.message;
  }

  if (data && typeof data === "object" && typeof data.message === "string" && data.message.trim()) {
    return data.message;
  }

  if (typeof error?.message === "string" && error.message.trim()) return error.message;

  return fallback;
}

// ---------------------------------------------------------------------------
// Schema errors are for developers, not operators.
//
// A FastAPI 422 reaches the UI as e.g.
//   {"type":"dict_type","loc":["body","risk"],"msg":"Input should be a valid dictionary"}
// and the generic formatter renders that verbatim: "risk: Input should be a
// valid dictionary". That is exactly what the deploy wizard showed when it sent
// `risk: null`, and it tells the operator nothing they can act on.
//
// This maps the shapes we actually emit onto sentences that name the control the
// user sees. Anything unmapped falls through to the generic formatter, so a new
// backend field degrades to the old behaviour rather than to silence.
// ---------------------------------------------------------------------------

const FIELD_LABELS = {
  risk: "Exit / Risk controls",
  friction: "Execution realism",
  option_moneyness: "Option selection",
  dte_filter: "Expiry (DTE) filter",
  default_lots: "Lots per signal",
  lots_override: "Lots per signal",
  capital_amount: "Paper capital",
  source_id: "Source",
  name: "Deployment name",
  pretrade_profile: "Pre-trade profile",
  mode: "Mode",
};

const TYPE_HINTS = {
  dict_type: "expected an object — this is an app bug, not a setting you can change.",
  list_type: "expected a list — this is an app bug, not a setting you can change.",
  missing: "is required.",
  int_parsing: "must be a whole number.",
  float_parsing: "must be a number.",
  greater_than: "is too small.",
  greater_than_equal: "is too small.",
  less_than: "is too large.",
  less_than_equal: "is too large.",
  string_type: "must be text.",
  bool_type: "must be true or false.",
};

/** One 422 item -> an operator-facing sentence, or null when unmapped. */
export function friendlyValidationItem(item) {
  if (!item || typeof item !== "object") return null;
  const loc = Array.isArray(item.loc) ? item.loc.filter((p) => p !== "body") : [];
  if (!loc.length) return null;
  const label = FIELD_LABELS[loc[0]];
  const hint = TYPE_HINTS[item.type];
  if (!label || !hint) return null;
  const path = loc.length > 1 ? ` (${loc.slice(1).join(".")})` : "";
  return `${label}${path} ${hint}`;
}

/**
 * Operator-facing message for a failed deployment.
 *
 * Prefers a mapped, field-specific sentence; falls back to the backend's own
 * 400 text (which for exit/risk values is already actionable — e.g.
 * "breakeven.trigger must be in (0, 1) for unit=pct"); and only then to the
 * generic formatter.
 */
export function getDeploymentErrorMessage(error, fallback = "Deployment failed") {
  const detail = error?.response?.data?.detail;
  if (Array.isArray(detail) && detail.length) {
    const mapped = detail.map(friendlyValidationItem).filter(Boolean);
    if (mapped.length) return mapped.join(" ");
  }
  return getApiErrorMessage(error, fallback);
}
