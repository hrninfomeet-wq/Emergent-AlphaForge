import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import { receiptBrokerEvidence } from "@/lib/receiptBrokerEvidence";

const value = (x) => x == null ? "Unknown" : String(x);
const money = (x) => x == null ? "Unknown" : `₹${Number(x).toFixed(2)}`;
const eventLabel = (x) => value(x).replaceAll("_", " ");

function DurableEvidence({ evidence }) {
  if (!evidence || evidence.state === "unavailable") return <details className="border-t border-line pt-2" data-testid="receipt-durable-evidence">
    <summary className="cursor-pointer font-medium">Persisted evidence · unavailable</summary>
    <div className="mt-1">Durable evidence could not be read. Historical events are unknown.</div>
  </details>;
  const events = Array.isArray(evidence.events) ? evidence.events : [];
  return <details className="border-t border-line pt-2" data-testid="receipt-durable-evidence">
    <summary className="cursor-pointer font-medium">Persisted evidence · {evidence.state === "available" ? `${events.length} event${events.length === 1 ? "" : "s"}` : "unknown"}{evidence.truncated === true ? " · history truncated" : ""}</summary>
    <div className="mt-1 space-y-2">
      {evidence.state !== "available" && <div>Durable evidence state is unknown; no conclusion about historical events is available.</div>}
      {evidence.truncated === true && <div className="text-warning">History is truncated; this list may omit persisted events.</div>}
      {events.some((event) => event.fill) && <div className="text-warning">Fill snapshots are cumulative and may arrive out of order. Do not sum them; capture order does not establish execution order.</div>}
      {events.map((event, index) => <div className="rounded border border-line p-2" key={event.event_key || `${event.kind || "event"}-${index}`} data-testid="receipt-durable-event">
        <div className="font-medium">{eventLabel(event.kind)} · {eventLabel(event.evidence_state)} · event {value(event.event_key)}</div>
        <div>Captured at {value(event.recorded_at)} · source {value(event.source)}</div>
        <div>Order {value(event.norenordno)} · client reference {value(event.client_order_id)} · approval {value(event.approval_id)} · deployment {value(event.deployment_id)}</div>
        {event.instruction && <div>Recorded instruction: exchange {value(event.instruction.exch)} · symbol {value(event.instruction.tsym)} · side {value(event.instruction.trantype)} · product {value(event.instruction.prd)} · order type {value(event.instruction.prctyp)} · quantity {value(event.instruction.qty)} · price {value(event.instruction.prc)} · trigger {value(event.instruction.trgprc)} · validity {value(event.instruction.ret)}</div>}
        {event.fill && <div>Reported fill: quantity {value(event.fill.quantity)} · average price {value(event.fill.average_price)} · order status {value(event.fill.order_status)} · event time {value(event.fill.event_time)}</div>}
        {event.exit_evidence && <div>Reported exit evidence: entry order {value(event.exit_evidence.entry_norenordno)} · exit order {value(event.exit_evidence.exit_norenordno)} · fill {value(event.exit_evidence.fill_id)} · quantity {value(event.exit_evidence.quantity)} · price {value(event.exit_evidence.price)} · event time {value(event.exit_evidence.event_time)} · attribution {value(event.exit_evidence.attribution)}</div>}
        {(event.reasons || []).length > 0 && <div className="text-warning">Evidence reasons: {event.reasons.map(value).join(", ")}</div>}
      </div>)}
      {events.length === 0 && evidence.state === "available" && <div>No persisted events are available in this receipt.</div>}
    </div>
  </details>;
}

