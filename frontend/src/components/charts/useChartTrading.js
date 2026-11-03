import { useCallback, useEffect, useRef, useState } from "react";
import { chartTradingApi } from "../../lib/chartTradingApi";
import { entryPayload, initialTicket } from "../../lib/chartTradingModel";

const message = error => error?.response?.data?.detail || error?.message || "Request failed";
// Instant applies only to new entries and remains scoped to this instrument and mode.
export default function useChartTrading(instrument) {
  const key = instrument?.instrument_key || "";
  const [mode, setModeState] = useState("paper"), [form, setForm] = useState(initialTicket);
  const [metadata, setMetadata] = useState(null), [rows, setRows] = useState([]);
  const [asOf, setAsOf] = useState(null), [stale, setStale] = useState(true);
  const [error, setError] = useState(""), [loading, setLoading] = useState(false);
  const [pending, setPending] = useState(null), [busy, setBusy] = useState(false);
  const [instantContext, setInstantContext] = useState(null);
  const instant = instantContext?.key === key && instantContext?.mode === mode;
  const context = useRef({ key, mode, generation: 0 });
  const mutationLock = useRef(false);
  if (context.current.key !== key || context.current.mode !== mode) context.current = { key, mode, generation: context.current.generation + 1 };
  const current = useCallback(generation => context.current.generation === generation, []);
  const refresh = useCallback(async () => {
    try { const data = await chartTradingApi.orders(); setRows(data.rows || []); setAsOf(data.as_of_ms); setStale(false); }
    catch (e) { setStale(true); setError(`Order snapshot unavailable: ${message(e)}`); }
  }, []);
  useEffect(() => { refresh(); const timer = setInterval(refresh, 5000); return () => clearInterval(timer); }, [refresh]);
  useEffect(() => {
    let active = true; setInstantContext(null); setMetadata(null); setPending(null); setForm(initialTicket()); setError("");
    if (!key) return () => { active = false; };
    setLoading(true);
    chartTradingApi.metadata(key).then(data => { if (active) setMetadata(data); }).catch(e => { if (active) setError(message(e)); }).finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [key]);
  const setMode = useCallback(next => { if (busy || mutationLock.current) return; context.current = { ...context.current, mode: next, generation: context.current.generation + 1 }; setInstantContext(null); setPending(null); setError(""); setForm(initialTicket()); setModeState(next); }, [busy]);
  const prepare = useCallback(async (enableInstant = false) => {
    if (busy || mutationLock.current || !key || !metadata?.supported || (mode === "live" && !metadata.live_available)) return;
    const generation = context.current.generation; mutationLock.current = true; setBusy(true); setError(""); setPending(null);
    try {
      const preview = await chartTradingApi.preview(entryPayload(key, mode, form));
      if (!current(generation)) return;
      if (enableInstant) setPending({ kind: "enableInstant", preview, mode, key, generation });
      else if (instant) {
        // No await between the context check and dispatch; stale previews cannot arm.
        await chartTradingApi.arm(preview.preview_id, mode === "live");
        await refresh();
      } else setPending({ kind: "entry", preview, mode, key, generation });
    }
    catch (e) { if (current(generation)) { setError(instant && !enableInstant ? `${message(e)}. Check the order snapshot before retrying; the outcome may be unknown.` : message(e)); if (instant && !enableInstant) setStale(true); } } finally { mutationLock.current = false; setBusy(false); }
  }, [busy, key, metadata, mode, form, current, instant, refresh]);
  const requestChange = useCallback((row, field, price) => {
    if (busy || stale || (field !== "cancel" && mode === "live" && !metadata?.live_available) || !row?.active || !row.chart_owned || row.mode !== mode || row.instrument_key !== key || (field !== "cancel" && row.pending_change) || row.cancel_requested) return;
    setError(""); setPending({ kind: "change", row: { ...row }, field, price, mode, key, generation: context.current.generation });
  }, [busy, stale, mode, key, metadata]);
  const confirm = useCallback(async (acknowledgedLiveRisk = false) => {
    if (!pending || busy || mutationLock.current || !current(pending.generation) || pending.key !== key || pending.mode !== mode) return;
    if (pending.kind === "enableInstant") {
      if (pending.mode === "live" && acknowledgedLiveRisk !== true) return;
      setInstantContext({ key: pending.key, mode: pending.mode }); setPending(null); return;
    }
    if (pending.kind === "change" && pending.field !== "cancel" && mode === "live" && !metadata?.live_available) { setPending(null); setError("Live modifications unavailable."); return; }
    if (pending.kind === "change" && stale) { setPending(null); setError("Order snapshot is stale. Refresh and confirm again."); return; }
    const action = pending; mutationLock.current = true; setPending(null); setBusy(true); setError("");
    try {
      if (action.kind === "entry") await chartTradingApi.arm(action.preview.preview_id, action.mode === "live");
      else await chartTradingApi.change(action.row.id, { field: action.field, ...(action.field !== "cancel" ? { price: action.price } : {}), edit_version: action.row.edit_version, confirm_live: action.mode === "live" });
      await refresh();
    } catch (e) { setError(`${message(e)}. Check the order snapshot before retrying; the outcome may be unknown.`); setStale(true); }
    finally { mutationLock.current = false; setBusy(false); }
  }, [pending, busy, current, key, mode, stale, refresh, metadata]);
  return { mode, setMode, instant, disableInstant: () => setInstantContext(null), form, setForm, metadata, rows, asOf, stale, error, loading, busy, pending, setPending, prepare, requestChange, confirm, refresh };
}
