import React, { act } from "react";
import { createRoot } from "react-dom/client";
import ChartTrading from "./ChartTrading";
import useChartTrading from "./useChartTrading";
import { chartTradingApi as api } from "../../lib/chartTradingApi";
jest.mock("../../lib/chartTradingApi", () => ({ chartTradingApi: { metadata: jest.fn(), orders: jest.fn(), preview: jest.fn(), arm: jest.fn(), change: jest.fn() } }));
let container, root, state;
const instrument = { instrument_key: "A", symbol: "A" };
const row = { id: "1", instrument_key: "A", symbol: "A", mode: "paper", active: true, chart_owned: true, status: "OPEN", quantity: 0, ordered_quantity: 50, order_type: "LIMIT", limit_price: 100, stop_price: 80, edit_version: 2 };
function Harness({ selected }) { state = useChartTrading(selected); return <ChartTrading instrument={selected} trading={state} />; }
async function render(selected = instrument) { await act(async () => root.render(<Harness selected={selected} />)); }
function button(text) { return [...container.querySelectorAll("button")].find(b => b.textContent === text); }
beforeEach(async () => { globalThis.IS_REACT_ACT_ENVIRONMENT = true; jest.clearAllMocks(); api.metadata.mockResolvedValue({ supported:true, live_available:false, live_reason:"Live locked", paper_order_types:["MARKET","LIMIT","STOP_LIMIT"], live_order_types:["LIMIT","STOP_LIMIT"] }); api.orders.mockResolvedValue({rows:[row],as_of_ms:1000}); api.preview.mockResolvedValue({preview_id:"p", summary:{symbol:"A",mode:"paper",side:"BUY",quantity:50,order_type:"LIMIT",limit_price:100,stop_price:80}}); api.arm.mockResolvedValue({}); api.change.mockResolvedValue({}); container=document.createElement("div"); document.body.appendChild(container); root=createRoot(container); await render(); });
afterEach(async () => { await act(async () => root.unmount()); container.remove(); });
test("paper default and unavailable live; entry waits for confirmation", async () => { expect(state.mode).toBe("paper"); expect(button("View LIVE").disabled).toBe(false); expect(container.textContent).toContain("Live locked"); expect([...container.querySelectorAll("option")].some(o => o.value === "MARKET")).toBe(true); await act(async () => state.setForm({lots:"1",order_type:"LIMIT",limit_price:"100",trigger_price:"",stop_price:"80",target_price:""})); await act(async () => state.prepare()); expect(api.arm).not.toHaveBeenCalled(); expect(container.querySelector('[role="dialog"]').textContent).toContain("A"); await act(async () => button("Confirm PAPER").click()); expect(api.arm).toHaveBeenCalledWith("p",false); });
test("change and cancel wait for confirmation", async () => { await act(async () => state.requestChange(row,"stop_price",85)); expect(api.change).not.toHaveBeenCalled(); await act(async () => button("Confirm PAPER").click()); expect(api.change).toHaveBeenCalledWith("1",{field:"stop_price",price:85,edit_version:2,confirm_live:false}); await act(async () => state.requestChange(row,"cancel")); expect(api.change).toHaveBeenCalledTimes(1); await act(async () => button("Confirm PAPER").click()); expect(api.change).toHaveBeenLastCalledWith("1",{field:"cancel",edit_version:2,confirm_live:false}); });
test("instrument or mode change invalidates confirmation", async () => { await act(async () => state.requestChange(row,"cancel")); await render({instrument_key:"B",symbol:"B"}); expect(container.querySelector('[role="dialog"]')).toBeNull(); await act(async () => state.confirm()); expect(api.change).not.toHaveBeenCalled(); api.metadata.mockResolvedValue({supported:true,live_available:true,paper_order_types:["LIMIT"],live_order_types:["LIMIT"]}); await render(); await act(async () => state.requestChange(row,"cancel")); await act(async () => state.setMode("live")); await act(async () => state.confirm()); expect(api.change).not.toHaveBeenCalled(); });
test("failed polling blocks changes", async () => { api.orders.mockRejectedValueOnce(new Error("offline")); await act(async () => state.refresh()); expect(state.stale).toBe(true); await act(async () => state.requestChange(row,"cancel")); expect(state.pending).toBeNull(); });
const fillTicket = async () => act(async () => state.setForm({lots:"1",order_type:"LIMIT",limit_price:"100",trigger_price:"",stop_price:"80",target_price:"120"}));
test("instant is off by default; paper activation reviews preview, then submits without a second confirmation", async () => {
  expect(state.instant).toBe(false); await fillTicket();
  await act(async () => button("Review Instant Order").click());
  expect(api.arm).not.toHaveBeenCalled(); expect(container.querySelector('[role="dialog"]').textContent).toContain("50");
  await act(async () => button("Enable Instant Order").click()); expect(state.instant).toBe(true);
  await act(async () => state.prepare()); expect(api.arm).toHaveBeenCalledWith("p",false);
  await act(async () => state.requestChange(row,"cancel")); expect(container.querySelector('[role="dialog"]').textContent).toContain("Confirm cancel order"); expect(api.change).not.toHaveBeenCalled();
  await render({instrument_key:"B",symbol:"B"}); expect(state.instant).toBe(false);
});
test("LIVE instant requires explicit acknowledgement and resets on mode switch", async () => {
  api.metadata.mockResolvedValue({supported:true,live_available:true,paper_order_types:["LIMIT"],live_order_types:["LIMIT"]});
  await render({instrument_key:"B",symbol:"B"}); await render(); await act(async () => state.setMode("live")); await fillTicket();
  await act(async () => button("Review Instant Order").click()); const enable = button("Enable Instant Order"); expect(enable.disabled).toBe(true);
  await act(async () => state.confirm()); expect(state.instant).toBe(false);
  const checkbox = container.querySelector('[role="dialog"] input[type="checkbox"]'); await act(async () => checkbox.click());
  await act(async () => enable.click()); expect(state.instant).toBe(true); expect(api.arm).not.toHaveBeenCalled();
  await act(async () => state.setMode("paper")); expect(state.instant).toBe(false);
});
test("an asynchronous preview for an old instrument never arms", async () => {
  await fillTicket(); await act(async () => button("Review Instant Order").click()); await act(async () => button("Enable Instant Order").click());
  let resolvePreview; api.preview.mockImplementationOnce(() => new Promise(resolve => { resolvePreview = resolve; }));
  let operation; await act(async () => { operation = state.prepare(); }); await render({instrument_key:"B",symbol:"B"});
  await act(async () => { resolvePreview({preview_id:"old",summary:{}}); await operation; }); expect(api.arm).not.toHaveBeenCalled();
});
test("dialog traps focus, closes with Escape and restores focus", async () => {
  await fillTicket(); const opener = button("Review Instant Order"); opener.focus(); await act(async () => opener.click());
  const dialog = container.querySelector('[role="dialog"]'); const buttons = dialog.querySelectorAll("button"); expect(document.activeElement).toBe(buttons[0]);
  buttons[buttons.length-1].focus(); await act(async () => document.dispatchEvent(new KeyboardEvent("keydown",{key:"Tab",bubbles:true}))); expect(document.activeElement).toBe(buttons[0]);
  await act(async () => document.dispatchEvent(new KeyboardEvent("keydown",{key:"Escape",bubbles:true}))); expect(container.querySelector('[role="dialog"]')).toBeNull(); expect(document.activeElement).toBe(opener);
});
test("LIVE instant submits with live acknowledgement and synchronously locks duplicate submits", async () => {
  api.metadata.mockResolvedValue({supported:true,live_available:true,paper_order_types:["LIMIT"],live_order_types:["LIMIT"]}); await render({instrument_key:"B",symbol:"B"}); await render();
  await act(async () => state.setMode("live")); await fillTicket(); await act(async () => button("Review Instant Order").click()); await act(async () => container.querySelector('[role="dialog"] input').click()); await act(async () => button("Enable Instant Order").click());
  let resolvePreview; api.preview.mockClear().mockImplementationOnce(() => new Promise(resolve => { resolvePreview = resolve; }));
  let first, second; await act(async () => { first = state.prepare(); second = state.prepare(); }); expect(api.preview).toHaveBeenCalledTimes(1);
  await act(async () => { resolvePreview({preview_id:"live",summary:{}}); await Promise.all([first,second]); }); expect(api.arm).toHaveBeenCalledTimes(1); expect(api.arm).toHaveBeenCalledWith("live",true);
});

