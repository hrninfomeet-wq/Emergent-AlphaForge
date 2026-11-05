import React, { act } from "react";
import { createRoot } from "react-dom/client";
import ChartAnalysisPanel from "./ChartAnalysisPanel";
import { defaultAnalysisSettings } from "../../lib/chartAnalysisSettings";
let root, host;
beforeEach(() => { globalThis.IS_REACT_ACT_ENVIRONMENT = true; host = document.createElement("div"); document.body.append(host); root = createRoot(host); });
afterEach(async () => { await act(async () => root.unmount()); host.remove(); });
test("independent concepts keep unavailable controls disabled and never change forecast implicitly", async () => {
  const settings = defaultAnalysisSettings(), onChange = jest.fn();
  await act(async () => root.render(<ChartAnalysisPanel settings={settings} onChange={onChange} analysis={{ catalog: { overlays: [{ id: "fvg", available: true }, { id: "volume_imbalance", available: false, reason: "Order flow unavailable" }], strategies: [] } }} />));
  await act(async () => host.querySelector('[aria-label="Fair value gaps"]').click());
  const next = onChange.mock.calls[0][0]; expect(next.overlays.fvg).toBe(true); expect(next.overlays.order_blocks).toBe(false); expect(next.forecast).toBe(false);
  expect(host.querySelector('[aria-label="Volume imbalance"]').disabled).toBe(true); expect(host.textContent).toContain("Order flow unavailable");
});
test("forecast distinguishes next-close probability from accuracy, scores and absent metrics", async () => {
  await act(async () => root.render(<ChartAnalysisPanel settings={{ ...defaultAnalysisSettings(), forecast: true }} onChange={() => {}} analysis={{ stale: true, data: { prediction: { status: "logged", model: "analogue", sample_size: 24, zone_low: 99, zone_high: 102, probabilities: { up: 0.6, flat: 0.1, down: 0.3 }, reasons: ["Historical analogue"] }, scores: { evaluated: 0, pending: 1, model_mae: null } } }} />));
  expect(host.textContent).toContain("model probability, not accuracy"); expect(host.textContent).toContain("Next-close zone"); expect(host.textContent).toContain("Samples 24"); expect(host.textContent).toContain("60.0%"); expect(host.textContent).toContain("MAE model / no-change — / —"); expect(host.textContent).toContain("STALE"); expect(host.querySelector('button')).toBeNull();
});

test('elapsed target is shown as historical and preview reasons stay visible', async () => {
  const data = { prediction: { status: 'logged', target_end: 60, probabilities: { up: .3, flat: .5, down: .2 }, reasons: [] } };
  const signals = [{ id: 's', time: 60, direction: 'CE', reasons: ['sweep low+reclaim'] }];
  await act(async () => root.render(<ChartAnalysisPanel now={61000} settings={{ ...defaultAnalysisSettings(), forecast: true, strategyId: 'smc_liquidity_sweep_fvg' }} onChange={() => {}} analysis={{ data, signals }} />));
  expect(host.textContent).toContain('Forecast horizon closed'); expect(host.textContent).toContain('Historical next-close zone');
  expect(host.textContent).toContain('Leading outcome: flat'); expect(host.textContent).toContain('sweep low+reclaim');
});
