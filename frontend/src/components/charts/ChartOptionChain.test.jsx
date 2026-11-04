import React, { act } from "react";
import { createRoot } from "react-dom/client";
import ChartOptionChain from "./ChartOptionChain";
import useChartOptionChain from "./useChartOptionChain";
jest.mock("./useChartOptionChain");
let container, root, state, onSelect, onClose, opener;
const instrument = { instrument_key: "NIFTY", symbol: "NIFTY" };
const leg = { instrument: { instrument_key: "NSE_FO|123", symbol: "NIFTY CE", kind: "option" }, ltp: 0, iv: 12.5, oi_change: -10, oi: null, volume: 0, quote_time_ms: null, quote_source: "rest" };
const find = (label) => container.querySelector(`[aria-label="${label}"]`);
beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true; jest.clearAllMocks(); onSelect = jest.fn(); onClose = jest.fn();
  state = { loading: false, error: "", empty: false, expiry: "2026-11-10", expiries: ["2026-11-10"], status: "Snapshot", streamHealthy: false, warnings: [], center: null,
    data: { underlying: instrument, spot: 25000, atm_strike: 25000, total_strikes: 80, captured_at_ms: Date.now(), rows: [{ strike: 25000, atm: true, ce: leg, pe: null }] },
    setExpiry: jest.fn(), refresh: jest.fn(), moveWindow: jest.fn(), backToAtm: jest.fn() };
  useChartOptionChain.mockImplementation(() => state); container = document.createElement("div"); document.body.appendChild(container);
  opener = document.createElement("button"); document.body.appendChild(opener); opener.focus(); root = createRoot(container);
});
afterEach(async () => { await act(async () => root.unmount()); container.remove(); opener.remove(); });
const render = async () => act(async () => root.render(<ChartOptionChain instrument={instrument} onSelect={onSelect} onClose={onClose} />));
test("renders missing distinct from zero and identifies vendor IV and previous OI reference", async () => {
  await render(); expect(container.textContent).toContain("0.00"); expect(container.textContent).toContain("12.50"); expect(container.textContent).toContain("IV (vendor)"); expect(container.textContent).toContain("to vendor previous");
  expect(container.textContent).toContain("Received"); expect(container.textContent).toContain("exchange quote time unavailable"); expect(container.querySelector("tr.is-atm")).not.toBeNull(); expect(container.querySelectorAll("td")[1].textContent).toBe("—");
});
test("price and explicit Chart action only hand canonical metadata back and close", async () => {
  await render(); await act(async () => find("Load 25000 CE chart at price").click()); expect(onSelect).toHaveBeenCalledWith(leg.instrument); expect(onClose).toHaveBeenCalledTimes(1);
  await act(async () => find("Load 25000 CE chart").click()); expect(onSelect).toHaveBeenCalledTimes(2); expect(onClose).toHaveBeenCalledTimes(2);
  expect(container.querySelector('button[aria-label*="order"]')).toBeNull();
});
test("dialog traps keyboard focus, closes on Escape and restores the opener", async () => {
  await render(); expect(document.activeElement).toBe(find("Close option chain"));
  const buttons = [...container.querySelectorAll("button:not(:disabled), select:not(:disabled)")]; const last = buttons[buttons.length - 1]; last.focus();
  await act(async () => last.dispatchEvent(new KeyboardEvent("keydown", { key: "Tab", bubbles: true, cancelable: true }))); expect(document.activeElement).toBe(find("Close option chain"));
  await act(async () => find("Close option chain").dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }))); expect(onClose).toHaveBeenCalledTimes(1);
  await act(async () => root.unmount()); expect(document.activeElement).toBe(opener); root = createRoot(container);
});
test("toolbar routes expiry, refresh, window and ATM actions", async () => {
  state.data.rows.push({ strike: 25050, ce: null, pe: null });
  await render(); await act(async () => find("Option expiry").dispatchEvent(new Event("change", { bubbles: true }))); expect(state.setExpiry).toHaveBeenCalledWith("2026-11-10");
  for (const label of ["Refresh option chain", "Lower strikes", "Higher strikes", "Back to ATM"]) await act(async () => find(label).click());
  expect(state.refresh).toHaveBeenCalledTimes(1); expect(state.moveWindow.mock.calls).toEqual([[-1], [1]]); expect(state.backToAtm).toHaveBeenCalledTimes(1);
});
test("empty and failed snapshots explain absence without fabricated values", async () => {
  state.data = null; state.error = "Option chain could not be loaded."; await render(); expect(container.querySelector('[role="alert"]').textContent).toContain("could not");
  state.error = ""; state.empty = true; await render(); expect(container.textContent).toContain("No listed option expiries"); expect(container.querySelector("tbody")).toBeNull();
});

test("centers ATM on a new window while quote-only updates preserve manual scroll", async () => {
  const geometry = jest.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockImplementation(function () {
    return { top: this.matches("tr.is-atm") ? 900 : 100, height: this.matches("tr.is-atm") ? 40 : 400 };
  });
  const height = jest.spyOn(HTMLElement.prototype, "clientHeight", "get").mockReturnValue(400);
  try {
    await render(); const scroller = container.querySelector(".chart-option-scroll");
    expect(scroller.scrollTop).toBe(620);
    scroller.scrollTop = 123;
    state.data = { ...state.data, captured_at_ms: state.data.captured_at_ms + 1000,
      rows: [{ ...state.data.rows[0], ce: { ...leg, ltp: 456 } }] };
    await render(); expect(scroller.scrollTop).toBe(123);
    state.center = 27000;
    state.data = { ...state.data, rows: [{ strike: 27000, ce: leg, pe: null }] };
    await render(); expect(scroller.scrollTop).toBe(0);
    state.center = null; state.data = { ...state.data, rows: [{ strike: 25000, atm: true, ce: leg, pe: null }] };
    await render(); expect(scroller.scrollTop).toBe(620);
  } finally { geometry.mockRestore(); height.mockRestore(); }
});
