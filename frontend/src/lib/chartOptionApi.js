import { apiClient } from "./api";
import { chartStreamBase } from "./chartStreamBase";

const paramsFor = (instrumentKey, { expiry, center, radius = 20 } = {}) => ({
  instrument_key: instrumentKey, ...(expiry ? { expiry } : {}), ...(center != null ? { center } : {}), radius,
});

// Read-only chart discovery and market data. Selection uses the existing chart context.
export const chartOptionApi = {
  expiries: (instrumentKey, { signal } = {}) => apiClient.get("/charts/options/expiries", {
    params: { instrument_key: instrumentKey }, signal, timeout: 15000,
  }).then(({ data }) => data),
  chain: (instrumentKey, params, { signal } = {}) => apiClient.get("/charts/options/chain", {
    params: paramsFor(instrumentKey, params), signal, timeout: 15000,
  }).then(({ data }) => data),
  stream: (instrumentKey, params) => new EventSource(`${chartStreamBase}/charts/options/stream?${new URLSearchParams(paramsFor(instrumentKey, params))}`),
};
