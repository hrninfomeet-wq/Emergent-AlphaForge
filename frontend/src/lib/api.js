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
  marketHeader: () => apiClient.get("/market/header").then((r) => r.data),

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
  warehouseLookup: (instrument, date, time) =>
    apiClient.get("/warehouse/lookup", {
      params: { instrument, date, ...(time ? { time } : {}) },
    }).then((r) => r.data),
  warehouseOhlc: (instrument, params = {}) =>
    apiClient.get(`/warehouse/ohlc/${instrument}`, { params }).then((r) => r.data),
  auditWarehouse: (instrument, startTs, endTs) =>
    apiClient.get(`/warehouse/audit/${instrument}`, {
      params: {
        ...(startTs ? { start_ts: startTs } : {}),
        ...(endTs ? { end_ts: endTs } : {}),
      },
    }).then((r) => r.data),
  clearWarehouseData: (instrument = "ALL") =>
    apiClient.delete(`/warehouse/data/${instrument}?confirm=CLEAR`).then((r) => r.data),
  upstoxStatus: () => apiClient.get("/upstox/status").then((r) => r.data),
  startUpstoxAuth: () => apiClient.get("/upstox/auth/start").then((r) => r.data),
  disconnectUpstox: () => apiClient.post("/upstox/disconnect").then((r) => r.data),
  marketQuote: (instrument) =>
    apiClient.get(`/upstox/market-quote/${instrument}`).then((r) => r.data),
  startUpstoxStream: (payload = {}) =>
    apiClient.post("/upstox/stream/start", payload).then((r) => r.data),
  stopUpstoxStream: () =>
    apiClient.post("/upstox/stream/stop").then((r) => r.data),
  upstoxStreamStatus: () =>
    apiClient.get("/upstox/stream/status").then((r) => r.data),
  latestUpstoxTicks: (limit = 50) =>
    apiClient.get(`/upstox/stream/ticks/latest?limit=${limit}`).then((r) => r.data),
  upstoxOptionStreamUniverse: (params = {}) =>
    apiClient.get("/upstox/stream/options/universe", { params }).then((r) => r.data),
  restartUpstoxOptionStream: (payload = {}) =>
    apiClient.post("/upstox/stream/options/restart", payload).then((r) => r.data),
  ingestUpstox: (payload) =>
    apiClient.post("/upstox/warehouse/ingest", payload).then((r) => r.data),
  startUpstoxIngestJob: (payload) =>
    apiClient.post("/upstox/warehouse/ingest/jobs", payload).then((r) => r.data),
  getUpstoxIngestJob: (id) =>
    apiClient.get(`/upstox/warehouse/ingest/jobs/${id}`).then((r) => r.data),
  previewOptionWarehouse: (payload) =>
    apiClient.post("/upstox/options/warehouse/preview", payload).then((r) => r.data),
  fetchOptionWarehouse: (payload) =>
    apiClient.post("/upstox/options/warehouse/fetch", payload).then((r) => r.data),
  startOptionWarehouseFetchJob: (payload) =>
    apiClient.post("/upstox/options/warehouse/fetch/jobs", payload).then((r) => r.data),
  getOptionWarehouseFetchJob: (id) =>
    apiClient.get(`/upstox/options/warehouse/fetch/jobs/${id}`).then((r) => r.data),
  backfillExpiredOptionContracts: (instrument, payload) =>
    apiClient.post(`/upstox/expired-options/contracts/${instrument}/sync`, payload).then((r) => r.data),
  auditOptionData: (instrument, params) =>
    apiClient.get(`/options/audit/${instrument}`, { params }).then((r) => r.data),
  clearOptionData: (instrument = "ALL") =>
    apiClient.delete(`/options/data/${instrument}?confirm=CLEAR`).then((r) => r.data),
  optionCoverage: (underlying) =>
    apiClient.get("/options/coverage", {
      params: underlying ? { underlying } : {},
    }).then((r) => r.data),
  marketHolidays: (year) =>
    apiClient.get("/calendar/holidays", {
      params: year ? { year } : {},
    }).then((r) => r.data),

  // Data Hygiene
  dataHygienePlan: (payload = {}) =>
    apiClient.post("/data-hygiene/plan", payload).then((r) => r.data),
  dataHygieneExecute: (plan, payload = {}) =>
    apiClient.post("/data-hygiene/execute", { plan, ...payload }).then((r) => r.data),
  dataHygieneCatchUp: (payload = {}) =>
    apiClient.post("/data-hygiene/catch-up", payload).then((r) => r.data),
  dataHygieneStatus: (planId) =>
    apiClient.get("/data-hygiene/status", {
      params: planId ? { plan_id: planId } : {},
    }).then((r) => r.data),
  autoUpdateStatus: () =>
    apiClient.get("/warehouse/auto-update/status").then((r) => r.data),
  autoUpdateToggle: (enabled) =>
    apiClient.post("/warehouse/auto-update/toggle", { enabled }).then((r) => r.data),
  autoUpdateRunNow: () =>
    apiClient.post("/warehouse/auto-update/run").then((r) => r.data),
  vixCoverage: () =>
    apiClient.get("/warehouse/vix/coverage").then((r) => r.data),
  vixIngest: (payload) =>
    apiClient.post("/warehouse/vix/ingest", payload).then((r) => r.data),

  // Profiles
  listProfiles: () => apiClient.get("/profiles").then((r) => r.data),
  saveProfile: (name, settings) =>
    apiClient.put(`/profiles/${name}`, { name, settings }).then((r) => r.data),

  // Backtest
  runBacktest: (payload) =>
    apiClient.post("/backtest/run", payload).then((r) => r.data),
  optionPreflight: (payload, ingestMissing = false) =>
    apiClient.post("/backtest/option-preflight", payload, { params: { ingest_missing: ingestMissing } }).then((r) => r.data),
  listBacktestRuns: (limit = 50) =>
    apiClient.get(`/backtest/runs?limit=${limit}`).then((r) => r.data),
  getBacktestRun: (id) =>
    apiClient.get(`/backtest/runs/${id}`).then((r) => r.data),
  deleteBacktestRun: (id) =>
    apiClient.delete(`/backtest/runs/${id}`).then((r) => r.data),

  // Live signal lifecycle + paper trading
  listSignals: (params = {}) =>
    apiClient.get("/signals", { params }).then((r) => r.data),
  createSignal: (payload) =>
    apiClient.post("/signals", payload).then((r) => r.data),
  transitionSignal: (id, payload) =>
    apiClient.post(`/signals/${id}/transition`, payload).then((r) => r.data),
  approveSignal: (id, payload = {}) =>
    apiClient.post(`/signals/${id}/approve`, payload).then((r) => r.data),
  skipSignal: (id, payload = {}) =>
    apiClient.post(`/signals/${id}/skip`, payload).then((r) => r.data),
  markBlockedSignal: (id, payload = {}) =>
    apiClient.post(`/signals/${id}/mark-blocked`, payload).then((r) => r.data),
  deploySignalToPaper: (id, payload) =>
    apiClient.post(`/signals/${id}/paper`, payload).then((r) => r.data),
  listPaperTrades: (params = {}) =>
    apiClient.get("/paper/trades", { params }).then((r) => r.data),
  markPaperTrade: (id, payload) =>
    apiClient.post(`/paper/trades/${id}/mark`, payload).then((r) => r.data),
  closePaperTrade: (id, payload) =>
    apiClient.post(`/paper/trades/${id}/close`, payload).then((r) => r.data),
  listDeployments: (params = {}) =>
    apiClient.get("/deployments", { params }).then((r) => r.data),
  createDeployment: (payload) =>
    apiClient.post("/deployments", payload).then((r) => r.data),
  pauseDeployment: (id) =>
    apiClient.post(`/deployments/${id}/pause`).then((r) => r.data),
  resumeDeployment: (id) =>
    apiClient.post(`/deployments/${id}/resume`).then((r) => r.data),
  archiveDeployment: (id) =>
    apiClient.post(`/deployments/${id}/archive`).then((r) => r.data),
  evaluateDeployment: (id) =>
    apiClient.post(`/deployments/${id}/evaluate-on-close`).then((r) => r.data),
  evaluateActiveDeployments: () =>
    apiClient.post("/deployments/evaluate-active").then((r) => r.data),
  listDeploymentSignals: (id, params = {}) =>
    apiClient.get(`/deployments/${id}/signals`, { params }).then((r) => r.data),
  listDeploymentMetrics: (params = {}) =>
    apiClient.get("/deployments/metrics", { params }).then((r) => r.data),
  deploymentMetrics: (id) =>
    apiClient.get(`/deployments/${id}/metrics`).then((r) => r.data),
  deploymentPreflight: (instrument, params = {}) =>
    apiClient.get("/deployments/preflight", { params: { instrument, ...params } }).then((r) => r.data),
  deploymentQuality: (sourceType, sourceId) =>
    apiClient.get("/deployments/quality", { params: { source_type: sourceType, source_id: sourceId } }).then((r) => r.data),
  deploymentReadiness: (sourceType, sourceId) =>
    apiClient.get("/deployments/readiness", { params: { source_type: sourceType, source_id: sourceId } }).then((r) => r.data),

  // Optimizer
  startOptimization: (payload) =>
    apiClient.post("/optimize/start", payload).then((r) => r.data),
  startWfo: (payload) =>
    apiClient.post("/optimize/wfo", payload).then((r) => r.data),
  listOptJobs: (limit = 50) =>
    apiClient.get(`/optimize/jobs?limit=${limit}`).then((r) => r.data),
  getOptJob: (id) =>
    apiClient.get(`/optimize/jobs/${id}`).then((r) => r.data),
  deleteOptJob: (id) =>
    apiClient.delete(`/optimize/jobs/${id}`).then((r) => r.data),
  cancelOptJob: (id) =>
    apiClient.post(`/optimize/jobs/${id}/cancel`).then((r) => r.data),
  pauseOptJob: (id) =>
    apiClient.post(`/optimize/jobs/${id}/pause`).then((r) => r.data),
  resumeOptJob: (id) =>
    apiClient.post(`/optimize/jobs/${id}/resume`).then((r) => r.data),
  applyOptAsPreset: (jobId, name) =>
    apiClient.post(`/optimize/apply-as-preset/${jobId}?name=${encodeURIComponent(name)}`).then((r) => r.data),

  // Presets
  listPresets: () => apiClient.get("/presets").then((r) => r.data),
  savePreset: (name, config) =>
    apiClient.put(`/presets/${name}`, { name, config }).then((r) => r.data),
  deletePreset: (name) =>
    apiClient.delete(`/presets/${name}`).then((r) => r.data),
};
