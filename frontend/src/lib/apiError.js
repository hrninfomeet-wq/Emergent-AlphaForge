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
