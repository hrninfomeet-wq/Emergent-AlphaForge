import assert from "node:assert/strict";
import { test } from "node:test";
import { receiptBrokerEvidence, RECEIPT_BOOK_MAX_AGE_MS } from "../../frontend/src/lib/receiptBrokerEvidence.js";

const receipt = () => ({ mode: "live", norenordno: "own", instruction: {
  symbol: "SENSEX-TEST", exchange: "BFO", side: "B", product: "I", order_type: "LMT", quantity: 100, limit_price: 104,
}, journal: { status: "CLOSED", exit_price: 110, gross_pnl: 1000 } });
const row = () => ({ norenordno: "own", tsym: "SENSEX-TEST", exch: "BFO", trantype: "B", prd: "I",
  prctyp: "LMT", prc: "104", qty: "100", fillshares: "60", avgprc: "102", status: "OPEN" });
const snapshot = () => ({ book: { orders: [row()] }, readAt: 100000, now: 105000 });

test("own partial fills compare without making an exit-fill or position claim", () => {
  const item = receipt(), s = snapshot(), before = structuredClone([item,s]);
  const result = receiptBrokerEvidence(item,s);
  assert.equal(result.state,"observed"); assert.equal(result.quantity,60); assert.equal(result.averagePrice,102);
  assert.equal(result.grossAtJournalExit,480); assert.equal(result.grossDifference,-520);
  assert.deepEqual(result.instructionDifferences,[]); assert.deepEqual([item,s],before);
  assert.match(result.note,/not a current-position or exit-fill/);
});

test("failed read, missing timestamp, old read and backwards clock withhold values", () => {
  for (const extra of [{error:true}, {readAt:null}, {now:130001}, {now:99999}, {book:{stat:"Not_Ok"}}]) {
    const r=receiptBrokerEvidence(receipt(),{...snapshot(),...extra});
    assert.notEqual(r.state,"observed"); assert.equal(r.quantity,undefined);
  }
  assert.equal(receiptBrokerEvidence(receipt(),{...snapshot(),now:100000+RECEIPT_BOOK_MAX_AGE_MS}).state,"observed");
});

test("absent historic orders and mock/unsubmitted instructions never become zero fills", () => {
  const r=receiptBrokerEvidence(receipt(),{...snapshot(),book:{orders:[]}});
  assert.equal(r.state,"not_in_book"); assert.equal(r.quantity,undefined);
  assert.match(r.note,/day-scoped/);
  for (const changes of [{mode:"mock"},{norenordno:null}]) assert.equal(receiptBrokerEvidence({...receipt(),...changes},snapshot()).state,"unavailable");
});

test("same symbol or duplicate ID cannot establish order ownership", () => {
  const s=snapshot(); s.book.orders[0].norenordno="foreign";
  assert.equal(receiptBrokerEvidence(receipt(),s).state,"not_in_book");
  s.book.orders=[row(),row()]; assert.equal(receiptBrokerEvidence(receipt(),s).state,"conflict");
  for (const key of ["tsym","exch","trantype","prd"]) {
    s.book.orders=[{...row(),[key]:"other"}]; assert.equal(receiptBrokerEvidence(receipt(),s).state,"conflict");
    s.book.orders=[{...row(),[key]:null}]; assert.equal(receiptBrokerEvidence(receipt(),s).state,"conflict");
  }
});

test("invalid fill quantities and averages cannot produce an estimate", () => {
  for (const fillshares of [true,"",null,-1,1.5,"NaN","Infinity",101,9007199254740992]) {
    const s=snapshot();s.book.orders=[{...row(),fillshares}];
    assert.equal(receiptBrokerEvidence(receipt(),s).state,"conflict");
  }
  for (const avgprc of [true,0,-1,"",null,"NaN","Infinity"]) {
    const s=snapshot();s.book.orders=[{...row(),avgprc}];
    assert.equal(receiptBrokerEvidence(receipt(),s).state,"conflict");
  }
});

test("zero is shown only when broker reports it; malformed terminal status withheld", () => {
  let s=snapshot();s.book.orders=[{...row(),fillshares:"0",avgprc:"0"}];
  let r=receiptBrokerEvidence(receipt(),s);assert.equal(r.state,"observed");assert.equal(r.quantity,0);assert.equal(r.averagePrice,null);assert.equal(r.grossAtJournalExit,null);
  for (const status of ["COMPLETE","nonsense",null]) {
    s=snapshot();s.book.orders=[{...row(),status}];assert.equal(receiptBrokerEvidence(receipt(),s).state,"conflict");
  }
});

test("broker instruction drift is exposed alongside own fills", () => {
  const s=snapshot();s.book.orders=[{...row(),qty:"120",prc:"105",prctyp:"SL-LMT"}];
  const r=receiptBrokerEvidence(receipt(),s);assert.equal(r.state,"observed");
  assert.deepEqual(r.instructionDifferences,["ordered quantity","order type","limit price"]);
});

test("no realized estimate for open journals or missing exit mark", () => {
  for(const journal of [null,{status:"OPEN",exit_price:110},{status:"CLOSED",exit_price:null}])
    assert.equal(receiptBrokerEvidence({...receipt(),journal},snapshot()).grossAtJournalExit,null);
});
