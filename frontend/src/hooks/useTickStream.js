import { useCallback, useEffect, useRef, useState } from "react";
import { API } from "@/lib/api";

/**
 * useTickStream — subscribe to a server tick stream (SSE), with the existing
 * polling path kept as an automatic fallback.
 *
 * WHY: the money values on /live-trading and /paper were polled at 15s and 2s.
 * Measured tick-to-pixel was p50 7.8s / p95 14.5s (live) and p50 1.3s / p95 2.2s
 * (paper) — and ~99% of that was the poll interval itself, not server work
 * (per-request recompute measured 0.02ms). Pushing on the tick removes the wait.
 *
 * DEGRADE, NEVER GO DARK. Three independent failure paths all land back on
 * polling, because a stale number on a trading screen is worse than a slow one:
 *   1. No EventSource in the browser      -> poll immediately.
 *   2. EventSource errors / disconnects   -> poll until a snapshot arrives again.
 *   3. Stream open but SILENT             -> the watchdog below.
 *
 * (3) is the one that is easy to miss. A proxy can hold a connection open while
 * delivering nothing, and `onerror` never fires — the page would sit on a frozen
 * price looking perfectly healthy. The server therefore emits a real `heartbeat`
 * EVENT every 15s (not an SSE `: comment`, which EventSource discards without
 * firing any listener), and any gap longer than `staleMs` trips the fallback.
 *
 * Returns { data, source, connected, lastAt, error }, where `source` is
 * "stream" | "poll" | null so callers can label the freshness honestly.
 */
const DEFAULT_STALE_MS = 25_000; // > the server heartbeat (15s) + slack
const LATENCY_RING = 300;

/**
 * Tick-to-screen latency, recorded into `window.__tickLatency[path]`.
 *
 * The server stamps `wake_ingest_ts_ms`: the local clock at which the OLDEST tick
 * in this emit's batch was received off the Upstox socket — the tick that waited
 * longest, so the number is the worst case within the batch and includes the
 * server-side coalescing window.
 *
 * Opened here (on the SSE message) and closed in a useEffect after React commits,
 * so the measurement ends when the value is actually in the DOM.
 *
 * Costs one object push per update (~10/s). Kept always-on because "how stale is
 * this number?" is the question this whole path exists to answer, and it is
 * unanswerable after the fact without it. Backend and browser share the host
 * clock (the backend runs in Docker on this machine).
 */
function ringFor(path) {
  const store = (window.__tickLatency = window.__tickLatency || {});
  return (store[path] = store[path] || []);
}

function recordLatency(path, payload) {
  const wake = payload && payload.wake_ingest_ts_ms;
  if (!wake) return null; // connect snapshot / heartbeat: no triggering tick
  try {
    return {
      wake,
      received_at: Date.now(),
      recv_ms: Date.now() - wake,
      emit_ms: payload.emitted_at_ms ? payload.emitted_at_ms - wake : null,
      ticks: payload.wake_tick_count || 1,
    };
  } catch (e) {
    return null; // instrumentation must never break the page
  }
}

/**
 * Close the measurement once React has actually put the value in the DOM.
 *
 * Called from a useEffect keyed on `data`, which runs after commit. We measure
 * to COMMIT rather than to a requestAnimationFrame paint on purpose: rAF is
 * throttled by the compositor (an offscreen or background tab can stall it for
 * seconds), which would report the browser's frame scheduling as if it were
 * pipeline latency. Commit is when the number is in the document; a visible
 * browser adds one display frame (~16ms at 60Hz) on top.
 */
function closeLatency(path, pending) {
  if (!pending) return;
  try {
    const ring = ringFor(path);
    ring.push({
      ...pending,
      committed_at: Date.now(),
      tick_to_commit_ms: Date.now() - pending.wake,
    });
    if (ring.length > LATENCY_RING) ring.splice(0, ring.length - LATENCY_RING);
  } catch (e) {
    /* instrumentation must never break the page */
  }
}

export function useTickStream(
  path,
  { fallback, fallbackMs = 5_000, staleMs = DEFAULT_STALE_MS, enabled = true } = {},
) {
  const [data, setData] = useState(null);
  const [source, setSource] = useState(null);
  const [error, setError] = useState(null);
  const [lastAt, setLastAt] = useState(null);

  // Kept in refs so changing the fallback closure never tears down the stream.
  const fallbackRef = useRef(fallback);
  fallbackRef.current = fallback;
  const lastMsgRef = useRef(0);
  const pendingLatencyRef = useRef(null);

  const stable = useCallback(() => {}, []);

  useEffect(() => {
    if (!enabled) return undefined;

    let cancelled = false;
    let es = null;
    let pollTimer = null;
    let watchdog = null;

    const poll = async () => {
      if (cancelled || !fallbackRef.current) return;
      try {
        const next = await fallbackRef.current();
        if (cancelled) return;
        setData(next);
        setSource("poll");
        setLastAt(Date.now());
        setError(null);
      } catch (e) {
        if (!cancelled) setError(e);
      }
    };

    const startPolling = () => {
      if (pollTimer || cancelled || !fallbackRef.current) return;
      poll();
      pollTimer = window.setInterval(poll, fallbackMs);
    };

    const stopPolling = () => {
      if (pollTimer) {
        window.clearInterval(pollTimer);
        pollTimer = null;
      }
    };

    const onMessage = (evt, isSnapshot) => {
      if (cancelled) return;
      lastMsgRef.current = Date.now();
      if (!isSnapshot) return; // heartbeat: liveness only, no payload
      try {
        const parsed = JSON.parse(evt.data);
        setData(parsed);
        setSource("stream");
        setLastAt(Date.now());
        setError(null);
        stopPolling(); // the stream is delivering again
        pendingLatencyRef.current = recordLatency(path, parsed);
      } catch (e) {
        /* malformed frame: keep the last good value, SSE keeps flowing */
      }
    };

    if (typeof EventSource === "undefined") {
      startPolling();
      return () => {
        cancelled = true;
        stopPolling();
      };
    }

    try {
      es = new EventSource(`${API}${path}`);
      lastMsgRef.current = Date.now();
      es.addEventListener("snapshot", (e) => onMessage(e, true));
      es.addEventListener("heartbeat", (e) => onMessage(e, false));
      es.addEventListener("stream_error", () => {
        // The server reached us but could not build a payload (e.g. a broker
        // blip). Poll alongside until snapshots resume.
        startPolling();
      });
      es.onerror = () => {
        // EventSource auto-reconnects; poll while it is down.
        if (!cancelled) startPolling();
      };
    } catch (e) {
      startPolling();
    }

    // Watchdog: catches an open-but-silent stream, which onerror never reports.
    watchdog = window.setInterval(() => {
      if (cancelled) return;
      if (Date.now() - lastMsgRef.current > staleMs) startPolling();
    }, Math.max(1_000, Math.floor(staleMs / 5)));

    return () => {
      cancelled = true;
      if (es) es.close();
      stopPolling();
      if (watchdog) window.clearInterval(watchdog);
    };
  }, [path, fallbackMs, staleMs, enabled, stable]);

  // Runs after React has committed `data` to the DOM — the honest end of the
  // tick-to-pixel measurement (see closeLatency).
  useEffect(() => {
    if (pendingLatencyRef.current) {
      closeLatency(path, pendingLatencyRef.current);
      pendingLatencyRef.current = null;
    }
  }, [data, path]);

  return { data, source, connected: source === "stream", lastAt, error };
}
