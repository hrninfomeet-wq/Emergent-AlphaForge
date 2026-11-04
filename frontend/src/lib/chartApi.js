import { API, apiClient } from "./api";
import { createChartStreamPool } from "./chartStreamPool";

const chartStreams = createChartStreamPool(API);

// This adapter deliberately exposes only read endpoints. Closing a browser
// stream releases its chart lease on the server, never an execution lease.
export const chartApi = {
  instruments: (q = "", { signal } = {}) => apiClient.get("/charts/instruments", {
    params: { q, limit: 30 }, signal,
  }).then((response) => response.data),
  history: (instrumentKey, timeframe, { before, limit = 600, signal } = {}) => apiClient.get("/charts/history", {
    params: { instrument_key: instrumentKey, timeframe, limit, ...(before != null ? { before } : {}) }, signal,
  }).then((response) => response.data),
  positions: (instrumentKey, { signal } = {}) => apiClient.get("/charts/positions", {
    params: { instrument_key: instrumentKey }, signal, timeout: 15000,
  }).then((response) => response.data),
  stream: (instrumentKey, timeframe) => chartStreams.stream(instrumentKey, timeframe),
};
