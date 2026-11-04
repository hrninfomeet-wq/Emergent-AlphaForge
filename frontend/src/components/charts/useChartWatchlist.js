import { useCallback, useEffect, useRef, useState } from "react";
import { chartWatchlistApi as api } from "../../lib/chartWatchlistApi";
import { finiteValue, priceText, timeText } from "./chartPresentation";

const CAPACITY = 200;
const SNAPSHOT_TIMEOUT_MS = 12000;
const TICK_TIMEOUT_MS = 10000;
const validList = (value) => value?.id === "default" && Number.isInteger(value.revision) && value.revision >= 0 &&
  typeof value.name === "string" && Array.isArray(value.items) && value.items.length <= CAPACITY &&
  value.items.every((item) => typeof item.id === "string" && item.instrument?.instrument_key);

export function watchlistQuote(quote, streamHealthy, now) {
  const tick = quote?.tick;
  const price = finiteValue(tick?.last_price) && Number(tick.last_price) > 0 ? Number(tick.last_price) : null;
  const close = finiteValue(tick?.close_price) && Number(tick.close_price) > 0 ? Number(tick.close_price) : null;
  const percent = price !== null && close !== null ? (price - close) / close * 100 : null;
  const elapsed = Math.max(0, now - (quote?.receivedAtMs || 0));
  const age = finiteValue(quote?.last_tick_age_ms) ? Number(quote.last_tick_age_ms) + elapsed : null;
  const stamp = finiteValue(tick?.ts) && Number(tick.ts) > 0 && Number(tick.ts) <= 8640000000000000 ? Number(tick.ts) : null;
  const validStamp = stamp !== null && stamp <= now + 5000;
  const fresh = streamHealthy && price !== null && validStamp && quote?.connected === true &&
    ["live", "fresh"].includes(quote?.status) && age !== null && age >= 0 && age <= TICK_TIMEOUT_MS;
  const status = quote?.status === "unavailable" ? "Unavailable" : !streamHealthy || quote?.connected === false ? "Offline" :
    ["market_closed", "off_hours"].includes(quote?.status) ? "Market closed" : fresh ? "Live" : price === null ? "Waiting" : "Stale";
  return { price, priceText: price === null ? "—" : priceText(price), percent: Number.isFinite(percent) ? percent : null,
    fresh, status, timestamp: stamp === null ? "Quote time unavailable" : `${timeText(stamp / 1000)} IST` };
}

