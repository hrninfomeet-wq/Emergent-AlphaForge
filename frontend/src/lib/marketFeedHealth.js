/**
 * What the global market header says about its feed. No imports, no JSX — loads
 * under node (tests/test_market_feed_health.py executes it).
 *
 * The header used to read `snapshot.source_mode === "live_ticks" || stream.running`
 * and paint a green "LIVE TICKS · Upstox WebSocket". `stream.running` is the TASK
 * being alive, which stays true through every backoff-and-retry after the socket
 * drops (the loop retries forever), so a dead feed kept a green icon on every page.
 *
 * The backend status now carries the facts (`connected`, `last_tick_age_s`,
 * `last_error`, `reconnect_count` — `last_tick_age_s` is computed server-side so the
 * viewer's own clock cannot move it). This module turns them into ONE verdict:
 *
 *   ok       green  — socket open AND a tick within TICK_STALE_S AND the header's
 *                     own quotes really are WS ticks
 *   warn     amber  — reconnecting / connected but silent / stale / unverifiable
 *   bad      red    — the market-header API itself is unreachable
 *   neutral  grey   — no stream, quotes from the REST fallback (normal, not "live")
 *
 * Unknown is never green: a missing stream status, or a status that does not say
 * `connected`, cannot produce "live ticks".
 */

/** Seconds without a tick after which an open socket is called stale. */
export const TICK_STALE_S = 30;

/** "45s" / "3m 05s" / "1h 02m". Negative or non-finite -> "—". */
export function fmtAge(seconds) {
  // Number(null) is 0 — "0s" would read as a tick a moment ago. Unknown is unknown.
  if (seconds === null || seconds === undefined || seconds === "") return "—";
  const n = Number(seconds);
  if (!Number.isFinite(n) || n < 0) return "—";
  const t = Math.floor(n);
  const h = Math.floor(t / 3600);
  const m = Math.floor((t % 3600) / 60);
  const s = t % 60;
  if (h > 0) return `${h}h ${String(m).padStart(2, "0")}m`;
  if (m > 0) return `${m}m ${String(s).padStart(2, "0")}s`;
  return `${s}s`;
}

/**
 * @param {object} p
 * @param {boolean} p.loading        first snapshot not yet received
 * @param {string}  p.error          non-empty when the market-header API failed
 * @param {object}  p.snapshot       { source_mode, items, ... }
 * @param {object|null} p.streamStatus  GET /upstox/stream/status, null = unknown
 * @param {string}  p.quotesText     the "N/M quotes" text for the non-live states
 * @returns {{text: string, detail: string, tone: "ok"|"warn"|"bad"|"neutral",
 *            live: boolean, title: string}}
 */
export function deriveFeedIndicator({ loading, error, snapshot, streamStatus, quotesText }) {
  const quotes = quotesText || "quotes";

  if (error) {
    return { text: "offline", detail: "market header unavailable", tone: "bad",
             live: false, title: String(error) };
  }
  if (loading) {
    return { text: "loading", detail: "", tone: "neutral", live: false, title: "" };
  }
  if (!streamStatus || typeof streamStatus !== "object") {
    // The stream's own status could not be read. The snapshot's source_mode is
    // freshness-checked server-side but says nothing about whether the socket is
    // still up, so it is not enough to claim a live feed.
    return { text: quotes, detail: "stream status unavailable", tone: "neutral",
             live: false, title: "The Upstox stream status could not be read." };
  }
  if (!streamStatus.running) {
    return { text: quotes, detail: "API fallback", tone: "neutral", live: false,
             title: "The Upstox tick stream is not running; quotes come from the REST API." };
  }

  // The task is alive. Is the socket?
  const lastError = streamStatus.last_error ? String(streamStatus.last_error) : "";
  const attempts = Number(streamStatus.reconnect_count) || 0;
  if (streamStatus.connected === false) {
    if (!lastError && attempts === 0) {
      return { text: "connecting", detail: "Upstox WebSocket", tone: "neutral",
               live: false, title: "Opening the Upstox WebSocket." };
    }
    return {
      text: "reconnecting",
      detail: lastError ? `Upstox WebSocket down: ${lastError}` : "Upstox WebSocket down",
      tone: "warn", live: false,
      title: `The Upstox WebSocket is down and retrying (${attempts} reconnect${attempts === 1 ? "" : "s"}).`
        + (lastError ? ` Last error: ${lastError}` : ""),
    };
  }
  if (streamStatus.connected !== true) {
    return { text: "unverified", detail: "stream health unknown", tone: "warn",
             live: false, title: "The stream status did not report whether the socket is connected." };
  }

  // Socket open. Are ticks actually arriving?
  const age = streamStatus.last_tick_age_s;
  const ageNum = age === null || age === undefined || age === "" ? null : Number(age);
  if (ageNum === null || !Number.isFinite(ageNum)) {
    return { text: "no ticks", detail: "Upstox WebSocket connected · no tick received yet",
             tone: "warn", live: false,
             title: "The socket is open but no tick has arrived since it started." };
  }
  if (ageNum > TICK_STALE_S) {
    return { text: `stale ${fmtAge(ageNum)}`, detail: `Upstox WebSocket · last tick ${fmtAge(ageNum)} ago`,
             tone: "warn", live: false,
             title: `The socket is open but the last tick was ${fmtAge(ageNum)} ago.` };
  }
  // Fresh ticks. The header's OWN quotes must also be WS ticks before it says so.
  if (snapshot?.source_mode !== "live_ticks") {
    return { text: quotes, detail: "stream connected · header quotes from API", tone: "neutral",
             live: false,
             title: "Ticks are arriving, but none of them is one of the header instruments." };
  }
  return { text: "live ticks", detail: "Upstox WebSocket", tone: "ok", live: true,
           title: `Last tick ${fmtAge(ageNum)} ago.` };
}

/** Tailwind classes for the indicator icon, by tone. */
export function feedToneClass(tone) {
  switch (tone) {
    case "ok": return "text-emerald-400";
    case "warn": return "text-amber-400";
    case "bad": return "text-red-400";
    default: return "text-dimmer";
  }
}
