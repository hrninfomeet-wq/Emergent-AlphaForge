/**
 * Which index the cockpit's Market pulse + Market analysis cards show, and what
 * those cards may honestly say about it. Pure — no JSX, no browser APIs — so
 * tests/test_market_analysis_view.py executes it under node.
 *
 * GET /market/analysis(/stream)?instrument=X already serves all three indices
 * (candles_1m per instrument, the live option universe per underlying).
 */

/** The indices the cards can show, in tab order. */
export const ANALYSIS_INSTRUMENTS = ["NIFTY", "SENSEX", "BANKNIFTY"];

/** localStorage key for the viewer's last choice (a per-viewer convenience). */
export const ANALYSIS_INSTRUMENT_KEY = "liveCockpitAnalysisInstrument";

/** Any value -> one of ANALYSIS_INSTRUMENTS; anything unknown falls back to NIFTY. */
export function normalizeAnalysisInstrument(value) {
  const s = String(value ?? "").trim().toUpperCase();
  return ANALYSIS_INSTRUMENTS.includes(s) ? s : "NIFTY";
}

/**
 * The payload to render for the selected index, or null while it is not here yet.
 *
 * The stream hook keeps its last payload until the NEW stream's first message, so
 * right after a tab switch the data in hand is still the previous index's. Never
 * render it under the new tab: a SENSEX tab showing NIFTY's chain would be a wrong
 * number with a right-looking label.
 */
export function analysisForInstrument(payload, selected) {
  if (!payload || typeof payload !== "object") return null;
  const have = String(payload.instrument ?? "").trim().toUpperCase();
  return have && have === normalizeAnalysisInstrument(selected) ? payload : null;
}

/**
 * Label for the IV-rank source. The "vix_proxy" source is INDIA VIX — the implied
 * volatility of NIFTY 50 options — so for SENSEX / BANKNIFTY it is a borrowed number
 * and must say so; the same 28% printed under three indices would otherwise read as
 * three measurements.
 */
export function ivSourceLabel(source, instrument) {
  if (source === "atm_iv") return "ATM IV";
  if (source === "vix_proxy") {
    const inst = String(instrument ?? "").trim().toUpperCase();
    return inst && inst !== "NIFTY" ? "India VIX proxy (NIFTY 50 IV, not " + inst + ")" : "VIX proxy";
  }
  if (!source) return "unavailable";
  return String(source);
}
