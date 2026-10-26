import { apiClient } from "./api";

const root = "/conditional-orders";
// Mutations are intentionally never retried by this client.
export const conditionalApi = {
  list: () => apiClient.get(root, { timeout: 10000 }).then(r => r.data),
  metadata: underlying => apiClient.get(`${root}/metadata`, { params: { underlying } }).then(r => r.data),
  preview: spec => apiClient.post(`${root}/preview`, spec).then(r => r.data),
  arm: (preview_id, confirm_live) => apiClient.post(`${root}/arm`, { preview_id, confirm_live }).then(r => r.data),
  cancel: id => apiClient.post(`${root}/${encodeURIComponent(id)}/cancel`, {}).then(r => r.data),
  kill: mode => apiClient.post(`${root}/kill`, { mode }).then(r => r.data),
  reset: mode => apiClient.post(`${root}/reset`, { mode }).then(r => r.data),
};
