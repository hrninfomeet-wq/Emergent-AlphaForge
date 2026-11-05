export const ANALYSIS_OVERLAYS = Object.freeze({ support_resistance: "Support / resistance", fvg: "Fair value gaps", order_blocks: "Order blocks", structure_breaks: "Break of structure", change_of_character: "Change of character", displacement: "Displacement", patterns: "Rejection candles", premium_discount: "Premium / discount", volume_imbalance: "Volume imbalance" });
export const defaultAnalysisSettings = () => ({ overlays: Object.fromEntries(Object.keys(ANALYSIS_OVERLAYS).map(id => [id, false])), forecast: false, strategyId: null });
export function normalizeAnalysisSettings(value) {
  if (value === undefined) return defaultAnalysisSettings();
  if (!value || typeof value !== "object" || Array.isArray(value) || typeof value.forecast !== "boolean" ||
    !(value.strategyId === null || (typeof value.strategyId === "string" && /^[a-zA-Z0-9_-]{1,100}$/.test(value.strategyId))) ||
    !value.overlays || typeof value.overlays !== "object" || Array.isArray(value.overlays) ||
    Object.entries(value.overlays).some(([id, enabled]) => !Object.prototype.hasOwnProperty.call(ANALYSIS_OVERLAYS, id) || typeof enabled !== "boolean")) return null;
  return { overlays: { ...defaultAnalysisSettings().overlays, ...value.overlays }, forecast: value.forecast, strategyId: value.strategyId };
}
