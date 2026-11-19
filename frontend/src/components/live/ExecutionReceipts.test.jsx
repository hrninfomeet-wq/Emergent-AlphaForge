import React, { act } from "react";
import { createRoot } from "react-dom/client";
import ExecutionReceipts, { Receipt } from "./ExecutionReceipts";
import { api } from "@/lib/api";

jest.mock("@/lib/api", () => ({ api: { executionReceipts: jest.fn() } }));
let root, container;
beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  jest.clearAllMocks();
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});
afterEach(async () => { await act(async () => root.unmount()); container.remove(); jest.useRealTimers(); });

test("separates stored fills, unknown approval, software registration and estimated P&L", async () => {
  await act(async () => root.render(<Receipt item={{ instruction: { quantity: 100, symbol: "TEST" },
    fills: { quantity: 60, average_price: 102, partial: true, evidence: "stored_order_event" },
    protection: { state: "registered", stop_level: 90 }, journal: { status: "CLOSED", gross_pnl: 1000,
      gross_at_journal_exit_using_entry_fills: 480, gross_difference: -520 } }} />));
  const text = container.textContent;
  expect(text).toContain("Approval snapshot unavailable");
  expect(text).toContain("not a live broker-book reconciliation");
  expect(text).toContain("Registration does not confirm a healthy monitor");
  expect(text).toContain("₹480.00");
  expect(text).toContain("not confirmed realized P&L");
});

test("shows persisted approval as a captured decision with placement and fills separate", async () => {
  await act(async () => root.render(<Receipt item={{ instruction: { symbol: "TEST" }, durable_evidence: {
    state: "available", truncated: false, events: [{ event_key: "e1", kind: "approval_decided", evidence_state: "observed",
      recorded_at: "2026-10-24T04:00:00Z", source: "app_approval", approval_id: "A1", client_order_id: "C1",
      instruction: { exch: "BFO", tsym: "SENSEX", trantype: "B", prd: "I", prctyp: "LMT", qty: 10, prc: "104", trgprc: "0", ret: "DAY" } }],
  } }} />));
  expect(container.textContent).toContain("Approval decision recorded; placement and fills are separate evidence.");
  expect(container.textContent).not.toContain("Approval snapshot unavailable");
  expect(container.textContent).toContain("Captured at 2026-10-24T04:00:00Z · source app_approval");
  expect(container.textContent).toContain("quantity 10 · price 104 · trigger 0 · validity DAY");
  expect(container.textContent).toContain("Persisted evidence · 1 event");
});

test("unavailable durable history stays unknown rather than presenting an empty history", async () => {
  await act(async () => root.render(<Receipt item={{ instruction: { symbol: "TEST" }, durable_evidence: {
    state: "unavailable", events: [], truncated: false,
  } }} />));
  expect(container.textContent).toContain("Approval snapshot unavailable");
  expect(container.textContent).toContain("Persisted evidence · unavailable");
  expect(container.textContent).toContain("Historical events are unknown");
  expect(container.textContent).not.toContain("No persisted events are available");
});

test("keeps conflicting events and truncation visible without deriving exit P&L", async () => {
  await act(async () => root.render(<Receipt item={{ instruction: { symbol: "TEST" }, durable_evidence: {
    state: "available", truncated: true, events: [{ event_key: "e2", kind: "exit_fill_evidence", evidence_state: "conflicting",
      recorded_at: "2026-10-24T04:01:00Z", source: "broker_book", exit_evidence: { entry_norenordno: "E1", exit_norenordno: "X1",
        fill_id: "F1", quantity: 2, price: "105", event_time: "unknown", attribution: "unresolved" }, reasons: ["multiple_candidates"] }],
  } }} />));
  expect(container.textContent).toContain("history truncated");
  expect(container.textContent).toContain("exit fill evidence · conflicting");
  expect(container.textContent).toContain("Evidence reasons: multiple_candidates");
  expect(container.textContent).toContain("attribution unresolved");
  expect(container.textContent).not.toContain("P&amp;L");
  expect(container.textContent).not.toContain("₹210.00");
});

