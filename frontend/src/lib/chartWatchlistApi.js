import { API, apiClient } from "./api";

// Only watchlist preferences are writable here; quote streams reuse server feed ownership.
export const chartWatchlistApi = {
  get: ({ signal } = {}) => apiClient.get("/charts/watchlist", { signal }).then(({ data }) => data),
  save: (watchlist, { signal } = {}) => apiClient.put("/charts/watchlist", watchlist, { signal }).then(({ data }) => data),
  stream: (revision) => new EventSource(`${API}/charts/watchlist/stream?revision=${encodeURIComponent(revision)}`),
};
