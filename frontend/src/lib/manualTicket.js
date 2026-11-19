/** Keep confirmation bound to the exact preview and supported manual instruction. */
export function manualTicketPlacementError(ticket, preview) {
  if (ticket.side !== "B" || ticket.lots !== 1 || ticket.product !== "MIS" || ticket.order_type !== "LIMIT") {
    return "Manual placement supports one-lot BUY, MIS, LIMIT only. Other tickets are preview-only.";
  }
  if (ticket.buffer_pct != null && ticket.buffer_pct !== 0.5) {
    return "Manual placement requires the 0.5% limit buffer.";
  }
  if (!preview?.ok || preview.ticketKey !== JSON.stringify(ticket)) {
    return "Preview this exact ticket before placing it.";
  }
  if (preview.placement_supported !== true) {
    return preview.placement_reason || "The server has not confirmed manual placement support. Refresh the preview.";
  }
  return null;
}

/** The resolved execution fields the operator sees; IDs may change per request. */
export function samePreviewEntry(first, second) {
  if (!first || !second) return false;
  for (const key of ["exch", "tsym", "trantype", "prd", "prctyp", "ret"]) {
    if (!first[key] || first[key] !== second[key]) return false;
  }
  for (const key of ["qty", "prc"]) {
    const a = Number(first[key]); const b = Number(second[key]);
    if (!Number.isFinite(a) || a <= 0 || a !== b) return false;
  }
  return [first, second].every((item) => item.trgprc == null || item.trgprc === "" || Number(item.trgprc) === 0);
}