export function Receipt({ item, brokerEvidence }) {
  const i = item.instruction || {};
  const f = item.fills || {};
  const p = item.protection || {};
  const j = item.journal;
  return <details className="rounded border border-line p-2" data-testid="execution-receipt">
    <summary className="cursor-pointer text-xs">
      <span className="font-mono">{value(i.symbol)}</span> · {value(item.mode)} · recorded state {value(item.state)}
      {f.evidence !== "stored_order_event" && <span className="text-warning"> · stored fills {f.evidence === "needs_review" ? "need review" : "unavailable"}</span>}
      {f.partial && <span className="text-warning"> · partial fills recorded</span>}
      {brokerEvidence?.state === "observed" && <span> · broker snapshot reports {value(brokerEvidence.quantity)} filled</span>}
    </summary>
    <div className="mt-2 space-y-2 text-[11px] text-dim">
      <div className="font-mono break-all">Broker order: {value(item.norenordno)} · Client reference: {value(item.client_order_id)}</div>
      <div>Recorded instruction: {value(i.side)} · {value(i.exchange)} · {value(i.product)} · {value(i.order_type)} · quantity {value(i.quantity)} · limit {money(i.limit_price)}</div>
      <div>Recorded at {value(item.recorded_at)} · Submitted at {value(item.submitted_at)}</div>
      {item.durable_evidence?.state === "available" && item.durable_evidence.events?.some((event) => event.kind === "approval_decided" && event.evidence_state === "observed" && event.approval_id) ?
        <div>Approval decision recorded; placement and fills are separate evidence.</div> :
        <div>Approval snapshot unavailable in this stored record. The instruction is not proof of operator approval or a fill.</div>}
      <div>Stored order fills: quantity {value(f.quantity)} · average {money(f.average_price)}. {f.evidence === "needs_review" ? "Conflicting evidence — do not rely on these values." : "Freshness is not verified; this is not a live broker-book reconciliation."}</div>
      {brokerEvidence && <div className="border-t border-line pt-2" data-testid="receipt-broker-evidence">
        <div className="font-medium">Broker order-book snapshot · {value(brokerEvidence.state).replaceAll("_", " ")}</div>
        <div>{value(brokerEvidence.note)}</div>
        {brokerEvidence.state === "observed" && <>
          <div>Broker-reported fills: quantity {value(brokerEvidence.quantity)} · average {money(brokerEvidence.averagePrice)} · order status {value(brokerEvidence.orderStatus)}</div>
          <div>Fetched at {brokerEvidence.readAt == null ? "Unknown" : new Date(brokerEvidence.readAt).toLocaleString()} · age {brokerEvidence.ageSeconds == null ? "Unknown" : `${brokerEvidence.ageSeconds} seconds`}</div>
          {(brokerEvidence.instructionDifferences || []).length > 0 && <div className="text-warning">Instruction differences: {brokerEvidence.instructionDifferences.join("; ")}</div>}
          {brokerEvidence.grossAtJournalExit != null && <div data-testid="broker-gross-estimate">
            Diagnostic gross estimate {money(brokerEvidence.grossAtJournalExit)} · difference from journal {money(brokerEvidence.grossDifference)}. This uses the journal exit mark; final exit fills and current exposure are not verified.
          </div>}
        </>}
      </div>}
      <DurableEvidence evidence={item.durable_evidence} />
      <div>Exit monitor: {p.state === "registered" ? `registered in app memory · stop ${money(p.stop_level)} · target ${money(p.target_level)}` : value(p.state).replaceAll("_", " ")}. Registration does not confirm a healthy monitor or a working broker exit order.</div>
      {j && <div className="border-t border-line pt-2">
        <div>Journal ({value(j.status)}): quantity basis {value(j.quantity_basis)} · entry basis {money(j.entry_basis_price)} · exit {money(j.exit_price)}</div>
        <div>Journal gross P&amp;L {money(j.gross_pnl)} · net after modeled costs {money(j.net_pnl)}. Exit fill not verified by this receipt.</div>
        {j.gross_at_journal_exit_using_entry_fills != null && <div data-testid="receipt-estimate">
          Diagnostic estimate using entry fills and the journal exit: {money(j.gross_at_journal_exit_using_entry_fills)} gross · difference from journal {money(j.gross_difference)}. This is not confirmed realized P&amp;L; it does not change history, statistics or risk limits.
        </div>}
      </div>}
      {!j && <div>No uniquely matched journal row. Manual instructions may have no trade journal.</div>}
      {(item.warnings || []).length > 0 && <ul className="list-disc pl-4 text-warning">{item.warnings.map((w) => <li key={w}>{w}</li>)}</ul>}
    </div>
  </details>;
}

