import { useEffect, useRef, useState } from "react";
import { chartAnalysisApi as api } from "../../lib/chartAnalysisApi";
import { instrumentIdentity } from "../../lib/chartModel";
import { defaultAnalysisSettings, normalizeAnalysisSettings } from "../../lib/chartAnalysisSettings";

const empty = { data: null, signals: [], error: "", stale: false, loading: false, receivedAt: 0 };
export default function useChartAnalysis(instrument, timeframe, suppliedSettings) {
  const settings = normalizeAnalysisSettings(suppliedSettings) || defaultAnalysisSettings();
  const key = instrument?.instrument_key || "";
  const contract = JSON.stringify(instrumentIdentity(instrument || {}));
  const enabled = settings.forecast || Boolean(settings.strategyId) || Object.values(settings.overlays).some(Boolean);
  const identity = JSON.stringify([key, contract, timeframe, settings.strategyId, settings.forecast, enabled]);
  const [state, setState] = useState({ identity, ...empty });
  const [catalog, setCatalog] = useState(null);
  const [catalogError, setCatalogError] = useState("");
  const [clock, setClock] = useState(Date.now);
  const generation = useRef(0);
  useEffect(() => {
    if (!enabled) return undefined;
    setClock(Date.now());
    const timer = setInterval(() => setClock(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [enabled]);
  useEffect(() => {
    let disposed = false; const controller = new AbortController();
    setCatalog(null); setCatalogError("");
    if (key) api.catalog(key, timeframe, { signal: controller.signal }).then(value => {
      if (!disposed && !controller.signal.aborted) setCatalog({ key, contract, timeframe, value });
    }).catch(() => { if (!disposed && !controller.signal.aborted) setCatalogError("Analysis catalogue unavailable. Change instrument or timeframe to retry."); });
    return () => { disposed = true; controller.abort(); };
  }, [key, contract, timeframe]);
  useEffect(() => {
    const current = ++generation.current;
    let disposed = false, timer, inFlight = false, controller, failures = 0;
    const signals = new Map();
    let data = null, receivedAt = 0;
    const active = () => !disposed && generation.current === current;
    const publish = patch => { if (active()) setState({ ...empty, identity, data, receivedAt, signals: [...signals.values()], ...patch }); };
    publish({ loading: enabled && Boolean(key) });
    const poll = async () => {
      if (!active() || !enabled || !key || inFlight || document.hidden) return;
      clearTimeout(timer); inFlight = true; controller = new AbortController();
      try {
        const value = await api.observe(key, timeframe, { strategyId: settings.strategyId, forecast: settings.forecast }, { signal: controller.signal });
        if (!active() || controller.signal.aborted) return;
        const received = value?.instrument;
        const expected = JSON.parse(contract);
        if (!expected || received?.instrument_key !== key || value.timeframe !== timeframe || ["expiry", "strike", "option_type"].some(field => expected[field] != null && String(received?.[field]) !== String(expected[field]))) throw new Error("Analysis identity mismatch");
        const receipt = Date.now();
        if (!Number.isFinite(value.as_of_ms) || value.as_of_ms <= 0 || value.as_of_ms > receipt + 5000 || !Array.isArray(value.warnings) || !value.overlays || typeof value.overlays !== "object" || Array.isArray(value.overlays)) throw new Error("Invalid analysis response");
        if (data && Number(value.as_of_ms) < Number(data.as_of_ms)) throw new Error("Older analysis response");
        data = value; receivedAt = receipt; setClock(receipt);
        if (settings.strategyId && value.strategy?.id === settings.strategyId) for (const signal of value.strategy.signals || []) {
          if (typeof signal.id === "string" && Number.isFinite(signal.time) && Number.isFinite(signal.price)) signals.set(signal.id, signal);
        }
        const bounded = [...signals.values()].sort((a, b) => a.time - b.time || a.id.localeCompare(b.id)).slice(-100);
        signals.clear(); bounded.forEach(signal => signals.set(signal.id, signal));
        failures = 0; publish({ loading: false });
      } catch (failure) {
        if (active() && !controller.signal.aborted) { failures += 1; publish({ loading: false, stale: Boolean(data), error: failure.message === "Analysis identity mismatch" ? failure.message : "Analysis refresh unavailable; retained research is stale." }); }
      } finally {
        inFlight = false;
        if (active() && enabled) timer = setTimeout(poll, Math.min(30000, 5000 * 2 ** Math.min(failures, 3)));
      }
    };
    const visibility = () => { if (!document.hidden) poll(); };
    if (enabled) { poll(); document.addEventListener("visibilitychange", visibility); }
    return () => { disposed = true; clearTimeout(timer); controller?.abort(); document.removeEventListener("visibilitychange", visibility); };
  }, [key, contract, timeframe, identity, enabled, settings.strategyId, settings.forecast]);
  const visible = state.identity === identity ? state : empty;
  const ageStale = Boolean(visible.data && (clock - visible.receivedAt >= 15000 || clock - visible.data.as_of_ms >= 15000));
  return { ...visible, stale: visible.stale || ageStale, enabled,
    catalog: catalog?.key === key && catalog.contract === contract && catalog.timeframe === timeframe ? catalog.value : null, catalogError };
}
