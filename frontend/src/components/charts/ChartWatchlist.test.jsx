import React, { act } from "react";
import { createRoot } from "react-dom/client";
import ChartWatchlist from "./ChartWatchlist";
import InstrumentSearch from "./InstrumentSearch";
import { INDEX_SHORTCUTS } from "./chartPresentation";
import { chartWatchlistApi as api } from "../../lib/chartWatchlistApi";
import { chartApi } from "../../lib/chartApi";

jest.mock("../../lib/chartWatchlistApi", () => ({ chartWatchlistApi: { get: jest.fn(), save: jest.fn(), stream: jest.fn() } }));
jest.mock("../../lib/chartApi", () => ({ chartApi: { instruments: jest.fn() } }));
let container, root, server, listeners, onSelect;
const [a, b] = INDEX_SHORTCUTS;
const find = (label) => container.querySelector(`[aria-label="${label}"]`);
const click = async (label) => act(async () => find(label).click());
const input = async (label, value) => act(async () => {
  const element = find(label);
  Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set.call(element, value);
  element.dispatchEvent(new Event("input", { bubbles: true }));
});
const submit = async (label) => act(async () => find(label).closest("form").dispatchEvent(new Event("submit", { bubbles: true, cancelable: true })));
beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true; jest.clearAllMocks(); jest.useFakeTimers(); onSelect = jest.fn(); listeners = {};
  server = { id: "default", name: "Session watchlist", revision: 1, capacity: 200, items: [{ id: "a", instrument: a, label: "" }, { id: "b", instrument: b, label: "" }] };
  api.get.mockImplementation(async () => server);
  api.save.mockImplementation(async (payload) => {
    server = { ...server, name: payload.name, revision: server.revision + 1, items: payload.items.map((item, index) => ({
      id: item.id || `added-${index}`, instrument: INDEX_SHORTCUTS.find((candidate) => candidate.instrument_key === item.instrument_key), label: item.label,
    })) }; return server;
  });
  api.stream.mockImplementation(() => ({ addEventListener: (type, listener) => { listeners[type] = listener; }, close: jest.fn() }));
  chartApi.instruments.mockResolvedValue({ items: INDEX_SHORTCUTS });
  container = document.createElement("div"); document.body.appendChild(container); root = createRoot(container);
});
afterEach(async () => { await act(async () => root.unmount()); container.remove(); jest.useRealTimers(); });
const render = async (props = {}) => act(async () => root.render(<ChartWatchlist activeInstrument={a} onSelect={onSelect} {...props} />));

test("user can rename list and label, reorder, remove and choose a chart through visible controls", async () => {
  await render(); expect(find("Load NIFTY chart").getAttribute("aria-pressed")).toBe("true");
  await click("Rename watchlist"); await input("Watchlist name", "Opening session"); await submit("Watchlist name"); expect(container.textContent).toContain("Opening session");
  await click("Rename NIFTY"); await input("Display label for NIFTY", "Main index"); await submit("Display label for NIFTY"); expect(find("Load Main index chart")).not.toBeNull();
  await click("Move BANKNIFTY up"); expect(container.querySelector("ol li button").getAttribute("aria-label")).toBe("Load BANKNIFTY chart");
  await click("Load Main index chart"); expect(onSelect).toHaveBeenCalledWith(a);
  await click("Remove BANKNIFTY"); expect(find("Load BANKNIFTY chart")).toBeNull(); expect(container.textContent).toContain("1 / 200");
  expect(api.save).toHaveBeenCalledTimes(4); expect(container.querySelector('[aria-label*="option chain"]')).toBeNull();
});

test("add dialog reuses instrument search with independent ARIA ids and saves chosen instrument", async () => {
  await act(async () => root.render(<><InstrumentSearch onSelect={jest.fn()} /><ChartWatchlist activeInstrument={a} onSelect={onSelect} /></>));
  await click("Add symbol");
  const searches = [...container.querySelectorAll('[role="combobox"]')]; expect(searches).toHaveLength(2);
  expect(searches[0].getAttribute("aria-controls")).not.toBe(searches[1].getAttribute("aria-controls"));
  expect(document.activeElement).toBe(searches[1]);
  await act(async () => jest.advanceTimersByTime(230));
  await act(async () => [...container.querySelectorAll('[role="option"]')].find((option) => option.textContent.includes("SENSEX")).click());
  expect(container.querySelector('[role="dialog"]')).toBeNull(); expect(find("Load SENSEX chart")).not.toBeNull(); expect(container.textContent).toContain("3 / 200");
  expect(document.activeElement).toBe(find("Add symbol"));
});

test("price, previous-close percentage and unavailable identity reason remain explicit", async () => {
  await render({ onOptionChain: jest.fn() });
  await act(async () => listeners.snapshot({ data: JSON.stringify({ revision: 1, quotes: {
    [a.instrument_key]: { tick: { last_price: 110, close_price: 100, ts: Date.now() }, last_tick_age_ms: 0, connected: true, status: "live" },
    [b.instrument_key]: { tick: {}, connected: false, status: "unavailable", warnings: ["Saved instrument identity changed."] },
  } }) }));
  expect(container.textContent).toContain("110.00"); expect(container.textContent).toContain("+10.00%"); expect(container.textContent).toContain("Live");
  expect(container.textContent).toContain("Unavailable"); expect(container.textContent).toContain("Saved instrument identity changed.");
  expect(find("Open NIFTY option chain")).not.toBeNull();
  await act(async () => jest.advanceTimersByTime(13000)); expect(container.textContent).toContain("Offline"); expect(container.textContent).toContain("110.00"); expect(container.textContent).toContain("IST");
});