export default function useChartWatchlist() {
  const [watchlist, setWatchlist] = useState(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [quotes, setQuotes] = useState({});
  const [transport, setTransport] = useState({ healthy: false, receivedAt: 0 });
  const [warnings, setWarnings] = useState([]);
  const [now, setNow] = useState(Date.now);
  const mounted = useRef(false), current = useRef(null), busy = useRef(false);
  const reads = useRef(0), readController = useRef(null), writeController = useRef(null), reloadNeeded = useRef(false), readBusy = useRef(false);

  const accept = useCallback((value) => {
    if (!validList(value)) throw new Error("Invalid watchlist response");
    if (current.current && value.revision < current.current.revision) return;
    current.current = value;
    setWatchlist(value);
  }, []);

  const refresh = useCallback(async ({ preserveError = false } = {}) => {
    if (!mounted.current) return false;
    if (busy.current) { reloadNeeded.current = true; return false; }
    const request = ++reads.current;
    readController.current?.abort();
    const controller = new AbortController(); readController.current = controller;
    readBusy.current = true;
    setLoading(true);
    if (!preserveError) setError("");
    try {
      const value = await api.get({ signal: controller.signal });
      if (!mounted.current || controller.signal.aborted || request !== reads.current) return false;
      accept(value);
      return true;
    } catch {
      if (mounted.current && !controller.signal.aborted && request === reads.current) {
        setError((previous) => `${preserveError && previous ? `${previous} ` : ""}Watchlist could not be loaded. Retry to read the saved list.`);
      }
      return false;
    } finally {
      if (request === reads.current) {
        readBusy.current = false;
        if (mounted.current) setLoading(false);
      }
    }
  }, [accept]);

  useEffect(() => {
    mounted.current = true;
    refresh();
    const clock = setInterval(() => setNow(Date.now()), 1000);
    return () => {
      mounted.current = false;
      readController.current?.abort(); writeController.current?.abort(); clearInterval(clock);
    };
  }, [refresh]);

  const revision = watchlist?.revision;
  useEffect(() => {
    if (revision == null) return undefined;
    let disposed = false, stream;
    setTransport({ healthy: false, receivedAt: 0 });
    setQuotes({});
    setWarnings([]);
    try {
      stream = api.stream(revision);
      stream.addEventListener("snapshot", (event) => {
        if (disposed) return;
        try {
          const value = JSON.parse(event.data);
          if (value.revision !== revision || !value.quotes || typeof value.quotes !== "object" || Array.isArray(value.quotes)) return;
          const receivedAt = Date.now();
          const incoming = Object.fromEntries(Object.entries(value.quotes).map(([key, quote]) => [key, { ...quote, receivedAtMs: receivedAt }]));
          setQuotes((previous) => ({ ...previous, ...incoming }));
          setTransport({ healthy: true, receivedAt });
          setWarnings(Array.isArray(value.warnings) ? value.warnings.filter((message) => typeof message === "string") : []);
          setNow(receivedAt);
        } catch { setTransport((previous) => ({ ...previous, healthy: false })); }
      });
      stream.addEventListener("watchlist_changed", () => {
        if (disposed) return;
        setTransport((previous) => ({ ...previous, healthy: false }));
        refresh();
      });
      stream.addEventListener("stream_error", (event) => {
        if (disposed) return;
        setTransport((previous) => ({ ...previous, healthy: false }));
        try {
          const value = JSON.parse(event.data);
          setWarnings(Array.isArray(value.warnings) ? value.warnings.filter((message) => typeof message === "string") : ["Watchlist quote stream unavailable."]);
        } catch { setWarnings(["Watchlist quote stream unavailable."]); }
      });
      stream.onerror = () => { if (!disposed) setTransport((previous) => ({ ...previous, healthy: false })); };
    } catch { setTransport({ healthy: false, receivedAt: 0 }); }
    return () => { disposed = true; stream?.close(); };
  }, [revision, refresh]);

  const mutate = useCallback(async (change) => {
    if (!mounted.current || busy.current || readBusy.current || !current.current) return false;
    const base = current.current;
    let next;
    try { next = change(base); } catch (failure) { setError(failure.message); return false; }
    if (!next) return false;
    busy.current = true;
    ++reads.current; readController.current?.abort();
    setSaving(true); setLoading(false); setError("");
    const controller = new AbortController(); writeController.current = controller;
    let conflict = false;
    try {
      const response = await api.save({ revision: base.revision, name: next.name, items: next.items.map((item) => ({
        ...(item.id ? { id: item.id } : {}), instrument_key: item.instrument?.instrument_key || item.instrument_key, label: item.label || "",
      })) }, { signal: controller.signal });
      if (!mounted.current || controller.signal.aborted) return false;
      accept(response);
      return true;
    } catch (failure) {
      if (!mounted.current || controller.signal.aborted) return false;
      conflict = failure.response?.status === 409;
      setError(conflict ? "Watchlist changed in another window. Your edit was not saved; loading the latest list. Review it before trying again." :
        "Watchlist save was not confirmed. Reload the saved list before retrying.");
      // Even a failed network acknowledgement may follow a committed write.
      reloadNeeded.current = true;
      return false;
    } finally {
      busy.current = false;
      if (mounted.current) {
        setSaving(false);
        if (conflict || reloadNeeded.current) { reloadNeeded.current = false; await refresh({ preserveError: true }); }
      }
    }
  }, [accept, refresh]);

  const add = useCallback((instrument) => mutate((base) => {
    if (!instrument?.instrument_key) throw new Error("Choose an instrument from search.");
    if (base.items.some((item) => item.instrument.instrument_key === instrument.instrument_key)) throw new Error("This instrument is already in your watchlist.");
    if (base.items.length >= CAPACITY) throw new Error("Watchlist is full (200 instruments). Remove one before adding another.");
    return { ...base, items: [...base.items, { instrument, label: "" }] };
  }), [mutate]);
  const remove = useCallback((id) => mutate((base) => ({ ...base, items: base.items.filter((item) => item.id !== id) })), [mutate]);
  const rename = useCallback((name) => mutate((base) => {
    if (!name.trim() || name.trim().length > 80) throw new Error("Watchlist name must contain 1–80 characters.");
    return { ...base, name: name.trim() };
  }), [mutate]);
  const relabel = useCallback((id, label) => mutate((base) => {
    if (label.trim().length > 80) throw new Error("Display label must be 80 characters or fewer.");
    return { ...base, items: base.items.map((item) => item.id === id ? { ...item, label: label.trim() } : item) };
  }), [mutate]);
  const move = useCallback((id, delta) => mutate((base) => {
    const index = base.items.findIndex((item) => item.id === id), destination = index + delta;
    if (index < 0 || destination < 0 || destination >= base.items.length) return null;
    const items = [...base.items]; [items[index], items[destination]] = [items[destination], items[index]];
    return { ...base, items };
  }), [mutate]);

  const streamHealthy = transport.healthy && now - transport.receivedAt <= SNAPSHOT_TIMEOUT_MS;
  return { watchlist, loading, saving, error, quotes, warnings, streamHealthy, now, refresh, add, remove, rename, relabel, move };
}
