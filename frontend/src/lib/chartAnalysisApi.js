import { apiClient } from "./api";
export const chartAnalysisApi = {
  catalog: (instrumentKey, timeframe, { signal } = {}) => apiClient.get("/charts/analysis/catalog", { params: { instrument_key: instrumentKey, timeframe }, signal, timeout: 15000 }).then(({ data }) => data),
  observe: (instrumentKey, timeframe, { strategyId = null, forecast = false } = {}, { signal } = {}) => apiClient.post("/charts/analysis/observe", { instrument_key: instrumentKey, timeframe, strategy_id: strategyId, forecast }, { signal, timeout: 15000 }).then(({ data }) => data),
};