export default function ExecutionReceipts({ brokerOrders, brokerOrdersError, brokerOrdersReadAt }) {
  const [page, setPage] = useState(0);
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  const [refresh, setRefresh] = useState(0);
  const [, setClockTick] = useState(0);
  const evidenceNow = Date.now();
  const reload = useCallback(() => setRefresh((n) => n + 1), []);
  useEffect(() => {
    const timer = window.setInterval(() => setClockTick((tick) => tick + 1), 5000);
    return () => window.clearInterval(timer);
  }, []);
  useEffect(() => {
    let active = true;
    setBusy(true);
    setData(null);
    setError(null);
    api.executionReceipts(50, page * 50).then((result) => {
      if (active) setData(result);
    }).catch(() => {
      if (active) setError("Receipts unavailable. No current evidence was loaded.");
    }).finally(() => { if (active) setBusy(false); });
    return () => { active = false; };
  }, [page, refresh]);
  return <section className="rounded-lg border border-line bg-bg-1 p-3 space-y-2" data-testid="execution-receipts">
    <div className="flex items-center justify-between gap-2">
      <h3 className="text-xs font-semibold">Execution receipts</h3>
      <button className="text-xs border border-line rounded px-2 py-1" onClick={reload} disabled={busy}>Refresh receipts</button>
    </div>
    <p className="text-[11px] text-dim">Recorded instructions, stored fill evidence and journal estimates. Includes mock and unsubmitted instructions with their mode and state. No broker request is made by this panel.</p>
    {busy && <p className="text-xs text-dim">Loading receipts…</p>}
    {error && <p role="alert" className="text-xs text-warning">{error}</p>}
    {data && <>
      <p className="text-[10px] text-dimmer">Records read at {data.read_at} · {data.items.length} shown of {data.total}. Fill and protection evidence may be older.</p>
      <div data-testid="receipt-capture-status" className={data.capture_status?.state === "degraded" ? "text-[11px] text-warning" : "text-[11px] text-dim"}>
        <div>Capture runs on existing cockpit order/trade-book reads; only acknowledged events survive restarts; pending captures can be lost.</div>
        <div>Capture telemetry is scoped to this process and does not establish historical completeness. Pending captures: {value(data.capture_status?.pending_count ?? data.capture_status?.pending_writes)}.</div>
        {data.capture_status?.state === "degraded" ? `Capture degraded · ${value(data.capture_status.failed_writes)} failed writes observed this process · last failure ${value(data.capture_status.last_failure_at)}` :
          data.capture_status?.state === "ok" ? "No capture failure observed this process; this does not mean all history is complete." :
            data.capture_status?.state === "not_observed" ? "Capture status not observed this process; historical completeness is unknown." : "Capture status unknown; historical completeness is unknown."}
      </div>
      {data.items.map((item, n) => <Receipt key={item.client_order_id || `${page}-${n}`} item={item}
        brokerEvidence={receiptBrokerEvidence(item, { book: brokerOrders, error: brokerOrdersError, readAt: brokerOrdersReadAt, now: evidenceNow })} />)}
      {data.items.length === 0 && <p className="text-xs text-dim">No stored instructions on this page.</p>}
    </>}
    <div className="flex gap-3 text-xs">
      <button disabled={busy || page === 0} onClick={() => setPage((p) => p - 1)}>Previous</button>
      <span>Page {page + 1}</span>
      <button disabled={busy || !data || (page + 1) * 50 >= data.total} onClick={() => setPage((p) => p + 1)}>Next</button>
    </div>
  </section>;
}
