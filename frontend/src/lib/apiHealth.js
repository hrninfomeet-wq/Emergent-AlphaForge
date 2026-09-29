/**
 * Is the backend actually reachable? The sidebar footer's verdict. No imports, no JSX
 * — loads under node (tests/test_api_health.py executes it).
 *
 * The footer used to be a hard-coded green dot beside "local API live". The frontend
 * is a static bundle served independently of the backend, so when the backend
 * container crashed or restarted the dot stayed green on every page. It is now driven
 * by a light periodic GET /api/health (a Mongo ping) folded through nextApiHealth:
 *
 *   checking  grey    no answer yet — never claimed live before one arrives
 *   up        green   the last check succeeded
 *   degraded  amber   one missed check (a busy backend can time a ping out), OR the
 *                     API answered but reported its database unhealthy (HTTP error)
 *   down      red     two or more consecutive checks got no answer at all
 *
 * The elapsed time since the last success is measured on the CLIENT clock against
 * the CLIENT clock, so a skewed PC clock cannot move it.
 */

/** Consecutive no-answer checks before amber becomes red. */
export const DOWN_AFTER_FAILURES = 2;

export function initialApiHealth() {
  return { status: "checking", lastOkAt: null, failures: 0, detail: "" };
}

/**
 * Fold one check result into the state.
 * @param {object} prev
 * @param {{ok: boolean, httpStatus?: number|null, detail?: string}} result
 *   ok=true: the API answered 2xx. ok=false + httpStatus: it answered with an error
 *   (reachable, unhealthy). ok=false without httpStatus: no answer (network / timeout).
 * @param {number} nowMs client clock, epoch ms
 */
export function nextApiHealth(prev, result, nowMs) {
  const p = prev && typeof prev === "object" ? prev : initialApiHealth();
  if (result && result.ok === true) {
    return { status: "up", lastOkAt: Number(nowMs), failures: 0, detail: "" };
  }
  const httpStatus = result ? Number(result.httpStatus) : NaN;
  const detail = String((result && result.detail) || "").slice(0, 160);
  if (Number.isFinite(httpStatus) && httpStatus > 0) {
    // It ANSWERED — so it is reachable — but said it is unhealthy (e.g. 503: Mongo down).
    return { status: "degraded", lastOkAt: p.lastOkAt, failures: 0,
             detail: detail || `API answered HTTP ${httpStatus}` };
  }
  const failures = (Number(p.failures) || 0) + 1;
  return {
    status: failures >= DOWN_AFTER_FAILURES ? "down" : "degraded",
    lastOkAt: p.lastOkAt, failures,
    detail: detail || "no response from the API",
  };
}

/** "45s ago" / "3m ago" / "2h ago"; "never" when there was no success. */
export function fmtSince(lastOkAt, nowMs) {
  if (lastOkAt === null || lastOkAt === undefined || lastOkAt === "") return "never";
  const t = Number(lastOkAt);
  const now = Number(nowMs);
  if (!Number.isFinite(t) || !Number.isFinite(now)) return "never";
  const s = Math.max(0, Math.floor((now - t) / 1000));
  if (s < 60) return `${s}s ago`;
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  return `${Math.floor(s / 3600)}h ago`;
}

/**
 * @returns {{tone: "neutral"|"ok"|"warn"|"bad", label: string, title: string}}
 */
export function describeApiHealth(state, nowMs) {
  const s = state && typeof state === "object" ? state : initialApiHealth();
  const since = fmtSince(s.lastOkAt, nowMs);
  switch (s.status) {
    case "up":
      return { tone: "ok", label: "local API live", title: "The backend answered its last health check." };
    case "degraded":
      return {
        tone: "warn",
        label: s.failures > 0 ? "API not responding" : "API unhealthy",
        title: `${s.detail || "The backend health check is failing."} Last OK: ${since}.`,
      };
    case "down":
      return {
        tone: "bad",
        label: `local API unreachable · last OK ${since}`,
        title: `${s.detail || "No response from the backend."} The page is showing data the backend can no longer refresh.`,
      };
    default:
      return { tone: "neutral", label: "checking API…", title: "Waiting for the first backend health check." };
  }
}

/** Tailwind background class for the dot. */
export function apiDotClass(tone) {
  switch (tone) {
    case "ok": return "bg-emerald-500";
    case "warn": return "bg-amber-400";
    case "bad": return "bg-red-500";
    default: return "bg-slate-500";
  }
}
