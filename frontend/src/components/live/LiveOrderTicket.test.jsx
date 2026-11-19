import React, { act } from "react";
import { createRoot } from "react-dom/client";
import LiveOrderTicket from "./LiveOrderTicket";
import { api } from "@/lib/api";

// This UI fixture has no HTTP client. Arming/redeeming is forbidden, even here.
jest.mock("@/lib/api", () => ({ api: {
  getOrderRules: jest.fn(), previewLiveOrder: jest.fn(), createOrderApproval: jest.fn(),
  setLiveMode: jest.fn(() => { throw new Error("arming forbidden in UI test"); }),
  approveOrder: jest.fn(() => { throw new Error("redemption forbidden in UI test"); }),
} }));
jest.mock("./PayoffChart", () => () => null);

const entry = { exch: "NFO", tsym: "NIFTY29OCT26C25000", trantype: "B",
  prd: "I", prctyp: "LMT", ret: "DAY", qty: "65", prc: "201" };
let container, root;

beforeEach(async () => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  jest.clearAllMocks();
  api.getOrderRules.mockResolvedValue({ exch: "NFO", lot_size: 65, freeze_qty: 3510,
    tick: 0.05, price_types: ["LIMIT", "MARKET", "SL-LMT"], products: ["MIS", "NRML"] });
  api.previewLiveOrder.mockResolvedValue({ ok: true, placement_supported: true,
    children: [entry], verdicts: [] });
  api.createOrderApproval.mockResolvedValue({ ok: true, approval_id: "fixture", token: "fixture",
    summary: { would_send: [entry] } });
  container = document.createElement("div"); document.body.appendChild(container);
  root = createRoot(container);
  await act(async () => { root.render(<LiveOrderTicket />); });
});

afterEach(async () => {
  expect(api.setLiveMode).not.toHaveBeenCalled();
  expect(api.approveOrder).not.toHaveBeenCalled();
  await act(async () => root.unmount());
  container.remove();
});

async function fill(label, value) {
  const el = [...container.querySelectorAll("label")].find(l => l.textContent.startsWith(label))
    ?.parentElement.querySelector("input");
  if (!el) throw new Error(`Missing input ${label}`);
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set.call(el, value);
    el.dispatchEvent(new Event("input", { bubbles: true }));
  });
}
function button(text) {
  return [...container.querySelectorAll("button")].find(b => b.textContent.includes(text));
}
async function preview() {
  await fill("Strike", "25000"); await fill("Expiry", "2026-10-29");
  await fill("Ref LTP", "200");
  await act(async () => button("Preview").click());
}

test("confirmation shows normalized guard/band and the resolved approved limit", async () => {
  expect(container.textContent).toContain("App-reported rules · running build / broker state unverified");
  expect(container.textContent).toContain("Selectable by app rules: MIS / NRML · LIMIT / SL-LMT");
  expect(container.textContent).toContain("Manual placement supports One-lot BUY · MIS · LIMIT only");
  expect(container.textContent).toContain("CO/BO are unavailable in this app's order rules");
  expect(container.textContent).toContain("does not verify any broker-held exit protection");
  await fill("Stop %", "0"); await fill("Target %", "-10"); await fill("Band %", "");
  await preview();
  expect([...container.querySelectorAll("option")].some(o => o.value === "MARKET")).toBe(false);
  await act(async () => button("Place order — REAL MONEY").click());
  expect(container.textContent).toContain("Final confirm");
  expect(container.textContent).toContain("guard stop 50%");
  expect(container.textContent).toContain("band 5%");
  expect(container.textContent).not.toContain("guard stop 0%");
  expect(container.textContent).not.toContain("target -10%");
  expect(api.createOrderApproval.mock.calls[0][0].levels).toEqual({ stop_pct: 50 });
});

test("changed resolved quantity requires a new preview before confirmation", async () => {
  await preview();
  api.createOrderApproval.mockResolvedValueOnce({ ok: true, approval_id: "changed", token: "fixture",
    summary: { would_send: [{ ...entry, qty: "130" }] } });
  await act(async () => button("Place order — REAL MONEY").click());
  expect(container.textContent).toContain("The resolved order changed");
  expect(container.textContent).not.toContain("Final confirm");
});

test("fractional or changed lots cannot reuse a previous preview", async () => {
  await preview();
  await fill("Lots", "1.9");
  expect(button("Preview").disabled).toBe(true);
  expect(button("Place order — REAL MONEY")).toBeUndefined();
  expect(api.createOrderApproval).not.toHaveBeenCalled();
});
