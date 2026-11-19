import { act } from "react";
import { createRoot } from "react-dom/client";
import ResearchComparison from "./ResearchComparison";
import { api } from "@/lib/api";

// No axios client, service, database or broker exists in these UI fixtures.
jest.mock("@/lib/api", () => ({ api: {
  listOptJobs: jest.fn(), getOptJob: jest.fn(), getBacktestRun: jest.fn(),
} }));
let container, root;
const saved = id => ({ id, name: id, params_applied: { threshold: id === "first" ? 2 : 3 },
  instrument: "NIFTY", strategy_id: "fixture", config: { timeframe: "1m", start_ts: 1000, end_ts: 2000,
    costs_enabled: true, pretrade_filters: {}, trade_window_start: "09:25", trade_window_end: "14:50",
    option_backtest: { enabled: false } }, metrics: { total_pnl_pts: 25, trade_count: 4 } });
beforeEach(async () => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  jest.clearAllMocks();
  api.listOptJobs.mockResolvedValue({ items: [{ id: "job", name: "Saved job", status: "done" }] });
  api.getOptJob.mockResolvedValue({ id: "job", status: "done", best_backtest_run_id: "winner",
    param_space: { threshold: { min: 1, max: 5 } }, best_params: { threshold: 3 } });
  api.getBacktestRun.mockImplementation(async id => saved(id));
  container = document.createElement("div"); document.body.appendChild(container); root = createRoot(container);
  await act(async () => root.render(<ResearchComparison pastRuns={[saved("first"), saved("second")]} presets={[
    { name: "Known", config: { strategy_id: "fixture", params: { threshold: 2 }, execution: { lots: 5 } } },
  ]} />));
});
afterEach(async () => { await act(async () => root.unmount()); container.remove(); });
async function select(label, value) {
  await act(async () => { const el = container.querySelector(`[aria-label="${label}"]`);
    el.value = value; el.dispatchEvent(new Event("change", { bubbles: true })); });
}
async function open() { await act(async () => container.querySelector("button").click()); }

test("comparison loads on demand and displays effective unknown/difference evidence", async () => {
  expect(api.listOptJobs).not.toHaveBeenCalled(); await open();
  await select("Comparison source", "run:first"); await select("Comparison optimization job", "job");
  expect(api.getOptJob).toHaveBeenCalledWith("job");
  expect(api.getBacktestRun.mock.calls.map(args => args[0])).toEqual(["first", "winner"]);
  expect(container.querySelector('[data-testid="research-comparison-status"]').textContent).toBe("Same recorded settings");
  expect(container.textContent).toContain("Dataset identity unknown");
  expect(container.textContent).toContain("threshold"); expect(container.textContent).toContain('"max": 5');
  expect(container.textContent).toContain("inclusion is unknown");
});

test("preset selection displays its policy without calling it an effective result", async () => {
  await open(); await select("Comparison source", "preset:Known"); await select("Comparison optimization job", "job");
  expect(api.getBacktestRun.mock.calls.map(args => args[0])).toEqual(["winner"]);
  expect(container.textContent).toContain("A preset records a declared policy");
  expect(container.textContent).toContain('"lots": 5');
  expect(container.querySelector('[data-testid="research-comparison-status"]').textContent).toContain("Unknown");
});

test("switching source hides the old result and ignores a late first read", async () => {
  let resolve;
  api.getBacktestRun.mockImplementation(id => id === "first" ? new Promise(r => { resolve = r; }) : Promise.resolve(saved(id)));
  await open(); await select("Comparison source", "run:first"); await select("Comparison optimization job", "job");
  await select("Comparison source", "run:second");
  expect(container.textContent).toContain("Source: second.");
  await act(async () => resolve(saved("first")));
  expect(container.textContent).toContain("Source: second."); expect(container.textContent).not.toContain("Source: first.");
});

test("missing linked saved run remains unknown rather than using job request settings", async () => {
  api.getOptJob.mockResolvedValue({ id: "job", status: "done", best_params: { threshold: 3 },
    config: { costs_enabled: true, option_config: { lots: 100 } } });
  await open(); await select("Comparison source", "run:first"); await select("Comparison optimization job", "job");
  expect(container.textContent).toContain("job has no linked saved backtest");
  expect(container.querySelector('[data-testid="research-comparison-status"]').textContent).toContain("Unknown");
  expect(api.getBacktestRun.mock.calls.map(args => args[0])).toEqual(["first"]);
});
