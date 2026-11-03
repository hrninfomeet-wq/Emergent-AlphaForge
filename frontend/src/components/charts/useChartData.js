import { useCallback, useEffect, useRef, useState } from "react";
import { chartApi } from "../../lib/chartApi";
import { instrumentIdentity, mergeBars, normalizeBars } from "../../lib/chartModel";

const empty = (identity) => ({ identity, loading: true, bars: [], instrument: null, source: "", warnings: [],
  error: "", refreshing: false, loadingOlder: false, hasMore: false, nextBefore: null, receivedAt: 0,
  snapshot: null, snapshotAt: 0, transport: "connecting", positions: [], positionsAt: 0,
  positionWarnings: [], positionError: "", volumeAvailable: false });
const warnings = (values) => Array.isArray(values) ? values.filter((value) => typeof value === "string") : [];
const observedAt = (value) => typeof value === "number" && Number.isFinite(value) && value > 0 ? Math.min(Date.now(), value) : Date.now();

export default function useChartData(instrument, timeframe) {
  const key = instrument?.instrument_key;
  const contractIdentity = JSON.stringify(instrumentIdentity(instrument || {}));
  const identity = `${key}|${contractIdentity}|${timeframe}`;
  const [state, setState] = useState(() => empty(identity));
  const generation = useRef(0);
  const actions = useRef({});

  useEffect(() => {
    const current = ++generation.current;
    let disposed = false;
    let stream;
    let historyBusy = false;
    let identityRejected = false;
    let positionsBusy = false;
    let olderBusy = false;
    let lastLiveUpdates = new Map();
    let liveVersion = 0;
    let historyAsOf = null;
    const contract = JSON.parse(contractIdentity);
    let snapshot = empty(identity);
    const controllers = new Set();
    const active = () => !disposed && generation.current === current;
    const publish = (patch) => {
      if (!active()) return;
      snapshot = { ...snapshot, ...(typeof patch === "function" ? patch(snapshot) : patch) };
      setState(snapshot);
    };
    const controller = () => { const value = new AbortController(); controllers.add(value); return value; };
    const verifyHistory = (data) => {
      const derivativeMismatch = ["option", "future"].includes(contract?.kind) &&
        ["expiry", "strike", "option_type"].some((field) => contract[field] != null && String(data?.instrument?.[field]) !== String(contract[field]));
      if (data?.instrument?.instrument_key !== key || data.timeframe !== timeframe || derivativeMismatch) {
        identityRejected = true;
        stream?.close();
        publish({ bars: [], positions: [], positionsAt: 0, snapshot: null, transport: "error", hasMore: false });
        throw new Error("Chart response did not match the selected instrument and timeframe.");
      }
    };
    publish({});

    const refreshHistory = async (initial = false) => {
      if (!key || !active() || historyBusy || identityRejected) return;
      historyBusy = true;
      const request = controller();
      const requestLiveVersion = liveVersion;
      lastLiveUpdates = new Map();
      if (!initial) publish({ refreshing: true });
      try {
        const data = await chartApi.history(key, timeframe, { signal: request.signal });
        if (!active()) return;
        verifyHistory(data);
        const responseAsOf = typeof data.as_of_ms === "number" && Number.isFinite(data.as_of_ms) ? data.as_of_ms : null;
        if (responseAsOf !== null && historyAsOf !== null && responseAsOf < historyAsOf) return;
        let bars = mergeBars(snapshot.bars, data.bars);
        // Arrival time alone cannot establish authority: an old server
        // accumulator can arrive later while still missing REST extrema.
        if (liveVersion > requestLiveVersion) {
          const newer = [...lastLiveUpdates.values()].filter((update) => responseAsOf === null ||
            (update.historyAsOf !== null && update.historyAsOf >= responseAsOf)).map((update) => update.bar);
          bars = mergeBars(bars, newer);
        }
        if (responseAsOf !== null) historyAsOf = responseAsOf;
        publish({ bars, instrument: data.instrument, source: data.source || "unknown", warnings: warnings(data.warnings),
          volumeAvailable: data.volume_available === true, error: "", receivedAt: Date.now(),
          ...(initial ? { hasMore: data.has_more === true, nextBefore: data.next_before } : {}) });
      } catch (error) {
        if (active() && !request.signal.aborted) publish({ error: error.message === "Chart response did not match the selected instrument and timeframe."
          ? error.message : "History could not be refreshed. Existing candles may be out of date." });
      } finally {
        controllers.delete(request);
        historyBusy = false;
        publish({ loading: false, refreshing: false });
      }
    };

    const refreshPositions = async () => {
      if (!key || !active() || positionsBusy || identityRejected) return;
      positionsBusy = true;
      const request = controller();
      try {
        const data = await chartApi.positions(key, { signal: request.signal });
        if (!active() || identityRejected) return;
        if (data.instrument_key && data.instrument_key !== key) throw new Error("Position identity mismatch");
        publish({ positions: Array.isArray(data.positions) ? data.positions : [], positionsAt: observedAt(data.as_of_ms),
          positionWarnings: warnings(data.warnings), positionError: "" });
      } catch {
        // Retain a visibly stale snapshot rather than imply that a failed
        // broker read established a flat account.
        if (active() && !request.signal.aborted) publish({ positionError: "Position refresh unavailable; last snapshot is not current.", positionsAt: 0 });
      } finally {
        controllers.delete(request);
        positionsBusy = false;
      }
    };

    const loadOlder = async () => {
      if (!active() || identityRejected || olderBusy || !snapshot.hasMore || snapshot.nextBefore == null) return;
      olderBusy = true;
      const request = controller();
      const before = snapshot.nextBefore;
      publish({ loadingOlder: true });
      try {
        const data = await chartApi.history(key, timeframe, { before, signal: request.signal });
        if (!active()) return;
        verifyHistory(data);
        const older = normalizeBars(data.bars).filter((bar) => bar.time < before);
        const bars = mergeBars(older, snapshot.bars);
        publish({ bars, hasMore: data.has_more === true && older.length > 0 && bars.length < 10000 && data.next_before < before,
          nextBefore: data.next_before, warnings: [...new Set([...snapshot.warnings, ...warnings(data.warnings),
            ...(bars.length >= 10000 ? ["Chart limit reached: 10,000 candles. Change timeframe for a wider view."] : [])])] });
      } catch {
        if (active() && !request.signal.aborted) publish({ error: "Older candles could not be loaded. Try again." });
      } finally {
        controllers.delete(request);
        olderBusy = false;
        publish({ loadingOlder: false });
      }
    };

    const onSnapshot = (event) => {
      if (!active() || identityRejected) return;
      let data;
      try { data = JSON.parse(event.data); } catch { return; }
      if (data.instrument_key !== key || data.timeframe !== timeframe) return;
      // Stream volume is never authoritative: only a REST candle can establish it.
      const changed = Array.isArray(data.bars) ? data.bars : data.bar ? [data.bar] : [];
      const streamAsOf = typeof data.history_as_of_ms === "number" && Number.isFinite(data.history_as_of_ms) ? data.history_as_of_ms : null;
      const outdated = streamAsOf !== null && historyAsOf !== null && streamAsOf < historyAsOf;
      const bars = outdated ? [] : normalizeBars(changed.map((bar) => ({ ...bar, volume: null })));
      if (bars.length) {
        bars.forEach((bar) => lastLiveUpdates.set(bar.time, { bar, historyAsOf: streamAsOf }));
        lastLiveUpdates = new Map([...lastLiveUpdates].sort(([left], [right]) => left - right).slice(-20));
        liveVersion += 1;
      }
      publish({ snapshot: data, snapshotAt: observedAt(data.emitted_at_ms), transport: "open",
        ...(bars.length ? { bars: mergeBars(snapshot.bars, bars) } : {}) });
    };

    const connect = () => {
      if (!key || !active() || identityRejected) return;
      try {
        stream = chartApi.stream(key, timeframe);
        stream.addEventListener("snapshot", onSnapshot);
        stream.addEventListener("heartbeat", onSnapshot);
        stream.addEventListener("stream_error", onSnapshot);
        stream.addEventListener("resync", () => { if (active()) refreshHistory(); });
        stream.onopen = () => { if (active()) { publish({ transport: "open" }); refreshHistory(); } };
        stream.onerror = () => { if (active()) publish({ transport: "reconnecting" }); };
      } catch {
        publish({ transport: "error" });
      }
    };

    actions.current = { refresh: () => { refreshHistory(); refreshPositions(); }, loadOlder };
    if (key) {
      refreshHistory(true).finally(connect);
      refreshPositions();
    } else publish({ loading: false, transport: "idle" });
    const historyTimer = setInterval(refreshHistory, 60000);
    const positionTimer = setInterval(refreshPositions, 2000);
    return () => {
      disposed = true;
      clearInterval(historyTimer);
      clearInterval(positionTimer);
      controllers.forEach((request) => request.abort());
      if (stream) stream.close();
      actions.current = {};
    };
  }, [identity, key, timeframe, contractIdentity]);

  const refresh = useCallback(() => actions.current.refresh?.(), []);
  const loadOlder = useCallback(() => actions.current.loadOlder?.(), []);
  // Effect cleanup follows render. This guard hides previous data immediately,
  // including the single render between selecting a symbol and its new effect.
  return { ...(state.identity === identity ? state : empty(identity)), refresh, loadOlder };
}
