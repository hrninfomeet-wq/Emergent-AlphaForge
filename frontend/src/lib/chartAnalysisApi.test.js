import { apiClient } from "./api";
import { chartAnalysisApi } from "./chartAnalysisApi";
jest.mock("./api", () => ({ apiClient: { post: jest.fn(), get: jest.fn() } }));
test("research API sends only authoritative-context selectors, never candles or execution fields", async () => {
  apiClient.post.mockResolvedValue({ data: { ok: true } }); apiClient.get.mockResolvedValue({ data: { overlays: [] } });
  const controller = new AbortController();
  await chartAnalysisApi.observe("NSE_INDEX|Nifty 50", "1m", { strategyId: "ema", forecast: true, bars: [{ close: 1 }], quantity: 99 }, { signal: controller.signal });
  expect(apiClient.post).toHaveBeenCalledWith("/charts/analysis/observe", { instrument_key: "NSE_INDEX|Nifty 50", timeframe: "1m", strategy_id: "ema", forecast: true }, { signal: controller.signal, timeout: 15000 });
  await chartAnalysisApi.catalog("NSE_INDEX|Nifty 50", "1m", { signal: controller.signal });
  expect(apiClient.get).toHaveBeenCalledWith("/charts/analysis/catalog", { params: { instrument_key: "NSE_INDEX|Nifty 50", timeframe: "1m" }, signal: controller.signal, timeout: 15000 });
});
