import React, { act } from "react";
import { createRoot } from "react-dom/client";
import ConditionalOrderPanel from "./ConditionalOrderPanel";
import { conditionalApi } from "@/lib/conditionalApi";

// Every API method is mocked. Tests cannot contact or arm a live broker.
jest.mock("@/lib/conditionalApi", () => ({ conditionalApi: {
  list: jest.fn(), metadata: jest.fn(), preview: jest.fn(), arm: jest.fn(),
  kill: jest.fn(), reset: jest.fn(), cancel: jest.fn(),
} }));

let container, root;
const reference = { contract: { trading_symbol: "NIFTY 25000 CE 29 OCT 26" }, premium: 200, spot: 25000 };
beforeEach(async () => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  jest.clearAllMocks();
  conditionalApi.list.mockResolvedValue({ orders: [], controls: { paper: { latched: false }, live: { latched: false } }, health: { state: "running" } });
  conditionalApi.metadata.mockResolvedValue({ expiries: ["2026-10-29"], contracts: [{ expiry: "2026-10-29", option_type: "CE", strike: 25000 }], live_available: true });
  conditionalApi.preview.mockResolvedValue({ preview_id: "preview-fixture", reference, live_available: true,
    expires_at: 1793260800, summary: { planned_loss: 1300, premium_exposure: 13000 } });
  conditionalApi.arm.mockResolvedValue({ order: { id: "order-fixture", status: "ARMED", reference } });
  container = document.createElement("div"); document.body.appendChild(container);
  root = createRoot(container);
  await act(async () => { root.render(<ConditionalOrderPanel />); });
});
afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
});

function control(label) {
  const wrapper = [...container.querySelectorAll("label")].find(node => node.textContent.startsWith(label));
  if (!wrapper) throw new Error(`Missing label ${label}`);
  return wrapper.querySelector("input,select");
}
async function fill(label, value) {
  const el = control(label);
  await act(async () => {
    const proto = el.tagName === "SELECT" ? HTMLSelectElement.prototype : HTMLInputElement.prototype;
    Object.getOwnPropertyDescriptor(proto, "value").set.call(el, value);
    el.dispatchEvent(new Event(el.tagName === "SELECT" ? "change" : "input", { bubbles: true }));
  });
}
async function toggle(label) { await act(async () => control(label).click()); }
function button(text) { return [...container.querySelectorAll("button")].find(node => node.textContent.includes(text)); }
async function click(text) { await act(async () => button(text).click()); }
async function preview() {
  await fill("Expiry from instrument master", "2026-10-29");
  await click("Preview conditional order");
}

test("defaults to paper and transmits the normalized request without live confirmation", async () => {
  expect(control("Execution mode").value).toBe("paper");
  await preview();
  expect(conditionalApi.preview).toHaveBeenCalledWith(expect.objectContaining({
    mode: "paper", underlying: "NIFTY", expiry: "2026-10-29", lots: 1,
    strike: { kind: "ATM", offset: 0 }, band_pct: 3,
    spot_condition: null, premium_condition: null, valid_minutes: 30,
    exits: { stop: { value: 10, unit: "percent" }, target: null, trailing: null, profit_lock: null, square_off: "15:00" },
  }));
  expect(container.textContent).toContain("Indicative reference contract");
  expect(container.textContent).toContain("NIFTY 25000 CE 29 OCT 26");
  expect(container.textContent).toContain("server captures a fresh reference");
  await click("Arm paper order");
  expect(conditionalApi.arm).toHaveBeenCalledTimes(1);
  expect(conditionalApi.arm).toHaveBeenCalledWith("preview-fixture", false);
});

test("live preview requires maximum premium and arming requires explicit confirmation", async () => {
  await fill("Execution mode", "live");
  await fill("Expiry from instrument master", "2026-10-29");
  expect(button("Preview conditional order").disabled).toBe(true);
  await fill("Maximum entry premium", "220");
  await click("Preview conditional order");
  expect(conditionalApi.preview).toHaveBeenCalledWith(expect.objectContaining({ mode: "live", max_entry_premium: 220 }));
  expect(button("Arm LIVE").disabled).toBe(true);
  await toggle("I confirm this live order");
  expect(button("Arm LIVE").disabled).toBe(false);
  await click("Arm LIVE");
  expect(conditionalApi.arm).toHaveBeenCalledWith("preview-fixture", true);
});

test("editing after preview invalidates the confirmation and old token", async () => {
  await preview();
  expect(button("Arm paper order")).toBeTruthy();
  await fill("Lots", "2");
  expect(button("Arm paper order")).toBeUndefined();
  expect(conditionalApi.arm).not.toHaveBeenCalled();
});

test("combined conditions and both trailing policies preserve explicit units", async () => {
  await toggle("Use underlying level"); await fill("Underlying spot level", "22310");
  await toggle("Use option premium move"); await fill("Premium move", "8"); await fill("Premium move units", "percent");
  await toggle("Use trailing stop"); await toggle("Use profit-lock trailing");
  await fill("Strike selection", "OTM"); await fill("Strike steps", "2");
  await preview();
  const spec = conditionalApi.preview.mock.calls[0][0];
  expect(spec.spot_condition).toEqual({ direction: "below", price: 22310, mode: "cross" });
  expect(spec.premium_condition).toEqual({ direction: "rise", value: 8, unit: "percent" });
  expect(spec.exits.trailing).toEqual({ activation: 10, distance: 5, step: 1 });
  expect(spec.exits.profit_lock).toEqual({ activation: 15, profit: 5, step: 5, raise_by: 5 });
  expect(spec.strike).toEqual({ kind: "OTM", offset: 2 });
});

test("server arming errors remain visible and are not retried", async () => {
  conditionalApi.arm.mockRejectedValueOnce({ response: { data: { detail: "Preview expired; prepare again" } } });
  await preview(); await click("Arm paper order");
  expect(container.textContent).toContain("Preview expired; prepare again");
  expect(conditionalApi.arm).toHaveBeenCalledTimes(1);
  expect(button("Arm paper order")).toBeUndefined();
});

test("kill is scoped to the selected mode", async () => {
  conditionalApi.kill.mockResolvedValue({});
  await click("Kill paper conditional orders");
  expect(conditionalApi.kill).toHaveBeenCalledWith("paper");
  expect(conditionalApi.arm).not.toHaveBeenCalled();
});
