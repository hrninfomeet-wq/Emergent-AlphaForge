import axios from "axios";

const BACKEND_URL = process.env.REACT_APP_BACKEND_URL;
export const API = `${BACKEND_URL}/api`;

export const apiClient = axios.create({
  baseURL: API,
  timeout: 60000,
});

// Heavy SYNCHRONOUS endpoints (backtest run, warehouse sync, data-hygiene scans,
// audits) can run for several minutes on large date ranges. They get a long
// PER-REQUEST timeout so they don't hit the 60s default — while ordinary calls keep
// the short global so a wedged backend still fails fast. Build-time configurable via
// REACT_APP_API_TIMEOUT_LONG (REACT_APP_* is baked at build, so it needs a rebuild;
// it is not a live runtime dial). FUTURE (#3): move /backtest/run to the optimizer's
// fire-and-poll job pattern, which removes this timeout class entirely.
export const LONG_TIMEOUT_MS = parseInt(process.env.REACT_APP_API_TIMEOUT_LONG || "600000", 10);

export const api = {
  // Health/Summary
  summary: () => apiClient.get("/dashboard/summary").then((r) => r.data),
  marketHeader: () => apiClient.get("/market/header").then((r) => r.data),

  // Strategies
  listStrategies: () => apiClient.get("/strategies").then((r) => r.data),
  getStrategy: (id) => apiClient.get(`/strategies/${id}`).then((r) => r.data),
  retireStrategy: (id) => apiClient.post(`/strategies/${id}/retire`).then((r) => r.data),
  unretireStrategy: (id) => apiClient.post(`/strategies/${id}/un-retire`).then((r) => r.data),
  deleteStrategy: (id) => apiClient.delete(`/strategies/${id}`).then((r) => r.data),
  reloadStrategies: () => apiClient.post("/strategies/reload").then((r) => r.data),
  getStrategyCatalog: () => apiClient.get("/strategies/catalog").then((r) => r.data),
  authorCompile: (spec) => apiClient.post("/strategies/author/compile", { spec }).then((r) => r.data),
  authorInstall: (spec, overwrite = false) =>
    apiClient.post("/strategies/author/install", { spec, overwrite }).then((r) => r.data),
  getAuthorProviders: () => apiClient.get("/strategies/author/providers").then((r) => r.data),
  authorFromSource: (source, provider) =>
    apiClient.post("/strategies/author/from-source", { source, provider }, { timeout: LONG_TIMEOUT_MS }).then((r) => r.data),
  authorConverse: (source, provider) =>
    apiClient.post("/strategies/author/converse", { source, provider }, { timeout: LONG_TIMEOUT_MS }).then((r) => r.data),
  authorPythonFromSource: (source, provider) =>
    apiClient.post("/strategies/author/python-from-source", { source, provider }, { timeout: LONG_TIMEOUT_MS }).then((r) => r.data),
  validatePython: (code) =>
    apiClient.post("/strategies/author/python/validate", { code }, { timeout: LONG_TIMEOUT_MS }).then((r) => r.data),
  installPython: (code, strategy_id, overwrite = false) =>
    apiClient.post("/strategies/author/python/install", { code, strategy_id, overwrite }).then((r) => r.data),

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
      timeout: LONG_TIMEOUT_MS,
    }).then((r) => r.data),
  volatilityAudit: (payload) =>
    apiClient.post("/volatility/audit", payload, { timeout: LONG_TIMEOUT_MS }).then((r) => r.data),
  clearWarehouseData: (instrument = "ALL") =>
    apiClient.delete(`/warehouse/data/${instrument}?confirm=CLEAR`).then((r) => r.data),
  upstoxStatus: () => apiClient.get("/upstox/status").then((r) => r.data),
  startUpstoxAuth: () => apiClient.get("/upstox/auth/start").then((r) => r.data),
  upstoxAuthStart: () => apiClient.get("/upstox/auth/start").then((r) => r.data),
  getLiveFeedHealth: () => apiClient.get("/live-feed/health").then((r) => r.data),
  restartLiveFeed: async () => {
    // Best-effort manual bring-up: clears the supervisor's manual-stop suppression
    // and starts stream + roller. The supervisor keeps them up thereafter.
    await apiClient.post("/upstox/stream/start", {}).catch(() => {});
    return apiClient.post("/live-candles/start", {}).then((r) => r.data);
  },
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
    apiClient.post("/data-hygiene/plan", payload, { timeout: LONG_TIMEOUT_MS }).then((r) => r.data),
  dataHygieneExecute: (plan, payload = {}) =>
    apiClient.post("/data-hygiene/execute", { plan, ...payload }, { timeout: LONG_TIMEOUT_MS }).then((r) => r.data),
  dataHygieneCatchUp: (payload = {}) =>
    apiClient.post("/data-hygiene/catch-up", payload, { timeout: LONG_TIMEOUT_MS }).then((r) => r.data),
  dataHygieneLatest: () =>
    apiClient.get("/data-hygiene/latest").then((r) => r.data),
  warehouseSync: (payload = {}) =>
    apiClient.post("/warehouse/sync", payload, { timeout: LONG_TIMEOUT_MS }).then((r) => r.data),
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
    apiClient.post("/backtest/run", payload, { timeout: LONG_TIMEOUT_MS }).then((r) => r.data),
  // Fire-and-forget: returns {run_id, status} instantly; poll getBacktestRun until
  // status is terminal. Avoids holding one long request (no 60s-timeout / dup-run).
  startBacktest: (payload) =>
    apiClient.post("/backtest/start", payload).then((r) => r.data),
  optionPreflight: (payload, ingestMissing = false) =>
    apiClient.post("/backtest/option-preflight", payload, { params: { ingest_missing: ingestMissing }, timeout: LONG_TIMEOUT_MS }).then((r) => r.data),
  preflightIngestJob: (runId) =>
    apiClient.get(`/upstox/warehouse/ingest/jobs/${runId}`).then((r) => r.data),
  paperDeploymentStats: (deploymentId) =>
    apiClient.get("/paper/deployment-stats", { params: { deployment_id: deploymentId } }).then((r) => r.data),
  listBacktestRuns: (limit = 50) =>
    apiClient.get(`/backtest/runs?limit=${limit}`).then((r) => r.data),
  getBacktestRun: (id) =>
    apiClient.get(`/backtest/runs/${id}`).then((r) => r.data),
  deleteBacktestRun: (id) =>
    apiClient.delete(`/backtest/runs/${id}`).then((r) => r.data),

  // Signals ledger + paper trading. (Manual research-signal creation,
  // lifecycle transitions, and the approval flow were retired 2026-06-12.)
  listSignals: (params = {}) =>
    apiClient.get("/signals", { params }).then((r) => r.data),
  listSignalsEnriched: (params = {}) =>
    apiClient.get("/signals/enriched", { params }).then((r) => r.data),
  purgeSignals: (payload) =>
    apiClient.post("/signals/purge", payload).then((r) => r.data),
  listPaperTrades: (params = {}) =>
    apiClient.get("/paper/trades", { params }).then((r) => r.data),
  openPositions: () => apiClient.get("/paper/open-positions").then((r) => r.data),
  purgePaperTrades: (payload) =>
    apiClient.post("/paper/trades/purge", payload).then((r) => r.data),
  markPaperTrade: (id, payload) =>
    apiClient.post(`/paper/trades/${id}/mark`, payload).then((r) => r.data),
  closePaperTrade: (id, payload) =>
    apiClient.post(`/paper/trades/${id}/close`, payload).then((r) => r.data),
  squareOffAll: () =>
    apiClient.post("/paper/square-off").then((r) => r.data),
  paperAnalytics: () => apiClient.get("/paper/analytics").then((r) => r.data),
  paperStrategyStats: () => apiClient.get("/paper/strategy-stats").then((r) => r.data),
  getPaperAccountConfig: () => apiClient.get("/paper/account-config").then((r) => r.data),
  setPaperAccountConfig: (starting_capital) =>
    apiClient.put("/paper/account-config", { starting_capital }).then((r) => r.data),
  listDeployments: (params = {}) =>
    apiClient.get("/deployments", { params }).then((r) => r.data),
  deploymentsOverview: () =>
    apiClient.get("/deployments/overview").then((r) => r.data),
  createDeployment: (payload) =>
    apiClient.post("/deployments", payload).then((r) => r.data),
  pauseDeployment: (id) =>
    apiClient.post(`/deployments/${id}/pause`).then((r) => r.data),
  resumeDeployment: (id) =>
    apiClient.post(`/deployments/${id}/resume`).then((r) => r.data),
  stopDeployment: (id) =>
    apiClient.post(`/deployments/${id}/stop`).then((r) => r.data),
  stopAllDeployments: () =>
    apiClient.post("/deployments/stop-all").then((r) => r.data),
  repinDeploymentSource: (id) =>
    apiClient.post(`/deployments/${id}/repin-source`).then((r) => r.data),
  archiveDeployment: (id, params = {}) =>
    apiClient.post(`/deployments/${id}/archive`, null, { params }).then((r) => r.data),
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

  // Live broker (Flattrade) — read-only, display only (L0)
  flattradeStatus: () => apiClient.get("/flattrade/status").then((r) => r.data),
  flattradeAuthStart: () => apiClient.get("/flattrade/auth/start").then((r) => r.data),
  disconnectFlattrade: () => apiClient.post("/flattrade/disconnect").then((r) => r.data),
  liveBrokerLimits: () => apiClient.get("/live-broker/limits").then((r) => r.data),
  liveBrokerPositions: () => apiClient.get("/live-broker/positions").then((r) => r.data),
  liveBrokerOrders: () => apiClient.get("/live-broker/orders").then((r) => r.data),
  liveBrokerReconcile: () => apiClient.get("/live-broker/reconcile").then((r) => r.data),
  getLiveBlotter: (limit = 100) =>
    apiClient.get("/live-broker/blotter", { params: { limit } }).then((r) => r.data),

  // Live broker mode + L3 order management
  getArmState: () => apiClient.get("/live-broker/arm-state").then((r) => r.data),
  getLiveMode: () => apiClient.get("/live-broker/mode").then((r) => r.data),
  setLiveMode: (mode, confirm) =>
    apiClient.put("/live-broker/mode", { mode, confirm }).then((r) => r.data),
  dryRunLiveOrder: (payload) =>
    apiClient.post("/live-broker/order/dry-run", payload).then((r) => r.data),
  placeLiveTestOrder: (payload) =>
    apiClient.post("/live-broker/order/place", payload).then((r) => r.data),
  squareLivePosition: () =>
    apiClient.post("/live-broker/order/square", {}).then((r) => r.data),
  getLiveTestSession: () =>
    apiClient.get("/live-broker/test-session").then((r) => r.data),
  liveKillSwitch: () =>
    apiClient.post("/live-broker/kill-switch").then((r) => r.data),
  getOptionPremium: ({ underlying, strike, expiry_date, side }) =>
    apiClient.post("/live-broker/option-premium", { underlying, strike, expiry_date, side }).then((r) => r.data),
  getAtmSuggest: ({ underlying, side }) =>
    apiClient.get("/live-broker/atm-suggest", { params: { underlying, side } }).then((r) => r.data),

  // Live order page (P1.7) — choke-point preview + approval queue
  getOrderRules: (underlying) =>
    apiClient.get(`/live-broker/order-rules/${underlying}`).then((r) => r.data),
  previewLiveOrder: (payload) =>
    apiClient.post("/live-broker/order/preview", payload).then((r) => r.data),
  createOrderApproval: (payload) =>
    apiClient.post("/live-broker/order/approvals", payload).then((r) => r.data),
  listOrderApprovals: () =>
    apiClient.get("/live-broker/order/approvals").then((r) => r.data),
  approveOrder: (approvalId, token) =>
    apiClient.post(`/live-broker/order/approvals/${approvalId}/approve`, { token }).then((r) => r.data),
  rejectOrder: (approvalId) =>
    apiClient.post(`/live-broker/order/approvals/${approvalId}/reject`, {}).then((r) => r.data),

  // Deploy-to-Live: arm/disarm/stop/status per deployment + account safety config
  getSafetyConfig: () => apiClient.get("/live-broker/safety-config").then((r) => r.data),
  liveArm: (id, body) =>
    apiClient.post(`/deployments/${id}/live/arm`, body).then((r) => r.data),
  liveDisarm: (id) =>
    apiClient.post(`/deployments/${id}/live/disarm`).then((r) => r.data),
  liveStop: (id) =>
    apiClient.post(`/deployments/${id}/live/stop`).then((r) => r.data),
  liveStatus: (id) =>
    apiClient.get(`/deployments/${id}/live/status`).then((r) => r.data),
  // Batched: one request for many deployments → { id: <same per-id payload> }.
  liveStatusBatch: (ids) =>
    apiClient
      .get("/deployments/live/status", { params: { ids: (ids || []).join(",") } })
      .then((r) => r.data),

  // Live order page (Phase 2/3) — overall controls + GTT backstop
  getOverallSettings: (scope = "overall") =>
    apiClient.get("/live-broker/overall-settings", { params: { scope } }).then((r) => r.data),
  putOverallSettings: (scope, config) =>
    apiClient.put("/live-broker/overall-settings", { config }, { params: { scope: scope || "overall" } }).then((r) => r.data),
  listGtt: () =>
    apiClient.get("/live-broker/gtt").then((r) => r.data),
  placeGtt: (payload) =>
    apiClient.post("/live-broker/gtt", payload).then((r) => r.data),
  cancelGtt: (alId, kind = "gtt") =>
    apiClient
      .delete(`/live-broker/gtt/${alId}`, { params: { kind } })
      .then((r) => r.data),
  getGuardStatus: () =>
    apiClient.get("/live-broker/guard-status").then((r) => r.data),
  getLiveGreeks: () =>
    apiClient.get("/live-broker/greeks").then((r) => r.data),

  // Presets
  listPresets: () => apiClient.get("/presets").then((r) => r.data),
  savePreset: (name, config) =>
    apiClient.put(`/presets/${name}`, { name, config }).then((r) => r.data),
  renamePreset: (name, newName) =>
    apiClient.post(`/presets/${encodeURIComponent(name)}/rename`, null, { params: { new_name: newName } }).then((r) => r.data),
  deletePreset: (name) =>
    apiClient.delete(`/presets/${name}`).then((r) => r.data),
};
