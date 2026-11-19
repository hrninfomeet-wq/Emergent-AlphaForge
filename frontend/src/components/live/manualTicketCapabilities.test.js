import { manualTicketCapabilitySummary } from "./manualTicketCapabilities";

test("summarizes app-reported rules without treating them as broker-verified", () => {
  const summary = manualTicketCapabilitySummary({
    underlying: "SENSEX",
    source: "api",
    rules: { exch: "BFO", lot_size: 20, freeze_qty: 1000, tick: 0.05,
      products: ["MIS", "NRML"], price_types: ["LIMIT", "SL-LMT", "MARKET"] },
  });

  expect(summary).toMatchObject({ exchange: "BFO", lotSize: 20, freezeQty: 1000, tick: 0.05,
    products: ["MIS", "NRML"], priceTypes: ["LIMIT", "SL-LMT"],
    rulesSource: "app-reported", rulesVerified: false });
  expect(summary.manualInstruction).toBe("One-lot BUY · MIS · LIMIT only");
  expect(summary.unsupportedInstruction).toContain("CO/BO are unavailable in this app");
  expect(summary.exitInstruction).toContain("does not verify any broker-held exit protection");
});

test("labels local fallback values as offline and unverified", () => {
  const summary = manualTicketCapabilitySummary({ underlying: "NIFTY", source: "offline" });
  expect(summary).toMatchObject({ exchange: "NFO", lotSize: 65, freezeQty: 3510, tick: 0.05,
    rulesSource: "offline-defaults", rulesVerified: false });
});

test("does not present incomplete API rules as a complete app rule set", () => {
  const summary = manualTicketCapabilitySummary({ underlying: "SENSEX", source: "api",
    rules: { exch: "BFO", lot_size: 20 } });
  expect(summary.rulesSource).toBe("mixed");
  expect(summary.rulesVerified).toBe(false);
});
