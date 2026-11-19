const DEFAULT_RULES = {
  NIFTY: { exch: "NFO", lot_size: 65, freeze_qty: 3510, tick: 0.05, products: ["NRML", "MIS"], price_types: ["LIMIT", "SL-LMT"] },
  BANKNIFTY: { exch: "NFO", lot_size: 30, freeze_qty: 1440, tick: 0.05, products: ["NRML", "MIS"], price_types: ["LIMIT", "SL-LMT"] },
  SENSEX: { exch: "BFO", lot_size: 20, freeze_qty: 1000, tick: 0.05, products: ["NRML", "MIS"], price_types: ["LIMIT", "SL-LMT"] },
};

export function defaultManualRulesFor(underlying) {
  return DEFAULT_RULES[underlying] ?? DEFAULT_RULES.NIFTY;
}

/** Display-only summary: these rules describe current app policy, not verified broker state. */
export function manualTicketCapabilitySummary({ underlying, rules, source = "offline" }) {
  const fallback = defaultManualRulesFor(underlying);
  const resolved = rules ?? fallback;
  const hasReportedValues = source === "api" && rules && typeof rules === "object";
  const completeRules = hasReportedValues
    && ["exch", "lot_size", "freeze_qty", "tick"].every((key) => rules[key] != null)
    && Array.isArray(rules.products) && rules.products.length > 0
    && Array.isArray(rules.price_types) && rules.price_types.length > 0;
  return {
    exchange: resolved.exch ?? fallback.exch,
    lotSize: Number(resolved.lot_size) || fallback.lot_size,
    freezeQty: Number(resolved.freeze_qty) || fallback.freeze_qty,
    tick: resolved.tick ?? fallback.tick,
    products: Array.isArray(resolved.products) && resolved.products.length ? resolved.products : fallback.products,
    priceTypes: Array.isArray(resolved.price_types) && resolved.price_types.length
      ? resolved.price_types.filter((type) => type !== "MARKET")
      : fallback.price_types,
    rulesSource: completeRules ? "app-reported" : hasReportedValues ? "mixed" : "offline-defaults",
    rulesVerified: false,
    manualInstruction: "One-lot BUY · MIS · LIMIT only",
    unsupportedInstruction: "CO/BO are unavailable in this app's order rules.",
    exitInstruction: "Stops use the software guard. This ticket does not verify any broker-held exit protection.",
  };
}
