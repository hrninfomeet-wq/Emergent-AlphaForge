import { apiClient } from "./api";

// No automatic retries: a timeout does not prove a mutation failed.
export const chartTradingApi = {
  metadata: instrumentKey => apiClient.get("/conditional-orders/chart/metadata", { params: { instrument_key: instrumentKey } }).then(r => r.data),
  orders: () => apiClient.get("/conditional-orders/chart/orders").then(r => r.data),
  preview: spec => apiClient.post("/conditional-orders/chart/preview", spec).then(r => r.data),
  arm: (preview_id, confirm_live) => apiClient.post("/conditional-orders/arm", { preview_id, confirm_live }).then(r => r.data),
  change: (id, body) => apiClient.post(`/conditional-orders/${encodeURIComponent(id)}/chart-change`, body).then(r => r.data),
};
