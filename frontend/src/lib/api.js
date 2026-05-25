import axios from "axios";

const BACKEND_URL = process.env.REACT_APP_BACKEND_URL;
export const API = `${BACKEND_URL}/api`;

export const apiClient = axios.create({
  baseURL: API,
  timeout: 60000,
});

export const api = {
  // Health/Summary
  summary: () => apiClient.get("/dashboard/summary").then((r) => r.data),

  // Strategies
  listStrategies: () => apiClient.get("/strategies").then((r) => r.data),
  getStrategy: (id) => apiClient.get(`/strategies/${id}`).then((r) => r.data),

  // Warehouse
  ingest: (instrument, days = 7) =>
    apiClient.post("/warehouse/ingest", { instrument, days }).then((r) => r.data),
  coverage: () => apiClient.get("/warehouse/coverage").then((r) => r.data),
  warehouseRuns: (limit = 50) =>
    apiClient.get(`/warehouse/runs?limit=${limit}`).then((r) => r.data),
  candles: (instrument, limit = 500) =>
    apiClient.get(`/warehouse/candles/${instrument}?limit=${limit}`).then((r) => r.data),

  // Profiles
  listProfiles: () => apiClient.get("/profiles").then((r) => r.data),
  saveProfile: (name, settings) =>
    apiClient.put(`/profiles/${name}`, { name, settings }).then((r) => r.data),

  // Backtest
  runBacktest: (payload) =>
    apiClient.post("/backtest/run", payload).then((r) => r.data),
  listBacktestRuns: (limit = 50) =>
    apiClient.get(`/backtest/runs?limit=${limit}`).then((r) => r.data),
  getBacktestRun: (id) =>
    apiClient.get(`/backtest/runs/${id}`).then((r) => r.data),
  deleteBacktestRun: (id) =>
    apiClient.delete(`/backtest/runs/${id}`).then((r) => r.data),

  // Presets
  listPresets: () => apiClient.get("/presets").then((r) => r.data),
  savePreset: (name, config) =>
    apiClient.put(`/presets/${name}`, { name, config }).then((r) => r.data),
  deletePreset: (name) =>
    apiClient.delete(`/presets/${name}`).then((r) => r.data),
};