test("warns that cumulative fill snapshots can arrive out of order and are not summed", async () => {
  await act(async () => root.render(<Receipt item={{ instruction: { symbol: "TEST" }, durable_evidence: {
    state: "available", truncated: false, events: [
      { event_key: "later-capture", kind: "order_fill_snapshot", recorded_at: "2026-10-24T04:02:00Z", source: "order_event", evidence_state: "observed", fill: { quantity: 10, average_price: "101" } },
      { event_key: "earlier-capture", kind: "order_fill_snapshot", recorded_at: "2026-10-24T04:01:00Z", source: "order_event", evidence_state: "observed", fill: { quantity: 4, average_price: "100" } },
    ],
  } }} />));
  expect(container.textContent).toContain("Fill snapshots are cumulative and may arrive out of order. Do not sum them; capture order does not establish execution order.");
  expect(container.textContent).toContain("quantity 10 · average price 101");
  expect(container.textContent).toContain("quantity 4 · average price 100");
  expect(container.textContent).not.toContain("quantity 14");
});

test("shows failed capture globally without exposing raw errors", async () => {
  api.executionReceipts.mockResolvedValueOnce({ items: [{ instruction: { symbol: "TEST" }, durable_evidence: {
    state: "available", events: [], truncated: false,
  } }], total: 1, capture_status: { state: "degraded", failed_writes: 3, pending_count: 2, last_failure_at: "2026-10-24T04:02:00Z", error: "secret raw payload" } });
  await act(async () => root.render(<ExecutionReceipts />));
  expect(container.textContent).toContain("Capture degraded · 3 failed writes observed this process · last failure 2026-10-24T04:02:00Z");
  expect(container.textContent).toContain("Pending captures: 2");
  expect(container.textContent).toContain("Capture runs on existing cockpit order/trade-book reads");
  expect(container.textContent).toContain("only acknowledged events survive restarts; pending captures can be lost");
  expect(container.textContent).toContain("does not establish historical completeness");
  expect(container.textContent).not.toContain("secret raw payload");
});

test("ok capture status means no failure observed in this process, not complete history", async () => {
  api.executionReceipts.mockResolvedValueOnce({ items: [], total: 0, capture_status: { state: "ok", failed_writes: 0, pending_count: 1, last_failure_at: null } });
  await act(async () => root.render(<ExecutionReceipts />));
  expect(container.textContent).toContain("No capture failure observed this process; this does not mean all history is complete.");
  expect(container.textContent).toContain("Pending captures: 1");
});

test("missing capture status stays unknown and does not imply healthy or complete history", async () => {
  api.executionReceipts.mockResolvedValueOnce({ items: [], total: 0 });
  await act(async () => root.render(<ExecutionReceipts />));
  expect(container.textContent).toContain("Pending captures: Unknown");
  expect(container.textContent).toContain("Capture status unknown; historical completeness is unknown.");
});

test("failed refresh removes prior receipts instead of showing stale evidence", async () => {
  api.executionReceipts.mockResolvedValueOnce({ items: [{ client_order_id: "a", instruction: { symbol: "OLD" } }], total: 1 });
  await act(async () => root.render(<ExecutionReceipts />));
  expect(container.textContent).toContain("OLD");
  api.executionReceipts.mockRejectedValueOnce(new Error("offline"));
  await act(async () => container.querySelector("button").click());
  expect(container.textContent).not.toContain("OLD");
  expect(container.textContent).toContain("No current evidence was loaded");
  expect(api.executionReceipts).toHaveBeenCalledTimes(2);
});

test("paging is disabled during reads and next page uses the correct offset", async () => {
  api.executionReceipts.mockResolvedValueOnce({ items: [], total: 51 });
  await act(async () => root.render(<ExecutionReceipts />));
  let finishRefresh;
  api.executionReceipts.mockImplementationOnce(() => new Promise((resolve) => { finishRefresh = resolve; }));
  await act(async () => container.querySelector("button").click());
  // Busy state disables paging while an in-flight read is pending.
  expect(Array.from(container.querySelectorAll("button")).find((b) => b.textContent === "Next").disabled).toBe(true);
  await act(async () => finishRefresh({ items: [], total: 51 }));
  api.executionReceipts.mockResolvedValueOnce({ items: [], total: 51 });
  await act(async () => Array.from(container.querySelectorAll("button")).find((b) => b.textContent === "Next").click());
  expect(api.executionReceipts).toHaveBeenLastCalledWith(50, 50);
  expect(container.textContent).toContain("Page 2");
});

