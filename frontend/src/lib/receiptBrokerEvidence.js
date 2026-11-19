// Display-only join against the cockpit's existing order-book snapshot. No I/O.
// A matching order event is not proof of an exit or current account exposure.
export const RECEIPT_BOOK_MAX_AGE_MS = 30000;

function number(value, { integer = false, positive = false } = {}) {
  if (value == null || typeof value === "boolean" || !["string", "number"].includes(typeof value)
      || (typeof value === "string" && !value.trim())) return null;
  const n = Number(value);
  return Number.isFinite(n) && n >= 0 && (!positive || n > 0)
    && (!integer || Number.isSafeInteger(n)) ? n : null;
}
const text = v => v == null ? "" : String(v);

export function receiptBrokerEvidence(item, { book, error, readAt, now } = {}) {
  const at = number(readAt, { positive: true });
  const clock = number(now, { positive: true });
  const result = { state: "unavailable", readAt: at,
    ageSeconds: at != null && clock != null ? Math.max(0, Math.floor((clock - at) / 1000)) : null,
    note: "No usable broker order-book snapshot. Stored evidence is separate." };
  const finish = (state, note) => ({ ...result, state, note });
  if (item?.mode !== "live" || !item?.norenordno) {
    return finish("unavailable", "No submitted live broker-order identity to compare.");
  }
  if (error || !book || !Array.isArray(book.orders) || at == null || clock == null) return result;
  if (clock < at || clock - at > RECEIPT_BOOK_MAX_AGE_MS) {
    return finish("stale", "Order-book read is stale or its clock is inconsistent; fill comparison withheld.");
  }
  const rows = book.orders.filter(r => r && text(r.norenordno) === text(item.norenordno));
  if (rows.length === 0) return finish("not_in_book", "This order is absent from the fetched book, which may be day-scoped. Its fills and present exposure are unknown.");
  if (rows.length !== 1) return finish("conflict", "Duplicate broker-order identities; fill comparison withheld.");
  const row = rows[0], i = item.instruction || {};
  const identities = [["tsym", "symbol"], ["exch", "exchange"], ["trantype", "side"], ["prd", "product"]];
  if (identities.some(([key, field]) => !text(i[field]) || !text(row[key]) || text(row[key]) !== text(i[field]))) {
    return finish("conflict", "Broker symbol, exchange, side or product does not match the recorded instruction (or is missing).");
  }
  const quantity = number(row.fillshares, { integer: true });
  const ordered = number(row.qty, { integer: true, positive: true });
  const averagePrice = number(row.avgprc, { positive: true });
  const status = text(row.status).trim().toUpperCase();
  const statuses = ["PENDING", "NEW", "OPEN", "TRIGGER_PENDING", "COMPLETE", "REJECTED", "REJECT", "CANCELED", "CANCELLED", "PARTIAL", "PARTIALLY_FILLED"];
  if (quantity == null || ordered == null || quantity > ordered || (quantity > 0 && averagePrice == null)
      || !statuses.includes(status) || (status === "COMPLETE" && quantity !== ordered)) {
    return finish("conflict", "Broker quantity, average price or status is incomplete or contradictory; fill comparison withheld.");
  }
  const instructionDifferences = [];
  const requested = number(i.quantity, { integer: true, positive: true });
  if (requested == null || requested !== ordered) instructionDifferences.push("ordered quantity");
  if (!text(row.prctyp) || text(row.prctyp) !== text(i.order_type)) instructionDifferences.push("order type");
  const price = number(row.prc), requestedPrice = number(i.limit_price);
  if (price == null || requestedPrice == null || Math.abs(price - requestedPrice) > 0.0001) instructionDifferences.push("limit price");
  const exit = number(item.journal?.exit_price, { positive: true });
  // This remains a mark-based diagnostic, not a round-trip fill reconciliation.
  const gross = quantity > 0 && averagePrice != null && exit != null && i.side === "B"
    && item.journal?.status === "CLOSED" ? Math.round(quantity * (exit - averagePrice) * 100) / 100 : null;
  const oldGross = item.journal?.gross_pnl;
  return { ...result, state: "observed", quantity, averagePrice: quantity > 0 ? averagePrice : null,
    orderStatus: status, instructionDifferences, grossAtJournalExit: gross,
    grossDifference: gross != null && typeof oldGross === "number" && Number.isFinite(oldGross)
      ? Math.round((gross - oldGross) * 100) / 100 : null,
    note: "Order-specific broker snapshot, not a current-position or exit-fill confirmation. Unfilled quantity may still be working." };
}
