/**
 * Pure logic for the opt-in live alerts (fill / exit / refusal / blocked / halt).
 *
 * No JSX, no alias imports — loads under plain node and is tested by EXECUTING it
 * (tests/test_live_notify.py). The hook that polls, toasts, notifies and beeps lives
 * in components/live/useLiveNotifications.js and holds NO decisions: what counts as
 * an event, and what the stored settings mean, is decided here.
 *
 * Two rules shape the diff:
 *   - Silence on the first look. An alert is a CHANGE between two snapshots, so the
 *     first snapshot (prev === null) never emits — opening the page on a book that
 *     already holds positions is not "a fill".
 *   - Silence on the unknown. A deployment is diffed only when it is present in BOTH
 *     snapshots, and the account halt only when the account state was known in
 *     both. A status fetch that failed for one poll must not read, on recovery, as
 *     "every position just filled" or "the account just latched".
 */
import { isLiveDeployment, reasonText } from "./liveDeploymentView.js";

export const NOTIFY_STORAGE_KEY = "af.liveNotify.v1";

/** Opt-in: every channel is OFF until the operator turns it on. */
export const DEFAULT_NOTIFY_SETTINGS = Object.freeze({ enabled: false, desktop: false, sound: false });

/**
 * Settings from the raw stored string. Anything that is not a JSON object — absent,
 * malformed, an array, a bare number — is the defaults (all off); each flag is on
 * only when it is literally `true`, so a stored "yes" or 1 cannot enable a channel.
 */
export function parseNotifySettings(raw) {
  if (typeof raw !== "string" || raw === "") return { ...DEFAULT_NOTIFY_SETTINGS };
  let o;
  try {
    o = JSON.parse(raw);
  } catch {
    return { ...DEFAULT_NOTIFY_SETTINGS };
  }
  if (!o || typeof o !== "object" || Array.isArray(o)) return { ...DEFAULT_NOTIFY_SETTINGS };
  return { enabled: o.enabled === true, desktop: o.desktop === true, sound: o.sound === true };
}

export function serializeNotifySettings(s) {
  return JSON.stringify({
    enabled: s?.enabled === true, desktop: s?.desktop === true, sound: s?.sound === true,
  });
}

/**
 * Which channels actually fire. The sonner toast is ALWAYS on when alerts are on;
 * desktop and sound are sub-options that mean nothing while alerts are off.
 */
export function activeChannels(settings) {
  const on = settings?.enabled === true;
  return { toast: on, desktop: on && settings?.desktop === true, sound: on && settings?.sound === true };
}

/**
 * The outcome of the browser's permission answer for desktop notifications. Only
 * "granted" turns the channel on; "denied" and a dismissed prompt ("default") leave
 * it off, each with the reason the operator is shown.
 */
export function desktopPermissionOutcome(supported, permission) {
  if (!supported) {
    return { granted: false, reason: "This browser does not support desktop notifications." };
  }
  if (permission === "granted") return { granted: true, reason: null };
  if (permission === "denied") {
    return {
      granted: false,
      reason: "Desktop notifications are blocked for this site — allow them in the browser's "
        + "site settings (address-bar lock icon), then tick the box again.",
    };
  }
  return { granted: false, reason: "Desktop notifications were not allowed — the permission prompt was dismissed." };
}

const positionKey = (op) => {
  const id = op?.id ?? op?.tsym;
  return id === null || id === undefined || id === "" ? null : String(id);
};

/**
 * One comparable snapshot of the live book: per LIVE deployment (with a status
 * payload) its open position ids, today's order count, the last entry outcome key,
 * and the governor's binding reason; plus the account stops (any live governor).
 */