test("unavailable live capability blocks entry and modification but allows confirmed cancel", async () => {
  const liveRow = {...row, mode:"live"}; api.orders.mockResolvedValue({rows:[liveRow],as_of_ms:1000}); await act(async () => state.refresh()); await act(async () => state.setMode("live")); await fillTicket();
  expect(button("Review BUY").disabled).toBe(true); await act(async () => state.requestChange(liveRow,"stop_price",85)); expect(state.pending).toBeNull();
  await act(async () => state.requestChange(liveRow,"cancel")); expect(state.pending.field).toBe("cancel"); expect(api.change).not.toHaveBeenCalled(); await act(async () => button("Confirm LIVE").click()); expect(api.change).toHaveBeenCalledWith("1",{field:"cancel",edit_version:2,confirm_live:true});
});
test("price and protection displays use precise readable labels", async () => {
  const marked = {...row, mark_price: 12345.100000001, protection:"software_only"}; api.orders.mockResolvedValue({rows:[marked],as_of_ms:1000}); await act(async () => state.refresh());
  expect(container.textContent).toContain("12,345.10"); expect(container.textContent).toContain("Software stop and target"); expect(container.textContent).toContain("IST");
});

test("closing a filled position confirms SELL with an estimated exit limit", async () => {
  const position = {...row, quantity:65, entry_price:200, mark_price:201};
  api.metadata.mockResolvedValue({supported:true, live_available:false, contract:{tick_size:.05}});
  await render({instrument_key:"B",symbol:"B"}); await render();
  await act(async () => state.requestChange(position,"cancel"));
  const dialog = container.querySelector('[role="dialog"]');
  expect(dialog.textContent).toContain("SELL (close)");
  expect(dialog.textContent).toContain("199.95");
  expect(dialog.textContent).toContain("estimate");
});
test("pending modification leaves cancel button and hook usable", async () => {
  const pendingRow = {...row, pending_change:true}; api.orders.mockResolvedValue({rows:[pendingRow],as_of_ms:1000});
  await act(async () => state.refresh()); await act(async () => state.requestChange(pendingRow,"limit_price",105));
  expect(state.pending).toBeNull(); const cancel = button("Review cancel"); expect(cancel.disabled).toBe(false);
  await act(async () => cancel.click()); expect(state.pending.field).toBe("cancel");
  await act(async () => button("Confirm PAPER").click());
  expect(api.change).toHaveBeenCalledWith("1",{field:"cancel",edit_version:2,confirm_live:false});
});
