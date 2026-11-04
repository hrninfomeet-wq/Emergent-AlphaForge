import { useCallback, useEffect, useRef, useState } from "react";
import { chartOptionApi as api } from "../../lib/chartOptionApi";
import { finiteValue } from "./chartPresentation";

const STREAM_TIMEOUT_MS = 12000;
const RADIUS = 20;
const tradingDate = (now) => new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Kolkata", year: "numeric", month: "2-digit", day: "2-digit" }).format(now);
const messages = (value) => Array.isArray(value) ? value.filter((item) => typeof item === "string") : [];
const expiryList = (value) => Array.isArray(value) ? [...new Set(value.filter((date) => typeof date === "string" && /^\d{4}-\d{2}-\d{2}$/.test(date)))].sort() : [];

export default function useChartOptionChain(instrument) {
  const key = instrument?.instrument_key || "";
  const [metadata, setMetadata] = useState(null);
  const [selection, setSelection] = useState(null);
  const [result, setResult] = useState(null);
  const [metaRetry, setMetaRetry] = useState(0), [refreshId, setRefreshId] = useState(0);
  const [metaLoading, setMetaLoading] = useState(true), [loading, setLoading] = useState(false);
  const [metaError, setMetaError] = useState(""), [error, setError] = useState("");
  const [transport, setTransport] = useState({ healthy: false, receivedAt: 0 });
  const [streamWarnings, setStreamWarnings] = useState([]);
  const [now, setNow] = useState(Date.now);
  const current = useRef(null);
  const today = tradingDate(now);
  const expiry = selection?.key === key ? selection.expiry : "";
  const center = selection?.key === key ? selection.center : null;
  const identity = JSON.stringify([key, expiry, center]);
  const data = result?.identity === identity ? result.value : null;

  useEffect(() => {
    let disposed = false;
    const controller = new AbortController();
    setMetadata(null); setMetaLoading(true); setMetaError("");
    if (!key) { setMetaError("Choose an instrument to view its option chain."); setMetaLoading(false); return () => controller.abort(); }
    api.expiries(key, { signal: controller.signal }).then((value) => {
      if (disposed || controller.signal.aborted) return;
      if (!value?.underlying?.instrument_key || !Array.isArray(value.expiries)) throw new Error("Invalid option expiry response");
      const expiries = expiryList(value.expiries).filter((date) => date >= today);
      setMetadata({ ...value, key, expiries });
      setSelection((previous) => previous?.key === key && expiries.includes(previous.expiry) ? previous : { key, expiry: expiries[0] || "", center: null });
    }).catch((failure) => {
      if (!disposed && !controller.signal.aborted) setMetaError(failure.response?.data?.detail || "Option expiries could not be loaded. Refresh to retry.");
    }).finally(() => { if (!disposed) setMetaLoading(false); });
    return () => { disposed = true; controller.abort(); };
  }, [key, metaRetry, today]);

  useEffect(() => {
    if (!key || !expiry || metadata?.key !== key) return undefined;
    let disposed = false, stream, fingerprint = "", lastEmission = 0;
    const controller = new AbortController();
    const params = { expiry, ...(center != null ? { center } : {}), radius: RADIUS };
    const valid = (value) => value?.expiry === expiry && value.underlying?.instrument_key === metadata.underlying.instrument_key &&
      Array.isArray(value.rows) && value.rows.every((row) => finiteValue(row?.strike));
    const accept = (value, streaming) => {
      if (disposed || controller.signal.aborted || !valid(value)) return false;
      const stamp = finiteValue(value.as_of_ms) ? Number(value.as_of_ms) : 0;
      if (stamp && lastEmission && stamp < lastEmission) return false;
      if (stamp) lastEmission = stamp;
      const nextFingerprint = JSON.stringify(value);
      if (nextFingerprint !== fingerprint) { fingerprint = nextFingerprint; setResult({ identity, value }); current.current = value; }
      if (streaming) { const receivedAt = Date.now(); setTransport({ healthy: true, receivedAt }); setStreamWarnings([]); setNow(receivedAt); }
      return true;
    };
    const streamError = (event) => {
      if (disposed) return;
      setTransport((previous) => ({ ...previous, healthy: false }));
      try { setStreamWarnings(messages(JSON.parse(event.data).warnings)); } catch { setStreamWarnings(["Option quote updates are unavailable. Refresh to retry."]); }
    };
    current.current = null; setLoading(true); setError(""); setStreamWarnings([]); setTransport({ healthy: false, receivedAt: 0 });
    api.chain(key, params, { signal: controller.signal }).then((value) => {
      if (disposed || controller.signal.aborted) return;
      if (!accept(value, false)) throw new Error("Option chain response did not match the selected expiry.");
      try {
        stream = api.stream(key, params);
        stream.addEventListener("snapshot", (event) => {
          if (disposed) return;
          try { if (!accept(JSON.parse(event.data), true)) streamError({ data: "{}" }); } catch { streamError({ data: "{}" }); }
        });
        stream.addEventListener("stream_error", streamError);
        stream.onerror = () => streamError({ data: JSON.stringify({ warnings: ["Option quote stream disconnected. Displayed values may be stale."] }) });
      } catch { streamError({ data: JSON.stringify({ warnings: ["Option quote stream could not be opened."] }) }); }
    }).catch((failure) => {
      if (!disposed && !controller.signal.aborted) setError(typeof failure.response?.data?.detail === "string" ? failure.response.data.detail : "Option chain could not be loaded. Refresh to retry.");
    }).finally(() => { if (!disposed) setLoading(false); });
    return () => { disposed = true; controller.abort(); stream?.close(); };
  }, [key, expiry, center, refreshId, identity, metadata]);

  useEffect(() => { const timer = setInterval(() => setNow(Date.now()), 1000); return () => clearInterval(timer); }, []);
  const setExpiry = useCallback((value) => {
    if (metadata?.key === key && metadata.expiries.includes(value)) setSelection({ key, expiry: value, center: null });
  }, [key, metadata]);
  const refresh = useCallback(() => { if (!expiry) setMetaRetry((count) => count + 1); else setRefreshId((count) => count + 1); }, [expiry]);
  const moveWindow = useCallback((direction) => {
    const rows = current.current?.rows || [];
    if (rows.length < 2) return;
    const strikes = rows.map((row) => Number(row.strike)).sort((a, b) => a - b);
    const step = strikes.slice(1).map((strike, index) => strike - strikes[index]).find((gap) => gap > 0);
    if (!step) return;
    const middle = center ?? strikes[Math.floor(strikes.length / 2)];
    setSelection({ key, expiry, center: Math.max(step, middle + Math.sign(direction) * step * RADIUS * 2) });
  }, [key, expiry, center]);
  const backToAtm = useCallback(() => setSelection({ key, expiry, center: null }), [key, expiry]);
  const streamHealthy = Boolean(data && transport.healthy && now - transport.receivedAt <= STREAM_TIMEOUT_MS);
  const status = !data ? "Waiting" : error || data.stale ? "Stale" : data.status === "disconnected" ? "Disconnected" :
    !streamHealthy && transport.receivedAt ? "Stale" : !streamHealthy ? "Snapshot" :
    ["market_closed", "off_hours"].includes(data.status) ? "Market closed" : data.status === "stale" ? "Stale" :
      data.status === "live" ? "Live updates" : data.status === "unavailable" ? "Unavailable" : "Waiting for quotes";
  return { data, expiry, center, expiries: metadata?.key === key ? metadata.expiries : [], loading: metaLoading || loading,
    empty: metadata?.key === key && metadata.expiries.length === 0, error: metaError || error,
    warnings: [...new Set([...messages(metadata?.warnings), ...messages(data?.warnings), ...streamWarnings])], streamHealthy, status, now,
    setExpiry, refresh, moveWindow, backToAtm };
}