export function buildLiveSnapshot(deployLive, deployments) {
  const byId = deployLive && typeof deployLive === "object" ? deployLive : {};
  const out = {};
  const account = { known: false, latched: false, halted: false, haltReason: null };
  for (const dep of Array.isArray(deployments) ? deployments : []) {
    if (!isLiveDeployment(dep) || !dep?.id) continue;
    const st = byId[dep.id];
    if (!st || typeof st !== "object") continue;
    const open = Array.isArray(st.open_positions) ? st.open_positions : [];
    const tsyms = {};
    const openIds = [];
    for (const op of open) {
      const k = positionKey(op);
      if (k === null) continue;
      openIds.push(k);
      tsyms[k] = op?.tsym ? String(op.tsym) : k;
    }
    const le = st.last_entry && typeof st.last_entry === "object" ? st.last_entry : null;
    const err = le && le.error !== null && le.error !== undefined && le.error !== "" ? String(le.error) : null;
    const gov = st.governor && typeof st.governor === "object" && !st.governor.error ? st.governor : null;
    out[dep.id] = {
      name: dep.name || String(dep.id).slice(0, 8),
      openIds: openIds.sort(),
      tsyms,
      orders: Number.isFinite(Number(st.today?.orders)) ? Number(st.today.orders) : null,
      lastEntryKey: le ? `${le.signal_id ?? ""}|${err ?? ""}` : null,
      lastEntryError: err,
      binding: gov?.binding?.reason ? String(gov.binding.reason) : null,
    };
    const stops = gov?.account?.stops;
    if (stops && typeof stops === "object") {
      account.known = true;
      account.latched = account.latched || stops.latched === true;
      if (stops.engine_halted === true) {
        account.halted = true;
        account.haltReason = stops.engine_halt_reason ? String(stops.engine_halt_reason) : account.haltReason;
      }
    }
  }
  return { deployments: out, account };
}

const pretty = (s) => String(s).replace(/[_:]/g, " ").trim();

/**
 * The alerts that happened between two snapshots: `[{kind, deploymentId, name,
 * title, body, key}]`. `key` is stable per event so a desktop notification can be
 * de-duplicated by tag. An unchanged snapshot yields nothing — an alert fires
 * exactly once, on the poll where the state changed.
 */
export function diffLiveEvents(prev, next) {
  if (!prev || !next) return [];
  const events = [];
  const push = (kind, id, name, title, body, detail) => events.push({
    kind, deploymentId: id, name, title, body, key: `${kind}:${id ?? "account"}:${detail}`,
  });
  for (const [id, cur] of Object.entries(next.deployments || {})) {
    const was = prev.deployments?.[id];
    if (!was) continue;
    const before = new Set(was.openIds);
    const after = new Set(cur.openIds);
    for (const pid of cur.openIds) {
      if (before.has(pid)) continue;
      const orders = cur.orders === null ? "" : ` · ${cur.orders} order${cur.orders === 1 ? "" : "s"} today`;
      push("fill", id, cur.name, `Live fill — ${cur.name}`,
        `Position opened: ${cur.tsyms?.[pid] || pid}${orders}`, pid);
    }
    for (const pid of was.openIds) {
      if (after.has(pid)) continue;
      push("exit", id, cur.name, `Live exit — ${cur.name}`,
        `No longer held by the guard: ${was.tsyms?.[pid] || pid} — closed or squared. Check the blotter for the fill.`, pid);
    }
    if (cur.lastEntryKey !== was.lastEntryKey && cur.lastEntryError) {
      push("refusal", id, cur.name, `Entry refused — ${cur.name}`, pretty(cur.lastEntryError), cur.lastEntryKey);
    }
    if (cur.binding && cur.binding !== was.binding) {
      push("blocked", id, cur.name, `Entries blocked — ${cur.name}`,
        reasonText(cur.binding) || pretty(cur.binding), cur.binding);
    }
  }
  const a = prev.account;
  const b = next.account;
  if (a?.known && b?.known) {
    const why = [];
    if (!a.latched && b.latched) why.push("account safety latch set — entries blocked until it is reset");
    if (!a.halted && b.halted) why.push(`engine halted${b.haltReason ? `: ${pretty(b.haltReason)}` : ""}`);
    if (why.length) push("halt", null, null, "LIVE SAFETY HALT", why.join(" · "), why.join("|"));
  }
  return events;
}
