export const FIELD_LABELS = { limit_price: "Limit price", trigger_price: "Trigger price", stop_price: "Stop loss", target_price: "Target", cancel: "Cancel / close" };
export const displayPrice = value => value == null || value === "" || !Number.isFinite(Number(value)) ? "—" : Number(value).toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
export const displayQuantity = value => value == null || value === "" || !Number.isFinite(Number(value)) ? "—" : Number(value).toLocaleString("en-IN", { maximumFractionDigits: 0 });
export const protectionLabel = value => /broker/i.test(value || "") ? "Broker SL-LMT capability configured; protection unverified; target software" : value ? "Software stop and target" : "Protection unknown";
export const istTime = value => value ? new Date(value).toLocaleTimeString("en-IN", { timeZone: "Asia/Kolkata", hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false }) + " IST" : "time unknown";
export const rowIsPosition = row => Number(row.quantity) > 0;
export const selectableRows = (rows, mode) => rows.filter(row => row.mode === mode && row.active && row.status !== "DRAFT");
export function entryPayload(instrumentKey, mode, form) {
  return { instrument_key: instrumentKey, mode, lots: Number(form.lots), order_type: form.order_type,
    ...(form.order_type !== "MARKET" ? { limit_price: Number(form.limit_price) } : {}),
    ...(form.order_type === "STOP_LIMIT" ? { trigger_price: Number(form.trigger_price) } : {}),
    stop_price: Number(form.stop_price), ...(form.target_price !== "" ? { target_price: Number(form.target_price) } : {}) };
}
export const initialTicket = () => ({ lots: "1", order_type: "LIMIT", limit_price: "", trigger_price: "", stop_price: "", target_price: "" });