test("broker book evidence ages to stale without another receipt request", async () => {
  jest.useFakeTimers();
  jest.setSystemTime(new Date("2026-10-24T10:00:00Z"));
  api.executionReceipts.mockResolvedValueOnce({ items: [{ mode: "live", norenordno: "N1", instruction: {
    symbol: "TEST", exchange: "BFO", side: "B", product: "I", order_type: "LMT", quantity: 10, limit_price: 104,
  } }], total: 1 });
  const at = Date.now();
  const book = { orders: [{ norenordno: "N1", tsym: "TEST", exch: "BFO", trantype: "B", prd: "I",
    prctyp: "LMT", prc: "104", qty: "10", fillshares: "4", avgprc: "101", status: "OPEN" }] };
  await act(async () => root.render(<ExecutionReceipts brokerOrders={book} brokerOrdersReadAt={at} />));
  expect(container.textContent).toContain("Broker order-book snapshot · observed");
  expect(container.textContent).toContain("Broker-reported fills: quantity 4 · average ₹101.00");
  await act(async () => { jest.advanceTimersByTime(35_000); });
  expect(container.textContent).toContain("Broker order-book snapshot · stale");
  expect(container.textContent).not.toContain("Broker-reported fills: quantity 4");
  expect(api.executionReceipts).toHaveBeenCalledTimes(1);
});

test("failed order-book read suppresses otherwise matching book values", async () => {
  api.executionReceipts.mockResolvedValueOnce({ items: [{ mode: "live", norenordno: "N1", instruction: {
    symbol: "TEST", exchange: "BFO", side: "B", product: "I", order_type: "LMT", quantity: 10, limit_price: 104,
  } }], total: 1 });
  const book = { orders: [{ norenordno: "N1", tsym: "TEST", exch: "BFO", trantype: "B", prd: "I",
    prctyp: "LMT", prc: "104", qty: "10", fillshares: "4", avgprc: "101", status: "OPEN" }] };
  await act(async () => root.render(<ExecutionReceipts brokerOrders={book} brokerOrdersError="offline" brokerOrdersReadAt={Date.now()} />));
  expect(container.textContent).toContain("Broker order-book snapshot · unavailable");
  expect(container.textContent).not.toContain("Broker-reported fills:");
});

test("an order missing from today's book is unknown, not a zero fill", async () => {
  api.executionReceipts.mockResolvedValueOnce({ items: [{ mode: "live", norenordno: "OLD", instruction: {
    symbol: "TEST", exchange: "BFO", side: "B", product: "I", order_type: "LMT", quantity: 10, limit_price: 104,
  } }], total: 1 });
  await act(async () => root.render(<ExecutionReceipts brokerOrders={{ orders: [] }} brokerOrdersReadAt={Date.now()} />));
  expect(container.textContent).toContain("Broker order-book snapshot · not in book");
  expect(container.textContent).not.toContain("Broker-reported fills:");
  expect(api.executionReceipts).toHaveBeenCalledTimes(1);
});

test("a parent read between local clock ticks is evaluated at render time", async () => {
  jest.useFakeTimers();
  jest.setSystemTime(new Date("2026-10-24T10:00:00Z"));
  const item = { mode: "live", norenordno: "N1", instruction: {
    symbol: "TEST", exchange: "BFO", side: "B", product: "I", order_type: "LMT", quantity: 10, limit_price: 104,
  } };
  api.executionReceipts.mockResolvedValueOnce({ items: [item], total: 1 });
  const book = { orders: [{ norenordno: "N1", tsym: "TEST", exch: "BFO", trantype: "B", prd: "I",
    prctyp: "LMT", prc: "104", qty: "10", fillshares: "4", avgprc: "101", status: "OPEN" }] };
  await act(async () => root.render(<ExecutionReceipts brokerOrders={book} brokerOrdersReadAt={Date.now()} />));
  await act(async () => { jest.advanceTimersByTime(2000); });
  await act(async () => root.render(<ExecutionReceipts brokerOrders={book} brokerOrdersReadAt={Date.now()} />));
  expect(container.textContent).toContain("Broker-reported fills: quantity 4");
  expect(container.textContent).not.toContain("snapshot · stale");
  expect(api.executionReceipts).toHaveBeenCalledTimes(1);
});
