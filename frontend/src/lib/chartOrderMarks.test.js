import { markChartOrders } from "./chartOrderMarks";
const now = 100000;
const row = {instrument_key:"A", quantity:65, entry_price:190, mark_price:195, pnl:325, mark_fresh:true, mark_age_ms:100};
const snapshot = {connected:true, instrument_key:"A", tick:{last_price:200, ts:now, ingest_ts:now}};
test("fresh same-instrument tick marks authoritative quantity and P&L", () => {
  expect(markChartOrders([row], snapshot, now, now, false)[0]).toMatchObject({mark_price:200, mark_fresh:true, pnl:650});
});
test.each([
  ["stale tick", {...snapshot, tick:{...snapshot.tick, ts:now-10001}}],
  ["future tick", {...snapshot, tick:{...snapshot.tick, ingest_ts:now+5001}}],
  ["wrong identity", {...snapshot, instrument_key:"B"}],
  ["disconnected", {...snapshot, connected:false}],
])("%s never replaces previous mark", (_, bad) => {
  expect(markChartOrders([row], bad, now, now, false)[0]).toMatchObject({mark_price:195, pnl:325});
});
test("old quantity snapshot prevents a fresh tick from updating P&L", () => {
  expect(markChartOrders([row], snapshot, now-10001, now, false)[0]).toMatchObject({mark_price:195, pnl:325, mark_fresh:false});
});
